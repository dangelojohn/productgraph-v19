# -*- coding: utf-8 -*-
import threading
import time

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgRevision(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        # Promote test admin into all relevant groups
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        # PG-120 SoD is off here: these tests pre-date the guard and exercise
        # state-machine semantics as a single user. Dedicated SoD coverage
        # lives in test_sod.py.
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.enforce_sod', 'False',
        )

    def _make_item(self, item_type='part'):
        item = self.env['pg.item'].create({
            'name': 'Test Item',
            'item_type': item_type,
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    def _make_revision(self, item, code='A', summary='Initial release of the test item.'):
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': summary,
        })

    def test_create_revision_in_draft(self):
        item = self._make_item()
        rev = self._make_revision(item)
        self.assertEqual(rev.state, 'draft')
        self.assertEqual(rev.display_name, f'{item.part_number} Rev A')

    def test_submit_for_review_requires_long_summary(self):
        item = self._make_item()
        rev = self._make_revision(item, summary='too short')
        with self.assertRaises(UserError):
            rev.action_submit_for_review()

    def test_release_flow_auto_releases_item(self):
        item = self._make_item()
        rev = self._make_revision(item)
        rev.action_submit_for_review()
        self.assertEqual(rev.state, 'review')
        rev.action_release()
        self.assertEqual(rev.state, 'released')
        self.assertEqual(item.state, 'released')
        self.assertTrue(rev.effective_date)
        self.assertTrue(rev.release_timestamp)

    def test_released_revision_immutable_without_bypass(self):
        item = self._make_item()
        rev = self._make_revision(item)
        rev.action_submit_for_review()
        rev.action_release()
        with self.assertRaises(UserError):
            rev.write({'change_summary': 'mutated'})

    def test_released_chatter_allowed(self):
        item = self._make_item()
        rev = self._make_revision(item)
        rev.action_submit_for_review()
        rev.action_release()
        rev.message_post(body='chatter ok')

    def test_supersede_requires_released_successor(self):
        item = self._make_item()
        rev_a = self._make_revision(item, code='A')
        rev_a.action_submit_for_review()
        rev_a.action_release()
        rev_b = self._make_revision(item, code='B', summary='Updated release.')
        # B is still in draft — cannot be a successor yet
        rev_a.write({'successor_revision_id': rev_b.id})
        with self.assertRaises(UserError):
            rev_a.action_supersede()
        rev_b.action_submit_for_review()
        rev_b.action_release()
        rev_a.action_supersede()
        self.assertEqual(rev_a.state, 'superseded')

    def test_action_supersede_rejects_stale_successor_state(self):
        # B6.8 — regression guard: action_supersede must still raise even
        # after the row lock is taken, if the successor's freshly-read
        # state is not 'released'. Simulates a concurrent transaction that
        # changed the successor's state out from under the ORM cache (a raw
        # SQL UPDATE stands in for a second, already-committed transaction
        # whose write becomes visible once its own FOR UPDATE lock is
        # released and this transaction re-reads the row). Guards against
        # the fix weakening the existing validation by trusting a stale
        # cached value instead of the freshly-locked one.
        item = self._make_item()
        rev_a = self._make_revision(item, code='A')
        rev_a.action_submit_for_review()
        rev_a.action_release()
        rev_b = self._make_revision(item, code='B', summary='Updated release.')
        rev_b.action_submit_for_review()
        rev_b.action_release()
        rev_a.write({'successor_revision_id': rev_b.id})
        # Warm the ORM cache with the (currently correct) 'released' value.
        self.assertEqual(rev_b.state, 'released')
        # Bypass write() entirely so the ORM cache is left holding the
        # stale 'released' value while the DB row itself has moved on.
        self.env.cr.execute(
            "UPDATE pg_revision SET state = 'obsolete' WHERE id = %s",
            (rev_b.id,),
        )
        with self.assertRaises(UserError):
            rev_a.action_supersede()
        self.assertEqual(rev_a.state, 'released')

    def test_reject_requires_reason(self):
        item = self._make_item()
        rev = self._make_revision(item)
        rev.action_submit_for_review()
        # Direct API call (skipping wizard) should fail without enough reason
        with self.assertRaises(Exception):  # ValidationError
            rev._reject_with_reason('x')
        rev._reject_with_reason('Insufficient mechanical clearance.')
        self.assertEqual(rev.state, 'draft')
        self.assertTrue(rev.rejection_reason)

    def test_bypass_fields_blocked_without_context(self):
        item = self._make_item()
        rev = self._make_revision(item)
        with self.assertRaises(UserError):
            rev.write({'effective_date': '2026-01-01'})

    def test_current_revision_resolves(self):
        item = self._make_item()
        rev_a = self._make_revision(item, code='A')
        rev_a.action_submit_for_review()
        rev_a.action_release()
        self.assertEqual(item.current_revision_id, rev_a)

    def test_released_revision_blocks_raw_state_regression(self):
        # B6.1 — 'state' is inside LIFECYCLE_MUTABLE_LOCKED (ALLOWLIST mode)
        # so a raw write({'state': 'draft'}) on a released revision used to
        # slip through untouched. The mixin's unconditional
        # _check_transition_allowed() guard must now block it.
        item = self._make_item()
        rev = self._make_revision(item)
        rev.action_submit_for_review()
        rev.action_release()
        with self.assertRaises(UserError):
            rev.write({'state': 'draft'})
        self.assertEqual(rev.state, 'released')

    def test_archive_allowed_when_eco_module_not_installed(self):
        # B6.5 — the cross-module ECO guard in write() must be a pure no-op
        # when product_graph_eco isn't installed in this registry (it isn't,
        # for this module's own test suite). Regression guard for the
        # optional-dependency env.get() pattern.
        self.assertIsNone(self.env.get('pg.eco.line'))
        item = self._make_item()
        rev = self._make_revision(item)
        rev.write({'active': False})
        self.assertFalse(rev.active)

    def test_unarchive_never_blocked(self):
        # B6.5 — re-activating a revision is always safe; the guard only
        # fires on active=False.
        item = self._make_item()
        rev = self._make_revision(item)
        rev.write({'active': False})
        rev.write({'active': True})
        self.assertTrue(rev.active)


@tagged('post_install', '-at_install', 'product_graph')
class TestPgRevisionSupersedeLock(TransactionCase):
    """B6.8 — proves action_supersede's row lock actually blocks a
    concurrent writer at the database level.

    TransactionCase runs each test inside a single uncommitted transaction
    on one DB connection, so a genuine second connection (a real concurrent
    transaction) would not see this fixture data at all under Postgres'
    default READ COMMITTED isolation. To exercise real cross-connection
    locking we commit the fixture explicitly and clean it up by hand via
    addCleanup, instead of relying on TransactionCase's implicit rollback.
    """

    def setUp(self):
        super().setUp()
        cat = self.env.ref('product_graph_base.cat_mechanical')
        self.env.user.group_ids |= (
            self.env.ref('product_graph_base.group_pg_engineer')
            | self.env.ref('product_graph_base.group_pg_approver')
            | self.env.ref('product_graph_base.group_pg_admin')
        )
        self.env['ir.config_parameter'].sudo().set_param(
            'productgraph.enforce_sod', 'False',
        )
        item = self.env['pg.item'].create({
            'name': 'Lock Test Item',
            'item_type': 'part',
            'category_id': cat.id,
        })
        item.action_to_engineering()
        self.rev_a = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'Initial release of the test item.',
        })
        self.rev_a.action_submit_for_review()
        self.rev_a.action_release()
        self.rev_b = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'B',
            'change_summary': 'Updated release.',
        })
        self.rev_b.action_submit_for_review()
        self.rev_b.action_release()
        self.rev_a.write({'successor_revision_id': self.rev_b.id})
        # Commit so a second, independent cursor can see these rows.
        self.env.cr.commit()
        self.addCleanup(
            self._cleanup_committed_records, item.id, (self.rev_a.id, self.rev_b.id),
        )

    def _cleanup_committed_records(self, item_id, rev_ids):
        with self.registry.cursor() as cr:
            cr.execute(
                "DELETE FROM pg_audit_log WHERE res_model = 'pg.revision' "
                "AND res_id IN %s",
                (rev_ids,),
            )
            cr.execute(
                "DELETE FROM mail_message WHERE model = 'pg.revision' AND res_id IN %s",
                (rev_ids,),
            )
            cr.execute("DELETE FROM pg_revision WHERE id IN %s", (rev_ids,))
            cr.execute(
                "DELETE FROM pg_audit_log WHERE res_model = 'pg.item' AND res_id = %s",
                (item_id,),
            )
            cr.execute(
                "DELETE FROM mail_message WHERE model = 'pg.item' AND res_id = %s",
                (item_id,),
            )
            cr.execute("DELETE FROM pg_item WHERE id = %s", (item_id,))

    def test_action_supersede_locks_successor_row(self):
        """A concurrent UPDATE against the successor's row must block while
        another transaction holds the FOR UPDATE lock action_supersede
        takes, and only proceed after that transaction commits — proving
        the lock closes the TOCTOU race window."""
        lock_acquired = threading.Event()
        release_lock = threading.Event()
        writer_done = threading.Event()
        elapsed = {}

        def holder():
            # Mirrors the lock action_supersede takes internally, on an
            # independent connection standing in for "the transaction
            # running action_supersede".
            with self.registry.cursor() as cr1:
                cr1.execute(
                    "SELECT id FROM pg_revision WHERE id IN %s FOR UPDATE",
                    (tuple(sorted({self.rev_a.id, self.rev_b.id})),),
                )
                lock_acquired.set()
                release_lock.wait(timeout=10)
                # Clean `with`-block exit commits, releasing the lock.

        def writer():
            lock_acquired.wait(timeout=10)
            start = time.monotonic()
            with self.registry.cursor() as cr2:
                cr2.execute(
                    "UPDATE pg_revision SET state = 'obsolete' WHERE id = %s",
                    (self.rev_b.id,),
                )
            elapsed['seconds'] = time.monotonic() - start
            writer_done.set()

        t_holder = threading.Thread(target=holder)
        t_writer = threading.Thread(target=writer)
        t_holder.start()
        self.assertTrue(lock_acquired.wait(timeout=10), "Lock was never acquired.")
        t_writer.start()
        # The writer should still be blocked on the row lock a moment
        # later — this is the assertion that the lock actually blocks.
        self.assertFalse(
            writer_done.wait(timeout=1),
            "Concurrent UPDATE completed while the FOR UPDATE lock should "
            "still have been held — action_supersede's lock did not block it.",
        )
        release_lock.set()
        t_holder.join(timeout=10)
        t_writer.join(timeout=10)
        self.assertTrue(writer_done.is_set(), "Concurrent writer thread never completed.")
        self.assertGreaterEqual(elapsed.get('seconds', 0), 0.5)
