# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – Relationship Engine',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing/PLM',
    'summary': 'Directed-edge engineering relationships between pg.items '
               '(substitutes, alternates, supersedes, references)',
    'description': """
ProductGraph – Relationship Engine
====================================

Phase 2 module that adds directed-edge relationships between engineering
items. These are the relationships that live *outside* the BOM tree —
substitutes, drop-in alternates, supersession chains, free-text
references — which the EBOM closure cannot express but which production
engineering depends on every day.

Where this sits in the stack
----------------------------

* ``product_graph_ebom`` owns the BOM hierarchy and its materialized
  closure. That is the "parent-child" graph.
* This module owns the "sideways" graph — substitutes, alternates,
  supersedes, references. Each is a directed edge with an explicit
  kind.

The two graphs are independent. A query for "what items can replace this
item" walks the relationship edges; "what assemblies use this item" walks
the EBOM closure.

Models
------

* **pg.relationship** — One directed edge ``(source_item, target_item,
  kind)``. The kinds are:

  - ``substitutes`` — drop-in replacement (form-fit-function equivalent).
    Treated as **symmetric** by the helper methods: if A substitutes B,
    B substitutes A.
  - ``alternates`` — interchangeable for some operating envelope but
    not necessarily drop-in. Also symmetric.
  - ``supersedes`` — explicit lineage: A supersedes B. The
    superseded item is the predecessor; the successor is what's
    actually shipped now.
  - ``references`` — free-form linkage (e.g., a part references a
    drawing or a parent assembly).
  - ``related`` — catch-all for non-categorised engineering links.

* **pg.item inherit** — adds computed many2many helpers:

  - ``substitute_ids`` (symmetric)
  - ``alternate_ids`` (symmetric)
  - ``supersedes_ids`` (this item supersedes these — predecessors)
  - ``superseded_by_ids`` (this item is superseded by these — successors)
  - ``reference_ids`` (this item references these)
  - ``related_to_ids`` (this item is the source of a ``related`` edge)

The item form gets a "Relationships" tab listing the outgoing edges
grouped by kind.

Constraints
-----------

* ``source_item_id`` != ``target_item_id`` (no self-edges).
* No duplicate ``(source, target, kind)`` triple.
* Supersession edge to / from an obsolete item is allowed (lineage is
  preserved across obsolescence).

What this module does NOT do
-----------------------------

* No materialised closure (one-hop queries today; closure can be added
  later if traversal hot-spots appear).
* No CAD-driven auto-population — that's the planned ``pg.cad``
  connector module's job.
* No ECO integration — relationships are not auto-superseded by
  pg.eco award. That coupling will be added when the eco→graph bridge
  is specified.

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_base',
        'product_graph_eco',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/pg_relationship_views.xml',
        'views/pg_item_inherit_views.xml',
        'views/pg_relationship_menus.xml',
    ],
    'installable': True,
    'application': False,
}
