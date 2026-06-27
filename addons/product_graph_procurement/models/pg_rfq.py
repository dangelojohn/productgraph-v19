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
        """Create a purchase.requisition + lines from the awarded bids."""
        self.ensure_one()
        Requisition = self.env['purchase.requisition']
        ReqLine = self.env['purchase.requisition.line']

        # Vendor must have a linked partner for the requisition's
        # vendor_id field.
        awarded = self.line_ids.mapped(
            lambda l: l.bid_ids.filtered('is_awarded')[:1])
        if not awarded:
            raise UserError(_(
                "RFQ %s has no awarded bids — cannot create a "
                "purchase requisition.") % self.display_name)

        partners = awarded.mapped('vendor_id.partner_id')
        # Multiple vendors per RFQ → multi-vendor requisition (purchase
        # requisition supports several supplier_ids).
        partners = partners.filtered(lambda p: p)

        req_vals = {
            'company_id': self.company_id.id,
            'reference': self.name,
            'user_id': self.requestor_id.id,
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
        awarded_bids = self.line_ids.mapped(
            lambda l: l.bid_ids.filtered('is_awarded')[:1])
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
        # message).
        if awarded_bids:
            lead_lines = [
                _("• %s: %s days (qty %s)") % (
                    bid.vendor_id.display_name,
                    bid.lead_time_days or 0,
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

        # Build the lines.
        for rfq_line in self.line_ids:
            bid = rfq_line.bid_ids.filtered('is_awarded')[:1]
            if not bid:
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
            line_vals = {
                'requisition_id': requisition.id,
                'product_id': product.id,
                'product_qty': rfq_line.qty,
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
            # Vendor-part-number → variant description so the eventual PO
            # carries it through to the supplier.
            if bid.vendor_part_number and 'product_description_variants' in ReqLine._fields:
                line_vals['product_description_variants'] = bid.vendor_part_number

            ReqLine.create(line_vals)

        self.requisition_id = requisition.id
        self.message_post(
            body=_("Purchase requisition %s created with %d line(s).")
            % (requisition.display_name, len(requisition.line_ids)),
            subtype_xmlid='mail.mt_log_note',
        )
        _logger.info(
            "ProductGraph: RFQ %s → purchase.requisition %s (%d lines)",
            self.display_name, requisition.display_name,
            len(requisition.line_ids),
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
