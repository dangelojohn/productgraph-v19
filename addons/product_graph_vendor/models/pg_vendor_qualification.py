# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PgVendorQualification(models.Model):
    """Append-only qualification-decision audit row. Created by every
    ``pg.vendor`` state transition. Never edited or deleted after creation.
    """
    _name = 'pg.vendor.qualification'
    _description = 'Vendor Qualification Decision'
    _order = 'create_date desc, id desc'

    vendor_id = fields.Many2one(
        'pg.vendor', string='Vendor',
        required=True, ondelete='cascade', index=True,
    )
    decision = fields.Selection(
        [
            ('unqualified', 'Unqualified'),
            ('probationary', 'Probationary'),
            ('qualified', 'Qualified'),
            ('suspended', 'Suspended'),
            ('disqualified', 'Disqualified'),
        ],
        string='Decision',
        required=True,
    )
    decided_by_id = fields.Many2one(
        'res.users', string='Decided By',
        required=True, ondelete='restrict',
        default=lambda self: self.env.user,
    )
    decided_on = fields.Datetime(
        string='Decided On', required=True, default=fields.Datetime.now,
    )
    comment = fields.Text(string='Comment')

    # PG-133 — qualification validity window. Optional so legacy rows keep
    # working. The expiry cron only looks at rows with valid_until set.
    valid_from = fields.Date(string='Valid From')
    valid_until = fields.Date(string='Valid Until', index=True)
    is_expired = fields.Boolean(
        compute='_compute_is_expired',
        store=False,
        string='Expired',
        help='True when valid_until is in the past.',
    )

    @api.depends('valid_until')
    def _compute_is_expired(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_expired = bool(rec.valid_until and rec.valid_until < today)

    def write(self, vals):
        raise UserError(_(
            "Vendor qualification records are append-only and cannot be edited."
        ))

    def unlink(self):
        raise UserError(_(
            "Vendor qualification records are append-only and cannot be deleted."
        ))
