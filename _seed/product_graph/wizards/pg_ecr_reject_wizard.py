# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PgEcrRejectWizard(models.TransientModel):
    _name = 'pg.ecr.reject.wizard'
    _description = 'ECR Rejection Wizard'

    change_request_id = fields.Many2one(
        'pg.change.request',
        string='Change Request',
        required=True,
        readonly=True,
    )
    rejection_reason = fields.Text(
        string='Rejection Reason',
        required=True,
        help='Explain why this change request is being rejected.',
    )

    def action_confirm_reject(self):
        self.ensure_one()
        if not self.rejection_reason or not self.rejection_reason.strip():
            raise UserError(_("A rejection reason is required."))

        cr = self.change_request_id
        cr._check_transition_allowed('rejected')

        cr.write({
            'state':             'rejected',
            'rejection_reason':  self.rejection_reason,
            'rejected_by_id':    self.env.uid,
            'rejected_date':     fields.Datetime.now(),
        })
        cr.message_post(
            body=_("ECR rejected by %(user)s. Reason: %(reason)s",
                   user=self.env.user.name,
                   reason=self.rejection_reason),
            message_type='notification',
        )

        # Log
        self.env['pg.audit.log'].sudo().create({
            'item_id':              cr.item_id.id,
            'action':               'ecr_reject',
            'user_id':              self.env.uid,
            'triggered_by_model':  'pg.change.request',
            'triggered_by_res_id': cr.id,
            'notes':                f'ECR {cr.name} rejected: {self.rejection_reason[:200]}',
        })
        return {'type': 'ir.actions.act_window_close'}
