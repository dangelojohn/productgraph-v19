# -*- coding: utf-8 -*-
"""Extend pg.item with where-used helper using the materialized closure."""
from odoo import api, fields, models, _


class PgItem(models.Model):
    _inherit = 'pg.item'

    where_used_count = fields.Integer(
        compute='_compute_where_used_count',
        string='# Parent BOMs',
    )

    def _compute_where_used_count(self):
        Closure = self.env['pg.ebom.closure']
        for rec in self:
            rec.where_used_count = Closure.search_count([
                ('descendant_id', '=', rec.id),
            ])

    def action_view_where_used(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Where Used — %s') % self.display_name,
            'res_model': 'pg.ebom.closure',
            'view_mode': 'list',
            'domain': [('descendant_id', '=', self.id)],
        }
