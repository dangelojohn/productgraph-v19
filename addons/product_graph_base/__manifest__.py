# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – Base',
    'version': '19.0.1.11.0',
    'category': 'Manufacturing/PLM',
    'summary': 'Engineering item catalog, property templates, and security foundation for ProductGraph',
    'description': """
ProductGraph – Base
===================

Foundation module for ProductGraph on Odoo 19 CE. Provides:

* **pg.item** — Engineering item master with 6-state lifecycle
  (concept → prototype → engineering → released → service → obsolete)
* **pg.category** + **pg.item.tag** — Engineering taxonomy
* **pg.property.template** + **pg.property.field** — OpenBOM-style dynamic
  per-item-type property templates (definition only; values live in
  ``product_graph_revision``)
* **pg.vendor** / **pg.vendor.part** — Model-only stubs for Phase 2 AVL/AML
* **pg.asset** — Digital Twin stub for Phase 3 maintenance/IoT linkage
* **pg.audit.log** — Append-only audit trail
* Six security groups: viewer < user < engineer < approver < mfg < admin
* Per-item-type sequences: PG-ASM, PG-PRT, PG-BUY, PG-DOC, PG-SFT, PG-SVC

This module installs cleanly on its own and is the foundation for
``product_graph_revision``, ``product_graph_ebom``, ``product_graph_release``,
and ``product_graph_api``.

Hard architectural rules
------------------------
* No raw SQL on pg.* tables.
* No hard deletes on records past ``concept``/``draft`` state.
* All state transitions emit an audit log + chatter message.
* Released records are immutable except via a controlled bypass context flag
  used only by the release flow (see ``product_graph_release``).

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'product',
    ],
    'data': [
        # Security loaded first
        'security/pg_security.xml',
        'security/ir.model.access.csv',

        # Sequences + static data
        'data/pg_sequence.xml',
        'data/pg_category_data.xml',
        'data/pg_category_cabinetry_seed.xml',
        'data/property_templates_seed.xml',
        'data/pg_sod_config.xml',

        # Views (one model per file)
        'views/pg_category_views.xml',
        'views/pg_item_tag_views.xml',
        'views/pg_property_template_views.xml',
        'views/pg_item_views.xml',
        'views/pg_asset_views.xml',
        'views/pg_audit_log_views.xml',
        'views/menus.xml',
        'views/res_config_settings_views.xml',

        # Wizards
        'wizard/views/force_release_wizard_view.xml',
        # apply_template_wizard moved to product_graph_revision (needs pg.revision)
    ],
    'demo': [
        'demo/pg_base_demo.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
    'sequence': 150,
}
