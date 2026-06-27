# -*- coding: utf-8 -*-
"""
Inherit mrp.production to add permanent engineering traceability.

Architecture Bible §10.3 + Manufacturing Governance §11. These fields make
every Manufacturing Order traceable back to the exact ProductGraph release
that generated its BOM. They are read-only — only the release flow writes
to them. The MO ``create()`` override looks up the owning ``pg.release`` by
``bom_id`` (Option A: cheap, deterministic, atomic) and stamps the field at
MO-create time. Existing open MOs are NEVER mutated (MG-1 frozen-MO rule).
"""
from odoo import api, fields, models


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
    pg_root_item_id = fields.Many2one(
        'pg.item',
        string='Engineering Item',
        related='pg_release_id.root_item_id',
        store=True,
        readonly=True,
    )

    # ─────────────────────────────────────────────────────────────────
    # W081 (R4.S5) — stored compute: is this MO on a superseded
    # engineering revision?
    # ─────────────────────────────────────────────────────────────────
    #
    # JTBD: "Planner sees 'your revision is older than latest' at a
    # glance." Without this flag, a planner reviewing the MO queue
    # has no quick way to know that a MO created against revision A
    # is now obsolete because revision B has been released.
    #
    # Computation: True iff `pg_release_id.revision_id.state ==
    # 'superseded'`. Stored so a list-view decoration / kanban badge
    # can read it cheaply (one column, no per-row resolver hit).
    # Depends include the revision state so the compute auto-refires
    # when an upstream pg.revision is superseded (Odoo's @api.depends
    # walks the m2o.m2o.field chain on touched records).
    pg_is_on_superseded_rev = fields.Boolean(
        string='On Superseded Revision',
        compute='_compute_pg_is_on_superseded_rev',
        store=True,
        index=True,
        help='True iff this MO was created from a pg.release whose '
             'pg.revision.state is now "superseded". Drives the red '
             'list-view decoration + kanban badge that warns the '
             'planner the MO is running an obsolete revision.',
    )

    @api.depends('pg_release_id.revision_id.state')
    def _compute_pg_is_on_superseded_rev(self):
        for mo in self:
            rev = mo.pg_release_id.revision_id
            mo.pg_is_on_superseded_rev = bool(
                rev and rev.state == 'superseded'
            )

    # ═════════════════════════════════════════════════════════════════════════
    # Create override — stamp traceability from bom_id (Option A)
    # ═════════════════════════════════════════════════════════════════════════

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-stamp ``pg_release_id`` from ``bom_id`` for new MOs.

        We resolve at create time rather than via a stored compute because
        ``mrp.bom`` is a small lookup and ``bom_id`` is typically set on
        create. If ``bom_id`` is set later, the fallback is the explicit
        ``_stamp_pg_release_from_bom()`` helper (rare).
        """
        bom_ids = [v.get('bom_id') for v in vals_list if v.get('bom_id') and not v.get('pg_release_id')]
        release_by_bom = {}
        if bom_ids:
            releases = self.env['pg.release'].sudo().search([
                ('mrp_bom_id', 'in', bom_ids),
                ('state', '=', 'completed'),
            ])
            for rel in releases:
                # Last completed release wins per bom_id (there should only be one).
                release_by_bom[rel.mrp_bom_id.id] = rel.id
        for vals in vals_list:
            bom_id = vals.get('bom_id')
            if bom_id and not vals.get('pg_release_id') and bom_id in release_by_bom:
                vals['pg_release_id'] = release_by_bom[bom_id]
        return super().create(vals_list)

    def _stamp_pg_release_from_bom(self):
        """Backfill ``pg_release_id`` for MOs whose bom_id was assigned after create."""
        for mo in self:
            if mo.pg_release_id or not mo.bom_id:
                continue
            release = self.env['pg.release'].sudo().search([
                ('mrp_bom_id', '=', mo.bom_id.id),
                ('state', '=', 'completed'),
            ], limit=1)
            if release:
                mo.write({'pg_release_id': release.id})

    def _create_or_update_from_release(self, release):
        """Hook for the release flow to stamp these fields explicitly."""
        return self.with_context(_pg_release_bypass=True).write({
            'pg_release_id': release.id,
        })
