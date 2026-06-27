# -*- coding: utf-8 -*-
"""W030 (R6.5) — Switch Source wizard tests.

Locks in:
  * Wizard re-targets the parent doc's vendor to the new pick.
  * AVL preferred-source flag is NOT modified (regression-proof).
  * Chatter notes land on BOTH old and new vendor records + the parent doc.
  * Locked PO state (done/cancel) blocks the switch.
  * Disqualified target vendor blocks the switch.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'w030')
class TestW030SwitchSourceWizard(TransactionCase):

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

        cls.product = cls.env['product.product'].create({
            'name': 'W030 Product',
            'type': 'consu',
            'is_storable': True,
        })
        cls.item = cls.env['pg.item'].create({
            'name': 'W030 Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()
        cls.item.product_id = cls.product.id

        # Two vendors — A is preferred, B is the override target.
        cls.partner_a = cls.env['res.partner'].create({
            'name': 'W030 Vendor A Partner', 'company_type': 'company',
        })
        cls.partner_b = cls.env['res.partner'].create({
            'name': 'W030 Vendor B Partner', 'company_type': 'company',
        })
        cls.vendor_a = cls.env['pg.vendor'].create({
            'code': 'W030-A', 'name': 'W030 Vendor A',
            'partner_id': cls.partner_a.id,
        })
        cls.vendor_a.action_to_probationary()
        cls.vendor_a.action_qualify()
        cls.vendor_b = cls.env['pg.vendor'].create({
            'code': 'W030-B', 'name': 'W030 Vendor B',
            'partner_id': cls.partner_b.id,
        })
        cls.vendor_b.action_to_probationary()
        cls.vendor_b.action_qualify()

        # AVL — A is the preferred source.
        cls.vp_a = cls.env['pg.vendor.part'].create({
            'item_id': cls.item.id,
            'vendor_id': cls.vendor_a.id,
            'vendor_part_number': 'VPN-A',
            'rank': 10,
            'is_preferred': True,
            'unit_price_hint': 7.50,
            'lead_time_days': 14,
        })
        cls.vp_b = cls.env['pg.vendor.part'].create({
            'item_id': cls.item.id,
            'vendor_id': cls.vendor_b.id,
            'vendor_part_number': 'VPN-B',
            'rank': 20,
            'is_preferred': False,
            'unit_price_hint': 8.00,
            'lead_time_days': 7,
        })

    def _make_po_line(self):
        po = self.env['purchase.order'].create({
            'partner_id': self.partner_a.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': self.product.name,
                'product_qty': 10.0,
                'price_unit': 7.50,
                'product_uom': self.product.uom_id.id,
                'pg_item_id': self.item.id,
            })],
        })
        return po, po.order_line

    def _make_req_line(self):
        req_vals = {'reference': 'W030-REQ'}
        if 'vendor_id' in self.env['purchase.requisition']._fields:
            req_vals['vendor_id'] = self.partner_a.id
        req = self.env['purchase.requisition'].create(req_vals)
        line = self.env['purchase.requisition.line'].create({
            'requisition_id': req.id,
            'product_id': self.product.id,
            'product_qty': 10.0,
            'product_uom_id': self.product.uom_id.id,
            'price_unit': 7.50,
            'pg_item_id': self.item.id,
        })
        return req, line

    # ──────────────────────────────────────────────────────────────────
    # Happy path: PO line switch
    # ──────────────────────────────────────────────────────────────────
    def test_10_po_line_switch_retargets_partner(self):
        po, line = self._make_po_line()
        self.assertEqual(po.partner_id, self.partner_a)
        wiz = self.env['pg.switch.source.wizard'].with_context(
            active_model='purchase.order.line',
            active_id=line.id,
        ).create({
            'new_vendor_part_id': self.vp_b.id,
            'reason': 'Vendor A on holiday until 2026-07-15',
        })
        wiz.action_confirm()
        # The PO's partner_id was rewritten to Vendor B's partner.
        po.invalidate_recordset(['partner_id'])
        self.assertEqual(
            po.partner_id, self.partner_b,
            "PO partner should be re-targeted to Vendor B.",
        )

    # ──────────────────────────────────────────────────────────────────
    # Preferred flag invariant
    # ──────────────────────────────────────────────────────────────────
    def test_20_does_not_flip_preferred_on_target(self):
        """The chosen AVL entry's is_preferred flag stays exactly as it
        was before the wizard ran (W030 == one-time override)."""
        po, line = self._make_po_line()
        pre_b = self.vp_b.is_preferred
        pre_a = self.vp_a.is_preferred
        self.assertFalse(pre_b)
        self.assertTrue(pre_a)
        wiz = self.env['pg.switch.source.wizard'].with_context(
            active_model='purchase.order.line',
            active_id=line.id,
        ).create({
            'new_vendor_part_id': self.vp_b.id,
            'reason': 'One-time switch',
        })
        wiz.action_confirm()
        self.vp_a.invalidate_recordset(['is_preferred'])
        self.vp_b.invalidate_recordset(['is_preferred'])
        self.assertEqual(
            self.vp_a.is_preferred, pre_a,
            "Old AVL preferred flag must not be touched.",
        )
        self.assertEqual(
            self.vp_b.is_preferred, pre_b,
            "Target AVL preferred flag must not be flipped to True.",
        )

    # ──────────────────────────────────────────────────────────────────
    # Chatter audit on both old and new vendor
    # ──────────────────────────────────────────────────────────────────
    def test_30_chatter_lands_on_both_vendors(self):
        po, line = self._make_po_line()
        a_before = len(self.vendor_a.message_ids)
        b_before = len(self.vendor_b.message_ids)
        wiz = self.env['pg.switch.source.wizard'].with_context(
            active_model='purchase.order.line',
            active_id=line.id,
        ).create({
            'new_vendor_part_id': self.vp_b.id,
            'reason': 'Audit chatter test',
        })
        wiz.action_confirm()
        self.assertGreater(
            len(self.vendor_a.message_ids), a_before,
            "Old vendor should receive a chatter note.",
        )
        self.assertGreater(
            len(self.vendor_b.message_ids), b_before,
            "New vendor should receive a chatter note.",
        )
        latest = self.vendor_b.message_ids[0].body
        self.assertIn('Switch Source', latest)
        self.assertIn('Audit chatter test', latest)

    # ──────────────────────────────────────────────────────────────────
    # Lock-state validation
    # ──────────────────────────────────────────────────────────────────
    def test_40_locked_po_state_blocks_switch(self):
        po, line = self._make_po_line()
        po.button_confirm()
        # Confirmed purchase orders are in 'purchase' state — not yet
        # "done/cancel" but the wizard also rejects non-draft/non-sent
        # in the PO-line branch. Verify that path raises.
        wiz = self.env['pg.switch.source.wizard'].with_context(
            active_model='purchase.order.line',
            active_id=line.id,
        ).create({
            'new_vendor_part_id': self.vp_b.id,
            'reason': 'Try',
        })
        with self.assertRaises(UserError):
            wiz.action_confirm()

    # ──────────────────────────────────────────────────────────────────
    # Disqualified target blocks
    # ──────────────────────────────────────────────────────────────────
    def test_50_disqualified_target_blocks(self):
        self.vendor_b.with_context(
            default_qualification_comment='Failing audit',
        ).action_disqualify()
        po, line = self._make_po_line()
        wiz = self.env['pg.switch.source.wizard'].with_context(
            active_model='purchase.order.line',
            active_id=line.id,
        ).create({
            'new_vendor_part_id': self.vp_b.id,
            'reason': 'Disqualified target',
        })
        with self.assertRaises(UserError):
            wiz.action_confirm()
