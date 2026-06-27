# -*- coding: utf-8 -*-
"""
pg.force.release.wizard — Justification gate for `pg.item.action_force_release`.

PG-121: A force release bypasses normal release governance, so we collect a
mandatory written justification and an explicit confirmation tick before
handing off to `pg.item._do_force_release`, which writes the audit entry.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PgForceReleaseWizard(models.TransientModel):
    _name = 'pg.force.release.wizard'
    _description = 'Force Release Justification'

    item_id = fields.Many2one(
        'pg.item', required=True, readonly=True,
        ondelete='cascade',
    )
    reason = fields.Text(
        required=True,
        help='Why is normal release gating being bypassed? '
             'This entry is audited and visible to all stakeholders.',
    )
    confirm = fields.Boolean(
        string='I understand this bypasses normal release governance.',
    )

    @api.constrains('reason')
    def _check_reason_length(self):
        for rec in self:
            if not rec.reason or len(rec.reason.strip()) < 20:
                raise ValidationError(_(
                    "Force-release justification must be at least 20 characters."
                ))

    def action_confirm(self):
        self.ensure_one()
        if not self.confirm:
            raise UserError(_("Tick the confirmation checkbox to proceed."))
        return self.item_id.with_context(
            _pg_force_release_reason=self.reason,
        )._do_force_release()
