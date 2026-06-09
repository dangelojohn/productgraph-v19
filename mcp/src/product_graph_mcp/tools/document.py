"""Document attachment — upload a binary blob to a revision. Mutating."""
from __future__ import annotations

import base64
from typing import Any

from ..odoo_client import OdooClient
from ..config import Config


def attach_document(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    rev_id = args.get("revision_id")
    name = args.get("name")
    content_b64 = args.get("content_base64")
    if not (rev_id and name and content_b64):
        return {"error": "revision_id, name and content_base64 are required"}
    # Validate base64
    try:
        base64.b64decode(content_b64, validate=True)
    except Exception:
        return {"error": "content_base64 is not valid base64"}
    doc_id = odoo.create("pg.document", {
        "revision_id": int(rev_id),
        "name": name,
        "document_type": args.get("document_type", "drawing"),
        "version_label": args.get("version_label"),
        "file_data": content_b64,
        "file_name": args.get("file_name", name),
        "description": args.get("description"),
    })
    return {"document_id": doc_id, "ok": True}


TOOLS = [
    {
        "name": "attach_document",
        "description": (
            "Attach a binary document (drawing, spec, manual, etc.) to a "
            "revision. The document is locked when the revision is released."
        ),
        "input_schema": {
            "type": "object",
            "required": ["revision_id", "name", "content_base64"],
            "properties": {
                "revision_id": {"type": "integer"},
                "name": {"type": "string"},
                "document_type": {
                    "type": "string",
                    "enum": ["drawing", "spec", "manual", "3d_model",
                             "source", "test_report", "certificate", "other"],
                },
                "version_label": {"type": "string"},
                "content_base64": {"type": "string"},
                "file_name": {"type": "string"},
                "description": {"type": "string"},
            },
        },
        "handler": attach_document,
        "mutating": True,
    },
]
