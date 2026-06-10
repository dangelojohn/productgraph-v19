# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – Vendor Governance (AVL/AML)',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing/PLM',
    'summary': 'Approved Vendor List / Approved Manufacturer List on top of '
               'the pg.vendor + pg.vendor.part stubs',
    'description': """
ProductGraph – Vendor Governance
=================================

Phase 2 module that turns the model-only ``pg.vendor`` / ``pg.vendor.part``
stubs (shipped in ``product_graph_base`` under Decision D3) into a usable
Approved Vendor List + Approved Manufacturer List feature with full UI.

What this module adds
---------------------

* **Vendor qualification lifecycle** on ``pg.vendor``:
  ``unqualified → probationary → qualified → suspended → disqualified``,
  state machine with audit-log + chatter on every transition (Bible R4).
* **pg.vendor.qualification** — append-only audit row capturing
  ``(decision, decided_by, decided_on, comment)`` per transition. Cannot
  be edited or deleted after creation.
* **pg.item.vendor_part_ids** — One2many of approved sources for an
  engineering item, with a computed ``primary_vendor_part_id`` (the
  highest-rank preferred source). Visible directly on the item form
  alongside the EBOM and revisions.
* **Vendor + vendor-part UI** — list / kanban / form / search views,
  filters for "qualified", "suspended", "preferred-source", grouping by
  status and currency.
* **Menus** under the existing ProductGraph root.

What this module does *not* do
------------------------------

* No procurement integration — ``purchase.order`` stays untouched. The
  ``primary_vendor_part_id`` is exposed for downstream consumption but
  this module does not write to purchase.
* No RFQ workflow — that's the planned ``product_graph_rfq`` companion.
* No automatic re-qualification cadence — qualification expiry is
  flagged on the form but enforcement is manual.

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_base',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/pg_vendor_views.xml',
        'views/pg_vendor_part_views.xml',
        'views/pg_vendor_qualification_views.xml',
        'views/pg_item_inherit_views.xml',
        'views/pg_vendor_menus.xml',
    ],
    'installable': True,
    'application': False,
}
