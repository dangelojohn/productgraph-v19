# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgPropertyValue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        # Build property template + fields inline so the test does not
        # depend on demo data being loaded (demo data only loads when
        # Odoo creates the database itself, which is not the case in
        # CI / test-DB setups that pre-create the DB with createdb).
        cls.tmpl_mech = cls.env['pg.property.template'].create({
            'name': 'Cabinet Mechanical (test)',
            'code': 'TEST_CABINET_MECH',
            'item_type': 'assembly',
            'description': 'Test fixture for TestPgPropertyValue.',
        })
        cls.fld_width = cls.env['pg.property.field'].create({
            'template_id': cls.tmpl_mech.id,
            'key': 'width_mm',
            'label': 'Width (mm)',
            'field_type': 'integer',
            'is_required': True,
            'sequence': 10,
        })
        cls.fld_material = cls.env['pg.property.field'].create({
            'template_id': cls.tmpl_mech.id,
            'key': 'core_material',
            'label': 'Core Material',
            'field_type': 'selection',
            'selection_options': 'MDF,Plywood,Particleboard,Solid Wood',
            'sequence': 40,
        })

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
        val = self.env['pg.property.value'].create({
            'revision_id': rev.id,
            'field_id': self.fld_width.id,
            'value_integer': 600,
        })
        self.assertEqual(val.display_value, '600')

    def test_selection_value_must_be_in_options(self):
        item = self._make_item()
        rev = self._make_revision(item)
        with self.assertRaises(ValidationError):
            self.env['pg.property.value'].create({
                'revision_id': rev.id,
                'field_id': self.fld_material.id,
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
        with self.assertRaises(ValidationError):
            self.env['pg.property.value'].create({
                'revision_id': rev.id,
                'field_id': self.fld_width.id,
                'value_integer': 600,
            })
