# -*- coding: utf-8 -*-
from odoo import _, fields, models
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

    def write(self, vals):
        raise UserError(_(
            "Vendor qualification records are append-only and cannot be edited."
        ))

    def unlink(self):
        raise UserError(_(
            "Vendor qualification records are append-only and cannot be deleted."
        ))
