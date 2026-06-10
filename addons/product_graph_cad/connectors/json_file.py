# -*- coding: utf-8 -*-
"""JSON-file reference driver.

Reads a single JSON file at the path supplied in ``config_json.path``.
The file's envelope shape is:

.. code-block:: json

    {
        "documents": [
            {
                "part_number": "ASM-001",
                "name": "Top assembly",
                "description": "...",
                "item_type": "assembly",
                "properties": {"width_mm": 600},
                "ebom": [
                    {"child_part_number": "PRT-001", "qty": 2.0,
                     "ref_designator": "R12"},
                    ...
                ]
            },
            ...
        ]
    }

Production teams can point this driver at any export from any CAD /
PDM system that produces the same envelope — exporting from
SOLIDWORKS PDM, OnShape, Fusion 360 to this shape is a Python script
in each case, not Odoo code.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .base import BaseCadConnector, CadBomLine, CadDocument, CONNECTOR_REGISTRY


class JsonFileConnector(BaseCadConnector):
    code = 'json_file'
    label = 'JSON file'

    # ──────────────────────────────────────────────────────────────────
    # Config
    # ──────────────────────────────────────────────────────────────────
    def _path(self) -> str:
        config = self.record._config_dict()
        path = config.get('path')
        if not path:
            raise ValueError(
                "JSON file connector %s has no 'path' in its config_json"
                % self.record.code
            )
        return path

    def _load(self) -> dict[str, Any]:
        path = self._path()
        if not os.path.exists(path):
            raise FileNotFoundError(f"JSON file not found: {path}")
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)

    # ──────────────────────────────────────────────────────────────────
    # BaseCadConnector
    # ──────────────────────────────────────────────────────────────────
    def fetch_metadata(self) -> dict[str, Any]:
        try:
            data = self._load()
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            return {
                'code': self.code,
                'reachable': False,
                'detail': str(exc),
            }
        return {
            'code': self.code,
            'reachable': True,
            'path': self._path(),
            'document_count': len(data.get('documents', [])),
        }

    def list_documents(self) -> list[CadDocument]:
        data = self._load()
        return [self._to_doc(row) for row in data.get('documents', [])]

    def pull_document(self, ref: str) -> CadDocument | None:
        for doc in self.list_documents():
            if doc.part_number == ref:
                return doc
        return None

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    @staticmethod
    def _to_doc(row: dict[str, Any]) -> CadDocument:
        return CadDocument(
            part_number=str(row['part_number']),
            name=str(row.get('name', row['part_number'])),
            description=str(row.get('description', '')),
            item_type=str(row.get('item_type', 'part')),
            properties=dict(row.get('properties', {})),
            ebom=[
                CadBomLine(
                    child_part_number=str(line['child_part_number']),
                    qty=float(line.get('qty', 1.0)),
                    ref_designator=str(line.get('ref_designator', '')),
                )
                for line in row.get('ebom', [])
            ],
        )


# Register at import time.
CONNECTOR_REGISTRY.append((JsonFileConnector.code, JsonFileConnector))
