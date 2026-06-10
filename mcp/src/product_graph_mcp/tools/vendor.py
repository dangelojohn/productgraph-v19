"""Vendor / AVL tools — qualified-vendor catalog and per-item approved sources.

All read-only. Vendor qualification transitions are intentionally NOT exposed
as MCP tools — they are a human-in-the-loop governance action.
"""
from __future__ import annotations

from typing import Any

from ..odoo_client import OdooClient
from ..config import Config


VENDOR_FIELDS = [
    "id", "name", "code", "state", "is_approved",
    "qualified_on", "qualified_by_id", "qualification_expires_on",
    "vendor_part_count", "item_count", "partner_id", "notes", "active",
]

VENDOR_PART_FIELDS = [
    "id", "item_id", "vendor_id", "vendor_part_number",
    "rank", "is_preferred", "is_qualified", "vendor_state",
    "moq", "unit_price_hint", "currency_id", "lead_time_days",
    "notes", "active",
]


def _clamp(limit: int | None, config: Config) -> int:
    if limit is None:
        return config.default_limit
    return min(int(limit), config.max_limit)


def list_vendors(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    domain: list = []
    if args.get("state"):
        domain.append(("state", "=", args["state"]))
    if args.get("qualified_only"):
        domain.append(("state", "=", "qualified"))
    if args.get("active_only", True):
        domain.append(("active", "=", True))
    vendors = odoo.search_read(
        "pg.vendor", domain, VENDOR_FIELDS,
        limit=_clamp(args.get("limit"), config),
        offset=int(args.get("offset", 0)),
        order="name asc",
    )
    return {"count": len(vendors), "vendors": vendors}


def get_vendor(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    if not args.get("code") and not args.get("id"):
        return {"error": "must specify code or id"}
    if args.get("id"):
        rows = odoo.read("pg.vendor", [int(args["id"])], VENDOR_FIELDS)
    else:
        rows = odoo.search_read(
            "pg.vendor", [("code", "=", args["code"])], VENDOR_FIELDS, limit=1,
        )
    if not rows:
        return {"error": "not_found"}
    vendor = rows[0]
    # Surface the qualification log for traceability.
    log = odoo.search_read(
        "pg.vendor.qualification",
        [("vendor_id", "=", vendor["id"])],
        ["id", "decision", "decided_by_id", "decided_on", "comment"],
        order="decided_on desc",
        limit=20,
    )
    vendor["qualification_log"] = log
    return vendor


def list_vendor_parts_for_item(
    odoo: OdooClient, config: Config, args: dict[str, Any],
) -> dict:
    """Return the AVL for one item — the approved sources, sorted by rank."""
    if not args.get("item_id") and not args.get("part_number"):
        return {"error": "must specify item_id or part_number"}
    if args.get("item_id"):
        item_id = int(args["item_id"])
    else:
        items = odoo.search_read(
            "pg.item",
            [("part_number", "=", args["part_number"])],
            ["id", "part_number"],
            limit=1,
        )
        if not items:
            return {"error": "item_not_found"}
        item_id = items[0]["id"]
    parts = odoo.search_read(
        "pg.vendor.part",
        [("item_id", "=", item_id), ("active", "=", True)],
        VENDOR_PART_FIELDS,
        order="is_preferred desc, rank asc",
        limit=_clamp(args.get("limit"), config),
    )
    return {
        "item_id": item_id,
        "count": len(parts),
        "vendor_parts": parts,
    }


TOOLS = [
    {
        "name": "list_vendors",
        "description": (
            "List vendors in the AVL, optionally filtered by qualification state "
            "(unqualified / probationary / qualified / suspended / disqualified)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["unqualified", "probationary", "qualified",
                             "suspended", "disqualified"],
                },
                "qualified_only": {"type": "boolean"},
                "active_only": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        "handler": list_vendors,
    },
    {
        "name": "get_vendor",
        "description": (
            "Get a single vendor by code (preferred) or id, with the most "
            "recent 20 qualification-log entries inlined."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "id": {"type": "integer"},
            },
        },
        "handler": get_vendor,
    },
    {
        "name": "list_vendor_parts_for_item",
        "description": (
            "List the AVL (approved vendor parts) for one engineering item, "
            "sorted preferred-first then by rank ascending. Identify the item "
            "via item_id (preferred) or part_number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "part_number": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
        "handler": list_vendor_parts_for_item,
    },
]
