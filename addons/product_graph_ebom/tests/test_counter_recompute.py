# -*- coding: utf-8 -*-
"""
PG-102 regression tests — stored computed counter on pg.ebom.line_count.

The bug: `line_count` was a non-stored compute, so list/tree views fetched
the value but didn't always invalidate on related-record writes from another
session. After PG-102 we made it `store=True` with `@api.depends('ebom_line_ids')`
so the ORM persists + recomputes on every line create/write/unlink.

These tests assert correctness; the perf/staleness protection is structural
(if the stored value matches reality after each mutation, the view will too).
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestCounterRecompute(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disable SoD by default; tests that exercise SoD opt back in.
        cls.env['ir.config_parameter'].sudo().set_param('productgraph.enforce_sod', 'False')
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

    def _make_released_item(self, item_type='part', name=None):
        item = self.env['pg.item'].create({
            'name': name or 'Component',
            'item_type': item_type,
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'Initial release for counter-recompute test.',
        })
        rev.action_submit_for_review()
        rev.action_release()
        return item, rev

    def _make_draft_ebom(self, n_components=0):
        asm, asm_rev = self._make_released_item(item_type='assembly', name='Asm')
        comps = [
            self._make_released_item(item_type='part', name=f'Comp{i}')[0]
            for i in range(n_components)
        ]
        ebom = self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [
                (0, 0, {'pg_item_id': c.id, 'product_qty': 1.0})
                for c in comps
            ],
        })
        return ebom, comps

    # ─── Primary PG-102 cases ──────────────────────────────────────────────

    def test_ebom_line_count_after_add(self):
        """Adding lines updates the stored counter."""
        ebom, _ = self._make_draft_ebom(n_components=0)
        self.assertEqual(ebom.line_count, 0)
        c1, _ = self._make_released_item(item_type='part', name='Add1')
        c2, _ = self._make_released_item(item_type='part', name='Add2')
        self.env['pg.ebom.line'].create([
            {'ebom_id': ebom.id, 'pg_item_id': c1.id, 'product_qty': 1.0},
            {'ebom_id': ebom.id, 'pg_item_id': c2.id, 'product_qty': 1.0},
        ])
        # Read fresh to defeat any in-memory cache illusion.
        ebom.invalidate_recordset(['line_count'])
        self.assertEqual(ebom.line_count, 2)

    def test_ebom_line_count_after_remove(self):
        """Unlinking a line updates the stored counter."""
        ebom, _ = self._make_draft_ebom(n_components=3)
        self.assertEqual(ebom.line_count, 3)
        ebom.ebom_line_ids[0].unlink()
        ebom.invalidate_recordset(['line_count'])
        self.assertEqual(ebom.line_count, 2)

    def test_ebom_line_count_after_qty_write(self):
        """Writing a non-depending field on a line does NOT change the count
        (recompute is keyed on `ebom_line_ids`, not on `product_qty`)."""
        ebom, _ = self._make_draft_ebom(n_components=2)
        self.assertEqual(ebom.line_count, 2)
        ebom.ebom_line_ids[0].write({'product_qty': 99.0})
        ebom.invalidate_recordset(['line_count'])
        self.assertEqual(ebom.line_count, 2)

    def test_ebom_line_count_stored_persists_across_sessions(self):
        """The stored value survives flush + a fresh recordset fetch — proxy
        for the cross-session staleness symptom that was PG-102."""
        ebom, _ = self._make_draft_ebom(n_components=2)
        self.env.flush_all()
        # New recordset, fresh cache.
        fresh = self.env['pg.ebom'].browse(ebom.id)
        fresh.invalidate_recordset(['line_count'])
        self.assertEqual(fresh.line_count, 2)

    # ─── pg.revision.ebom_count (also converted to store=True) ────────────

    def test_revision_ebom_count_after_add(self):
        """pg.revision.ebom_count (extension in product_graph_ebom) recomputes
        when a new pg.ebom is created against it."""
        asm, asm_rev = self._make_released_item(
            item_type='assembly', name='AsmEbomCount',
        )
        self.assertEqual(asm_rev.ebom_count, 0)
        self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
        })
        asm_rev.invalidate_recordset(['ebom_count'])
        self.assertEqual(asm_rev.ebom_count, 1)
