# -*- coding: utf-8 -*-
"""B6.6 — advisory row-lock on the parent pg.eco row before writing lines
that reference eco state (and before action_release()'s own check-then-act).

pg.eco.line.write() blocks edits to non-status/notes fields once the
parent ECO is released, based on `eco_state` (a related, store=True mirror
of eco_id.state). pg.eco.action_release() does its own line validation +
state write. Neither locked the parent pg.eco row, so two concurrent
transactions — one editing a line, one releasing the ECO — could each read
a pre-transition snapshot, both pass their checks, and both commit.

These tests verify:
  1. pg.eco.line.write() issues a `SELECT ... FOR UPDATE` against the
     parent pg_eco row before doing anything else (spy on cr.execute,
     mirroring the existing advisory-lock test in product_graph_ebom).
  2. pg.eco.action_release() issues the same lock against its own row
     before validation/write.
  3. A genuine two-cursor test: a second, separate DB connection attempting
     the same line write while action_release()'s transaction holds the
     lock (uncommitted) is blocked — proven via a short `lock_timeout`
     rather than letting the test hang.
  4. Single-threaded regression: the internal related-field recompute that
     action_release()'s own write() triggers on every pg.eco.line row
     re-acquires the SAME eco-row lock within the SAME transaction without
     raising or hanging (Postgres treats that as a no-op).
  5. Empty-recordset guard: `_lock_for_update()` on no records is a no-op.
"""
from unittest import mock

import psycopg2

from odoo import api
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgEcoRowLock(TransactionCase):

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
        # SoD / auto-close off — these tests exercise explicit transitions
        # single-user, same as test_pg_eco_state_machine.py.
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.enforce_sod', 'False',
        )
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.auto_close_eco_on_all_revisions_released', 'False',
        )

    def _make_item(self):
        item = self.env['pg.item'].create({
            'name': 'Row-Lock Test Item',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    def _make_revision(self, item, code='A'):
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'Initial release of the row-lock test item.',
        })

    def _release_revision(self, item, code='A'):
        rev = self._make_revision(item, code=code)
        rev.action_submit_for_review()
        rev.action_release()
        return rev

    def _make_eco(self):
        item = self._make_item()
        rev_a = self._release_revision(item, code='A')
        rev_b = self._make_revision(item, code='B')
        eco = self.env['pg.eco'].create({
            'title': 'Row-Lock Test ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })
        line = self.env['pg.eco.line'].create({
            'eco_id': eco.id,
            'item_id': item.id,
            'from_revision_id': rev_a.id,
            'to_revision_id': rev_b.id,
            'change_kind': 'revision_bump',
        })
        return eco, line, rev_b

    def _to_implementation(self, eco, rev_b):
        """Walk eco to 'implementation' and release rev_b, so
        action_release() will pass its own validation and proceed straight
        to (and past) the row lock."""
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve()
        rev_b.action_submit_for_review()
        rev_b.action_release()

    @staticmethod
    def _spy_cr(cr):
        """Return (spy_context, seen) — a mock.patch.object context manager
        that records every query cr.execute() is asked to run while still
        actually running it."""
        seen = []
        original_execute = cr.execute

        def spy(query, *args, **kwargs):
            seen.append((str(query or ''), args, kwargs))
            return original_execute(query, *args, **kwargs)

        return mock.patch.object(cr, 'execute', side_effect=spy), seen

    @staticmethod
    def _lock_calls(seen):
        return [
            (q, args, kwargs) for q, args, kwargs in seen
            if 'pg_eco' in q.lower() and 'for update' in q.lower()
        ]

    # ──────────────────────────────────────────────────────────────────
    # 1. pg.eco.line.write() locks the parent row
    # ──────────────────────────────────────────────────────────────────
    def test_line_write_locks_parent_eco_row(self):
        eco, line, _rev_b = self._make_eco()

        patcher, seen = self._spy_cr(self.env.cr)
        with patcher:
            line.write({'notes': 'progress note'})

        lock_calls = self._lock_calls(seen)
        self.assertTrue(
            lock_calls, 'expected a FOR UPDATE lock on pg_eco to be issued',
        )
        _q, args, kwargs = lock_calls[0]
        params = args[0] if args else kwargs.get('params')
        self.assertEqual(tuple(params[0]), (eco.id,))
        self.assertEqual(line.notes, 'progress note')

    # ──────────────────────────────────────────────────────────────────
    # 2. pg.eco.action_release() locks its own row
    # ──────────────────────────────────────────────────────────────────
    def test_action_release_locks_eco_row(self):
        eco, line, rev_b = self._make_eco()
        self._to_implementation(eco, rev_b)

        patcher, seen = self._spy_cr(self.env.cr)
        with patcher:
            eco.action_release()

        lock_calls = self._lock_calls(seen)
        self.assertTrue(
            lock_calls, 'expected a FOR UPDATE lock on pg_eco to be issued',
        )
        _q, args, kwargs = lock_calls[0]
        params = args[0] if args else kwargs.get('params')
        self.assertEqual(tuple(params[0]), (eco.id,))
        self.assertEqual(eco.state, 'released')

    # ──────────────────────────────────────────────────────────────────
    # 3. Genuine two-cursor concurrency test
    # ──────────────────────────────────────────────────────────────────
    def test_concurrent_line_write_blocks_on_release_lock(self):
        """action_release() takes the row lock and (within this test's
        transaction) never commits it. A second, independent DB connection
        attempting a line write on the same ECO must be blocked by
        Postgres — proven via a short lock_timeout so the test can't hang
        even if the locking behaviour regresses."""
        eco, line, rev_b = self._make_eco()
        self._to_implementation(eco, rev_b)

        # Cursor 1 (this test's own transaction) takes + holds the lock.
        # TransactionCase never commits mid-test, so it stays held for the
        # remainder of this method.
        eco.action_release()
        self.assertEqual(eco.state, 'released')

        cr2 = self.cursor()
        try:
            cr2.execute("SET LOCAL lock_timeout = '200ms'")
            env2 = api.Environment(cr2, self.test_user.id, {})
            with self.assertRaises(psycopg2.OperationalError):
                env2['pg.eco.line'].browse(line.id).write(
                    {'notes': 'racing edit from a second connection'}
                )
        finally:
            cr2.rollback()
            cr2.close()

    # ──────────────────────────────────────────────────────────────────
    # 4. Single-threaded regression: re-lock within the same transaction
    #    (action_release()'s own related-field recompute on eco_state)
    #    must not raise or hang.
    # ──────────────────────────────────────────────────────────────────
    def test_action_release_recompute_reacquires_lock_without_error(self):
        eco, line, rev_b = self._make_eco()
        self._to_implementation(eco, rev_b)
        # No exception, no hang: action_release()'s write() triggers a
        # related-field (eco_state) recompute that writes pg.eco.line rows,
        # which in turn re-locks the already-locked parent pg_eco row from
        # the same transaction.
        eco.action_release()
        self.assertEqual(eco.state, 'released')
        self.assertEqual(line.eco_state, 'released')
        # And the existing released-line immutability guard still fires
        # normally afterwards.
        with self.assertRaises(UserError):
            line.write({'change_kind': 'obsolete'})
        # ...while status/notes remain writable per the existing allowlist.
        line.write({'notes': 'post-release note'})
        self.assertEqual(line.notes, 'post-release note')

    # ──────────────────────────────────────────────────────────────────
    # 5. Empty-recordset guard
    # ──────────────────────────────────────────────────────────────────
    def test_lock_for_update_noop_on_empty_recordset(self):
        # Must not raise (e.g. on `IN ()` SQL) when there is nothing to lock.
        self.env['pg.eco'].browse([])._lock_for_update()
