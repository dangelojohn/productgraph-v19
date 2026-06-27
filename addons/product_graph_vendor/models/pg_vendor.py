# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Days before a vendor qualification's valid_until that we start nagging.
QUALIFICATION_EXPIRY_WINDOW_DAYS = 30

VENDOR_STATES = [
    ('unqualified', 'Unqualified'),
    ('probationary', 'Probationary'),
    ('qualified', 'Qualified'),
    ('suspended', 'Suspended'),
    ('disqualified', 'Disqualified'),
]

# Valid forward transitions. Anything not listed here is rejected.
VENDOR_TRANSITIONS = {
    'unqualified': {'probationary', 'disqualified'},
    'probationary': {'qualified', 'suspended', 'disqualified'},
    'qualified': {'suspended', 'disqualified'},
    'suspended': {'qualified', 'disqualified'},
    'disqualified': set(),
}


class PgVendor(models.Model):
    _name = 'pg.vendor'
    _inherit = ['pg.vendor', 'mail.thread', 'mail.activity.mixin']

    state = fields.Selection(
        VENDOR_STATES,
        string='Qualification State',
        default='unqualified',
        required=True,
        tracking=True,
        copy=False,
    )

    qualified_on = fields.Date(
        string='Qualified On',
        copy=False,
        help='Date the vendor first reached the qualified state.',
    )
    qualified_by_id = fields.Many2one(
        'res.users', string='Qualified By', copy=False, ondelete='restrict',
    )
    qualification_expires_on = fields.Date(
        string='Re-qualify By',
        help='Re-qualification cadence target. Not enforced automatically.',
    )

    # The is_approved field shipped on the base stub stays as a denormalized
    # convenience flag; we keep it in sync with state == 'qualified'.
    is_approved = fields.Boolean(
        compute='_compute_is_approved',
        store=True,
        readonly=True,
    )

    qualification_ids = fields.One2many(
        'pg.vendor.qualification', 'vendor_id',
        string='Qualification Log', readonly=True,
    )
    vendor_part_ids = fields.One2many(
        'pg.vendor.part', 'vendor_id', string='Cross-References',
    )
    vendor_part_count = fields.Integer(compute='_compute_counts', store=True)
    item_count = fields.Integer(
        compute='_compute_counts',
        store=True,
        help='Distinct pg.items this vendor supplies.',
    )

    # Engineering categories this vendor is approved to supply. Used by the
    # RFQ auto-draft cron to pick candidate vendors for an unsourced item.
    category_ids = fields.Many2many(
        'pg.category',
        'pg_vendor_category_rel',
        'vendor_id', 'category_id',
        string='Approved Categories',
        help='Engineering categories this vendor is qualified to bid on. '
             'The auto-RFQ cron seeds bids for vendors whose categories '
             "match the requesting item's category.",
    )

    responsible_id = fields.Many2one(
        'res.users',
        string='Responsible Buyer',
        help='Buyer responsible for this vendor relationship. Activities '
             'raised by the qualification-expiry cron are assigned here.',
    )

    # ──────────────────────────────────────────────────────────────────
    # Compute
    # ──────────────────────────────────────────────────────────────────
    @api.depends('state')
    def _compute_is_approved(self):
        for rec in self:
            rec.is_approved = rec.state == 'qualified'

    @api.depends('vendor_part_ids', 'vendor_part_ids.item_id')
    def _compute_counts(self):
        for rec in self:
            rec.vendor_part_count = len(rec.vendor_part_ids)
            rec.item_count = len(rec.vendor_part_ids.mapped('item_id'))

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def _log_qualification(self, decision, comment=''):
        self.ensure_one()
        self.env['pg.vendor.qualification'].create({
            'vendor_id': self.id,
            'decision': decision,
            'decided_by_id': self.env.user.id,
            'comment': comment or '',
        })
        self.env['pg.audit.log'].sudo()._log(
            self, 'state_change',
            from_state=self._origin.state if self._origin else None,
            to_state=decision, notes=comment or None,
        )

    def _transition(self, to_state, comment=''):
        self.ensure_one()
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise UserError(
                _("Vendor qualification transitions require the "
                  "ProductGraph / Approver role.")
            )
        if to_state not in VENDOR_TRANSITIONS.get(self.state, set()):
            raise UserError(
                _("Vendor %s cannot transition from %s to %s.")
                % (self.display_name, self.state, to_state)
            )
        prev = self.state
        vals = {'state': to_state}
        if to_state == 'qualified' and not self.qualified_on:
            vals['qualified_on'] = fields.Date.today()
            vals['qualified_by_id'] = self.env.user.id
        self.write(vals)
        self.env['pg.vendor.qualification'].create({
            'vendor_id': self.id,
            'decision': to_state,
            'decided_by_id': self.env.user.id,
            'comment': comment or '',
        })
        self.env['pg.audit.log'].sudo()._log(
            self, 'state_change',
            from_state=prev, to_state=to_state, notes=comment or None,
        )
        self.message_post(
            body=_("Vendor qualification: <b>%s → %s</b>") % (prev, to_state),
            subtype_xmlid='mail.mt_log_note',
        )

    # ──────────────────────────────────────────────────────────────────
    # State actions
    # ──────────────────────────────────────────────────────────────────
    def action_to_probationary(self):
        for rec in self:
            rec._transition('probationary')

    def action_qualify(self):
        for rec in self:
            rec._transition('qualified')

    def action_suspend(self):
        comment = self.env.context.get('default_qualification_comment', '')
        for rec in self:
            if not comment:
                raise ValidationError(
                    _("Suspending a vendor requires a comment.")
                )
            rec._transition('suspended', comment=comment)

    def action_reinstate(self):
        for rec in self:
            rec._transition('qualified')

    def action_disqualify(self):
        comment = self.env.context.get('default_qualification_comment', '')
        for rec in self:
            if not comment:
                raise ValidationError(
                    _("Disqualifying a vendor requires a comment.")
                )
            rec._transition('disqualified', comment=comment)

    def action_view_vendor_parts(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Vendor Parts for %s") % self.display_name,
            'res_model': 'pg.vendor.part',
            'view_mode': 'list,form',
            'domain': [('vendor_id', '=', self.id)],
            'context': {'default_vendor_id': self.id},
        }

    # ──────────────────────────────────────────────────────────────────
    # Expiring-qualification cron (PG-133 task 4)
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def _cron_flag_expiring_qualifications(self):
        """Daily sweep — nag on qualifications expiring within the window.

        For every ``pg.vendor.qualification`` with ``valid_until`` inside
        the warning window, posts a chatter message on the vendor and raises
        a ``mail.activity`` for the responsible buyer (falling back to the
        ``group_pg_approver`` users when no buyer is assigned). Idempotent
        per (qualification, valid_until) — an existing open activity for the
        same record + summary suppresses re-creation so daily reruns don't
        spam.
        """
        today = fields.Date.context_today(self)
        horizon = fields.Date.add(today, days=QUALIFICATION_EXPIRY_WINDOW_DAYS)
        Qualification = self.env['pg.vendor.qualification']
        expiring = Qualification.search([
            ('valid_until', '!=', False),
            ('valid_until', '>=', today),
            ('valid_until', '<=', horizon),
            ('decision', '=', 'qualified'),
        ])
        if not expiring:
            return 0
        Activity = self.env['mail.activity']
        ActivityType = self.env.ref(
            'mail.mail_activity_data_todo', raise_if_not_found=False,
        )
        activity_type_id = ActivityType.id if ActivityType else False
        flagged = 0
        for qual in expiring:
            vendor = qual.vendor_id
            if not vendor:
                continue
            assignee = vendor.responsible_id
            if not assignee:
                approver_group = self.env.ref(
                    'product_graph_base.group_pg_approver',
                    raise_if_not_found=False,
                )
                if approver_group:
                    # Odoo 19 removed res.groups.users — query the inverse.
                    assignee = self.env['res.users'].search(
                        [('group_ids', 'in', approver_group.id)], limit=1,
                    )
            days_left = (qual.valid_until - today).days
            summary = _(
                "Vendor qualification expires in %(days)d day(s) (%(date)s)"
            ) % {'days': days_left, 'date': qual.valid_until}
            note = _(
                "Qualification decided %(decided)s by %(by)s expires on "
                "%(until)s. Schedule re-qualification or move the vendor to "
                "Probationary."
            ) % {
                'decided': qual.decided_on,
                'by': qual.decided_by_id.name or _('(unknown)'),
                'until': qual.valid_until,
            }
            existing = Activity.search([
                ('res_model', '=', vendor._name),
                ('res_id', '=', vendor.id),
                ('summary', '=', summary),
            ], limit=1)
            if existing:
                continue
            if assignee and activity_type_id:
                vendor_model = self.env['ir.model']._get(vendor._name)
                Activity.sudo().create({
                    'res_model_id': vendor_model.id,
                    'res_id': vendor.id,
                    'activity_type_id': activity_type_id,
                    'summary': summary,
                    'note': note,
                    'user_id': assignee.id,
                    'date_deadline': qual.valid_until,
                })
            vendor.message_post(
                body=_(
                    "<b>Qualification expiring soon</b> — %s"
                ) % summary,
                subtype_xmlid='mail.mt_log_note',
            )
            self.env['pg.audit.log'].sudo()._log(
                vendor, 'other',
                notes=_('Qualification expiring on %s') % qual.valid_until,
                payload={
                    'qualification_id': qual.id,
                    'valid_until': str(qual.valid_until),
                    'days_left': days_left,
                    'assignee_id': assignee.id if assignee else None,
                },
            )
            flagged += 1
        if flagged:
            _logger.info(
                "Flagged %d expiring vendor qualifications (window: %d days)",
                flagged, QUALIFICATION_EXPIRY_WINDOW_DAYS,
            )
        return flagged
