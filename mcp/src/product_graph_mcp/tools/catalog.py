"""Catalog browsing — list items, get item, search by text."""
from __future__ import annotations

from typing import Any

from ..odoo_client import OdooClient
from ..config import Config


ITEM_FIELDS = [
    "id", "part_number", "name", "item_type", "state",
    "category_id", "responsible_id", "current_revision_name",
    "product_id", "description",
]


def _clamp_limit(limit: int | None, config: Config) -> int:
    if limit is None:
        return config.default_limit
    return min(int(limit), config.max_limit)


def list_items(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    domain: list = []
    if args.get("state"):
        domain.append(("state", "=", args["state"]))
    if args.get("item_type"):
        domain.append(("item_type", "=", args["item_type"]))
    if args.get("category_id"):
        domain.append(("category_id", "=", int(args["category_id"])))
    items = odoo.search_read(
        "pg.item", domain, ITEM_FIELDS,
        limit=_clamp_limit(args.get("limit"), config),
        offset=int(args.get("offset", 0)),
        order="part_number asc",
    )
    return {"count": len(items), "items": items}


def get_item(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    if not args.get("part_number") and not args.get("id"):
        return {"error": "must specify part_number or id"}
    if args.get("id"):
        items = odoo.read("pg.item", [int(args["id"])], ITEM_FIELDS)
    else:
        items = odoo.search_read(
            "pg.item", [("part_number", "=", args["part_number"])], ITEM_FIELDS, limit=1,
        )
    if not items:
        return {"error": "not_found"}
    item = items[0]
    # Augment with revision list.
    revs = odoo.search_read(
        "pg.revision",
        [("item_id", "=", item["id"])],
        ["id", "name", "state", "effective_date", "released_by_id"],
        order="sequence asc",
    )
    item["revisions"] = revs
    return item


def search_catalog(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    q = args.get("query", "").strip()
    if not q:
        return {"error": "query required"}
    domain = [
        "|",
        ("part_number", "ilike", q),
        ("name", "ilike", q),
    ]
    items = odoo.search_read(
        "pg.item", domain, ITEM_FIELDS,
        limit=_clamp_limit(args.get("limit"), config),
    )
    return {"count": len(items), "items": items}


TOOLS = [
    {
        "name": "list_items",
        "description": (
            "List engineering items in the ProductGraph catalog, optionally "
            "filtered by state, item_type, or category."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["concept", "prototype", "engineering",
                             "released", "service", "obsolete"],
                },
                "item_type": {
                    "type": "string",
                    "enum": ["assembly", "part", "purchased",
                             "document", "software", "service"],
                },
                "category_id": {"type": "integer"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        "handler": list_items,
    },
    {
        "name": "get_item",
        "description": (
            "Get a single engineering item by part_number (preferred) or id, "
            "including its revision list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "part_number": {"type": "string"},
                "id": {"type": "integer"},
            },
        },
        "handler": get_item,
    },
    {
        "name": "search_catalog",
        "description": (
            "Free-text search across part_number and name fields of the "
            "engineering item catalog."
        ),
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
        "handler": search_catalog,
    },
]
