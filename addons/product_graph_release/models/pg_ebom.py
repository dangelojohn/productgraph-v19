# -*- coding: utf-8 -*-
"""Extend pg.ebom with the release_ids One2many for the smart button."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PgEbom(models.Model):
    _inherit = 'pg.ebom'

    release_ids = fields.One2many(
        'pg.release',
        'ebom_id',
        string='MRP Releases',
    )
    release_count = fields.Integer(compute='_compute_release_count', string='# Releases')

    @api.depends('release_ids')
    def _compute_release_count(self):
        for rec in self:
            rec.release_count = len(rec.release_ids)

    def action_open_release_wizard(self):
        """Quick-launch the release wizard from an EBOM."""
        self.ensure_one()
        if self.state != 'released':
            raise UserError(_("Only Released EBOMs can be released to MRP."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Release to MRP'),
            'res_model': 'pg.release.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_ebom_id': self.id},
        }
