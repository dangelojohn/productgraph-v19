# -*- coding: utf-8 -*-
"""SOLIDWORKS PDM driver tests — all proxy HTTP calls mocked.

The driver shape is verified against the proxy contract documented in
``docs/solidworks_proxy_spec.md``. Real PDM access is not required to
run these tests.
"""
import json
from unittest.mock import patch, MagicMock

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.product_graph_cad.connectors.base import CONNECTOR_REGISTRY


@tagged('post_install', '-at_install', 'product_graph')
class TestSolidworksPdmDriver(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)

    def _make_connector(self, **config):
        defaults = {
            'proxy_url': 'http://win-pdm-proxy:9100',
            'vault': 'TestVault',
        }
        defaults.update(config)
        return self.env['pg.cad.connector'].create({
            'code': f'PDM-{id(self)}',
            'name': 'SW PDM test',
            'driver_code': 'solidworks_pdm',
            'state': 'active',
            'config_json': json.dumps(defaults),
        })

    # ──────────────────────────────────────────────────────────────────
    # Registry
    # ──────────────────────────────────────────────────────────────────
    def test_driver_registered(self):
        codes = [c for c, _cls in CONNECTOR_REGISTRY]
        self.assertIn('solidworks_pdm', codes)

    # ──────────────────────────────────────────────────────────────────
    # Config validation
    # ──────────────────────────────────────────────────────────────────
    def test_metadata_reports_missing_config_keys(self):
        conn = self.env['pg.cad.connector'].create({
            'code': 'PDM-BAD',
            'name': 'bad',
            'driver_code': 'solidworks_pdm',
            'state': 'active',
            'config_json': '{}',
        })
        driver = conn._driver()
        meta = driver.fetch_metadata()
        self.assertFalse(meta['reachable'])
        self.assertIn('missing required config keys', meta['detail'])

    # ──────────────────────────────────────────────────────────────────
    # Proxy contract
    # ──────────────────────────────────────────────────────────────────
    def test_metadata_calls_pdm_info(self):
        conn = self._make_connector(api_key='secret')
        driver = conn._driver()
        fake_resp = MagicMock(
            status_code=200,
            json=lambda: {
                'reachable': True,
                'vault': 'TestVault',
                'version': '2024.2.0',
                'detail': '',
            },
        )
        fake_resp.raise_for_status = MagicMock()
        with patch(
            'odoo.addons.product_graph_cad_solidworks.connectors'
            '.solidworks_pdm.requests.get',
            return_value=fake_resp,
        ) as mock_get:
            meta = driver.fetch_metadata()
            mock_get.assert_called_once()
            url = mock_get.call_args.args[0]
            self.assertTrue(url.endswith('/pdm/info'))
            headers = mock_get.call_args.kwargs['headers']
            self.assertEqual(headers.get('X-Proxy-Key'), 'secret')
        self.assertTrue(meta['reachable'])
        self.assertEqual(meta['vault'], 'TestVault')
        self.assertEqual(meta['pdm_version'], '2024.2.0')

    def test_metadata_handles_proxy_unreachable(self):
        conn = self._make_connector()
        driver = conn._driver()
        import requests
        with patch(
            'odoo.addons.product_graph_cad_solidworks.connectors'
            '.solidworks_pdm.requests.get',
            side_effect=requests.ConnectionError('refused'),
        ):
            meta = driver.fetch_metadata()
        self.assertFalse(meta['reachable'])
        self.assertIn('proxy unreachable', meta['detail'])

    def test_pull_document_maps_payload_to_cad_document(self):
        conn = self._make_connector()
        driver = conn._driver()
        fake_resp = MagicMock(
            status_code=200,
            json=lambda: {
                'part_number': 'ASM-001',
                'name': 'Top assembly',
                'description': '<p>From SW PDM</p>',
                'item_type': 'assembly',
                'card_variables': {
                    'width_mm': 600,
                    'material': 'plywood',
                },
                'bom': [
                    {'child_part_number': 'PRT-001', 'qty': 2.0, 'ref': 'P1'},
                    {'child_part_number': 'PRT-002', 'qty': 1.0},
                ],
            },
        )
        fake_resp.raise_for_status = MagicMock()
        with patch(
            'odoo.addons.product_graph_cad_solidworks.connectors'
            '.solidworks_pdm.requests.get',
            return_value=fake_resp,
        ) as mock_get:
            doc = driver.pull_document('\\\\TestVault\\Projects\\ASM-001')
            mock_get.assert_called_once()
        self.assertEqual(doc.part_number, 'ASM-001')
        self.assertEqual(doc.item_type, 'assembly')
        self.assertEqual(len(doc.ebom), 2)
        self.assertEqual(doc.ebom[0].child_part_number, 'PRT-001')
        self.assertEqual(doc.ebom[0].ref_designator, 'P1')
        self.assertEqual(doc.properties.get('material'), 'plywood')

    def test_list_documents_walks_listing_and_pulls_each(self):
        conn = self._make_connector(folder_efolder='12345')
        driver = conn._driver()

        def fake_get(url, params=None, headers=None, timeout=None):
            response = MagicMock(status_code=200)
            response.raise_for_status = MagicMock()
            if url.endswith('/pdm/documents'):
                response.json = lambda: [
                    {'path': '\\\\Vault\\X\\A.SLDPRT'},
                    {'path': '\\\\Vault\\X\\B.SLDPRT'},
                ]
            else:  # /pdm/document
                pn = params['path'].split('\\')[-1].replace('.SLDPRT', '')
                response.json = lambda: {
                    'part_number': pn,
                    'name': f'Doc {pn}',
                    'item_type': 'part',
                    'card_variables': {},
                    'bom': [],
                }
            return response

        with patch(
            'odoo.addons.product_graph_cad_solidworks.connectors'
            '.solidworks_pdm.requests.get',
            side_effect=fake_get,
        ):
            docs = driver.list_documents()
        self.assertEqual(len(docs), 2)
        self.assertEqual({d.part_number for d in docs}, {'A', 'B'})

    def test_list_documents_skips_failed_pulls(self):
        conn = self._make_connector()
        driver = conn._driver()

        def fake_get(url, params=None, headers=None, timeout=None):
            response = MagicMock(status_code=200)
            response.raise_for_status = MagicMock()
            if url.endswith('/pdm/documents'):
                response.json = lambda: [
                    {'path': '\\\\Vault\\good'},
                    {'path': '\\\\Vault\\bad'},
                ]
            elif 'bad' in params['path']:
                raise RuntimeError('mock failure')
            else:
                response.json = lambda: {
                    'part_number': 'good',
                    'name': 'good',
                    'item_type': 'part',
                    'card_variables': {},
                    'bom': [],
                }
            return response

        with patch(
            'odoo.addons.product_graph_cad_solidworks.connectors'
            '.solidworks_pdm.requests.get',
            side_effect=fake_get,
        ):
            docs = driver.list_documents()
        # 'bad' is skipped; 'good' survives.
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].part_number, 'good')
