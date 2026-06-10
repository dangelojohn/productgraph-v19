# -*- coding: utf-8 -*-
"""OnShape driver tests — all HTTPS calls mocked."""
import json
from unittest.mock import patch, MagicMock

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.product_graph_cad.connectors.base import CONNECTOR_REGISTRY


@tagged('post_install', '-at_install', 'product_graph')
class TestOnShapeDriver(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)

    def _make_connector(self, **overrides):
        cfg = {
            'base_url': 'https://cad.onshape.com',
            'api_key': 'BEARER',
            'document_id': 'D123',
            'workspace_id': 'W123',
            'element_id': 'E123',
        }
        cfg.update(overrides)
        return self.env['pg.cad.connector'].create({
            'code': f'OS-{id(self)}',
            'name': 'OnShape test',
            'driver_code': 'onshape',
            'state': 'active',
            'config_json': json.dumps(cfg),
        })

    # ──────────────────────────────────────────────────────────────────
    # Registry
    # ──────────────────────────────────────────────────────────────────
    def test_driver_registered(self):
        codes = [c for c, _cls in CONNECTOR_REGISTRY]
        self.assertIn('onshape', codes)

    # ──────────────────────────────────────────────────────────────────
    # Config + auth
    # ──────────────────────────────────────────────────────────────────
    def test_metadata_reports_missing_config_keys(self):
        conn = self.env['pg.cad.connector'].create({
            'code': 'OS-BAD',
            'name': 'bad',
            'driver_code': 'onshape',
            'state': 'active',
            'config_json': '{}',
        })
        driver = conn._driver()
        meta = driver.fetch_metadata()
        self.assertFalse(meta['reachable'])
        self.assertIn('missing required config keys', meta['detail'])

    def test_headers_carry_bearer(self):
        conn = self._make_connector()
        driver = conn._driver()
        h = driver._headers()
        self.assertEqual(h['Authorization'], 'Bearer BEARER')

    def test_metadata_pings_documents_endpoint(self):
        conn = self._make_connector()
        driver = conn._driver()
        fake_resp = MagicMock(
            status_code=200,
            json=lambda: {
                'name': 'My OnShape Doc',
                'owner': {'name': 'Engineering'},
                'createdAt': '2026-06-10T00:00:00Z',
            },
        )
        fake_resp.raise_for_status = MagicMock()
        with patch(
            'odoo.addons.product_graph_cad_onshape.connectors'
            '.onshape.requests.get',
            return_value=fake_resp,
        ) as mock_get:
            meta = driver.fetch_metadata()
            mock_get.assert_called_once()
            self.assertIn('/api/v6/documents/D123', mock_get.call_args.args[0])
        self.assertTrue(meta['reachable'])
        self.assertEqual(meta['document_name'], 'My OnShape Doc')

    # ──────────────────────────────────────────────────────────────────
    # Listing semantics
    # ──────────────────────────────────────────────────────────────────
    def test_list_parts_and_assembly(self):
        conn = self._make_connector()
        driver = conn._driver()

        import requests as r
        def fake_get(url, params=None, headers=None, timeout=None):
            resp = MagicMock(status_code=200)
            resp.raise_for_status = MagicMock()
            if '/api/v6/parts/' in url:
                resp.json = lambda: [
                    {'partNumber': 'P-001', 'name': 'Part A',
                     'description': 'a', 'properties': {'material': 'mdf'}},
                    {'partNumber': 'P-002', 'name': 'Part B'},
                ]
            elif '/api/v6/assemblies/' in url and '/bom' in url:
                resp.json = lambda: {
                    'partNumber': 'ASM-XYZ',
                    'name': 'My assembly',
                    'rows': [
                        {'childPartNumber': 'P-001', 'quantity': 2.0,
                         'refDesignator': 'R1'},
                        {'childPartNumber': 'P-002', 'quantity': 4.0},
                    ],
                }
            else:
                raise AssertionError(f"unexpected url: {url}")
            return resp

        with patch(
            'odoo.addons.product_graph_cad_onshape.connectors'
            '.onshape.requests.get',
            side_effect=fake_get,
        ):
            docs = driver.list_documents()
        # 2 parts + 1 assembly = 3 docs.
        self.assertEqual(len(docs), 3)
        asm = next(d for d in docs if d.item_type == 'assembly')
        self.assertEqual(asm.part_number, 'ASM-XYZ')
        self.assertEqual(len(asm.ebom), 2)
        self.assertEqual(asm.ebom[0].child_part_number, 'P-001')
        self.assertEqual(asm.ebom[0].qty, 2.0)
        self.assertEqual(asm.ebom[0].ref_designator, 'R1')

    def test_list_handles_part_studio_no_bom_404(self):
        """Part Studios return 404 on /assemblies/bom — the driver must
        treat that as 'no assembly here' and still surface the parts."""
        conn = self._make_connector()
        driver = conn._driver()

        import requests as r
        def fake_get(url, params=None, headers=None, timeout=None):
            if '/api/v6/parts/' in url:
                resp = MagicMock(status_code=200)
                resp.raise_for_status = MagicMock()
                resp.json = lambda: [{'partNumber': 'PRT-1', 'name': 'p'}]
                return resp
            # assemblies/bom -> 404
            err_resp = MagicMock(status_code=404)
            http_err = r.HTTPError(response=err_resp)
            err_resp.raise_for_status.side_effect = http_err
            return err_resp

        with patch(
            'odoo.addons.product_graph_cad_onshape.connectors'
            '.onshape.requests.get',
            side_effect=fake_get,
        ):
            docs = driver.list_documents()
        # 1 part, no assembly added.
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].part_number, 'PRT-1')
