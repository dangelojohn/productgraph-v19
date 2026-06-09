"""MCP server wiring — registers every tool and dispatches calls."""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from .config import Config
from .odoo_client import OdooClient
from .tools import catalog, bom, revision as revision_tools, document, release

log = logging.getLogger(__name__)


# Each tool module exposes (name, description, input_schema, handler, mutating).
# Mutating tools force the auth boundary check inside the handler too.
TOOL_REGISTRY = [
    # Catalog (read-only)
    *catalog.TOOLS,
    # BOM analysis (read-only)
    *bom.TOOLS,
    # Revision flow (mutating)
    *revision_tools.TOOLS,
    # Documents (mutating)
    *document.TOOLS,
    # Release simulation (read-only — full release goes through the wizard / REST)
    *release.TOOLS,
]


def build_server(config: Config) -> Server:
    odoo = OdooClient(config)
    server = Server("product-graph")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["input_schema"],
            )
            for spec in TOOL_REGISTRY
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        arguments = arguments or {}
        spec = next((t for t in TOOL_REGISTRY if t["name"] == name), None)
        if spec is None:
            return [TextContent(type="text", text=json.dumps({
                "error": f"Unknown tool: {name}",
            }))]
        try:
            if spec.get("mutating"):
                # Decision D6 — per-tool group check at the call site.
                odoo.require_group("product_graph_base.group_pg_engineer")
            result = spec["handler"](odoo, config, arguments)
            payload = json.dumps(result, default=str, indent=2)
            return [TextContent(type="text", text=payload)]
        except PermissionError as e:
            return [TextContent(type="text", text=json.dumps({
                "error": "permission_denied",
                "message": str(e),
            }))]
        except Exception as e:
            log.exception("Tool %s failed", name)
            return [TextContent(type="text", text=json.dumps({
                "error": "tool_error",
                "tool": name,
                "message": str(e),
            }))]

    return server
