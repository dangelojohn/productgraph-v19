# -*- coding: utf-8 -*-
"""Smart-button: Procurement History on pg.item.

Counts purchase.order.line records back-pointing at this item via the new
``pg_item_id`` field (W003). Non-stored compute so the form just reads it
at open time — saves a write-path on every PO line create.
"""
from odoo import _, api, fields, models


class PgItem(models.Model):
    _inherit = 'pg.item'

    purchase_line_count = fields.Integer(
        string='Procurement History',
        compute='_compute_purchase_line_count',
        store=False,
        help='Number of purchase.order.line records traceable back to this '
             'item via the W003 pg_item_id back-reference.',
    )

    @api.depends_context('uid')
    def _compute_purchase_line_count(self):
        # Defensive: search runs as the form-opening user, so honour rules.
        POL = self.env['purchase.order.line']
        for rec in self:
            if not rec.id:
                rec.purchase_line_count = 0
                continue
            rec.purchase_line_count = POL.search_count([
                ('pg_item_id', '=', rec.id),
            ])

    def action_view_purchase_lines(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Procurement History — %s') % self.display_name,
            'res_model': 'purchase.order.line',
            'view_mode': 'list,form',
            'domain': [('pg_item_id', '=', self.id)],
            'context': {'search_default_pg_item_id': self.id},
        }
