# -*- coding: utf-8 -*-
"""
Inherit mrp.production to add permanent engineering traceability.

Architecture Bible §10.3 + Manufacturing Governance §11. These fields make
every Manufacturing Order traceable back to the exact ProductGraph release
that generated its BOM. They are read-only — only the release flow writes
to them.
"""
from odoo import fields, models


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    pg_release_id = fields.Many2one(
        'pg.release',
        string='ProductGraph Release',
        readonly=True,
        index=True,
        copy=False,
        help='The ProductGraph release that generated the BOM used by this MO.',
    )
    pg_ebom_id = fields.Many2one(
        'pg.ebom',
        string='Source EBOM',
        related='pg_release_id.ebom_id',
        store=True,
        readonly=True,
    )
    pg_revision_code = fields.Char(
        string='Engineering Revision',
        related='pg_release_id.revision_id.name',
        store=True,
        readonly=True,
    )
    pg_item_id = fields.Many2one(
        'pg.item',
        string='Engineering Item',
        related='pg_release_id.root_item_id',
        store=True,
        readonly=True,
    )

    def _create_or_update_from_release(self, release):
        """Hook for the release flow to stamp these fields.
        Called only when an MO is created against a newly released BOM —
        existing open MOs are FROZEN per MG-1 and never modified by this hook.
        """
        return self.with_context(_pg_release_bypass=True).write({
            'pg_release_id': release.id,
        })
