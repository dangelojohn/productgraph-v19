"""BOM analysis — where-used, explosion, validation. All read-only."""
from __future__ import annotations

from typing import Any

from ..odoo_client import OdooClient
from ..config import Config


CLOSURE_FIELDS = [
    "id", "ebom_id", "ancestor_id", "descendant_id", "depth", "qty_chain",
]


def where_used(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    item_id = args.get("item_id")
    if not item_id and args.get("part_number"):
        items = odoo.search_read(
            "pg.item", [("part_number", "=", args["part_number"])], ["id"], limit=1,
        )
        if not items:
            return {"error": "item not found by part_number"}
        item_id = items[0]["id"]
    if not item_id:
        return {"error": "item_id or part_number required"}
    domain = [("descendant_id", "=", int(item_id))]
    if args.get("max_depth"):
        domain.append(("depth", "<=", int(args["max_depth"])))
    rows = odoo.search_read(
        "pg.ebom.closure", domain, CLOSURE_FIELDS,
        order="ancestor_id, depth",
        limit=min(int(args.get("limit", 500)), config.max_limit),
    )
    return {"item_id": item_id, "count": len(rows), "rows": rows}


def bom_explode(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    ebom_id = args.get("ebom_id")
    if not ebom_id:
        return {"error": "ebom_id required"}
    domain = [("ebom_id", "=", int(ebom_id))]
    if args.get("max_depth"):
        domain.append(("depth", "<=", int(args["max_depth"])))
    rows = odoo.search_read(
        "pg.ebom.closure", domain, CLOSURE_FIELDS,
        order="depth, descendant_id",
        limit=min(int(args.get("limit", 1000)), config.max_limit),
    )
    return {"ebom_id": ebom_id, "count": len(rows), "rows": rows}


def validate_ebom(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    ebom_id = args.get("ebom_id")
    if not ebom_id:
        return {"error": "ebom_id required"}
    result = odoo.call_method(
        "pg.ebom", [int(ebom_id)], "action_validate",
    )
    # action_validate is intended to return the dict directly when called via
    # MCP. Older Odoo paths may wrap it in a list.
    if isinstance(result, list) and result:
        return result[0]
    return result if isinstance(result, dict) else {"raw": result}


TOOLS = [
    {
        "name": "where_used",
        "description": (
            "Return every released EBOM in which an item appears, with depth "
            "and chained qty. Powered by the materialized pg.ebom.closure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "part_number": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
        "handler": where_used,
    },
    {
        "name": "bom_explode",
        "description": (
            "Return the full multi-level explosion of a released EBOM as flat "
            "closure rows (ancestor, descendant, depth, chained qty)."
        ),
        "input_schema": {
            "type": "object",
            "required": ["ebom_id"],
            "properties": {
                "ebom_id": {"type": "integer"},
                "max_depth": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
        },
        "handler": bom_explode,
    },
    {
        "name": "validate_ebom",
        "description": (
            "Run pre-release validation on an EBOM without changing state. "
            "Returns {ok, errors, warnings}."
        ),
        "input_schema": {
            "type": "object",
            "required": ["ebom_id"],
            "properties": {"ebom_id": {"type": "integer"}},
        },
        "handler": validate_ebom,
    },
]
