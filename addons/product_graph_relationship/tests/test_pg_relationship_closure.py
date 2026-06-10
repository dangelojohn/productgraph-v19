# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgRelationshipClosure(TransactionCase):

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

        def mk_item(name):
            item = cls.env['pg.item'].create({
                'name': name,
                'item_type': 'part',
                'category_id': cls.cat.id,
            })
            item.action_to_engineering()
            return item

        cls.a = mk_item('Closure Test A')
        cls.b = mk_item('Closure Test B')
        cls.c = mk_item('Closure Test C')
        cls.d = mk_item('Closure Test D')

    def _mk_rel(self, source, target, kind):
        return self.env['pg.relationship'].create({
            'source_item_id': source.id,
            'target_item_id': target.id,
            'kind': kind,
        })

    # ──────────────────────────────────────────────────────────────────
    # Multi-hop substitute chains
    # ──────────────────────────────────────────────────────────────────
    def test_substitute_chain_two_hops(self):
        # A subs B, B subs C — closure must surface (A,C,2) and back.
        self._mk_rel(self.a, self.b, 'substitutes')
        self._mk_rel(self.b, self.c, 'substitutes')
        Closure = self.env['pg.relationship.closure']
        reachable_from_a = Closure.get_reachable(self.a, 'substitutes')
        # A reaches B (depth 1) AND C (depth 2)
        self.assertIn(self.b, reachable_from_a)
        self.assertIn(self.c, reachable_from_a)
        # C reaches both too (symmetric)
        reachable_from_c = Closure.get_reachable(self.c, 'substitutes')
        self.assertIn(self.a, reachable_from_c)
        self.assertIn(self.b, reachable_from_c)

    def test_substitute_depth_recorded(self):
        self._mk_rel(self.a, self.b, 'substitutes')
        self._mk_rel(self.b, self.c, 'substitutes')
        Closure = self.env['pg.relationship.closure']
        row_a_to_c = Closure.search([
            ('source_item_id', '=', self.a.id),
            ('target_item_id', '=', self.c.id),
            ('kind', '=', 'substitutes'),
        ], limit=1)
        self.assertEqual(row_a_to_c.depth, 2)
        row_a_to_b = Closure.search([
            ('source_item_id', '=', self.a.id),
            ('target_item_id', '=', self.b.id),
            ('kind', '=', 'substitutes'),
        ], limit=1)
        self.assertEqual(row_a_to_b.depth, 1)

    # ──────────────────────────────────────────────────────────────────
    # Supersession chain (directed)
    # ──────────────────────────────────────────────────────────────────
    def test_supersession_chain_forward(self):
        # B supersedes A, C supersedes B, D supersedes C.
        # Lineage: A → B → C → D (D is newest).
        self._mk_rel(self.b, self.a, 'supersedes')
        self._mk_rel(self.c, self.b, 'supersedes')
        self._mk_rel(self.d, self.c, 'supersedes')
        Closure = self.env['pg.relationship.closure']
        # From D the chain back is [C, B, A] (each older than the last)
        chain_back = Closure.supersession_chain(self.d, direction='forward')
        # Forward from D = items D supersedes = predecessors = C, B, A
        self.assertEqual(set(chain_back.ids),
                         {self.c.id, self.b.id, self.a.id})

    def test_supersession_chain_directed_not_symmetric(self):
        # B supersedes A. Reverse direction should not produce B as a
        # predecessor of A.
        self._mk_rel(self.b, self.a, 'supersedes')
        Closure = self.env['pg.relationship.closure']
        forward_from_a = Closure.get_reachable(self.a, 'supersedes')
        self.assertFalse(forward_from_a)

    # ──────────────────────────────────────────────────────────────────
    # Closure rebuild on write / unlink
    # ──────────────────────────────────────────────────────────────────
    def test_closure_rebuilds_on_unlink(self):
        rel = self._mk_rel(self.a, self.b, 'substitutes')
        self._mk_rel(self.b, self.c, 'substitutes')
        Closure = self.env['pg.relationship.closure']
        self.assertEqual(len(Closure.get_reachable(self.a, 'substitutes')), 2)
        rel.unlink()
        # After removing A-B, A is alone; B-C still exists so B still reaches C.
        self.assertFalse(Closure.get_reachable(self.a, 'substitutes'))
        self.assertIn(self.c, Closure.get_reachable(self.b, 'substitutes'))

    def test_closure_rebuilds_on_deactivate(self):
        rel = self._mk_rel(self.a, self.b, 'substitutes')
        Closure = self.env['pg.relationship.closure']
        self.assertEqual(len(Closure.get_reachable(self.a, 'substitutes')), 1)
        rel.write({'active': False})
        self.assertFalse(Closure.get_reachable(self.a, 'substitutes'))

    # ──────────────────────────────────────────────────────────────────
    # Max-depth cap
    # ──────────────────────────────────────────────────────────────────
    def test_get_reachable_with_max_depth(self):
        self._mk_rel(self.a, self.b, 'substitutes')
        self._mk_rel(self.b, self.c, 'substitutes')
        self._mk_rel(self.c, self.d, 'substitutes')
        Closure = self.env['pg.relationship.closure']
        # max_depth=1 limits to direct neighbours.
        depth1 = Closure.get_reachable(self.a, 'substitutes', max_depth=1)
        self.assertEqual(set(depth1.ids), {self.b.id})
        depth2 = Closure.get_reachable(self.a, 'substitutes', max_depth=2)
        self.assertEqual(set(depth2.ids), {self.b.id, self.c.id})
