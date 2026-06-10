"""Relationship-graph tools — query the sideways graph (substitutes,
alternates, supersession, references) that lives outside the EBOM tree.

Read-only. Writing relationships is an engineering action that goes
through the form or REST.
"""
from __future__ import annotations

from typing import Any

from ..odoo_client import OdooClient
from ..config import Config

REL_FIELDS = [
    "id", "source_item_id", "target_item_id", "kind",
    "is_symmetric", "eco_id", "notes", "active",
]

ITEM_FIELDS = ["id", "part_number", "name", "state", "item_type"]


def _resolve_item_id(odoo: OdooClient, args: dict[str, Any]) -> int | None:
    if args.get("item_id"):
        return int(args["item_id"])
    if args.get("part_number"):
        rows = odoo.search_read(
            "pg.item", [("part_number", "=", args["part_number"])],
            ["id"], limit=1,
        )
        if rows:
            return rows[0]["id"]
    return None


def relationships_for_item(
    odoo: OdooClient, config: Config, args: dict[str, Any],
) -> dict:
    """Return every relationship where item appears on either end, grouped
    by direction and kind."""
    item_id = _resolve_item_id(odoo, args)
    if not item_id:
        return {"error": "must specify item_id or part_number"}
    outgoing = odoo.search_read(
        "pg.relationship",
        [("source_item_id", "=", item_id), ("active", "=", True)],
        REL_FIELDS,
    )
    incoming = odoo.search_read(
        "pg.relationship",
        [("target_item_id", "=", item_id), ("active", "=", True)],
        REL_FIELDS,
    )
    return {
        "item_id": item_id,
        "outgoing_count": len(outgoing),
        "incoming_count": len(incoming),
        "outgoing": outgoing,
        "incoming": incoming,
    }


def substitutes_for(
    odoo: OdooClient, config: Config, args: dict[str, Any],
) -> dict:
    """Return the substitute set (symmetric kind — walks both directions)."""
    item_id = _resolve_item_id(odoo, args)
    if not item_id:
        return {"error": "must specify item_id or part_number"}
    out = odoo.search_read(
        "pg.relationship",
        [
            ("source_item_id", "=", item_id),
            ("kind", "=", "substitutes"),
            ("active", "=", True),
        ],
        ["target_item_id"],
    )
    inc = odoo.search_read(
        "pg.relationship",
        [
            ("target_item_id", "=", item_id),
            ("kind", "=", "substitutes"),
            ("active", "=", True),
        ],
        ["source_item_id"],
    )
    pair_ids = set()
    for r in out:
        pair_ids.add(r["target_item_id"][0] if isinstance(r["target_item_id"], list) else r["target_item_id"])
    for r in inc:
        pair_ids.add(r["source_item_id"][0] if isinstance(r["source_item_id"], list) else r["source_item_id"])
    if not pair_ids:
        return {"item_id": item_id, "count": 0, "substitutes": []}
    items = odoo.search_read(
        "pg.item", [("id", "in", list(pair_ids))], ITEM_FIELDS,
    )
    return {"item_id": item_id, "count": len(items), "substitutes": items}


def supersession_chain(
    odoo: OdooClient, config: Config, args: dict[str, Any],
) -> dict:
    """Return the full supersession chain ending at `item`: predecessors
    backward and successors forward (up to max_depth hops in each direction).
    """
    item_id = _resolve_item_id(odoo, args)
    if not item_id:
        return {"error": "must specify item_id or part_number"}
    max_depth = int(args.get("max_depth", 10))

    def walk(start_id: int, direction: str) -> list[dict[str, Any]]:
        """direction='back' walks who supersedes this; 'forward' walks what
        this supersedes."""
        chain: list[dict[str, Any]] = []
        seen = {start_id}
        cur_id = start_id
        for _ in range(max_depth):
            if direction == "forward":
                edges = odoo.search_read(
                    "pg.relationship",
                    [
                        ("source_item_id", "=", cur_id),
                        ("kind", "=", "supersedes"),
                        ("active", "=", True),
                    ],
                    ["target_item_id"],
                    limit=1,
                )
                next_id = edges[0]["target_item_id"][0] if edges else None
            else:
                edges = odoo.search_read(
                    "pg.relationship",
                    [
                        ("target_item_id", "=", cur_id),
                        ("kind", "=", "supersedes"),
                        ("active", "=", True),
                    ],
                    ["source_item_id"],
                    limit=1,
                )
                next_id = edges[0]["source_item_id"][0] if edges else None
            if not next_id or next_id in seen:
                break
            seen.add(next_id)
            item_rows = odoo.read("pg.item", [next_id], ITEM_FIELDS)
            if item_rows:
                chain.append(item_rows[0])
            cur_id = next_id
        return chain

    predecessors = walk(item_id, "back")  # who got superseded earlier in chain
    successors = walk(item_id, "forward")  # what this supersedes (forward = newer)
    root = odoo.read("pg.item", [item_id], ITEM_FIELDS)
    return {
        "item": root[0] if root else None,
        "predecessors": predecessors,
        "successors": successors,
    }


TOOLS = [
    {
        "name": "relationships_for_item",
        "description": (
            "Return every active relationship edge where this item is on "
            "either end. Outgoing edges (source) and incoming edges (target) "
            "are returned separately, each with kind + target/source labels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "part_number": {"type": "string"},
            },
        },
        "handler": relationships_for_item,
    },
    {
        "name": "substitutes_for",
        "description": (
            "Return the substitute set for this item — drop-in replacements "
            "from the symmetric 'substitutes' relationship kind. Walks both "
            "directions of each edge."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "part_number": {"type": "string"},
            },
        },
        "handler": substitutes_for,
    },
    {
        "name": "supersession_chain",
        "description": (
            "Return the full supersession chain that this item is part of: "
            "predecessors (items it supersedes, in reverse age order) and "
            "successors (items that supersede it, in forward age order). "
            "max_depth caps the walk in each direction (default 10)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "part_number": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
        "handler": supersession_chain,
    },
]
