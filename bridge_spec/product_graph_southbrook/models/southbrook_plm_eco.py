# -*- coding: utf-8 -*-
"""
Extend `southbrook_plm.eco` to optionally drive a ProductGraph release on close.

NOTE — model name and closure method name are GUESSED. Adjust to match the
actual Southbrook PLM addon (see bridge_spec/SPEC.md §"Open questions").

This file is the ONLY place ProductGraph and southbrook_plm meet.
ProductGraph addons never import from here or from southbrook_plm.
"""
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SouthbrookPlmEco(models.Model):
    # TODO: confirm the exact model name. Common candidates:
    #   - southbrook_plm.eco
    #   - southbrook.plm.eco
    #   - southbrook_plm.eco.order
    _inherit = 'southbrook_plm.eco'

    # ─── ProductGraph linkage ──────────────────────────────────────────────
    pg_ebom_id = fields.Many2one(
        'pg.ebom',
        string='Target EBOM',
        domain="[('state','=','released')]",
        help='If set, ECO closure will trigger a pg.release against this EBOM.',
    )
    pg_release_id = fields.Many2one(
        'pg.release',
        string='Triggered ProductGraph Release',
        readonly=True,
        copy=False,
    )
    pg_auto_release = fields.Boolean(
        string='Auto-release on ECO close',
        default=True,
        help='When the ECO closes, create and execute a pg.release '
             'automatically.',
    )
    pg_release_reason = fields.Text(
        string='Release Reason',
        compute='_compute_pg_release_reason',
        store=False,
        readonly=False,
        help='Derived from ECO. Editable before ECO close.',
    )

    @api.depends('name', 'title')
    def _compute_pg_release_reason(self):
        for eco in self:
            # TODO: adapt to the actual ECO fields (title, description, etc.)
            eco.pg_release_reason = (
                getattr(eco, 'title', None)
                or getattr(eco, 'name', None)
                or _('Closed via Southbrook ECO bridge.')
            )

    # ─── Hook into ECO closure ─────────────────────────────────────────────

    def action_close(self):
        """Trigger a pg.release after the Southbrook close logic runs.

        TODO: confirm `action_close` is the actual close method on the
        Southbrook ECO model. If not, override the correct one.
        """
        result = super().action_close()
        for eco in self:
            if not (eco.pg_auto_release and eco.pg_ebom_id and not eco.pg_release_id):
                continue
            try:
                release = self.env['pg.release'].create({
                    'ebom_id': eco.pg_ebom_id.id,
                    'release_reason': (
                        eco.pg_release_reason
                        or f'Southbrook ECO {getattr(eco, "name", eco.id)}'
                    ),
                })
                release.action_execute_release()
                eco.write({'pg_release_id': release.id})
                eco.message_post(body=_(
                    "ProductGraph release <b>%s</b> executed on ECO closure.",
                    release.name,
                ))
                _logger.info(
                    'Bridge: ECO %s → pg.release %s executed',
                    getattr(eco, 'name', eco.id), release.name,
                )
            except Exception as e:
                _logger.exception(
                    'Bridge: ECO %s release failed', getattr(eco, 'name', eco.id),
                )
                eco.message_post(body=_(
                    "<strong>ProductGraph release failed</strong>: %s. "
                    "ECO remains closed. Investigate and re-trigger manually.",
                    e,
                ))
        return result
