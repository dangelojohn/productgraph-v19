# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – RFQ Workflow',
    'version': '19.0.1.8.0',
    'category': 'Manufacturing/PLM',
    'summary': 'Engineering-driven Request-for-Quote workflow that feeds AVL '
               'pricing from real vendor bids',
    'description': """
ProductGraph – RFQ Workflow
============================

Phase 2 module that adds an engineering-driven Request-for-Quote workflow
on top of ``product_graph_vendor``.

The RFQ here is distinct from Odoo's procurement RFQ: this one is the
engineering-side enquiry that produces *prices we trust enough to write
back to the AVL*. Procurement integration can layer on top later.

Flow
----

::

    draft -> sent -> received -> awarded
                              -> cancelled

Models
------

* **pg.rfq** — Header. Holds the request title, due date, requesting
  engineer, set of items being quoted. State machine + chatter.
* **pg.rfq.line** — One row per (item, qty) being quoted. Each line
  references a single ``pg.item`` and target quantity; vendors bid
  against the lines.
* **pg.rfq.bid** — A vendor's quote against a specific RFQ line.
  Fields: vendor, unit price, lead time, MOQ, valid-until, comment.
  Multiple bids per line are allowed; the ``is_awarded`` flag tags
  the winner.

Award action
------------

``pg.rfq.action_award`` walks every line of an RFQ and for each bid
flagged ``is_awarded=True``:

1. Finds (or creates) a ``pg.vendor.part`` for the (item, vendor) pair.
2. Updates ``unit_price_hint``, ``lead_time_days``, and ``moq`` from the
   winning bid.
3. Promotes the AVL row to ``is_preferred=True`` if the option was set
   on the RFQ award.

The RFQ is the only place this module writes to ``pg.vendor.part``.

Constraints
-----------

* Bids may only be created / edited while the RFQ is in ``sent`` or
  ``received`` (after award, prices are locked).
* Award requires the RFQ to be in ``received`` and every line to have
  at least one ``is_awarded`` bid.
* Only one bid per line may carry ``is_awarded=True``.
* Awarded RFQ records are immutable at ``write()`` (non-chatter fields).

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_base',
        'product_graph_vendor',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/pg_rfq_cron.xml',
        'views/pg_rfq_views.xml',
        'views/pg_rfq_line_views.xml',
        'views/pg_rfq_bid_views.xml',
        'views/pg_sourcing_inbox.xml',
        'views/pg_rfq_menus.xml',
    ],
    'installable': True,
    'application': False,
}
