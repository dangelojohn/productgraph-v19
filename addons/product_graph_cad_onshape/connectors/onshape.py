# -*- coding: utf-8 -*-
"""OnShape driver — pure REST.

Notes
-----

* The connector record points at one ``(document_id, workspace_id,
  element_id)`` triple. Multiple elements / studios require multiple
  connector records.
* The driver expects ``api_key`` to be the prepared bearer token. For
  long-lived OnShape API key pairs you can construct the token offline
  per OnShape's HMAC docs and store it in ``config_json.api_key``.
* The driver does not crawl referenced documents — only the element
  the connector points at. Referenced sub-assemblies surface as BOM
  rows whose ``child_part_number`` is the OnShape part id; the
  ProductGraph pull treats unknown part numbers as orphan stubs and
  links them in the BOM placeholder.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from odoo.addons.product_graph_cad.connectors.base import (
    BaseCadConnector, CadBomLine, CadDocument, CONNECTOR_REGISTRY,
)

_logger = logging.getLogger(__name__)


class OnShapeConnector(BaseCadConnector):
    code = 'onshape'
    label = 'OnShape (REST)'

    _REQUIRED = ('base_url', 'api_key', 'document_id',
                 'workspace_id', 'element_id')

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
        return {
            'Accept': 'application/json',
            'Authorization': f"Bearer {cfg['api_key']}",
        }

    def _url(self, path: str) -> str:
        return f"{self._cfg()['base_url'].rstrip('/')}{path}"

    def _get(self, path: str, params: dict | None = None,
             timeout: int = 30) -> Any:
        resp = requests.get(
            self._url(path), params=params,
            headers=self._headers(), timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────────────────────────────
    # BaseCadConnector
    # ──────────────────────────────────────────────────────────────────
    def fetch_metadata(self) -> dict[str, Any]:
        ok, why = self._validate_cfg()
        if not ok:
            return {'code': self.code, 'reachable': False, 'detail': why}
        cfg = self._cfg()
        try:
            # OnShape doesn't have a single "ping" endpoint; loading the
            # document metadata is the cheapest reachability test.
            info = self._get(
                f"/api/v6/documents/{cfg['document_id']}", timeout=10)
            return {
                'code': self.code,
                'reachable': True,
                'document_name': info.get('name'),
                'owner': (info.get('owner') or {}).get('name'),
                'created_at': info.get('createdAt'),
            }
        except requests.RequestException as exc:
            return {
                'code': self.code,
                'reachable': False,
                'detail': f'OnShape unreachable: {exc}',
            }

    def list_documents(self) -> list[CadDocument]:
        """The OnShape "documents" here are parts within the configured
        element. Each part becomes one CadDocument; if the element is an
        assembly, the assembly itself becomes a CadDocument with the
        BOM populated from the assembly definition."""
        cfg = self._cfg()
        triple = (
            f"d/{cfg['document_id']}"
            f"/w/{cfg['workspace_id']}"
            f"/e/{cfg['element_id']}"
        )
        # Pull parts
        parts = self._get(f"/api/v6/parts/{triple}")
        docs: list[CadDocument] = []
        for p in parts:
            docs.append(CadDocument(
                part_number=str(p.get('partNumber') or p.get('partId')),
                name=str(p.get('name', p.get('partId'))),
                description=str(p.get('description', '')),
                item_type='part',
                properties=self._collect_properties(p),
                ebom=[],
            ))

        # Pull assembly BOM if element is an assembly. /assemblies/.../bom
        # returns 404 for Part Studios — swallow and continue.
        try:
            asm = self._get(f"/api/v6/assemblies/{triple}/bom")
            asm_doc = CadDocument(
                part_number=str(asm.get('partNumber') or cfg['element_id']),
                name=str(asm.get('name', 'Assembly')),
                description=str(asm.get('description', '')),
                item_type='assembly',
                properties=self._collect_properties(asm),
                ebom=self._extract_bom(asm),
            )
            docs.append(asm_doc)
        except requests.HTTPError as exc:
            if getattr(exc.response, 'status_code', None) != 404:
                _logger.warning("OnShape BOM fetch failed: %s", exc)

        return docs

    def pull_document(self, ref: str) -> CadDocument | None:
        # OnShape integration is "one element per connector" — pull_document
        # just round-trips the listing and filters.
        for doc in self.list_documents():
            if doc.part_number == ref:
                return doc
        return None

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _collect_properties(row: dict[str, Any]) -> dict[str, Any]:
        """Flatten OnShape's property block into a single dict."""
        props = dict(row.get('properties', {}) or {})
        # OnShape sometimes nests under "additionalProperties" too.
        props.update(row.get('additionalProperties', {}) or {})
        return props

    @staticmethod
    def _extract_bom(asm: dict[str, Any]) -> list[CadBomLine]:
        """The OnShape assembly BOM is a table of rows keyed by header
        ids. We expect the caller to pass a representation where each
        row has at least ``childPartNumber`` and ``quantity``.
        Implementations may need to look these up via the header
        descriptors — the proxy / wrapper does that resolution."""
        out: list[CadBomLine] = []
        for row in asm.get('rows', []) or []:
            child = (row.get('childPartNumber')
                     or (row.get('itemSource') or {}).get('partNumber'))
            if not child:
                continue
            qty = float(row.get('quantity', 1.0) or 1.0)
            out.append(CadBomLine(
                child_part_number=str(child),
                qty=qty,
                ref_designator=str(row.get('refDesignator', '')),
            ))
        return out


CONNECTOR_REGISTRY.append((OnShapeConnector.code, OnShapeConnector))
