"""Thin XML-RPC client to Odoo, with API-key authentication.

The MCP server uses XML-RPC rather than the REST controllers when calling
Odoo so that the ProductGraph REST module stays an *optional* extra — the
sidecar can talk to an Odoo deployment that has only base/revision/ebom/release
installed.
"""
from __future__ import annotations

import logging
import threading
import xmlrpc.client
from typing import Any, Sequence

from .config import Config

log = logging.getLogger(__name__)


class OdooClient:
    """Connection-per-thread wrapper around xmlrpc.client.ServerProxy."""

    def __init__(self, config: Config):
        self.config = config
        self._tls = threading.local()
        self._uid_cache: int | None = None

    # ─── Internals ──────────────────────────────────────────────────────────

    def _proxy(self, endpoint: str) -> xmlrpc.client.ServerProxy:
        key = f"_proxy_{endpoint}"
        proxy = getattr(self._tls, key, None)
        if proxy is None:
            url = f"{self.config.odoo_url}/xmlrpc/2/{endpoint}"
            proxy = xmlrpc.client.ServerProxy(url, allow_none=True)
            setattr(self._tls, key, proxy)
        return proxy

    def _authenticate(self) -> int:
        if self._uid_cache is not None:
            return self._uid_cache
        common = self._proxy("common")
        uid = common.authenticate(
            self.config.db,
            self.config.user,
            self.config.api_key,
            {},
        )
        if not uid:
            raise RuntimeError(
                "Odoo authentication failed. Check PRODUCTGRAPH_ODOO_USER and "
                "PRODUCTGRAPH_API_KEY."
            )
        log.info("Authenticated to Odoo as %s (uid=%s)", self.config.user, uid)
        self._uid_cache = uid
        return uid

    # ─── Public API ─────────────────────────────────────────────────────────

    def execute(self, model: str, method: str, args: Sequence[Any] | None = None,
                kwargs: dict | None = None) -> Any:
        uid = self._authenticate()
        return self._proxy("object").execute_kw(
            self.config.db,
            uid,
            self.config.api_key,
            model,
            method,
            list(args or []),
            kwargs or {},
        )

    def search_read(self, model: str, domain: list, fields: list[str],
                    limit: int | None = None, offset: int = 0,
                    order: str | None = None) -> list[dict]:
        kwargs: dict[str, Any] = {"fields": fields, "offset": offset}
        if limit is not None:
            kwargs["limit"] = limit
        if order:
            kwargs["order"] = order
        return self.execute(model, "search_read", [domain], kwargs)

    def read(self, model: str, ids: Sequence[int], fields: list[str]) -> list[dict]:
        return self.execute(model, "read", [list(ids)], {"fields": fields})

    def search(self, model: str, domain: list, limit: int | None = None) -> list[int]:
        kwargs = {"limit": limit} if limit is not None else {}
        return self.execute(model, "search", [domain], kwargs)

    def search_count(self, model: str, domain: list) -> int:
        return self.execute(model, "search_count", [domain])

    def create(self, model: str, vals: dict) -> int:
        return self.execute(model, "create", [vals])

    def write(self, model: str, ids: Sequence[int], vals: dict) -> bool:
        return self.execute(model, "write", [list(ids), vals])

    def call_method(self, model: str, ids: Sequence[int], method: str,
                    args: Sequence[Any] | None = None,
                    kwargs: dict | None = None) -> Any:
        return self.execute(model, method, [list(ids)] + list(args or []), kwargs or {})

    # ─── Auth boundary check (Decision D6) ───────────────────────────────────

    def has_group(self, xml_id: str) -> bool:
        """Return whether the authenticated user is in the named group."""
        uid = self._authenticate()
        result = self.execute(
            "res.users", "has_group", [[uid], xml_id],
        )
        # has_group returns bool when called via the model rather than the recordset.
        # When called with [[uid], xml_id] Odoo invokes it on the user record and
        # the result is a list of bool aligned with the ids. Normalize.
        if isinstance(result, list):
            return bool(result and result[0])
        return bool(result)

    def require_group(self, xml_id: str) -> None:
        if not self.has_group(xml_id):
            raise PermissionError(
                f"User {self.config.user} lacks required group {xml_id}."
            )
