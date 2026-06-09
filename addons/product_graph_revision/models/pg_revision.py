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
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'item_id, sequence, id'
    _rec_name = 'display_name'

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

    # ─── Misc ───────────────────────────────────────────────────────────────
    description = fields.Html(string='Description / Notes')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(related='item_id.company_id', store=True, readonly=True)

    _sql_constraints = [
        ('item_revision_code_unique', 'UNIQUE(item_id, name)',
         'Revision code must be unique per item.'),
    ]

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

    @staticmethod
    def _group_expand_states(states, domain, order):
        return [s[0] for s in REVISION_STATES]

    # ═════════════════════════════════════════════════════════════════════════
    # ORM overrides
    # ═════════════════════════════════════════════════════════════════════════

    def write(self, vals):
        bypass = self.env.context.get('_pg_release_bypass')
        for rec in self:
            if rec.state in ('released', 'superseded', 'obsolete') and not bypass:
                allowed = _MUTABLE_ON_LOCKED
                blocked = set(vals.keys()) - allowed
                if blocked:
                    raise UserError(_(
                        "Revision %(name)s is %(state)s and immutable.\n"
                        "Blocked fields: %(fields)s.\n"
                        "Create a new revision to propose changes.",
                        name=rec.display_name,
                        state=rec.state,
                        fields=', '.join(sorted(blocked)),
                    ))
            # Bypass-only fields require the bypass even on draft/review revisions.
            if not bypass:
                bypass_attempt = set(vals.keys()) & _BYPASS_ONLY_FIELDS
                if bypass_attempt:
                    raise UserError(_(
                        "Fields %(fields)s on revision %(name)s can only be "
                        "written by the release flow (pg.release.action_execute_release).",
                        fields=', '.join(sorted(bypass_attempt)),
                        name=rec.display_name,
                    ))
        return super().write(vals)

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

    # ═════════════════════════════════════════════════════════════════════════
    # Transition helpers
    # ═════════════════════════════════════════════════════════════════════════

    def _check_transition_allowed(self, target_state):
        self.ensure_one()
        allowed = _REVISION_TRANSITIONS.get(self.state, [])
        if target_state not in allowed:
            raise UserError(_(
                "Cannot move revision %(name)s from '%(from)s' to '%(to)s'.\n"
                "Allowed: %(allowed)s",
                name=self.display_name,
                **{'from': self.state},
                to=target_state,
                allowed=', '.join(allowed) or _('none — terminal state'),
            ))

    def _log_state_change(self, from_state, to_state, notes=None, triggered_by=None):
        self.env['pg.audit.log']._log(
            self,
            action='state_change',
            from_state=from_state,
            to_state=to_state,
            notes=notes,
            triggered_by=triggered_by,
        )

    # ═════════════════════════════════════════════════════════════════════════
    # Action buttons
    # ═════════════════════════════════════════════════════════════════════════

    def action_submit_for_review(self):
        if not self.env.user.has_group('product_graph_base.group_pg_engineer'):
            raise UserError(_("Only Engineers may submit revisions for review."))
        for rec in self:
            rec._check_transition_allowed('review')
            rec._check_submission_requirements()
            old = rec.state
            rec.write({'state': 'review'})
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
        return True

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
            self.message_subscribe(partner_ids=approver_group.users.partner_id.ids)

    def _notify_manufacturing(self):
        mfg_group = self.env.ref(
            'product_graph_base.group_pg_mfg', raise_if_not_found=False,
        )
        if mfg_group:
            self.message_subscribe(partner_ids=mfg_group.users.partner_id.ids)

    # ═════════════════════════════════════════════════════════════════════════
    # UI actions
    # ═════════════════════════════════════════════════════════════════════════

    def action_view_documents(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Documents for %s') % self.display_name,
            'res_model': 'pg.document',
            'view_mode': 'list,form',
            'domain': [('revision_id', '=', self.id)],
            'context': {'default_revision_id': self.id},
        }
