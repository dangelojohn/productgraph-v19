"""Entry point — runs the MCP server with the transport configured via env."""
import asyncio
import logging
import os
import sys

from .config import Config
from .server import build_server


def main() -> int:
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    )
    log = logging.getLogger("product_graph_mcp")
    log.info("Starting ProductGraph MCP server (transport=%s)", config.transport)

    server = build_server(config)

    if config.transport == "stdio":
        from mcp.server.stdio import stdio_server

        async def _run():
            async with stdio_server() as (read, write):
                await server.run(
                    read, write,
                    server.create_initialization_options(),
                )

        asyncio.run(_run())
    elif config.transport == "sse":
        # MCP SDK 1.x SSE transport
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
        import uvicorn

        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send,
            ) as (read, write):
                await server.run(
                    read, write,
                    server.create_initialization_options(),
                )

        app = Starlette(routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ])
        uvicorn.run(app, host=config.host, port=config.port, log_level=config.log_level.lower())
    else:
        log.error("Unknown transport %r", config.transport)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
