# -*- coding: utf-8 -*-
"""Performance benchmarks for pg.relationship.closure.

Targets are from Architecture Bible §12.2 (Where-Used 3 levels deep
< 3s target, < 5s hard limit).

The benchmarks build a synthetic graph at three scales and assert each
read path stays within the hard limit. Useful as a CI smoke test on
performance — failing here means somebody changed the closure
indexes or the rebuild algorithm and made traversal slower.
"""
import time

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'perf')
class TestClosurePerformance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

    def _make_items(self, n):
        """Bulk-create n items. Stays in 'concept' state — relationship
        edges work regardless of item lifecycle, so we skip
        action_to_engineering() per item (which serialises N message_post
        + audit_log writes and dominates per-test runtime)."""
        Item = self.env['pg.item']
        return Item.create([{
            'name': f'PerfItem {i}',
            'item_type': 'part',
            'category_id': self.cat.id,
        } for i in range(n)])

    def _make_substitute_chain(self, items):
        """Build a substitute chain with the bulk-load context flag so
        the closure is rebuilt once at the end rather than per-edge."""
        Rel = self.env['pg.relationship'].with_context(
            _pg_relationship_skip_closure=True)
        Rel.create([{
            'source_item_id': items[i].id,
            'target_item_id': items[i + 1].id,
            'kind': 'substitutes',
        } for i in range(len(items) - 1)])
        self.env['pg.relationship.closure']._rebuild_kind('substitutes')

    def _make_supersession_chain(self, items):
        """Directed supersession chain: items[i+1] supersedes items[i]."""
        Rel = self.env['pg.relationship'].with_context(
            _pg_relationship_skip_closure=True)
        Rel.create([{
            'source_item_id': items[i + 1].id,
            'target_item_id': items[i].id,
            'kind': 'supersedes',
        } for i in range(len(items) - 1)])
        self.env['pg.relationship.closure']._rebuild_kind('supersedes')

    # ──────────────────────────────────────────────────────────────────
    # Small scale (50 items, 49 edges) — sanity check + warmup
    # ──────────────────────────────────────────────────────────────────
    def test_small_scale_substitute_query(self):
        items = self._make_items(50)
        self._make_substitute_chain(items)
        Closure = self.env['pg.relationship.closure']
        start = time.monotonic()
        reachable = Closure.get_reachable(items[0], 'substitutes')
        elapsed = time.monotonic() - start
        # All 49 other items reachable, but symmetric — so 49 reachable.
        self.assertEqual(len(reachable), 49)
        # Should be near-instant.
        self.assertLess(elapsed, 1.0,
                        f'Small-scale query took {elapsed:.3f}s')

    # ──────────────────────────────────────────────────────────────────
    # Medium scale (200 items, 199 edges) — 3-hop where-used target
    # ──────────────────────────────────────────────────────────────────
    def test_medium_scale_three_hop_query(self):
        items = self._make_items(200)
        self._make_supersession_chain(items)
        Closure = self.env['pg.relationship.closure']
        start = time.monotonic()
        # 3-hop from a mid-chain item.
        result = Closure.get_reachable(items[100], 'supersedes', max_depth=3)
        elapsed = time.monotonic() - start
        # supersedes is directed forward — from item 100 we reach
        # items[99], [98], [97] (3 hops back through the chain).
        # Note: 'forward' from items[i] = chain target = items[i-1]
        # because edge was items[i+1] supersedes items[i] (source→target).
        # So items[100]'s outgoing edges target items[99]. 3 hops →
        # items 99, 98, 97.
        self.assertEqual(len(result), 3)
        # Bible §12.2 target for Where-Used 3 levels: < 3s.
        self.assertLess(elapsed, 3.0,
                        f'Three-hop where-used took {elapsed:.3f}s '
                        f'(Bible §12.2 target: < 3s, hard limit: 5s)')

    # ──────────────────────────────────────────────────────────────────
    # Large scale (300 items, 299 edges) — closure rebuild stress.
    #
    # A chain of N items in symmetric (substitutes) kind produces
    # N×(N-1) shortest-path closure rows (≈ N²). 300 items = ~90k
    # closure rows — already a stress test for the CTE; growing to
    # 500 (≈ 250k rows) was murder on the recursive-CTE planner
    # without buying real signal for a sideways-graph use case where
    # < 1000 active edges per kind is the realistic upper bound.
    # ──────────────────────────────────────────────────────────────────
    def test_large_scale_closure_rebuild(self):
        items = self._make_items(300)
        # Bulk-create with the skip-closure context flag so we time the
        # ONE rebuild at the end, not 299 incremental rebuilds.
        Rel = self.env['pg.relationship'].with_context(
            _pg_relationship_skip_closure=True)
        rows = [{
            'source_item_id': items[i].id,
            'target_item_id': items[i + 1].id,
            'kind': 'substitutes',
        } for i in range(len(items) - 1)]
        Rel.create(rows)

        Closure = self.env['pg.relationship.closure']
        start = time.monotonic()
        count = Closure._rebuild_kind('substitutes')
        elapsed = time.monotonic() - start

        self.assertGreater(count, 0)
        self.assertLess(elapsed, 15.0,
                        f'300-item closure rebuild took {elapsed:.3f}s')

    # ──────────────────────────────────────────────────────────────────
    # Query-after-rebuild on the 300-item closure
    # ──────────────────────────────────────────────────────────────────
    def test_large_scale_three_hop_after_full_rebuild(self):
        items = self._make_items(300)
        self._make_supersession_chain(items)
        Closure = self.env['pg.relationship.closure']
        # Already triggered rebuild on create; query directly.
        start = time.monotonic()
        result = Closure.get_reachable(items[150], 'supersedes', max_depth=3)
        elapsed = time.monotonic() - start
        self.assertEqual(len(result), 3)
        self.assertLess(elapsed, 3.0,
                        f'300-item 3-hop query took {elapsed:.3f}s '
                        f'(Bible §12.2 target: < 3s)')
