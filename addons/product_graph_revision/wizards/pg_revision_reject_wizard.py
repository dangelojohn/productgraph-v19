# -*- coding: utf-8 -*-
"""
pg.revision.reject.wizard — Capture rejection reason when an Approver
rejects a revision from `review` back to `draft`.
"""
from odoo import api, fields, models, _


class PgRevisionRejectWizard(models.TransientModel):
    _name = 'pg.revision.reject.wizard'
    _description = 'ProductGraph Revision Rejection Wizard'

    revision_id = fields.Many2one(
        'pg.revision',
        string='Revision',
        required=True,
        readonly=True,
    )
    reason = fields.Text(
        string='Rejection Reason',
        required=True,
        help='Minimum 5 characters. Will be recorded on the revision and in the audit log.',
    )

    def action_reject(self):
        self.ensure_one()
        self.revision_id._reject_with_reason(self.reason)
        return {'type': 'ir.actions.act_window_close'}
