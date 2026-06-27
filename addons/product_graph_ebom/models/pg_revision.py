# -*- coding: utf-8 -*-
"""Extend pg.revision with the ebom_ids One2many for the smart button."""
from odoo import api, fields, models


class PgRevision(models.Model):
    _inherit = 'pg.revision'

    ebom_ids = fields.One2many('pg.ebom', 'revision_id', string='EBOMs')
    ebom_count = fields.Integer(
        compute='_compute_ebom_count',
        string='# EBOMs',
        store=True,
    )

    @api.depends('ebom_ids')
    def _compute_ebom_count(self):
        for rec in self:
            rec.ebom_count = len(rec.ebom_ids)
