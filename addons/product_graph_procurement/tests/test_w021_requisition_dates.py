# -*- coding: utf-8 -*-
"""W021 — bridge stamps date_end + schedule_date + lead-time annotation.

The bridge now stamps the buyer-relevant dates on the purchase.requisition
when it's created from an awarded RFQ. Verify:

* schedule_date = today + max(lead_time_days) across awarded bids;
* date_end = min(valid_until) across awarded bids when set, else
  today + max_lead + 30d buffer;
* the lead-time-per-vendor annotation lands in the requisition chatter.
"""
from datetime import date, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestW021RequisitionDates(TransactionCase):

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
            'name': 'W021 test product',
            'type': 'consu',
            'is_storable': True,
        })
        cls.item = cls.env['pg.item'].create({
            'name': 'W021 test item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()
        cls.item.product_id = cls.product.id

        cls.partner = cls.env['res.partner'].create({
            'name': 'W021 Supplier',
            'company_type': 'company',
        })
        cls.vendor = cls.env['pg.vendor'].create({
            'code': 'W021-1',
            'name': 'W021 Supplier',
            'partner_id': cls.partner.id,
        })
        cls.vendor.action_to_probationary()
        cls.vendor.action_qualify()

    def _award_rfq(self, lead_days, valid_until=None):
        rfq = self.env['pg.rfq'].create({'title': 'W021 RFQ'})
        self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': self.item.id,
            'qty': 50.0,
        })
        bid_vals = {
            'line_id': rfq.line_ids.id,
            'vendor_id': self.vendor.id,
            'unit_price': 9.99,
            'lead_time_days': lead_days,
            'is_awarded': True,
        }
        if valid_until is not None:
            bid_vals['valid_until'] = valid_until
        self.env['pg.rfq.bid'].create(bid_vals)
        rfq.action_send()
        rfq.action_mark_received()
        rfq.action_award()
        return rfq

    def test_schedule_date_set_from_max_lead(self):
        rfq = self._award_rfq(lead_days=21)
        req = rfq.requisition_id
        self.assertTrue(req, "Requisition was not created")
        if 'schedule_date' in req._fields:
            self.assertEqual(
                req.schedule_date,
                date.today() + timedelta(days=21),
                "schedule_date should be today + max lead-time",
            )

    def test_date_end_uses_quote_valid_until_when_present(self):
        cutoff = date.today() + timedelta(days=45)
        rfq = self._award_rfq(lead_days=10, valid_until=cutoff)
        req = rfq.requisition_id
        if 'date_end' in req._fields:
            self.assertEqual(
                req.date_end, cutoff,
                "date_end should track the bid's quote-valid-until",
            )

    def test_lead_time_annotation_posted_to_chatter(self):
        rfq = self._award_rfq(lead_days=14)
        req = rfq.requisition_id
        # Find the lead-time summary note in the requisition chatter.
        messages = req.message_ids.filtered(
            lambda m: m.body and 'lead-time summary' in (m.body or '').lower()
        )
        self.assertTrue(
            messages,
            "Bridge should post a lead-time summary to requisition chatter",
        )
