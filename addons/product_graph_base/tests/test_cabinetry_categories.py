# -*- coding: utf-8 -*-
"""PG-145 smoke tests for the cabinetry-relevant Engineering Categories seed."""
from odoo.tests.common import TransactionCase


class TestCabinetryCategories(TransactionCase):

    def test_cabinetry_root_seeded(self):
        cat = self.env.ref(
            'product_graph_base.category_cabinetry_root',
            raise_if_not_found=False,
        )
        self.assertTrue(cat, "Cabinetry root category should be seeded")
        self.assertEqual(cat.code, 'CAB')
        self.assertFalse(cat.parent_id, "Cabinetry root must be top-level")

    def test_subcategories_count(self):
        root = self.env.ref(
            'product_graph_base.category_cabinetry_root',
            raise_if_not_found=False,
        )
        self.assertTrue(root, "Cabinetry root must exist before counting children")
        children = self.env['pg.category'].search([('parent_id', '=', root.id)])
        self.assertGreaterEqual(
            len(children), 10,
            "Should seed at least 10 cabinetry subcategories",
        )

    def test_codes_unique_among_cabinetry_seed(self):
        cab_codes = [
            'CAB', 'CAB-CASE', 'CAB-DD', 'CAB-HW-HS', 'CAB-HW-PK',
            'CAB-EDGE', 'CAB-LAM', 'CAB-PNL', 'CAB-FST', 'CAB-FIN',
            'CAB-GL', 'CAB-CT',
        ]
        for code in cab_codes:
            cats = self.env['pg.category'].search([('code', '=', code)])
            self.assertEqual(
                len(cats), 1,
                "Expected exactly one pg.category with code %s" % code,
            )
