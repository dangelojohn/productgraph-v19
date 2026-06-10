"""RFQ tools — read-only inspection of engineering RFQs and bids.

The award action is NOT exposed as MCP — it writes to pg.vendor.part and
is a human-in-the-loop sourcing decision.
"""
from __future__ import annotations

from typing import Any

from ..odoo_client import OdooClient
from ..config import Config


RFQ_FIELDS = [
    "id", "name", "title", "state", "requestor_id",
    "due_date", "sent_on", "awarded_on",
    "line_count", "bid_count", "promote_to_preferred",
]

RFQ_LINE_FIELDS = [
    "id", "rfq_id", "item_id", "qty", "target_date",
    "bid_count", "best_unit_price", "awarded_bid_id", "spec_notes",
]

RFQ_BID_FIELDS = [
    "id", "line_id", "rfq_id", "vendor_id", "vendor_part_number",
    "unit_price", "currency_id", "moq", "lead_time_days",
    "valid_until", "is_awarded", "comment",
]


def _clamp(limit: int | None, config: Config) -> int:
    if limit is None:
        return config.default_limit
    return min(int(limit), config.max_limit)


def list_rfqs(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    domain: list = []
    if args.get("state"):
        domain.append(("state", "=", args["state"]))
    if args.get("open_only"):
        domain.append(("state", "not in", ("awarded", "cancelled")))
    rfqs = odoo.search_read(
        "pg.rfq", domain, RFQ_FIELDS,
        limit=_clamp(args.get("limit"), config),
        offset=int(args.get("offset", 0)),
        order="create_date desc",
    )
    return {"count": len(rfqs), "rfqs": rfqs}


def get_rfq(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    if not args.get("name") and not args.get("id"):
        return {"error": "must specify name or id"}
    if args.get("id"):
        rows = odoo.read("pg.rfq", [int(args["id"])], RFQ_FIELDS)
    else:
        rows = odoo.search_read(
            "pg.rfq", [("name", "=", args["name"])], RFQ_FIELDS, limit=1,
        )
    if not rows:
        return {"error": "not_found"}
    rfq = rows[0]
    lines = odoo.search_read(
        "pg.rfq.line", [("rfq_id", "=", rfq["id"])],
        RFQ_LINE_FIELDS, order="sequence asc",
    )
    # Inline each line's bids.
    line_ids = [l["id"] for l in lines]
    bids_by_line: dict[int, list[dict[str, Any]]] = {l_id: [] for l_id in line_ids}
    if line_ids:
        all_bids = odoo.search_read(
            "pg.rfq.bid", [("line_id", "in", line_ids)],
            RFQ_BID_FIELDS, order="unit_price asc",
        )
        for bid in all_bids:
            line_id = bid["line_id"][0] if isinstance(bid["line_id"], list) else bid["line_id"]
            bids_by_line.setdefault(line_id, []).append(bid)
    for line in lines:
        line["bids"] = bids_by_line.get(line["id"], [])
    rfq["lines"] = lines
    return rfq


TOOLS = [
    {
        "name": "list_rfqs",
        "description": (
            "List engineering RFQs, optionally filtered by state. "
            "open_only=True excludes awarded and cancelled."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["draft", "sent", "received", "awarded", "cancelled"],
                },
                "open_only": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
            },
        },
        "handler": list_rfqs,
    },
    {
        "name": "get_rfq",
        "description": (
            "Get a single RFQ by name (e.g. RFQ/2026/00001) or id, with all "
            "lines and each line's bids (sorted ascending by unit_price) inlined."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "id": {"type": "integer"},
            },
        },
        "handler": get_rfq,
    },
]
