# -*- coding: utf-8 -*-
import json
import os
import tempfile

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


SAMPLE_PAYLOAD = {
    "documents": [
        {
            "part_number": "CAD-ASM-001",
            "name": "Top assembly imported from CAD",
            "description": "<p>From CAD vault</p>",
            "item_type": "assembly",
            "properties": {"width_mm": 600},
            "ebom": [
                {"child_part_number": "CAD-PRT-001", "qty": 2.0,
                 "ref_designator": "P1"},
                {"child_part_number": "CAD-PRT-002", "qty": 1.0,
                 "ref_designator": "P2"},
            ],
        },
        {
            "part_number": "CAD-PRT-001",
            "name": "Side panel",
            "item_type": "part",
            "properties": {"width_mm": 600, "thickness_mm": 18},
            "ebom": [],
        },
        {
            "part_number": "CAD-PRT-002",
            "name": "Hinge bracket",
            "item_type": "part",
            "properties": {"material": "steel"},
            "ebom": [],
        },
    ]
}


@tagged('post_install', '-at_install', 'product_graph')
class TestPgCadJsonDriver(TransactionCase):

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

    def _write_fixture(self, payload=None):
        path = tempfile.NamedTemporaryFile(
            suffix='.json', delete=False, mode='w',
        )
        try:
            json.dump(payload if payload is not None else SAMPLE_PAYLOAD, path)
            path.close()
            self.addCleanup(os.unlink, path.name)
            return path.name
        finally:
            if not path.closed:
                path.close()

    def _make_connector(self, path):
        return self.env['pg.cad.connector'].create({
            'code': 'JSON-1',
            'name': 'JSON test connector',
            'driver_code': 'json_file',
            'state': 'active',
            'config_json': json.dumps({'path': path}),
        })

    # ──────────────────────────────────────────────────────────────────
    # Connection
    # ──────────────────────────────────────────────────────────────────
    def test_test_connection_reaches_file(self):
        path = self._write_fixture()
        conn = self._make_connector(path)
        conn.action_test_connection()
        # No raise — chatter posted; verify by reading the latest message.
        msgs = conn.message_ids.filtered(lambda m: 'Test connection' in (m.body or ''))
        self.assertTrue(msgs)

    def test_test_connection_unreachable_raises(self):
        conn = self._make_connector('/nonexistent/path.json')
        with self.assertRaises(UserError):
            conn.action_test_connection()

    # ──────────────────────────────────────────────────────────────────
    # Pull — happy path
    # ──────────────────────────────────────────────────────────────────
    def test_pull_creates_items_and_ebom(self):
        path = self._write_fixture()
        conn = self._make_connector(path)
        conn.action_pull()
        # Latest import should be completed.
        last = conn.import_ids.sorted('id', reverse=True)[:1]
        self.assertEqual(last.state, 'completed')
        self.assertEqual(last.line_count, 3)
        self.assertEqual(last.created_count, 3)
        # Items must exist.
        asm = self.env['pg.item'].search(
            [('part_number', '=', 'CAD-ASM-001')], limit=1)
        self.assertTrue(asm)
        self.assertEqual(asm.item_type, 'assembly')
        # EBOM must have 2 lines.
        ebom = self.env['pg.ebom'].search(
            [('root_item_id', '=', asm.id)], limit=1)
        self.assertTrue(ebom)
        self.assertEqual(len(ebom.ebom_line_ids), 2)
        # Revision must be draft.
        rev = self.env['pg.revision'].search(
            [('item_id', '=', asm.id)], limit=1)
        self.assertTrue(rev)
        self.assertEqual(rev.state, 'draft')

    # ──────────────────────────────────────────────────────────────────
    # Pull — idempotent re-import
    # ──────────────────────────────────────────────────────────────────
    def test_re_pull_updates_not_duplicates(self):
        path = self._write_fixture()
        conn = self._make_connector(path)
        conn.action_pull()
        first_run = conn.import_ids.sorted('id', reverse=True)[:1]
        self.assertEqual(first_run.created_count, 3)
        conn.action_pull()
        second_run = conn.import_ids.sorted('id', reverse=True)[:1]
        self.assertEqual(second_run.state, 'completed')
        # No new items created — second run should be all updates.
        self.assertEqual(second_run.created_count, 0)
        self.assertEqual(second_run.updated_count, 3)
        # Item count stays at 3 (plus whatever the test env shipped).
        items = self.env['pg.item'].search([
            ('part_number', 'in', ['CAD-ASM-001', 'CAD-PRT-001', 'CAD-PRT-002'])
        ])
        self.assertEqual(len(items), 3)

    # ──────────────────────────────────────────────────────────────────
    # Pull — failure rollback
    # ──────────────────────────────────────────────────────────────────
    def test_pull_failure_records_failed_run(self):
        conn = self._make_connector('/nonexistent/path.json')
        conn.action_pull()
        last = conn.import_ids.sorted('id', reverse=True)[:1]
        self.assertEqual(last.state, 'failed')
        self.assertTrue(last.failure_reason)
        self.assertEqual(conn.last_sync_status, 'failed')

    # ──────────────────────────────────────────────────────────────────
    # Last-sync bookkeeping
    # ──────────────────────────────────────────────────────────────────
    def test_pull_updates_last_sync_at(self):
        path = self._write_fixture()
        conn = self._make_connector(path)
        self.assertFalse(conn.last_sync_at)
        conn.action_pull()
        self.assertTrue(conn.last_sync_at)
        self.assertEqual(conn.last_sync_status, 'ok')

    # ──────────────────────────────────────────────────────────────────
    # Import audit immutability
    # ──────────────────────────────────────────────────────────────────
    def test_completed_import_is_read_only(self):
        path = self._write_fixture()
        conn = self._make_connector(path)
        conn.action_pull()
        last = conn.import_ids.sorted('id', reverse=True)[:1]
        with self.assertRaises(UserError):
            last.write({'state': 'running'})
