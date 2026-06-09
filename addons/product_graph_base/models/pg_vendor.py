# -*- coding: utf-8 -*-
"""
pg.vendor — Phase 2 stub.

Reserves the schema path for the future Approved Vendor List / Approved
Manufacturer List feature (Architecture Bible §6.1). Phase 1 ships the model
only — no views, no menus, no procurement hooks. (Decision D3.)

Once Phase 2 lands as `product_graph_supplier`, this stub will be replaced
or extended with full vendor governance, RFQ, and procurement integration.
"""
from odoo import fields, models


class PgVendor(models.Model):
    _name = 'pg.vendor'
    _description = 'ProductGraph Approved Vendor (Phase 2 stub)'
    _order = 'name'

    name = fields.Char(string='Vendor Name', required=True, index=True)
    code = fields.Char(string='Vendor Code', index=True)
    partner_id = fields.Many2one(
        'res.partner',
        string='Linked Partner',
        ondelete='restrict',
        index=True,
        help='Optional link to a res.partner. Phase 2 will populate this.',
    )
    is_approved = fields.Boolean(string='Approved', default=False)
    notes = fields.Text(string='Notes')
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_unique', 'UNIQUE(code)', 'Vendor code must be unique.'),
    ]
