# -*- coding: utf-8 -*-
{
    'name': 'ProductGraph – CAD Connector Framework',
    'version': '19.0.1.0.0',
    'category': 'Manufacturing/PLM',
    'summary': 'Pluggable CAD connector framework with a JSON-file '
               'reference driver — real PDM drivers slot in behind the '
               'same interface',
    'description': """
ProductGraph – CAD Connector Framework
========================================

Phase 2 module that brings engineering items + EBOM structure in from
upstream CAD / PDM systems. The contract is the same regardless of
source — SOLIDWORKS PDM, OnShape, a flat JSON export from any other
system. This module ships the abstraction plus a JSON-file driver that
production teams can use today; vendor-specific drivers slot in behind
the same interface.

Why JSON-file is the default driver
------------------------------------

* Every real CAD / PDM system can already export its BOM to JSON.
* No vendor library dependencies in the base module.
* The same payload shape is what a real SOLIDWORKS PDM driver would
  produce after walking the EPDM file vault — so the contract is real,
  not a toy.
* Test fixtures are JSON files in the repo, not mocked function calls.

Models
------

* **pg.cad.connector** — One row per configured upstream. Holds code,
  driver class, state, config JSON, last sync, last status.
* **pg.cad.import** — Run record per pull. State machine: pending →
  running → completed / failed. Atomic — if any line fails, the whole
  run rolls back.
* **pg.cad.import.line** — Per-document trace: part_number, action
  (created / updated / skipped / error), message.

Driver interface (Python)
-------------------------

Drivers live under ``connectors/`` and inherit
``BaseCadConnector`` (``connectors/base.py``). Three abstract methods:

* ``fetch_metadata() -> dict``: connector self-description (used by
  ``action_test_connection``)
* ``list_documents() -> list[CadDocument]``: discover what's pullable
* ``pull_document(ref) -> CadDocument``: load one document with its
  BOM

The shipped ``JsonFileConnector`` reads a single file at the
configured path containing a ``{"documents": [...]}`` envelope.

Pull action
-----------

``pg.cad.connector.action_pull`` walks every document the driver
returns and for each:

1. Finds or creates the matching ``pg.item`` by part_number.
2. Updates name / description / item_type / responsible_id.
3. Optionally creates a draft ``pg.revision`` and ``pg.ebom`` from the
   CAD document's BOM tree (one level — recursion is the driver's
   responsibility).
4. Writes a ``pg.cad.import.line`` trace row.

The whole run is wrapped in a ``savepoint()`` — any
ValidationError / UserError on any line rolls back the entire pull and
marks the ``pg.cad.import`` as failed with the captured reason.

What this module does NOT do
-----------------------------

* No CAD-side push (we read-from, never write-to, the CAD vault).
* No incremental / delta sync (always full pull — driver decides
  scope).
* No SOLIDWORKS-specific code (that lives in a separate
  ``product_graph_cad_solidworks`` driver module that depends on this
  one).

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
        'security/ir.model.access.csv',
        'views/pg_cad_connector_views.xml',
        'views/pg_cad_import_views.xml',
        'views/pg_cad_menus.xml',
    ],
    'installable': True,
    'application': False,
}
