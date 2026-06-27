# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – Revision Control',
    'version': '19.0.1.7.0',
    'category': 'Manufacturing/PLM',
    'summary': 'Revision lifecycle, property values, and versioned engineering documents',
    'description': """
ProductGraph – Revision Control
================================

Adds revision lifecycle on top of ``product_graph_base``:

* **pg.revision** — Five-state revision lifecycle
  (draft → review → released → superseded → obsolete) with strict immutability
  on released revisions enforced at the ORM ``write()`` level. Uses the
  ``_pg_release_bypass`` context flag for the controlled bypass used by the
  release flow.
* **pg.property.value** — Typed value per (revision, property field).
  One row per field per revision; uniqueness enforced at the DB level.
* **pg.document** — Versioned engineering document attachments scoped to a
  revision. Documents on released revisions are read-only.

Phase 2 (not in this module): EBOM, ECO, release-to-MRP.

Compatible with Odoo 19.0 Community Edition.
    """,
    'author': 'ProductGraph Contributors',
    'website': 'https://github.com/productgraph/odoo-productgraph',
    'license': 'LGPL-3',
    'depends': [
        'product_graph_base',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/pg_revision_sequence.xml',
        'views/pg_property_value_views.xml',
        'views/pg_document_views.xml',
        'views/pg_revision_views.xml',
        'views/pg_asset_views.xml',
        'wizards/pg_revision_reject_wizard_views.xml',
        'wizards/apply_template_wizard_view.xml',
        'views/pg_revision_menus.xml',
    ],
    'demo': [
        'demo/pg_revision_demo.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'sequence': 151,
}
