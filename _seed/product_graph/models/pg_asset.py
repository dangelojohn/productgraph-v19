# -*- coding: utf-8 -*-
"""
pg.asset — Digital Twin stub (Phase 2)

Reserved schema path: pg.item → pg.asset → IoT device.
Phase 1 provides the model and basic fields only.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PgAsset(models.Model):
    _name = 'pg.asset'
    _description = 'ProductGraph Installed Asset (Digital Twin stub)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'
    _rec_name = 'name'

    name = fields.Char(string='Asset Name', required=True, tracking=True)
    serial_number = fields.Char(string='Serial Number', index=True, tracking=True)
    item_id = fields.Many2one(
        'pg.item',
        string='Item',
        required=True,
        ondelete='restrict',
        index=True,
        tracking=True,
    )
    revision_id = fields.Many2one(
        'pg.revision',
        string='Installed Revision',
        domain="[('item_id', '=', item_id), ('state', '=', 'released')]",
        tracking=True,
    )
    state = fields.Selection([
        ('active',      'Active'),
        ('maintenance', 'In Maintenance'),
        ('retired',     'Retired'),
    ], string='Status', default='active', required=True, tracking=True)

    location = fields.Char(string='Location / Site')
    install_date = fields.Date(string='Installation Date')
    retire_date  = fields.Date(string='Retirement Date', readonly=True)
    notes        = fields.Html(string='Notes')
    active       = fields.Boolean(default=True)

    # Phase 2 reserved fields
    iot_device_id   = fields.Char(string='IoT Device ID (Phase 2)', readonly=True)
    last_telemetry  = fields.Datetime(string='Last Telemetry (Phase 2)', readonly=True)

    def action_retire(self):
        for rec in self:
            rec.write({
                'state':        'retired',
                'retire_date':  fields.Date.today(),
                'active':       False,
            })
        return True
