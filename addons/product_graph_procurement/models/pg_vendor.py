# -*- coding: utf-8 -*-
"""W022 (R6.3) — vendor suspension exposure surface.

Before this commit a buyer suspending a vendor saw the state change
land cleanly but had no idea what was exposed downstream: open POs?
in-flight requisitions? RFQs in progress? Answering "what's exposed
if I suspend X" took eight clicks across two menus.

This module is the procurement-bridge home for the surface because
it's the place that already depends on both pg.vendor and the
purchase / purchase.requisition stack — putting the wiring here keeps
product_graph_vendor purchase-stack-clean (per its manifest "what
this module does not do").

Three pieces ship:

  1. Smart buttons on pg.vendor:
       open_po_count       — purchase.order in draft / sent / to_approve
       open_requisition_count — purchase.requisition not done/cancel
       open_rfq_line_count  — pg.rfq.line on RFQs not awarded/cancelled
                              that have a bid from this vendor

  2. action_view_open_pos / action_view_open_requisitions /
     action_view_open_rfq_lines — drill-through act_windows.

  3. action_suspend override — on transition into 'suspended',
     fires a mail.activity assigned to the responsible buyer (or the
     first pg approver if no buyer) with the impact summary.

Result: "what's exposed if I suspend X" answered in 2 clicks (open
vendor, look at smart-button row) plus the buyer gets an explicit
activity on their inbox the moment suspension lands.
"""
from odoo import _, api, fields, models


OPEN_PO_STATES = ('draft', 'sent', 'to_approve')
OPEN_REQ_STATES_DEFAULT = ('draft', 'ongoing', 'in_progress')


class PgVendorExposure(models.Model):
    _inherit = 'pg.vendor'

    open_po_count = fields.Integer(
        compute='_compute_open_exposure_counts',
        string='Open POs',
        help='Open purchase orders referencing this vendor.partner_id '
             '(states draft / sent / to_approve). Smart-button counter.',
    )
    open_requisition_count = fields.Integer(
        compute='_compute_open_exposure_counts',
        string='Open Requisitions',
        help='Open purchase requisitions referencing this vendor.partner_id '
             '(not yet done/cancel). Smart-button counter.',
    )
    open_rfq_line_count = fields.Integer(
        compute='_compute_open_exposure_counts',
        string='Open RFQ Lines',
        help='pg.rfq.line records on RFQs not yet awarded/cancelled that '
             'carry at least one bid from this vendor. Smart-button counter.',
    )

    @api.depends('partner_id')
    def _compute_open_exposure_counts(self):
        # Compute defaults to 0 — keeps the field meaningful when
        # partner_id is unset (a probationary vendor mid-onboarding).
        PO = self.env['purchase.order'].sudo()
        Req = self.env['purchase.requisition'].sudo() \
            if 'purchase.requisition' in self.env else None
        Bid = self.env['pg.rfq.bid'].sudo() \
            if 'pg.rfq.bid' in self.env else None
        # Resolve open requisition states defensively — Odoo 19 ships
        # 'draft' / 'ongoing' / 'in_progress' / 'open' / 'done' /
        # 'cancel' across the various purchase_requisition flavors.
        # We treat anything not done/cancel as open.
        for rec in self:
            partner = rec.partner_id
            if not partner:
                rec.open_po_count = 0
                rec.open_requisition_count = 0
            else:
                rec.open_po_count = PO.search_count([
                    ('partner_id', '=', partner.id),
                    ('state', 'in', list(OPEN_PO_STATES)),
                ])
                if Req is not None:
                    rec.open_requisition_count = Req.search_count([
                        ('vendor_id', '=', partner.id),
                        ('state', 'not in', ('done', 'cancel')),
                    ])
                else:
                    rec.open_requisition_count = 0
            if Bid is not None:
                rec.open_rfq_line_count = len(Bid.search([
                    ('vendor_id', '=', rec.id),
                    ('line_id.rfq_state', 'not in',
                     ('awarded', 'cancelled')),
                ]).mapped('line_id'))
            else:
                rec.open_rfq_line_count = 0

    # ──────────────────────────────────────────────────────────────────
    # Smart-button actions
    # ──────────────────────────────────────────────────────────────────
    def action_view_open_pos(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Open POs for %s") % self.display_name,
            'res_model': 'purchase.order',
            'view_mode': 'list,form,kanban',
            'domain': [
                ('partner_id', '=', self.partner_id.id),
                ('state', 'in', list(OPEN_PO_STATES)),
            ],
            'context': {'search_default_group_by_state': 1},
        }

    def action_view_open_requisitions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Open Requisitions for %s") % self.display_name,
            'res_model': 'purchase.requisition',
            'view_mode': 'list,form',
            'domain': [
                ('vendor_id', '=', self.partner_id.id),
                ('state', 'not in', ('done', 'cancel')),
            ],
        }

    def action_view_open_rfq_lines(self):
        self.ensure_one()
        Bid = self.env['pg.rfq.bid'].sudo()
        line_ids = Bid.search([
            ('vendor_id', '=', self.id),
            ('line_id.rfq_state', 'not in', ('awarded', 'cancelled')),
        ]).mapped('line_id').ids
        return {
            'type': 'ir.actions.act_window',
            'name': _("Open RFQ Lines bid by %s") % self.display_name,
            'res_model': 'pg.rfq.line',
            'view_mode': 'list,form',
            'domain': [('id', 'in', line_ids)],
        }

    # ──────────────────────────────────────────────────────────────────
    # Suspension activity-fire
    # ──────────────────────────────────────────────────────────────────
    def _w022_build_impact_summary(self):
        """Build a human-readable impact summary for the suspension
        activity. Pulls the three smart-button counts + lists the
        first few PO names so the buyer doesn't need to click through
        before triaging."""
        self.ensure_one()
        po_count = self.open_po_count
        req_count = self.open_requisition_count
        rfq_count = self.open_rfq_line_count
        if not (po_count or req_count or rfq_count):
            return _("No open downstream exposure detected.")
        lines = [_("Vendor suspended. Open exposure snapshot:")]
        if po_count:
            lines.append(_("  - %d open purchase order(s)") % po_count)
        if req_count:
            lines.append(_("  - %d open requisition(s)") % req_count)
        if rfq_count:
            lines.append(_("  - %d open RFQ line(s) with a bid from "
                           "this vendor") % rfq_count)
        # Pull first 5 PO names for inline preview if any.
        if po_count and self.partner_id:
            PO = self.env['purchase.order'].sudo()
            preview = PO.search([
                ('partner_id', '=', self.partner_id.id),
                ('state', 'in', list(OPEN_PO_STATES)),
            ], limit=5).mapped('name')
            if preview:
                lines.append(_("  PO sample: %s") % ", ".join(preview))
        return "\n".join(lines)

    def _w022_resolve_assignee(self):
        """Pick the buyer to assign the impact activity to. Falls back
        to the first pg approver if no responsible buyer."""
        self.ensure_one()
        if self.responsible_id:
            return self.responsible_id
        approver_group = self.env.ref(
            'product_graph_base.group_pg_approver',
            raise_if_not_found=False,
        )
        if approver_group:
            # v19 — query the inverse on res.users.group_ids.
            return self.env['res.users'].sudo().search([
                ('group_ids', 'in', approver_group.id),
            ], limit=1)
        return self.env.user

    def _w022_fire_suspension_activity(self):
        """Create the on-suspend impact-summary mail.activity."""
        self.ensure_one()
        ActivityType = self.env.ref(
            'mail.mail_activity_data_todo', raise_if_not_found=False)
        if not ActivityType:
            return False
        assignee = self._w022_resolve_assignee()
        if not assignee:
            return False
        vendor_model = self.env['ir.model']._get(self._name)
        summary = _("Vendor %s suspended — review open exposure") \
            % self.display_name
        note = self._w022_build_impact_summary()
        return self.env['mail.activity'].sudo().create({
            'res_model_id': vendor_model.id,
            'res_id': self.id,
            'activity_type_id': ActivityType.id,
            'summary': summary,
            'note': note.replace("\n", "<br/>"),
            'user_id': assignee.id,
            'date_deadline': fields.Date.context_today(self),
        })

    def action_suspend(self):
        """Override to fire the W022 impact-summary activity AFTER the
        state transition lands. Wrapping super() in try/except is
        deliberately avoided — if super raises (no comment, wrong
        role, illegal transition), we don't want a spurious activity."""
        # Capture pre-state per record so we only fire for actual
        # unqualified/probationary/qualified -> suspended transitions.
        firing = self.filtered(lambda v: v.state in ('probationary',
                                                      'qualified'))
        result = super().action_suspend()
        for rec in firing:
            if rec.state == 'suspended':
                rec._w022_fire_suspension_activity()
        return result
