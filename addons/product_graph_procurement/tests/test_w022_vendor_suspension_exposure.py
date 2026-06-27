# -*- coding: utf-8 -*-
"""W022 (R6.3) — vendor suspension exposure surface.

Verifies the three smart-button computed counts on pg.vendor (open
POs / open requisitions / open RFQ lines) and the on-suspend
mail.activity fire that lands on the responsible buyer's todo list
with the impact summary.

Smart-button counts must:
  * be 0 for a vendor with no partner_id
  * count open POs / requisitions referencing the vendor's partner_id
  * count open RFQ lines that carry a bid from this vendor

Activity fire must:
  * land exactly one activity on action_suspend (qualified -> suspended)
  * assign the activity to vendor.responsible_id when set
  * fall back to the first pg approver when no responsible_id
  * include the open-exposure summary in the note
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'w022')
class TestW022VendorSuspensionExposure(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        cls.item = cls.env['pg.item'].create({
            'name': 'W022 Test Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()

        cls.partner = cls.env['res.partner'].create({
            'name': 'W022 Supplier Partner',
            'company_type': 'company',
        })
        cls.vendor = cls.env['pg.vendor'].create({
            'code': 'W022-1',
            'name': 'W022 Supplier',
            'partner_id': cls.partner.id,
        })
        cls.vendor.action_to_probationary()
        cls.vendor.action_qualify()

    def _make_open_po(self):
        product = self.env['product.product'].create({
            'name': 'W022 product', 'type': 'consu', 'is_storable': True,
        })
        po = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'name': product.name,
                'product_qty': 1.0,
                'price_unit': 10.0,
                'product_uom': product.uom_id.id,
            })],
        })
        # Default state is 'draft' — counts as open.
        return po

    # ──────────────────────────────────────────────────────────────────
    # Smart-button counts
    # ──────────────────────────────────────────────────────────────────
    def test_10_zero_counts_without_partner(self):
        """A vendor with no partner_id can't have downstream PO/req
        exposure — counts must be 0, not raise."""
        v = self.env['pg.vendor'].create({
            'code': 'W022-NOPART', 'name': 'No Partner',
        })
        self.assertEqual(v.open_po_count, 0)
        self.assertEqual(v.open_requisition_count, 0)
        self.assertEqual(v.open_rfq_line_count, 0)

    def test_20_open_po_count_picks_up_draft_po(self):
        """A draft PO against the vendor's partner_id increases the
        open_po_count."""
        before = self.vendor.open_po_count
        self._make_open_po()
        self.vendor.invalidate_recordset(['open_po_count'])
        self.assertEqual(
            self.vendor.open_po_count, before + 1,
            "open_po_count must pick up the new draft PO.",
        )

    def test_30_open_rfq_line_count_picks_up_bid(self):
        """An RFQ line with a bid from this vendor on a non-awarded
        RFQ counts against open_rfq_line_count."""
        rfq = self.env['pg.rfq'].create({'title': 'W022 RFQ'})
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id, 'item_id': self.item.id, 'qty': 5.0,
        })
        rfq.action_send()
        self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': self.vendor.id,
            'unit_price': 12.5,
            'vendor_part_number': 'W022-VPN',
        })
        self.vendor.invalidate_recordset(['open_rfq_line_count'])
        self.assertEqual(
            self.vendor.open_rfq_line_count, 1,
            "open_rfq_line_count must pick up the new bid's line.",
        )

    # ──────────────────────────────────────────────────────────────────
    # Activity fire on suspend
    # ──────────────────────────────────────────────────────────────────
    def _activities_on_vendor(self, vendor):
        return self.env['mail.activity'].sudo().search([
            ('res_model', '=', 'pg.vendor'),
            ('res_id', '=', vendor.id),
        ])

    def test_40_suspend_fires_activity(self):
        """action_suspend (qualified -> suspended) creates exactly one
        impact-summary mail.activity on the vendor."""
        before = len(self._activities_on_vendor(self.vendor))
        self.vendor.with_context(
            default_qualification_comment='Quality slip',
        ).action_suspend()
        self.assertEqual(self.vendor.state, 'suspended')
        after = self._activities_on_vendor(self.vendor)
        self.assertEqual(
            len(after), before + 1,
            "Exactly one W022 activity should land on suspension.",
        )
        act = after[-1]
        self.assertIn(
            "W022 Supplier", act.summary,
            "Activity summary should name the vendor.",
        )

    def test_50_suspend_activity_assigned_to_responsible(self):
        """When the vendor has a responsible_id, the activity goes
        to that user."""
        buyer = self.env['res.users'].create({
            'name': 'W022 Buyer',
            'login': 'w022_buyer@example.invalid',
        })
        v = self.env['pg.vendor'].create({
            'code': 'W022-2', 'name': 'W022 Supplier 2',
            'partner_id': self.partner.id,
            'responsible_id': buyer.id,
        })
        v.action_to_probationary()
        v.action_qualify()
        v.with_context(
            default_qualification_comment='Reason',
        ).action_suspend()
        acts = self._activities_on_vendor(v)
        self.assertTrue(acts, "Activity should fire on suspend.")
        self.assertEqual(
            acts[-1].user_id, buyer,
            "Activity must be assigned to the responsible buyer.",
        )

    def test_60_suspend_activity_note_includes_impact_summary(self):
        """The activity note carries the PO / requisition / RFQ count
        line items the buyer needs to triage."""
        # Give the vendor one open PO so the summary is non-trivial.
        self._make_open_po()
        self.vendor.invalidate_recordset(['open_po_count'])
        self.vendor.with_context(
            default_qualification_comment='Quality slip',
        ).action_suspend()
        acts = self._activities_on_vendor(self.vendor)
        self.assertTrue(acts, "Activity should fire on suspend.")
        note = acts[-1].note or ''
        self.assertIn(
            "open purchase order", note,
            "Activity note must include the open-PO impact line.",
        )

    def test_70_already_suspended_does_not_refire(self):
        """Calling action_suspend on a vendor not in
        (probationary, qualified) doesn't transition (super raises),
        and therefore doesn't fire a second activity."""
        self.vendor.with_context(
            default_qualification_comment='First',
        ).action_suspend()
        baseline = len(self._activities_on_vendor(self.vendor))
        # Now suspended -> suspended is NOT a legal transition; super
        # raises UserError. Catch and assert no new activity fired.
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.vendor.with_context(
                default_qualification_comment='Again',
            ).action_suspend()
        self.assertEqual(
            len(self._activities_on_vendor(self.vendor)),
            baseline,
            "No second activity should fire on illegal re-suspend.",
        )
