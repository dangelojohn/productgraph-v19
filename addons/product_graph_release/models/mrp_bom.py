# -*- coding: utf-8 -*-
"""
Inherit mrp.bom to add ProductGraph provenance markers.

Two fields, both written ONLY from inside ``PgRelease.action_execute_release``:

* ``pg_release_id`` — the release that CREATED this BOM. Set at mrp.bom
  ``create`` time inside the release savepoint. Allows R1-compliant
  identification of "BOMs we own" vs. manually-created BOMs.
* ``superseded_by_release_id`` — the release that ARCHIVED this BOM. Set
  when a follow-on release flips ``active=False`` on the prior BOM.

R1 carve-out: these are additive provenance trackers written inside the
single sanctioned write path (``action_execute_release``). They do not
expand the surface of arbitrary ``mrp.bom`` writes elsewhere in the codebase.
"""
from odoo import fields, models


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    pg_release_id = fields.Many2one(
        'pg.release',
        string='Created By ProductGraph Release',
        readonly=True,
        copy=False,
        index=True,
        help='The pg.release that generated this mrp.bom. Blank for BOMs '
             'created outside ProductGraph.',
    )
    superseded_by_release_id = fields.Many2one(
        'pg.release',
        string='Superseded By ProductGraph Release',
        readonly=True,
        copy=False,
        help='The pg.release that archived this mrp.bom (active=False).',
    )
