# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PgVendorPart(models.Model):
    _name = 'pg.vendor.part'
    _inherit = 'pg.vendor.part'

    rank = fields.Integer(
        string='Source Rank',
        default=10,
        help='Lower rank = preferred. The primary source on an item is the '
             'enabled vendor-part with the lowest rank.',
    )
    is_qualified = fields.Boolean(
        related='vendor_id.is_approved',
        store=True,
        string='Vendor Qualified',
    )
    vendor_state = fields.Selection(
        related='vendor_id.state', store=True, string='Vendor State',
    )

    display_name = fields.Char(
        compute='_compute_display_name', store=True,
    )

    @api.depends('item_id.part_number', 'vendor_id.name', 'vendor_part_number')
    def _compute_display_name(self):
        for rec in self:
            item_pn = rec.item_id.part_number or rec.item_id.display_name or ''
            vendor = rec.vendor_id.name or ''
            vpn = rec.vendor_part_number or '—'
            rec.display_name = f'{item_pn} @ {vendor} ({vpn})'

    @api.constrains('vendor_id', 'is_preferred', 'item_id')
    def _check_one_preferred_per_item(self):
        for rec in self:
            if not rec.is_preferred:
                continue
            other_preferred = self.search([
                ('item_id', '=', rec.item_id.id),
                ('is_preferred', '=', True),
                ('id', '!=', rec.id),
            ], limit=1)
            if other_preferred:
                raise ValidationError(
                    _("Item %s already has another preferred source (%s). "
                      "Only one preferred vendor-part per item is allowed.")
                    % (rec.item_id.display_name,
                       other_preferred.vendor_id.name)
                )
