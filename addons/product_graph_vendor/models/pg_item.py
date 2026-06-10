# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PgItem(models.Model):
    _inherit = 'pg.item'

    vendor_part_ids = fields.One2many(
        'pg.vendor.part', 'item_id',
        string='Approved Sources',
        help='AVL/AML — the qualified vendors authorized to supply this item.',
    )
    vendor_part_count = fields.Integer(compute='_compute_vendor_part_count')
    primary_vendor_part_id = fields.Many2one(
        'pg.vendor.part',
        string='Primary Source',
        compute='_compute_primary_vendor_part',
        store=True,
        help='The preferred-flagged source, falling back to the lowest-rank '
             'qualified vendor-part.',
    )
    primary_vendor_id = fields.Many2one(
        'pg.vendor',
        string='Primary Vendor',
        related='primary_vendor_part_id.vendor_id',
        store=True,
    )

    @api.depends('vendor_part_ids', 'vendor_part_ids.active')
    def _compute_vendor_part_count(self):
        for rec in self:
            rec.vendor_part_count = len(rec.vendor_part_ids.filtered('active'))

    @api.depends(
        'vendor_part_ids.is_preferred',
        'vendor_part_ids.rank',
        'vendor_part_ids.is_qualified',
        'vendor_part_ids.active',
    )
    def _compute_primary_vendor_part(self):
        for rec in self:
            parts = rec.vendor_part_ids.filtered('active')
            preferred = parts.filtered('is_preferred')
            if preferred:
                rec.primary_vendor_part_id = preferred[:1]
                continue
            qualified = parts.filtered('is_qualified').sorted('rank')
            rec.primary_vendor_part_id = qualified[:1] or False

    def action_view_vendor_parts(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Approved Sources for {self.display_name}',
            'res_model': 'pg.vendor.part',
            'view_mode': 'list,form',
            'domain': [('item_id', '=', self.id)],
            'context': {'default_item_id': self.id},
        }
