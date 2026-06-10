# -*- coding: utf-8 -*-
"""SOLIDWORKS Enterprise PDM driver.

Talks to a Windows-side proxy service that wraps the EPDM COM API
(``IEdmVault5``, ``IEdmFile5``, ``IEdmBomMgr``). The proxy is
deployment-specific and ships separately; see
``docs/solidworks_proxy_spec.md`` for the contract this driver expects.

Driver config (JSON in ``pg.cad.connector.config_json``)::

    {
        "proxy_url":       "http://win-pdm-proxy:9100",
        "vault":           "MyVault",
        "folder_efolder":  "12345",     // optional EFolder id
        "api_key":         "..."        // optional shared secret
    }
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from odoo.addons.product_graph_cad.connectors.base import (
    BaseCadConnector, CadBomLine, CadDocument, CONNECTOR_REGISTRY,
)

_logger = logging.getLogger(__name__)


class SolidworksPdmConnector(BaseCadConnector):
    code = 'solidworks_pdm'
    label = 'SOLIDWORKS PDM'

    # Required + optional config keys, for fast validation in
    # fetch_metadata.
    _REQUIRED = ('proxy_url', 'vault')

    # ──────────────────────────────────────────────────────────────────
    # Config + HTTP plumbing
    # ──────────────────────────────────────────────────────────────────
    def _cfg(self) -> dict[str, Any]:
        return self.record._config_dict()

    def _validate_cfg(self) -> tuple[bool, str]:
        cfg = self._cfg()
        missing = [k for k in self._REQUIRED if not cfg.get(k)]
        if missing:
            return False, f"missing required config keys: {', '.join(missing)}"
        return True, ''

    def _headers(self) -> dict[str, str]:
        cfg = self._cfg()
        h = {'Accept': 'application/json'}
        if cfg.get('api_key'):
            h['X-Proxy-Key'] = cfg['api_key']
        return h

    def _get(self, path: str, params: dict | None = None,
             timeout: int = 30) -> Any:
        cfg = self._cfg()
        url = f"{cfg['proxy_url'].rstrip('/')}{path}"
        resp = requests.get(
            url, params=params, headers=self._headers(), timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────────────────────────────
    # BaseCadConnector
    # ──────────────────────────────────────────────────────────────────
    def fetch_metadata(self) -> dict[str, Any]:
        ok, why = self._validate_cfg()
        if not ok:
            return {'code': self.code, 'reachable': False, 'detail': why}
        try:
            info = self._get('/pdm/info', timeout=10)
            return {
                'code': self.code,
                'reachable': bool(info.get('reachable')),
                'vault': info.get('vault'),
                'pdm_version': info.get('version'),
                'detail': info.get('detail', ''),
            }
        except requests.RequestException as exc:
            return {
                'code': self.code,
                'reachable': False,
                'detail': f'proxy unreachable: {exc}',
            }
        except ValueError as exc:
            return {
                'code': self.code,
                'reachable': False,
                'detail': f'proxy returned non-JSON: {exc}',
            }

    def list_documents(self) -> list[CadDocument]:
        """Walk the configured folder (or full vault) and pull every
        document. The proxy returns lightweight rows; we fetch the full
        document for each via pull_document so card variables + BOM are
        populated."""
        cfg = self._cfg()
        params = {}
        if cfg.get('folder_efolder'):
            params['folder'] = cfg['folder_efolder']
        listing = self._get('/pdm/documents', params=params, timeout=60)
        docs: list[CadDocument] = []
        for entry in listing:
            try:
                doc = self.pull_document(entry['path'])
                if doc:
                    docs.append(doc)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "SOLIDWORKS PDM: failed to load %s, skipping",
                    entry.get('path'))
        return docs

    def pull_document(self, ref: str) -> CadDocument | None:
        """``ref`` is the EPDM path (e.g. ``\\\\Vault\\Projects\\ASM-001.SLDASM``)."""
        try:
            payload = self._get('/pdm/document', params={'path': ref})
        except requests.RequestException as exc:
            _logger.warning(
                "SOLIDWORKS PDM: pull_document(%s) failed: %s", ref, exc)
            return None
        if not payload:
            return None
        return CadDocument(
            part_number=str(payload.get('part_number') or payload.get('name')),
            name=str(payload.get('name', payload.get('part_number', ref))),
            description=str(payload.get('description', '')),
            item_type=str(payload.get('item_type', 'part')),
            properties=dict(payload.get('card_variables', {})),
            ebom=[
                CadBomLine(
                    child_part_number=str(line['child_part_number']),
                    qty=float(line.get('qty', 1.0)),
                    ref_designator=str(line.get('ref', '')),
                )
                for line in payload.get('bom', [])
            ],
        )


CONNECTOR_REGISTRY.append((SolidworksPdmConnector.code, SolidworksPdmConnector))
