# -*- coding: utf-8 -*-
"""
pg.release.line — Per-line traceability from pg.ebom.line to mrp.bom.line.

One row per EBOM line that was released. Captures the source EBOM line, the
source pg.item, the linked product, and the generated mrp.bom.line. Read-only
after the parent release completes.
"""
from odoo import fields, models, _
from odoo.exceptions import UserError


class PgReleaseLine(models.Model):
    _name = 'pg.release.line'
    _description = 'ProductGraph Release Line'
    _order = 'release_id, sequence, id'

    release_id = fields.Many2one(
        'pg.release',
        string='Release',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(default=10)

    pg_ebom_line_id = fields.Many2one(
        'pg.ebom.line',
        string='Source EBOM Line',
        ondelete='set null',
        readonly=True,
    )
    pg_item_id = fields.Many2one(
        'pg.item',
        string='Item',
        ondelete='restrict',
        readonly=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product',
        readonly=True,
    )
    product_qty = fields.Float(string='Qty', readonly=True)

    mrp_bom_line_id = fields.Many2one(
        'mrp.bom.line',
        string='Generated mrp.bom.line',
        readonly=True,
    )

    notes = fields.Char(string='Notes', readonly=True)

    def write(self, vals):
        for rec in self:
            if rec.release_id.state == 'completed':
                raise UserError(_(
                    "Release lines on completed release %s are immutable.",
                    rec.release_id.name,
                ))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.release_id.state == 'completed':
                raise UserError(_(
                    "Release lines on completed release %s cannot be deleted.",
                    rec.release_id.name,
                ))
        return super().unlink()
