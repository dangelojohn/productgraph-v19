"""Procurement bridge tools — read-only inspection of purchase.requisition
records produced by RFQ awards.
"""
from __future__ import annotations

from typing import Any

from ..odoo_client import OdooClient
from ..config import Config


REQUISITION_FIELDS = [
    "id", "name", "state", "reference", "vendor_id",
    "date_start", "date_end", "user_id", "company_id",
    "requisition_type",
]

REQUISITION_LINE_FIELDS = [
    "id", "requisition_id", "product_id", "product_qty",
    "product_uom_id", "price_unit",
]


def _clamp(limit: int | None, config: Config) -> int:
    if limit is None:
        return config.default_limit
    return min(int(limit), config.max_limit)


def get_requisition_for_rfq(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    """Look up the purchase.requisition the procurement bridge created
    when an RFQ was awarded."""
    if not args.get("rfq_id") and not args.get("rfq_name"):
        return {"error": "must specify rfq_id or rfq_name"}
    if args.get("rfq_id"):
        rfqs = odoo.read(
            "pg.rfq", [int(args["rfq_id"])],
            ["id", "name", "requisition_id"],
        )
    else:
        rfqs = odoo.search_read(
            "pg.rfq", [("name", "=", args["rfq_name"])],
            ["id", "name", "requisition_id"], limit=1,
        )
    if not rfqs:
        return {"error": "rfq_not_found"}
    rfq = rfqs[0]
    req_field = rfq.get("requisition_id")
    if not req_field:
        return {
            "rfq_id": rfq["id"],
            "rfq_name": rfq["name"],
            "requisition": None,
            "detail": "RFQ has no linked purchase.requisition",
        }
    req_id = req_field[0] if isinstance(req_field, list) else req_field
    req_rows = odoo.read(
        "purchase.requisition", [req_id], REQUISITION_FIELDS)
    if not req_rows:
        return {"error": "requisition_not_found"}
    req = req_rows[0]
    req["lines"] = odoo.search_read(
        "purchase.requisition.line",
        [("requisition_id", "=", req["id"])],
        REQUISITION_LINE_FIELDS,
    )
    return {
        "rfq_id": rfq["id"],
        "rfq_name": rfq["name"],
        "requisition": req,
    }


def list_requisitions(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    domain: list = []
    if args.get("state"):
        domain.append(("state", "=", args["state"]))
    rows = odoo.search_read(
        "purchase.requisition", domain, REQUISITION_FIELDS,
        limit=_clamp(args.get("limit"), config),
        order="create_date desc",
    )
    return {"count": len(rows), "requisitions": rows}


TOOLS = [
    {
        "name": "get_requisition_for_rfq",
        "description": (
            "Get the purchase.requisition (if any) that the procurement "
            "bridge wrote when this RFQ was awarded. Identify the RFQ via "
            "rfq_id (preferred) or rfq_name (RFQ/YYYY/NNNNN)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rfq_id": {"type": "integer"},
                "rfq_name": {"type": "string"},
            },
        },
        "handler": get_requisition_for_rfq,
    },
    {
        "name": "list_requisitions",
        "description": (
            "List purchase requisitions, optionally filtered by state. "
            "Useful for tracking procurement throughput from ProductGraph RFQs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
        "handler": list_requisitions,
    },
]
