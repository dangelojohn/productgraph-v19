# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Marker string embedded in the SLA reminder chatter body so the dedup check
# can recognise its own posts (we don't ship a custom mt_ subtype in this round).
_SLA_REMINDER_MARKER = '[pg-eco-sla-reminder]'
_SLA_ESCALATION_MARKER = '[pg-eco-sla-escalation]'

ECO_STATES = [
    ('draft', 'Draft'),
    ('analysis', 'Analysis'),
    ('approval', 'Approval'),
    ('implementation', 'Implementation'),
    ('released', 'Released'),
    ('rejected', 'Rejected'),
]

# pg.eco transitions per Bible §5. This is informational for the lifecycle
# mixin's _check_transition_allowed helper; the actual gate today is
# self._check_transition(from_states, to_state, group_xmlid) which has a
# different signature and bundles the group check inline.
_ECO_TRANSITIONS = {
    'draft':          ['analysis'],
    'analysis':       ['approval', 'draft'],
    'approval':       ['implementation', 'rejected'],
    'implementation': ['released'],
    'released':       [],
    'rejected':       [],
}

# Fields that may NEVER be written once the ECO has reached `released`.
# Chatter / activity / message_follower fields are allowed because they are
# the audit surface — the record stays alive but its substance is frozen.
# Now consumed by pg.lifecycle.mixin as LIFECYCLE_RELEASED_LOCKED_FIELDS.
RELEASED_LOCKED_FIELDS = {
    'title', 'description', 'change_type', 'priority',
    'engineering_lead_id', 'approval_board_ids',
    'effective_date', 'target_release_date',
    'impact_summary', 'line_ids', 'requestor_id',
}


class PgEco(models.Model):
    _name = 'pg.eco'
    _description = 'Engineering Change Order'
    _inherit = ['pg.lifecycle.mixin', 'mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    # ─── Lifecycle mixin config ──────────────────────────────────────────────
    # pg.eco uses BLOCKLIST-on-released semantics (only the named fields
    # are blocked, and only when state == 'released'). The mixin's
    # LIFECYCLE_RELEASED_LOCKED_FIELDS path replicates that behavior.
    LIFECYCLE_STATES = ECO_STATES
    LIFECYCLE_TRANSITIONS = _ECO_TRANSITIONS
    LIFECYCLE_BYPASS_KEY = '_pg_eco_release_bypass'
    LIFECYCLE_RELEASED_LOCKED_FIELDS = frozenset(RELEASED_LOCKED_FIELDS)

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
    # PG-120 SoD: who originally requested vs who submitted-for-approval.
    # `requested_by_id` defaults to author at create; `submitted_by_id` is
    # stamped at analysis → approval. Both are blocked from being the approver.
    requested_by_id = fields.Many2one(
        'res.users',
        string='Requested By',
        readonly=True, copy=False,
        default=lambda self: self.env.user,
    )
    submitted_by_id = fields.Many2one(
        'res.users',
        string='Submitted By',
        readonly=True, copy=False,
    )
    submitted_at = fields.Datetime(
        string='Submitted At',
        readonly=True, copy=False,
    )
    is_self_submitted = fields.Boolean(
        compute='_compute_is_self_submitted',
        help='True when the current user is the requestor or the submitter. '
             'Drives view-level read-only protection of author-only fields '
             'once the ECO has left draft (SoD).',
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

    # PG-134 — SLA escalation tracking. Bumped by the daily cron when the
    # ECO is past target_release_date; capped at 2.
    escalation_level = fields.Integer(
        string='Escalation Level',
        default=0,
        copy=False,
        readonly=True,
        tracking=True,
    )
    escalation_stamped_at = fields.Datetime(
        string='Last Escalated At',
        copy=False,
        readonly=True,
    )

    line_ids = fields.One2many('pg.eco.line', 'eco_id', string='Affected Items')
    approval_ids = fields.One2many(
        'pg.eco.approval', 'eco_id', string='Approval Log', readonly=True,
    )

    line_count = fields.Integer(compute='_compute_line_count', store=True)

    # PG-161 — one-click Promote + Express Release UX flags.
    can_promote = fields.Boolean(
        compute='_compute_lifecycle_buttons',
        help='True when action_promote will succeed for the current user.',
    )
    promote_blocked_reason = fields.Char(
        compute='_compute_lifecycle_buttons',
        help='Human-readable reason can_promote is False; empty when promotion is OK.',
    )
    can_express_release = fields.Boolean(
        compute='_compute_lifecycle_buttons',
        help='True when action_express_release (draft → released) will succeed.',
    )
    express_release_blocked_reason = fields.Char(
        compute='_compute_lifecycle_buttons',
        help='Human-readable reason can_express_release is False.',
    )

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
    )

    # ──────────────────────────────────────────────────────────────────
    # Constraints
    # ──────────────────────────────────────────────────────────────────
    @api.constrains('title')
    def _check_title_not_blank(self):
        """Reject empty/whitespace-only titles with a friendly error.

        `title` is declared required=True at the field level, but v19's ORM
        precheck lets a missing key on `create({})` fall through to the
        Postgres NOT NULL constraint — the user then sees a raw
        psycopg2.NotNullViolation instead of a clean ValidationError. This
        catches the case cleanly and covers whitespace-only titles too.
        """
        for rec in self:
            if not rec.title or not rec.title.strip():
                raise ValidationError(_(
                    "ECO title is required and cannot be blank. "
                    "Please provide a short human-readable summary of "
                    "the change."
                ))

    # ──────────────────────────────────────────────────────────────────
    # Compute / display
    # ──────────────────────────────────────────────────────────────────
    @api.depends('line_ids')
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    @api.depends('requested_by_id', 'submitted_by_id')
    def _compute_is_self_submitted(self):
        uid = self.env.user.id
        for rec in self:
            rec.is_self_submitted = bool(
                (rec.requested_by_id and rec.requested_by_id.id == uid)
                or (rec.submitted_by_id and rec.submitted_by_id.id == uid)
            )

    # PG-161 — Compute Promote / Express-Release availability and reasons.
    # Non-stored: cheap to derive from current state + current user identity,
    # and we want it to track the *requesting* user, not the record owner.
    @api.depends(
        'state', 'requested_by_id', 'submitted_by_id',
        'engineering_lead_id', 'approval_board_ids',
        'line_ids', 'line_ids.to_revision_id', 'line_ids.to_revision_id.state',
        'line_ids.change_kind',
    )
    def _compute_lifecycle_buttons(self):
        ICP = self.env['ir.config_parameter'].sudo()
        sod_enforced = str(ICP.get_param('productgraph.enforce_sod', 'True')) == 'True'
        uid = self.env.user.id
        is_admin = self.env.user.has_group('product_graph_base.group_pg_admin')
        for rec in self:
            # Default values; overridden below.
            rec.can_promote = False
            rec.promote_blocked_reason = False
            rec.can_express_release = False
            rec.express_release_blocked_reason = False

            # ── Promote ────────────────────────────────────────────────
            if rec.state == 'draft':
                if rec.line_ids:
                    rec.can_promote = True
                else:
                    rec.promote_blocked_reason = _(
                        "Add at least one affected-item line before starting analysis."
                    )
            elif rec.state == 'analysis':
                if not rec.line_ids:
                    rec.promote_blocked_reason = _(
                        "Add at least one affected-item line before submitting."
                    )
                elif not rec.approval_board_ids:
                    rec.promote_blocked_reason = _(
                        "Assign at least one Approval Board member before submitting."
                    )
                else:
                    rec.can_promote = True
            elif rec.state == 'approval':
                sod_block = sod_enforced and (
                    (rec.requested_by_id and rec.requested_by_id.id == uid)
                    or (rec.submitted_by_id and rec.submitted_by_id.id == uid)
                )
                if sod_block:
                    rec.promote_blocked_reason = _(
                        "A different user from requestor/submitter must approve (SoD)."
                    )
                else:
                    rec.can_promote = True
            elif rec.state == 'implementation':
                governed = rec.line_ids.filtered(
                    lambda l: l.change_kind in (
                        'revision_bump', 'property_change', 'bom_change',
                    )
                )
                unfinished = governed.filtered(
                    lambda l: not l.to_revision_id
                    or l.to_revision_id.state not in ('released', 'superseded')
                )
                if unfinished:
                    rec.promote_blocked_reason = _(
                        "Waiting on %d affected revisions to release."
                    ) % len(unfinished)
                else:
                    sod_block = sod_enforced and (
                        (rec.requested_by_id and rec.requested_by_id.id == uid)
                        or (rec.submitted_by_id and rec.submitted_by_id.id == uid)
                    )
                    if sod_block:
                        rec.promote_blocked_reason = _(
                            "A different user from requestor/submitter must release (SoD)."
                        )
                    else:
                        rec.can_promote = True
            else:
                # released / rejected — final states.
                rec.promote_blocked_reason = _("ECO is in a final state.")

            # ── Express Release (draft → released) ─────────────────────
            if rec.state != 'draft':
                rec.express_release_blocked_reason = _(
                    "Express Release only applies to Draft ECOs."
                )
            elif sod_enforced and not is_admin:
                rec.express_release_blocked_reason = _(
                    "SoD enforced — admin-only for ECO Express Release."
                )
            elif not rec.line_ids:
                rec.express_release_blocked_reason = _(
                    "Add at least one affected-item line first."
                )
            elif not rec.approval_board_ids:
                rec.express_release_blocked_reason = _(
                    "Assign at least one Approval Board member first."
                )
            else:
                rec.can_express_release = True

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

    # write() — released-only blocklist immutability is provided by
    # pg.lifecycle.mixin (LIFECYCLE_RELEASED_LOCKED_FIELDS).

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

    # ─── PG-120 Segregation of Duties ────────────────────────────────────────
    def _check_sod(self, action_label):
        """Block actor == requestor OR actor == submitter for governed ECO
        transitions (approve, release). Bypass via ICP
        ``productgraph.enforce_sod=False`` (default True).
        """
        # PG-161 — coordinated bypass for action_express_release. That action
        # gates SoD itself (admin-only when SoD is enforced) and consciously
        # approves + releases as the same user, so the standard SoD raise here
        # would block the path it just authorised.
        if self.env.context.get('_pg_express_release'):
            return
        ICP = self.env['ir.config_parameter'].sudo()
        if str(ICP.get_param('productgraph.enforce_sod', 'True')) != 'True':
            return
        uid = self.env.user.id
        for rec in self:
            blockers = []
            if rec.requested_by_id and rec.requested_by_id.id == uid:
                blockers.append(_(
                    "You requested ECO %(name)s — a different user must "
                    "%(action)s it (Segregation of Duties).",
                    name=rec.display_name, action=action_label,
                ))
            if rec.submitted_by_id and rec.submitted_by_id.id == uid:
                blockers.append(_(
                    "You submitted ECO %(name)s for approval — a different "
                    "user must %(action)s it (Segregation of Duties).",
                    name=rec.display_name, action=action_label,
                ))
            if blockers:
                raise UserError("\n".join(blockers))

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
            rec.write({
                'state': 'approval',
                'submitted_by_id': self.env.user.id,
                'submitted_at': fields.Datetime.now(),
            })
            rec._post_transition('analysis', 'approval')
            # PG-134 — route to assigned approver via mail.activity.
            rec._route_submitted_to_approver()

    def action_approve(self, comment=''):
        # SoD: gate before any state change so the audit trail stays clean.
        self._check_sod(_("approve"))
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
        self._check_sod(_("release"))
        for rec in self:
            rec._check_transition(
                ('implementation',), 'released', 'product_graph_base.group_pg_approver',
            )
            # change_kind='obsolete' and 'introduce' don't have a target
            # revision for the affected item itself — the validation
            # only matters for revision_bump / property_change / bom_change.
            unreleased = rec.line_ids.filtered(
                lambda l: l.change_kind in (
                    'revision_bump', 'property_change', 'bom_change')
                and (not l.to_revision_id
                     or l.to_revision_id.state != 'released')
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

    # ─── PG-161 — One-click Promote + Express Release ───────────────────────
    def action_promote(self):
        """One-click advance to the next valid ECO state. Reuses existing
        transitions + guards (no SoD or precondition is bypassed)."""
        self.ensure_one()
        PROMOTION_MAP = {
            'draft': 'action_start_analysis',
            'analysis': 'action_submit_for_approval',
            'approval': 'action_approve',
            'implementation': 'action_release',
            'released': None,
            'rejected': None,
        }
        target_method = PROMOTION_MAP.get(self.state)
        if target_method is None:
            raise UserError(_(
                "ECO %s is in %s and cannot be promoted further. "
                "(Released and Rejected are final states; use 'Reset to Draft' "
                "if you need to re-open from analysis.)"
            ) % (
                self.display_name,
                dict(self._fields['state'].selection).get(self.state),
            ))
        # PG-161 — Surface the line-presence guard that action_start_analysis
        # itself doesn't check, so Promote from draft fails fast when the
        # underlying analysis→approval guard would have caught it.
        if self.state == 'draft' and not self.line_ids:
            raise UserError(_(
                "Add at least one affected-item line before starting analysis."
            ))
        return getattr(self, target_method)()

    def action_express_release(self):
        """draft → released in one click. Admin-only when SoD is enforced;
        respects all data preconditions on each cascaded action."""
        self.ensure_one()
        if self.state == 'released':
            raise UserError(_(
                "ECO %s is already released."
            ) % self.display_name)
        if self.state == 'rejected':
            raise UserError(_(
                "ECO %s is rejected. Reset to draft first."
            ) % self.display_name)
        if self.state != 'draft':
            raise UserError(_(
                "Express Release only applies to Draft ECOs (this ECO is in %s)."
            ) % self.state)
        ICP = self.env['ir.config_parameter'].sudo()
        sod_enforced = str(ICP.get_param('productgraph.enforce_sod', 'True')) == 'True'
        if sod_enforced and not self.env.user.has_group(
            'product_graph_base.group_pg_admin',
        ):
            raise UserError(_(
                "Express Release for an ECO would have you fast-track approval "
                "AND release as the same user, which violates Segregation of "
                "Duties. Either disable SoD in system parameters "
                "(productgraph.enforce_sod=False), or use the staged Promote "
                "button with separate approver/releaser. Admin users may "
                "bypass this gate."
            ))
        # Cascade through states. Each `action_*` mutates self.state in place,
        # so re-checks below pick the next leg up. Any precondition failure
        # mid-chain (e.g. a target revision not yet released) propagates the
        # original UserError/ValidationError and the surrounding transaction
        # rolls back the partial cascade.
        if self.state == 'draft':
            self.action_start_analysis()
        if self.state == 'analysis':
            self.action_submit_for_approval()
        if self.state == 'approval':
            self.with_context(_pg_express_release=True).action_approve(
                comment=_("Express-released by %s.") % self.env.user.name,
            )
        if self.state == 'implementation':
            self.with_context(_pg_express_release=True).action_release()
        self.message_post(
            body=_("ECO express-released by %s.") % self.env.user.name,
        )
        return True

    # ──────────────────────────────────────────────────────────────────
    # PG-130 — Auto-close on all revisions released
    # ──────────────────────────────────────────────────────────────────
    def _check_close_on_all_released(self):
        """Auto-close any ECO in `implementation` whose every governed line
        has reached a released/superseded target revision.

        Called from `pg.revision.action_release()._propagate_release_to_eco`
        whenever a revision flips to released. ICP-gated so admins can
        require manual close. No-op for ECOs not in `implementation` (the
        Bible only allows release from `implementation`, mirroring
        `action_release`'s own transition check).
        """
        ICP = self.env['ir.config_parameter'].sudo()
        if str(ICP.get_param(
            'productgraph.auto_close_eco_on_all_revisions_released', 'True',
        )) != 'True':
            return False
        for rec in self:
            if rec.state != 'implementation':
                continue
            # Filter to the change_kinds that actually carry a to_revision
            # (mirrors pg.eco.action_release()'s own gate).
            governed_lines = rec.line_ids.filtered(
                lambda l: l.change_kind in (
                    'revision_bump', 'property_change', 'bom_change',
                )
            )
            if not governed_lines:
                # Nothing to gate on — don't auto-close (would surprise users
                # whose ECO has only obsolete/introduce lines).
                continue
            unfinished = governed_lines.filtered(
                lambda l: not l.to_revision_id
                or l.to_revision_id.state not in ('released', 'superseded')
            )
            if unfinished:
                continue
            rec._do_auto_close()
        return True

    def _do_auto_close(self):
        """Cascade ECO close: state → released, stamp, audit, chatter, notify.

        SoD is intentionally NOT enforced here — this is a system-triggered
        close (the user who released the last revision already passed SoD on
        the revision). Audit log shows actor as the triggering user.
        """
        self.ensure_one()
        # actual_release_date is readonly; use the same bypass key as
        # action_release uses to stamp it.
        self.with_context(_pg_eco_release_bypass=True).write({
            'state': 'released',
            'actual_release_date': fields.Datetime.now(),
        })
        self.env['pg.audit.log'].sudo()._log(
            self, 'state_change',
            from_state='implementation', to_state='released',
            notes=_("Auto-closed: all affected revisions reached Released."),
        )
        self.message_post(body=_(
            "ECO auto-closed — all affected revisions are released."
        ))
        # Bible §7.3 — reuse the standard release-transition email.
        self._send_transition_email('released')
        # Schedule a follow-up activity for the requestor / engineering lead
        # only when ICP is on (notify_on_auto_advance applies to both the
        # item auto-promotion and the ECO auto-close cascade).
        ICP = self.env['ir.config_parameter'].sudo()
        notify_enabled = str(ICP.get_param(
            'productgraph.notify_on_auto_advance', 'True',
        )) == 'True'
        if notify_enabled:
            assignee = self.engineering_lead_id or self.requested_by_id
            if assignee:
                self.activity_schedule(
                    'mail.mail_activity_data_todo',
                    user_id=assignee.id,
                    summary=_("ECO %s auto-closed") % self.display_name,
                    note=_(
                        "All affected revisions reached Released. Review and "
                        "archive if appropriate."
                    ),
                )
        # PG-162: foreground toast notification to the user who triggered the
        # cascade (the user who released the last revision). _do_auto_close is
        # called from pg.revision.action_release(), so the return value isn't
        # surfaced to the client — bus is the right channel. ICP-gated so the
        # same switch silences both the activity and the toast.
        if notify_enabled:
            trigger_user = self.env.user
            if trigger_user and trigger_user.partner_id:
                try:
                    self.env['bus.bus']._sendone(
                        trigger_user.partner_id,
                        'simple_notification',
                        {
                            'type': 'success',
                            'title': _("ECO Closed"),
                            'message': _(
                                "ECO %s auto-closed — all affected revisions "
                                "released."
                            ) % self.display_name,
                            'sticky': False,
                        },
                    )
                except Exception as e:  # noqa: BLE001
                    _logger.warning(
                        "Could not send ECO auto-close bus notification "
                        "for %s: %s", self.display_name, e,
                    )
        return True

    # ──────────────────────────────────────────────────────────────────
    # PG-134 — Approval routing, SLAs, escalation
    # ──────────────────────────────────────────────────────────────────
    def _route_submitted_to_approver(self):
        """On submit → approval, schedule a Todo activity on the assigned
        approver (engineering_lead_id; falls back to any approver-group user).

        ICP-gated (productgraph.eco_route_on_submit). Reminder-only: never
        auto-approves; humans still drive the state transition.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        if str(ICP.get_param(
            'productgraph.eco_route_on_submit', 'True',
        )) != 'True':
            return False
        approver_group = self.env.ref(
            'product_graph_base.group_pg_approver', raise_if_not_found=False,
        )
        for rec in self:
            assignee = rec.engineering_lead_id
            if not assignee and approver_group:
                # Odoo 19 removed res.groups.users — query the inverse side.
                approvers = self.env['res.users'].search(
                    [('group_ids', 'in', approver_group.id)], limit=1,
                )
                if approvers:
                    assignee = approvers
            if not assignee:
                continue
            rec.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=assignee.id,
                summary=_("ECO %s — your approval requested")
                        % rec.display_name,
                note=_(
                    "ECO submitted by %(submitter)s on %(when)s. Please "
                    "review and approve or reject."
                ) % {
                    'submitter': rec.submitted_by_id.name or '-',
                    'when': rec.submitted_at or '-',
                },
            )
            rec.message_post(
                body=_("Routed to %s for approval.") % assignee.name,
            )
        return True

    @api.model
    def _cron_sla_reminders(self):
        """Daily sweep: remind on approaching Target Release Date, escalate
        stalled items past their target.

        ICP-gated (productgraph.eco_sla_enabled). Scans ECOs in any non-final
        state (analysis / approval / implementation) with a target_release_date.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        if str(ICP.get_param(
            'productgraph.eco_sla_enabled', 'True',
        )) != 'True':
            return False
        today = fields.Date.today()
        # Reminders: window = (today, today+3 days]
        soon = self.search([
            ('state', 'in', ('analysis', 'approval', 'implementation')),
            ('target_release_date', '!=', False),
            ('target_release_date', '>=', today),
            ('target_release_date', '<=', today + timedelta(days=3)),
        ])
        for rec in soon:
            if rec._has_recent_reminder():
                continue
            rec.message_post(body=_(
                '<i class="fa fa-bell"/> %(marker)s Reminder — Target Release '
                'Date %(date)s is approaching.'
            ) % {
                'marker': _SLA_REMINDER_MARKER,
                'date': rec.target_release_date,
            })
        # Escalations: target_release_date already past.
        overdue = self.search([
            ('state', 'in', ('analysis', 'approval', 'implementation')),
            ('target_release_date', '!=', False),
            ('target_release_date', '<', today),
        ])
        for rec in overdue:
            rec._escalate_overdue()
        return True

    def _has_recent_reminder(self):
        """True iff an SLA reminder was posted on this ECO in the last 24h.

        Recognises its own posts via the embedded `_SLA_REMINDER_MARKER`
        string in the chatter body — keeps this round dependency-free
        (no custom mail.subtype seed required).
        """
        self.ensure_one()
        cutoff = fields.Datetime.now() - timedelta(hours=24)
        return bool(self.message_ids.filtered(
            lambda m: m.create_date and m.create_date >= cutoff
            and m.body and _SLA_REMINDER_MARKER in m.body
        ))

    def _escalate_overdue(self):
        """Bump escalation_level (capped at 2) and post a chatter note.

        Reminder-only: no state transitions, no auto-routing to a higher
        authority in this round. Higher-authority routing is the next ticket.
        """
        self.ensure_one()
        if (self.escalation_level or 0) >= 2:
            return False
        new_level = (self.escalation_level or 0) + 1
        self.write({
            'escalation_level': new_level,
            'escalation_stamped_at': fields.Datetime.now(),
        })
        self.message_post(body=_(
            '<i class="fa fa-exclamation-triangle"/> %(marker)s ESCALATED '
            '(level %(level)d) — ECO is past Target Release Date %(date)s.'
        ) % {
            'marker': _SLA_ESCALATION_MARKER,
            'level': new_level,
            'date': self.target_release_date,
        })
        return True
