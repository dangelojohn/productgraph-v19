# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgEbom(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
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
            'change_summary': 'Initial release for EBOM test.',
        })
        rev.action_submit_for_review()
        rev.action_release()
        return item, rev

    def test_ebom_create_and_lines(self):
        asm, asm_rev = self._make_released_item(item_type='assembly', name='Asm')
        comp, _ = self._make_released_item(item_type='part', name='Comp')
        ebom = self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [(0, 0, {
                'pg_item_id': comp.id,
                'product_qty': 2.0,
            })],
        })
        self.assertEqual(ebom.line_count, 1)
        self.assertTrue(ebom.name.startswith('EBOM-'))

    def test_release_blocked_when_child_not_released(self):
        asm, asm_rev = self._make_released_item(item_type='assembly', name='Asm')
        # Create a non-released component
        comp = self.env['pg.item'].create({
            'name': 'Draft Comp',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        ebom = self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [(0, 0, {
                'pg_item_id': comp.id,
                'product_qty': 1.0,
            })],
        })
        ebom.action_submit_for_review()
        with self.assertRaises(UserError):
            ebom.action_release()

    def test_release_succeeds_with_released_children(self):
        asm, asm_rev = self._make_released_item(item_type='assembly', name='Asm')
        comp, _ = self._make_released_item(item_type='part', name='Comp')
        ebom = self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [(0, 0, {
                'pg_item_id': comp.id,
                'product_qty': 3.0,
            })],
        })
        ebom.action_submit_for_review()
        ebom.action_release()
        self.assertEqual(ebom.state, 'released')

    def test_released_ebom_immutable(self):
        asm, asm_rev = self._make_released_item(item_type='assembly', name='Asm2')
        comp, _ = self._make_released_item(item_type='part', name='Comp2')
        ebom = self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [(0, 0, {'pg_item_id': comp.id, 'product_qty': 1.0})],
        })
        ebom.action_submit_for_review()
        ebom.action_release()
        with self.assertRaises(UserError):
            ebom.write({'ebom_type': 'prototype'})
        with self.assertRaises(UserError):
            ebom.ebom_line_ids[0].write({'product_qty': 9.0})
