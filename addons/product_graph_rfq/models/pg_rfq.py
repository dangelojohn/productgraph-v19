# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

RFQ_STATES = [
    ('draft', 'Draft'),
    ('sent', 'Sent'),
    ('received', 'Bids Received'),
    ('awarded', 'Awarded'),
    ('cancelled', 'Cancelled'),
]

# Forward transitions only. Anything not listed is rejected.
RFQ_TRANSITIONS = {
    'draft': {'sent', 'cancelled'},
    'sent': {'received', 'cancelled'},
    'received': {'awarded', 'cancelled'},
    'awarded': set(),
    'cancelled': set(),
}

# Fields locked once the RFQ is in awarded state. Chatter / activity stays
# open as the audit surface.
AWARDED_LOCKED_FIELDS = {
    'title', 'description', 'requestor_id', 'due_date',
    'line_ids',
}

# Open states for the auto-draft dedupe lookup (no point auto-drafting a
# second RFQ if one is already in flight for the same item).
RFQ_OPEN_STATES = ('draft', 'sent', 'received')


class PgRfq(models.Model):
    _name = 'pg.rfq'
    _description = 'Engineering Request for Quote'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    name = fields.Char(
        string='RFQ #',
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: _('New'),
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)
    title = fields.Char(string='Title', required=True, tracking=True)
    description = fields.Html(string='Description')

    # Optional single-item shortcut. Auto-drafted RFQs (cron-created) set
    # this so the dedupe lookup can match by item. Manual RFQs may leave it
    # empty and rely on line_ids.
    item_id = fields.Many2one(
        'pg.item', string='Primary Item',
        index=True, ondelete='restrict',
        help='Primary item this RFQ is sourcing. Set by the auto-draft '
             'cron; manual RFQs may use line_ids alone.',
    )
    auto_drafted = fields.Boolean(
        string='Auto-Drafted',
        default=False, readonly=True, copy=False,
        help='True when this RFQ was created by the unsourced-items cron.',
    )
    reason = fields.Text(
        string='Reason',
        help='Human-readable rationale for the RFQ. Populated by the auto-'
             'draft cron; editable in draft for context.',
    )

    state = fields.Selection(
        RFQ_STATES, string='State', required=True, default='draft',
        copy=False, tracking=True,
    )

    requestor_id = fields.Many2one(
        'res.users', string='Requesting Engineer',
        required=True, default=lambda self: self.env.user, tracking=True,
    )
    # W062 (R6.16, 2026-06-27) — buyer ownership for multi-buyer teams.
    # The engineer who REQUESTED the RFQ (requestor_id) is often a
    # different person from the buyer who OWNS the sourcing decision
    # and clicks Award. Keeping the two fields separate makes the
    # "RFQs I own as a buyer" filter precise (vs the "RFQs I requested
    # as an engineer" filter on requestor_id). Defaults to the
    # creating user so single-buyer orgs continue to work without
    # configuration. Tracking on so reassignment shows up in chatter.
    buyer_id = fields.Many2one(
        'res.users', string='Buyer',
        default=lambda self: self.env.user,
        tracking=True, index=True,
        help='Buyer who owns the sourcing decision on this RFQ. Used '
             'by the "My RFQs as Buyer" search filter so multi-buyer '
             'teams can scope their queue. Distinct from requestor_id '
             '(the engineer who raised the request).',
    )
    due_date = fields.Date(string='Quotes Due By', tracking=True)
    sent_on = fields.Datetime(string='Sent On', readonly=True, copy=False)
    awarded_on = fields.Datetime(string='Awarded On', readonly=True, copy=False)

    line_ids = fields.One2many('pg.rfq.line', 'rfq_id', string='Lines')
    line_count = fields.Integer(compute='_compute_counts', store=True)
    bid_count = fields.Integer(compute='_compute_counts', store=True)

    promote_to_preferred = fields.Boolean(
        string='Promote Winners to Preferred',
        default=False,
        help='When awarding, mark the winning vendor-part as the preferred '
             'source for its item. Only one preferred source per item is '
             'allowed — pre-existing preferred sources will be demoted.',
    )

    company_id = fields.Many2one(
        'res.company', string='Company',
        required=True, default=lambda self: self.env.company,
    )

    # ──────────────────────────────────────────────────────────────────
    # Compute
    # ──────────────────────────────────────────────────────────────────
    @api.depends('name', 'title')
    def _compute_display_name(self):
        for rec in self:
            base = rec.name if rec.name and rec.name != _('New') else _('RFQ')
            rec.display_name = f'{base} — {rec.title}' if rec.title else base

    @api.depends('line_ids', 'line_ids.bid_ids')
    def _compute_counts(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.bid_count = sum(len(l.bid_ids) for l in rec.line_ids)

    # ──────────────────────────────────────────────────────────────────
    # CRUD
    # ──────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('pg.rfq') or _('New')
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get('_pg_rfq_award_bypass'):
            locked = AWARDED_LOCKED_FIELDS & set(vals.keys())
            if locked:
                for rec in self:
                    if rec.state == 'awarded':
                        raise UserError(
                            _("RFQ %s is AWARDED and is immutable. "
                              "Fields blocked: %s")
                            % (rec.display_name, ', '.join(sorted(locked)))
                        )
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state not in ('draft', 'cancelled'):
                raise UserError(
                    _("Only draft or cancelled RFQs may be deleted. "
                      "%s is %s.") % (rec.display_name, rec.state)
                )
        return super().unlink()

    # ──────────────────────────────────────────────────────────────────
    # State machine
    # ──────────────────────────────────────────────────────────────────
    def _transition(self, to_state, comment=''):
        self.ensure_one()
        if to_state not in RFQ_TRANSITIONS.get(self.state, set()):
            raise UserError(
                _("RFQ %s cannot transition from %s to %s.")
                % (self.display_name, self.state, to_state)
            )
        prev = self.state
        self.write({'state': to_state})
        self.env['pg.audit.log'].sudo()._log(
            self, 'state_change',
            from_state=prev, to_state=to_state, notes=comment or None,
        )
        self.message_post(
            body=_("RFQ state: <b>%s → %s</b>%s") % (
                prev, to_state, (' — %s' % comment) if comment else ''
            ),
            subtype_xmlid='mail.mt_log_note',
        )

    def action_send(self):
        for rec in self:
            if not rec.line_ids:
                raise ValidationError(
                    _("RFQ %s has no lines — add at least one line before sending.")
                    % rec.display_name
                )
            rec._transition('sent')
            rec.write({'sent_on': fields.Datetime.now()})

    def action_mark_received(self):
        for rec in self:
            empty_lines = rec.line_ids.filtered(lambda l: not l.bid_ids)
            if empty_lines:
                items = ', '.join(empty_lines.mapped(
                    lambda l: l.item_id.part_number or l.item_id.display_name
                ))
                raise ValidationError(
                    _("These RFQ lines have no bids yet: %s") % items
                )
            rec._transition('received')

    def action_cancel(self):
        for rec in self:
            rec._transition('cancelled')

    def action_award(self):
        # W060 (R6.10, 2026-06-27) — dual-source award. Each line may
        # carry one OR multiple awarded bids; the sum of awarded_qty
        # across them must not exceed line.qty (constraint enforced on
        # pg.rfq.line). Single-source legacy callers leave awarded_qty
        # at 0 and we backfill with line.qty here so the canonical flow
        # is unchanged.
        for rec in self:
            if rec.state != 'received':
                raise UserError(
                    _("RFQ %s must be in 'received' before it can be awarded "
                      "(currently %s).") % (rec.display_name, rec.state)
                )
            total_awards = 0
            for line in rec.line_ids:
                awarded = line.bid_ids.filtered('is_awarded')
                if not awarded:
                    raise ValidationError(
                        _("Line for %s has no awarded bid — flag at least "
                          "one bid as Awarded before clicking Award.")
                        % line.item_id.display_name
                    )
                # Backfill awarded_qty for single-source: one awarded
                # bid carrying qty=0 gets the full line qty. Multi-bid
                # splits MUST set awarded_qty explicitly per bid; if
                # any single multi-bid carries 0 we treat that as the
                # buyer leaving an unfilled slot and reject.
                if len(awarded) == 1 and not awarded.awarded_qty:
                    awarded.awarded_qty = line.qty
                else:
                    zero = awarded.filtered(lambda b: not b.awarded_qty)
                    if zero:
                        raise ValidationError(
                            _("Line for %(item)s has %(n)d awarded bids "
                              "but %(z)d carry awarded_qty=0. Either "
                              "set a positive awarded_qty on each split "
                              "bid or un-award the empty ones.") % {
                                'item': line.item_id.display_name,
                                'n': len(awarded),
                                'z': len(zero),
                            }
                        )
                for bid in awarded:
                    rec._award_bid(line, bid)
                total_awards += len(awarded)
            rec.with_context(_pg_rfq_award_bypass=True).write({
                'state': 'awarded',
                'awarded_on': fields.Datetime.now(),
            })
            rec.env['pg.audit.log'].sudo()._log(
                rec, 'state_change',
                from_state='received', to_state='awarded',
                notes='AVL pricing refreshed from %d bid(s) across %d line(s)' % (
                    total_awards, len(rec.line_ids),
                ),
            )
            rec.message_post(
                body=_("RFQ <b>awarded</b>. AVL refreshed from %(n)d bid(s) "
                       "across %(lines)d line(s).") % {
                    'n': total_awards, 'lines': len(rec.line_ids),
                },
                subtype_xmlid='mail.mt_log_note',
            )

    def _award_bid(self, line, bid):
        """Write the winning bid's economics back to pg.vendor.part."""
        self.ensure_one()
        VendorPart = self.env['pg.vendor.part']
        part = VendorPart.search([
            ('item_id', '=', line.item_id.id),
            ('vendor_id', '=', bid.vendor_id.id),
        ], limit=1)
        vals = {
            'unit_price_hint': bid.unit_price,
            'currency_id': bid.currency_id.id or False,
            'lead_time_days': bid.lead_time_days,
            'moq': bid.moq or 1.0,
        }
        if not part:
            vals.update({
                'item_id': line.item_id.id,
                'vendor_id': bid.vendor_id.id,
                'vendor_part_number': bid.vendor_part_number or '',
                'rank': 10,
            })
            part = VendorPart.create(vals)
        else:
            if bid.vendor_part_number and not part.vendor_part_number:
                vals['vendor_part_number'] = bid.vendor_part_number
            part.write(vals)
        if self.promote_to_preferred:
            # Demote any other preferred for this item, then promote ours.
            other = VendorPart.search([
                ('item_id', '=', line.item_id.id),
                ('is_preferred', '=', True),
                ('id', '!=', part.id),
            ])
            if other:
                other.write({'is_preferred': False})
            part.write({'is_preferred': True})

    # ──────────────────────────────────────────────────────────────────
    # Auto-draft cron (PG-133 task 1)
    # ──────────────────────────────────────────────────────────────────
    # W033 (R6.8, 2026-06-27) — batch cap + per-item cooldown.
    # Two ICP keys gate the cron:
    #   southbrook.auto_rfq_max_per_run     int, default 10 (cap on new
    #                                       RFQs per pass)
    #   southbrook.auto_rfq_cooldown_hours  int, default 24 (per-item
    #                                       re-attempt cooldown)
    # Per-item cooldown anchored on pg.item.last_auto_rfq_on (added by
    # this addon). The stamp is written for BOTH outcomes (drafted RFQ
    # OR posted unsourced-item activity) so a missing-category item
    # does not keep firing the activity-creation path daily either.
    _DEFAULT_AUTO_RFQ_MAX_PER_RUN = 10
    _DEFAULT_AUTO_RFQ_COOLDOWN_HOURS = 24

    @api.model
    def _get_auto_rfq_max_per_run(self):
        Param = self.env['ir.config_parameter'].sudo()
        raw = Param.get_param('southbrook.auto_rfq_max_per_run')
        try:
            value = int(raw) if raw is not None and raw != '' else self._DEFAULT_AUTO_RFQ_MAX_PER_RUN
        except (TypeError, ValueError):
            value = self._DEFAULT_AUTO_RFQ_MAX_PER_RUN
        # 0 / negative values are treated as "no cap" — the original
        # behaviour, useful for tests or when an admin wants the legacy
        # firehose back.
        return value if value > 0 else None

    @api.model
    def _get_auto_rfq_cooldown_hours(self):
        Param = self.env['ir.config_parameter'].sudo()
        raw = Param.get_param('southbrook.auto_rfq_cooldown_hours')
        try:
            value = int(raw) if raw is not None and raw != '' else self._DEFAULT_AUTO_RFQ_COOLDOWN_HOURS
        except (TypeError, ValueError):
            value = self._DEFAULT_AUTO_RFQ_COOLDOWN_HOURS
        return max(value, 0)

    @api.model
    def _cron_auto_draft_missing_rfqs(self):
        """Daily sweep: draft RFQs for purchased items lacking a qualified
        AVL vendor. Recommendation-only — the RFQ stays in 'draft' so a
        human still has to confirm pricing and click Send / Award.

        W033 (R6.8) — capped at `southbrook.auto_rfq_max_per_run` new
        RFQs per pass (default 10) AND skips items whose
        `last_auto_rfq_on` is within
        `southbrook.auto_rfq_cooldown_hours` (default 24). Counts only
        RFQs actually created against the cap — items skipped by
        cooldown or already-sourced don't burn the budget.
        """
        Item = self.env['pg.item']
        max_per_run = self._get_auto_rfq_max_per_run()
        cooldown_hours = self._get_auto_rfq_cooldown_hours()

        domain = [
            ('item_type', '=', 'purchased'),
            ('state', 'in', ('engineering', 'released')),
            ('active', '=', True),
        ]
        if cooldown_hours:
            cutoff = fields.Datetime.now() - timedelta(hours=cooldown_hours)
            domain.append('|')
            domain.append(('last_auto_rfq_on', '=', False))
            domain.append(('last_auto_rfq_on', '<', cutoff))
        items = Item.search(domain)

        created = self.env['pg.rfq']
        considered_items = Item
        for item in items:
            if max_per_run is not None and len(created) >= max_per_run:
                _logger.info(
                    "Auto-RFQ batch cap (%d) reached — deferring further "
                    "candidate items to the next cron pass.",
                    max_per_run,
                )
                break
            qualified = item.vendor_part_ids.filtered(
                lambda vp: vp.vendor_id.state == 'qualified' and vp.active
            )
            if qualified:
                continue
            existing = self.search([
                ('item_id', '=', item.id),
                ('state', 'in', list(RFQ_OPEN_STATES)),
            ], limit=1)
            if existing:
                continue
            rfq = self._auto_draft_for_item(item)
            considered_items |= item
            if rfq:
                created |= rfq
        # Stamp the cooldown anchor for every item we touched (drafted OR
        # bounced off the no-category / no-vendor path). Sudo because the
        # cron runs as the cron user and pg.item writes are gated.
        if considered_items:
            considered_items.sudo().write({
                'last_auto_rfq_on': fields.Datetime.now(),
            })
        if created:
            _logger.info(
                "Auto-drafted %d RFQs for unsourced purchased items "
                "(cap=%s, cooldown=%dh)",
                len(created),
                'none' if max_per_run is None else str(max_per_run),
                cooldown_hours,
            )
        return created

    @api.model
    def _auto_draft_for_item(self, item):
        """Build a draft RFQ for a purchased item and seed placeholder bids
        for every qualified vendor whose category_ids include the item's
        category. Returns the created ``pg.rfq`` or ``False`` if there were
        no candidate vendors."""
        if not item.category_id:
            _logger.warning(
                "pg.item %s has no category — auto-RFQ skipped.",
                item.display_name,
            )
            self._notify_unsourced_item(item, reason='no_category')
            return False
        Vendor = self.env['pg.vendor']
        category_vendors = Vendor.search([
            ('category_ids', 'in', item.category_id.id),
            ('state', '=', 'qualified'),
            ('active', '=', True),
        ])
        if not category_vendors:
            _logger.warning(
                "pg.item %s in category %s has no qualified category "
                "vendors — auto-RFQ skipped.",
                item.display_name, item.category_id.complete_name,
            )
            self._notify_unsourced_item(item)
            return False
        title = _("Auto-RFQ for %s") % (item.part_number or item.display_name)
        reason = _(
            "Auto-drafted: item %(item)s is in state %(state)s with no "
            "qualified AVL vendor. Candidate vendors selected by category "
            "%(cat)s."
        ) % {
            'item': item.display_name,
            'state': item.state,
            'cat': item.category_id.complete_name,
        }
        rfq = self.sudo().create({
            'title': title,
            'item_id': item.id,
            'auto_drafted': True,
            'reason': reason,
        })
        line = self.env['pg.rfq.line'].sudo().create({
            'rfq_id': rfq.id,
            'item_id': item.id,
            'qty': 1.0,
            'spec_notes': reason,
        })
        Bid = self.env['pg.rfq.bid'].sudo()
        for vendor in category_vendors:
            Bid.create({
                'line_id': line.id,
                'vendor_id': vendor.id,
                'unit_price': 0.0,
                'is_awarded': False,
            })
        item.message_post(
            body=_(
                "Auto-drafted RFQ <b>%s</b> seeded with %d candidate "
                "vendor(s) — review pricing and send."
            ) % (rfq.display_name, len(category_vendors)),
            subtype_xmlid='mail.mt_log_note',
        )
        self.env['pg.audit.log'].sudo()._log(
            rfq, 'other',
            from_state=None, to_state='draft',
            notes=_('Auto-drafted RFQ for unsourced item %s') % item.display_name,
            payload={
                'item_id': item.id,
                'category_id': item.category_id.id,
                'candidate_vendor_ids': category_vendors.ids,
            },
        )
        return rfq

    # ──────────────────────────────────────────────────────────────────
    # R6.4 / W013 — Sourcing Inbox activity for unsourced items
    # ──────────────────────────────────────────────────────────────────
    UNSOURCED_ACTIVITY_SUMMARY = 'Unsourced item — needs vendor'

    @api.model
    def _notify_unsourced_item(self, item, reason='no_category_vendor'):
        """Create a mail.activity on the pg.item flagging the sourcing gap.

        Replaces the silent log line (R6.4 finding) so the engineer who owns
        the item, or a fallback engineering-group user, gets an addressable
        inbox marker instead of a buried warning.

        Idempotent: if there's already an open activity with the same summary
        on this item, returns without creating a duplicate. This keeps the
        daily auto-RFQ cron from spamming.

        Args:
            item: pg.item record to flag.
            reason: 'no_category_vendor' (default) or 'no_category'. Drives
                the chatter note body so the engineer sees which gap it is.
        """
        if not item:
            return
        Activity = self.env['mail.activity']
        # Idempotency check — open activity for this item with our summary.
        existing = Activity.sudo().search([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', item.id),
            ('summary', '=', self.UNSOURCED_ACTIVITY_SUMMARY),
            ('date_deadline', '>=', fields.Date.context_today(self)),
        ], limit=1)
        if existing:
            return
        # Resolve assignee: prefer item.responsible_id, else first engineer.
        assignee = item.responsible_id
        if not assignee:
            eng_group = self.env.ref(
                'product_graph_base.group_pg_engineer',
                raise_if_not_found=False,
            )
            if eng_group:
                # Odoo 19: query the inverse rather than .users.
                assignee = self.env['res.users'].search(
                    [('group_ids', 'in', eng_group.id)], limit=1,
                )
        if not assignee:
            _logger.warning(
                "No assignee for unsourced item %s — activity skipped.",
                item.display_name,
            )
            return
        activity_type = self.env.ref(
            'mail.mail_activity_data_todo', raise_if_not_found=False,
        )
        if not activity_type:
            _logger.warning(
                "mail.mail_activity_data_todo missing — cannot schedule "
                "unsourced-item activity for %s.", item.display_name,
            )
            return
        if reason == 'no_category':
            note = _(
                "Auto-RFQ cron could not draft an RFQ for this item: it has "
                "no category, so candidate vendors cannot be selected. "
                "Assign a category on the item to unblock sourcing."
            )
        else:
            cat = item.category_id.complete_name if item.category_id else _('(none)')
            note = _(
                "Auto-RFQ cron found no qualified vendor for this item's "
                "category (%(cat)s). Either qualify a vendor in that "
                "category or move the item to a category that has one."
            ) % {'cat': cat}
        item.sudo().activity_schedule(
            activity_type_id=activity_type.id,
            user_id=assignee.id,
            summary=self.UNSOURCED_ACTIVITY_SUMMARY,
            note=note,
        )
        self.env['pg.audit.log'].sudo()._log(
            item, 'other',
            notes=_('Unsourced-item activity scheduled for %s') % assignee.name,
            payload={
                'item_id': item.id,
                'reason': reason,
                'assignee_id': assignee.id,
            },
        )
