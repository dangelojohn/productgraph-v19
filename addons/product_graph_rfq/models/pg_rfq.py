# -*- coding: utf-8 -*-
import logging

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

    state = fields.Selection(
        RFQ_STATES, string='State', required=True, default='draft',
        copy=False, tracking=True,
    )

    requestor_id = fields.Many2one(
        'res.users', string='Requesting Engineer',
        required=True, default=lambda self: self.env.user, tracking=True,
    )
    due_date = fields.Date(string='Quotes Due By', tracking=True)
    sent_on = fields.Datetime(string='Sent On', readonly=True, copy=False)
    awarded_on = fields.Datetime(string='Awarded On', readonly=True, copy=False)

    line_ids = fields.One2many('pg.rfq.line', 'rfq_id', string='Lines')
    line_count = fields.Integer(compute='_compute_counts')
    bid_count = fields.Integer(compute='_compute_counts')

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
        for rec in self:
            if rec.state != 'received':
                raise UserError(
                    _("RFQ %s must be in 'received' before it can be awarded "
                      "(currently %s).") % (rec.display_name, rec.state)
                )
            for line in rec.line_ids:
                awarded = line.bid_ids.filtered('is_awarded')
                if len(awarded) != 1:
                    raise ValidationError(
                        _("Line for %s must have exactly one awarded bid "
                          "(found %d).") % (
                              line.item_id.display_name, len(awarded))
                    )
                rec._award_bid(line, awarded)
            rec.with_context(_pg_rfq_award_bypass=True).write({
                'state': 'awarded',
                'awarded_on': fields.Datetime.now(),
            })
            rec.env['pg.audit.log'].sudo()._log(
                rec, 'state_change',
                from_state='received', to_state='awarded',
                notes='AVL pricing refreshed from %d bids' % len(rec.line_ids),
            )
            rec.message_post(
                body=_("RFQ <b>awarded</b>. AVL refreshed for %d items.")
                % len(rec.line_ids),
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
