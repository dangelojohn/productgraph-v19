# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestProcurementBridge(TransactionCase):

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

        # Test item linked to a real product.product so the requisition
        # can carry a product line.
        cls.product = cls.env['product.product'].create({
            'name': 'Procurement test product',
            'type': 'consu',
            'is_storable': True,
        })
        cls.item = cls.env['pg.item'].create({
            'name': 'Procurement Test Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()
        cls.item.product_id = cls.product.id

        # Vendor + linked res.partner.
        cls.partner = cls.env['res.partner'].create({
            'name': 'Acme Supplier',
            'company_type': 'company',
        })
        cls.vendor = cls.env['pg.vendor'].create({
            'code': 'PROC-1',
            'name': 'Acme Supplier',
            'partner_id': cls.partner.id,
        })
        cls.vendor.action_to_probationary()
        cls.vendor.action_qualify()

    def _make_rfq(self, with_bid=True):
        rfq = self.env['pg.rfq'].create({
            'title': 'Procurement Test RFQ',
        })
        self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': self.item.id,
            'qty': 100.0,
        })
        if with_bid:
            self.env['pg.rfq.bid'].create({
                'line_id': rfq.line_ids.id,
                'vendor_id': self.vendor.id,
                'vendor_part_number': 'ACME-001',
                'unit_price': 7.50,
                'lead_time_days': 14,
                'is_awarded': True,
            })
        return rfq

    # ──────────────────────────────────────────────────────────────────
    # Happy path
    # ──────────────────────────────────────────────────────────────────
    def test_award_creates_requisition(self):
        rfq = self._make_rfq()
        rfq.action_send()
        rfq.action_mark_received()
        rfq.action_award()
        self.assertTrue(rfq.requisition_id)
        self.assertEqual(rfq.requisition_id.reference, rfq.name)
        # One line with the bid's price + qty
        self.assertEqual(len(rfq.requisition_id.line_ids), 1)
        line = rfq.requisition_id.line_ids
        self.assertEqual(line.price_unit, 7.50)
        self.assertEqual(line.product_qty, 100.0)
        self.assertEqual(line.product_id, self.product)

    def test_single_vendor_requisition_carries_vendor_id(self):
        rfq = self._make_rfq()
        rfq.action_send()
        rfq.action_mark_received()
        rfq.action_award()
        if 'vendor_id' in rfq.requisition_id._fields:
            self.assertEqual(rfq.requisition_id.vendor_id, self.partner)

    def test_auto_create_off_skips_requisition(self):
        rfq = self._make_rfq()
        rfq.auto_create_requisition = False
        rfq.action_send()
        rfq.action_mark_received()
        rfq.action_award()
        self.assertFalse(rfq.requisition_id)

    # ──────────────────────────────────────────────────────────────────
    # Skip when item has no Odoo product
    # ──────────────────────────────────────────────────────────────────
    def test_item_without_product_logs_and_skips(self):
        unreleased = self.env['pg.item'].create({
            'name': 'Unreleased',
            'item_type': 'purchased',
            'category_id': self.cat.id,
        })
        unreleased.action_to_engineering()
        rfq = self.env['pg.rfq'].create({'title': 'No-product RFQ'})
        self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': unreleased.id,
            'qty': 1.0,
        })
        self.env['pg.rfq.bid'].create({
            'line_id': rfq.line_ids.id,
            'vendor_id': self.vendor.id,
            'unit_price': 1.0,
            'is_awarded': True,
        })
        rfq.action_send()
        rfq.action_mark_received()
        rfq.action_award()
        # Requisition was created but has no lines (the only line had
        # no linked product so was skipped).
        self.assertTrue(rfq.requisition_id)
        self.assertEqual(len(rfq.requisition_id.line_ids), 0)
