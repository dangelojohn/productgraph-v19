# -*- coding: utf-8 -*-
"""W061 (R6.17) — vendor performance smart buttons tests.

Locks in:
  * open_po_count_all picks up confirmed POs (broader than W022's
    open_po_count which scopes to draft/sent/to_approve).
  * late_po_count picks up an open PO whose date_planned is in the past.
  * defect_rate_30d defaults to 0.0 when no partner is set.
  * defect_rate_30d defaults to 0.0 when mi.check is not registered
    (the generic product_graph repo never depends on it).
  * Smart-button drill-through actions return well-formed act_windows.
"""
from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'w061')
class TestW061VendorPerformance(TransactionCase):

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
            'name': 'W061 Product',
            'type': 'consu',
            'is_storable': True,
        })
        cls.item = cls.env['pg.item'].create({
            'name': 'W061 Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()
        cls.item.product_id = cls.product.id

        cls.partner = cls.env['res.partner'].create({
            'name': 'W061 Vendor Partner', 'company_type': 'company',
        })
        cls.vendor = cls.env['pg.vendor'].create({
            'code': 'W061-V', 'name': 'W061 Vendor',
            'partner_id': cls.partner.id,
        })
        cls.vendor.action_to_probationary()
        cls.vendor.action_qualify()

    def _make_po(self, state=None, date_planned=None):
        po = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'name': self.product.name,
                'product_qty': 5.0,
                'price_unit': 10.0,
                'product_uom': self.product.uom_id.id,
                'pg_item_id': self.item.id,
            })],
        })
        if date_planned:
            po.order_line.date_planned = date_planned
        if state == 'purchase':
            po.button_confirm()
        return po

    # ──────────────────────────────────────────────────────────────────
    # Zero-state baselines
    # ──────────────────────────────────────────────────────────────────
    def test_10_zero_counts_without_partner(self):
        v = self.env['pg.vendor'].create({
            'code': 'W061-NOPART', 'name': 'W061 No Partner',
        })
        self.assertEqual(v.open_po_count_all, 0)
        self.assertEqual(v.late_po_count, 0)
        self.assertEqual(v.defect_rate_30d, 0.0)

    # ──────────────────────────────────────────────────────────────────
    # open_po_count_all picks up confirmed POs (broader than W022)
    # ──────────────────────────────────────────────────────────────────
    def test_20_confirmed_po_counted(self):
        before = self.vendor.open_po_count_all
        self._make_po(state='purchase')
        self.vendor.invalidate_recordset(['open_po_count_all'])
        self.assertEqual(
            self.vendor.open_po_count_all, before + 1,
            "Confirmed PO must be picked up by open_po_count_all "
            "(broader than W022's draft/sent/to_approve scope).",
        )

    # ──────────────────────────────────────────────────────────────────
    # late_po_count picks up overdue PO
    # ──────────────────────────────────────────────────────────────────
    def test_30_late_po_counted(self):
        past = datetime.now() - timedelta(days=5)
        self._make_po(date_planned=past)
        self.vendor.invalidate_recordset(['late_po_count'])
        self.assertGreaterEqual(
            self.vendor.late_po_count, 1,
            "PO whose date_planned is in the past must be counted as late.",
        )

    def test_40_future_po_not_counted_as_late(self):
        future = datetime.now() + timedelta(days=10)
        baseline = self.vendor.late_po_count
        self._make_po(date_planned=future)
        self.vendor.invalidate_recordset(['late_po_count'])
        self.assertEqual(
            self.vendor.late_po_count, baseline,
            "PO with a future date_planned must NOT bump late_po_count.",
        )

    # ──────────────────────────────────────────────────────────────────
    # defect_rate_30d defensive default
    # ──────────────────────────────────────────────────────────────────
    def test_50_defect_rate_zero_without_mi_check_model(self):
        """The generic product_graph repo never depends on mi.check.
        defect_rate_30d must compute to 0.0 in this environment without
        crashing, even when the vendor has live PO line traffic."""
        self._make_po()
        # mi.check / southbrook.mi.check are not in this env.
        for name in ('mi.check', 'southbrook.mi.check'):
            self.assertNotIn(name, self.env)
        self.vendor.invalidate_recordset(['defect_rate_30d'])
        self.assertEqual(
            self.vendor.defect_rate_30d, 0.0,
            "Without an mi.check model, defect_rate_30d must be 0.0.",
        )

    # ──────────────────────────────────────────────────────────────────
    # Drill-through actions are well-formed
    # ──────────────────────────────────────────────────────────────────
    def test_60_smart_button_actions_return_act_window(self):
        for method in ('action_view_open_pos_all',
                       'action_view_late_pos',
                       'action_view_defect_lines_30d'):
            action = getattr(self.vendor, method)()
            self.assertEqual(action['type'], 'ir.actions.act_window')
            self.assertEqual(action['res_model'], (
                'purchase.order' if 'pos' in method or 'late' in method
                else 'purchase.order.line'
            ))
            self.assertIn('domain', action)
