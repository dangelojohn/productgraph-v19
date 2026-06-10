# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Kinds that are treated as symmetric by the helper methods. An edge
# (A, B, substitutes) implies (B, A, substitutes) for traversal purposes
# without us having to store a second physical row.
SYMMETRIC_KINDS = frozenset({'substitutes', 'alternates'})

KIND_SELECTION = [
    ('substitutes', 'Substitutes (drop-in replacement)'),
    ('alternates', 'Alternate (interchangeable within envelope)'),
    ('supersedes', 'Supersedes (predecessor → successor)'),
    ('references', 'References'),
    ('related', 'Related (free-form)'),
]


class PgRelationship(models.Model):
    _name = 'pg.relationship'
    _description = 'ProductGraph Engineering Relationship'
    _order = 'source_item_id, kind, target_item_id'

    source_item_id = fields.Many2one(
        'pg.item', string='Source', required=True,
        ondelete='cascade', index=True,
    )
    target_item_id = fields.Many2one(
        'pg.item', string='Target', required=True,
        ondelete='cascade', index=True,
    )
    kind = fields.Selection(
        KIND_SELECTION, string='Kind', required=True, index=True,
    )
    is_symmetric = fields.Boolean(
        compute='_compute_is_symmetric', store=True,
        help='True when the kind is treated as symmetric for traversal '
             '(substitutes / alternates).',
    )

    notes = fields.Text(string='Notes')
    active = fields.Boolean(default=True)

    # Sourced from an ECO if a future bridge writes this.
    eco_id = fields.Many2one(
        'pg.eco', string='Sourcing ECO',
        ondelete='set null', index=True,
        help='If this relationship was written by an ECO award flow, '
             'this links back to the ECO that introduced it.',
    )

    _rel_unique = models.Constraint(
        'UNIQUE(source_item_id, target_item_id, kind)',
        'A relationship of this kind already exists between these two items.',
    )

    # ──────────────────────────────────────────────────────────────────
    # Compute
    # ──────────────────────────────────────────────────────────────────
    @api.depends('kind')
    def _compute_is_symmetric(self):
        for rec in self:
            rec.is_symmetric = rec.kind in SYMMETRIC_KINDS

    # ──────────────────────────────────────────────────────────────────
    # Constraints
    # ──────────────────────────────────────────────────────────────────
    @api.constrains('source_item_id', 'target_item_id')
    def _check_no_self_loop(self):
        for rec in self:
            if rec.source_item_id and rec.target_item_id \
                    and rec.source_item_id == rec.target_item_id:
                raise ValidationError(
                    _("A relationship's source and target must be "
                      "different items (got %s on both ends).")
                    % rec.source_item_id.display_name
                )

    @api.constrains('source_item_id', 'target_item_id', 'kind')
    def _check_no_redundant_inverse_for_symmetric(self):
        """For symmetric kinds, reject (B, A, kind) when (A, B, kind) already
        exists — we only store one direction."""
        for rec in self:
            if not rec.is_symmetric:
                continue
            inverse = self.search([
                ('id', '!=', rec.id),
                ('source_item_id', '=', rec.target_item_id.id),
                ('target_item_id', '=', rec.source_item_id.id),
                ('kind', '=', rec.kind),
            ], limit=1)
            if inverse:
                raise ValidationError(
                    _("A %s relationship already exists between %s and %s "
                      "(symmetric — only one direction stored).")
                    % (rec.kind, rec.target_item_id.display_name,
                       rec.source_item_id.display_name)
                )

    # ──────────────────────────────────────────────────────────────────
    # Helpers — used by pg.item computed M2Ms
    # ──────────────────────────────────────────────────────────────────
    # ──────────────────────────────────────────────────────────────────
    # Closure maintenance — every CRUD that affects the graph triggers
    # a rebuild of the closure for the affected kinds. The closure is
    # the cheaper read path; this is the cost-amortisation.
    # ──────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        self._rebuild_closure_for_kinds(set(recs.mapped('kind')))
        return recs

    def write(self, vals):
        before = set(self.mapped('kind'))
        result = super().write(vals)
        after = set(self.mapped('kind'))
        # If the kind itself was changed, both old and new need rebuilding.
        if 'kind' in vals or 'source_item_id' in vals \
                or 'target_item_id' in vals or 'active' in vals:
            self._rebuild_closure_for_kinds(before | after)
        return result

    def unlink(self):
        kinds = set(self.mapped('kind'))
        result = super().unlink()
        self._rebuild_closure_for_kinds(kinds)
        return result

    @api.model
    def _rebuild_closure_for_kinds(self, kinds):
        if not kinds:
            return
        Closure = self.env['pg.relationship.closure'].sudo()
        for k in kinds:
            Closure._rebuild_kind(k)

    @api.model
    def get_related_items(self, item, kind):
        """Return all pg.item records related to `item` by `kind`.

        For symmetric kinds, both outgoing and incoming edges are walked.
        For directed kinds, only outgoing (source_item_id = item) by default.
        """
        if kind in SYMMETRIC_KINDS:
            out = self.search([
                ('source_item_id', '=', item.id),
                ('kind', '=', kind),
            ]).mapped('target_item_id')
            inc = self.search([
                ('target_item_id', '=', item.id),
                ('kind', '=', kind),
            ]).mapped('source_item_id')
            return out | inc
        return self.search([
            ('source_item_id', '=', item.id),
            ('kind', '=', kind),
        ]).mapped('target_item_id')

    @api.model
    def get_incoming_items(self, item, kind):
        """For directed kinds, return items that point AT `item` (predecessors)."""
        return self.search([
            ('target_item_id', '=', item.id),
            ('kind', '=', kind),
        ]).mapped('source_item_id')
