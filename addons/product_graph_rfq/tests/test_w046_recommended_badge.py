# -*- coding: utf-8 -*-
"""W046 + W058 — recommended_bid_id surfacing in pg.rfq.line list view.

W046 (R6.11): surface recommended_bid_id as an optional list column with
              a "Recommended" badge (widget="badge").
W058 (R6.11): row decoration-info when has_recommendation is True.

These changes also introduce a thin stored boolean
`pg.rfq.line.has_recommendation` so both the row decoration and the
badge have a stable signal to bind to.

This file tests the model-side compute. The view-side widget + decoration
is exercised by view validation on module install (the list-view ParseError
that would fire if has_recommendation didn't exist or if the binding was
wrong is a load-time gate, not a runtime one).
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'w046', 'w058')
class TestRecommendedBadge(TransactionCase):

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
            'name': 'W046 Test Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()

        cls.vendor_a = cls.env['pg.vendor'].create(
            {'code': 'W46A', 'name': 'W46 A Co'})
        cls.vendor_a.action_to_probationary()
        cls.vendor_a.action_qualify()
        cls.vendor_b = cls.env['pg.vendor'].create(
            {'code': 'W46B', 'name': 'W46 B Co'})
        cls.vendor_b.action_to_probationary()
        cls.vendor_b.action_qualify()

    def _make_line_with_bids(self):
        rfq = self.env['pg.rfq'].create({'title': 'W046 RFQ'})
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': self.item.id,
            'qty': 50.0,
        })
        rfq.action_send()
        self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': self.vendor_a.id,
            'unit_price': 9.50,
            'vendor_part_number': 'A-VPN',
            'lead_time_days': 14,
            'moq': 25,
        })
        self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': self.vendor_b.id,
            'unit_price': 8.75,
            'vendor_part_number': 'B-VPN',
            'lead_time_days': 21,
            'moq': 50,
        })
        return rfq, line

    def test_10_has_recommendation_false_for_empty_line(self):
        """A line without bids has no recommendation."""
        rfq = self.env['pg.rfq'].create({'title': 'W046 empty'})
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': self.item.id,
            'qty': 1.0,
        })
        self.assertFalse(
            line.recommended_bid_id,
            "Empty line should have no recommended bid.",
        )
        self.assertFalse(
            line.has_recommendation,
            "has_recommendation should be False without bids.",
        )

    def test_20_has_recommendation_true_when_bids_present(self):
        """Once the auto-ranker picks a bid, has_recommendation flips."""
        _, line = self._make_line_with_bids()
        self.assertTrue(
            line.recommended_bid_id,
            "Auto-ranker should have selected one of the two bids.",
        )
        self.assertTrue(
            line.has_recommendation,
            "has_recommendation must follow recommended_bid_id.",
        )

    def test_30_has_recommendation_is_stored_and_searchable(self):
        """W058 row decoration binds against this field — it must be
        stored so a list-view domain can use it without forcing a per-
        row compute on every paint."""
        _, line = self._make_line_with_bids()
        # Search uses the stored column directly. This would raise
        # if the field weren't stored.
        found = self.env['pg.rfq.line'].search([
            ('has_recommendation', '=', True),
            ('id', '=', line.id),
        ])
        self.assertEqual(
            found, line,
            "Stored has_recommendation must be domain-searchable.",
        )
