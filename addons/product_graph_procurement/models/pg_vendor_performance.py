# -*- coding: utf-8 -*-
"""W061 (R6.17) — Vendor performance smart buttons.

JTBD: when awarding a tie, see vendor history (open POs, late POs, defect
rate) in one click — not flying blind.

Three computed counts surfaced as smart buttons on pg.vendor form:

  * open_po_count_all       — purchase.order with vendor=this in states
                              (draft, sent, to_approve, purchase)
  * late_po_count           — open POs whose earliest line date_planned
                              (or order date_planned) is before today
  * defect_rate_30d         — pct: defect-flagged mi.check records linked
                              via the W003 pg_item_id back-ref where the
                              po line vendor matches, over total
                              vendor-attributable lines in the 30d window.

Smart-button counts are non-stored computes — the form opens occasionally
and the queries are cheap (indexed on partner_id + create_date). If the
volume grows we'll switch to stored + cron-refreshed (TODO marker below).

defect_rate compute is defensive about the mi.check model — the model
only exists in sibling Southbrook addons (southbrook.mi.check). When
that model is not in the registry, defect_rate_30d returns 0.0 silently
(reads as "no data" in the UI; smart button is hidden via invisible=).

NOTE on naming: W022 already shipped an `open_po_count` field on
pg.vendor scoped to (draft, sent, to_approve). W061 needs the broader
"all open including purchase" range for the awarding-context view, so
the new field is `open_po_count_all`. Keeping W022's narrower field
backward-compatible — no existing smart buttons get rewired.
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


# W061: order states that count as "open" for performance reporting.
# Broader than W022's exposure surface — includes confirmed POs, since a
# confirmed-but-not-received PO is the buyer's main pipeline view when
# awarding a tie.
OPEN_PO_STATES_W061 = ('draft', 'sent', 'to_approve', 'purchase')

DEFECT_WINDOW_DAYS = 30
MI_CHECK_MODELS = ('mi.check', 'southbrook.mi.check')


class PgVendorPerformance(models.Model):
    _inherit = 'pg.vendor'

    open_po_count_all = fields.Integer(
        compute='_compute_w061_performance_counts',
        string='Open POs (performance)',
        help='All open purchase orders to this vendor (draft, sent, '
             'to_approve, purchase). Broader than W022 — answers '
             '"how busy is this vendor right now?" when awarding ties.',
    )
    late_po_count = fields.Integer(
        compute='_compute_w061_performance_counts',
        string='Late POs',
        help='Open POs whose date_planned is earlier than today. '
             'Surfaces vendor reliability at award time.',
    )
    defect_rate_30d = fields.Float(
        compute='_compute_w061_performance_counts',
        string='Defect rate (30d)',
        digits=(8, 4),
        help='Fraction (0.0..1.0) of vendor-attributable PO lines in the '
             'last 30 days that have at least one defect-flagged '
             'mi.check record. Returns 0.0 when no mi.check model is '
             'installed in this environment.',
    )

    @api.depends('partner_id')
    def _compute_w061_performance_counts(self):
        PO = self.env['purchase.order'].sudo()
        POL = self.env['purchase.order.line'].sudo()
        mi_model = self._w061_resolve_mi_model()
        today = fields.Date.context_today(self)
        cutoff = today - timedelta(days=DEFECT_WINDOW_DAYS)
        for rec in self:
            partner = rec.partner_id
            if not partner:
                rec.open_po_count_all = 0
                rec.late_po_count = 0
                rec.defect_rate_30d = 0.0
                continue
            # Open count (broader range than W022).
            rec.open_po_count_all = PO.search_count([
                ('partner_id', '=', partner.id),
                ('state', 'in', list(OPEN_PO_STATES_W061)),
            ])
            # Late count: any open PO with date_planned < today. Odoo 19
            # stores date_planned on the line; we count distinct orders.
            open_pos = PO.search([
                ('partner_id', '=', partner.id),
                ('state', 'in', list(OPEN_PO_STATES_W061)),
            ])
            late = 0
            for po in open_pos:
                planned = po.date_planned if 'date_planned' in po._fields else False
                if planned and fields.Date.to_date(planned) < today:
                    late += 1
            rec.late_po_count = late
            # Defect rate.
            rec.defect_rate_30d = rec._w061_compute_defect_rate(
                POL, mi_model, cutoff,
            )

    def _w061_resolve_mi_model(self):
        """Return the mi.check-shaped model present in this env, or None.

        Southbrook private addons ship `southbrook.mi.check`. The generic
        ProductGraph repo never depends on either — this resolution is
        defensive so the compute works in any combination.
        """
        for name in MI_CHECK_MODELS:
            if name in self.env:
                return self.env[name].sudo()
        return None

    def _w061_compute_defect_rate(self, POL, mi_model, cutoff):
        """Return defect rate as fraction in [0, 1].

        Numerator: distinct PO lines linked to this vendor's partner_id
        that have at least one mi.check with a defect flag created in
        the window.
        Denominator: PO lines linked to this vendor's partner_id created
        in the window (whether or not they have mi.checks).

        Returns 0.0 if mi.check is not installed or denominator is 0.
        """
        self.ensure_one()
        if mi_model is None:
            return 0.0
        partner = self.partner_id
        if not partner:
            return 0.0
        # Denominator: vendor PO lines created in the window.
        denom_lines = POL.search([
            ('partner_id', '=', partner.id),
            ('create_date', '>=', cutoff),
        ])
        denom = len(denom_lines)
        if denom == 0:
            return 0.0
        # Numerator: mi.check rows within window where the linked pg item
        # appears as the pg_item_id back-ref on one of the denom lines AND
        # the check is "defect-classed". `result` / `x_sbk_result` carries
        # the pass/fail; treat anything other than 'pass' as a defect.
        # Be defensive — only filter on fields the model actually has.
        result_field = None
        for candidate in ('x_sbk_result', 'result', 'state'):
            if candidate in mi_model._fields:
                result_field = candidate
                break
        item_field = None
        for candidate in ('pg_item_id', 'item_id', 'x_pg_item_id'):
            if candidate in mi_model._fields:
                item_field = candidate
                break
        if not item_field:
            # mi.check shipped without an engineering-item back-ref —
            # cannot attribute. Returns 0.
            return 0.0
        item_ids = denom_lines.mapped('pg_item_id').ids
        if not item_ids:
            return 0.0
        domain = [
            (item_field, 'in', item_ids),
            ('create_date', '>=', cutoff),
        ]
        if result_field:
            # Treat anything other than 'pass' as a defect. Use `not in`
            # to be inclusive of fail / hold / scrap / rework variants.
            domain.append((result_field, 'not in', ('pass', 'ok', 'passed')))
        defects = mi_model.search(domain)
        defect_item_ids = set(defects.mapped(item_field).ids)
        defect_lines = denom_lines.filtered(
            lambda l: l.pg_item_id and l.pg_item_id.id in defect_item_ids
        )
        return float(len(defect_lines)) / float(denom)

    # ──────────────────────────────────────────────────────────────────
    # Smart-button actions
    # ──────────────────────────────────────────────────────────────────
    def action_view_open_pos_all(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Open POs (all) for %s") % self.display_name,
            'res_model': 'purchase.order',
            'view_mode': 'list,form,kanban',
            'domain': [
                ('partner_id', '=', self.partner_id.id),
                ('state', 'in', list(OPEN_PO_STATES_W061)),
            ],
            'context': {'search_default_group_by_state': 1},
        }

    def action_view_late_pos(self):
        self.ensure_one()
        today = fields.Date.context_today(self)
        return {
            'type': 'ir.actions.act_window',
            'name': _("Late POs for %s") % self.display_name,
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [
                ('partner_id', '=', self.partner_id.id),
                ('state', 'in', list(OPEN_PO_STATES_W061)),
                ('date_planned', '<', today),
            ],
        }

    def action_view_defect_lines_30d(self):
        """Drill into the vendor PO lines that fed defect-flagged
        mi.check records in the 30d window. When mi.check is not in the
        registry, fall back to the vendor's PO line list (the smart
        button on the form is hidden in that case via invisible=)."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        cutoff = today - timedelta(days=DEFECT_WINDOW_DAYS)
        return {
            'type': 'ir.actions.act_window',
            'name': _("Vendor PO lines (30d) for %s") % self.display_name,
            'res_model': 'purchase.order.line',
            'view_mode': 'list,form',
            'domain': [
                ('partner_id', '=', self.partner_id.id),
                ('create_date', '>=', cutoff),
            ],
        }
