"""Release tools — simulate (read-only dry-run) and inspect existing releases.

The full release flow is intentionally NOT exposed as a single-call MCP tool.
Executing a release writes mrp.bom and freezes MOs — that's a human-in-the-loop
action that should go through the wizard's pre-flight review or the REST
/releases endpoint with explicit reason. The MCP layer surfaces simulation
and status only.
"""
from __future__ import annotations

from typing import Any

from ..odoo_client import OdooClient
from ..config import Config


RELEASE_FIELDS = [
    "id", "name", "state", "ebom_id", "revision_id", "root_item_id",
    "mrp_bom_id", "product_id", "release_timestamp", "released_by_id",
    "frozen_mo_count", "effective_date", "release_reason", "failure_reason",
]


def simulate_release(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    """Pre-flight validation summary — same data the wizard's html block uses."""
    ebom_id = args.get("ebom_id")
    if not ebom_id:
        return {"error": "ebom_id required"}
    # Read ebom + lines + their items
    ebom_rows = odoo.read("pg.ebom", [int(ebom_id)], [
        "id", "name", "state", "root_item_id", "revision_id", "line_count",
    ])
    if not ebom_rows:
        return {"error": "ebom not found"}
    ebom = ebom_rows[0]
    if ebom["state"] != "released":
        return {
            "ok": False,
            "errors": [f'EBOM state is {ebom["state"]}, must be released'],
            "warnings": [],
        }
    lines = odoo.search_read(
        "pg.ebom.line",
        [("ebom_id", "=", int(ebom_id))],
        ["pg_item_id", "part_number", "item_state", "product_qty"],
        limit=config.max_limit,
    )
    errors: list[str] = []
    warnings: list[str] = []
    for line in lines:
        if line["item_state"] != "released":
            errors.append(
                f'{line["part_number"]} is in state {line["item_state"]} (must be released)'
            )
    return {
        "ok": not errors,
        "ebom_id": ebom_id,
        "ebom_name": ebom["name"],
        "line_count": ebom["line_count"],
        "errors": errors,
        "warnings": warnings,
    }


def get_release(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    rel_id = args.get("release_id")
    if not rel_id:
        return {"error": "release_id required"}
    rows = odoo.read("pg.release", [int(rel_id)], RELEASE_FIELDS)
    if not rows:
        return {"error": "release not found"}
    return rows[0]


TOOLS = [
    {
        "name": "simulate_release",
        "description": (
            "Dry-run a release of an EBOM. Returns errors and warnings without "
            "creating any mrp.bom or freezing any MOs. Read-only."
        ),
        "input_schema": {
            "type": "object",
            "required": ["ebom_id"],
            "properties": {"ebom_id": {"type": "integer"}},
        },
        "handler": simulate_release,
    },
    {
        "name": "get_release",
        "description": "Inspect an existing pg.release record.",
        "input_schema": {
            "type": "object",
            "required": ["release_id"],
            "properties": {"release_id": {"type": "integer"}},
        },
        "handler": get_release,
    },
]
