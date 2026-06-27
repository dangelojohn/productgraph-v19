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
    # W046/W058 (2026-06-27, R6.11) — list-view affordances.
    # has_recommendation is a thin stored boolean derived from
    # recommended_bid_id; it gives the list view a clean field to bind
    # both the row decoration-info and badge-widget visibility against
    # without making the recommendation domain-searchable.
    has_recommendation = fields.Boolean(
        compute='_compute_has_recommendation',
        store=True,
        string='Has Recommendation',
        help='True when the auto-ranker selected a recommended bid for '
             'this line. Used to drive the Recommended badge + row '
             'highlight in the list view.',
    )

    # W059 (R6.9, 2026-06-27) — MOQ shortfall warning.
    # Anchored on `recommended_bid_id.moq` because that's the bid the
    # buyer is on the cusp of awarding; if it carries an MOQ above
    # `qty`, the order won't actually clear with that vendor. We do not
    # compute shortfall against EVERY bid because the buyer hasn't
    # committed to those yet — recompute on Award changes if the
    # recommendation shifts to a different vendor.
    #
    # `moq_shortfall_accepted` lets the buyer acknowledge the gap
    # explicitly so the red banner mutes. The two together model the
    # "warn, don't block" UX the brief requires.
    moq_shortfall = fields.Boolean(
        string='MOQ Shortfall',
        compute='_compute_moq_shortfall',
        store=True,
        help='True when the recommended bid has a Minimum Order Quantity '
             'higher than this line qty. Surfaces a warning banner in '
             'the form; buyer either bumps qty or sets '
             'moq_shortfall_accepted.',
    )
    moq_shortfall_amount = fields.Float(
        string='MOQ Shortfall Qty',
        compute='_compute_moq_shortfall',
        store=True,
        help='moq - qty when shortfall is True, else 0. Drives the '
             'banner text "MOQ 100, requesting 20".',
    )
    moq_shortfall_accepted = fields.Boolean(
        string='MOQ Shortfall Accepted',
        default=False,
        copy=False,
        help='Buyer flag — when True the MOQ-shortfall banner stops '
             'shouting. Use this when you knowingly accept the overage '
             '(e.g. you intend to stock the surplus or split the qty '
             'across multiple lines).',
    )

    @api.depends('recommended_bid_id')
    def _compute_has_recommendation(self):
        for rec in self:
            rec.has_recommendation = bool(rec.recommended_bid_id)

    @api.depends('qty', 'recommended_bid_id', 'recommended_bid_id.moq')
    def _compute_moq_shortfall(self):
        for rec in self:
            bid = rec.recommended_bid_id
            moq = bid.moq if bid else 0.0
            if bid and moq and rec.qty < moq:
                rec.moq_shortfall = True
                rec.moq_shortfall_amount = moq - rec.qty
            else:
                rec.moq_shortfall = False
                rec.moq_shortfall_amount = 0.0

    def action_bump_qty_to_moq(self):
        """Convenience action — bump qty up to the recommended bid's MOQ.
        Idempotent; clears `moq_shortfall_accepted` since the gap is gone."""
        for rec in self:
            bid = rec.recommended_bid_id
            if not bid or not bid.moq or rec.qty >= bid.moq:
                continue
            rec.write({
                'qty': bid.moq,
                'moq_shortfall_accepted': False,
            })
        return True

    def action_accept_moq_shortfall(self):
        """Buyer's 'I know, ship it anyway' click — flags the line as
        knowingly under-MOQ so the banner stops shouting."""
        self.write({'moq_shortfall_accepted': True})
        return True

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

    @api.depends('bid_ids.score', 'bid_ids.unit_price', 'bid_ids.is_expired')
    def _compute_recommended(self):
        for rec in self:
            # W032 (R6.7) — drop expired bids from the recommendation pool.
            # The bid record stays for audit; it just can't be the suggested
            # winner once the quote went stale.
            candidates = rec.bid_ids.filtered(lambda b: not b.is_expired)
            if not candidates:
                rec.recommended_bid_id = False
                continue
            # Primary sort: score (lower=better). Tiebreaker: unit_price
            # (lower=better). Python's sort is stable so the tuple does
            # both passes in one shot.
            ranked = candidates.sorted(
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
