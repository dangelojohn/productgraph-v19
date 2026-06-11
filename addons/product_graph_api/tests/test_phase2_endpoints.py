# -*- coding: utf-8 -*-
"""Phase-2 REST endpoint tests.

We test the serialiser helpers directly via a TransactionCase — calling
the route-decorated methods from a unit test fights Odoo's HTTP plumbing
(KeyError: 'type' in route_wrapper because the routing dict is only
populated when Odoo's dispatch builds the call frame).

The HTTP framing is exercised at deploy time via curl. The serialiser
coverage here is what we actually care about — if the dict shape changes,
clients break.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.product_graph_api.controllers.phase2_api import (
    _eco_dict, _vendor_dict, _vendor_part_dict, _rfq_dict,
    _relationship_dict, _cad_connector_dict,
)


@tagged('post_install', '-at_install', 'product_graph')
class TestPhase2Serialisers(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

    # ──────────────────────────────────────────────────────────────────
    # ECO serialiser
    # ──────────────────────────────────────────────────────────────────
    def test_eco_dict_shape(self):
        if 'pg.eco' not in self.env:
            self.skipTest('pg.eco not installed')
        eco = self.env['pg.eco'].create({
            'title': 'Serialiser Test',
            'change_type': 'corrective',
        })
        d = _eco_dict(eco)
        for key in ('id', 'name', 'title', 'state', 'change_type',
                    'priority', 'requestor_id', 'effective_date',
                    'line_count'):
            self.assertIn(key, d)
        self.assertEqual(d['state'], 'draft')
        self.assertEqual(d['title'], 'Serialiser Test')

    def test_eco_dict_detail_includes_lines_and_approvals(self):
        if 'pg.eco' not in self.env:
            self.skipTest('pg.eco not installed')
        eco = self.env['pg.eco'].create({
            'title': 'Detail Test',
            'change_type': 'corrective',
        })
        d = _eco_dict(eco, detail=True)
        self.assertIn('lines', d)
        self.assertIn('approvals', d)
        self.assertEqual(d['lines'], [])
        self.assertEqual(d['approvals'], [])

    # ──────────────────────────────────────────────────────────────────
    # Vendor serialiser
    # ──────────────────────────────────────────────────────────────────
    def test_vendor_dict_shape(self):
        vendor = self.env['pg.vendor'].create({
            'code': 'API-TEST-1',
            'name': 'API Test Vendor',
        })
        d = _vendor_dict(vendor)
        for key in ('id', 'code', 'name', 'state', 'is_approved',
                    'qualified_on', 'vendor_part_count', 'item_count',
                    'active'):
            self.assertIn(key, d)
        self.assertEqual(d['state'], 'unqualified')
        self.assertFalse(d['is_approved'])

    def test_vendor_dict_detail_includes_qualification_log(self):
        vendor = self.env['pg.vendor'].create({
            'code': 'API-TEST-2',
            'name': 'API Test Vendor 2',
        })
        vendor.action_to_probationary()
        d = _vendor_dict(vendor, detail=True)
        self.assertIn('qualification_log', d)
        self.assertGreaterEqual(len(d['qualification_log']), 1)
        self.assertEqual(d['qualification_log'][0]['decision'], 'probationary')

    # ──────────────────────────────────────────────────────────────────
    # Vendor part serialiser
    # ──────────────────────────────────────────────────────────────────
    def test_vendor_part_dict_shape(self):
        item = self.env['pg.item'].create({
            'name': 'API Serialiser Test Item',
            'item_type': 'purchased',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        vendor = self.env['pg.vendor'].create({
            'code': 'API-VP-1',
            'name': 'VP test',
        })
        part = self.env['pg.vendor.part'].create({
            'item_id': item.id,
            'vendor_id': vendor.id,
            'vendor_part_number': 'XYZ-1',
            'rank': 5,
            'is_preferred': True,
        })
        d = _vendor_part_dict(part)
        self.assertEqual(d['vendor_part_number'], 'XYZ-1')
        self.assertEqual(d['rank'], 5)
        self.assertTrue(d['is_preferred'])

    # ──────────────────────────────────────────────────────────────────
    # RFQ serialiser
    # ──────────────────────────────────────────────────────────────────
    def test_rfq_dict_shape(self):
        if 'pg.rfq' not in self.env:
            self.skipTest('pg.rfq not installed')
        rfq = self.env['pg.rfq'].create({'title': 'RFQ test'})
        d = _rfq_dict(rfq)
        for key in ('id', 'name', 'title', 'state', 'line_count',
                    'bid_count'):
            self.assertIn(key, d)
        self.assertEqual(d['state'], 'draft')

    def test_rfq_dict_detail_includes_lines(self):
        if 'pg.rfq' not in self.env:
            self.skipTest('pg.rfq not installed')
        rfq = self.env['pg.rfq'].create({'title': 'RFQ detail test'})
        item = self.env['pg.item'].create({
            'name': 'RFQ test item',
            'item_type': 'purchased',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': item.id,
            'qty': 50.0,
        })
        d = _rfq_dict(rfq, detail=True)
        self.assertEqual(len(d['lines']), 1)
        self.assertEqual(d['lines'][0]['qty'], 50.0)

    # ──────────────────────────────────────────────────────────────────
    # Relationship serialiser
    # ──────────────────────────────────────────────────────────────────
    def test_relationship_dict_shape(self):
        if 'pg.relationship' not in self.env:
            self.skipTest('pg.relationship not installed')
        item_a = self.env['pg.item'].create({
            'name': 'A', 'item_type': 'part', 'category_id': self.cat.id,
        })
        item_b = self.env['pg.item'].create({
            'name': 'B', 'item_type': 'part', 'category_id': self.cat.id,
        })
        item_a.action_to_engineering()
        item_b.action_to_engineering()
        rel = self.env['pg.relationship'].create({
            'source_item_id': item_a.id,
            'target_item_id': item_b.id,
            'kind': 'substitutes',
        })
        d = _relationship_dict(rel)
        self.assertEqual(d['kind'], 'substitutes')
        self.assertTrue(d['is_symmetric'])
        self.assertEqual(d['source_item_id'], item_a.id)
        self.assertEqual(d['target_item_id'], item_b.id)

    # ──────────────────────────────────────────────────────────────────
    # CAD connector serialiser
    # ──────────────────────────────────────────────────────────────────
    def test_cad_connector_dict_shape(self):
        if 'pg.cad.connector' not in self.env:
            self.skipTest('pg.cad.connector not installed')
        conn = self.env['pg.cad.connector'].create({
            'code': 'API-CAD-1',
            'name': 'API CAD Test',
            'driver_code': 'json_file',
            'state': 'inactive',
            'config_json': '{}',
        })
        d = _cad_connector_dict(conn)
        for key in ('id', 'code', 'name', 'driver_code', 'state',
                    'last_sync_at', 'last_sync_status', 'import_count'):
            self.assertIn(key, d)
        self.assertEqual(d['last_sync_status'], 'never')
