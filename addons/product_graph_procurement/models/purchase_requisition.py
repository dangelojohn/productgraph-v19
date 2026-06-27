# -*- coding: utf-8 -*-
"""W078 (R6.14) — source_type stamp on purchase.requisition.

The pg.rfq.action_award path now routes by source_type
(purchase / subcontract / dropship). The bridge stamps the chosen
source_type onto the generated requisition so:

  * Procurement can filter "show me all subcontract requisitions
    awaiting MO conversion" without joining back to pg.rfq.
  * The planner who later clicks "Create Subcontract MO" on the
    requisition can read the intent off the requisition itself.

We do NOT create the mrp.production (subcontract) here — that's the
planner's choice via a button on the requisition (out of scope for
this round per the W078 brief). The bridge just stamps + posts a
chatter note explaining the next step.
"""
from odoo import fields, models


class PurchaseRequisition(models.Model):
    _inherit = 'purchase.requisition'

    pg_source_type = fields.Selection(
        [
            ('purchase', 'Purchase'),
            ('subcontract', 'Subcontract'),
            ('dropship', 'Dropship'),
        ],
        string='ProductGraph Source Type',
        index=True, copy=False,
        help='Source-type intent inherited from the originating pg.rfq. '
             'NULL on requisitions created outside the ProductGraph flow. '
             'Filters in the procurement view drive on this so a buyer '
             'can scope to "subcontract" or "dropship" backlog without '
             'walking back to the pg.rfq.',
    )
    pg_rfq_id = fields.Many2one(
        'pg.rfq',
        string='Source RFQ',
        ondelete='set null',
        index=True, copy=False,
        help='The pg.rfq whose award generated this requisition. '
             'Stamped by the bridge for traceability.',
    )
