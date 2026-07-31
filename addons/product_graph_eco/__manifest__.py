# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – Engineering Change Orders',
    'version': '19.0.1.10.0',
    'category': 'Manufacturing/PLM',
    'summary': 'Engineering Change Order (ECO) workflow tying revisions to a '
               'governed change-board approval cycle',
    'description': """
ProductGraph – Engineering Change Orders
=========================================

Implements the **pg.eco** state machine from State-Machine Bible §5
(Phase 2). An ECO is the governance wrapper around a coordinated set of
revision changes — multiple items can change together under one approved
change record, with audit trail and approval-board sign-off.

State machine
-------------

::

    draft -> analysis -> approval -> implementation -> released
                                 \\-> rejected

Models
------

* **pg.eco** — ECO header with state machine, change classification,
  priority, requestor / engineering-lead / approval-board assignment,
  effective + target-release dates, impact summary.
* **pg.eco.line** — Per-item affected scope: ``(pg.item, from_revision,
  to_revision, change_type, status)``. ``to_revision`` must reach the
  ``released`` state before the ECO can transition to ``released``.
* **pg.eco.approval** — Append-only approval audit row recording each
  approval-board member's decision (approved / rejected) + signed-on
  timestamp + free-text comment.

Constraints
-----------

* Released ECOs are immutable at ``write()`` (non-chatter fields).
* ECOs may only be unlinked while in ``draft``.
* Rejection requires a non-empty ``rejection_reason``.
* ``action_release`` blocks unless every line's ``to_revision_id`` is
  itself in the ``released`` state — keeps the ECO ↔ revision contracts
  in sync.

Phase-2 follow-ups (tracked, not in this module)
------------------------------------------------

* Mail templates for the transition notifications enumerated in
  State-Machine Bible §7.3.
* Wiring ``pg.release.eco_id`` so a release triggered by an ECO writes
  the back-link automatically (currently a manual O2M on pg.eco).
* CAD-connector handshake (``pg.eco.cad_change_set_ref``) for SOLIDWORKS
  PDM-driven ECOs.

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_base',
        'product_graph_revision',
        'product_graph_ebom',
        'mail',
    ],
    'data': [
        'security/pg_eco_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/mail_templates.xml',
        'data/pg_eco_config.xml',
        'views/pg_eco_views.xml',
        'views/pg_eco_line_views.xml',
        'views/pg_eco_approval_views.xml',
        'views/pg_eco_menus.xml',
        'views/pg_work_queue_views.xml',
        'wizard/views/eco_impact_wizard_view.xml',
    ],
    'demo': [
        'demo/pg_eco_demo.xml',
    ],
    'installable': True,
    'application': False,
}
