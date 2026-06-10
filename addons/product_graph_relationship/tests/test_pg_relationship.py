# -*- coding: utf-8 -*-
from psycopg2.errors import UniqueViolation

from odoo.exceptions import ValidationError
from odoo.tools import mute_logger
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgRelationship(TransactionCase):

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

        cls.a = mk_item('Rel Test Item A')
        cls.b = mk_item('Rel Test Item B')
        cls.c = mk_item('Rel Test Item C')

    def _mk_rel(self, source, target, kind, **kw):
        return self.env['pg.relationship'].create({
            'source_item_id': source.id,
            'target_item_id': target.id,
            'kind': kind,
            **kw,
        })

    # ──────────────────────────────────────────────────────────────────
    # Basic creation + invalidation
    # ──────────────────────────────────────────────────────────────────
    def test_symmetric_substitute_visible_from_both_sides(self):
        self._mk_rel(self.a, self.b, 'substitutes')
        self.a.invalidate_recordset()
        self.b.invalidate_recordset()
        self.assertEqual(self.a.substitute_ids, self.b)
        self.assertEqual(self.b.substitute_ids, self.a)

    def test_symmetric_alternate_visible_from_both_sides(self):
        self._mk_rel(self.a, self.b, 'alternates')
        self.a.invalidate_recordset()
        self.b.invalidate_recordset()
        self.assertEqual(self.a.alternate_ids, self.b)
        self.assertEqual(self.b.alternate_ids, self.a)

    def test_supersedes_is_directed(self):
        # B is the new revision; A is the old one. So B supersedes A.
        self._mk_rel(self.b, self.a, 'supersedes')
        self.a.invalidate_recordset()
        self.b.invalidate_recordset()
        self.assertEqual(self.b.supersedes_ids, self.a)
        self.assertFalse(self.a.supersedes_ids)
        self.assertEqual(self.a.superseded_by_ids, self.b)
        self.assertFalse(self.b.superseded_by_ids)

    def test_references_is_directed(self):
        self._mk_rel(self.a, self.b, 'references')
        self.a.invalidate_recordset()
        self.b.invalidate_recordset()
        self.assertEqual(self.a.reference_ids, self.b)
        self.assertFalse(self.b.reference_ids)

    def test_related_is_directed(self):
        self._mk_rel(self.a, self.b, 'related')
        self.a.invalidate_recordset()
        self.b.invalidate_recordset()
        self.assertEqual(self.a.related_to_ids, self.b)
        self.assertFalse(self.b.related_to_ids)

    # ──────────────────────────────────────────────────────────────────
    # Constraints
    # ──────────────────────────────────────────────────────────────────
    def test_self_loop_blocked(self):
        with self.assertRaises(ValidationError):
            self._mk_rel(self.a, self.a, 'substitutes')

    def test_duplicate_triple_blocked(self):
        self._mk_rel(self.a, self.b, 'substitutes')
        with self.assertRaises(Exception), mute_logger('odoo.sql_db'):
            with self.env.cr.savepoint():
                self._mk_rel(self.a, self.b, 'substitutes')

    def test_redundant_inverse_blocked_for_symmetric(self):
        self._mk_rel(self.a, self.b, 'substitutes')
        with self.assertRaises(ValidationError):
            self._mk_rel(self.b, self.a, 'substitutes')

    def test_inverse_allowed_for_directed_kinds(self):
        # supersedes is directed — both A→B and B→A are conceptually different
        # (the first says "A is the new one", the second says "B is the new one").
        # The unique constraint blocks the literal duplicate but the reversed
        # tuple is a different (source, target) and is allowed.
        self._mk_rel(self.a, self.b, 'supersedes')
        self._mk_rel(self.b, self.a, 'supersedes')
        # Both rows exist; ID set is 2.

    # ──────────────────────────────────────────────────────────────────
    # Multi-edge fan-out
    # ──────────────────────────────────────────────────────────────────
    def test_three_way_substitute(self):
        # A substitutes B; A substitutes C. From A's perspective, both.
        # From B's perspective, just A. From C's perspective, just A.
        # (Substitution is symmetric pairwise but not transitive.)
        self._mk_rel(self.a, self.b, 'substitutes')
        self._mk_rel(self.a, self.c, 'substitutes')
        self.a.invalidate_recordset()
        self.b.invalidate_recordset()
        self.c.invalidate_recordset()
        self.assertEqual(self.a.substitute_ids, self.b | self.c)
        self.assertEqual(self.b.substitute_ids, self.a)
        self.assertEqual(self.c.substitute_ids, self.a)

    def test_supersession_chain(self):
        # A → B → C  (C is newest)
        self._mk_rel(self.b, self.a, 'supersedes')
        self._mk_rel(self.c, self.b, 'supersedes')
        self.a.invalidate_recordset()
        self.b.invalidate_recordset()
        self.c.invalidate_recordset()
        self.assertEqual(self.a.superseded_by_ids, self.b)
        self.assertEqual(self.b.superseded_by_ids, self.c)
        self.assertEqual(self.b.supersedes_ids, self.a)
        self.assertEqual(self.c.supersedes_ids, self.b)
        self.assertFalse(self.c.superseded_by_ids)
        self.assertFalse(self.a.supersedes_ids)

    # ──────────────────────────────────────────────────────────────────
    # Helper methods (called by computed fields, also exposed as API)
    # ──────────────────────────────────────────────────────────────────
    def test_get_related_items_symmetric(self):
        self._mk_rel(self.a, self.b, 'substitutes')
        Rel = self.env['pg.relationship']
        self.assertEqual(Rel.get_related_items(self.a, 'substitutes'), self.b)
        self.assertEqual(Rel.get_related_items(self.b, 'substitutes'), self.a)

    def test_get_related_items_directed(self):
        self._mk_rel(self.a, self.b, 'references')
        Rel = self.env['pg.relationship']
        self.assertEqual(Rel.get_related_items(self.a, 'references'), self.b)
        self.assertFalse(Rel.get_related_items(self.b, 'references'))

    def test_get_incoming_items(self):
        self._mk_rel(self.a, self.b, 'supersedes')
        self._mk_rel(self.c, self.b, 'supersedes')
        Rel = self.env['pg.relationship']
        self.assertEqual(
            Rel.get_incoming_items(self.b, 'supersedes'),
            self.a | self.c,
        )
