# -*- coding: utf-8 -*-
"""
pg.audit.log — Append-only audit trail for ProductGraph.

Every state transition, every blocked write on an immutable record, and every
release event writes a row here. Rows are write-protected and unlink-protected
at the ORM level — once recorded they cannot be modified or deleted.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PgAuditLog(models.Model):
    _name = 'pg.audit.log'
    _description = 'ProductGraph Audit Log'
    _order = 'create_date desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Log Entry',
        compute='_compute_name',
        store=True,
    )

    # References — kept as plain integers + model to survive unlink of source
    res_model = fields.Char(string='Source Model', required=True, index=True)
    res_id = fields.Integer(string='Source Record ID', required=True, index=True)
    res_name = fields.Char(string='Source Record Name')

    action = fields.Selection(
        [
            ('state_change', 'State Change'),
            ('release', 'Release to Manufacturing'),
            ('field_write_blocked', 'Field Write Blocked'),
            ('obsolete', 'Obsolescence'),
            ('property_update', 'Property Value Update'),
            ('document_upload', 'Document Upload'),
            ('mo_freeze', 'Manufacturing Order Frozen'),
            ('other', 'Other'),
        ],
        string='Action',
        required=True,
        index=True,
    )

    from_state = fields.Char(string='From State')
    to_state = fields.Char(string='To State')

    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=True,
        ondelete='restrict',
        index=True,
        default=lambda self: self.env.uid,
    )
    user_name = fields.Char(related='user_id.name', string='User Name', store=True)

    ip_address = fields.Char(string='IP Address')
    session_id = fields.Char(string='Session ID')

    triggered_by_model = fields.Char(string='Triggered By (Model)')
    triggered_by_res_id = fields.Integer(string='Triggered By (ID)')

    notes = fields.Text(string='Notes')
    payload = fields.Text(string='Payload (JSON)', readonly=True)

    @api.depends('action', 'res_name', 'from_state', 'to_state')
    def _compute_name(self):
        for rec in self:
            parts = []
            if rec.res_name:
                parts.append(rec.res_name)
            parts.append(rec.action or 'log')
            if rec.from_state and rec.to_state:
                parts.append(f'{rec.from_state}→{rec.to_state}')
            rec.name = ' | '.join(parts)

    def write(self, vals):
        raise UserError(_(
            "Audit log entries are immutable and cannot be modified.\n"
            "They form a tamper-evident trail of all ProductGraph lifecycle events."
        ))

    def unlink(self):
        raise UserError(_(
            "Audit log entries cannot be deleted.\n"
            "They form a tamper-evident trail of all ProductGraph lifecycle events."
        ))

    @api.model
    def _log(self, record, action, from_state=None, to_state=None,
             notes=None, triggered_by=None, payload=None):
        """Helper for callers — record a single audit event from any pg.* model."""
        vals = {
            'res_model': record._name,
            'res_id': record.id,
            'res_name': record.display_name,
            'action': action,
            'from_state': from_state,
            'to_state': to_state,
            'notes': notes,
        }
        if triggered_by is not None:
            vals['triggered_by_model'] = triggered_by._name
            vals['triggered_by_res_id'] = triggered_by.id
        if payload is not None:
            import json
            vals['payload'] = json.dumps(payload, default=str)
        return self.sudo().create(vals)
