# -*- coding: utf-8 -*-
"""
pg.revision — Revision lifecycle

States: draft → in_review → approved → released → obsolete

Immutability
------------
- 'released' revisions: only a whitelist of admin fields may be written.
- 'approved' revisions: only review-coordination fields may be written.
- mrp_bom_id is set via raw SQL in PgRelease to bypass immutability.
"""
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_REVISION_TRANSITIONS = {
    'draft':     ['in_review', 'obsolete'],
    'in_review': ['approved', 'draft'],
    'approved':  ['released', 'in_review'],
    'released':  ['obsolete'],
    'obsolete':  [],
}

_MUTABLE_ON_APPROVED = frozenset({
    'state', 'active',
    'message_follower_ids', 'message_ids', 'activity_ids',
    'approved_by_id', 'approved_date',
})

_MUTABLE_ON_RELEASED = frozenset({
    'state', 'active', 'mrp_bom_id', 'released_date', 'released_by_id',
    'obsolete_date',
    'message_follower_ids', 'message_ids', 'activity_ids',
})


class PgRevision(models.Model):
    _name = 'pg.revision'
    _description = 'ProductGraph Item Revision'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'item_id asc, sequence asc'
    _rec_name = 'display_name_full'

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    item_id = fields.Many2one(
        'pg.item',
        string='Item',
        required=True,
        ondelete='cascade',
        index=True,
        tracking=True,
    )
    revision_code = fields.Char(
        string='Revision',
        required=True,
        copy=False,
        tracking=True,
    )
    sequence = fields.Integer(
        string='Sequence',
        default=10,
        help='Determines revision ordering within an item.',
    )
    display_name_full = fields.Char(
        string='Full Name',
        compute='_compute_display_name_full',
        store=True,
    )
    description = fields.Html(string='Revision Description / Change Summary')

    # ------------------------------------------------------------------
    # Lifecycle state
    # ------------------------------------------------------------------
    state = fields.Selection([
        ('draft',     'Draft'),
        ('in_review', 'In Review'),
        ('approved',  'Approved'),
        ('released',  'Released'),
        ('obsolete',  'Obsolete'),
    ], string='Status', default='draft', required=True,
       copy=False, index=True, tracking=True,
       group_expand='_group_expand_states',
    )

    # ------------------------------------------------------------------
    # BOM data
    # ------------------------------------------------------------------
    bom_line_ids = fields.One2many(
        'pg.bom.line',
        'revision_id',
        string='Engineering BOM Lines',
        copy=True,
    )
    bom_line_count = fields.Integer(compute='_compute_bom_line_count', string='Components')

    # ------------------------------------------------------------------
    # Manufacturing linkage (set by PgRelease — immutable after that)
    # ------------------------------------------------------------------
    mrp_bom_id = fields.Many2one(
        'mrp.bom',
        string='Released mrp.bom',
        readonly=True,
        copy=False,
        index=True,
        help='Set automatically during release. Never modify directly.',
    )

    # ------------------------------------------------------------------
    # Review / approval trail
    # ------------------------------------------------------------------
    reviewer_id = fields.Many2one(
        'res.users',
        string='Reviewer',
        index=True,
        tracking=True,
    )
    approver_id = fields.Many2one(
        'res.users',
        string='Approver',
        index=True,
        tracking=True,
    )
    approved_by_id = fields.Many2one(
        'res.users',
        string='Approved By',
        readonly=True,
        copy=False,
    )
    approved_date = fields.Datetime(string='Approved At', readonly=True, copy=False)
    released_by_id = fields.Many2one(
        'res.users',
        string='Released By',
        readonly=True,
        copy=False,
    )
    released_date = fields.Datetime(string='Released At', readonly=True, copy=False)
    obsolete_date = fields.Datetime(string='Obsoleted At', readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Traceability
    # ------------------------------------------------------------------
    parent_revision_id = fields.Many2one(
        'pg.revision',
        string='Based On Revision',
        copy=False,
        domain="[('item_id', '=', item_id)]",
    )
    change_request_id = fields.Many2one(
        'pg.change.request',
        string='Originating ECR',
        readonly=True,
        copy=False,
        index=True,
    )
    incompatible_with_previous = fields.Boolean(
        string='Incompatible with Previous Rev',
        default=False,
        tracking=True,
        help='If set, manufacturing orders on earlier revisions should be updated before '
             'this revision can be used.',
    )

    # ------------------------------------------------------------------
    # Document attachments
    # ------------------------------------------------------------------
    document_ids = fields.Many2many(
        'ir.attachment',
        'pg_revision_attachment_rel',
        'revision_id', 'attachment_id',
        string='Documents',
    )
    document_count = fields.Integer(compute='_compute_doc_count', string='Documents')

    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('item_rev_unique', 'UNIQUE(item_id, revision_code)',
         'Revision code must be unique per item.'),
    ]

    # ================================================================
    # Computes
    # ================================================================

    @api.depends('item_id.item_number', 'item_id.name', 'revision_code')
    def _compute_display_name_full(self):
        for rec in self:
            item = rec.item_id
            prefix = f'[{item.item_number}] {item.name}' if item else '?'
            rec.display_name_full = f'{prefix} Rev {rec.revision_code}' if rec.revision_code else prefix

    @api.depends('bom_line_ids')
    def _compute_bom_line_count(self):
        for rec in self:
            rec.bom_line_count = len(rec.bom_line_ids)

    def _compute_doc_count(self):
        for rec in self:
            rec.document_count = len(rec.document_ids)

    @staticmethod
    def _group_expand_states(states, domain, order):
        return ['draft', 'in_review', 'approved', 'released', 'obsolete']

    # ================================================================
    # ORM overrides — immutability enforcement
    # ================================================================

    def write(self, vals):
        for rec in self:
            if rec.state == 'approved':
                disallowed = set(vals.keys()) - _MUTABLE_ON_APPROVED
                if disallowed:
                    raise UserError(_(
                        "Revision %(name)s is Approved — fields %(fields)s are locked.\n"
                        "Submit for review again if changes are needed.",
                        name=rec.display_name_full,
                        fields=', '.join(sorted(disallowed)),
                    ))
            elif rec.state == 'released':
                disallowed = set(vals.keys()) - _MUTABLE_ON_RELEASED
                if disallowed:
                    raise UserError(_(
                        "Revision %(name)s is Released and immutable.\n"
                        "Fields %(fields)s cannot be changed directly.\n"
                        "Please create an Engineering Change Request (ECR).",
                        name=rec.display_name_full,
                        fields=', '.join(sorted(disallowed)),
                    ))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state not in ('draft', 'obsolete'):
                raise UserError(_(
                    "Revision %s cannot be deleted in state '%s'.\n"
                    "Only Draft or Obsolete revisions may be deleted.",
                    rec.display_name_full, rec.state,
                ))
        return super().unlink()

    # ================================================================
    # State transitions
    # ================================================================

    def _check_transition_allowed(self, target_state):
        allowed = _REVISION_TRANSITIONS.get(self.state, [])
        if target_state not in allowed:
            raise UserError(_(
                "Cannot move revision %(name)s from '%(from)s' to '%(to)s'.\n"
                "Allowed: %(allowed)s",
                name=self.display_name_full,
                **{'from': self.state},
                to=target_state,
                allowed=', '.join(allowed) if allowed else 'none',
            ))

    def action_submit_for_review(self):
        for rec in self:
            rec._check_transition_allowed('in_review')
            if not rec.bom_line_ids and rec.item_id.item_type == 'manufactured':
                raise UserError(_(
                    "Revision %s has no BOM lines. Add components before submitting.",
                    rec.display_name_full,
                ))
            rec.write({'state': 'in_review'})
            rec.message_post(
                body=_("Revision submitted for review."),
                message_type='notification',
            )
        return True

    def action_return_to_draft(self):
        for rec in self:
            rec._check_transition_allowed('draft')
            rec.write({'state': 'draft'})
        return True

    def action_approve(self):
        for rec in self:
            rec._check_transition_allowed('approved')
            if not self.env.user.has_group('product_graph.group_pg_approver'):
                raise UserError(_("Only Approvers may approve revisions."))
            rec.write({
                'state': 'approved',
                'approved_by_id': self.env.uid,
                'approved_date': fields.Datetime.now(),
            })
            rec.message_post(
                body=_("Revision approved by %s.") % self.env.user.name,
                message_type='notification',
            )
        return True

    def action_reject_to_review(self):
        for rec in self:
            rec._check_transition_allowed('in_review')
            rec.write({'state': 'in_review'})
        return True

    def action_release(self):
        """Open the release wizard — the wizard calls action_execute_release()."""
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_("Only Approved revisions can be released."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Release Revision %s') % self.display_name_full,
            'res_model': 'pg.release.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_revision_id': self.id,
                'default_item_id': self.item_id.id,
            },
        }

    def _force_obsolete_from_item(self):
        """Called by pg.item when the item is obsoleted — bypasses transition check."""
        self.write({
            'state': 'obsolete',
            'obsolete_date': fields.Datetime.now(),
        })
        self.message_post(
            body=_("Revision obsoleted automatically when parent item was obsoleted."),
            message_type='notification',
        )
