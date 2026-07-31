# -*- coding: utf-8 -*-
"""B9.9 — Concurrent CAD imports on the same pg.item.

_do_pull() had zero serialization: two overlapping pulls (cron-vs-manual,
cron-vs-cron, or two different connectors racing on the same part_number)
could both `existing.write(vals)` the same pg.item with plain
last-writer-wins MVCC semantics — no error, no trace, just a silently
clobbered write.

The fix adds:
  1. A per-part_number pg_advisory_xact_lock in _do_pull, keyed by
     (_CAD_LOCK_NAMESPACE, crc32(part_number)) — serializes ANY two
     concurrent writers (same connector or different) targeting the
     same item.
  2. A same-connector overlap guard at the top of action_pull() that
     rejects a second run while one is already state='running'.

A genuine two-connection race is fiddly to drive inside a single
TransactionCase (the test transaction is one session). Following the
precedent set in product_graph_ebom/tests/test_closure.py
(test_closure_advisory_lock_is_taken), the primary lock test spies on
cr.execute to prove the lock statement is actually issued with the
right (namespace, key) pair for each part_number touched by a pull —
that's the mechanism a real second session would block on.
"""
from unittest import mock

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.product_graph_cad.connectors import (
    BaseCadConnector,
    CONNECTOR_REGISTRY,
    CadDocument,
)
from odoo.addons.product_graph_cad.models.pg_cad_connector import (
    _CAD_LOCK_NAMESPACE,
    _advisory_lock_key,
)
from odoo.addons.product_graph_ebom.models.pg_ebom_closure import (
    CLOSURE_LOCK_NAMESPACE,
)


class _LockStubDriver(BaseCadConnector):
    """Test driver — returns whatever list_documents was monkey-patched
    to. Distinct registry code from test_scheduled_sync's _StubDriver so
    the two test modules can't collide if both are loaded in one run."""
    code = 'stub_lock_test'
    label = 'Stub (lock test)'

    _stub_docs: list = []

    def fetch_metadata(self):
        return {'code': self.code, 'reachable': True}

    def list_documents(self):
        return list(type(self)._stub_docs)

    def pull_document(self, ref):
        for doc in self.list_documents():
            if doc.part_number == ref:
                return doc
        return None


@tagged('post_install', '-at_install', 'product_graph')
class TestCadImportLock(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not any(code == _LockStubDriver.code for code, _ in CONNECTOR_REGISTRY):
            CONNECTOR_REGISTRY.append((_LockStubDriver.code, _LockStubDriver))
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)

    @classmethod
    def tearDownClass(cls):
        for i, (code, cls_) in enumerate(list(CONNECTOR_REGISTRY)):
            if code == _LockStubDriver.code:
                CONNECTOR_REGISTRY.pop(i)
                break
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        _LockStubDriver._stub_docs = []

    def _make_connector(self, code='LOCK-1'):
        return self.env['pg.cad.connector'].create({
            'code': code,
            'name': 'Lock test connector',
            'driver_code': _LockStubDriver.code,
            'state': 'active',
            'config_json': '{}',
        })

    # ──────────────────────────────────────────────────────────────────
    # Namespace sanity
    # ──────────────────────────────────────────────────────────────────
    def test_lock_namespace_is_signed_int32_and_distinct_from_closure(self):
        # pg_advisory_xact_lock(int4, int4) requires signed-32-bit values.
        self.assertIsInstance(_CAD_LOCK_NAMESPACE, int)
        self.assertGreater(_CAD_LOCK_NAMESPACE, 0)
        self.assertLess(_CAD_LOCK_NAMESPACE, 2 ** 31)
        # Must never collide with the EBOM closure lock namespace — they
        # protect unrelated resources and share the same Postgres advisory
        # lock table.
        self.assertNotEqual(_CAD_LOCK_NAMESPACE, CLOSURE_LOCK_NAMESPACE)

    def test_advisory_lock_key_is_deterministic_signed_int32(self):
        key_a = _advisory_lock_key('PG-PRT-0001')
        key_b = _advisory_lock_key('PG-PRT-0001')
        key_c = _advisory_lock_key('PG-PRT-0002')
        self.assertEqual(key_a, key_b, 'same input must hash to the same key')
        self.assertNotEqual(key_a, key_c)
        for key in (key_a, key_c):
            self.assertGreaterEqual(key, -(2 ** 31))
            self.assertLess(key, 2 ** 31)

    # ──────────────────────────────────────────────────────────────────
    # Task — advisory-lock query is issued per part_number during _do_pull
    # ──────────────────────────────────────────────────────────────────
    def test_do_pull_issues_advisory_lock_per_part_number(self):
        conn = self._make_connector()
        _LockStubDriver._stub_docs = [
            CadDocument(part_number='LOCK-A', name='Lock A', item_type='part'),
            CadDocument(part_number='LOCK-B', name='Lock B', item_type='part'),
        ]

        original_execute = self.env.cr.execute
        calls = []

        def spy_execute(query, *args, **kwargs):
            if 'pg_advisory_xact_lock' in (query or ''):
                calls.append((query, args, kwargs))
            return original_execute(query, *args, **kwargs)

        with mock.patch.object(self.env.cr, 'execute', side_effect=spy_execute):
            conn.action_pull()

        self.assertEqual(
            len(calls), 2,
            'expected exactly one pg_advisory_xact_lock call per '
            'part_number pulled',
        )
        seen_keys = set()
        for _query, args, kwargs in calls:
            params = args[0] if args else kwargs.get('params')
            self.assertEqual(params[0], _CAD_LOCK_NAMESPACE)
            seen_keys.add(params[1])
        self.assertEqual(
            seen_keys,
            {_advisory_lock_key('LOCK-A'), _advisory_lock_key('LOCK-B')},
        )

    def test_two_connectors_same_part_number_no_lost_write(self):
        """Two different connectors (e.g. solidworks + onshape stand-ins)
        both emitting a descriptor for the same part_number, pulled
        back-to-back, must leave exactly one pg.item with the LAST
        writer's data — not a duplicate, not a partially-applied write."""
        conn_a = self._make_connector(code='LOCK-CONN-A')
        conn_b = self._make_connector(code='LOCK-CONN-B')

        _LockStubDriver._stub_docs = [
            CadDocument(part_number='SHARED-PN', name='From A',
                        item_type='part'),
        ]
        conn_a.action_pull()

        _LockStubDriver._stub_docs = [
            CadDocument(part_number='SHARED-PN', name='From B',
                        item_type='part'),
        ]
        conn_b.action_pull()

        items = self.env['pg.item'].search([('part_number', '=', 'SHARED-PN')])
        self.assertEqual(len(items), 1, 'must not duplicate the item row')
        self.assertEqual(items.name, 'From B')

    # ──────────────────────────────────────────────────────────────────
    # Task — same-connector overlap guard
    # ──────────────────────────────────────────────────────────────────
    def test_action_pull_rejects_overlap_on_same_connector(self):
        conn = self._make_connector()
        # Simulate an in-flight run left over from a slow driver.
        stuck_run = self.env['pg.cad.import'].create({
            'connector_id': conn.id,
            'state': 'running',
        })
        before_count = self.env['pg.cad.import'].search_count(
            [('connector_id', '=', conn.id)])

        with self.assertRaises(UserError):
            conn.action_pull()

        after_count = self.env['pg.cad.import'].search_count(
            [('connector_id', '=', conn.id)])
        self.assertEqual(
            before_count, after_count,
            'a rejected overlapping pull must not create a second '
            "'running' pg.cad.import row",
        )
        self.assertEqual(stuck_run.state, 'running')

    def test_action_pull_allows_sequential_non_overlapping_runs(self):
        """The overlap guard must not false-positive on ordinary
        sequential (non-overlapping) syncs — the regression this guards
        against is spurious rejection of back-to-back manual syncs."""
        conn = self._make_connector()
        _LockStubDriver._stub_docs = [
            CadDocument(part_number='SEQ-1', name='Seq 1', item_type='part'),
        ]
        conn.action_pull()
        conn.action_pull()
        conn.action_pull()
        runs = self.env['pg.cad.import'].search([('connector_id', '=', conn.id)])
        self.assertEqual(len(runs), 3)
        self.assertTrue(all(r.state == 'completed' for r in runs))
