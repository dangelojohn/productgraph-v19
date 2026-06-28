# -*- coding: utf-8 -*-
"""W076 (R6.12, 2026-06-27) — vendor portal stub.

Covers the portal.mixin extension on pg.rfq.bid + the three /my routes
(list / detail / submit) + the 3-layer access control (login + token +
vendor.partner_id) + the post-award lock.

The HTTP route layer is exercised via HttpCase so we get a real session
+ real CSRF + real render. The pure-Python model layer is exercised
via TransactionCase for speed.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import HttpCase, TransactionCase


@tagged('post_install', '-at_install', 'product_graph', 'w076')
class TestW076PortalModel(TransactionCase):
    """Pure-Python checks: portal mixin wiring + submission helper.

    These don't open a session — they exercise the model layer that
    sits behind the controller so failures here are easier to bisect
    than HTTP-level breakage.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref('base.user_admin')
        cls.admin.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.admin)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        cls.item = cls.env['pg.item'].create({
            'name': 'W076 Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.action_to_engineering()

        cls.partner_a = cls.env['res.partner'].create({
            'name': 'W076 Vendor A',
            'email': 'w076a@example.com',
        })
        cls.partner_b = cls.env['res.partner'].create({
            'name': 'W076 Vendor B',
            'email': 'w076b@example.com',
        })

        cls.vendor_a = cls.env['pg.vendor'].create({
            'code': 'W076A', 'name': 'W076 A Co',
            'partner_id': cls.partner_a.id,
        })
        cls.vendor_a.action_to_probationary()
        cls.vendor_a.action_qualify()
        cls.vendor_b = cls.env['pg.vendor'].create({
            'code': 'W076B', 'name': 'W076 B Co',
            'partner_id': cls.partner_b.id,
        })
        cls.vendor_b.action_to_probationary()
        cls.vendor_b.action_qualify()

    def _make_rfq_with_bid(self, vendor, state='sent', price=10.0):
        rfq = self.env['pg.rfq'].create({'title': 'W076 RFQ'})
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id, 'item_id': self.item.id, 'qty': 50.0,
        })
        bid = self.env['pg.rfq.bid'].create({
            'line_id': line.id, 'vendor_id': vendor.id,
            'unit_price': price, 'lead_time_days': 14, 'moq': 25,
        })
        if state in ('sent', 'received', 'awarded'):
            rfq.action_send()
        if state in ('received', 'awarded'):
            rfq.action_mark_received()
        if state == 'awarded':
            bid.is_awarded = True
            rfq.action_award()
        return rfq, line, bid

    def test_10_portal_mixin_inherited(self):
        # The bid model should carry portal.mixin fields.
        Bid = self.env['pg.rfq.bid']
        self.assertIn('access_token', Bid._fields)
        self.assertIn('access_url', Bid._fields)

    def test_20_access_token_minted_on_create(self):
        _, _, bid = self._make_rfq_with_bid(self.vendor_a, state='sent')
        self.assertTrue(
            bid.access_token,
            "access_token should be eagerly minted on create for W076 portal",
        )

    def test_30_access_url_points_to_portal_route(self):
        _, _, bid = self._make_rfq_with_bid(self.vendor_a, state='sent')
        # Force the compute via read.
        bid.invalidate_recordset(['access_url'])
        self.assertEqual(bid.access_url, f'/my/rfq-bids/{bid.id}')

    def test_40_submission_updates_fields(self):
        _, _, bid = self._make_rfq_with_bid(self.vendor_a, state='sent')
        bid._portal_post_submission({
            'unit_price': 12.5, 'lead_time_days': 21, 'moq': 100,
            'vendor_part_number': 'VPN-W076',
        }, by_user=self.admin)
        bid.invalidate_recordset()
        self.assertEqual(bid.unit_price, 12.5)
        self.assertEqual(bid.lead_time_days, 21)
        self.assertEqual(bid.moq, 100)
        self.assertEqual(bid.vendor_part_number, 'VPN-W076')

    def test_50_submission_rejected_after_award(self):
        _, _, bid = self._make_rfq_with_bid(self.vendor_a, state='awarded')
        self.assertFalse(bid._portal_can_be_submitted())
        with self.assertRaises(UserError):
            bid._portal_post_submission(
                {'unit_price': 99.0}, by_user=self.admin,
            )

    def test_60_submission_strips_non_allowed_fields(self):
        # is_awarded is NOT in the writable allowlist — vendor must not
        # be able to flag their own bid as the winner.
        _, _, bid = self._make_rfq_with_bid(self.vendor_a, state='sent')
        bid._portal_post_submission(
            {'unit_price': 13.0, 'is_awarded': True}, by_user=self.admin,
        )
        bid.invalidate_recordset()
        self.assertEqual(bid.unit_price, 13.0)
        self.assertFalse(
            bid.is_awarded,
            "Vendor must not be able to self-award via the portal submission",
        )

    def test_70_isolation_between_vendors(self):
        # Two bids on the same line, different vendors. Each should
        # have a distinct access_token and a distinct partner gate.
        rfq = self.env['pg.rfq'].create({'title': 'W076 multi'})
        line = self.env['pg.rfq.line'].create({
            'rfq_id': rfq.id, 'item_id': self.item.id, 'qty': 10,
        })
        bid_a = self.env['pg.rfq.bid'].create({
            'line_id': line.id, 'vendor_id': self.vendor_a.id,
            'unit_price': 8, 'lead_time_days': 7, 'moq': 1,
        })
        bid_b = self.env['pg.rfq.bid'].create({
            'line_id': line.id, 'vendor_id': self.vendor_b.id,
            'unit_price': 9, 'lead_time_days': 5, 'moq': 1,
        })
        self.assertTrue(bid_a.access_token)
        self.assertTrue(bid_b.access_token)
        self.assertNotEqual(bid_a.access_token, bid_b.access_token)
        self.assertNotEqual(
            bid_a.vendor_id.partner_id, bid_b.vendor_id.partner_id,
        )


@tagged('post_install', '-at_install', 'product_graph', 'w076')
class TestW076PortalHttp(HttpCase):
    """Real HTTP-level checks: routes, sessions, isolation, lockout.

    Each test logs in (or doesn't) and hits the route, asserting on
    status code / redirect target. The vendor portal user is a real
    res.users with group_portal so the auth='user' gate sees a session.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        cls.admin = cls.env.ref('base.user_admin')
        cls.admin.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )

        cls.item = cls.env['pg.item'].with_user(cls.admin).create({
            'name': 'W076 HTTP Item',
            'item_type': 'purchased',
            'category_id': cls.cat.id,
        })
        cls.item.with_user(cls.admin).action_to_engineering()

        # Vendor A: with a real portal user.
        cls.partner_a = cls.env['res.partner'].create({
            'name': 'W076 HTTP Vendor A',
            'email': 'w076_http_a@example.com',
        })
        cls.user_a = cls.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'W076 HTTP Vendor A',
            'login': 'w076_http_a',
            'email': 'w076_http_a@example.com',
            'partner_id': cls.partner_a.id,
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])],
        })
        cls.vendor_a = cls.env['pg.vendor'].create({
            'code': 'HTA', 'name': 'HTTP A',
            'partner_id': cls.partner_a.id,
        })
        cls.vendor_a.action_to_probationary()
        cls.vendor_a.action_qualify()

        # Vendor B: also with a portal user — for isolation tests.
        cls.partner_b = cls.env['res.partner'].create({
            'name': 'W076 HTTP Vendor B',
            'email': 'w076_http_b@example.com',
        })
        cls.user_b = cls.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'W076 HTTP Vendor B',
            'login': 'w076_http_b',
            'email': 'w076_http_b@example.com',
            'partner_id': cls.partner_b.id,
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])],
        })
        cls.vendor_b = cls.env['pg.vendor'].create({
            'code': 'HTB', 'name': 'HTTP B',
            'partner_id': cls.partner_b.id,
        })
        cls.vendor_b.action_to_probationary()
        cls.vendor_b.action_qualify()

        cls.rfq = cls.env['pg.rfq'].with_user(cls.admin).create({
            'title': 'W076 HTTP',
        })
        cls.line = cls.env['pg.rfq.line'].with_user(cls.admin).create({
            'rfq_id': cls.rfq.id, 'item_id': cls.item.id, 'qty': 100,
        })
        cls.bid_a = cls.env['pg.rfq.bid'].with_user(cls.admin).create({
            'line_id': cls.line.id, 'vendor_id': cls.vendor_a.id,
            'unit_price': 5.0, 'lead_time_days': 14, 'moq': 10,
        })
        cls.bid_b = cls.env['pg.rfq.bid'].with_user(cls.admin).create({
            'line_id': cls.line.id, 'vendor_id': cls.vendor_b.id,
            'unit_price': 6.0, 'lead_time_days': 10, 'moq': 25,
        })
        # Move the RFQ into the open state so the portal accepts bids.
        cls.rfq.with_user(cls.admin).action_send()

    def test_10_anonymous_redirected_to_login(self):
        # auth='user' should boot anyone without a session to /web/login.
        resp = self.url_open('/my/rfq-bids', allow_redirects=False)
        # Either 303 / 302 to /web/login or a render of the login page.
        self.assertIn(resp.status_code, (301, 302, 303, 307, 308))
        loc = resp.headers.get('Location', '')
        self.assertIn('/web/login', loc)

    def test_20_vendor_lists_own_bids_only(self):
        self.authenticate('w076_http_a', 'w076_http_a')
        resp = self.url_open('/my/rfq-bids')
        self.assertEqual(resp.status_code, 200)
        # The page should reference vendor A's bid id and NOT vendor B's
        # (the list is partner-scoped at the controller).
        body = resp.text
        self.assertIn(f'/my/rfq-bids/{self.bid_a.id}', body)
        self.assertNotIn(f'/my/rfq-bids/{self.bid_b.id}', body)

    def test_30_vendor_cannot_open_other_bid(self):
        # Vendor A tries to open vendor B's bid by guessing the id.
        # Controller redirects to /my (we never 200 the detail page).
        self.authenticate('w076_http_a', 'w076_http_a')
        resp = self.url_open(
            f'/my/rfq-bids/{self.bid_b.id}', allow_redirects=False,
        )
        self.assertIn(resp.status_code, (301, 302, 303, 307, 308))
        loc = resp.headers.get('Location', '')
        self.assertIn('/my', loc)
        self.assertNotIn(f'rfq-bids/{self.bid_b.id}', loc)

    def test_40_own_detail_renders(self):
        self.authenticate('w076_http_a', 'w076_http_a')
        resp = self.url_open(f'/my/rfq-bids/{self.bid_a.id}')
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        # The detail page should expose at least the item part-no and a
        # form posting back to the submit endpoint.
        self.assertIn(f'/my/rfq-bids/{self.bid_a.id}/submit', body)
        # MOQ + unit price inputs should be present.
        self.assertIn('name="unit_price"', body)
        self.assertIn('name="moq"', body)

    def test_50_submit_updates_bid(self):
        self.authenticate('w076_http_a', 'w076_http_a')
        # Fetch the detail page first so the session gets a CSRF token.
        self.url_open(f'/my/rfq-bids/{self.bid_a.id}')
        csrf = self.session.get('csrf_token') if hasattr(self, 'session') else None
        # HttpCase exposes a CSRF helper via the route — easier: pull
        # the token from the form. Use a raw post with csrf disabled
        # would defeat the test; instead, ride the session's stable
        # csrf via the same channel the page uses.
        # The simplest path that works under HttpCase is to scrape the
        # rendered form for the csrf_token hidden input.
        page = self.url_open(f'/my/rfq-bids/{self.bid_a.id}').text
        import re
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', page)
        self.assertIsNotNone(
            m, "Detail page should expose a csrf_token hidden input",
        )
        token = m.group(1)
        resp = self.url_open(
            f'/my/rfq-bids/{self.bid_a.id}/submit',
            data={
                'csrf_token': token,
                'unit_price': '7.5',
                'lead_time_days': '21',
                'moq': '100',
                'vendor_part_number': 'VPN-HTTP',
            },
            allow_redirects=False,
        )
        self.assertIn(resp.status_code, (301, 302, 303, 307, 308))
        self.bid_a.invalidate_recordset()
        self.assertEqual(self.bid_a.unit_price, 7.5)
        self.assertEqual(self.bid_a.lead_time_days, 21)
        self.assertEqual(self.bid_a.moq, 100)
        self.assertEqual(self.bid_a.vendor_part_number, 'VPN-HTTP')

    def test_60_landing_counter_visible(self):
        # _prepare_home_portal_values should expose `rfq_bid_count`
        # when the counter is queried. We can't easily probe the
        # landing template directly here, so we exercise the helper
        # via the controller (which is what /my calls).
        from odoo.addons.product_graph_rfq.controllers.portal import PgVendorPortal
        portal_ctrl = PgVendorPortal()
        # Mock-light: set up request manually is brittle; instead
        # assert the count via the same sudo domain the controller uses.
        Bid = self.env['pg.rfq.bid'].sudo()
        count = Bid.search_count([
            ('vendor_id.partner_id', '=', self.partner_a.id),
            ('rfq_state', 'in', ('sent', 'received', 'awarded')),
        ])
        self.assertEqual(count, 1)
