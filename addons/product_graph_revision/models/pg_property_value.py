# -*- coding: utf-8 -*-
"""
pg.property.value — A typed value for one property field on one revision.

Each row represents the value of a single ``pg.property.field`` on a single
``pg.revision``. The DB constraint ``(revision_id, field_id)`` enforces "at
most one value per field per revision".

The typed slots (value_char/integer/float/boolean/date/selection) keep the
storage normalized — at write time the value is dispatched into the column
matching the field's ``field_type``. ``display_value`` reads back from the
right slot for display.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class PgPropertyValue(models.Model):
    _name = 'pg.property.value'
    _description = 'ProductGraph Property Value'
    _order = 'revision_id, field_id'
    _rec_name = 'display_value'

    revision_id = fields.Many2one(
        'pg.revision',
        string='Revision',
        required=True,
        ondelete='cascade',
        index=True,
    )
    field_id = fields.Many2one(
        'pg.property.field',
        string='Property Field',
        required=True,
        ondelete='restrict',
        index=True,
    )

    # Convenience related fields (for views / search)
    field_label = fields.Char(related='field_id.label', store=True, readonly=True)
    field_key = fields.Char(related='field_id.key', store=True, readonly=True)
    field_type = fields.Selection(related='field_id.field_type', store=True, readonly=True)
    field_uom_id = fields.Many2one(related='field_id.uom_id', readonly=True)

    # Typed slots — exactly one is meaningful at a time, chosen by field_type.
    value_char = fields.Char(string='Text Value')
    value_integer = fields.Integer(string='Integer Value')
    value_float = fields.Float(string='Decimal Value', digits='Product Unit of Measure')
    value_boolean = fields.Boolean(string='Boolean Value')
    value_date = fields.Date(string='Date Value')
    value_selection = fields.Char(string='Selection Value')

    display_value = fields.Char(
        string='Value',
        compute='_compute_display_value',
        store=True,
    )

    _sql_constraints = [
        ('revision_field_unique', 'UNIQUE(revision_id, field_id)',
         'A property field may have at most one value per revision.'),
    ]

    # ═════════════════════════════════════════════════════════════════════════
    # Computes & validation
    # ═════════════════════════════════════════════════════════════════════════

    @api.depends('field_type', 'value_char', 'value_integer', 'value_float',
                 'value_boolean', 'value_date', 'value_selection')
    def _compute_display_value(self):
        for rec in self:
            ftype = rec.field_type
            if ftype == 'char':
                rec.display_value = rec.value_char or ''
            elif ftype == 'integer':
                rec.display_value = str(rec.value_integer) if rec.value_integer is not False else ''
            elif ftype == 'float':
                rec.display_value = f'{rec.value_float:g}' if rec.value_float else ''
            elif ftype == 'boolean':
                rec.display_value = _('Yes') if rec.value_boolean else _('No')
            elif ftype == 'date':
                rec.display_value = str(rec.value_date) if rec.value_date else ''
            elif ftype == 'selection':
                rec.display_value = rec.value_selection or ''
            else:
                rec.display_value = ''

    @api.constrains('field_id', 'value_selection')
    def _check_selection_in_options(self):
        for rec in self:
            if rec.field_type == 'selection' and rec.value_selection:
                valid = [v for v, _label in rec.field_id.get_selection_choices()]
                if rec.value_selection not in valid:
                    raise ValidationError(_(
                        "Value %(val)r is not in the allowed options for field "
                        "%(label)s. Allowed: %(allowed)s",
                        val=rec.value_selection,
                        label=rec.field_id.label,
                        allowed=', '.join(valid),
                    ))

    @api.constrains('field_id', 'revision_id')
    def _check_template_belongs_to_item(self):
        for rec in self:
            if not rec.revision_id or not rec.field_id:
                continue
            tmpl = rec.field_id.template_id
            if tmpl not in rec.revision_id.item_id.property_template_ids:
                raise ValidationError(_(
                    "Property template %(tmpl)s is not attached to item "
                    "%(item)s. Attach the template to the item first.",
                    tmpl=tmpl.name,
                    item=rec.revision_id.item_id.display_name,
                ))

    # ═════════════════════════════════════════════════════════════════════════
    # ORM overrides — released revisions are immutable, including their values
    # ═════════════════════════════════════════════════════════════════════════

    def _check_revision_writable(self):
        for rec in self:
            if rec.revision_id.state in ('released', 'superseded', 'obsolete') \
                    and not self.env.context.get('_pg_release_bypass'):
                raise UserError(_(
                    "Cannot edit property values on revision %(name)s — "
                    "revision is %(state)s.",
                    name=rec.revision_id.display_name,
                    state=rec.revision_id.state,
                ))

    def write(self, vals):
        self._check_revision_writable()
        return super().write(vals)

    def unlink(self):
        self._check_revision_writable()
        return super().unlink()
