# -*- coding: utf-8 -*-
"""Tests for pg.revision — lifecycle, immutability, BOM line guards."""
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestPgRevision(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env['pg.category'].create({
            'name': 'Test Cat', 'code': 'TC2',
        })
        cls.item = cls.env['pg.item'].create({
            'name': 'Revision Test Item',
            'item_type': 'manufactured',
            'category_id': cls.category.id,
        })

    def _make_revision(self, code='A', state='draft'):
        rev = self.env['pg.revision'].create({
            'item_id': self.item.id,
            'revision_code': code,
            'sequence': 10,
        })
        if state != 'draft':
            self.env.cr.execute(
                "UPDATE pg_revision SET state = %s WHERE id = %s", (state, rev.id)
            )
            self.env.invalidate_all()
        return rev

    def test_initial_state_draft(self):
        rev = self._make_revision()
        self.assertEqual(rev.state, 'draft')

    def test_unique_revision_code_per_item(self):
        self._make_revision('B')
        with self.assertRaises(Exception):
            self._make_revision('B')  # duplicate

    def test_released_revision_immutable(self):
        rev = self._make_revision('C', state='released')
        with self.assertRaises(UserError):
            rev.write({'description': 'Changed after release'})

    def test_bom_line_locked_on_approved(self):
        rev = self._make_revision('D', state='approved')
        comp = self.env['pg.item'].create({
            'name': 'Component', 'item_type': 'purchased',
            'category_id': self.category.id,
        })
        uom = self.env.ref('uom.product_uom_unit')
        # Create BOM line while approved — write() on it should fail
        line = self.env['pg.bom.line'].create({
            'revision_id': rev.id,
            'component_item_id': comp.id,
            'product_qty': 1,
            'product_uom_id': uom.id,
        })
        # Now trying to write should raise
        with self.assertRaises(UserError):
            line.write({'product_qty': 2})

    def test_cannot_delete_released_revision(self):
        rev = self._make_revision('E', state='released')
        with self.assertRaises(UserError):
            rev.unlink()
