# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

ECO_STATES = [
    ('draft', 'Draft'),
    ('analysis', 'Analysis'),
    ('approval', 'Approval'),
    ('implementation', 'Implementation'),
    ('released', 'Released'),
    ('rejected', 'Rejected'),
]

# Fields that may NEVER be written once the ECO has reached `released`.
# Chatter / activity / message_follower fields are allowed because they are
# the audit surface — the record stays alive but its substance is frozen.
RELEASED_LOCKED_FIELDS = {
    'title', 'description', 'change_type', 'priority',
    'engineering_lead_id', 'approval_board_ids',
    'effective_date', 'target_release_date',
    'impact_summary', 'line_ids', 'requestor_id',
}


class PgEco(models.Model):
    _name = 'pg.eco'
    _description = 'Engineering Change Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    name = fields.Char(
        string='ECO #',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New'),
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)
    title = fields.Char(string='Title', required=True, tracking=True)
    description = fields.Html(string='Description')

    change_type = fields.Selection(
        [
            ('corrective', 'Corrective (fix defect)'),
            ('adaptive', 'Adaptive (new requirement)'),
            ('perfective', 'Perfective (improvement)'),
            ('regulatory', 'Regulatory (compliance)'),
        ],
        string='Change Type',
        required=True,
        default='perfective',
        tracking=True,
    )
    priority = fields.Selection(
        [
            ('low', 'Low'),
            ('normal', 'Normal'),
            ('high', 'High'),
            ('critical', 'Critical'),
        ],
        string='Priority',
        required=True,
        default='normal',
        tracking=True,
    )

    state = fields.Selection(
        ECO_STATES,
        string='State',
        required=True,
        default='draft',
        copy=False,
        tracking=True,
    )

    requestor_id = fields.Many2one(
        'res.users',
        string='Requestor',
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
    )
    engineering_lead_id = fields.Many2one(
        'res.users',
        string='Engineering Lead',
        tracking=True,
    )
    approval_board_ids = fields.Many2many(
        'res.users',
        'pg_eco_board_rel',
        'eco_id',
        'user_id',
        string='Approval Board',
        help='Members of the change-approval board for this ECO.',
    )

    effective_date = fields.Date(string='Effective Date')
    target_release_date = fields.Date(string='Target Release Date')
    actual_release_date = fields.Datetime(
        string='Actual Release', readonly=True, copy=False,
    )

    impact_summary = fields.Html(string='Impact Summary')
    rejection_reason = fields.Text(string='Rejection Reason', copy=False)

    line_ids = fields.One2many('pg.eco.line', 'eco_id', string='Affected Items')
    approval_ids = fields.One2many(
        'pg.eco.approval', 'eco_id', string='Approval Log', readonly=True,
    )

    line_count = fields.Integer(compute='_compute_line_count')

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
    )

    # ──────────────────────────────────────────────────────────────────
    # Compute / display
    # ──────────────────────────────────────────────────────────────────
    @api.depends('line_ids')
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    @api.depends('name', 'title')
    def _compute_display_name(self):
        for rec in self:
            if rec.name and rec.name != _('New'):
                rec.display_name = f'{rec.name} — {rec.title or ""}'.rstrip(' —')
            else:
                rec.display_name = rec.title or _('New ECO')

    # ──────────────────────────────────────────────────────────────────
    # CRUD
    # ──────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('pg.eco') or _('New')
        return super().create(vals_list)

    def write(self, vals):
        # Immutability on released ECOs (non-chatter fields).
        if not self.env.context.get('_pg_eco_release_bypass'):
            locked = RELEASED_LOCKED_FIELDS & set(vals.keys())
            if locked:
                for rec in self:
                    if rec.state == 'released':
                        raise UserError(
                            _(
                                "ECO %s is RELEASED and is immutable. "
                                "Fields blocked: %s"
                            )
                            % (rec.display_name, ', '.join(sorted(locked)))
                        )
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(
                    _("Only draft ECOs may be deleted. %s is %s.")
                    % (rec.display_name, rec.state)
                )
        return super().unlink()

    # ──────────────────────────────────────────────────────────────────
    # State machine — Bible §5
    # draft -> analysis -> approval -> implementation -> released
    #                              \-> rejected
    # ──────────────────────────────────────────────────────────────────
    def _check_transition(self, from_states, to_state, group_xmlid):
        """Validate that `self` is in one of `from_states` and the current
        user holds `group_xmlid`. Raises UserError on either failure.
        """
        self.ensure_one()
        if self.state not in from_states:
            raise UserError(
                _("ECO %s cannot transition to %s from %s.")
                % (self.display_name, to_state, self.state)
            )
        if not self.env.user.has_group(group_xmlid):
            raise UserError(
                _("Group required for this transition: %s.") % group_xmlid
            )

    def _post_transition(self, from_state, to_state, reason=''):
        """Chatter message + audit log entry. State-Machine Bible §7.1 + §7.2."""
        self.message_post(
            body=_("ECO state: <b>%s → %s</b> by %s%s") % (
                from_state, to_state, self.env.user.name,
                (' — %s' % reason) if reason else '',
            ),
            subtype_xmlid='mail.mt_log_note',
        )
        # Pipe into the existing pg.audit.log (base dep).
        self.env['pg.audit.log'].sudo()._log(
            self, 'state_change',
            from_state=from_state, to_state=to_state,
            notes=reason or None,
        )
        # Bible §7.3 transition notifications.
        self._send_transition_email(to_state)

    # Map state machine target → mail.template external ID. Transitions
    # that don't have a template (draft / analysis / cancelled) just skip.
    _TRANSITION_TEMPLATES = {
        'approval':       'product_graph_eco.mail_template_eco_submitted',
        'implementation': 'product_graph_eco.mail_template_eco_approved',
        'released':       'product_graph_eco.mail_template_eco_released',
        'rejected':       'product_graph_eco.mail_template_eco_rejected',
    }

    def _send_transition_email(self, to_state):
        """Send the Bible §7.3 notification for this transition.

        Resolves the mail.template by XML id, calls send_mail on each
        record in self. Quietly skips when the target state has no
        template configured (e.g. analysis, draft).
        """
        xmlid = self._TRANSITION_TEMPLATES.get(to_state)
        if not xmlid:
            return
        template = self.env.ref(xmlid, raise_if_not_found=False)
        if not template:
            return
        for rec in self:
            try:
                # force_send=False: queue for the outgoing-mail worker so a
                # broken SMTP doesn't poison the state-machine transaction.
                template.send_mail(rec.id, force_send=False)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "ECO %s: failed to queue transition email for %s",
                    rec.display_name, to_state,
                )

    def action_start_analysis(self):
        for rec in self:
            rec._check_transition(
                ('draft',), 'analysis', 'product_graph_base.group_pg_engineer',
            )
            rec.write({'state': 'analysis'})
            rec._post_transition('draft', 'analysis')

    def action_submit_for_approval(self):
        for rec in self:
            rec._check_transition(
                ('analysis',), 'approval', 'product_graph_base.group_pg_engineer',
            )
            if not rec.line_ids:
                raise ValidationError(
                    _("ECO %s has no affected items — add at least one line "
                      "before requesting approval.") % rec.display_name
                )
            if not rec.approval_board_ids:
                raise ValidationError(
                    _("ECO %s has no approval-board members assigned.")
                    % rec.display_name
                )
            missing_to = rec.line_ids.filtered(
                lambda l: l.change_kind in ('revision_bump', 'property_change', 'bom_change')
                and not l.to_revision_id
            )
            if missing_to:
                bad = ', '.join(missing_to.mapped(
                    lambda l: l.item_id.part_number or l.item_id.display_name
                ))
                raise ValidationError(
                    _("ECO %s has lines missing a target (to) revision: %s")
                    % (rec.display_name, bad)
                )
            rec.write({'state': 'approval'})
            rec._post_transition('analysis', 'approval')

    def action_approve(self, comment=''):
        for rec in self:
            rec._check_transition(
                ('approval',), 'implementation', 'product_graph_base.group_pg_approver',
            )
            self.env['pg.eco.approval'].create({
                'eco_id': rec.id,
                'user_id': self.env.user.id,
                'decision': 'approved',
                'comment': comment or '',
            })
            rec.write({'state': 'implementation'})
            rec._post_transition('approval', 'implementation', reason=comment)

    def action_reject(self, reason=''):
        reason = (reason or self.env.context.get('default_rejection_reason', '')).strip()
        for rec in self:
            rec._check_transition(
                ('approval',), 'rejected', 'product_graph_base.group_pg_approver',
            )
            effective_reason = reason or (rec.rejection_reason or '').strip()
            if not effective_reason:
                raise ValidationError(
                    _("Rejecting an ECO requires a rejection reason.")
                )
            self.env['pg.eco.approval'].create({
                'eco_id': rec.id,
                'user_id': self.env.user.id,
                'decision': 'rejected',
                'comment': effective_reason,
            })
            rec.write({
                'state': 'rejected',
                'rejection_reason': effective_reason,
            })
            rec._post_transition('approval', 'rejected', reason=effective_reason)

    def action_release(self):
        for rec in self:
            rec._check_transition(
                ('implementation',), 'released', 'product_graph_base.group_pg_approver',
            )
            unreleased = rec.line_ids.filtered(
                lambda l: not l.to_revision_id or l.to_revision_id.state != 'released'
            )
            if unreleased:
                bad = ', '.join(unreleased.mapped(
                    lambda l: l.item_id.part_number or l.item_id.display_name
                ))
                raise ValidationError(
                    _("Cannot release ECO %s — these lines reference a target "
                      "revision that is not yet released: %s")
                    % (rec.display_name, bad)
                )
            # Stamp release time. actual_release_date is readonly normally,
            # so flip via context-bypass for this one write.
            rec.with_context(_pg_eco_release_bypass=True).write({
                'state': 'released',
                'actual_release_date': fields.Datetime.now(),
            })
            rec._post_transition('implementation', 'released')

    def action_reset_to_draft(self):
        for rec in self:
            rec._check_transition(
                ('analysis',), 'draft', 'product_graph_base.group_pg_engineer',
            )
            rec.write({'state': 'draft'})
            rec._post_transition('analysis', 'draft')
