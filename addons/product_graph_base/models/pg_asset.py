# -*- coding: utf-8 -*-
"""
pg.asset — Phase 3 stub for the Digital Twin schema path.

Reserves: pg.item → pg.asset → (Phase 4) IoT device → telemetry.

Phase 1 ships the model + minimal views. Phase 3 will inherit
maintenance.equipment from the Odoo Enterprise maintenance module (or its
CE equivalent) and add the full installed-asset registry on top.
"""
from odoo import fields, models, _


class PgAsset(models.Model):
    _name = 'pg.asset'
    _description = 'ProductGraph Installed Asset (Digital Twin stub)'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

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
    # revision_id is added via _inherit in product_graph_revision so pg.asset
    # stays installable without pg.revision being present (Phase 1 — asset is
    # a stub; the full Phase 3 module will deepen this).

    state = fields.Selection(
        [
            ('active', 'Active'),
            ('maintenance', 'In Maintenance'),
            ('retired', 'Retired'),
        ],
        string='Status',
        default='active',
        required=True,
        tracking=True,
    )

    location = fields.Char(string='Location / Site')
    install_date = fields.Date(string='Installation Date')
    retire_date = fields.Date(string='Retirement Date', readonly=True)
    notes = fields.Html(string='Notes')
    active = fields.Boolean(default=True)

    # Phase 4 reserved fields
    iot_device_id = fields.Char(string='IoT Device ID (Phase 4)', readonly=True)
    last_telemetry = fields.Datetime(string='Last Telemetry (Phase 4)', readonly=True)

    def action_retire(self):
        for rec in self:
            rec.write({
                'state': 'retired',
                'retire_date': fields.Date.today(),
                'active': False,
            })
            rec.message_post(body=_("Asset retired by %s.") % self.env.user.name)
        return True
