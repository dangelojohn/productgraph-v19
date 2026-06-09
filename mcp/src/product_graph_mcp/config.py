"""Env-driven configuration. Single source of truth — no other module reads os.environ."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class Config:
    odoo_url: str
    db: str
    user: str
    api_key: str
    transport: str
    host: str
    port: int
    default_limit: int
    max_limit: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        def need(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                raise SystemExit(
                    f"Required environment variable {name} is missing. "
                    f"See .env.example."
                )
            return v

        return cls(
            odoo_url=need("PRODUCTGRAPH_ODOO_URL").rstrip("/"),
            db=need("PRODUCTGRAPH_DB"),
            user=need("PRODUCTGRAPH_ODOO_USER"),
            api_key=need("PRODUCTGRAPH_API_KEY"),
            transport=os.environ.get("PRODUCTGRAPH_MCP_TRANSPORT", "stdio").lower(),
            host=os.environ.get("PRODUCTGRAPH_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("PRODUCTGRAPH_MCP_PORT", "9234")),
            default_limit=int(os.environ.get("PRODUCTGRAPH_DEFAULT_LIMIT", "50")),
            max_limit=int(os.environ.get("PRODUCTGRAPH_MAX_LIMIT", "500")),
            log_level=os.environ.get("PRODUCTGRAPH_LOG_LEVEL", "INFO").upper(),
        )
