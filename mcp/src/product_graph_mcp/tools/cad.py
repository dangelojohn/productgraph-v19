"""CAD connector tools — read-only inspection of connectors + import history.

Pulling documents is a mutating operation (creates pg.items, drafts
revisions, etc.) and is intentionally NOT exposed as MCP. Run it from
the Odoo UI or via the REST API.
"""
from __future__ import annotations

from typing import Any

from ..odoo_client import OdooClient
from ..config import Config


CONNECTOR_FIELDS = [
    "id", "code", "name", "driver_code", "state",
    "last_sync_at", "last_sync_status", "last_sync_detail",
    "import_count", "create_revision", "create_ebom",
]

IMPORT_FIELDS = [
    "id", "connector_id", "state", "started_at", "completed_at",
    "line_count", "created_count", "updated_count", "skipped_count",
    "failure_reason",
]

IMPORT_LINE_FIELDS = [
    "id", "import_id", "part_number", "action", "item_id", "message",
]


def _clamp(limit: int | None, config: Config) -> int:
    if limit is None:
        return config.default_limit
    return min(int(limit), config.max_limit)


def list_cad_connectors(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    domain: list = []
    if args.get("driver_code"):
        domain.append(("driver_code", "=", args["driver_code"]))
    if args.get("state"):
        domain.append(("state", "=", args["state"]))
    rows = odoo.search_read(
        "pg.cad.connector", domain, CONNECTOR_FIELDS,
        limit=_clamp(args.get("limit"), config),
        order="code asc",
    )
    return {"count": len(rows), "connectors": rows}


def get_cad_connector(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    if not args.get("code") and not args.get("id"):
        return {"error": "must specify code or id"}
    if args.get("id"):
        rows = odoo.read("pg.cad.connector", [int(args["id"])], CONNECTOR_FIELDS)
    else:
        rows = odoo.search_read(
            "pg.cad.connector", [("code", "=", args["code"])],
            CONNECTOR_FIELDS, limit=1,
        )
    if not rows:
        return {"error": "not_found"}
    conn = rows[0]
    # Last 20 imports inlined for traceability.
    conn["recent_imports"] = odoo.search_read(
        "pg.cad.import", [("connector_id", "=", conn["id"])],
        IMPORT_FIELDS, order="create_date desc", limit=20,
    )
    return conn


def list_cad_imports(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    domain: list = []
    if args.get("connector_id"):
        domain.append(("connector_id", "=", int(args["connector_id"])))
    if args.get("state"):
        domain.append(("state", "=", args["state"]))
    rows = odoo.search_read(
        "pg.cad.import", domain, IMPORT_FIELDS,
        limit=_clamp(args.get("limit"), config),
        order="create_date desc",
    )
    return {"count": len(rows), "imports": rows}


def get_cad_import(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    if not args.get("id"):
        return {"error": "must specify id"}
    rows = odoo.read("pg.cad.import", [int(args["id"])], IMPORT_FIELDS)
    if not rows:
        return {"error": "not_found"}
    run = rows[0]
    run["lines"] = odoo.search_read(
        "pg.cad.import.line", [("import_id", "=", run["id"])],
        IMPORT_LINE_FIELDS, order="id asc",
        limit=_clamp(args.get("line_limit"), config),
    )
    return run


TOOLS = [
    {
        "name": "list_cad_connectors",
        "description": (
            "List configured CAD connectors. Optionally filter by driver_code "
            "(json_file / solidworks_pdm / onshape) or state (active / inactive)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "driver_code": {"type": "string"},
                "state": {"type": "string", "enum": ["active", "inactive"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
        "handler": list_cad_connectors,
    },
    {
        "name": "get_cad_connector",
        "description": (
            "Get a single CAD connector by code or id with its last 20 "
            "import runs inlined."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "id": {"type": "integer"},
            },
        },
        "handler": get_cad_connector,
    },
    {
        "name": "list_cad_imports",
        "description": (
            "List CAD import runs across all connectors, newest first. "
            "Filter by connector_id or state (pending / running / completed / failed)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "connector_id": {"type": "integer"},
                "state": {
                    "type": "string",
                    "enum": ["pending", "running", "completed", "failed"],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
        "handler": list_cad_imports,
    },
    {
        "name": "get_cad_import",
        "description": (
            "Get the trace rows for one CAD import run — per-document "
            "action (created / updated / skipped / error) and message."
        ),
        "input_schema": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "integer"},
                "line_limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
        "handler": get_cad_import,
    },
]
