# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – Release Governance',
    'version': '19.0.1.4.0',
    'category': 'Manufacturing/PLM',
    'summary': 'The single governance gate between engineering and manufacturing — the only place mrp.bom is created',
    'description': """
ProductGraph – Release Governance
=================================

The keystone module. Contains the single code path that bridges engineering
data into Odoo manufacturing: ``PgRelease.action_execute_release``.

* **pg.release** — Immutable release-audit record. Four-state machine:
  pending → executing → completed → failed.
* **pg.release.line** — Per-line traceability from pg.ebom.line to
  mrp.bom.line.
* **pg.release.wizard** — Five-step pre-flight UI: validate → frozen-MO
  warning → reason → confirm → success.
* **mrp.production** inherit — Adds pg_release_id / pg_ebom_id /
  pg_revision_code traceability fields so every Manufacturing Order is
  permanently linked to the engineering revision that produced its BOM.
* QWeb reports — Indented BOM, Where Used, Release Audit Trail.

Manufacturing Governance (docs/026_Manufacturing_Governance.md) is fully
implemented:

* MG-1 — Existing open MOs are frozen at the previous BOM (recorded in
  ``pg.release.frozen_mo_ids``).
* MG-2 — Open POs trigger a notification (no auto-amendment).
* MG-3 — ``pg.revision.incompatible_with_previous`` blocks old-revision
  stock consumption.
* MG-5 — Old mrp.bom for the same product is deactivated atomically.

Bible-faithful: every release wraps in a single transaction savepoint; any
failure rolls back the mrp.bom + product.product changes and marks the
pg.release as ``failed`` with the captured reason.

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_base',
        'product_graph_revision',
        'product_graph_ebom',
        'mrp',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/pg_release_sequence.xml',
        'views/pg_release_views.xml',
        'views/pg_release_line_views.xml',
        'views/mrp_production_inherit_views.xml',
        'views/mrp_bom_inherit_views.xml',
        'data/pg_release_cron.xml',
        'wizards/pg_release_wizard_views.xml',
        'reports/pg_report_indented_bom.xml',
        'reports/pg_report_where_used.xml',
        'reports/pg_report_release_audit.xml',
        'views/pg_release_menus.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'sequence': 153,
}
