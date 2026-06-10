# -*- coding: utf-8 -*-
"""
pg.vendor.part — Phase 2 stub.

Cross-reference between a pg.item and a pg.vendor (with vendor-specific part
number, MOQ, price hint, etc.). Phase 1 ships the model only (Decision D3).
"""
from odoo import fields, models


class PgVendorPart(models.Model):
    _name = 'pg.vendor.part'
    _description = 'ProductGraph Vendor Part Cross-Reference (Phase 2 stub)'
    _order = 'item_id, vendor_id'

    item_id = fields.Many2one(
        'pg.item',
        string='Engineering Item',
        required=True,
        ondelete='cascade',
        index=True,
    )
    vendor_id = fields.Many2one(
        'pg.vendor',
        string='Vendor',
        required=True,
        ondelete='restrict',
        index=True,
    )
    vendor_part_number = fields.Char(string='Vendor Part Number', index=True)
    is_preferred = fields.Boolean(string='Preferred Source', default=False)

    # Optional pricing hint (Phase 2 will replace with full vendor pricelist)
    moq = fields.Float(string='Minimum Order Quantity', default=1.0)
    unit_price_hint = fields.Float(string='Unit Price Hint')
    currency_id = fields.Many2one('res.currency', string='Currency')
    lead_time_days = fields.Integer(string='Lead Time (days)')

    notes = fields.Text(string='Notes')
    active = fields.Boolean(default=True)

    _item_vendor_unique = models.Constraint(
        'UNIQUE(item_id, vendor_id, vendor_part_number)',
        'Each (item, vendor, part number) tuple must be unique.',
    )
