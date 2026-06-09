# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – Engineering Lifecycle & PLM',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing/Manufacturing',
    'summary': 'Engineering Item Governance, Revision Control & Controlled Release to Manufacturing',
    'description': """
ProductGraph – Engineering Lifecycle & PLM
==========================================

ProductGraph is a Product Lifecycle Management (PLM) module for Odoo 19 Community Edition
that gives engineering teams full governance over items, revisions, and the controlled
release of Bills of Materials into manufacturing — without touching Odoo's core MRP data
until a release is formally approved.

Key Features
------------
* **Item Registry** – A single source of truth for every engineering item, separate from
  Odoo's product catalogue until release is authorised.
* **Revision Control** – Full draft → in_review → approved → released → obsolete lifecycle
  with mandatory immutability once a revision reaches *released*.
* **Controlled BOM Release** – ``mrp.bom`` records are created *only* inside
  ``PgRelease.action_execute_release()``. No BOM can be created outside this gate.
* **Change Request Workflow** – Formal ECO (Engineering Change Order / ECR) process with
  impact assessment, approver routing, and audit trail.
* **Manufacturing Collision Detection** – Automatic detection of open MOs referencing
  the same item before obsolescence is allowed.
* **Digital Twin Stubs** – Reserved schema path from ``pg.item`` → ``pg.asset`` → IoT
  device for Phase 2 without breaking migrations.
* **REST API** – JSON endpoints for CAD/PLM tool integration.
* **Full Audit Log** – Every state change and field write on released records is logged
  with user, timestamp, and before/after values.

Architecture Principles
-----------------------
* **ProductGraph owns** ``pg.*`` — Odoo owns ``mrp.*``.
* ``mrp.bom`` is never created by any code outside ``PgRelease.action_execute_release()``.
* Released revisions and items are **immutable** — write attempts raise ``UserError``
  directing users to the ECO process.
* All state transitions are guarded by ``_check_transition_allowed()`` with logged reasons.

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors (co-developed with Claude AI / Anthropic)',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'support': 'support@productgraph.io',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'product',
        'mrp',
        'stock',
        'web',
    ],
    'data': [
        # Security — load first
        'security/pg_security.xml',
        'security/ir.model.access.csv',

        # Sequences & static data
        'data/pg_sequence.xml',
        'data/pg_category_data.xml',

        # Views
        'views/pg_category_views.xml',
        'views/pg_item_views.xml',
        'views/pg_revision_views.xml',
        'views/pg_bom_line_views.xml',
        'views/pg_release_views.xml',
        'views/pg_change_request_views.xml',
        'views/pg_asset_views.xml',
        'views/pg_audit_log_views.xml',
        'views/pg_menuitem.xml',

        # Wizards
        'wizards/pg_release_wizard_views.xml',
        'wizards/pg_ecr_reject_wizard_views.xml',
    ],
    'demo': [
        'demo/pg_demo_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'product_graph/static/src/scss/pg_styles.scss',
            'product_graph/static/src/js/pg_statusbar.js',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'auto_install': False,
    'application': True,
    'sequence': 150,
    'price': 0.0,
    'currency': 'EUR',
}
