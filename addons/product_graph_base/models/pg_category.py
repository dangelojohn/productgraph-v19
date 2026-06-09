# -*- coding: utf-8 -*-
"""
pg.category — Hierarchical engineering category taxonomy.

Independent of `product.category` — engineering categorization need not match
sales/inventory categorization. Items always belong to exactly one category.
"""
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PgCategory(models.Model):
    _name = 'pg.category'
    _description = 'ProductGraph Engineering Category'
    _parent_name = 'parent_id'
    _parent_store = True
    _order = 'complete_name'
    _rec_name = 'complete_name'

    name = fields.Char(string='Category Name', required=True, translate=True)
    complete_name = fields.Char(
        string='Full Path',
        compute='_compute_complete_name',
        store=True,
        recursive=True,
    )
    parent_id = fields.Many2one(
        'pg.category',
        string='Parent Category',
        ondelete='cascade',
        index=True,
    )
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many('pg.category', 'parent_id', string='Sub-categories')

    code = fields.Char(string='Code', size=10, index=True)
    description = fields.Text(string='Description')
    active = fields.Boolean(default=True)
    color = fields.Integer(string='Color Index')
    item_count = fields.Integer(compute='_compute_item_count', string='Items')

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
    def _check_no_recursion(self):
        # parent_store keeps a path; we use it for the cheap cycle check.
        for rec in self:
            seen = set()
            node = rec
            while node:
                if node.id in seen:
                    raise ValidationError(_('A category cannot be its own ancestor.'))
                seen.add(node.id)
                node = node.parent_id
