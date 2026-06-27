# -*- coding: utf-8 -*-
"""Edge-case coverage for the RFQ → purchase.requisition bridge.

PG-151 audit follow-up. The happy-path is in ``test_procurement_bridge.py``;
this file pins down the failure modes:

* released-but-unsourced item never auto-creates a requisition (the bridge
  is RFQ-award-triggered, not release-triggered);
* multi-line RFQs collapse into ONE requisition carrying N lines;
* the bridge uses the item's currently-released revision (rev A) when a
  draft/review-state successor (rev B) exists;
* re-triggering ``action_award`` is blocked by the RFQ state machine →
  effectively idempotent;
* the resulting requisition + lines carry whatever traceability fields
  exist back to pg.item / pg.rfq (and we capture what's MISSING as a
  follow-up);
* no ``purchase.order`` ever springs into existence — the bridge stops
  at requisition (R1 boundary mirror for procurement).
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestProcurementEdgeCases(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disable SoD by default; tests that exercise SoD opt back in.
        cls.env['ir.config_parameter'].sudo().set_param('productgraph.enforce_sod', 'False')
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        # Primary item + product (released, linked to product.product).
        cls.product = cls.env['product.product'].create({
            'name': 'Edge-case test product',
            'type': 'consu',
            'is_storable': True,
        })
        cls.item = cls.env['pg.item'].create({
            'name': 'Edge-case Test Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()
        cls.item.product_id = cls.product.id

        # Vendor + linked partner.
        cls.partner = cls.env['res.partner'].create({
            'name': 'Edge Supplier',
            'company_type': 'company',
        })
        cls.vendor = cls.env['pg.vendor'].create({
            'code': 'EDGE-1',
            'name': 'Edge Supplier',
            'partner_id': cls.partner.id,
        })
        cls.vendor.action_to_probationary()
        cls.vendor.action_qualify()

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def _make_item(self, name, item_type='purchased', linked_product=True):
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': item_type,
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        if linked_product:
            p = self.env['product.product'].create({
                'name': name,
                'type': 'consu',
                'is_storable': True,
            })
            item.product_id = p.id
        return item

    def _release_revision(self, item, code='A'):
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'Initial release of %s for edge-case tests.' % item.name,
        })
        rev.action_submit_for_review()
        rev.action_release()
        return rev

    def _make_rfq_with_lines(self, items_with_bids):
        """items_with_bids: list of (item, qty, unit_price)."""
        rfq = self.env['pg.rfq'].create({'title': 'Edge-case RFQ'})
        for item, qty, price in items_with_bids:
            line = self.env['pg.rfq.line'].create({
                'rfq_id': rfq.id,
                'item_id': item.id,
                'qty': qty,
            })
            self.env['pg.rfq.bid'].create({
                'line_id': line.id,
                'vendor_id': self.vendor.id,
                'vendor_part_number': 'EDGE-%s' % item.id,
                'unit_price': price,
                'lead_time_days': 7,
                'is_awarded': True,
            })
        return rfq

    # ──────────────────────────────────────────────────────────────────
    # 1. No qualified vendor on a released item ≠ auto-requisition.
    #    The bridge is RFQ-award-triggered, not release-triggered.
    # ──────────────────────────────────────────────────────────────────
    def test_release_with_no_qualified_vendor(self):
        unsourced = self._make_item('Unsourced-on-release')
        Req = self.env['purchase.requisition']
        before = Req.search_count([])
        # Releasing a revision should advance the item to released state
        # but must NOT spawn any purchase.requisition.
        self._release_revision(unsourced, code='A')
        self.assertEqual(unsourced.state, 'released')
        after = Req.search_count([])
        self.assertEqual(
            after, before,
            "Releasing a purchased item with no qualified vendor must not "
            "auto-create a purchase.requisition — the bridge only fires "
            "from pg.rfq.action_award().",
        )

    # ──────────────────────────────────────────────────────────────────
    # 2. Multi-line RFQ → ONE requisition with N lines.
    #    (Reading the bridge: Requisition.create() runs once, then a
    #     for-loop adds one ReqLine per awarded RFQ line.)
    # ──────────────────────────────────────────────────────────────────
    def test_award_with_multiple_lines_creates_requisition_per_line(self):
        item_a = self._make_item('Multi-line A')
        item_b = self._make_item('Multi-line B')
        item_c = self._make_item('Multi-line C')
        rfq = self._make_rfq_with_lines([
            (item_a, 10.0, 1.50),
            (item_b, 20.0, 2.50),
            (item_c, 30.0, 3.50),
        ])
        rfq.action_send()
        rfq.action_mark_received()
        rfq.action_award()

        # Exactly one requisition, three lines.
        self.assertTrue(rfq.requisition_id)
        self.assertEqual(
            len(rfq.requisition_id.line_ids), 3,
            "RFQ with 3 awarded lines must produce a single requisition "
            "carrying 3 lines (one per RFQ line).",
        )
        # Lines map 1:1 to the awarded bid economics.
        prices = sorted(rfq.requisition_id.line_ids.mapped('price_unit'))
        self.assertEqual(prices, [1.50, 2.50, 3.50])
        qtys = sorted(rfq.requisition_id.line_ids.mapped('product_qty'))
        self.assertEqual(qtys, [10.0, 20.0, 30.0])

    # ──────────────────────────────────────────────────────────────────
    # 3. With rev A released + rev B in review, the requisition must
    #    reflect the CURRENT released revision (A) on the source item.
    #    The bridge maps via item.product_id which doesn't change with
    #    revisions, but pg.item.current_revision_id is the authoritative
    #    "current" pointer — verify it resolves to A, not B.
    # ──────────────────────────────────────────────────────────────────
    def test_award_mid_supersession_uses_current_revision(self):
        item = self._make_item('Supersession-mid-flight')
        rev_a = self._release_revision(item, code='A')
        # Rev B in review (not yet released).
        rev_b = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'B',
            'change_summary': 'In-flight revision; not yet released for procurement.',
        })
        rev_b.action_submit_for_review()
        self.assertEqual(rev_b.state, 'review')
        # Current revision pointer should be A.
        self.assertEqual(
            item.current_revision_id, rev_a,
            "Items in mid-supersession (B in review) must still report A "
            "as the current released revision.",
        )

        rfq = self._make_rfq_with_lines([(item, 5.0, 9.99)])
        rfq.action_send()
        rfq.action_mark_received()
        rfq.action_award()

        self.assertTrue(rfq.requisition_id)
        # The requisition line carries product_id from item.product_id; the
        # source item's current released revision must resolve to A, NOT B.
        # The bridge itself does not stamp a revision pointer on the
        # requisition (see test_requisition_carries_pg_item_traceability),
        # so we verify via the RFQ line's source item — which is the path
        # any future revision-aware traceability would have to walk.
        req_line = rfq.requisition_id.line_ids
        self.assertEqual(len(req_line), 1)
        self.assertEqual(req_line.product_id, item.product_id)
        source_item = rfq.line_ids.item_id
        self.assertEqual(
            source_item.current_revision_id, rev_a,
            "Mid-supersession award must see rev A as the source item's "
            "current revision (in-review B is NOT yet the released spec).",
        )
        self.assertNotEqual(source_item.current_revision_id, rev_b)

    # ──────────────────────────────────────────────────────────────────
    # 4. Re-trigger action_award. The RFQ state machine blocks the
    #    second call (state is already 'awarded'), so the bridge cannot
    #    create a duplicate requisition. That IS the idempotence guard.
    # ──────────────────────────────────────────────────────────────────
    def test_no_double_create_on_re_award(self):
        rfq = self._make_rfq_with_lines([(self.item, 1.0, 5.0)])
        rfq.action_send()
        rfq.action_mark_received()
        rfq.action_award()
        first_req = rfq.requisition_id
        self.assertTrue(first_req)
        Req = self.env['purchase.requisition']
        count_before = Req.search_count([])
        # Re-call action_award: super().action_award() raises because the
        # RFQ is already 'awarded' (only 'received' may transition).
        with self.assertRaises(
            UserError,
            msg="Re-awarding an already-awarded RFQ must raise (the RFQ "
                "state machine is the idempotence guard for the bridge).",
        ):
            rfq.action_award()
        # And no new requisition leaked.
        self.assertEqual(
            Req.search_count([]), count_before,
            "Re-award attempt must not create a second requisition.",
        )
        self.assertEqual(rfq.requisition_id, first_req)

    # ──────────────────────────────────────────────────────────────────
    # 5. Traceability surface. Capture what the bridge DOES wire today
    #    (forward pointer + reference string) and document missing
    #    back-references on requisition / requisition.line.
    # ──────────────────────────────────────────────────────────────────
    def test_requisition_carries_pg_item_traceability(self):
        rfq = self._make_rfq_with_lines([(self.item, 7.0, 4.25)])
        rfq.action_send()
        rfq.action_mark_received()
        rfq.action_award()
        req = rfq.requisition_id
        self.assertTrue(req)

        # Forward pointer pg.rfq → requisition is wired.
        self.assertEqual(rfq.requisition_id, req)
        # Soft traceability: the requisition.reference carries the RFQ name.
        self.assertEqual(req.reference, rfq.name)
        # The line resolves back to the same product as the RFQ line's item.
        self.assertEqual(req.line_ids.product_id, self.item.product_id)

        # ── Follow-up surface (PG-151 gap): the bridge does NOT add
        # ``pg_item_id`` or ``pg_rfq_line_id`` Many2one fields on
        # purchase.requisition.line. Auditors looking up "which pg.item
        # / pg.rfq produced this PO line" can only traverse:
        #   purchase.order.line → requisition_line_id
        #     → requisition_id → reference (string match RFQ name)
        # That's brittle. File as PG-151-FOLLOWUP if traceability is a
        # real audit requirement.
        ReqLine = self.env['purchase.requisition.line']
        self.assertNotIn(
            'pg_item_id', ReqLine._fields,
            "If pg_item_id ever lands on purchase.requisition.line, "
            "tighten this test to assert the back-reference value.",
        )
        self.assertNotIn(
            'pg_rfq_line_id', ReqLine._fields,
            "If pg_rfq_line_id ever lands on purchase.requisition.line, "
            "tighten this test to assert the back-reference value.",
        )

    # ──────────────────────────────────────────────────────────────────
    # 6. R1-style boundary mirror: bridge stops at requisition.
    #    No purchase.order may leak into existence for the awarded vendor.
    # ──────────────────────────────────────────────────────────────────
    def test_no_purchase_order_auto_created(self):
        PO = self.env['purchase.order']
        # Snapshot any pre-existing POs for our partner (should be zero in
        # a clean transaction, but be defensive).
        before_ids = set(PO.search([
            ('partner_id', '=', self.partner.id),
        ]).ids)

        rfq = self._make_rfq_with_lines([(self.item, 12.0, 6.66)])
        rfq.action_send()
        rfq.action_mark_received()
        rfq.action_award()

        after_ids = set(PO.search([
            ('partner_id', '=', self.partner.id),
        ]).ids)
        leaked = after_ids - before_ids
        self.assertFalse(
            leaked,
            "RFQ award must not auto-create purchase.order records — the "
            "bridge stops at requisition. Procurement converts the "
            "requisition into POs as a separate human-mediated step. "
            "Leaked PO ids: %s" % sorted(leaked),
        )
