"""Revision flow — propose (create), comment. All mutating, group-checked."""
from __future__ import annotations

from typing import Any

from ..odoo_client import OdooClient
from ..config import Config


def propose_revision(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    """Create a new draft revision against an item."""
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
    if not args.get("change_summary"):
        return {"error": "change_summary required (min 10 chars)"}
    code = args.get("revision_code")
    if not code:
        # Ask Odoo for the next code via the helper method.
        code = odoo.call_method("pg.item", [int(item_id)], "_next_revision_code")
        if isinstance(code, list):
            code = code[0] if code else "A"
    rev_id = odoo.create("pg.revision", {
        "item_id": int(item_id),
        "name": code,
        "change_summary": args["change_summary"],
        "incompatible_with_previous": bool(args.get("incompatible_with_previous", False)),
        "incompatibility_reason": args.get("incompatibility_reason"),
    })
    return {"revision_id": rev_id, "revision_code": code, "state": "draft"}


def comment_on_revision(odoo: OdooClient, config: Config, args: dict[str, Any]) -> dict:
    rev_id = args.get("revision_id")
    body = args.get("body")
    if not rev_id or not body:
        return {"error": "revision_id and body required"}
    odoo.call_method(
        "pg.revision", [int(rev_id)], "message_post",
        kwargs={"body": body, "subtype_xmlid": "mail.mt_note"},
    )
    return {"ok": True}


TOOLS = [
    {
        "name": "propose_revision",
        "description": (
            "Create a new draft revision against an item. The agent must "
            "provide a change_summary of at least 10 characters."
        ),
        "input_schema": {
            "type": "object",
            "required": ["change_summary"],
            "properties": {
                "item_id": {"type": "integer"},
                "part_number": {"type": "string"},
                "revision_code": {
                    "type": "string",
                    "description": "Optional. Auto-derived (A, B, C…) if omitted.",
                },
                "change_summary": {"type": "string", "minLength": 10},
                "incompatible_with_previous": {"type": "boolean"},
                "incompatibility_reason": {"type": "string"},
            },
        },
        "handler": propose_revision,
        "mutating": True,
    },
    {
        "name": "comment_on_revision",
        "description": "Post a chatter note on an existing revision.",
        "input_schema": {
            "type": "object",
            "required": ["revision_id", "body"],
            "properties": {
                "revision_id": {"type": "integer"},
                "body": {"type": "string"},
            },
        },
        "handler": comment_on_revision,
        "mutating": True,
    },
]
