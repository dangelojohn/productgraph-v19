"""ECO tools — read-only inspection of Engineering Change Orders.

ECO state transitions are NOT exposed as MCP tools — they require
human-in-the-loop governance per State-Machine Bible §6.
"""
from __future__ import annotations

from typing import Any

from ..odoo_client import OdooClient
from ..config import Config


ECO_FIELDS = [
    "id", "name", "title", "state", "change_type", "priority",
    "requestor_id", "engineering_lead_id", "effective_date",
    "target_release_date", "actual_release_date",
    "line_count", "rejection_reason",
]

ECO_LINE_FIELDS = [
    "id", "eco_id", "item_id", "from_revision_id", "to_revision_id",
    "change_kind", "status", "notes",
]

ECO_APPROVAL_FIELDS = [
    "id", "decided_by_id", "decision", "signed_on", "comment",
]


def _clamp(limit: int | None, config: Config) -> int:
    if limit is None:
        return config.default_limit
    return min(int(limit), config.max_limit)


def list_ecos(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    domain: list = []
    if args.get("state"):
        domain.append(("state", "=", args["state"]))
    if args.get("priority"):
        domain.append(("priority", "=", args["priority"]))
    if args.get("change_type"):
        domain.append(("change_type", "=", args["change_type"]))
    if args.get("open_only"):
        domain.append(("state", "not in", ("released", "rejected")))
    ecos = odoo.search_read(
        "pg.eco", domain, ECO_FIELDS,
        limit=_clamp(args.get("limit"), config),
        offset=int(args.get("offset", 0)),
        order="create_date desc",
    )
    return {"count": len(ecos), "ecos": ecos}


def get_eco(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    if not args.get("name") and not args.get("id"):
        return {"error": "must specify name or id"}
    if args.get("id"):
        rows = odoo.read("pg.eco", [int(args["id"])], ECO_FIELDS)
    else:
        rows = odoo.search_read(
            "pg.eco", [("name", "=", args["name"])], ECO_FIELDS, limit=1,
        )
    if not rows:
        return {"error": "not_found"}
    eco = rows[0]
    eco["lines"] = odoo.search_read(
        "pg.eco.line", [("eco_id", "=", eco["id"])],
        ECO_LINE_FIELDS, order="sequence asc",
    )
    eco["approvals"] = odoo.search_read(
        "pg.eco.approval", [("eco_id", "=", eco["id"])],
        ECO_APPROVAL_FIELDS, order="signed_on desc",
    )
    return eco


TOOLS = [
    {
        "name": "list_ecos",
        "description": (
            "List Engineering Change Orders, optionally filtered by state, "
            "priority, change_type, or open-only (excludes released and rejected)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["draft", "analysis", "approval",
                             "implementation", "released", "rejected"],
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "critical"],
                },
                "change_type": {
                    "type": "string",
                    "enum": ["corrective", "adaptive", "perfective", "regulatory"],
                },
                "open_only": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        "handler": list_ecos,
    },
    {
        "name": "get_eco",
        "description": (
            "Get a single ECO by name (e.g. ECO/2026/00001) or id, with its "
            "affected-item lines and approval-log entries inlined."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "id": {"type": "integer"},
            },
        },
        "handler": get_eco,
    },
]
