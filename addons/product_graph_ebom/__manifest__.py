# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – Engineering BOM',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing/PLM',
    'summary': 'Multi-level engineering BOMs with materialized where-used closure',
    'description': """
ProductGraph – Engineering BOM
==============================

Adds the engineering BOM layer on top of ``product_graph_revision``:

* **pg.ebom** — Engineering BOM header. Four-state lifecycle
  (draft → review → released → superseded). Released EBOMs are the input to
  ``pg.release.action_execute_release`` which mints the matching ``mrp.bom``.
* **pg.ebom.line** — Component lines. Native x2many editable list, copy-paste
  friendly. CSV import is supported via Odoo's standard list import.
* **pg.ebom.closure** — Materialized transitive-closure table maintained on
  EBOM release. Powers sub-3-second where-used and BOM-explosion queries on
  Phase 1 scale targets (100k items, 1M lines, 15 levels deep).

Per Architecture Bible §6.2 — EBOM is the engineering structure; mrp.bom is
the manufacturing structure. The only bridge between them is
``pg.release.action_execute_release`` (in ``product_graph_release``).

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_base',
        'product_graph_revision',
        'uom',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/pg_ebom_sequence.xml',
        'views/pg_ebom_line_views.xml',
        'views/pg_ebom_views.xml',
        'views/pg_ebom_closure_views.xml',
        'views/pg_ebom_menus.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'sequence': 152,
}
