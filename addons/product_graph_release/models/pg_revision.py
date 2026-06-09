# -*- coding: utf-8 -*-
"""Extend pg.revision with the release_ids One2many for the smart button."""
from odoo import api, fields, models


class PgRevision(models.Model):
    _inherit = 'pg.revision'

    release_ids = fields.One2many(
        'pg.release',
        'revision_id',
        string='MRP Releases',
    )
    release_count = fields.Integer(compute='_compute_release_count', string='# Releases')

    @api.depends('release_ids')
    def _compute_release_count(self):
        for rec in self:
            rec.release_count = len(rec.release_ids)
