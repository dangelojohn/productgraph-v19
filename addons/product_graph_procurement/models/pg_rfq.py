# -*- coding: utf-8 -*-
"""Hook the RFQ award path to create a purchase.requisition."""
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PgRfq(models.Model):
    _inherit = 'pg.rfq'

    auto_create_requisition = fields.Boolean(
        string='Create Purchase Requisition on Award',
        default=True,
        help='When awarded, generate a purchase.requisition with one line '
             'per RFQ line carrying the winning bid economics. The '
             'requisition is the handoff to procurement; they convert it '
             'into POs.',
    )
    requisition_id = fields.Many2one(
        'purchase.requisition',
        string='Purchase Requisition',
        readonly=True,
        copy=False,
        help='Populated by action_award when auto_create_requisition is set.',
    )

    # ──────────────────────────────────────────────────────────────────
    # Override action_award to attach a requisition write
    # ──────────────────────────────────────────────────────────────────
    def action_award(self):
        # Run the canonical award flow first — writes pg.vendor.part
        # economics, flips state, etc.
        super().action_award()
        # Then create / refresh the requisition.
        for rec in self:
            if rec.auto_create_requisition and not rec.requisition_id:
                rec._create_requisition()
        return True

    def _create_requisition(self):
        """Create a purchase.requisition + lines from the awarded bids.

        W060 (R6.10, 2026-06-27) — dual-source support. Each awarded
        bid emits ONE purchase.requisition.line carrying that bid's
        vendor + awarded_qty + price. Single-source RFQs land one line
        per pg.rfq.line as before (because there's exactly one awarded
        bid per line). Split awards land N lines per pg.rfq.line — one
        per awarded vendor — and procurement converts each into the
        appropriate PO.
        """
        self.ensure_one()
        Requisition = self.env['purchase.requisition']
        ReqLine = self.env['purchase.requisition.line']

        # W060 — gather ALL awarded bids across all lines (not just the
        # first). For a single-source line `awarded` will be one bid;
        # for a dual-source split it'll be 2+.
        awarded_bids = self.env['pg.rfq.bid']
        for line in self.line_ids:
            awarded_bids |= line.bid_ids.filtered('is_awarded')
        if not awarded_bids:
            raise UserError(_(
                "RFQ %s has no awarded bids — cannot create a "
                "purchase requisition.") % self.display_name)

        partners = awarded_bids.mapped('vendor_id.partner_id')
        # Multiple vendors per RFQ → multi-vendor requisition (purchase
        # requisition supports several supplier_ids).
        partners = partners.filtered(lambda p: p)

        req_vals = {
            'company_id': self.company_id.id,
            'reference': self.name,
            'user_id': self.requestor_id.id,
            # W078 (R6.14) — stamp source-type intent + back-ref on the
            # requisition so downstream procurement can filter
            # subcontract/dropship backlog without walking back to pg.rfq.
            'pg_source_type': self.source_type,
            'pg_rfq_id': self.id,
        }
        # Older purchase_requisition versions don't have requisition_type;
        # set it only when the field is present to stay portable.
        if 'requisition_type' in Requisition._fields:
            req_vals['requisition_type'] = 'blanket_order'
        # vendor_id is the primary supplier on Odoo 19's requisition; set
        # only when there's exactly one bid's vendor across the RFQ so we
        # don't lie. Multi-vendor RFQs leave this blank for procurement
        # to triage.
        if len(partners) == 1:
            req_vals['vendor_id'] = partners.id

        # W021 / R6.2 — stamp date_end + schedule_date from awarded-bid
        # lead-time data so the buyer picks up a ready-to-send
        # requisition rather than having to retype dates. Uses the
        # MAXIMUM lead-time across awarded bids (worst case → ensures
        # nobody promises earlier than the slowest vendor can deliver).
        # W060 — `awarded_bids` already covers every awarded bid (incl.
        # dual-source splits).
        leads = [b.lead_time_days for b in awarded_bids if b.lead_time_days]
        max_lead = max(leads) if leads else 0
        today = fields.Date.context_today(self)
        if max_lead and 'schedule_date' in Requisition._fields:
            req_vals['schedule_date'] = today + timedelta(days=max_lead)
        # date_end on purchase.requisition is the AGREEMENT validity
        # cutoff (when bids/blanket-order stops accepting). Use the
        # earliest quote-valid-until across awarded bids; if no bids
        # supplied valid_until, fall back to today + max_lead + 30d
        # buffer so the requisition has a sensible expiry.
        valid_dates = [b.valid_until for b in awarded_bids if b.valid_until]
        if 'date_end' in Requisition._fields:
            if valid_dates:
                req_vals['date_end'] = min(valid_dates)
            elif max_lead:
                req_vals['date_end'] = today + timedelta(days=max_lead + 30)

        requisition = Requisition.create(req_vals)

        # Lead-time annotation on the requisition chatter so the buyer
        # sees the per-vendor breakdown without drilling back into the
        # RFQ. Posted as a structured log note (not a customer-visible
        # message). W060 — per-bid (not per-line) so dual-source splits
        # render both rows explicitly.
        if awarded_bids:
            lead_lines = [
                _("• %s: %s days (awarded qty %s of line qty %s)") % (
                    bid.vendor_id.display_name,
                    bid.lead_time_days or 0,
                    bid.awarded_qty or bid.line_id.qty,
                    bid.line_id.qty,
                )
                for bid in awarded_bids
            ]
            requisition.message_post(
                body=_(
                    "ProductGraph lead-time summary (max %s days):\n%s"
                ) % (max_lead, "\n".join(lead_lines)),
                subtype_xmlid='mail.mt_log_note',
            )

        # Build the lines. W060 — emit ONE requisition line per
        # awarded bid (not per RFQ line). For single-source flows each
        # RFQ line still produces exactly one req line because there's
        # one awarded bid; dual-source splits emit 2+ per RFQ line.
        for rfq_line in self.line_ids:
            awarded_for_line = rfq_line.bid_ids.filtered('is_awarded')
            if not awarded_for_line:
                continue
            product = rfq_line.item_id.product_id
            if not product:
                # Item has no Odoo product yet (item never released to
                # MRP). Record this as a chatter note and skip.
                self.message_post(
                    body=_("Procurement: skipping %s — no linked "
                           "product.product yet (release first).")
                    % rfq_line.item_id.display_name,
                    subtype_xmlid='mail.mt_log_note',
                )
                continue

            uom = (
                product.uom_id
                if 'uom_id' in product._fields
                else self.env.ref('uom.product_uom_unit')
            )
            for bid in awarded_for_line:
                # W060 — qty on the requisition line = awarded_qty
                # (the slice this vendor won), NOT the full line qty.
                # Falls back to line.qty when awarded_qty is 0 to keep
                # legacy single-source callers correct even if they
                # bypassed the action_award backfill.
                qty = bid.awarded_qty or rfq_line.qty
                line_vals = {
                    'requisition_id': requisition.id,
                    'product_id': product.id,
                    'product_qty': qty,
                    'product_uom_id': uom.id,
                    'price_unit': bid.unit_price,
                    # W003 / R6.1 — back-refs to close the traceability gap.
                    # `pg_item_id` lands on the requisition line and propagates
                    # to purchase.order.line via a stored related (see
                    # purchase_order_line.py). The bridge stamps both so future
                    # PO defect-attribution is a 2-click walk, not a 7-click
                    # string-match on requisition.reference.
                    'pg_item_id': rfq_line.item_id.id,
                    'pg_rfq_line_id': rfq_line.id,
                }
                if 'qty_ordered' in ReqLine._fields:
                    line_vals['qty_ordered'] = 0.0
                # Vendor-part-number → variant description so the
                # eventual PO carries it through to the supplier.
                if bid.vendor_part_number and 'product_description_variants' in ReqLine._fields:
                    line_vals['product_description_variants'] = bid.vendor_part_number

                ReqLine.create(line_vals)

        self.requisition_id = requisition.id
        # W078 — explain the routing on the RFQ chatter so the buyer
        # sees which downstream artifact the bridge produced and what
        # action (if any) is still owed by the planner.
        if self.source_type == 'subcontract':
            tail = _(
                " <b>Subcontract</b> intent — planner converts the "
                "requisition into an mrp.production (subcontract type) "
                "via the Create Subcontract MO button on the "
                "requisition. The bridge does NOT auto-create the MO."
            )
        elif self.source_type == 'dropship':
            tail = _(
                " <b>Dropship</b> intent — procurement flips the PO to "
                "dropship routing when converting the requisition; "
                "the requisition is tagged so the queue filter picks "
                "it up."
            )
        else:
            tail = ''
        self.message_post(
            body=_("Purchase requisition %s created with %d line(s).%s")
            % (requisition.display_name, len(requisition.line_ids), tail),
            subtype_xmlid='mail.mt_log_note',
        )
        # Mirror the routing intent onto the requisition chatter so the
        # downstream planner sees it without bouncing back to pg.rfq.
        if self.source_type != 'purchase':
            requisition.message_post(
                body=_(
                    "ProductGraph: requisition routed as <b>%s</b> "
                    "(source pg.rfq %s)."
                ) % (self.source_type, self.display_name),
                subtype_xmlid='mail.mt_log_note',
            )
        _logger.info(
            "ProductGraph: RFQ %s → purchase.requisition %s (%d lines, source_type=%s)",
            self.display_name, requisition.display_name,
            len(requisition.line_ids), self.source_type,
        )

    # ──────────────────────────────────────────────────────────────────
    # Smart-button — view the requisition
    # ──────────────────────────────────────────────────────────────────
    def action_view_requisition(self):
        self.ensure_one()
        if not self.requisition_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'purchase.requisition',
            'res_id': self.requisition_id.id,
            'view_mode': 'form',
            'target': 'current',
        }
