# -*- coding: utf-8 -*-
"""PG-132 — ECO Impact pre-population wizard.

Surfaced from `pg.eco.line.action_explode_impact`. Takes the closure-derived
candidate ancestors and lets the user cherry-pick which to add as additional
ECO lines (default change_kind: bom_change — these are downstream consumers).
"""
from odoo import _, api, fields, models


class PgEcoImpactWizard(models.TransientModel):
    _name = 'pg.eco.impact.wizard'
    _description = 'ECO Impact Pre-population'

    eco_id = fields.Many2one('pg.eco', required=True, ondelete='cascade')
    source_item_id = fields.Many2one('pg.item', required=True, readonly=True)
    source_line_id = fields.Many2one('pg.eco.line', readonly=True)
    candidate_item_ids = fields.Many2many(
        'pg.item',
        relation='pg_eco_impact_wizard_candidate_rel',
        readonly=True,
    )
    selected_item_ids = fields.Many2many(
        'pg.item',
        relation='pg_eco_impact_wizard_selected_rel',
        string='Add to ECO',
    )
    default_change_kind = fields.Selection(
        [('bom_change', 'BOM Change (downstream impact)'),
         ('revision_bump', 'Revision Bump')],
        default='bom_change',
        required=True,
    )

    def action_add_to_eco(self):
        self.ensure_one()
        for item in self.selected_item_ids:
            self.env['pg.eco.line'].create({
                'eco_id': self.eco_id.id,
                'item_id': item.id,
                'change_kind': self.default_change_kind,
                'notes': _(
                    "Auto-added via impact analysis from %s."
                ) % self.source_item_id.display_name,
            })
        self.eco_id.message_post(
            body=_(
                "Added %(count)d items via impact analysis of %(source)s."
            ) % {
                'count': len(self.selected_item_ids),
                'source': self.source_item_id.display_name,
            },
        )
        return {'type': 'ir.actions.act_window_close'}
