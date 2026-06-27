# -*- coding: utf-8 -*-
"""PG-135 — Scheduled CAD Connector Sync.

Tests the cron dispatch + schema validation + draft-only enforcement +
error / partial / success status surfacing on pg.cad.connector.

The driver layer is mocked with an in-process subclass of BaseCadConnector
so we don't depend on real SOLIDWORKS / Onshape / JSON-file plumbing for
the cron behaviour under test. The mock driver is registered in
CONNECTOR_REGISTRY for the duration of each test and removed in tearDown.
"""
import itertools
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.product_graph_cad.connectors import (
    BaseCadConnector,
    CONNECTOR_REGISTRY,
    CadBomLine,
    CadDocument,
)


class _StubDriver(BaseCadConnector):
    """Test driver — returns whatever list_documents was monkey-patched to."""
    code = 'stub_test'
    label = 'Stub (test)'

    # Class-level monkey-patch points so the test can swap behaviour
    # without re-registering for each scenario.
    _stub_docs: list = []
    _stub_raise: Exception | None = None

    def fetch_metadata(self):
        return {'code': self.code, 'reachable': True}

    def list_documents(self):
        if type(self)._stub_raise is not None:
            raise type(self)._stub_raise
        return list(type(self)._stub_docs)

    def pull_document(self, ref):
        for doc in self.list_documents():
            if doc.part_number == ref:
                return doc
        return None


@tagged('post_install', '-at_install', 'product_graph')
class TestScheduledSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Register the stub driver in the global registry — Odoo's
        # _selection_from_registry will pick it up because it re-imports
        # the connectors module on every selection eval.
        if not any(code == _StubDriver.code for code, _ in CONNECTOR_REGISTRY):
            CONNECTOR_REGISTRY.append((_StubDriver.code, _StubDriver))
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)

    @classmethod
    def tearDownClass(cls):
        # Leave the registry as we found it so other test modules don't
        # see a phantom 'stub_test' driver option.
        for i, (code, cls_) in enumerate(list(CONNECTOR_REGISTRY)):
            if code == _StubDriver.code:
                CONNECTOR_REGISTRY.pop(i)
                break
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        # Reset stub state per test.
        _StubDriver._stub_docs = []
        _StubDriver._stub_raise = None

    # ──────────────────────────────────────────────────────────────────
    # Fixtures
    # ──────────────────────────────────────────────────────────────────
    _code_seq = itertools.count(1)

    def _make_connector(self, schedule='daily', last_sync_at=False, state='active'):
        return self.env['pg.cad.connector'].create({
            'code': f'STUB-{schedule}-{next(self._code_seq)}',
            'name': f'Stub {schedule}',
            'driver_code': _StubDriver.code,
            'state': state,
            'sync_schedule': schedule,
            'last_sync_at': last_sync_at or False,
            'config_json': '{}',
        })

    # ──────────────────────────────────────────────────────────────────
    # Cron dispatch
    # ──────────────────────────────────────────────────────────────────
    def test_cron_skips_manual_connectors(self):
        conn = self._make_connector(schedule='manual')
        _StubDriver._stub_docs = [
            CadDocument(part_number='X-1', name='X', item_type='part'),
        ]
        self.env['pg.cad.connector']._cron_run_scheduled_syncs()
        # Manual connectors are excluded from the search domain.
        self.assertFalse(conn.last_sync_at)
        self.assertEqual(conn.last_sync_status, 'never')

    def test_cron_runs_due_daily_connectors(self):
        two_days_ago = fields.Datetime.now() - timedelta(days=2)
        conn = self._make_connector(
            schedule='daily', last_sync_at=two_days_ago)
        _StubDriver._stub_docs = [
            CadDocument(part_number='X-2', name='X-2', item_type='part'),
        ]
        before = fields.Datetime.now()
        self.env['pg.cad.connector']._cron_run_scheduled_syncs()
        conn.invalidate_recordset()
        self.assertTrue(conn.last_sync_at)
        self.assertGreaterEqual(conn.last_sync_at, before)
        self.assertEqual(conn.last_sync_status, 'ok')

    def test_cron_skips_recent_daily_connectors(self):
        one_hour_ago = fields.Datetime.now() - timedelta(hours=1)
        conn = self._make_connector(
            schedule='daily', last_sync_at=one_hour_ago)
        _StubDriver._stub_docs = [
            CadDocument(part_number='X-3', name='X-3', item_type='part'),
        ]
        self.env['pg.cad.connector']._cron_run_scheduled_syncs()
        conn.invalidate_recordset()
        # last_sync_at must be unchanged (within rounding tolerance).
        self.assertEqual(conn.last_sync_at, one_hour_ago)

    # ──────────────────────────────────────────────────────────────────
    # Draft-only enforcement
    # ──────────────────────────────────────────────────────────────────
    def test_sync_creates_draft_items_only(self):
        conn = self._make_connector(schedule='daily')
        _StubDriver._stub_docs = [
            CadDocument(part_number=f'DRAFT-{i}', name=f'Item {i}',
                        item_type='part')
            for i in range(3)
        ]
        conn.action_sync_now()
        items = self.env['pg.item'].search([
            ('part_number', 'in', ['DRAFT-0', 'DRAFT-1', 'DRAFT-2']),
        ])
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(
                item.state, 'concept',
                "CAD sync must create items in 'concept' (draft) state.",
            )
        # Each new item should have a draft revision created.
        revisions = self.env['pg.revision'].search([
            ('item_id', 'in', items.ids),
        ])
        self.assertEqual(len(revisions), 3)
        for rev in revisions:
            self.assertEqual(rev.state, 'draft')

    def test_sync_cannot_modify_released_item(self):
        # Pre-create an item, fast-forward through states, force release
        # via the bypass context flag (no real release wizard in this test).
        item = self.env['pg.item'].create({
            'name': 'Released Pre-existing',
            'part_number': 'LOCKED-1',
            'item_type': 'part',
            'category_id': self.env.ref(
                'product_graph_base.cat_mechanical').id,
        })
        # Direct state writes are guarded; use the bypass context to set up the fixture.
        item.with_context(_pg_release_bypass=True).write({'state': 'released'})
        self.assertEqual(item.state, 'released')
        original_name = item.name

        conn = self._make_connector(schedule='daily')
        _StubDriver._stub_docs = [
            CadDocument(part_number='LOCKED-1', name='HIJACK',
                        item_type='assembly', description='evil'),
        ]
        conn.action_sync_now()

        item.invalidate_recordset()
        self.assertEqual(item.name, original_name,
                         "Released item must not be mutated by CAD sync.")
        self.assertEqual(item.state, 'released')
        # Trace row must record rejection.
        run = conn.import_ids.sorted('id', reverse=True)[:1]
        rejected = run.line_ids.filtered(lambda l: l.action == 'rejected')
        self.assertEqual(len(rejected), 1)
        self.assertIn('released', (rejected.reason or '').lower())

    # ──────────────────────────────────────────────────────────────────
    # Status surfacing
    # ──────────────────────────────────────────────────────────────────
    def test_sync_failure_surfaces_on_connector(self):
        conn = self._make_connector(
            schedule='daily',
            last_sync_at=fields.Datetime.now() - timedelta(days=2),
        )
        _StubDriver._stub_raise = RuntimeError("boom from driver")
        self.env['pg.cad.connector']._cron_run_scheduled_syncs()
        conn.invalidate_recordset()
        self.assertEqual(conn.last_sync_status, 'failed')
        self.assertIn('boom from driver', conn.last_sync_detail or '')

    def test_sync_partial_failure_status(self):
        conn = self._make_connector(schedule='daily')
        _StubDriver._stub_docs = [
            CadDocument(part_number='OK-1', name='OK 1', item_type='part'),
            CadDocument(part_number='OK-2', name='OK 2', item_type='part'),
            CadDocument(part_number='OK-3', name='OK 3', item_type='part'),
            CadDocument(part_number='OK-4', name='OK 4', item_type='part'),
            # Bad item_type — should be rejected by schema validation.
            CadDocument(part_number='BAD-1', name='Bad',
                        item_type='not_a_real_type'),
        ]
        conn.action_sync_now()
        run = conn.import_ids.sorted('id', reverse=True)[:1]
        self.assertEqual(len(run.line_ids), 5)
        self.assertEqual(run.created_count, 4)
        self.assertEqual(run.rejected_count, 1)
        self.assertEqual(conn.last_sync_status, 'partial')

    # ──────────────────────────────────────────────────────────────────
    # Defense-in-depth: descriptor-level target_state must not promote.
    # ──────────────────────────────────────────────────────────────────
    def test_import_line_strips_target_state(self):
        conn = self._make_connector(schedule='manual')
        line = self.env['pg.cad.import.line'].create({
            'import_id': self.env['pg.cad.import'].create({
                'connector_id': conn.id,
                'state': 'running',
            }).id,
            'part_number': 'X',
            'action': 'created',
            # These keys are silently stripped by the override.
            'target_state': 'released',
        })
        # No AttributeError, no smuggled state — line exists with action='created'.
        self.assertEqual(line.action, 'created')
        # The field doesn't even exist on the model — getattr default proves it.
        self.assertFalse(hasattr(line, 'target_state'))
