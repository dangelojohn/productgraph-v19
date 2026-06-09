# -*- coding: utf-8 -*-
"""
pg.audit.log — Immutable audit trail

Every state change and every attempted write to a released item/revision
is logged here. Records are write-protected and unlink-protected — they
can never be modified or deleted once created (except by pg.admin via raw SQL).
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

    # ------------------------------------------------------------------
    # References
    # ------------------------------------------------------------------
    item_id = fields.Many2one(
        'pg.item',
        string='Item',
        ondelete='set null',
        index=True,
    )
    revision_id = fields.Many2one(
        'pg.revision',
        string='Revision',
        ondelete='set null',
        index=True,
    )

    # ------------------------------------------------------------------
    # Action details
    # ------------------------------------------------------------------
    action = fields.Selection([
        ('state_change', 'State Change'),
        ('release',      'Release to Manufacturing'),
        ('field_write',  'Field Write (Blocked)'),
        ('obsolete',     'Obsolescence'),
        ('ecr_approve',  'ECR Approved'),
        ('ecr_reject',   'ECR Rejected'),
        ('other',        'Other'),
    ], string='Action', required=True)

    from_state  = fields.Char(string='From State')
    to_state    = fields.Char(string='To State')

    # ------------------------------------------------------------------
    # Who / when
    # ------------------------------------------------------------------
    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=True,
        ondelete='restrict',
        index=True,
        default=lambda self: self.env.uid,
    )
    user_name = fields.Char(related='user_id.name', string='User Name', store=True)
    # create_date is automatically set by Odoo ORM

    # ------------------------------------------------------------------
    # Traceability
    # ------------------------------------------------------------------
    triggered_by_model  = fields.Char(string='Triggered By (model)')
    triggered_by_res_id = fields.Integer(string='Triggered By (id)')
    notes = fields.Text(string='Notes')

    # ------------------------------------------------------------------
    # Additional data (JSON blob for extensibility)
    # ------------------------------------------------------------------
    payload = fields.Text(string='Payload (JSON)', readonly=True)

    # ================================================================
    # Computes
    # ================================================================

    @api.depends('action', 'item_id', 'from_state', 'to_state', 'user_id')
    def _compute_name(self):
        for rec in self:
            parts = []
            if rec.item_id:
                parts.append(rec.item_id.item_number or rec.item_id.name)
            parts.append(rec.action or 'log')
            if rec.from_state and rec.to_state:
                parts.append(f'{rec.from_state}→{rec.to_state}')
            rec.name = ' | '.join(parts)

    # ================================================================
    # Immutability
    # ================================================================

    def write(self, vals):
        raise UserError(_(
            "Audit log entries are immutable and cannot be modified.\n"
            "They form a tamper-evident trail of all lifecycle changes."
        ))

    def unlink(self):
        raise UserError(_(
            "Audit log entries cannot be deleted.\n"
            "They form a tamper-evident trail of all lifecycle changes."
        ))
