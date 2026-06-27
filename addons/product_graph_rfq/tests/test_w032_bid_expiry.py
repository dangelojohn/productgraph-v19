# -*- coding: utf-8 -*-
"""W032 (R6.7) — bid-expiry cron + recommended_bid_id zero-out.

The cron walks pg.rfq.bid records where valid_until < today and:
  * flips is_expired = True (+ stamps expired_on)
  * clears the parent line's recommended_bid_id when it pointed at the
    expired bid (re-evaluated against surviving in-date bids on next read)

The cron is idempotent — running it twice in a row produces no further
flagging, no duplicate chatter, no duplicate audit-log entries past the
initial flip.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'w032')
class TestW032BidExpiry(TransactionCase):

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
            'name': 'W032 Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()

        cls.vendor_a = cls.env['pg.vendor'].create(
            {'code': 'W32A', 'name': 'W32 A Co'})
        cls.vendor_a.action_to_probationary()
        cls.vendor_a.action_qualify()
        cls.vendor_b = cls.env['pg.vendor'].create(
            {'code': 'W32B', 'name': 'W32 B Co'})
        cls.vendor_b.action_to_probationary()
        cls.vendor_b.action_qualify()

    def _make_rfq_with_two_bids(self):
        """Stale (vendor_a, $8 — cheaper) vs fresh (vendor_b, $10)."""
        today = fields.Date.context_today(self.env['pg.rfq'])
        past = today - timedelta(days=7)
        future = today + timedelta(days=30)
        rfq = self.env['pg.rfq'].create({'title': 'W032 RFQ'})
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': self.item.id,
            'qty': 10.0,
        })
        rfq.action_send()
        stale = self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': self.vendor_a.id,
            'unit_price': 8.0,
            'lead_time_days': 14,
            'moq': 1.0,
            'valid_until': past,
        })
        fresh = self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': self.vendor_b.id,
            'unit_price': 10.0,
            'lead_time_days': 14,
            'moq': 1.0,
            'valid_until': future,
        })
        return rfq, line, stale, fresh

    def test_10_cron_flips_is_expired_on_past_bids(self):
        """Past valid_until → is_expired flipped + expired_on stamped."""
        _, _, stale, fresh = self._make_rfq_with_two_bids()
        flagged = self.env['pg.rfq.bid']._cron_flag_expired_bids()
        self.assertIn(stale, flagged)
        self.assertNotIn(fresh, flagged)
        stale.invalidate_recordset()
        fresh.invalidate_recordset()
        self.assertTrue(stale.is_expired)
        self.assertTrue(stale.expired_on)
        self.assertFalse(fresh.is_expired)

    def test_20_recommendation_drops_expired_bid(self):
        """Recommendation must re-rank to the surviving fresh bid even
        though the expired bid had the cheaper price."""
        rfq, line, stale, fresh = self._make_rfq_with_two_bids()
        # Before cron: stale (cheaper) is recommended.
        self.assertEqual(
            line.recommended_bid_id, stale,
            'Pre-expiry, cheapest bid should be recommended.',
        )
        self.env['pg.rfq.bid']._cron_flag_expired_bids()
        line.invalidate_recordset()
        self.assertEqual(
            line.recommended_bid_id, fresh,
            'After expiry-flag, recommendation must skip the expired bid.',
        )

    def test_30_cron_idempotent_on_second_run(self):
        """Re-running the cron must not re-flag already-expired bids
        nor post duplicate chatter."""
        rfq, _, stale, _ = self._make_rfq_with_two_bids()
        first = self.env['pg.rfq.bid']._cron_flag_expired_bids()
        self.assertIn(stale, first)
        first_expired_on = stale.expired_on
        # Second run — nothing left to flag.
        second = self.env['pg.rfq.bid']._cron_flag_expired_bids()
        self.assertFalse(
            second,
            'Cron must be idempotent — no records flagged on re-run.',
        )
        stale.invalidate_recordset()
        self.assertEqual(
            stale.expired_on, first_expired_on,
            'expired_on must not be overwritten on subsequent cron runs.',
        )

    def test_40_recommendation_none_when_all_expired(self):
        """If every bid on a line expires, the recommendation goes to
        False — buyer needs to re-RFQ from scratch."""
        today = fields.Date.context_today(self.env['pg.rfq'])
        past = today - timedelta(days=3)
        rfq = self.env['pg.rfq'].create({'title': 'W032 all-stale'})
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': self.item.id,
            'qty': 5.0,
        })
        rfq.action_send()
        self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': self.vendor_a.id,
            'unit_price': 7.0,
            'valid_until': past,
        })
        self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': self.vendor_b.id,
            'unit_price': 9.0,
            'valid_until': past,
        })
        self.assertTrue(
            line.recommended_bid_id,
            'Pre-cron, line should have a recommendation.',
        )
        self.env['pg.rfq.bid']._cron_flag_expired_bids()
        line.invalidate_recordset()
        self.assertFalse(
            line.recommended_bid_id,
            'When every bid is expired, recommendation must be cleared.',
        )
