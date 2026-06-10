# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – SOLIDWORKS PDM Driver',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing/PLM',
    'summary': 'SOLIDWORKS PDM driver for product_graph_cad — HTTP client '
               'to a Windows-side EPDM-COM proxy service',
    'description': """
ProductGraph – SOLIDWORKS PDM Driver
=====================================

Adds a SOLIDWORKS Enterprise PDM driver to ``product_graph_cad``.

Architecture: HTTP client to a Windows-side proxy
--------------------------------------------------

The SOLIDWORKS EPDM API (``IEdmVault5``, ``IEdmFile5``, ``IEdmBomMgr``)
is a Windows COM interface that needs ``pywin32`` + a SOLIDWORKS PDM
client installation to call. The Odoo container runs on Linux, so the
driver does **not** try to call COM directly. Instead it talks HTTP to
a small Windows-side proxy service that wraps the COM calls.

The proxy can be implemented in any language that can talk EPDM COM —
C# .NET 6+ is the natural choice (one csproj, ~200 lines). The
contract this driver requires:

::

    GET  /pdm/info
        -> {"vault": "...", "version": "...", "reachable": true}

    GET  /pdm/documents?folder=<EFolder>
        -> [{"path": "...", "name": "...", "rev": "..."}, ...]

    GET  /pdm/document?path=<EFolder>
        -> {"path": "...", "name": "...", "description": "...",
            "item_type": "...", "card_variables": {...},
            "bom": [{"child_path": "...", "qty": <f>, "ref": "..."}]}

The proxy is deployment-specific (vault layout, auth, host) and is
NOT shipped here. A reference C# implementation lives in
``docs/solidworks_proxy_spec.md``.

What this driver does
---------------------

* Implements ``SolidworksPdmConnector(BaseCadConnector)`` with the
  same three methods (``fetch_metadata``, ``list_documents``,
  ``pull_document``) as the JSON-file reference driver.
* Configuration via ``config_json``:

  - ``proxy_url`` (required): http://win-pdm-proxy:9100
  - ``vault`` (required): name of the EPDM vault
  - ``folder_efolder`` (optional): EFolder ID to scope the pull;
    omit for full-vault traversal
  - ``api_key`` (optional): shared secret sent as
    ``X-Proxy-Key`` header

* Maps EPDM card variables → ``pg.item.properties`` for property-
  template population in pass 2.

What this driver does NOT do
----------------------------

* No COM directly. The proxy does that.
* No vault discovery — the vault name is configured per connector.
* No bidirectional sync — read-only from the vault.

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_cad',
    ],
    'data': [],
    'installable': True,
    'application': False,
}
