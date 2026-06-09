# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgPropertyValue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        cls.tmpl_mech = cls.env.ref('product_graph_base.prop_tmpl_cabinet_mech')

    def _make_item(self):
        return self.env['pg.item'].create({
            'name': 'Cab',
            'item_type': 'assembly',
            'category_id': self.cat.id,
            'property_template_ids': [(4, self.tmpl_mech.id)],
        })

    def _make_revision(self, item):
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'Initial release of cabinet revision.',
        })

    def test_integer_value_stored_and_displayed(self):
        item = self._make_item()
        rev = self._make_revision(item)
        width = self.env.ref('product_graph_base.prop_fld_cabinet_width')
        val = self.env['pg.property.value'].create({
            'revision_id': rev.id,
            'field_id': width.id,
            'value_integer': 600,
        })
        self.assertEqual(val.display_value, '600')

    def test_selection_value_must_be_in_options(self):
        item = self._make_item()
        rev = self._make_revision(item)
        material = self.env.ref('product_graph_base.prop_fld_cabinet_material')
        with self.assertRaises(ValidationError):
            self.env['pg.property.value'].create({
                'revision_id': rev.id,
                'field_id': material.id,
                'value_selection': 'Concrete',
            })

    def test_template_must_be_attached_to_item(self):
        item = self.env['pg.item'].create({
            'name': 'No Templates',
            'item_type': 'assembly',
            'category_id': self.cat.id,
            # No property_template_ids attached
        })
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'Test without templates attached.',
        })
        width = self.env.ref('product_graph_base.prop_fld_cabinet_width')
        with self.assertRaises(ValidationError):
            self.env['pg.property.value'].create({
                'revision_id': rev.id,
                'field_id': width.id,
                'value_integer': 600,
            })
