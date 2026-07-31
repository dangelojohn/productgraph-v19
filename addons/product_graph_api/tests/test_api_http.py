# -*- coding: utf-8 -*-
"""HTTP-layer round-trip tests for the ProductGraph REST API.

These complement ``test_phase2_endpoints.py`` which exercises the serialiser
helpers via ``TransactionCase``. This module boots a real Odoo HTTP server
through ``HttpCase`` and walks every public route in ``controllers/pg_api.py``
end-to-end: auth gate, group gate, JSON envelope, HTTP status code, and
mutation effects.

Auth model — what the controllers do as of 19.0.1.2.0
-----------------------------------------------------
Every route except ``/health`` declares ``auth='bearer'``. Odoo's
``ir.http._auth_method_bearer`` resolves the request in this order:

  1. ``Authorization: Bearer <key>`` header present → validate against
     ``res.users.apikeys``; on success bind ``request.env.user`` to the
     key's owner and continue. On a bad key: raise 401 with
     ``WWW-Authenticate: bearer``.
  2. No bearer header AND no session ``uid`` → raise 401.
  3. No bearer header but session ``uid`` set AND the request carries
     browser Sec-Fetch navigation headers → fall through to
     ``_auth_method_user`` (this is the dual-mode escape hatch for
     same-origin browser navigation; API clients do not get it).

For test purposes we drive the API via the bearer header exclusively.
That mirrors how the MCP sidecar and CAD connectors authenticate, and
matches CLAUDE.md R6 (personal API keys are the only supported auth
for these routes).

Per CLAUDE.md R6, mutating endpoints additionally re-check group
membership at the controller level: ``group_pg_engineer`` for writes,
``group_pg_approver`` for approvals + release execution.
"""
import json
import logging
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.tests.common import HttpCase, tagged

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install', 'product_graph', 'product_graph_api_http')
class TestProductGraphApiHttp(HttpCase):

    # ──────────────────────────────────────────────────────────────────────
    # Setup
    # ──────────────────────────────────────────────────────────────────────
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.PG_BASE = '/api/v1/productgraph'
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        # Three users, one per relevant group, so we can pin down exactly
        # which gate is firing on every route.
        cls.engineer = cls._make_user(
            'pg_api_test_engineer', 'API Engineer',
            ['product_graph_base.group_pg_engineer'],
        )
        cls.approver = cls._make_user(
            'pg_api_test_approver', 'API Approver',
            ['product_graph_base.group_pg_approver'],
        )
        cls.viewer = cls._make_user(
            'pg_api_test_viewer', 'API Viewer',
            ['product_graph_base.group_pg_viewer'],
        )

        # API keys (Bearer) — created sudo so the user-duration cap is
        # waived; per v19 res_users.py:1597 superuser bypasses the cap.
        cls.engineer_api_key = cls._make_api_key(cls.engineer)
        cls.approver_api_key = cls._make_api_key(cls.approver)
        cls.viewer_api_key = cls._make_api_key(cls.viewer)

        # A baseline item to query against (created as admin sudo to skip
        # the API-creation path which we test separately).
        cls.seed_item = cls.env['pg.item'].create({
            'name': 'API HTTP test seed',
            'item_type': 'part',
            'category_id': cls.cat.id,
        })

    @classmethod
    def _make_user(cls, login, name, group_xmlids):
        groups = cls.env['res.groups']
        for xmlid in group_xmlids:
            groups |= cls.env.ref(xmlid)
        # group_ids is the v19 m2m field name (formerly groups_id pre-19).
        # Falls back to groups_id if running on an older base.
        field_name = 'group_ids' if 'group_ids' in cls.env['res.users']._fields else 'groups_id'
        return cls.env['res.users'].create({
            'name': name,
            'login': login,
            'password': login,  # password == login keeps the test self-contained
            field_name: [(6, 0, groups.ids)],
        })

    @classmethod
    def _make_api_key(cls, user):
        """Generate a personal API key for ``user`` and return the plaintext.

        ``res.users.apikeys._generate(scope, name, expiration_date)`` —
        v19 requires an expiration_date unless called as a superuser
        (``env.is_system()`` short-circuits the cap check). We call it as
        the user under ``sudo()`` and provide a date 1 day out.
        """
        ApiKeys = cls.env['res.users.apikeys'].with_user(user).sudo()
        expiry = datetime.now() + timedelta(days=1)
        return ApiKeys._generate('rpc', f'PG API HTTP test ({user.login})', expiry)

    # ──────────────────────────────────────────────────────────────────────
    # Low-level request helper
    # ──────────────────────────────────────────────────────────────────────
    def _url(self, path):
        return self.base_url() + path

    def _get(self, path, api_key=None, session_user=None):
        """GET ``path`` and return the requests Response."""
        if session_user:
            self.authenticate(session_user.login, session_user.login)
        headers = {}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        return self.url_open(self._url(path), headers=headers, timeout=20)

    def _post_json(self, path, payload, api_key=None, session_user=None):
        """POST a JSON-RPC envelope to ``path``.

        Odoo's ``type='json'`` dispatcher expects
        ``{"jsonrpc": "2.0", "params": {...}}`` and ignores raw payloads.
        It returns a wrapped envelope ``{"jsonrpc": "2.0", "result": ...}``
        on success and ``{... "error": ...}`` on failure (HTTP 200 either way).
        """
        if session_user:
            self.authenticate(session_user.login, session_user.login)
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        body = json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': payload}).encode('utf-8')
        return self.url_open(self._url(path), data=body, headers=headers, timeout=20)

    def _safe_logout(self):
        """Guarded session logout. HttpCase doesn't always initialize
        self.session on test setUp; calling .logout() on None crashes
        tearDown and masks the real failure."""
        sess = getattr(self, 'session', None)
        if sess is not None:
            try:
                sess.logout()
            except Exception:
                pass

    def _assert_forbidden(self, resp, msg=''):
        """Accept any of: plain HTTP 401/403, JSON-RPC error envelope, or
        JSON-RPC envelope with a wrapped 403 Response. PG-170's bearer auth
        short-circuits before the JSON-RPC dispatcher, so the response shape
        varies depending on whether auth or group-check fired."""
        if resp.status_code in (401, 403):
            return
        if resp.status_code != 200:
            self.fail(f'Expected 401/403 or 200+error envelope, got '
                      f'{resp.status_code}. {msg}')
        envelope = resp.json()
        if 'error' in envelope:
            return
        result = envelope.get('result', '')
        result_str = str(result)
        if '403' in result_str or '401' in result_str or 'FORBIDDEN' in result_str.upper():
            return
        self.fail(f'Expected forbidden marker; envelope: {envelope}. {msg}')

    # ──────────────────────────────────────────────────────────────────────
    # /health  (auth='public' — anyone)
    # ──────────────────────────────────────────────────────────────────────
    def test_health_no_auth_required(self):
        """/health is auth='public' and must respond without any credentials."""
        resp = self._get(f'{self.PG_BASE}/health')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['module'], 'product_graph_api')
        self.assertEqual(body['api_version'], 'v1')

    # ──────────────────────────────────────────────────────────────────────
    # GET /items — list endpoint, bearer happy path
    # ──────────────────────────────────────────────────────────────────────
    def test_get_items_list_with_bearer(self):
        """Viewer can list items via bearer token."""
        resp = self._get(f'{self.PG_BASE}/items', api_key=self.viewer_api_key)
        self.assertEqual(resp.status_code, 200, f'Body: {resp.text[:500]}')
        body = resp.json()
        self.assertIn('items', body)
        self.assertIn('count', body)
        self.assertIn('total', body)
        part_numbers = [i['part_number'] for i in body['items']]
        self.assertIn(self.seed_item.part_number, part_numbers)

    def test_get_items_list_query_filter(self):
        """The ``q`` query param narrows the list."""
        resp = self._get(
            f'{self.PG_BASE}/items?q={self.seed_item.part_number}',
            api_key=self.viewer_api_key,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['count'], 1)
        self.assertEqual(body['items'][0]['part_number'], self.seed_item.part_number)

    # ──────────────────────────────────────────────────────────────────────
    # GET /items/<id>
    # ──────────────────────────────────────────────────────────────────────
    def test_get_one_item(self):
        resp = self._get(
            f'{self.PG_BASE}/items/{self.seed_item.id}',
            api_key=self.viewer_api_key,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['id'], self.seed_item.id)
        self.assertEqual(body['part_number'], self.seed_item.part_number)
        # detail=True path — these only appear on single-item GETs
        self.assertIn('counts', body)
        self.assertIn('revisions', body)

    def test_get_nonexistent_item_returns_404(self):
        resp = self._get(
            f'{self.PG_BASE}/items/99999999', api_key=self.viewer_api_key,
        )
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body['error']['code'], 'not_found')

    # ──────────────────────────────────────────────────────────────────────
    # GET /items/<id>/where-used
    # ──────────────────────────────────────────────────────────────────────
    def test_where_used_returns_envelope(self):
        resp = self._get(
            f'{self.PG_BASE}/items/{self.seed_item.id}/where-used',
            api_key=self.viewer_api_key,
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['item_id'], self.seed_item.id)
        self.assertEqual(body['part_number'], self.seed_item.part_number)
        self.assertIn('count', body)
        self.assertIn('rows', body)
        self.assertIsInstance(body['rows'], list)

    def test_where_used_404_on_unknown_item(self):
        resp = self._get(
            f'{self.PG_BASE}/items/99999999/where-used',
            api_key=self.viewer_api_key,
        )
        self.assertEqual(resp.status_code, 404)

    # ──────────────────────────────────────────────────────────────────────
    # GET /revisions, /eboms, /releases  — list smoke
    # ──────────────────────────────────────────────────────────────────────
    def test_list_revisions_smoke(self):
        resp = self._get(f'{self.PG_BASE}/revisions', api_key=self.viewer_api_key)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('revisions', body)
        self.assertIn('count', body)

    def test_list_eboms_smoke(self):
        resp = self._get(f'{self.PG_BASE}/eboms', api_key=self.viewer_api_key)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('eboms', body)

    def test_list_releases_smoke(self):
        resp = self._get(f'{self.PG_BASE}/releases', api_key=self.viewer_api_key)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('releases', body)

    def test_get_nonexistent_revision_404(self):
        resp = self._get(
            f'{self.PG_BASE}/revisions/99999999', api_key=self.viewer_api_key,
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_nonexistent_ebom_404(self):
        resp = self._get(
            f'{self.PG_BASE}/eboms/99999999', api_key=self.viewer_api_key,
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_nonexistent_release_404(self):
        resp = self._get(
            f'{self.PG_BASE}/releases/99999999', api_key=self.viewer_api_key,
        )
        self.assertEqual(resp.status_code, 404)

    def test_ebom_explosion_404_on_unknown(self):
        resp = self._get(
            f'{self.PG_BASE}/eboms/99999999/explosion', api_key=self.viewer_api_key,
        )
        self.assertEqual(resp.status_code, 404)

    def test_release_status_404_on_unknown(self):
        resp = self._get(
            f'{self.PG_BASE}/releases/99999999/status', api_key=self.viewer_api_key,
        )
        self.assertEqual(resp.status_code, 404)

    # ──────────────────────────────────────────────────────────────────────
    # POST /items — create (mutating, requires group_pg_engineer per R6)
    # ──────────────────────────────────────────────────────────────────────
    def test_create_item_as_engineer(self):
        """Engineer can create an item via POST /items with bearer auth."""
        resp = self._post_json(
            f'{self.PG_BASE}/items',
            payload={
                'name': 'HTTP-created item',
                'item_type': 'part',
                'category_id': self.cat.id,
            },
            api_key=self.engineer_api_key,
        )
        self.assertEqual(resp.status_code, 200, f'Body: {resp.text[:500]}')
        envelope = resp.json()
        # type='json' wraps in JSON-RPC envelope.
        self.assertNotIn('error', envelope, f'JSON-RPC error: {envelope.get("error")}')
        result = envelope['result']
        self.assertIn('id', result)
        self.assertEqual(result['name'], 'HTTP-created item')
        new = self.env['pg.item'].browse(result['id'])
        self.assertTrue(new.exists())
        self.assertEqual(new.name, 'HTTP-created item')

    def test_create_item_as_viewer_forbidden(self):
        """Viewer bearer key cannot create — group_pg_engineer check fires.

        ``_check_group`` returns a wrapped Response with HTTP 403; the JSON-RPC
        dispatcher passes that through, so we see the 403 directly.
        """
        resp = self._post_json(
            f'{self.PG_BASE}/items',
            payload={
                'name': 'Viewer attempt',
                'item_type': 'part',
                'category_id': self.cat.id,
            },
            api_key=self.viewer_api_key,
        )
        # JSON-RPC dispatch wraps a 403 Response as result; bearer auth may
        # also short-circuit with plain 401/403. Helper accepts all shapes.
        self._assert_forbidden(resp, 'Viewer POST should not succeed')

    # ──────────────────────────────────────────────────────────────────────
    # AUTH GATES — missing / invalid credentials
    # ──────────────────────────────────────────────────────────────────────
    def test_get_items_no_auth_rejected(self):
        """``auth='bearer'`` GET without credentials → 401."""
        # Ensure no session is carried over from a previous test.
        self._safe_logout()
        resp = self.url_open(
            self._url(f'{self.PG_BASE}/items'),
            allow_redirects=False,
            timeout=20,
        )
        self.assertEqual(
            resp.status_code, 401,
            f'Expected 401 from bearer auth, got {resp.status_code}',
        )

    def test_post_items_no_auth_rejected(self):
        """POST /items without credentials → JSON-RPC error envelope wrapping
        a 401 Unauthorized.

        Because the route is ``type='json'``, the JSON-RPC dispatcher catches
        the Unauthorized raised by ``_auth_method_bearer`` and returns it as
        an error envelope with HTTP 200 (per Odoo's JSON-RPC contract). The
        envelope's ``error.data.name`` carries ``werkzeug.exceptions.Unauthorized``
        so callers can still distinguish auth failure from other errors.
        """
        self._safe_logout()
        resp = self.url_open(
            self._url(f'{self.PG_BASE}/items'),
            data=json.dumps({
                'jsonrpc': '2.0', 'method': 'call', 'params': {
                    'name': 'no-auth attempt',
                    'item_type': 'part',
                    'category_id': self.cat.id,
                },
            }).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            timeout=20,
        )
        # JSON-RPC wraps the Unauthorized: HTTP 200 with error.data.name set.
        self.assertEqual(resp.status_code, 200)
        envelope = resp.json()
        self.assertIn('error', envelope)
        self.assertEqual(
            envelope['error']['data']['name'],
            'werkzeug.exceptions.Unauthorized',
        )

    def test_invalid_bearer_token_rejected(self):
        """Garbage Bearer token must not authenticate."""
        self._safe_logout()
        resp = self.url_open(
            self._url(f'{self.PG_BASE}/items'),
            headers={'Authorization': 'Bearer not-a-real-key-xxxxxx'},
            allow_redirects=False,
            timeout=20,
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn('bearer', resp.headers.get('WWW-Authenticate', '').lower())

    # ──────────────────────────────────────────────────────────────────────
    # BEARER (API-key) AUTH — positive coverage of R6
    # ──────────────────────────────────────────────────────────────────────
    def test_bearer_key_authenticates_engineer(self):
        """A valid engineer API key authenticates POST /items end-to-end."""
        resp = self._post_json(
            f'{self.PG_BASE}/items',
            payload={
                'name': 'Bearer-authenticated create',
                'item_type': 'part',
                'category_id': self.cat.id,
            },
            api_key=self.engineer_api_key,
        )
        self.assertEqual(resp.status_code, 200, f'Body: {resp.text[:500]}')
        envelope = resp.json()
        self.assertNotIn('error', envelope, f'Unexpected error: {envelope.get("error")}')
        result = envelope['result']
        self.assertEqual(result['name'], 'Bearer-authenticated create')
        # Verify the new record was created by the engineer user, not admin.
        new = self.env['pg.item'].browse(result['id'])
        self.assertTrue(new.exists())
        self.assertEqual(new.create_uid, self.engineer)

    def test_bearer_key_for_viewer_rejected_on_post_items(self):
        """Viewer's valid bearer token fails the engineer group check (R6)."""
        resp = self._post_json(
            f'{self.PG_BASE}/items',
            payload={
                'name': 'Viewer bearer attempt',
                'item_type': 'part',
                'category_id': self.cat.id,
            },
            api_key=self.viewer_api_key,
        )
        self._assert_forbidden(resp, 'Viewer bearer POST must not succeed')

    def test_no_session_cookie_required_with_bearer(self):
        """Bearer header alone authenticates — no session cookie needed."""
        # Explicitly drop any session that prior tests may have left.
        self._safe_logout()
        resp = self.url_open(
            self._url(f'{self.PG_BASE}/items'),
            headers={'Authorization': f'Bearer {self.viewer_api_key}'},
            timeout=20,
        )
        self.assertEqual(resp.status_code, 200, f'Body: {resp.text[:500]}')
        body = resp.json()
        self.assertIn('items', body)

    def test_session_cookie_alone_is_rejected(self):
        """Session cookie WITHOUT bearer header → 401 on these routes.

        Bearer auth's dual-mode fallthrough only fires when Sec-Fetch
        navigation headers are present (same-origin browser navigation).
        API clients carrying only a session cookie are now rejected, which
        is the documented R6 behaviour.
        """
        # Authenticate (sets session cookie on self.opener / self.session).
        self.authenticate(self.viewer.login, self.viewer.login)
        # Open the URL with the cookie but no Authorization header and no
        # Sec-Fetch-* navigation headers.
        resp = self.url_open(
            self._url(f'{self.PG_BASE}/items'),
            allow_redirects=False,
            timeout=20,
        )
        self.assertEqual(
            resp.status_code, 401,
            'Session-only request must be rejected by auth=bearer when no '
            'Sec-Fetch navigation headers are present',
        )

    # ──────────────────────────────────────────────────────────────────────
    # GROUP GATES — verify each mutating endpoint enforces its group
    # ──────────────────────────────────────────────────────────────────────
    def test_release_endpoint_requires_approver(self):
        """POST /releases requires group_pg_approver — engineer rejected."""
        resp = self._post_json(
            f'{self.PG_BASE}/releases',
            payload={
                'ebom_id': 99999999,  # bogus; we only care about the gate
                'release_reason': 'gate test',
            },
            api_key=self.engineer_api_key,
        )
        self._assert_forbidden(resp, 'Engineer must be rejected from /releases')

    def test_revision_release_endpoint_requires_approver(self):
        """POST /revisions/<id>/release requires group_pg_approver."""
        resp = self._post_json(
            f'{self.PG_BASE}/revisions/1/release',
            payload={},
            api_key=self.engineer_api_key,
        )
        self._assert_forbidden(resp, 'Engineer must be rejected from revision release')

    def test_revision_submit_endpoint_requires_engineer(self):
        """POST /revisions/<id>/submit requires group_pg_engineer."""
        resp = self._post_json(
            f'{self.PG_BASE}/revisions/1/submit',
            payload={},
            api_key=self.viewer_api_key,
        )
        self._assert_forbidden(resp, 'Viewer must be rejected from revision submit')

    # ──────────────────────────────────────────────────────────────────────
    # CONTENT-TYPE
    # ──────────────────────────────────────────────────────────────────────
    def test_get_items_returns_json_mimetype(self):
        resp = self._get(f'{self.PG_BASE}/items', api_key=self.viewer_api_key)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('application/json', resp.headers.get('Content-Type', ''))

    # ──────────────────────────────────────────────────────────────────────
    # RATE LIMITING (B8.4) — shared _safe wrapper, Postgres-backed
    # fixed-window counter (pg.api.rate.limit).
    #
    # Each test lowers the config-parameter cap to a small number so it
    # doesn't need dozens of HTTP round trips to trip the limiter, and
    # clears any pre-existing counter rows first so results aren't
    # sensitive to how many /items calls earlier tests in this class made.
    # ir.config_parameter writes and pg.api.rate.limit rows made inside a
    # test method are rolled back with the rest of that test's savepoint,
    # so this never leaks into sibling tests.
    # ──────────────────────────────────────────────────────────────────────
    def _reset_rate_limit_counters(self):
        self.env['pg.api.rate.limit'].sudo().search([]).unlink()

    def _set_rate_limit(self, limit):
        self.env['ir.config_parameter'].sudo().set_param(
            'productgraph.api_rate_limit_per_minute', str(limit))

    def test_rate_limit_first_hit_does_not_error_with_empty_counter_table(self):
        """First request for a fresh (identity, route) pair must succeed —
        the upsert's INSERT branch has to work with zero prior rows."""
        self._reset_rate_limit_counters()
        self._set_rate_limit(50)
        resp = self._get(f'{self.PG_BASE}/items', api_key=self.viewer_api_key)
        self.assertEqual(resp.status_code, 200, f'Body: {resp.text[:500]}')

    def test_rate_limit_returns_429_after_cap_on_get(self):
        """A cheap GET route (list_items) starts returning 429 once a
        single bearer key exceeds its configured per-minute cap; requests
        under the cap keep succeeding."""
        self._reset_rate_limit_counters()
        self._set_rate_limit(3)
        statuses = [
            self._get(f'{self.PG_BASE}/items', api_key=self.viewer_api_key).status_code
            for _i in range(3)
        ]
        self.assertEqual(statuses, [200, 200, 200], f'Under-cap requests: {statuses}')
        over_cap = self._get(f'{self.PG_BASE}/items', api_key=self.viewer_api_key)
        self.assertEqual(over_cap.status_code, 429, f'Body: {over_cap.text[:500]}')
        body = over_cap.json()
        self.assertEqual(body['error']['code'], 'rate_limited')

    def test_rate_limit_returns_429_on_mutating_route_without_partial_write(self):
        """POST /items (create_item) is throttled by the same shared check.
        Once the cap trips, the rejected call must NOT have created a
        pg.item — the limiter runs before the handler."""
        self._reset_rate_limit_counters()
        self._set_rate_limit(1)
        first = self._post_json(
            f'{self.PG_BASE}/items',
            payload={'name': 'RL first create', 'item_type': 'part',
                     'category_id': self.cat.id},
            api_key=self.engineer_api_key,
        )
        self.assertEqual(first.status_code, 200, f'Body: {first.text[:500]}')

        blocked_name = 'RL second create - must not exist'
        second = self._post_json(
            f'{self.PG_BASE}/items',
            payload={'name': blocked_name, 'item_type': 'part',
                     'category_id': self.cat.id},
            api_key=self.engineer_api_key,
        )
        self.assertEqual(second.status_code, 429, f'Body: {second.text[:500]}')
        self.assertEqual(
            self.env['pg.item'].search_count([('name', '=', blocked_name)]), 0,
            'Throttled create_item call must not have written a pg.item record',
        )

    def test_rate_limit_independent_per_api_key(self):
        """Two different users' bearer keys are throttled independently —
        one user tripping the cap must not affect another user's quota on
        the same route."""
        self._reset_rate_limit_counters()
        self._set_rate_limit(1)
        viewer_first = self._get(f'{self.PG_BASE}/items', api_key=self.viewer_api_key)
        self.assertEqual(viewer_first.status_code, 200)
        viewer_second = self._get(f'{self.PG_BASE}/items', api_key=self.viewer_api_key)
        self.assertEqual(viewer_second.status_code, 429)

        # A different key belonging to a different user is still fresh.
        approver_first = self._get(f'{self.PG_BASE}/items', api_key=self.approver_api_key)
        self.assertEqual(approver_first.status_code, 200, f'Body: {approver_first.text[:500]}')

    def test_rate_limit_disabled_when_cap_is_zero(self):
        """Setting the cap to 0 disables throttling for that caller class —
        the documented post-deploy tuning escape hatch (no code revert)."""
        self._reset_rate_limit_counters()
        self._set_rate_limit(0)
        for _i in range(5):
            resp = self._get(f'{self.PG_BASE}/items', api_key=self.viewer_api_key)
            self.assertEqual(resp.status_code, 200)

    def test_rate_limit_window_rollover_resets_counter(self):
        """A caller who trips the cap in one fixed window is allowed again
        once the window rolls over. Exercised at the model layer with a
        mocked clock rather than sleeping 60 real seconds."""
        Counter = self.env['pg.api.rate.limit'].sudo()
        Counter.search([]).unlink()
        base = 1_700_000_000  # arbitrary fixed epoch second
        with patch(
            'odoo.addons.product_graph_api.models.pg_api_rate_limit.time.time',
            return_value=float(base),
        ):
            self.assertEqual(Counter._hit('uid:999999', 'test.route'), 1)
            self.assertEqual(Counter._hit('uid:999999', 'test.route'), 2)
        with patch(
            'odoo.addons.product_graph_api.models.pg_api_rate_limit.time.time',
            return_value=float(base + Counter.WINDOW_SECONDS),
        ):
            # New window — the counter starts over at 1, not 3.
            self.assertEqual(Counter._hit('uid:999999', 'test.route'), 1)
