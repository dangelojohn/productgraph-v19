# -*- coding: utf-8 -*-
"""
pg.change.request — Engineering Change Request (ECR / ECO)

Formal workflow for proposing, reviewing, approving, and implementing
changes to released items and revisions.

States:  draft → submitted → impact_review → approved → implementing
                  → implemented → closed
         (any state) → rejected → closed
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_CR_TRANSITIONS = {
    'draft':         ['submitted', 'closed'],
    'submitted':     ['impact_review', 'rejected'],
    'impact_review': ['approved', 'rejected', 'submitted'],
    'approved':      ['implementing', 'rejected'],
    'implementing':  ['implemented', 'rejected'],
    'implemented':   ['closed'],
    'rejected':      ['closed'],
    'closed':        [],
}

_PRIORITY = [
    ('0', 'Low'),
    ('1', 'Normal'),
    ('2', 'High'),
    ('3', 'Urgent'),
]


class PgChangeRequest(models.Model):
    _name = 'pg.change.request'
    _description = 'ProductGraph Engineering Change Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'priority desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='ECR Reference',
        required=True,
        copy=False,
        index=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('pg.change.request'),
        tracking=True,
    )
    title = fields.Char(string='Change Title', required=True, tracking=True)

    # ------------------------------------------------------------------
    # Item / revision scope
    # ------------------------------------------------------------------
    item_id = fields.Many2one(
        'pg.item',
        string='Affected Item',
        required=True,
        ondelete='restrict',
        index=True,
        tracking=True,
    )
    affected_revision_ids = fields.Many2many(
        'pg.revision',
        'pg_cr_revision_rel',
        'cr_id', 'revision_id',
        string='Affected Revisions',
        domain="[('item_id', '=', item_id)]",
    )
    new_revision_id = fields.Many2one(
        'pg.revision',
        string='Implementation Revision',
        copy=False,
        domain="[('item_id', '=', item_id)]",
        help='The new revision that implements this change request.',
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    state = fields.Selection([
        ('draft',         'Draft'),
        ('submitted',     'Submitted'),
        ('impact_review', 'Impact Review'),
        ('approved',      'Approved'),
        ('implementing',  'Implementing'),
        ('implemented',   'Implemented'),
        ('rejected',      'Rejected'),
        ('closed',        'Closed'),
    ], string='Status', default='draft', required=True,
       copy=False, index=True, tracking=True,
       group_expand='_group_expand_states',
    )
    priority = fields.Selection(_PRIORITY, string='Priority', default='1', tracking=True)

    # ------------------------------------------------------------------
    # People
    # ------------------------------------------------------------------
    requester_id = fields.Many2one(
        'res.users',
        string='Requested By',
        default=lambda self: self.env.user,
        required=True,
        tracking=True,
    )
    reviewer_id  = fields.Many2one('res.users', string='Assigned Reviewer', tracking=True)
    approver_id  = fields.Many2one('res.users', string='Approver', tracking=True)
    approved_by_id = fields.Many2one('res.users', string='Approved By', readonly=True, copy=False)
    rejected_by_id = fields.Many2one('res.users', string='Rejected By', readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Dates
    # ------------------------------------------------------------------
    submitted_date   = fields.Datetime(string='Submitted', readonly=True, copy=False)
    approved_date    = fields.Datetime(string='Approved',  readonly=True, copy=False)
    rejected_date    = fields.Datetime(string='Rejected',  readonly=True, copy=False)
    target_date      = fields.Date(string='Target Implementation Date', tracking=True)
    implemented_date = fields.Datetime(string='Implemented', readonly=True, copy=False)
    closed_date      = fields.Datetime(string='Closed', readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------
    change_type = fields.Selection([
        ('design',       'Design Change'),
        ('process',      'Process Change'),
        ('material',     'Material / Component Change'),
        ('document',     'Documentation Change'),
        ('safety',       'Safety / Regulatory Change'),
        ('cost',         'Cost Reduction'),
        ('other',        'Other'),
    ], string='Change Type', required=True, default='design', tracking=True)

    description      = fields.Html(string='Problem Statement / Description', required=True)
    proposed_solution = fields.Html(string='Proposed Solution')
    impact_analysis  = fields.Html(string='Impact Analysis')
    rejection_reason = fields.Text(string='Rejection Reason', readonly=True)

    # ------------------------------------------------------------------
    # Related counts
    # ------------------------------------------------------------------
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('name_unique', 'UNIQUE(name)', 'ECR reference must be unique.'),
    ]

    # ================================================================
    # Computes
    # ================================================================

    @staticmethod
    def _group_expand_states(states, domain, order):
        return [
            'draft', 'submitted', 'impact_review', 'approved',
            'implementing', 'implemented', 'rejected', 'closed',
        ]

    # ================================================================
    # Transition helpers
    # ================================================================

    def _check_transition_allowed(self, target_state):
        allowed = _CR_TRANSITIONS.get(self.state, [])
        if target_state not in allowed:
            raise UserError(_(
                "Cannot move ECR %(name)s from '%(from)s' to '%(to)s'.\n"
                "Allowed transitions: %(allowed)s",
                name=self.name,
                **{'from': self.state},
                to=target_state,
                allowed=', '.join(allowed) if allowed else 'none',
            ))

    def action_submit(self):
        for rec in self:
            rec._check_transition_allowed('submitted')
            rec.write({'state': 'submitted', 'submitted_date': fields.Datetime.now()})
            rec.message_post(
                body=_("ECR submitted for review."),
                message_type='notification',
            )
        return True

    def action_start_impact_review(self):
        for rec in self:
            rec._check_transition_allowed('impact_review')
            rec.write({'state': 'impact_review'})
        return True

    def action_approve(self):
        for rec in self:
            rec._check_transition_allowed('approved')
            if not self.env.user.has_group('product_graph.group_pg_approver'):
                raise UserError(_("Only Approvers may approve change requests."))
            rec.write({
                'state': 'approved',
                'approved_by_id': self.env.uid,
                'approved_date': fields.Datetime.now(),
            })
            rec.message_post(
                body=_("ECR approved by %s.") % self.env.user.name,
                message_type='notification',
            )
        return True

    def action_start_implementing(self):
        """Start implementation — optionally create the next revision."""
        for rec in self:
            rec._check_transition_allowed('implementing')
            rec.write({'state': 'implementing'})
            if not rec.new_revision_id:
                next_code = rec.item_id._next_revision_code()
                new_rev = self.env['pg.revision'].create({
                    'item_id':           rec.item_id.id,
                    'revision_code':     next_code,
                    'sequence':          (rec.item_id.revision_ids and
                                         max(rec.item_id.revision_ids.mapped('sequence')) + 10
                                         or 10),
                    'change_request_id': rec.id,
                    'description':       _(
                        "Created automatically from ECR %s: %s", rec.name, rec.title
                    ),
                })
                rec.new_revision_id = new_rev
                rec.message_post(
                    body=_(
                        "New revision <b>%(rev)s</b> created for implementation.",
                        rev=new_rev.revision_code,
                    ),
                    message_type='notification',
                )
        return True

    def action_mark_implemented(self):
        for rec in self:
            rec._check_transition_allowed('implemented')
            if rec.new_revision_id and rec.new_revision_id.state != 'released':
                raise UserError(_(
                    "Implementation revision %s must be in 'Released' state before "
                    "marking the ECR as Implemented.",
                    rec.new_revision_id.display_name_full,
                ))
            rec.write({
                'state': 'implemented',
                'implemented_date': fields.Datetime.now(),
            })
        return True

    def action_close(self):
        for rec in self:
            rec._check_transition_allowed('closed')
            rec.write({'state': 'closed', 'closed_date': fields.Datetime.now()})
        return True

    def action_reject(self):
        """Open reject wizard to capture rejection reason."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reject ECR %s') % self.name,
            'res_model': 'pg.ecr.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_change_request_id': self.id},
        }

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('submitted', 'impact_review'):
                raise UserError(_("Can only reset Submitted or Impact-Review ECRs to Draft."))
            rec.write({'state': 'draft'})
        return True
