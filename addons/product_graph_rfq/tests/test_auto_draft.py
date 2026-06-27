# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgRfqAutoDraft(TransactionCase):
    """PG-133 — auto-draft, bid scoring, recommended-bid, no-auto-award."""

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

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def _make_purchased_item(self, name='Auto Item', state='engineering'):
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': 'purchased',
            'category_id': self.cat.id,
        })
        if state == 'engineering':
            item.action_to_engineering()
        return item

    def _make_vendor(self, code, qualified=False, categories=None):
        v = self.env['pg.vendor'].create({
            'code': code, 'name': f'Vendor {code}',
        })
        if categories:
            v.category_ids = [(6, 0, [c.id for c in categories])]
        if qualified:
            v.action_to_probationary()
            v.action_qualify()
        return v

    def _make_part(self, item, vendor, vpn='VPN-1', preferred=False):
        return self.env['pg.vendor.part'].create({
            'item_id': item.id,
            'vendor_id': vendor.id,
            'vendor_part_number': vpn,
            'is_preferred': preferred,
        })

    def _make_rfq_with_bids(self, item, bid_specs):
        """bid_specs: list of (vendor, unit_price, lead, moq) tuples."""
        rfq = self.env['pg.rfq'].create({
            'title': 'Scoring test',
            'item_id': item.id,
        })
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': item.id,
            'qty': 1.0,
        })
        bids = self.env['pg.rfq.bid']
        for vendor, price, lead, moq in bid_specs:
            bids |= self.env['pg.rfq.bid'].create({
                'line_id': line.id,
                'vendor_id': vendor.id,
                'unit_price': price,
                'lead_time_days': lead,
                'moq': moq,
            })
        return rfq, line, bids

    # ──────────────────────────────────────────────────────────────────
    # TASK 1 — auto-draft cron
    # ──────────────────────────────────────────────────────────────────
    def test_auto_draft_skips_item_with_qualified_vendor(self):
        item = self._make_purchased_item('Sourced')
        v = self._make_vendor('SRC1', qualified=True, categories=[self.cat])
        self._make_part(item, v)
        before = self.env['pg.rfq'].search_count([('item_id', '=', item.id)])
        self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        after = self.env['pg.rfq'].search_count([('item_id', '=', item.id)])
        self.assertEqual(after, before, 'Cron should not draft when item has a qualified vendor.')

    def test_auto_draft_creates_rfq_for_unsourced_item(self):
        item = self._make_purchased_item('Unsourced')
        v1 = self._make_vendor('CAT1', qualified=True, categories=[self.cat])
        v2 = self._make_vendor('CAT2', qualified=True, categories=[self.cat])
        # Important: do NOT attach v1/v2 to item via pg.vendor.part —
        # auto-draft should fire precisely because the AVL is empty.
        result = self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        self.assertTrue(result, 'Cron should return created records.')
        rfq = self.env['pg.rfq'].search([
            ('item_id', '=', item.id), ('auto_drafted', '=', True),
        ])
        self.assertEqual(len(rfq), 1)
        self.assertEqual(rfq.state, 'draft', 'Auto-drafted RFQs stay in draft until human Send.')
        self.assertTrue(rfq.reason, 'Reason text should be populated.')
        # Bid placeholders for both candidate vendors.
        self.assertEqual(len(rfq.line_ids), 1)
        self.assertEqual(len(rfq.line_ids.bid_ids), 2)
        self.assertEqual(set(rfq.line_ids.bid_ids.mapped('vendor_id')), {v1, v2})
        # None of the seeded bids may be flagged as awarded.
        self.assertFalse(any(rfq.line_ids.bid_ids.mapped('is_awarded')))

    def test_auto_draft_skips_if_open_rfq_exists(self):
        item = self._make_purchased_item('AlreadyOpen')
        self._make_vendor('CAT1', qualified=True, categories=[self.cat])
        # Pre-existing draft RFQ for this item.
        self.env['pg.rfq'].create({
            'title': 'Manual', 'item_id': item.id,
        })
        result = self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        # Filter to THIS item — demo data may include other purchased items
        # that legitimately auto-draft.
        this_item_created = result.filtered(lambda r: r.item_id == item)
        self.assertFalse(
            this_item_created,
            'Cron must skip items that already have an open RFQ.',
        )
        rfqs = self.env['pg.rfq'].search([('item_id', '=', item.id)])
        self.assertEqual(len(rfqs), 1, 'Only the original RFQ should exist.')

    def test_auto_draft_logs_when_no_category_vendor(self):
        item = self._make_purchased_item('NoCategoryVendor')
        # No vendors at all in this category.
        result = self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        # The cron walks all items; result may include other auto-drafts
        # from other tests' fixtures within this same transaction, but
        # never one for this item.
        rfq = self.env['pg.rfq'].search([('item_id', '=', item.id)])
        self.assertFalse(rfq, 'No RFQ should be drafted when no category vendor exists.')

    # ──────────────────────────────────────────────────────────────────
    # W013 / R6.4 — unsourced-item activity (Sourcing Inbox)
    # ──────────────────────────────────────────────────────────────────
    def test_unsourced_no_category_vendor_creates_activity(self):
        """Auto-RFQ failure on no-qualified-vendor must surface as an
        mail.activity on the pg.item, not just a buried log line."""
        item = self._make_purchased_item('ActivityNoVendor')
        # No vendors qualified in this category.
        self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', item.id),
            ('summary', '=', 'Unsourced item — needs vendor'),
        ])
        self.assertEqual(
            len(activities), 1,
            'Auto-RFQ cron should create exactly one unsourced-item activity.',
        )
        # Assignee should be the item's responsible engineer (admin in tests).
        self.assertEqual(activities.user_id, item.responsible_id)

    def test_unsourced_activity_idempotent_across_cron_runs(self):
        """Re-running the daily cron must NOT create duplicate activities for
        the same item."""
        item = self._make_purchased_item('IdempotentActivity')
        self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', item.id),
            ('summary', '=', 'Unsourced item — needs vendor'),
        ])
        self.assertEqual(
            len(activities), 1,
            'Cron must be idempotent — only one open activity per item.',
        )

    def test_unsourced_activity_skipped_when_vendor_qualifies(self):
        """If a qualified category-vendor IS present, no activity should fire."""
        item = self._make_purchased_item('HasCategoryVendor')
        self._make_vendor('SOURCED', qualified=True, categories=[self.cat])
        self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', item.id),
            ('summary', '=', 'Unsourced item — needs vendor'),
        ])
        self.assertFalse(
            activities,
            'No unsourced activity should be created when a category vendor exists.',
        )

    def test_sourcing_inbox_action_domain_filters_unsourced(self):
        """The Sourcing Inbox action's domain must return unsourced purchased
        items in engineering/released and EXCLUDE items with a primary vendor."""
        # Item A: purchased, engineering, NO vendor → should appear.
        item_a = self._make_purchased_item('InboxUnsourcedA')
        # Item B: purchased, engineering, HAS preferred qualified vendor → excluded.
        item_b = self._make_purchased_item('InboxSourcedB')
        v = self._make_vendor('INBOXV', qualified=True, categories=[self.cat])
        self._make_part(item_b, v, vpn='VPN-B', preferred=True)
        action = self.env.ref('product_graph_rfq.action_pg_sourcing_inbox')
        # action.domain is a string — eval it the way Odoo does.
        from odoo.tools.safe_eval import safe_eval
        domain = safe_eval(action.domain)
        ids_in_inbox = self.env['pg.item'].search(domain).ids
        self.assertIn(
            item_a.id, ids_in_inbox,
            'Unsourced engineering-state purchased item must appear in inbox.',
        )
        self.assertNotIn(
            item_b.id, ids_in_inbox,
            'Item with qualified preferred vendor must NOT appear in inbox.',
        )

    def test_auto_draft_idempotent(self):
        item = self._make_purchased_item('Idempotent')
        self._make_vendor('CAT1', qualified=True, categories=[self.cat])
        self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        first = self.env['pg.rfq'].search([('item_id', '=', item.id)])
        # Second run with the same state — no new RFQ.
        self.env['pg.rfq']._cron_auto_draft_missing_rfqs()
        second = self.env['pg.rfq'].search([('item_id', '=', item.id)])
        self.assertEqual(first, second, 'Second cron run must not duplicate.')

    # ──────────────────────────────────────────────────────────────────
    # TASK 2 — bid score
    # ──────────────────────────────────────────────────────────────────
    def test_bid_score_lowest_price_wins_single_dimension(self):
        item = self._make_purchased_item('ScorePrice')
        va = self._make_vendor('VA', qualified=True)
        vb = self._make_vendor('VB', qualified=True)
        vc = self._make_vendor('VC', qualified=True)
        # Same lead/moq, different prices.
        rfq, line, bids = self._make_rfq_with_bids(item, [
            (va, 10.0, 14, 50),
            (vb, 11.0, 14, 50),
            (vc, 12.0, 14, 50),
        ])
        line.invalidate_recordset()
        winner = line.recommended_bid_id
        self.assertEqual(winner.vendor_id, va, 'Cheapest bid should be recommended.')
        # Only the price dimension varies; weights collapse.
        self.assertAlmostEqual(winner.score, 0.0, places=4)

    def test_bid_score_combined_factors(self):
        item = self._make_purchased_item('ScoreCombined')
        va = self._make_vendor('CA', qualified=True)
        vb = self._make_vendor('CB', qualified=True)
        vc = self._make_vendor('CC', qualified=True)
        # va: best price, worst lead/moq
        # vb: midpoint on all three dimensions (linear)
        # vc: worst price, best lead/moq
        # MOQ chosen as 62.5 = (100+25)/2 so vb normalizes to 0.5
        # like price (15 = (10+20)/2) and lead (20 = (10+30)/2).
        rfq, line, bids = self._make_rfq_with_bids(item, [
            (va, 10.0, 30, 100.0),
            (vb, 15.0, 20, 62.5),
            (vc, 20.0, 10, 25.0),
        ])
        line.invalidate_recordset()
        scores = {b.vendor_id.code: b.score for b in line.bid_ids}
        # va: 0.5*0 + 0.3*1 + 0.2*1 = 0.5
        # vb: 0.5*0.5 + 0.3*0.5 + 0.2*0.5 = 0.5
        # vc: 0.5*1 + 0.3*0 + 0.2*0 = 0.5
        # All three tie at 0.5 — recommendation falls back to lowest unit_price.
        for code, s in scores.items():
            self.assertAlmostEqual(s, 0.5, places=4, msg=f'{code} expected 0.5')
        self.assertEqual(
            line.recommended_bid_id.vendor_id, va,
            'On a 3-way tie, lowest unit_price wins the recommendation.',
        )

    def test_bid_score_single_bid_is_zero(self):
        item = self._make_purchased_item('SingleBid')
        va = self._make_vendor('S1', qualified=True)
        _, line, bids = self._make_rfq_with_bids(item, [
            (va, 99.0, 7, 1),
        ])
        line.invalidate_recordset()
        self.assertEqual(bids.score, 0.0)
        self.assertEqual(line.recommended_bid_id, bids)

    # ──────────────────────────────────────────────────────────────────
    # TASK 3 — recommendation never auto-awards
    # ──────────────────────────────────────────────────────────────────
    def test_recommendation_is_not_award(self):
        item = self._make_purchased_item('RecNotAward')
        va = self._make_vendor('NA1', qualified=True)
        vb = self._make_vendor('NA2', qualified=True)
        rfq, line, bids = self._make_rfq_with_bids(item, [
            (va, 8.0, 14, 50),
            (vb, 20.0, 14, 50),
        ])
        line.invalidate_recordset()
        self.assertEqual(line.recommended_bid_id.vendor_id, va)
        # The recommendation never sets is_awarded.
        self.assertFalse(line.recommended_bid_id.is_awarded)
        self.assertFalse(any(line.bid_ids.mapped('is_awarded')))
        # And recomputing score does not flip the flag.
        line.bid_ids._compute_score()
        line.invalidate_recordset()
        self.assertFalse(any(line.bid_ids.mapped('is_awarded')))
