# -*- coding: utf-8 -*-
"""
PG-102 regression — pg.item reverse-search counters.

pg.item.revision_count and friends are reverse-search counters
(`search_count` against pg.revision keyed by item_id). They CANNOT be
made `store=True` because the dependency isn't a related field of pg.item —
it's an unrelated model's FK back. After PG-102 we removed them from
the pg.item list view (smart buttons on the form only) to avoid the
N+1 SQL on list render. These tests assert that the counter values are
correct on demand (the perf/relocation fix is structural; this protects
correctness).
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestCounterRecompute(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

    def _make_item(self, name='Test'):
        return self.env['pg.item'].create({
            'name': name,
            'item_type': 'part',
            'category_id': self.cat.id,
        })

    def test_item_revision_count_correct_across_many_items(self):
        """Create 5 items each with 2 revisions; assert counts on a single
        recordset read are all 2. The perf protection (no N+1 on list render)
        is structural — revision_count was removed from pg.item list view in
        PG-102 — so this test asserts correctness rather than query count."""
        items = self.env['pg.item']
        for i in range(5):
            it = self._make_item(name=f'It{i}')
            for v in ('A', 'B'):
                self.env['pg.revision'].create({
                    'item_id': it.id,
                    'name': v,
                    'change_summary': 'seed',
                })
            items |= it
        # Single recordset read — the compute runs per-item internally but
        # values should all be 2.
        items.invalidate_recordset(['revision_count'])
        for it in items:
            self.assertEqual(it.revision_count, 2,
                             f"item {it.display_name} revision_count wrong")
