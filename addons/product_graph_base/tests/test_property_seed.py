# -*- coding: utf-8 -*-
"""Smoke tests for the seeded engineering property templates.

Asserts the five templates load with all their expected fields. If
someone removes a template or renames a field, this test catches it
before the missing entry breaks downstream code that references the
xmlid.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPropertyTemplateSeed(TransactionCase):

    def _get_template(self, xmlid):
        return self.env.ref(f'product_graph_base.{xmlid}')

    def _field_keys(self, template):
        return set(template.field_ids.mapped('key'))

    # ──────────────────────────────────────────────────────────────────
    # All five templates exist
    # ──────────────────────────────────────────────────────────────────
    def test_dimensional_template(self):
        t = self._get_template('prop_tmpl_generic_dimensional')
        self.assertEqual(t.item_type, 'assembly')
        self.assertEqual(t.code, 'DIM_GENERIC')
        self.assertEqual(self._field_keys(t),
                         {'width_mm', 'height_mm', 'depth_mm', 'weight_g'})

    def test_mechanical_template(self):
        t = self._get_template('prop_tmpl_generic_mechanical')
        self.assertEqual(t.item_type, 'part')
        self.assertIn('material', self._field_keys(t))
        self.assertIn('hardness_hrc', self._field_keys(t))

    def test_electrical_template(self):
        t = self._get_template('prop_tmpl_generic_electrical')
        self.assertEqual(t.item_type, 'purchased')
        keys = self._field_keys(t)
        for k in ('voltage_v', 'current_a', 'power_w',
                  'frequency_hz', 'tolerance_pct'):
            self.assertIn(k, keys)

    def test_fastener_template(self):
        t = self._get_template('prop_tmpl_generic_fastener')
        self.assertEqual(t.item_type, 'purchased')
        keys = self._field_keys(t)
        for k in ('thread', 'length_mm', 'head_type',
                  'drive_type', 'material_grade'):
            self.assertIn(k, keys)

    def test_fastener_head_type_is_selection(self):
        t = self._get_template('prop_tmpl_generic_fastener')
        head = t.field_ids.filtered(lambda f: f.key == 'head_type')
        self.assertEqual(head.field_type, 'selection')
        self.assertIn('Hex', head.selection_options)
        self.assertIn('Pan', head.selection_options)

    def test_vendor_reference_template(self):
        t = self._get_template('prop_tmpl_generic_vendor_ref')
        self.assertEqual(t.item_type, 'purchased')
        keys = self._field_keys(t)
        for k in ('manufacturer_part_number', 'manufacturer',
                  'lead_time_days', 'datasheet_url', 'rohs_compliant'):
            self.assertIn(k, keys)

    def test_vendor_reference_rohs_is_boolean(self):
        t = self._get_template('prop_tmpl_generic_vendor_ref')
        rohs = t.field_ids.filtered(lambda f: f.key == 'rohs_compliant')
        self.assertEqual(rohs.field_type, 'boolean')

    # ──────────────────────────────────────────────────────────────────
    # Templates cover all the common item_types
    # ──────────────────────────────────────────────────────────────────
    def test_seed_covers_assembly_part_and_purchased(self):
        Template = self.env['pg.property.template']
        seeded = Template.search([('code', 'like', '%_GENERIC')])
        covered_types = set(seeded.mapped('item_type'))
        # We expect at least: assembly (Dimensional), part (Mechanical),
        # purchased (Electrical / Fastener / Vendor Reference).
        self.assertIn('assembly', covered_types)
        self.assertIn('part', covered_types)
        self.assertIn('purchased', covered_types)
