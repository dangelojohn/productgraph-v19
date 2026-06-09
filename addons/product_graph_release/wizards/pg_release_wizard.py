# -*- coding: utf-8 -*-
"""
pg.release.wizard — Five-step pre-flight + execute wizard.

UX flow (Bible §10.4):
    Step 1  Pre-release validation display (pass/fail per check)
    Step 2  Frozen MO warning if applicable
    Step 3  Release reason input (required)
    Step 4  Confirm + Execute
    Step 5  Success — links to generated mrp.bom

We implement steps 1-4 as a single form with computed warning_html and
required release_reason; step 5 is shown as the result act_window after
``action_confirm_release`` completes.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PgReleaseWizard(models.TransientModel):
    _name = 'pg.release.wizard'
    _description = 'ProductGraph Release Wizard'

    ebom_id = fields.Many2one(
        'pg.ebom',
        string='EBOM to Release',
        required=True,
        domain="[('state','=','released')]",
    )
    revision_id = fields.Many2one(
        related='ebom_id.revision_id', readonly=True, store=False,
    )
    root_item_id = fields.Many2one(
        related='ebom_id.root_item_id', readonly=True, store=False,
    )

    # ─── Options ────────────────────────────────────────────────────────────
    archive_previous_bom = fields.Boolean(
        string='Archive Previous BOM',
        default=True,
    )
    create_product_if_missing = fields.Boolean(
        string='Create Product If Missing',
        default=True,
    )
    effective_date = fields.Date(
        string='Effective Date',
        default=fields.Date.today,
    )
    release_reason = fields.Text(
        string='Release Reason',
        required=True,
        help='Required business justification — captured in the audit log.',
    )

    # ─── Validation display ─────────────────────────────────────────────────
    validation_html = fields.Html(
        string='Pre-Flight Validation',
        compute='_compute_validation',
        readonly=True,
    )
    has_blocking = fields.Boolean(compute='_compute_validation')
    bom_line_count = fields.Integer(
        related='ebom_id.line_count', readonly=True, store=False,
    )
    incompatible_with_previous = fields.Boolean(
        related='ebom_id.revision_id.incompatible_with_previous',
        readonly=True, store=False,
    )

    @api.depends('ebom_id')
    def _compute_validation(self):
        for rec in self:
            if not rec.ebom_id:
                rec.validation_html = False
                rec.has_blocking = False
                continue
            items_blocking = []
            warnings = []
            for line in rec.ebom_id.ebom_line_ids:
                comp = line.pg_item_id
                if comp.state != 'released':
                    items_blocking.append(
                        f'<li>🔴 <strong>{comp.display_name}</strong> '
                        f'is in state <code>{comp.state}</code> (must be released)</li>'
                    )
                elif not comp.product_id:
                    items_blocking.append(
                        f'<li>🟡 <strong>{comp.display_name}</strong> '
                        f'has no linked product — will be created on release</li>'
                    )
            # Frozen MO warning
            if rec.root_item_id.product_id:
                open_mos = rec.env['mrp.production'].search([
                    ('product_id', '=', rec.root_item_id.product_id.id),
                    ('state', 'in', ('draft', 'confirmed', 'progress')),
                ])
                if open_mos:
                    warnings.append(
                        f'<li>⚠️ <strong>{len(open_mos)} open Manufacturing '
                        f'Order(s)</strong> on the previous BOM will be '
                        f'FROZEN per Manufacturing Governance MG-1: '
                        f'<em>{", ".join(open_mos.mapped("name"))}</em></li>'
                    )
            if rec.incompatible_with_previous:
                warnings.append(
                    '<li>⚠️ Revision is marked <strong>Incompatible with '
                    'Previous</strong> — old-revision stock cannot be '
                    'consumed in new MOs. Warehouse and procurement will '
                    'be notified.</li>'
                )

            blocking = bool([x for x in items_blocking if '🔴' in x])
            html_parts = []
            if items_blocking:
                html_parts.append('<h4>Item readiness</h4><ul>' + ''.join(items_blocking) + '</ul>')
            if warnings:
                html_parts.append('<h4>Warnings</h4><ul>' + ''.join(warnings) + '</ul>')
            if not items_blocking and not warnings:
                html_parts.append('<p class="text-success"><strong>✅ All pre-flight checks passed.</strong></p>')

            rec.validation_html = ''.join(html_parts)
            rec.has_blocking = blocking

    # ═════════════════════════════════════════════════════════════════════════
    # Action — create pg.release and execute it
    # ═════════════════════════════════════════════════════════════════════════

    def action_confirm_release(self):
        self.ensure_one()
        if self.has_blocking:
            raise UserError(_(
                "Cannot release while blocking issues remain. Resolve them and retry."
            ))
        if not self.release_reason or len(self.release_reason.strip()) < 5:
            raise UserError(_("Please provide a release reason (min 5 chars)."))

        release = self.env['pg.release'].create({
            'ebom_id': self.ebom_id.id,
            'archive_previous_bom': self.archive_previous_bom,
            'create_product_if_missing': self.create_product_if_missing,
            'effective_date': self.effective_date,
            'release_reason': self.release_reason,
        })
        release.action_execute_release()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Release %s') % release.name,
            'res_model': 'pg.release',
            'res_id': release.id,
            'view_mode': 'form',
            'target': 'current',
        }
