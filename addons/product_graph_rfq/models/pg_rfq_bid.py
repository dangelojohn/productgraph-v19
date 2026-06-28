# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PgRfqBid(models.Model):
    _name = 'pg.rfq.bid'
    _description = 'RFQ Vendor Bid'
    _inherit = ['portal.mixin']
    _order = 'line_id, unit_price'

    # ──────────────────────────────────────────────────────────────────
    # W076 (R6.12, 2026-06-27) — vendor portal stub
    # ──────────────────────────────────────────────────────────────────
    # The bid is the unit of self-service. A vendor's res.users sees ONE
    # row per (rfq, line) under /my/rfq-bids and clicks through to
    # /my/rfq-bids/<id> to fill in the quote. portal.mixin provides
    # `access_token` + `access_url` + helpers; we override _compute_access_url
    # so the buyer's email can deep-link to the bid form with a signed
    # token. The token gates the submission POST as a second factor
    # (login + token + vendor.partner_id all required).

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
    # W060 (R6.10, 2026-06-27) — dual-source / split award support.
    # `awarded_qty` lets a single line be split across multiple winning
    # bids (e.g. 60/40 between two vendors for risk diversification).
    # Default 0; when is_awarded flips True without an explicit qty the
    # canonical single-source path mirrors the parent line.qty onto this
    # field at award time (see pg.rfq.action_award). The pg.rfq.line
    # constraint ensures the sum of awarded_qty across bids does not
    # exceed the line.qty, and that every is_awarded bid carries a
    # positive awarded_qty.
    awarded_qty = fields.Float(
        string='Awarded Qty',
        default=0.0,
        help='Quantity awarded to THIS vendor on this RFQ line. The sum '
             'of awarded_qty across all bids on the line may not exceed '
             'line.qty. Default 0 — for single-source awards the Award '
             'action backfills this with line.qty when the bid is '
             'flipped without an explicit awarded_qty.',
    )
    comment = fields.Text(string='Comment')

    # W032 (R6.7, 2026-06-27) — bid-expiry tracking.
    # `is_expired` is flipped by the daily _cron_flag_expired_bids when
    # valid_until < today. Expired bids are excluded from the recommended
    # ranking on the parent line, and the line's recommended_bid_id is
    # cleared when it pointed at one. The bid record itself is kept
    # (active=True) so the audit trail / vendor history stays intact —
    # filtering happens at compute time, not at search time.
    is_expired = fields.Boolean(
        string='Quote Expired',
        default=False,
        copy=False,
        index=True,
        help='Set to True by the daily bid-expiry cron when valid_until '
             'is in the past. Expired bids stay on the record for audit '
             'but are dropped from recommended-bid scoring.',
    )
    expired_on = fields.Datetime(
        string='Expired On',
        readonly=True,
        copy=False,
        help='Stamp set when the bid-expiry cron first observed this '
             'bid as expired. Used to keep the cron idempotent.',
    )

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
    # W076 (R6.12) — portal hooks
    # ──────────────────────────────────────────────────────────────────
    def _compute_access_url(self):
        super()._compute_access_url()
        for bid in self:
            bid.access_url = f'/my/rfq-bids/{bid.id}' if bid.id else '#'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Mint an access token at creation so the buyer's invitation
        # email can always deep-link. portal.mixin's
        # `_portal_ensure_token` would lazy-create one on first share,
        # but having it eagerly available simplifies the email send +
        # the W076 portal submission flow (token compared via consteq).
        for rec in records:
            if not rec.access_token:
                rec.sudo()._portal_ensure_token()
        return records

    def _portal_can_be_submitted(self):
        """True when the bid is still mutable by the vendor through the portal.

        The submission endpoint locks down once the parent RFQ moves past
        ``received`` (i.e. into ``awarded`` or ``cancelled``). This is the
        single source of truth used by both the controller (to short-circuit
        the POST) and the QWeb template (to hide the Submit button)."""
        self.ensure_one()
        return self.rfq_state in self._editable_states()

    def _portal_post_submission(self, values, by_user):
        """Apply a vendor-portal submission to this bid.

        Called from the portal controller after token + identity checks.
        Validates the writable field set, applies the write under sudo
        (the vendor is a portal user without backend RW), and drops a
        chatter note on both the bid and parent RFQ so the buyer sees
        what changed.

        ``values`` is the already-parsed dict of writable fields:
            unit_price, lead_time_days, moq, valid_until,
            vendor_part_number, comment
        ``by_user`` is the authenticated portal user posting the form.
        """
        self.ensure_one()
        if not self._portal_can_be_submitted():
            raise UserError(_(
                "RFQ %s is no longer accepting bids (state: %s)."
            ) % (self.rfq_id.display_name, self.rfq_state))
        allowed = {
            'unit_price', 'lead_time_days', 'moq', 'valid_until',
            'vendor_part_number', 'comment',
        }
        clean = {k: v for k, v in values.items() if k in allowed and v not in (None, '')}
        if not clean:
            return False
        self.sudo().write(clean)
        # If the RFQ is still in 'sent' state, advance it to 'received'
        # because at least one vendor responded. We do NOT auto-advance
        # if every line still has empty bids — that's the buyer's call.
        if self.rfq_state == 'sent':
            rfq = self.rfq_id.sudo()
            if rfq.line_ids and all(l.bid_ids for l in rfq.line_ids):
                # safe: same precondition as action_mark_received
                try:
                    rfq.action_mark_received()
                except Exception:  # noqa: BLE001 — never block submission
                    pass
        change_summary = ', '.join(sorted(clean.keys()))
        self.sudo().message_post(
            body=_("Vendor portal update by <b>%s</b>: %s") % (
                by_user.display_name, change_summary,
            ),
            subtype_xmlid='mail.mt_log_note',
        )
        self.rfq_id.sudo().message_post(
            body=_("Bid update from <b>%s</b> (vendor %s) — fields: %s") % (
                by_user.display_name,
                self.vendor_id.display_name,
                change_summary,
            ),
            subtype_xmlid='mail.mt_log_note',
        )
        self.env['pg.audit.log'].sudo()._log(
            self.rfq_id, 'other',
            notes=_('Vendor portal submission for bid %s') % self.id,
            payload={'bid_id': self.id, 'fields': sorted(clean.keys())},
        )
        return True

    # ──────────────────────────────────────────────────────────────────
    # Constraints
    # ──────────────────────────────────────────────────────────────────
    # W060 (R6.10, 2026-06-27) — the original "exactly one awarded bid
    # per line" constraint was removed to allow dual-source split
    # awards. The replacement guarantees live on pg.rfq.line
    # (_check_awarded_qty_sum) and below (_check_awarded_qty_nonneg).
    # A single-vendor award is still the default — leaving awarded_qty=0
    # while flipping is_awarded triggers the action_award backfill to
    # line.qty so legacy callers behave exactly as before.

    @api.constrains('awarded_qty', 'is_awarded')
    def _check_awarded_qty_nonneg(self):
        for rec in self:
            if rec.awarded_qty < 0:
                raise ValidationError(
                    _("Awarded quantity cannot be negative (bid for %s).")
                    % (rec.vendor_id.display_name or rec.id)
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
    #
    # W032 — `is_expired` / `expired_on` are written by the daily bid-expiry
    # cron and must be allowed to flow through even on awarded RFQs (the
    # award doesn't un-stale a quote; we still want the audit signal).
    # W076 — `access_token` is minted on create by `_portal_ensure_token`;
    # if the parent RFQ is created already-awarded (rare, but possible via
    # data imports) we allow the token write through the immutability guard
    # because it carries no economic signal.
    _COMPUTE_ONLY_FIELDS = frozenset({
        'score', 'is_expired', 'expired_on', 'access_token',
    })

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

    # ──────────────────────────────────────────────────────────────────
    # W032 (R6.7) — daily bid-expiry cron
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def _cron_flag_expired_bids(self):
        """Daily sweep: flag bids whose `valid_until` is in the past.

        For each newly-expired bid the parent line's `recommended_bid_id`
        is cleared if it pointed at the expired bid, forcing the
        recommendation compute to re-evaluate against the surviving
        in-date bids only.

        Idempotent: bids already marked `is_expired=True` are skipped on
        the next run because the domain filter excludes them. A bid that
        somehow re-validates (manual edit, new valid_until in the future)
        is intentionally NOT auto-un-flagged here — un-staling a quote is
        a buyer decision, not a cron decision.

        Returns the recordset of bids flagged this run (useful for
        callers + test assertions).
        """
        today = fields.Date.context_today(self)
        domain = [
            ('is_expired', '=', False),
            ('valid_until', '!=', False),
            ('valid_until', '<', today),
        ]
        expiring = self.search(domain)
        if not expiring:
            return self.browse()
        # Snapshot lines whose current recommended_bid_id falls in the
        # expiring set so we can clear + repost chatter after the flag.
        affected_lines = expiring.mapped('line_id').filtered(
            lambda l: l.recommended_bid_id and l.recommended_bid_id in expiring
        )
        expiring.write({
            'is_expired': True,
            'expired_on': fields.Datetime.now(),
        })
        # Force the recommendation recompute to drop expired bids.
        # The compute itself filters out is_expired (see pg_rfq_line._compute_recommended),
        # but invalidating the cache guarantees the next read recomputes.
        expiring.line_id.invalidate_recordset(['recommended_bid_id'])
        # Per-RFQ chatter note so the engineer / buyer sees the staleness.
        by_rfq = {}
        for bid in expiring:
            by_rfq.setdefault(bid.rfq_id, self.env['pg.rfq.bid'])
            by_rfq[bid.rfq_id] |= bid
        for rfq, bids in by_rfq.items():
            vendor_lines = ', '.join(sorted({
                b.vendor_id.name or b.vendor_id.code or _('?')
                for b in bids
            }))
            rfq.message_post(
                body=_(
                    "<b>%(n)d bid(s) expired.</b> Vendors: %(vendors)s. "
                    "Re-request quotes if you still need this sourcing."
                ) % {'n': len(bids), 'vendors': vendor_lines},
                subtype_xmlid='mail.mt_log_note',
            )
        # Audit log per affected line that lost its recommendation.
        for line in affected_lines:
            self.env['pg.audit.log'].sudo()._log(
                line.rfq_id, 'other',
                notes=_('Bid expiry cleared recommended_bid_id on line for '
                        '%s') % line.item_id.display_name,
                payload={
                    'line_id': line.id,
                    'item_id': line.item_id.id,
                    'expired_bid_ids': expiring.filtered(
                        lambda b: b.line_id == line
                    ).ids,
                },
            )
        return expiring
