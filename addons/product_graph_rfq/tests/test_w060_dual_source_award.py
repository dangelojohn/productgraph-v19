# -*- coding: utf-8 -*-
"""W060 (R6.10) — dual-source / split-award tests.

Locks in:
  * Single-source award still works (the legacy is_awarded-only flow)
    and backfills awarded_qty with line.qty so downstream consumers
    can rely on the field.
  * Two awarded bids on the same line with summed awarded_qty == qty
    awards cleanly (50/50 split, 60/40 split, etc.).
  * Sum-of-awarded-qty > line.qty is REJECTED by the line constraint
    before the buyer ever reaches Award.
  * Splitting with a 0-qty bid is rejected at award time.
  * Bridge writes ONE requisition line per awarded bid (so dual-source
    produces 2 req-lines for a single RFQ line).
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'w060')
class TestW060DualSourceAward(TransactionCase):

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
            'name': 'W060 Test Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()

        cls.vendor_a = cls.env['pg.vendor'].create({
            'code': 'W060A', 'name': 'W060 Vendor A',
        })
        cls.vendor_a.action_to_probationary()
        cls.vendor_a.action_qualify()
        cls.vendor_b = cls.env['pg.vendor'].create({
            'code': 'W060B', 'name': 'W060 Vendor B',
        })
        cls.vendor_b.action_to_probationary()
        cls.vendor_b.action_qualify()

    def _make_rfq(self, line_qty=100.0):
        rfq = self.env['pg.rfq'].create({'title': 'W060 RFQ'})
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': self.item.id,
            'qty': line_qty,
        })
        return rfq, line

    def _add_bid(self, line, vendor, price, awarded=False, awarded_qty=0.0):
        return self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': vendor.id,
            'unit_price': price,
            'vendor_part_number': 'VPN-%s' % vendor.code,
            'is_awarded': awarded,
            'awarded_qty': awarded_qty,
            'lead_time_days': 14,
            'moq': 1.0,
        })

    # ──────────────────────────────────────────────────────────────────
    # 1. Single-source path is unchanged (regression guard)
    # ──────────────────────────────────────────────────────────────────
    def test_10_single_source_award_still_works_and_backfills_qty(self):
        rfq, line = self._make_rfq(line_qty=100.0)
        rfq.action_send()
        bid = self._add_bid(line, self.vendor_a, 10.0, awarded=True)
        # awarded_qty left at default 0 → must backfill to line.qty=100.
        self.assertEqual(bid.awarded_qty, 0.0)
        rfq.action_mark_received()
        rfq.action_award()
        self.assertEqual(rfq.state, 'awarded')
        bid.invalidate_recordset(['awarded_qty'])
        self.assertEqual(
            bid.awarded_qty, 100.0,
            "Single-source award must backfill awarded_qty with line.qty.",
        )

    # ──────────────────────────────────────────────────────────────────
    # 2. Dual-source split (60/40) awards cleanly
    # ──────────────────────────────────────────────────────────────────
    def test_20_dual_source_60_40_split_awards(self):
        rfq, line = self._make_rfq(line_qty=100.0)
        rfq.action_send()
        bid_a = self._add_bid(line, self.vendor_a, 10.0,
                              awarded=True, awarded_qty=60.0)
        bid_b = self._add_bid(line, self.vendor_b, 11.0,
                              awarded=True, awarded_qty=40.0)
        rfq.action_mark_received()
        rfq.action_award()
        self.assertEqual(rfq.state, 'awarded')
        # Both AVL rows should now exist.
        avl_a = self.env['pg.vendor.part'].search([
            ('item_id', '=', self.item.id),
            ('vendor_id', '=', self.vendor_a.id),
        ])
        avl_b = self.env['pg.vendor.part'].search([
            ('item_id', '=', self.item.id),
            ('vendor_id', '=', self.vendor_b.id),
        ])
        self.assertEqual(len(avl_a), 1)
        self.assertEqual(len(avl_b), 1)
        self.assertEqual(avl_a.unit_price_hint, 10.0)
        self.assertEqual(avl_b.unit_price_hint, 11.0)

    # ──────────────────────────────────────────────────────────────────
    # 3. Over-award is rejected by line constraint
    # ──────────────────────────────────────────────────────────────────
    def test_30_sum_exceeds_line_qty_rejected(self):
        rfq, line = self._make_rfq(line_qty=100.0)
        rfq.action_send()
        self._add_bid(line, self.vendor_a, 10.0,
                      awarded=True, awarded_qty=60.0)
        with self.assertRaises(ValidationError):
            self._add_bid(line, self.vendor_b, 11.0,
                          awarded=True, awarded_qty=50.0)  # 60+50=110 > 100

    # ──────────────────────────────────────────────────────────────────
    # 4. Multi-bid split with one zero-qty bid is rejected at award
    # ──────────────────────────────────────────────────────────────────
    def test_40_split_with_zero_qty_bid_rejected_at_award(self):
        rfq, line = self._make_rfq(line_qty=100.0)
        rfq.action_send()
        self._add_bid(line, self.vendor_a, 10.0,
                      awarded=True, awarded_qty=70.0)
        self._add_bid(line, self.vendor_b, 11.0,
                      awarded=True, awarded_qty=0.0)
        rfq.action_mark_received()
        with self.assertRaises(ValidationError):
            rfq.action_award()

    # ──────────────────────────────────────────────────────────────────
    # 5. Bridge produces one requisition line per awarded bid
    # ──────────────────────────────────────────────────────────────────
    def test_50_bridge_emits_one_req_line_per_awarded_bid(self):
        # Bridge only exists when product_graph_procurement is
        # installed; skip cleanly if not.
        if not self.env['ir.module.module'].sudo().search([
            ('name', '=', 'product_graph_procurement'),
            ('state', '=', 'installed'),
        ]):
            self.skipTest('product_graph_procurement not installed')
        # Need linked product on item + partner on vendors for the
        # bridge to actually emit lines (otherwise it skips them).
        product = self.env['product.product'].create({
            'name': 'W060 Bridge Product', 'type': 'consu',
        })
        self.item.product_id = product.id
        partner_a = self.env['res.partner'].create({
            'name': 'W060 Partner A', 'company_type': 'company',
        })
        partner_b = self.env['res.partner'].create({
            'name': 'W060 Partner B', 'company_type': 'company',
        })
        self.vendor_a.partner_id = partner_a.id
        self.vendor_b.partner_id = partner_b.id

        rfq, line = self._make_rfq(line_qty=100.0)
        rfq.action_send()
        self._add_bid(line, self.vendor_a, 10.0,
                      awarded=True, awarded_qty=60.0)
        self._add_bid(line, self.vendor_b, 11.0,
                      awarded=True, awarded_qty=40.0)
        rfq.action_mark_received()
        rfq.action_award()
        self.assertTrue(rfq.requisition_id)
        # Expect TWO requisition lines — one per awarded vendor.
        self.assertEqual(
            len(rfq.requisition_id.line_ids), 2,
            "Dual-source award must emit one requisition line per "
            "awarded bid (got %d)." % len(rfq.requisition_id.line_ids),
        )
        qtys = sorted(rfq.requisition_id.line_ids.mapped('product_qty'))
        self.assertEqual(qtys, [40.0, 60.0])
