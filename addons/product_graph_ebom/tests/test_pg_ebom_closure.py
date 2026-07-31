# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgEbomClosure(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disable SoD by default; tests that exercise SoD opt back in.
        cls.env['ir.config_parameter'].sudo().set_param('productgraph.enforce_sod', 'False')
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
        )
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

    def _release_item(self, name, item_type='part'):
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': item_type,
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'Initial release for closure test.',
        })
        rev.action_submit_for_review()
        rev.action_release()
        return item, rev

    def _release_ebom(self, root, root_rev, child_lines):
        ebom = self.env['pg.ebom'].create({
            'root_item_id': root.id,
            'revision_id': root_rev.id,
            'ebom_line_ids': [
                (0, 0, {'pg_item_id': child.id, 'product_qty': qty})
                for child, qty in child_lines
            ],
        })
        ebom.action_submit_for_review()
        ebom.action_release()
        return ebom

    def test_closure_built_on_release(self):
        asm, asm_rev = self._release_item('Asm', 'assembly')
        a, _ = self._release_item('A')
        b, _ = self._release_item('B')
        self._release_ebom(asm, asm_rev, [(a, 2.0), (b, 4.0)])
        Closure = self.env['pg.ebom.closure']
        rows = Closure.search([('ancestor_id', '=', asm.id)])
        self.assertEqual(len(rows), 2)
        qtys = {r.descendant_id: r.qty_chain for r in rows}
        self.assertEqual(qtys[a], 2.0)
        self.assertEqual(qtys[b], 4.0)

    def test_where_used_finds_parents(self):
        asm, asm_rev = self._release_item('Asm', 'assembly')
        leaf, _ = self._release_item('Leaf')
        self._release_ebom(asm, asm_rev, [(leaf, 1.0)])
        used = self.env['pg.ebom.closure'].where_used(leaf.id)
        self.assertTrue(used)
        self.assertEqual(used[0].ancestor_id, asm)

    def test_closure_multi_level_chains_qty(self):
        top, top_rev = self._release_item('Top', 'assembly')
        mid, mid_rev = self._release_item('Mid', 'assembly')
        leaf, _ = self._release_item('Leaf')
        # mid uses 5 leaf
        self._release_ebom(mid, mid_rev, [(leaf, 5.0)])
        # top uses 3 mid
        self._release_ebom(top, top_rev, [(mid, 3.0)])
        Closure = self.env['pg.ebom.closure']
        # top -> leaf via mid should chain to 3 * 5 = 15
        rows = Closure.search([
            ('ancestor_id', '=', top.id),
            ('descendant_id', '=', leaf.id),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.qty_chain, 15.0)
        self.assertEqual(rows.depth, 2)

    # ─── B7.6 — archive/unarchive of a released EBOM keeps closure in sync ──

    def test_archive_released_ebom_clears_closure(self):
        asm, asm_rev = self._release_item('Asm', 'assembly')
        a, _ = self._release_item('A')
        ebom = self._release_ebom(asm, asm_rev, [(a, 2.0)])
        Closure = self.env['pg.ebom.closure']
        self.assertTrue(Closure.search([('ebom_id', '=', ebom.id)]))

        ebom.write({'active': False})

        self.assertFalse(Closure.search([('ebom_id', '=', ebom.id)]))
        # The EBOM record itself is untouched otherwise — still released.
        self.assertEqual(ebom.state, 'released')

    def test_unarchive_released_ebom_rebuilds_closure(self):
        asm, asm_rev = self._release_item('Asm', 'assembly')
        a, _ = self._release_item('A')
        b, _ = self._release_item('B')
        ebom = self._release_ebom(asm, asm_rev, [(a, 2.0), (b, 4.0)])
        Closure = self.env['pg.ebom.closure']
        original_rows = Closure.search([('ebom_id', '=', ebom.id)])
        original = {(r.descendant_id.id, r.depth): r.qty_chain for r in original_rows}

        ebom.write({'active': False})
        self.assertFalse(Closure.search([('ebom_id', '=', ebom.id)]))

        ebom.write({'active': True})

        rebuilt_rows = Closure.search([('ebom_id', '=', ebom.id)])
        rebuilt = {(r.descendant_id.id, r.depth): r.qty_chain for r in rebuilt_rows}
        self.assertEqual(rebuilt, original)

    def test_archive_draft_ebom_no_closure_touch(self):
        asm = self.env['pg.item'].create({
            'name': 'AsmDraft',
            'item_type': 'assembly',
            'category_id': self.cat.id,
        })
        asm.action_to_engineering()
        asm_rev = self.env['pg.revision'].create({
            'item_id': asm.id,
            'name': 'A',
            'change_summary': 'Draft EBOM for archive no-op test.',
        })
        a, _ = self._release_item('ADraftChild')
        ebom = self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [(0, 0, {'pg_item_id': a.id, 'product_qty': 1.0})],
        })
        self.assertEqual(ebom.state, 'draft')
        Closure = self.env['pg.ebom.closure']

        # Should not raise and should not create any closure rows.
        ebom.write({'active': False})
        self.assertFalse(Closure.search([('ebom_id', '=', ebom.id)]))

        ebom.write({'active': True})
        self.assertFalse(Closure.search([('ebom_id', '=', ebom.id)]))
