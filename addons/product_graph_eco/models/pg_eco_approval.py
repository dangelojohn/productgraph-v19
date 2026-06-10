# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class PgEcoApproval(models.Model):
    """Append-only approval audit row. Created by ``pg.eco.action_approve`` /
    ``action_reject``. Never edited or deleted after creation — the model is
    the legal record that a named user signed off on (or rejected) the change.
    """
    _name = 'pg.eco.approval'
    _description = 'ECO Approval Decision'
    _order = 'create_date desc, id desc'

    eco_id = fields.Many2one(
        'pg.eco', string='ECO', required=True, ondelete='cascade', index=True,
    )
    user_id = fields.Many2one(
        'res.users', string='Signer', required=True, ondelete='restrict',
    )
    decision = fields.Selection(
        [
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        string='Decision',
        required=True,
    )
    signed_on = fields.Datetime(
        string='Signed On', required=True, default=fields.Datetime.now,
    )
    comment = fields.Text(string='Comment')

    def write(self, vals):
        raise UserError(_("Approval records are append-only and cannot be edited."))

    def unlink(self):
        raise UserError(_("Approval records are append-only and cannot be deleted."))
