# -*- coding: utf-8 -*-
"""
pg.category — ProductGraph Engineering Category
pg.item.tag  — Tag taxonomy for items
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PgCategory(models.Model):
    _name = 'pg.category'
    _description = 'ProductGraph Engineering Category'
    _parent_name = 'parent_id'
    _parent_store = True
    _order = 'complete_name'
    _rec_name = 'complete_name'

    name = fields.Char('Category Name', required=True, translate=True)
    complete_name = fields.Char(
        'Full Path',
        compute='_compute_complete_name',
        store=True,
    )
    parent_id = fields.Many2one(
        'pg.category',
        string='Parent Category',
        ondelete='cascade',
        index=True,
    )
    parent_path = fields.Char(index=True, unaccent=False)
    child_ids = fields.One2many('pg.category', 'parent_id', string='Sub-categories')
    description = fields.Text('Description')
    code = fields.Char('Code', size=10, index=True)
    active = fields.Boolean(default=True)
    item_count = fields.Integer(compute='_compute_item_count', string='Items')
    color = fields.Integer('Color Index')

    _sql_constraints = [
        ('code_unique', 'UNIQUE(code)', 'Category code must be unique.'),
    ]

    @api.depends('name', 'parent_id.complete_name')
    def _compute_complete_name(self):
        for rec in self:
            if rec.parent_id:
                rec.complete_name = f'{rec.parent_id.complete_name} / {rec.name}'
            else:
                rec.complete_name = rec.name

    def _compute_item_count(self):
        for rec in self:
            rec.item_count = self.env['pg.item'].search_count([
                ('category_id', 'child_of', rec.id)
            ])

    @api.constrains('parent_id')
    def _check_category_recursion(self):
        if not self._check_recursion():
            raise UserError(_('A category cannot be its own parent.'))

    def name_get(self):
        result = []
        for rec in self:
            result.append((rec.id, rec.complete_name))
        return result


class PgItemTag(models.Model):
    _name = 'pg.item.tag'
    _description = 'ProductGraph Item Tag'
    _order = 'name'

    name = fields.Char('Tag', required=True, translate=True)
    color = fields.Integer('Color Index')
    active = fields.Boolean(default=True)
    item_count = fields.Integer(compute='_compute_item_count', string='Items')

    _sql_constraints = [
        ('name_unique', 'UNIQUE(name)', 'Tag name must be unique.'),
    ]

    def _compute_item_count(self):
        for rec in self:
            rec.item_count = self.env['pg.item'].search_count([
                ('tag_ids', 'in', rec.id)
            ])
