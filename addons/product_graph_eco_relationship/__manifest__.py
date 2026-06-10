# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – ECO → Relationship Bridge',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing/PLM',
    'summary': 'When an ECO releases, write the supersedes edges to '
               'pg.relationship automatically',
    'description': """
ProductGraph – ECO → Relationship Bridge
==========================================

When an ECO releases (state moves to ``released``), this bridge walks
its lines and writes ``pg.relationship`` rows so the sideways graph
stays in sync with engineering decisions.

What this module does
---------------------

* Adds ``replacement_item_id`` to ``pg.eco.line`` — an optional pointer
  to the item that takes over for an obsoleted one.
* On ECO release, for every line with ``change_kind='obsolete'`` and a
  ``replacement_item_id`` set, writes one ``pg.relationship`` row::

      source_item_id = replacement_item_id     # the new item
      target_item_id = line.item_id            # the old one
      kind           = 'supersedes'
      eco_id         = the releasing ECO

  The closure rebuilder fires automatically (via the existing
  pg.relationship.create hook), so the supersession chain is queryable
  the moment the ECO closes.

* Idempotent: re-running ``action_release`` (which can't happen today
  because released ECOs are immutable, but might once a future
  re-release flow lands) does not create duplicate edges.

What this module does NOT do
-----------------------------

* Does not write substitute / alternate edges — only supersedes.
* Does not retroactively process ECOs that released before this
  module was installed; admin can run the migration helper manually.

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_eco',
        'product_graph_relationship',
    ],
    'data': [
        'views/pg_eco_views.xml',
    ],
    'installable': True,
    'application': False,
}
