# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PgCadImport(models.Model):
    _name = 'pg.cad.import'
    _description = 'CAD Import Run'
    _order = 'create_date desc, id desc'

    connector_id = fields.Many2one(
        'pg.cad.connector', string='Connector',
        required=True, ondelete='cascade', index=True,
    )
    state = fields.Selection(
        [
            ('pending', 'Pending'),
            ('running', 'Running'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
        ],
        string='State', default='pending', required=True,
    )
    started_at = fields.Datetime(string='Started At')
    completed_at = fields.Datetime(string='Completed At')
    failure_reason = fields.Text(string='Failure Reason')

    line_ids = fields.One2many('pg.cad.import.line', 'import_id', string='Lines')
    line_count = fields.Integer(compute='_compute_line_count', store=True)
    created_count = fields.Integer(compute='_compute_action_counts', store=True)
    updated_count = fields.Integer(compute='_compute_action_counts', store=True)
    skipped_count = fields.Integer(compute='_compute_action_counts', store=True)
    rejected_count = fields.Integer(compute='_compute_action_counts', store=True)
    error_count = fields.Integer(compute='_compute_action_counts', store=True)

    @api.depends('line_ids')
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    @api.depends('line_ids', 'line_ids.action')
    def _compute_action_counts(self):
        for rec in self:
            counts = {
                'created': 0, 'updated': 0, 'skipped': 0,
                'rejected': 0, 'error': 0,
            }
            for line in rec.line_ids:
                if line.action in counts:
                    counts[line.action] += 1
            rec.created_count = counts['created']
            rec.updated_count = counts['updated']
            rec.skipped_count = counts['skipped']
            rec.rejected_count = counts['rejected']
            rec.error_count = counts['error']

    def write(self, vals):
        # Completed / failed imports are an audit record; freeze them.
        for rec in self:
            if rec.state in ('completed', 'failed') and set(vals.keys()) - {'failure_reason'}:
                raise UserError(_(
                    "CAD imports in state %s are read-only.") % rec.state)
        return super().write(vals)


class PgCadImportLine(models.Model):
    _name = 'pg.cad.import.line'
    _description = 'CAD Import Trace Row'
    _order = 'import_id, id'

    import_id = fields.Many2one(
        'pg.cad.import', string='Import', required=True,
        ondelete='cascade', index=True,
    )
    part_number = fields.Char(string='Part Number', index=True)
    action = fields.Selection(
        [
            ('created', 'Created'),
            ('updated', 'Updated'),
            ('skipped', 'Skipped'),
            ('rejected', 'Rejected'),
            ('error', 'Error'),
        ],
        string='Action', required=True,
    )
    item_id = fields.Many2one('pg.item', string='Item', ondelete='set null')
    message = fields.Text(string='Message')
    reason = fields.Text(
        string='Reason',
        help='Validation error or rejection cause. Set when action is '
             "'rejected' or 'error'.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        # Defense-in-depth: strip any future-introduced target_state-style
        # field the driver might smuggle. We never accept a release target
        # via the import-line layer — CAD sync produces drafts only.
        for vals in vals_list:
            for unsafe in ('target_state', 'state', 'released'):
                vals.pop(unsafe, None)
        return super().create(vals_list)
