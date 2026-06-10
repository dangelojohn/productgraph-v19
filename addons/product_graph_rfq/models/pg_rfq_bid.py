# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PgRfqBid(models.Model):
    _name = 'pg.rfq.bid'
    _description = 'RFQ Vendor Bid'
    _order = 'line_id, unit_price'

    line_id = fields.Many2one(
        'pg.rfq.line', string='RFQ Line',
        required=True, ondelete='cascade', index=True,
    )
    rfq_id = fields.Many2one(
        related='line_id.rfq_id', store=True, index=True, string='RFQ',
    )
    rfq_state = fields.Selection(
        related='line_id.rfq_id.state', store=True, string='RFQ State',
    )

    vendor_id = fields.Many2one(
        'pg.vendor', string='Vendor',
        required=True, ondelete='restrict', index=True,
    )
    vendor_part_number = fields.Char(string='Vendor Part Number')
    unit_price = fields.Float(string='Unit Price', required=True)
    currency_id = fields.Many2one(
        'res.currency', string='Currency',
        default=lambda self: self.env.company.currency_id,
    )
    moq = fields.Float(string='Min Order Qty', default=1.0)
    lead_time_days = fields.Integer(string='Lead Time (days)')
    valid_until = fields.Date(string='Quote Valid Until')

    is_awarded = fields.Boolean(string='Awarded', default=False)
    comment = fields.Text(string='Comment')

    # ──────────────────────────────────────────────────────────────────
    # Constraints
    # ──────────────────────────────────────────────────────────────────
    @api.constrains('is_awarded', 'line_id')
    def _check_one_awarded_per_line(self):
        for rec in self:
            if not rec.is_awarded:
                continue
            other = self.search([
                ('line_id', '=', rec.line_id.id),
                ('is_awarded', '=', True),
                ('id', '!=', rec.id),
            ], limit=1)
            if other:
                raise ValidationError(
                    _("Line for %s already has an awarded bid (from %s). "
                      "Only one awarded bid per line is allowed.")
                    % (rec.line_id.item_id.display_name,
                       other.vendor_id.name)
                )

    @api.constrains('unit_price')
    def _check_unit_price_positive(self):
        for rec in self:
            if rec.unit_price < 0:
                raise ValidationError(_("Unit price cannot be negative."))

    # ──────────────────────────────────────────────────────────────────
    # Write/unlink guards
    # ──────────────────────────────────────────────────────────────────
    def _editable_states(self):
        return ('sent', 'received')

    def write(self, vals):
        editable = self._editable_states()
        for rec in self:
            if rec.rfq_state and rec.rfq_state not in editable:
                if rec.rfq_state == 'awarded':
                    # Allow flipping is_awarded? No — awarded RFQs are immutable.
                    raise UserError(
                        _("RFQ %s is awarded — bids are read-only.")
                        % rec.rfq_id.display_name
                    )
                # draft / cancelled — also editable to allow flexible setup
                # and post-hoc bookkeeping on cancelled RFQs.
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.rfq_state == 'awarded':
                raise UserError(
                    _("Bids on awarded RFQ %s cannot be deleted.")
                    % rec.rfq_id.display_name
                )
        return super().unlink()
