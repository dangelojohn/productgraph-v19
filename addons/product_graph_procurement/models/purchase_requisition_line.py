# -*- coding: utf-8 -*-
"""Back-references on purchase.requisition.line → pg.item / pg.rfq.line.

W003 / R6.1: close the procurement-traceability gap. Without these back-refs,
a defective lot can only be attributed back to the engineering revision by
string-matching ``purchase.requisition.reference`` against ``pg.rfq.name`` —
brittle (reference is user-editable) and lossy. The two Many2ones land here
on the requisition line and propagate to ``purchase.order.line`` (sibling
module file) via a ``related`` so the buyer-day click count drops from 7
to 2.
"""
from odoo import fields, models


class PurchaseRequisitionLine(models.Model):
    _inherit = 'purchase.requisition.line'

    pg_item_id = fields.Many2one(
        'pg.item',
        string='PG Item',
        ondelete='set null',
        index=True,
        help='Engineering item that originated this requisition line. '
             'Stamped by the RFQ → requisition bridge at award time. NULL '
             'on requisition lines created outside the ProductGraph flow.',
    )
    pg_rfq_line_id = fields.Many2one(
        'pg.rfq.line',
        string='Source RFQ Line',
        ondelete='set null',
        index=True,
        help='The pg.rfq.line whose awarded bid populated this requisition '
             'line. Lets auditors walk back from a PO to the originating '
             'RFQ + revision without grep-matching reference strings.',
    )

    def _prepare_purchase_order_line(self, name, product_qty=0.0,
                                     price_unit=0.0, taxes_ids=False):
        """Propagate the W003 back-refs from the requisition line down to
        each PO line spawned from it. The native flow matches requisition
        lines to PO lines by product_id (Odoo 19 has no direct
        ``requisition_line_id`` on purchase.order.line), so the only
        clean place to copy the back-refs is here, at PO-line vals build.
        """
        vals = super()._prepare_purchase_order_line(
            name, product_qty=product_qty,
            price_unit=price_unit, taxes_ids=taxes_ids,
        )
        if self.pg_item_id:
            vals['pg_item_id'] = self.pg_item_id.id
        if self.pg_rfq_line_id:
            vals['pg_rfq_line_id'] = self.pg_rfq_line_id.id
        return vals
