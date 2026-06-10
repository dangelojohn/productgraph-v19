# -*- coding: utf-8 -*-
"""
pg.property.field — A single typed property within a `pg.property.template`.

Field types are kept narrow on purpose (Phase 1 — Decision D2). To add a new
type, you must extend both this model's selection AND `pg.property.value`'s
typed columns.

Field key uniqueness: (template_id, key) is unique. The key is the
machine-readable handle; the label is the human-facing display string.
"""
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


FIELD_TYPES = [
    ('char', 'Text'),
    ('integer', 'Integer'),
    ('float', 'Decimal'),
    ('boolean', 'Yes / No'),
    ('date', 'Date'),
    ('selection', 'Selection'),
]


class PgPropertyField(models.Model):
    _name = 'pg.property.field'
    _description = 'ProductGraph Property Field'
    _order = 'template_id, sequence, label'

    template_id = fields.Many2one(
        'pg.property.template',
        string='Template',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(default=10)

    key = fields.Char(
        string='Key',
        required=True,
        help='Stable machine-readable identifier (snake_case). Used by the API '
             'and MCP server. Examples: width_mm, mpn, lead_time_days.',
    )
    label = fields.Char(
        string='Label',
        required=True,
        translate=True,
        help='Human-readable label shown in forms and reports.',
    )
    field_type = fields.Selection(
        FIELD_TYPES,
        string='Type',
        required=True,
        default='char',
    )
    is_required = fields.Boolean(string='Required', default=False)
    help_text = fields.Char(string='Help', translate=True)
    default_value = fields.Char(string='Default Value')

    # Selection-type only
    selection_options = fields.Char(
        string='Selection Options',
        help='Comma-separated list of valid values when type is Selection. '
             'Example: "S,M,L,XL"',
    )

    # Numeric-type only
    uom_id = fields.Many2one(
        'uom.uom',
        string='Unit of Measure',
        help='Optional unit hint for numeric fields. Display-only in Phase 1.',
    )

    _template_key_unique = models.Constraint(
        'UNIQUE(template_id, key)',
        'Property field key must be unique within a template.',
    )

    @api.constrains('key')
    def _check_key_format(self):
        for rec in self:
            if not rec.key:
                continue
            if not rec.key.replace('_', '').isalnum():
                raise ValidationError(_(
                    "Property field key %r must be alphanumeric / underscore only.",
                    rec.key,
                ))
            if rec.key[0].isdigit():
                raise ValidationError(_(
                    "Property field key %r cannot start with a digit.",
                    rec.key,
                ))

    @api.constrains('field_type', 'selection_options')
    def _check_selection_has_options(self):
        for rec in self:
            if rec.field_type == 'selection' and not rec.selection_options:
                raise ValidationError(_(
                    "Selection field %r requires selection_options.",
                    rec.label or rec.key,
                ))

    def get_selection_choices(self):
        """Return list of (value, label) tuples for selection fields."""
        self.ensure_one()
        if self.field_type != 'selection':
            return []
        return [
            (opt.strip(), opt.strip())
            for opt in (self.selection_options or '').split(',')
            if opt.strip()
        ]
