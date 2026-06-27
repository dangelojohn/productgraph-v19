# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PgRfqLine(models.Model):
    _name = 'pg.rfq.line'
    _description = 'RFQ Line'
    _order = 'rfq_id, sequence, id'

    rfq_id = fields.Many2one(
        'pg.rfq', string='RFQ',
        required=True, ondelete='cascade', index=True,
    )
    sequence = fields.Integer(default=10)

    item_id = fields.Many2one(
        'pg.item', string='Item', required=True, ondelete='restrict',
    )
    qty = fields.Float(string='Quantity', default=1.0, required=True)
    target_date = fields.Date(string='Need By')
    spec_notes = fields.Text(string='Spec Notes')

    bid_ids = fields.One2many('pg.rfq.bid', 'line_id', string='Bids')
    bid_count = fields.Integer(compute='_compute_bid_stats', store=True)
    best_unit_price = fields.Float(
        compute='_compute_bid_stats', store=True,
        help='Lowest unit_price across all bids on this line.',
    )
    awarded_bid_id = fields.Many2one(
        'pg.rfq.bid', compute='_compute_awarded',
        store=True,
    )
    # PG-133 — recommendation only. Surfaced in the UI as a "Recommended"
    # badge; selecting it remains a manual decision (Award).
    recommended_bid_id = fields.Many2one(
        'pg.rfq.bid',
        compute='_compute_recommended',
        store=True,
        string='Recommended Bid',
        help='Lowest-score (best combined unit-price / lead-time / MOQ) bid '
             'on this line. Suggestion only — the Award action still '
             'requires a human flag on the chosen bid.',
    )

    rfq_state = fields.Selection(
        related='rfq_id.state', store=True, index=True, string='RFQ State',
    )

    @api.depends('bid_ids.unit_price')
    def _compute_bid_stats(self):
        for rec in self:
            rec.bid_count = len(rec.bid_ids)
            if rec.bid_ids:
                rec.best_unit_price = min(rec.bid_ids.mapped('unit_price'))
            else:
                rec.best_unit_price = 0.0

    @api.depends('bid_ids.is_awarded')
    def _compute_awarded(self):
        for rec in self:
            rec.awarded_bid_id = rec.bid_ids.filtered('is_awarded')[:1]

    @api.depends('bid_ids.score', 'bid_ids.unit_price')
    def _compute_recommended(self):
        for rec in self:
            if not rec.bid_ids:
                rec.recommended_bid_id = False
                continue
            # Primary sort: score (lower=better). Tiebreaker: unit_price
            # (lower=better). Python's sort is stable so the tuple does
            # both passes in one shot.
            ranked = rec.bid_ids.sorted(
                lambda b: (b.score, b.unit_price)
            )
            rec.recommended_bid_id = ranked[:1]

    def unlink(self):
        for rec in self:
            if rec.rfq_state not in ('draft', 'cancelled'):
                raise UserError(
                    _("RFQ lines can only be removed while the RFQ is in "
                      "draft or cancelled state. %s is %s.")
                    % (rec.rfq_id.display_name, rec.rfq_state)
                )
        return super().unlink()
