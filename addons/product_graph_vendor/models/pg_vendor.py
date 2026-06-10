# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

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
    vendor_part_count = fields.Integer(compute='_compute_counts')
    item_count = fields.Integer(
        compute='_compute_counts',
        help='Distinct pg.items this vendor supplies.',
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
