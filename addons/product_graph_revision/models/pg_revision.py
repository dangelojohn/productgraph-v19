# -*- coding: utf-8 -*-
"""
pg.revision — A formal, versioned snapshot of a pg.item's design intent.

State machine (per docs/020_State_Machine_Bible.md §2):

    draft → review → released → superseded → obsolete
                     └→ (back to draft on rejection, with reason)

Once a revision is RELEASED it is immutable. The only ways its fields may
change after release are:

1. State transitions (released → superseded, superseded → obsolete).
2. Supersession setting `successor_revision_id` on the predecessor.
3. The controlled `_pg_release_bypass` context flag used by
   ``pg.release.action_execute_release`` to stamp MRP linkage.

Releasing the FIRST revision of an item auto-promotes the parent pg.item
from `engineering` to `released` (Bible §1.1, auto-transition rule).
"""
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


# ─── State machine ───────────────────────────────────────────────────────────

REVISION_STATES = [
    ('draft', 'Draft'),
    ('review', 'Under Review'),
    ('released', 'Released'),
    ('superseded', 'Superseded'),
    ('obsolete', 'Obsolete'),
]

# Authoritative copies live on PgRevision.LIFECYCLE_* class attributes;
# these module-level aliases preserve the legacy attribute path for any
# sibling code that historically reached for them.
_REVISION_TRANSITIONS = {
    'draft':      ['review', 'obsolete'],
    'review':     ['released', 'draft'],
    'released':   ['superseded'],
    'superseded': ['obsolete'],
    'obsolete':   [],
}

# Fields safe to mutate on a released / superseded / obsolete revision
# without the _pg_release_bypass context flag.
_MUTABLE_ON_LOCKED = frozenset({
    'state',
    'successor_revision_id',     # set during supersession
    'active',
    'message_ids', 'message_follower_ids', 'activity_ids',
})

# Additional fields allowed only inside the _pg_release_bypass context
# (called by pg.release.action_execute_release):
_BYPASS_ONLY_FIELDS = frozenset({
    'effective_date', 'released_by_id', 'release_timestamp',
})


class PgRevision(models.Model):
    _name = 'pg.revision'
    _description = 'ProductGraph Item Revision'
    _inherit = ['pg.lifecycle.mixin', 'mail.thread', 'mail.activity.mixin']
    _order = 'item_id, sequence, id'
    _rec_name = 'display_name'

    # ─── Lifecycle mixin config ──────────────────────────────────────────────
    LIFECYCLE_STATES = REVISION_STATES
    LIFECYCLE_TRANSITIONS = _REVISION_TRANSITIONS
    LIFECYCLE_BYPASS_KEY = '_pg_release_bypass'
    LIFECYCLE_MUTABLE_LOCKED = _MUTABLE_ON_LOCKED
    LIFECYCLE_BYPASS_ONLY_FIELDS = _BYPASS_ONLY_FIELDS
    # released, superseded, and obsolete are all immutable. The empty-transition
    # auto-inference would only find 'obsolete', so declare explicitly to
    # match the prior write() guard exactly.
    LIFECYCLE_TERMINAL_STATES = frozenset({'released', 'superseded', 'obsolete'})

    # ─── Identity ────────────────────────────────────────────────────────────
    item_id = fields.Many2one(
        'pg.item',
        string='Item',
        required=True,
        ondelete='restrict',   # never cascade; revisions are historical record
        index=True,
        tracking=True,
    )
    name = fields.Char(
        string='Revision Code',
        required=True,
        copy=False,
        tracking=True,
        help='Human-facing revision code (A, B, C, AA, AB… or 1, 2, 3…).',
    )
    sequence = fields.Integer(
        default=10,
        help='Determines revision ordering within an item.',
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)

    # ─── Lifecycle state ─────────────────────────────────────────────────────
    state = fields.Selection(
        REVISION_STATES,
        string='Status',
        default='draft',
        required=True,
        copy=False,
        index=True,
        tracking=True,
        group_expand='_group_expand_states',
    )

    # ─── Required at release ─────────────────────────────────────────────────
    change_summary = fields.Text(
        string='Change Summary',
        required=True,
        tracking=True,
        help='What changed from the previous revision. Minimum 10 characters; '
             'enforced at submission for review.',
    )

    # ─── Stamped at submit (PG-120 SoD) ──────────────────────────────────────
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
        help='True when the current user is the one who submitted this revision '
             'for review. Used by view xpaths to read-only author-only fields '
             'once the record is under review (SoD).',
    )

    # ─── Stamped at release ──────────────────────────────────────────────────
    effective_date = fields.Date(string='Effective Date', readonly=True, copy=False)
    released_by_id = fields.Many2one(
        'res.users',
        string='Released By',
        readonly=True,
        copy=False,
    )
    release_timestamp = fields.Datetime(string='Released At', readonly=True, copy=False)

    # ─── Lineage ─────────────────────────────────────────────────────────────
    predecessor_revision_id = fields.Many2one(
        'pg.revision',
        string='Based On Revision',
        copy=False,
        domain="[('item_id','=',item_id)]",
        help='The revision this one was derived from. Optional but recommended.',
    )
    successor_revision_id = fields.Many2one(
        'pg.revision',
        string='Superseded By',
        readonly=True,
        copy=False,
        domain="[('item_id','=',item_id),('state','=','released')]",
    )

    # ─── Rejection trail ─────────────────────────────────────────────────────
    rejection_reason = fields.Text(
        string='Rejection Reason',
        readonly=True,
        copy=False,
        help='Required when transitioning review → draft (rejection).',
    )

    # ─── Compatibility flags (Mfg Governance §3.3) ──────────────────────────
    incompatible_with_previous = fields.Boolean(
        string='Incompatible with Previous Revision',
        default=False,
        tracking=True,
        help='If true, prior-revision inventory CANNOT be consumed against this '
             'revision\'s production. Engineering must formally declare this at '
             'release time. See Manufacturing Governance §3.',
    )
    incompatibility_reason = fields.Text(
        string='Incompatibility Reason',
        tracking=True,
    )

    # ─── Property values ─────────────────────────────────────────────────────
    property_value_ids = fields.One2many(
        'pg.property.value',
        'revision_id',
        string='Property Values',
        copy=True,
    )

    # ─── Documents ───────────────────────────────────────────────────────────
    document_ids = fields.One2many(
        'pg.document',
        'revision_id',
        string='Documents',
    )
    document_count = fields.Integer(compute='_compute_document_count', string='# Documents')

    # ─── Lifecycle button gates (PG-105) ─────────────────────────────────────
    # UI-facing mirrors of _check_submission_requirements / _check_release_requirements.
    # The server-side checks remain authoritative; these just hide buttons + show
    # a banner so users see *why* a button isn't actionable before clicking it.
    can_submit_for_review = fields.Boolean(
        compute='_compute_lifecycle_buttons',
        string='Can Submit for Review',
    )
    submit_blocked_reason = fields.Char(
        compute='_compute_lifecycle_buttons',
        string='Submit Block Reason',
    )
    can_release = fields.Boolean(
        compute='_compute_lifecycle_buttons',
        string='Can Release',
    )
    release_blocked_reason = fields.Char(
        compute='_compute_lifecycle_buttons',
        string='Release Block Reason',
    )

    # ─── PG-140 — One-click Promote + Express Release ────────────────────────
    can_promote = fields.Boolean(
        compute='_compute_lifecycle_buttons',
        string='Can Promote',
    )
    promote_blocked_reason = fields.Char(
        compute='_compute_lifecycle_buttons',
        string='Promote Block Reason',
    )
    can_express_release = fields.Boolean(
        compute='_compute_lifecycle_buttons',
        string='Can Express Release',
    )
    express_release_blocked_reason = fields.Char(
        compute='_compute_lifecycle_buttons',
        string='Express Release Block Reason',
    )

    # ─── Misc ───────────────────────────────────────────────────────────────
    description = fields.Html(string='Description / Notes')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(related='item_id.company_id', store=True, readonly=True)

    _item_revision_code_unique = models.Constraint(
        'UNIQUE(item_id, name)',
        'Revision code must be unique per item.',
    )

    # ═════════════════════════════════════════════════════════════════════════
    # Computes
    # ═════════════════════════════════════════════════════════════════════════

    @api.depends('item_id.part_number', 'item_id.name', 'name')
    def _compute_display_name(self):
        for rec in self:
            if rec.item_id and rec.name:
                rec.display_name = f'{rec.item_id.part_number} Rev {rec.name}'
            elif rec.name:
                rec.display_name = f'Rev {rec.name}'
            else:
                rec.display_name = _('New Revision')

    def _compute_document_count(self):
        for rec in self:
            rec.document_count = len(rec.document_ids)

    @api.depends(
        'state', 'item_id', 'item_id.state', 'item_id.active', 'item_id.company_id',
        'change_summary', 'incompatible_with_previous', 'incompatibility_reason',
    )
    def _compute_lifecycle_buttons(self):
        """Mirrors _check_submission_requirements / _check_release_requirements.

        The server-side gates remain authoritative — this compute only powers
        UI visibility (button invisible + reason banner). Keep the two in sync;
        the test suite asserts parity.
        """
        for rec in self:
            # Guard for NewId / un-saved records with no item picked yet.
            if not rec.item_id:
                rec.can_submit_for_review = False
                rec.submit_blocked_reason = _("Pick a parent item first.")
                rec.can_release = False
                rec.release_blocked_reason = _("Pick a parent item first.")
                rec.can_promote = False
                rec.promote_blocked_reason = _("Pick a parent item first.")
                rec.can_express_release = False
                rec.express_release_blocked_reason = _("Pick a parent item first.")
                continue

            # ── Submit-for-review preconditions ─────────────────────────────
            if rec.state != 'draft':
                rec.can_submit_for_review = False
                rec.submit_blocked_reason = _("Revision is not in draft state.")
            elif not rec.item_id.active:
                rec.can_submit_for_review = False
                rec.submit_blocked_reason = _("Parent item is archived.")
            elif rec.item_id.state == 'concept':
                rec.can_submit_for_review = False
                rec.submit_blocked_reason = _(
                    "Parent item is still in Concept — promote to Prototype or "
                    "Engineering before submitting."
                )
            elif not rec.change_summary or len(rec.change_summary.strip()) < 10:
                rec.can_submit_for_review = False
                rec.submit_blocked_reason = _(
                    "Change Summary must be at least 10 characters."
                )
            else:
                rec.can_submit_for_review = True
                rec.submit_blocked_reason = False

            # ── Release preconditions ───────────────────────────────────────
            if rec.state != 'review':
                rec.can_release = False
                rec.release_blocked_reason = _(
                    "Revision must be Under Review before release."
                )
            elif rec.item_id.state not in ('engineering', 'released'):
                rec.can_release = False
                state_label = dict(rec.item_id._fields['state'].selection).get(
                    rec.item_id.state, rec.item_id.state,
                )
                rec.release_blocked_reason = _(
                    "Parent item must be In Engineering or Released — current "
                    "state: %s."
                ) % state_label
            elif rec.incompatible_with_previous and not rec.incompatibility_reason:
                rec.can_release = False
                rec.release_blocked_reason = _(
                    "Incompatibility reason is required when the revision is "
                    "flagged incompatible."
                )
            else:
                # Required-property check mirrors _check_release_requirements.
                missing = []
                for tmpl in rec.item_id.property_template_ids:
                    for field in tmpl.field_ids.filtered(lambda f: f.is_required):
                        value = rec.property_value_ids.filtered(
                            lambda v: v.field_id == field
                        )
                        if not value or not value.display_value:
                            missing.append(f'{tmpl.name}.{field.label}')
                if missing:
                    rec.can_release = False
                    rec.release_blocked_reason = _(
                        "Missing required property values: %s"
                    ) % ', '.join(missing)
                else:
                    rec.can_release = True
                    rec.release_blocked_reason = False

            # ── Promote (PG-140) — reuses existing transition preconditions ─
            if rec.state == 'draft':
                rec.can_promote = rec.can_submit_for_review
                rec.promote_blocked_reason = rec.submit_blocked_reason or False
            elif rec.state == 'review':
                rec.can_promote = rec.can_release
                rec.promote_blocked_reason = rec.release_blocked_reason or False
            elif rec.state == 'superseded':
                rec.can_promote = True
                rec.promote_blocked_reason = False
            else:
                rec.can_promote = False
                rec.promote_blocked_reason = (
                    _("Revision is in final state.")
                    if rec.state == 'obsolete'
                    else _("Promotion not defined for this state.")
                )

            # ── Express Release (PG-140) — draft-only, respects SoD ─────────
            if rec.state != 'draft':
                rec.can_express_release = False
                rec.express_release_blocked_reason = _(
                    "Only available on Draft revisions."
                )
            elif not rec.can_submit_for_review:
                rec.can_express_release = False
                rec.express_release_blocked_reason = rec.submit_blocked_reason
            else:
                ICP = self.env['ir.config_parameter'].sudo()
                sod_enforced = str(ICP.get_param(
                    'productgraph.enforce_sod', 'True',
                )) == 'True'
                is_admin = self.env.user.has_group(
                    'product_graph_base.group_pg_admin',
                )
                if sod_enforced and not is_admin:
                    rec.can_express_release = False
                    rec.express_release_blocked_reason = _(
                        "SoD enforced — submitter and releaser must differ."
                    )
                else:
                    rec.can_express_release = True
                    rec.express_release_blocked_reason = False

    @api.depends('submitted_by_id')
    def _compute_is_self_submitted(self):
        uid = self.env.user.id
        for rec in self:
            rec.is_self_submitted = bool(
                rec.submitted_by_id and rec.submitted_by_id.id == uid
            )

    @staticmethod
    def _group_expand_states(states, domain, order):
        return [s[0] for s in REVISION_STATES]

    # ═════════════════════════════════════════════════════════════════════════
    # ORM overrides
    # ═════════════════════════════════════════════════════════════════════════

    # write() — immutability enforcement (allowlist on locked states +
    # bypass-only field guard) provided by pg.lifecycle.mixin.

    def write(self, vals):
        # B6.5 — 'active' is inside LIFECYCLE_MUTABLE_LOCKED so archiving a
        # revision is always allowed by the mixin's immutability guard,
        # regardless of state. That's correct for the lifecycle machine, but
        # it leaves a cross-model gap: archiving hides the revision from the
        # default (active=True) domains used throughout the ECO flow
        # (release-readiness checks, the revision picker), silently
        # stranding an in-flight ECO with an invisible source/target
        # revision. Guard the archive path specifically; unarchive
        # (active=True) is always safe and left untouched.
        if vals.get('active') is False:
            self._check_no_inflight_eco_references()
        return super().write(vals)

    def _check_no_inflight_eco_references(self):
        """Raise if any revision in self is referenced (as from_revision_id
        or to_revision_id) by a pg.eco.line whose parent ECO has not yet
        reached a terminal state (released / rejected).

        Defensive: `pg.eco.line` may not be installed — product_graph_eco is
        an optional sibling addon. Mirrors the env.get() guard already used
        by _propagate_release_to_eco / _warn_draft_child_eboms above, and
        keeps the module-boundary rule intact (revision never imports eco).
        sudo() because the user archiving the revision may not have direct
        read access to every ECO that references it.
        """
        EcoLine = self.env.get('pg.eco.line')
        if EcoLine is None:
            return
        EcoLine = self.env['pg.eco.line'].sudo()
        for rec in self:
            blocking_line = EcoLine.search([
                '|',
                ('from_revision_id', '=', rec.id),
                ('to_revision_id', '=', rec.id),
                ('eco_state', 'not in', ['released', 'rejected']),
            ], limit=1)
            if blocking_line:
                raise UserError(_(
                    "Cannot archive revision %(name)s — it is referenced by "
                    "in-flight ECO %(eco)s (state: %(state)s). Resolve or "
                    "close that ECO before archiving this revision.",
                    name=rec.display_name,
                    eco=blocking_line.eco_id.display_name,
                    state=blocking_line.eco_state,
                ))

    def unlink(self):
        for rec in self:
            if rec.state not in ('draft', 'obsolete'):
                raise UserError(_(
                    "Revision %(name)s in state '%(state)s' cannot be deleted.\n"
                    "Only Draft revisions may be hard-deleted; obsolete instead "
                    "to preserve the audit trail.",
                    name=rec.display_name,
                    state=rec.state,
                ))
        return super().unlink()

    # _check_transition_allowed / _log_state_change — provided by pg.lifecycle.mixin.

    # ═════════════════════════════════════════════════════════════════════════
    # Action buttons
    # ═════════════════════════════════════════════════════════════════════════

    # ─── PG-120 Segregation of Duties ────────────────────────────────────────
    def _check_sod(self, action_label):
        """Block actor == submitter for governed transitions.

        Bypass via ICP ``productgraph.enforce_sod=False`` (default True).
        Called from action_release on pg.revision; ECO has its own variant
        that also blocks the original requestor.
        """
        # PG-140 — coordinated bypass for action_express_release. That action
        # gates SoD itself (admin-only when SoD is enforced) and consciously
        # submits + releases as the same user, so the standard SoD raise here
        # would block the path it just authorised.
        if self.env.context.get('_pg_express_release'):
            return
        ICP = self.env['ir.config_parameter'].sudo()
        if str(ICP.get_param('productgraph.enforce_sod', 'True')) != 'True':
            return
        uid = self.env.user.id
        for rec in self:
            if rec.submitted_by_id and rec.submitted_by_id.id == uid:
                raise UserError(_(
                    "You submitted revision %(name)s yourself — a different "
                    "user must %(action)s it (Segregation of Duties).",
                    name=rec.display_name,
                    action=action_label,
                ))

    def action_submit_for_review(self):
        if not self.env.user.has_group('product_graph_base.group_pg_engineer'):
            raise UserError(_("Only Engineers may submit revisions for review."))
        for rec in self:
            rec._check_transition_allowed('review')
            rec._check_submission_requirements()
            old = rec.state
            rec.write({
                'state': 'review',
                'submitted_by_id': self.env.user.id,
                'submitted_at': fields.Datetime.now(),
            })
            rec._log_state_change(old, 'review')
            rec.message_post(
                body=_("Revision submitted for review by %s.") % self.env.user.name,
                subtype_xmlid='mail.mt_note',
            )
            rec._notify_approvers()
        return True

    def action_release(self):
        """Review → Released. Approver only. Stamps release metadata and
        auto-releases the parent item if it's still in engineering."""
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise UserError(_("Only Approvers may release revisions."))
        # SoD: gate before any state change so the audit trail stays clean.
        self._check_sod(_("release"))
        for rec in self:
            rec._check_transition_allowed('released')
            rec._check_release_requirements()
            old = rec.state
            # The bypass context flag lets us stamp the release metadata
            # in the same write that flips the state.
            rec.with_context(_pg_release_bypass=True).write({
                'state': 'released',
                'effective_date': fields.Date.today(),
                'released_by_id': self.env.uid,
                'release_timestamp': fields.Datetime.now(),
            })
            rec._log_state_change(
                old, 'released',
                notes=f'Released by {self.env.user.name}',
            )
            rec.message_post(body=_("Revision <b>%s</b> released.") % rec.display_name)
            # Auto-promote parent item if it was still in engineering
            rec.item_id._auto_release_from_revision(rec)
            rec._notify_manufacturing()
            # PG-103 cascade: warn (do NOT auto-release) about draft EBOMs
            # still pointing at this revision. Governance principle: never
            # release MRP-impacting artifacts without explicit user intent.
            rec._warn_draft_child_eboms()
            # PG-130 — propagate release event to any linked ECO line.
            rec._propagate_release_to_eco()
        return True

    def _warn_draft_child_eboms(self):
        """Post a chatter warning if draft EBOMs reference this revision.
        Uses self.env['pg.ebom'] without a Python import to avoid creating a
        cross-module import cycle (product_graph_ebom depends on us)."""
        self.ensure_one()
        EbomModel = self.env.get('pg.ebom')
        if EbomModel is None:
            return  # product_graph_ebom not installed; nothing to cascade.
        draft_eboms = EbomModel.search([
            ('revision_id', '=', self.id),
            ('state', '=', 'draft'),
        ])
        if draft_eboms:
            names = ', '.join(draft_eboms.mapped('display_name'))
            self.message_post(body=_(
                "Revision released. Draft EBOM(s) remain: %s. "
                "Release them via the EBOM action to materialize the MRP BOM."
            ) % names)

    def _propagate_release_to_eco(self):
        """PG-130 — Notify ECO(s) that one of their target revisions has
        reached `released`. Triggers the auto-close check on each linked
        ECO.

        Defensive: `pg.eco.line` may not be installed (product_graph_eco is
        an optional sibling). sudo() because the user releasing the
        revision may not have direct read on every ECO that references it
        — this is a system-side cascade, not a user query.
        """
        if 'pg.eco.line' not in self.env:
            return
        EcoLine = self.env['pg.eco.line'].sudo()
        for rec in self:
            eco_lines = EcoLine.search([('to_revision_id', '=', rec.id)])
            if not eco_lines:
                continue
            ecos = eco_lines.mapped('eco_id')
            ecos._check_close_on_all_released()

    # ─── PG-140 — One-click Promote + Express Release ───────────────────────
    def action_promote(self):
        """One-click advance to the next valid revision state. Reuses existing
        transitions + guards."""
        self.ensure_one()
        PROMOTION_MAP = {
            'draft': 'action_submit_for_review',
            'review': 'action_release',
            'released': None,  # supersede is intentional, not a promotion
            'superseded': 'action_obsolete',
            'obsolete': None,
        }
        target_method = PROMOTION_MAP.get(self.state)
        if target_method is None:
            raise UserError(_(
                "Revision %s cannot be promoted from %s. "
                "(Supersession is explicit, not a promotion; Obsolete is final.)"
            ) % (
                self.display_name,
                dict(self._fields['state'].selection).get(self.state),
            ))
        return getattr(self, target_method)()

    def action_express_release(self):
        """Submit-for-Review + Release in one click. Respects SoD unless
        ICP override or admin role allows the bypass."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_(
                "Express Release only applies to Draft revisions. "
                "This revision is in %s."
            ) % self.state)
        ICP = self.env['ir.config_parameter'].sudo()
        sod_enforced = str(ICP.get_param('productgraph.enforce_sod', 'True')) == 'True'
        if sod_enforced and not self.env.user.has_group(
            'product_graph_base.group_pg_admin',
        ):
            raise UserError(_(
                "Express Release would have you submit AND release the same "
                "revision, which violates Segregation of Duties. Either "
                "disable SoD in system parameters (productgraph.enforce_sod="
                "False), or use the standard Submit + a separate Approver to "
                "Release. Admin users may bypass this gate."
            ))
        # Submit (stamps submitted_by_id to current user).
        self.action_submit_for_review()
        # Release — must dodge the SoD check because we just submitted as
        # this user. The context flag is the coordinated bypass that
        # _check_sod honors; gating already enforced above.
        self.with_context(_pg_express_release=True).action_release()
        self.message_post(body=_("Express-released by %s.") % self.env.user.name)
        return True

    # ─── PG-131 — Revision naming helper ────────────────────────────────────
    @api.model
    def _next_revision_name_after(self, prior_name):
        """Compute the next revision name given a predecessor.

        Rules:
          - None / empty           -> 'A'
          - Numeric string         -> increment ('1' -> '2', '10' -> '11')
          - Single letter A-Y      -> next letter ('A' -> 'B', 'F' -> 'G')
          - Single letter 'Z'      -> 'AA' (rollover)
          - Single letter 'AA'..   -> '<prior>+1' (deferred; caller can
                                      override with a stricter scheme)

        Defer to project-specific naming policy if one exists. This is the
        safe default used by `pg.eco.line.action_spawn_revision`.
        """
        if not prior_name:
            return 'A'
        name = prior_name.strip()
        if name.isdigit():
            return str(int(name) + 1)
        if len(name) == 1 and name.isalpha():
            up = name.upper()
            if up == 'Z':
                return 'AA'
            return chr(ord(up) + 1)
        return f'{name}+1'

    def action_reject_to_draft(self):
        """Approver rejects from review back to draft. Reason required."""
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise UserError(_("Only Approvers may reject revisions."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reject Revision'),
            'res_model': 'pg.revision.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_revision_id': self.id},
        }

    def _reject_with_reason(self, reason):
        self.ensure_one()
        if not reason or len(reason.strip()) < 5:
            raise ValidationError(_("Rejection reason must be at least 5 characters."))
        self._check_transition_allowed('draft')
        old = self.state
        self.write({
            'state': 'draft',
            'rejection_reason': reason,
        })
        self._log_state_change(
            old, 'draft',
            notes=f'Rejected: {reason}',
        )
        self.message_post(body=_("Revision rejected by %s. Reason: %s") % (
            self.env.user.name, reason,
        ))

    def action_supersede(self):
        """Released → Superseded. Requires a released successor revision."""
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise UserError(_("Only Approvers may supersede revisions."))
        for rec in self:
            rec._check_transition_allowed('superseded')
            if not rec.successor_revision_id:
                raise UserError(_(
                    "Cannot supersede revision %(name)s — no successor revision "
                    "is set. Pick the released successor first.",
                    name=rec.display_name,
                ))
            # B6.8 — TOCTOU guard. Without a lock, a concurrent transaction
            # (e.g. another action_supersede/action_obsolete call) could flip
            # the successor's state between the read below and our write,
            # leaving this revision superseded while pointing at a successor
            # that is no longer 'released'. Take a row-level lock on both
            # this revision and its successor (ascending id order keeps lock
            # acquisition consistent across concurrent calls and avoids
            # introducing new deadlock risk) before reading the successor's
            # state, then drop the ORM cache for that field so the check
            # below reads the freshly-locked row rather than a stale cached
            # value. A concurrent transaction attempting to change the
            # successor's state blocks on FOR UPDATE until this transaction
            # commits or rolls back, closing the race window.
            lock_ids = tuple(sorted({rec.id, rec.successor_revision_id.id}))
            self.env.cr.execute(
                "SELECT id FROM pg_revision WHERE id IN %s FOR UPDATE",
                (lock_ids,),
            )
            (rec | rec.successor_revision_id).invalidate_recordset(['state'])
            if rec.successor_revision_id.state != 'released':
                raise UserError(_(
                    "Successor revision %(succ)s must be Released before "
                    "superseding %(name)s.",
                    succ=rec.successor_revision_id.display_name,
                    name=rec.display_name,
                ))
            old = rec.state
            rec.with_context(_pg_release_bypass=True).write({'state': 'superseded'})
            rec._log_state_change(
                old, 'superseded',
                notes=f'Superseded by {rec.successor_revision_id.display_name}',
            )
            rec.message_post(body=_(
                "Revision superseded by <b>%s</b>.",
                rec.successor_revision_id.display_name,
            ))
        return True

    def action_obsolete(self):
        if not self.env.user.has_group('product_graph_base.group_pg_admin'):
            raise UserError(_("Only Administrators may obsolete revisions."))
        for rec in self:
            rec._check_transition_allowed('obsolete')
            old = rec.state
            rec.with_context(_pg_release_bypass=True).write({
                'state': 'obsolete',
                'active': False,
            })
            rec._log_state_change(old, 'obsolete')
            rec.message_post(body=_("Revision obsoleted by %s.") % self.env.user.name)
        return True

    # ═════════════════════════════════════════════════════════════════════════
    # Pre-condition checks
    # ═════════════════════════════════════════════════════════════════════════

    def _check_submission_requirements(self):
        self.ensure_one()
        if not self.change_summary or len(self.change_summary.strip()) < 10:
            raise UserError(_(
                "A change summary of at least 10 characters is required before "
                "submitting revision %(name)s for review.",
                name=self.display_name,
            ))
        if self.item_id.state in ('concept',):
            raise UserError(_(
                "Cannot submit a revision while item %(item)s is in 'Concept' "
                "state. Move the item to Prototype or Engineering first.",
                item=self.item_id.display_name,
            ))

    def _check_release_requirements(self):
        self.ensure_one()
        if self.item_id.state not in ('engineering', 'released'):
            raise UserError(_(
                "Cannot release a revision while the parent item is in '%(state)s' "
                "state. Item must be in Engineering or higher.",
                state=self.item_id.state,
            ))
        if self.incompatible_with_previous and not self.incompatibility_reason:
            raise UserError(_(
                "Revision %(name)s is flagged incompatible with the previous "
                "revision but no incompatibility reason was provided.",
                name=self.display_name,
            ))
        # Required property fields must have values.
        missing = []
        for tmpl in self.item_id.property_template_ids:
            for field in tmpl.field_ids.filtered(lambda f: f.is_required):
                value = self.property_value_ids.filtered(lambda v: v.field_id == field)
                if not value or not value.display_value:
                    missing.append(f'{tmpl.name}.{field.label}')
        if missing:
            raise UserError(_(
                "Revision %(name)s is missing required property values:\n• %(list)s",
                name=self.display_name,
                list='\n• '.join(missing),
            ))

    # ═════════════════════════════════════════════════════════════════════════
    # Notifications
    # ═════════════════════════════════════════════════════════════════════════

    def _notify_approvers(self):
        approver_group = self.env.ref(
            'product_graph_base.group_pg_approver', raise_if_not_found=False,
        )
        if approver_group:
            # Odoo 19 removed res.groups.users — query the inverse side.
            users = self.env['res.users'].search([('group_ids', 'in', approver_group.id)])
            if users:
                self.message_subscribe(partner_ids=users.partner_id.ids)

    def _notify_manufacturing(self):
        mfg_group = self.env.ref(
            'product_graph_base.group_pg_mfg', raise_if_not_found=False,
        )
        if mfg_group:
            users = self.env['res.users'].search([('group_ids', 'in', mfg_group.id)])
            if users:
                self.message_subscribe(partner_ids=users.partner_id.ids)

    # ═════════════════════════════════════════════════════════════════════════
    # UI actions
    # ═════════════════════════════════════════════════════════════════════════

    def action_view_documents(self):
        self.ensure_one()
        documents = self.env['pg.document'].search([('revision_id', '=', self.id)])
        # PG-160: single-document shortcut to the form view.
        if len(documents) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': documents.display_name,
                'res_model': 'pg.document',
                'res_id': documents.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Documents for %s') % self.display_name,
            'res_model': 'pg.document',
            'view_mode': 'list,form',
            'domain': [('revision_id', '=', self.id)],
            'context': {'default_revision_id': self.id},
        }

    # PG-144 — Open the Apply Template wizard scoped to this revision so
    # empty property value rows are created on the revision in one shot.
    def action_open_apply_template_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Apply Template — %s') % self.display_name,
            'res_model': 'pg.apply.template.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_item_id': self.item_id.id,
                'default_revision_id': self.id,
            },
        }
