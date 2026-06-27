# -*- coding: utf-8 -*-
"""W059 (R6.9) — MOQ shortfall warning on pg.rfq.line.

Computes `moq_shortfall` (Boolean) + `moq_shortfall_amount` (Float)
against `recommended_bid_id.moq` so the buyer triaging the list view
can spot lines where the recommended vendor's MOQ is higher than the
qty being requested.

The warning is warn-only — it does NOT block save, award, or any
other action. Buyer can either bump qty to MOQ or click "Accept
Shortfall" to flag the line as knowingly under-MOQ.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'w059')
class TestW059MoqShortfall(TransactionCase):

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
            'name': 'W059 Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()
        cls.vendor = cls.env['pg.vendor'].create(
            {'code': 'W59V', 'name': 'W59 Vendor'})
        cls.vendor.action_to_probationary()
        cls.vendor.action_qualify()

    def _make_line(self, qty, bid_price=10.0, bid_moq=100.0):
        rfq = self.env['pg.rfq'].create({'title': 'W059 RFQ'})
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': self.item.id,
            'qty': qty,
        })
        rfq.action_send()
        self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': self.vendor.id,
            'unit_price': bid_price,
            'moq': bid_moq,
        })
        return rfq, line

    def test_10_shortfall_flagged_when_qty_below_moq(self):
        """qty=20 < moq=100 → moq_shortfall=True, amount=80."""
        _, line = self._make_line(qty=20, bid_moq=100.0)
        self.assertTrue(
            line.recommended_bid_id,
            'Recommended bid should be set so shortfall computes against it.',
        )
        self.assertTrue(line.moq_shortfall)
        self.assertAlmostEqual(line.moq_shortfall_amount, 80.0, places=2)

    def test_20_no_shortfall_when_qty_meets_moq(self):
        """qty=100 == moq=100 → no shortfall."""
        _, line = self._make_line(qty=100, bid_moq=100.0)
        self.assertFalse(line.moq_shortfall)
        self.assertAlmostEqual(line.moq_shortfall_amount, 0.0)

    def test_30_accept_shortfall_does_not_block_save(self):
        """The warning is warn-only — accepting it must not raise, the
        record stays editable, and the underlying flag stays True so
        the view can render the secondary 'acknowledged' banner."""
        _, line = self._make_line(qty=20, bid_moq=100.0)
        self.assertTrue(line.moq_shortfall)
        line.action_accept_moq_shortfall()
        line.invalidate_recordset()
        self.assertTrue(line.moq_shortfall_accepted)
        # Underlying gap is unchanged — the bid still wants 100, line is 20.
        self.assertTrue(line.moq_shortfall)
        self.assertAlmostEqual(line.moq_shortfall_amount, 80.0, places=2)
        # And the line is still editable — no UserError on a follow-up write.
        line.write({'target_date': '2026-12-31'})

    def test_40_bump_qty_to_moq_clears_shortfall(self):
        """Convenience action bumps qty up to bid.moq + clears the
        acceptance flag (no longer needed)."""
        _, line = self._make_line(qty=20, bid_moq=100.0)
        line.action_accept_moq_shortfall()  # buyer accepted, then changed mind
        line.action_bump_qty_to_moq()
        line.invalidate_recordset()
        self.assertAlmostEqual(line.qty, 100.0, places=2)
        self.assertFalse(line.moq_shortfall)
        self.assertAlmostEqual(line.moq_shortfall_amount, 0.0)
        self.assertFalse(line.moq_shortfall_accepted)
