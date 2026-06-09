# -*- coding: utf-8 -*-
"""
pg.release.wizard — Pre-flight checks and release launcher

The wizard runs all pre-flight checks, shows any warnings (e.g. open MOs
on the previous revision, incompatibility flag), collects release options,
and then calls pg.release.action_execute_release().
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PgReleaseWizard(models.TransientModel):
    _name = 'pg.release.wizard'
    _description = 'ProductGraph Release Wizard'

    # ------------------------------------------------------------------
    # Links
    # ------------------------------------------------------------------
    item_id = fields.Many2one(
        'pg.item',
        string='Item',
        required=True,
        readonly=True,
    )
    revision_id = fields.Many2one(
        'pg.revision',
        string='Revision to Release',
        required=True,
        readonly=True,
        domain="[('item_id','=',item_id),('state','=','approved')]",
    )

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------
    archive_previous_bom = fields.Boolean(
        string='Archive Previous BOM',
        default=True,
    )
    release_notes = fields.Html(string='Release Notes')
    effective_date = fields.Date(
        string='Effective Date',
        default=fields.Date.today,
    )

    # ------------------------------------------------------------------
    # Pre-flight warning display
    # ------------------------------------------------------------------
    warning_html = fields.Html(
        string='Pre-flight Warnings',
        compute='_compute_warnings',
        readonly=True,
    )
    has_warnings = fields.Boolean(compute='_compute_warnings')
    bom_line_count = fields.Integer(compute='_compute_bom_info', string='BOM Lines')
    previous_bom_name = fields.Char(compute='_compute_bom_info', string='Previous BOM')

    # ================================================================
    # Computes
    # ================================================================

    @api.depends('revision_id')
    def _compute_warnings(self):
        for rec in self:
            warnings = []
            if not rec.revision_id:
                rec.warning_html = False
                rec.has_warnings = False
                continue

            item = rec.revision_id.item_id

            # Check for incompatibility flag
            if rec.revision_id.incompatible_with_previous:
                warnings.append(
                    "⚠️ <strong>Incompatible with previous revision</strong> — "
                    "Manufacturing orders on earlier revisions should be reviewed "
                    "before releasing this revision."
                )

            # Check for open MOs on item
            if item.product_id:
                open_mos = rec.env['mrp.production'].search([
                    ('product_id', '=', item.product_id.id),
                    ('state', 'in', ('draft', 'confirmed', 'progress')),
                ])
                if open_mos:
                    mo_names = ', '.join(open_mos.mapped('name'))
                    warnings.append(
                        f"⚠️ <strong>{len(open_mos)} open Manufacturing Order(s)</strong> "
                        f"reference this item: <em>{mo_names}</em>. "
                        "These MOs will NOT be updated automatically."
                    )

            # Check for missing product on components
            missing_products = []
            for line in rec.revision_id.bom_line_ids:
                if not line.component_item_id.product_id:
                    missing_products.append(
                        f"{line.component_item_id.item_number} — {line.component_item_id.name}"
                    )
            if missing_products:
                items_str = '<br/>'.join(f'• {m}' for m in missing_products)
                warnings.append(
                    f"🔴 <strong>BLOCKING</strong>: The following component items have no "
                    f"linked Odoo product (release them first):<br/>{items_str}"
                )

            if warnings:
                rec.warning_html = '<ul>' + ''.join(f'<li>{w}</li>' for w in warnings) + '</ul>'
                rec.has_warnings = True
            else:
                rec.warning_html = '<p class="text-success">✅ All pre-flight checks passed.</p>'
                rec.has_warnings = False

    @api.depends('revision_id')
    def _compute_bom_info(self):
        for rec in self:
            if rec.revision_id:
                rec.bom_line_count = len(rec.revision_id.bom_line_ids)
                prev = rec.revision_id.item_id.current_revision_id
                rec.previous_bom_name = (
                    prev.mrp_bom_id.name if prev and prev.mrp_bom_id else 'None'
                )
            else:
                rec.bom_line_count = 0
                rec.previous_bom_name = 'None'

    # ================================================================
    # Action
    # ================================================================

    def action_confirm_release(self):
        """Create the pg.release record and execute it."""
        self.ensure_one()

        if not self.env.user.has_group('product_graph.group_pg_release_manager'):
            raise UserError(_("Only Release Managers may execute releases."))

        # Check for blocking warnings (missing component products)
        for line in self.revision_id.bom_line_ids:
            if not line.component_item_id.product_id:
                raise UserError(_(
                    "Cannot release — component %s has no linked Odoo product.",
                    line.component_item_id.display_name_full,
                ))

        # Create the pg.release record
        release = self.env['pg.release'].create({
            'item_id':              self.item_id.id,
            'revision_id':          self.revision_id.id,
            'archive_previous_bom': self.archive_previous_bom,
            'release_notes':        self.release_notes or '',
            'effective_date':       self.effective_date,
        })

        # Execute
        release.action_execute_release()

        return {
            'type': 'ir.actions.act_window',
            'name': _('Release %s') % release.name,
            'res_model': 'pg.release',
            'res_id': release.id,
            'view_mode': 'form',
        }
