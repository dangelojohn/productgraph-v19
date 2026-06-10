# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PgItem(models.Model):
    _inherit = 'pg.item'

    # The full relationship rows where this item is the source.
    outgoing_relationship_ids = fields.One2many(
        'pg.relationship', 'source_item_id',
        string='Outgoing Relationships',
    )
    incoming_relationship_ids = fields.One2many(
        'pg.relationship', 'target_item_id',
        string='Incoming Relationships',
    )

    # Per-kind computed many2many views. All `compute_sudo=True` because the
    # relationship rows themselves are not user-private and we want the
    # graph to be visible to any pg.* viewer even when they only have
    # read on pg.item.
    substitute_ids = fields.Many2many(
        'pg.item',
        compute='_compute_relationship_helpers',
        compute_sudo=True,
        string='Substitutes',
        help='Items flagged as drop-in substitutes (symmetric).',
    )
    alternate_ids = fields.Many2many(
        'pg.item',
        compute='_compute_relationship_helpers',
        compute_sudo=True,
        string='Alternates',
        help='Items interchangeable within an operating envelope (symmetric).',
    )
    supersedes_ids = fields.Many2many(
        'pg.item',
        compute='_compute_relationship_helpers',
        compute_sudo=True,
        string='Supersedes',
        help='Predecessor items — this item supersedes them.',
    )
    superseded_by_ids = fields.Many2many(
        'pg.item',
        compute='_compute_relationship_helpers',
        compute_sudo=True,
        string='Superseded By',
        help='Successor items — this item is superseded by them.',
    )
    reference_ids = fields.Many2many(
        'pg.item',
        compute='_compute_relationship_helpers',
        compute_sudo=True,
        string='References',
        help='Items this item references (directed).',
    )
    related_to_ids = fields.Many2many(
        'pg.item',
        compute='_compute_relationship_helpers',
        compute_sudo=True,
        string='Related To',
        help='Free-form related items (outgoing edges).',
    )

    @api.depends(
        'outgoing_relationship_ids', 'outgoing_relationship_ids.kind',
        'outgoing_relationship_ids.target_item_id',
        'incoming_relationship_ids', 'incoming_relationship_ids.kind',
        'incoming_relationship_ids.source_item_id',
    )
    def _compute_relationship_helpers(self):
        Rel = self.env['pg.relationship']
        for rec in self:
            rec.substitute_ids = Rel.get_related_items(rec, 'substitutes')
            rec.alternate_ids = Rel.get_related_items(rec, 'alternates')
            rec.supersedes_ids = Rel.get_related_items(rec, 'supersedes')
            rec.superseded_by_ids = Rel.get_incoming_items(rec, 'supersedes')
            rec.reference_ids = Rel.get_related_items(rec, 'references')
            rec.related_to_ids = Rel.get_related_items(rec, 'related')

    def action_view_relationships(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Relationships of {self.display_name}',
            'res_model': 'pg.relationship',
            'view_mode': 'list,form',
            'domain': [
                '|',
                ('source_item_id', '=', self.id),
                ('target_item_id', '=', self.id),
            ],
            'context': {'default_source_item_id': self.id},
        }
