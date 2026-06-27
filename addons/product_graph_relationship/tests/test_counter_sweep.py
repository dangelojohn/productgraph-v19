# -*- coding: utf-8 -*-
"""PG-106 — pg.relationship helper m2m sweep.

The six computed helpers on pg.item (substitute_ids, alternate_ids,
supersedes_ids, superseded_by_ids, reference_ids, related_to_ids) are
reverse-search computes that call pg.relationship.get_related_items() /
get_incoming_items(). Per PG-102's lesson reverse-search fields CANNOT
be store=True — the dep graph can't track an unrelated model's FK back.

So the helpers stay non-stored. We removed them from any list view (they
only render on the form as many2many_tags) and these tests assert:

1. The value persists across a fresh recordset read (cache-invalidates
   correctly via @api.depends on outgoing_/incoming_relationship_ids).
2. Adding a relationship row makes the helper m2m grow without manual
   invalidate.
3. Unlinking a relationship row makes the helper m2m shrink.

If any helper is later promoted to a list view, swap the helper to a
stored field OR keep the smart-button-only pattern PG-102 established.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestCounterSweep(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        def mk(name):
            it = cls.env['pg.item'].create({
                'name': name,
                'item_type': 'part',
                'category_id': cls.cat.id,
            })
            it.action_to_engineering()
            return it

        cls.a = mk('Sweep A')
        cls.b = mk('Sweep B')
        cls.c = mk('Sweep C')

    def _mk_rel(self, source, target, kind):
        return self.env['pg.relationship'].create({
            'source_item_id': source.id,
            'target_item_id': target.id,
            'kind': kind,
        })

    # ──────────────────────────────────────────────────────────────────
    # 1. Persistence across a fresh recordset read
    # ──────────────────────────────────────────────────────────────────
    def test_substitute_count_persists_across_session(self):
        """A fresh browse() of the same item must still see the substitute
        without us manually calling invalidate_recordset()."""
        self._mk_rel(self.a, self.b, 'substitutes')
        fresh = self.env['pg.item'].browse(self.a.id)
        self.assertEqual(
            len(fresh.substitute_ids), 1,
            'substitute_ids did not survive a fresh recordset read',
        )
        self.assertEqual(fresh.substitute_ids, self.b)

    def test_supersedes_persists_across_session(self):
        # A directed helper has the same persistence requirement.
        self._mk_rel(self.a, self.b, 'supersedes')
        fresh_a = self.env['pg.item'].browse(self.a.id)
        fresh_b = self.env['pg.item'].browse(self.b.id)
        self.assertEqual(fresh_a.supersedes_ids, self.b)
        self.assertEqual(fresh_b.superseded_by_ids, self.a)

    # ──────────────────────────────────────────────────────────────────
    # 2. Add a relationship → helper m2m grows
    # ──────────────────────────────────────────────────────────────────
    def test_relationship_helpers_correct_after_add(self):
        """The @api.depends on outgoing_relationship_ids must auto-fire so
        the helper sees a row added in the same transaction."""
        # First call seeds cache to one substitute.
        self._mk_rel(self.a, self.b, 'substitutes')
        self.assertEqual(self.a.substitute_ids, self.b)

        # Second relationship — helper should grow without manual invalidate.
        self._mk_rel(self.a, self.c, 'substitutes')
        self.assertEqual(
            self.a.substitute_ids, self.b | self.c,
            'substitute_ids did not pick up the second row automatically',
        )

    def test_reference_helper_grows_on_directed_add(self):
        self._mk_rel(self.a, self.b, 'references')
        self.assertEqual(self.a.reference_ids, self.b)
        self._mk_rel(self.a, self.c, 'references')
        self.assertEqual(self.a.reference_ids, self.b | self.c)

    # ──────────────────────────────────────────────────────────────────
    # 3. Unlink a relationship → helper m2m shrinks
    # ──────────────────────────────────────────────────────────────────
    def test_relationship_helpers_correct_after_remove(self):
        rel_ab = self._mk_rel(self.a, self.b, 'substitutes')
        rel_ac = self._mk_rel(self.a, self.c, 'substitutes')
        self.assertEqual(self.a.substitute_ids, self.b | self.c)

        rel_ab.unlink()
        self.assertEqual(
            self.a.substitute_ids, self.c,
            'substitute_ids did not drop b after the row was unlinked',
        )

        rel_ac.unlink()
        self.assertFalse(
            self.a.substitute_ids,
            'substitute_ids did not empty after all rows unlinked',
        )

    def test_superseded_by_drops_when_incoming_rel_removed(self):
        # Incoming edges drive the inverse helper. The compute @api.depends
        # on incoming_relationship_ids so unlinking should propagate.
        rel = self._mk_rel(self.b, self.a, 'supersedes')
        self.assertEqual(self.a.superseded_by_ids, self.b)
        rel.unlink()
        self.assertFalse(self.a.superseded_by_ids)
