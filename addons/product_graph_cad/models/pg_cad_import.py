# -*- coding: utf-8 -*-
from odoo import _, fields, models
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
    line_count = fields.Integer(compute='_compute_line_count')
    created_count = fields.Integer(compute='_compute_action_counts')
    updated_count = fields.Integer(compute='_compute_action_counts')
    skipped_count = fields.Integer(compute='_compute_action_counts')

    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    def _compute_action_counts(self):
        for rec in self:
            counts = {'created': 0, 'updated': 0, 'skipped': 0}
            for line in rec.line_ids:
                if line.action in counts:
                    counts[line.action] += 1
            rec.created_count = counts['created']
            rec.updated_count = counts['updated']
            rec.skipped_count = counts['skipped']

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
            ('error', 'Error'),
        ],
        string='Action', required=True,
    )
    item_id = fields.Many2one('pg.item', string='Item', ondelete='set null')
    message = fields.Text(string='Message')
