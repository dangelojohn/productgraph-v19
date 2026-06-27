# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – Procurement Bridge',
    'version': '19.0.1.8.0',
    'category': 'Manufacturing/PLM',
    'summary': 'On RFQ award, generate a purchase.requisition with the '
               'winning bids — closes the AVL → MRP loop',
    'description': """
ProductGraph – Procurement Bridge
==================================

Phase 2 bridge that hooks ``pg.rfq.action_award`` to create or refresh
a ``purchase.requisition`` carrying the winning bids as
``purchase.requisition.line`` entries. The procurement team picks up the
requisition without re-keying anything.

What this module does
---------------------

* Extends ``pg.rfq`` with a ``requisition_id`` Many2one + an
  ``auto_create_requisition`` flag.
* Overrides ``pg.rfq.action_award`` to call ``_create_requisition()``
  after the AVL-pricing write-back. The override is additive — the
  award still writes pg.vendor.part economics first, then writes the
  requisition lines.
* Maps each awarded bid → ``purchase.requisition.line`` with the
  bid's vendor (vendor.partner_id), price_unit, qty, product_id (if
  the item is already linked to a product.product via release), and
  the bid's vendor_part_number in the ``product_description_variants``.

What this module does NOT do
-----------------------------

* No purchase.order generation — requisitions are the agreed handover
  point. Procurement converts the requisition into POs.
* No vendor.partner_id auto-create — vendors must already have a
  linked res.partner (pg.vendor.partner_id).

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_rfq',
        'purchase_requisition',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/pg_w031_mirror_data.xml',
        'views/pg_rfq_views.xml',
        'views/purchase_inherit_views.xml',
        'views/pg_vendor_views.xml',
        'views/pg_switch_source_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
}
