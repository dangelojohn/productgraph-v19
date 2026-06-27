# -*- coding: utf-8 -*-
"""
pg.property.template — OpenBOM-style dynamic property templates.

A template is a named set of custom fields that can be attached to engineering
items of a given item_type. Examples:

* "Cabinet Mechanical" template (for item_type='manufactured'):
    width_mm:integer, height_mm:integer, depth_mm:integer, material:selection

* "Vendor Component" template (for item_type='purchased'):
    mpn:char, datasheet_url:char, lead_time_days:integer

* "Drawing" template (for item_type='document'):
    sheet_size:selection, scale:char, revision_letter:char

A pg.item may pick zero or more templates that match its item_type. Each
template's fields become available for value entry on the item's revisions
(values live in `pg.property.value`, defined in `product_graph_revision`).

Phase 1 limitations (Decision D2):
- No formula evaluation
- No cross-item rollup
- No conditional visibility
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


# Must mirror the selection values on pg.item.item_type
PG_ITEM_TYPES = [
    ('assembly', 'Assembly'),
    ('part', 'Manufactured Part'),
    ('purchased', 'Purchased Component'),
    ('document', 'Document'),
    ('software', 'Software'),
    ('service', 'Service'),
]


class PgPropertyTemplate(models.Model):
    _name = 'pg.property.template'
    _description = 'ProductGraph Property Template'
    _order = 'item_type, sequence, name'

    name = fields.Char(string='Template Name', required=True, translate=True)
    code = fields.Char(string='Code', index=True, help='Short stable identifier.')
    sequence = fields.Integer(default=10)

    item_type = fields.Selection(
        PG_ITEM_TYPES,
        string='Applies To Item Type',
        required=True,
        index=True,
        help='Items of this type may attach this template.',
    )

    description = fields.Text(string='Description')

    field_ids = fields.One2many(
        'pg.property.field',
        'template_id',
        string='Fields',
        copy=True,
    )
    field_count = fields.Integer(
        compute='_compute_field_count', string='# Fields', store=True,
    )

    active = fields.Boolean(default=True)

    _code_unique = models.Constraint(
        'UNIQUE(code)',
        'Property template code must be unique.',
    )

    @api.depends('field_ids')
    def _compute_field_count(self):
        for rec in self:
            rec.field_count = len(rec.field_ids)

    def name_get(self):
        return [
            (rec.id, f'{rec.name} [{dict(PG_ITEM_TYPES).get(rec.item_type, "?")}]')
            for rec in self
        ]
