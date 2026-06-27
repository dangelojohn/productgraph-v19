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

    # PG-133 — combined rank score (lower = better). Computed per-line so
    # the relative ranking is local to the bids competing for the same line.
    # 50% unit price, 30% lead time, 20% MOQ. Recommendation only — does
    # NOT set is_awarded; the human Award action is the only path to that.
    score = fields.Float(
        string='Combined Score',
        compute='_compute_score',
        store=True,
        digits=(8, 4),
        help='Lower is better. Weighted blend of normalized unit price '
             '(50%), lead time (30%), and MOQ (20%). Recommendation only — '
             'the explicit Award action remains the only path to is_awarded.',
    )

    @api.depends(
        'unit_price', 'lead_time_days', 'moq',
        'line_id', 'line_id.bid_ids',
        'line_id.bid_ids.unit_price',
        'line_id.bid_ids.lead_time_days',
        'line_id.bid_ids.moq',
    )
    def _compute_score(self):
        for bid in self:
            siblings = bid.line_id.bid_ids if bid.line_id else bid
            if not siblings:
                bid.score = 0.0
                continue
            bid.score = self._score_for(bid, siblings)

    @api.model
    def _score_for(self, bid, siblings):
        """Compute the 0..1 combined score for ``bid`` relative to ``siblings``.

        Each factor is min-max normalized within siblings (0 = best in set,
        1 = worst). A degenerate dimension (all bids tie) contributes 0.
        Single-bid case → score is 0.0 (winner by default).
        """
        weights = {'price': 0.5, 'lead': 0.3, 'moq': 0.2}

        def _norm(value, vmin, vmax):
            if vmax == vmin:
                return 0.0
            return (value - vmin) / (vmax - vmin)

        prices = siblings.mapped('unit_price')
        leads = siblings.mapped('lead_time_days')
        moqs = siblings.mapped('moq')
        return (
            weights['price'] * _norm(bid.unit_price, min(prices), max(prices))
            + weights['lead'] * _norm(bid.lead_time_days, min(leads), max(leads))
            + weights['moq'] * _norm(bid.moq, min(moqs), max(moqs))
        )

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

    # Fields the ORM's computed-store machinery writes back without user
    # intent. Allowing them through the immutability guard prevents recompute
    # storms on awarded RFQs from raising UserError.
    _COMPUTE_ONLY_FIELDS = frozenset({'score'})

    def write(self, vals):
        editable = self._editable_states()
        compute_only = set(vals.keys()) <= self._COMPUTE_ONLY_FIELDS
        for rec in self:
            if rec.rfq_state and rec.rfq_state not in editable:
                if rec.rfq_state == 'awarded' and not compute_only:
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
