# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgProperty(TransactionCase):

    def test_template_create(self):
        tmpl = self.env['pg.property.template'].create({
            'name': 'Test Template',
            'code': 'TEST_TMPL',
            'item_type': 'part',
        })
        self.assertEqual(tmpl.field_count, 0)

    def test_field_key_format_validation(self):
        tmpl = self.env['pg.property.template'].create({
            'name': 'Test', 'code': 'T1', 'item_type': 'part',
        })
        with self.assertRaises(ValidationError):
            self.env['pg.property.field'].create({
                'template_id': tmpl.id,
                'key': '1bad',  # starts with digit
                'label': 'Bad',
                'field_type': 'char',
            })
        with self.assertRaises(ValidationError):
            self.env['pg.property.field'].create({
                'template_id': tmpl.id,
                'key': 'bad-key',  # hyphen not allowed
                'label': 'Bad',
                'field_type': 'char',
            })

    def test_selection_requires_options(self):
        tmpl = self.env['pg.property.template'].create({
            'name': 'Test', 'code': 'T2', 'item_type': 'part',
        })
        with self.assertRaises(ValidationError):
            self.env['pg.property.field'].create({
                'template_id': tmpl.id,
                'key': 'size',
                'label': 'Size',
                'field_type': 'selection',
                # no selection_options
            })

    def test_selection_choices_parsed(self):
        tmpl = self.env['pg.property.template'].create({
            'name': 'Test', 'code': 'T3', 'item_type': 'part',
        })
        f = self.env['pg.property.field'].create({
            'template_id': tmpl.id,
            'key': 'size',
            'label': 'Size',
            'field_type': 'selection',
            'selection_options': ' S , M , L ',
        })
        self.assertEqual(f.get_selection_choices(), [('S', 'S'), ('M', 'M'), ('L', 'L')])
