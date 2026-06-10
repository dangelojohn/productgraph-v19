# -*- coding: utf-8 -*-
"""
pg.item.tag — Free-form tagging for engineering items.

Tags are independent of category. An item can have many tags. Useful for
cross-cutting attributes that don't fit the strict category tree
(e.g. "long-lead", "regulated", "outsourced", "kit").
"""
from odoo import api, fields, models


class PgItemTag(models.Model):
    _name = 'pg.item.tag'
    _description = 'ProductGraph Item Tag'
    _order = 'name'

    name = fields.Char(string='Tag', required=True, translate=True)
    color = fields.Integer(string='Color Index')
    active = fields.Boolean(default=True)
    item_count = fields.Integer(compute='_compute_item_count', string='Items')

    _name_unique = models.Constraint(
        'UNIQUE(name)',
        'Tag name must be unique.',
    )

    def _compute_item_count(self):
        for rec in self:
            rec.item_count = self.env['pg.item'].search_count([('tag_ids', 'in', rec.id)])
