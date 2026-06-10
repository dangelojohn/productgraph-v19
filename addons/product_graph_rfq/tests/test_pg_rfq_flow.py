# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgRfqFlow(TransactionCase):

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
            'name': 'RFQ Test Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()

        cls.vendor_a = cls.env['pg.vendor'].create({'code': 'A', 'name': 'A Co'})
        cls.vendor_a.action_to_probationary()
        cls.vendor_a.action_qualify()
        cls.vendor_b = cls.env['pg.vendor'].create({'code': 'B', 'name': 'B Co'})
        cls.vendor_b.action_to_probationary()
        cls.vendor_b.action_qualify()

    def _make_rfq(self, with_line=True, promote=False):
        rfq = self.env['pg.rfq'].create({
            'title': 'Test RFQ',
            'promote_to_preferred': promote,
        })
        if with_line:
            self.env['pg.rfq.line'].create({
                'rfq_id': rfq.id,
                'item_id': self.item.id,
                'qty': 100.0,
            })
        return rfq

    def _add_bid(self, line, vendor, price, vpn='VPN', awarded=False, lead=14, moq=50):
        return self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': vendor.id,
            'unit_price': price,
            'vendor_part_number': vpn,
            'is_awarded': awarded,
            'lead_time_days': lead,
            'moq': moq,
        })

    # ──────────────────────────────────────────────────────────────────
    # Happy path
    # ──────────────────────────────────────────────────────────────────
    def test_full_award_creates_vendor_part(self):
        rfq = self._make_rfq()
        self.assertTrue(rfq.name.startswith('RFQ/'))
        rfq.action_send()
        self.assertEqual(rfq.state, 'sent')
        self.assertTrue(rfq.sent_on)

        line = rfq.line_ids
        self._add_bid(line, self.vendor_a, 12.50)
        self._add_bid(line, self.vendor_b, 11.75, awarded=True, lead=21, moq=100)

        rfq.action_mark_received()
        self.assertEqual(rfq.state, 'received')

        rfq.action_award()
        self.assertEqual(rfq.state, 'awarded')
        self.assertTrue(rfq.awarded_on)

        # AVL row should now exist for (item, vendor_b) with bid economics.
        part = self.env['pg.vendor.part'].search([
            ('item_id', '=', self.item.id),
            ('vendor_id', '=', self.vendor_b.id),
        ])
        self.assertEqual(len(part), 1)
        self.assertEqual(part.unit_price_hint, 11.75)
        self.assertEqual(part.lead_time_days, 21)
        self.assertEqual(part.moq, 100)

    def test_award_promotes_to_preferred(self):
        rfq = self._make_rfq(promote=True)
        rfq.action_send()
        self._add_bid(rfq.line_ids, self.vendor_a, 10.0, awarded=True)
        rfq.action_mark_received()
        rfq.action_award()
        part = self.env['pg.vendor.part'].search([
            ('item_id', '=', self.item.id),
            ('vendor_id', '=', self.vendor_a.id),
        ])
        self.assertTrue(part.is_preferred)

    def test_award_demotes_previous_preferred(self):
        # Pre-existing preferred from vendor_a
        existing = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor_a.id,
            'is_preferred': True,
        })
        rfq = self._make_rfq(promote=True)
        rfq.action_send()
        self._add_bid(rfq.line_ids, self.vendor_b, 9.0, awarded=True)
        rfq.action_mark_received()
        rfq.action_award()
        existing.invalidate_recordset()
        self.assertFalse(existing.is_preferred)
        new_part = self.env['pg.vendor.part'].search([
            ('item_id', '=', self.item.id),
            ('vendor_id', '=', self.vendor_b.id),
        ])
        self.assertTrue(new_part.is_preferred)

    def test_award_updates_existing_avl_row(self):
        existing = self.env['pg.vendor.part'].create({
            'item_id': self.item.id,
            'vendor_id': self.vendor_a.id,
            'unit_price_hint': 999.0,
            'lead_time_days': 99,
        })
        rfq = self._make_rfq()
        rfq.action_send()
        self._add_bid(rfq.line_ids, self.vendor_a, 7.5, awarded=True, lead=10)
        rfq.action_mark_received()
        rfq.action_award()
        existing.invalidate_recordset()
        self.assertEqual(existing.unit_price_hint, 7.5)
        self.assertEqual(existing.lead_time_days, 10)

    # ──────────────────────────────────────────────────────────────────
    # Negative paths
    # ──────────────────────────────────────────────────────────────────
    def test_send_without_lines_raises(self):
        rfq = self._make_rfq(with_line=False)
        with self.assertRaises(ValidationError):
            rfq.action_send()

    def test_mark_received_requires_bids(self):
        rfq = self._make_rfq()
        rfq.action_send()
        with self.assertRaises(ValidationError):
            rfq.action_mark_received()

    def test_award_requires_one_awarded_bid_per_line(self):
        rfq = self._make_rfq()
        rfq.action_send()
        self._add_bid(rfq.line_ids, self.vendor_a, 10.0)  # not awarded
        self._add_bid(rfq.line_ids, self.vendor_b, 11.0)  # not awarded
        rfq.action_mark_received()
        with self.assertRaises(ValidationError):
            rfq.action_award()

    def test_two_awarded_bids_per_line_blocked_by_constraint(self):
        rfq = self._make_rfq()
        rfq.action_send()
        self._add_bid(rfq.line_ids, self.vendor_a, 10.0, awarded=True)
        with self.assertRaises(ValidationError):
            self._add_bid(rfq.line_ids, self.vendor_b, 11.0, awarded=True)

    def test_cancel_from_sent(self):
        rfq = self._make_rfq()
        rfq.action_send()
        rfq.action_cancel()
        self.assertEqual(rfq.state, 'cancelled')
        rfq.unlink()  # cancelled is unlinkable

    def test_negative_price_blocked(self):
        rfq = self._make_rfq()
        rfq.action_send()
        with self.assertRaises(ValidationError):
            self._add_bid(rfq.line_ids, self.vendor_a, -1.0)

    # ──────────────────────────────────────────────────────────────────
    # Immutability after award
    # ──────────────────────────────────────────────────────────────────
    def test_awarded_rfq_blocks_title_write(self):
        rfq = self._make_rfq()
        rfq.action_send()
        self._add_bid(rfq.line_ids, self.vendor_a, 10.0, awarded=True)
        rfq.action_mark_received()
        rfq.action_award()
        with self.assertRaises(UserError):
            rfq.write({'title': 'changed'})

    def test_awarded_bid_unlink_blocked(self):
        rfq = self._make_rfq()
        rfq.action_send()
        bid = self._add_bid(rfq.line_ids, self.vendor_a, 10.0, awarded=True)
        rfq.action_mark_received()
        rfq.action_award()
        with self.assertRaises(UserError):
            bid.unlink()
