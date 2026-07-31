# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – REST API v1',
    'version': '19.0.1.3.0',
    'category': 'Manufacturing/PLM',
    'summary': 'JSON REST API at /api/v1/productgraph/* for ProductGraph',
    'description': """
ProductGraph – REST API v1
==========================

JSON-only REST controllers under ``/api/v1/productgraph/`` matching the API
Bible (docs/ProductGraph_Architecture_Bible_v1.0.md Part VI).

Endpoints:

* GET    /health
* GET    /items, /items/<id>, /items/<id>/where-used
* POST   /items
* GET    /revisions, /revisions/<id>
* POST   /revisions
* POST   /revisions/<id>/submit, /release, /reject
* GET    /eboms, /eboms/<id>, /eboms/<id>/explosion
* POST   /eboms
* POST   /eboms/<id>/lines
* GET    /releases, /releases/<id>, /releases/<id>/status
* POST   /releases

Auth: Odoo personal API keys (header ``Authorization: Bearer <key>``)
enforced at the route level via ``auth='bearer'`` (Odoo 19 stock auth
method). Each mutating endpoint additionally checks the caller's groups
per CLAUDE.md R6.

The sidecar MCP service (``mcp/``) consumes this API via XML-RPC + REST.
External CAD connectors, headless integrations, and CI scripts use the same.

BREAKING CHANGE in 19.0.1.2.0: route auth changed from ``'user'`` (session)
to ``'bearer'`` (Authorization header). The earlier manifest claimed bearer
auth but the implementation only validated session cookies; this release
makes that claim true. The MCP sidecar and any external clients must send
``Authorization: Bearer <key>`` on every request; ``/health`` remains
``auth='public'``.

Compatible with Odoo 19.0 Community Edition.

Rate limiting (B8.4): every route (bearer and public alike) passes through
the shared ``_safe`` controller wrapper, which enforces a Postgres-backed
fixed-window (60s) per-caller-per-route request cap before the handler
runs. Defaults are generous (120/min for bearer callers, 300/min for
public callers such as ``/health``) and tunable via
``ir.config_parameter``:

* ``productgraph.api_rate_limit_per_minute``
* ``productgraph.api_rate_limit_per_minute_public``

Set either to ``0`` to disable throttling for that caller class.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_base',
        'product_graph_revision',
        'product_graph_ebom',
        'product_graph_release',
        'web',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_prune_rate_limit.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'sequence': 154,
}
