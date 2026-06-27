# -*- coding: utf-8 -*-
"""Back-references on purchase.order.line → pg.item / pg.rfq.line.

W003 / R6.1 follow-through: the bridge stamps these on the requisition
line at award time, and ``_prepare_purchase_order_line`` propagates them
to each PO line that the procurement team materializes from that
requisition. Both fields stay NULL on PO lines created outside the
ProductGraph flow.

Note: Odoo 19's ``purchase.order.line`` does NOT carry a direct
``requisition_line_id`` field — the link is only at the
``purchase.order.requisition_id`` level, with per-line matching done by
``product_id``. So a stored related won't work; we propagate via the
``_prepare_purchase_order_line`` override on ``purchase.requisition.line``
instead.
"""
from odoo import fields, models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    pg_item_id = fields.Many2one(
        'pg.item',
        string='PG Item',
        index=True,
        ondelete='set null',
        help='Engineering item that originated this PO line. Stamped by '
             'the RFQ → requisition → PO bridge; NULL on PO lines created '
             'outside the ProductGraph flow.',
    )
    pg_rfq_line_id = fields.Many2one(
        'pg.rfq.line',
        string='Source RFQ Line',
        index=True,
        ondelete='set null',
        help='RFQ line whose awarded bid produced the requisition line '
             'that produced this PO line.',
    )
