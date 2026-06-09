# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph ↔ Southbrook PLM Bridge',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing/PLM',
    'summary': 'Glue addon — Southbrook PLM ECO closure triggers a ProductGraph release',
    'description': """
ProductGraph ↔ Southbrook PLM Bridge
=====================================

Connects the existing Southbrook PLM ECO workflow to the generic
ProductGraph release flow (Decision D1).

* `southbrook_plm.eco` → adds pg_ebom_id, pg_release_id, pg_auto_release
* On ECO close: fires `pg.release.action_execute_release` if configured.

Coupling is strictly ONE-WAY. The generic ProductGraph addons never import
anything from this addon or from `southbrook_plm`.

This addon is Southbrook-specific. Do not publish — move to the private
~/southbrook-v19cr/ repository before deploying.
    """,
    'author': 'Southbrook Cabinetry (private)',
    'license': 'OPL-1',
    'depends': [
        'product_graph_release',
        'southbrook_plm',
    ],
    'data': [
        'views/southbrook_plm_eco_inherit_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
