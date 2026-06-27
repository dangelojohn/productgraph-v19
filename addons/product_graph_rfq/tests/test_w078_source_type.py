# -*- coding: utf-8 -*-
"""W078 (R6.14) — subcontract / dropship source-type on pg.rfq.

Hardware subcontract decisions used to bypass the AVL entirely
because awarded RFQs always landed as a generic purchase
requisition. With source_type on the header the bridge stamps
the chosen intent onto the downstream requisition + audit-logs
the per-bid choice so quality / planning can trace back.

Scope (per W078 constraints):
  * Bridge ROUTES to the correct downstream artifact (purchase,
    subcontract, dropship) — it does NOT auto-create subcontract
    MOs. That's a planner-button decision out of scope.

Tests cover:
  * Default = 'purchase' so legacy callers behave unchanged.
  * source_type write is tracked on chatter (mail.message audit).
  * action_award emits a payload entry per awarded bid carrying
    source_type for downstream defect-attribution queries.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'w078')
class TestW078SourceType(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        cls.item = cls.env['pg.item'].create({
            'name': 'W078 Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()

        cls.vendor = cls.env['pg.vendor'].create({
            'code': 'W078', 'name': 'W078 Vendor',
        })
        cls.vendor.action_to_probationary()
        cls.vendor.action_qualify()

    def _make_rfq_awarded(self, source_type='purchase'):
        rfq = self.env['pg.rfq'].create({
            'title': 'W078 RFQ %s' % source_type,
            'source_type': source_type,
        })
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id,
            'item_id': self.item.id,
            'qty': 10.0,
        })
        rfq.action_send()
        self.env['pg.rfq.bid'].create({
            'line_id': line.id,
            'vendor_id': self.vendor.id,
            'unit_price': 1.0,
            'is_awarded': True,
            'awarded_qty': 10.0,
        })
        rfq.action_mark_received()
        rfq.action_award()
        return rfq

    # ──────────────────────────────────────────────────────────────────
    # Defaults + selection coverage
    # ──────────────────────────────────────────────────────────────────
    def test_10_default_source_type_is_purchase(self):
        rfq = self.env['pg.rfq'].create({'title': 'W078 default'})
        self.assertEqual(
            rfq.source_type, 'purchase',
            "source_type must default to 'purchase' so legacy "
            "single-source flows are unchanged.",
        )

    def test_20_all_three_source_types_assignable(self):
        for st in ('purchase', 'subcontract', 'dropship'):
            rfq = self.env['pg.rfq'].create({
                'title': 'W078 %s' % st,
                'source_type': st,
            })
            self.assertEqual(rfq.source_type, st)

    # ──────────────────────────────────────────────────────────────────
    # Tracking — source_type writes show on the chatter
    # ──────────────────────────────────────────────────────────────────
    def test_30_source_type_change_is_tracked(self):
        rfq = self.env['pg.rfq'].create({'title': 'W078 tracked'})
        baseline = self.env['mail.message'].search_count([
            ('res_id', '=', rfq.id),
            ('model', '=', 'pg.rfq'),
        ])
        rfq.source_type = 'subcontract'
        after = self.env['mail.message'].search_count([
            ('res_id', '=', rfq.id),
            ('model', '=', 'pg.rfq'),
        ])
        self.assertGreater(
            after, baseline,
            "source_type field has tracking=True — the write should "
            "produce a tracked mail.message entry.",
        )

    # ──────────────────────────────────────────────────────────────────
    # Award audit log carries source_type per bid
    # ──────────────────────────────────────────────────────────────────
    def test_40_award_audit_log_payload_includes_source_type(self):
        rfq = self._make_rfq_awarded(source_type='subcontract')
        audit = self.env['pg.audit.log'].sudo().search([
            ('res_model', '=', 'pg.rfq'),
            ('res_id', '=', rfq.id),
            ('to_state', '=', 'awarded'),
        ], limit=1, order='id desc')
        self.assertTrue(audit, "Expected an awarded-state audit entry.")
        self.assertIn(
            'source_type', audit.notes or '',
            "Audit notes should mention source_type.",
        )
        # Payload is JSON-encoded.
        import json
        payload = json.loads(audit.payload or '{}')
        self.assertEqual(payload.get('source_type'), 'subcontract')
        self.assertTrue(payload.get('awarded_bids'))
        for entry in payload['awarded_bids']:
            self.assertEqual(entry['source_type'], 'subcontract')

    # ──────────────────────────────────────────────────────────────────
    # Bridge routing — requisition stamp + chatter
    # ──────────────────────────────────────────────────────────────────
    def test_50_bridge_stamps_source_type_on_requisition(self):
        if not self.env['ir.module.module'].sudo().search([
            ('name', '=', 'product_graph_procurement'),
            ('state', '=', 'installed'),
        ]):
            self.skipTest('product_graph_procurement not installed')
        # Need partner + product for the bridge to emit lines.
        product = self.env['product.product'].create({
            'name': 'W078 Bridge Product', 'type': 'consu',
        })
        self.item.product_id = product.id
        partner = self.env['res.partner'].create({
            'name': 'W078 Partner', 'company_type': 'company',
        })
        self.vendor.partner_id = partner.id

        rfq = self._make_rfq_awarded(source_type='dropship')
        self.assertTrue(rfq.requisition_id)
        req = rfq.requisition_id
        self.assertEqual(
            req.pg_source_type, 'dropship',
            "Bridge must stamp source_type onto the requisition.",
        )
        self.assertEqual(
            req.pg_rfq_id, rfq,
            "Bridge must back-ref the source RFQ on the requisition.",
        )
