# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – OnShape Driver',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing/PLM',
    'summary': 'OnShape REST driver for product_graph_cad — pure HTTPS, '
               'no vendor libraries required',
    'description': """
ProductGraph – OnShape Driver
==============================

Adds an OnShape driver to ``product_graph_cad``. OnShape exposes a clean
REST API over HTTPS with bearer-token auth, so this driver runs natively
in the Odoo container — no proxy needed.

Configuration (``pg.cad.connector.config_json``)
------------------------------------------------

::

    {
        "base_url":      "https://cad.onshape.com",
        "api_key":       "...",          // bearer token
        "document_id":   "...",          // OnShape did
        "workspace_id":  "...",          // OnShape wid
        "element_id":    "..."           // OnShape eid (Part Studio
                                          // or Assembly)
    }

Use OnShape's API key pair (access + secret) and combine them per
OnShape's documented HMAC scheme to produce the bearer token your
deployment uses. The driver does NOT implement HMAC signing — it
takes the prepared token. Most OnShape integrations use long-lived
API keys with an admin-issued HMAC sign-up step.

What this driver does
---------------------

* ``GET /api/v6/documents/d/<did>/w/<wid>/e/<eid>/parts`` — fetches
  the part / assembly list and turns each into a CadDocument
* ``GET /api/v6/assemblies/d/<did>/w/<wid>/e/<eid>/parts`` — fetches
  the assembly's 1-level BOM when the element is an assembly

What this driver does NOT do
-----------------------------

* No drawing / STEP export — only metadata + BOM
* No webhook subscriptions for change notifications
* No multi-element traversal — one connector record = one element

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
