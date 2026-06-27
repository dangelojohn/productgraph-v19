# -*- coding: utf-8 -*-
"""Mixin contract tests — exercises pg.item as the canonical subclass.

The mixin itself is an AbstractModel and can't be exercised directly; we
verify its public surface (_check_transition_allowed, _audit_state_change,
write() immutability guard, _do_transition orchestrator) on a concrete
subclass that has the cleanest config: pg.item.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph',
        'product_graph_lifecycle_mixin')
class TestLifecycleMixin(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Promote the test admin into the lifecycle groups so action_*
        # methods that gate on group membership succeed.
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )

    def setUp(self):
        super().setUp()
        category = self.env['pg.category'].search([], limit=1)
        if not category:
            category = self.env['pg.category'].create({'name': 'Test Cat'})
        self.item = self.env['pg.item'].create({
            'part_number': 'PG-PRT-MIXIN-TEST-001',
            'name': 'Mixin Test Item',
            'item_type': 'part',
            'category_id': category.id,
        })

    # ─── _check_transition_allowed ───────────────────────────────────────────

    def test_check_transition_allowed_passes_for_valid(self):
        # concept → prototype is allowed by ITEM_TRANSITIONS.
        self.item._check_transition_allowed('prototype')

    def test_check_transition_allowed_blocks_invalid(self):
        # concept → released is NOT allowed.
        with self.assertRaises(UserError) as ctx:
            self.item._check_transition_allowed('released')
        self.assertIn('Cannot transition', str(ctx.exception))

    def test_check_transition_allowed_blocks_terminal_state(self):
        # Force into obsolete (terminal — empty transitions list).
        self.item.with_context(_pg_release_bypass=True).write({
            'state': 'obsolete',
        })
        with self.assertRaises(UserError):
            self.item._check_transition_allowed('prototype')

    # ─── _audit_state_change ─────────────────────────────────────────────────

    def test_audit_helper_writes_to_pg_audit_log(self):
        Log = self.env['pg.audit.log']
        before = Log.search_count([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', self.item.id),
        ])
        self.item._audit_state_change('concept', 'prototype', notes='test')
        after = Log.search_count([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', self.item.id),
        ])
        self.assertEqual(after - before, 1)

    def test_log_state_change_alias_routes_to_audit(self):
        # Legacy alias still emits the audit row — keeps action_* methods
        # that call self._log_state_change working without refactoring.
        Log = self.env['pg.audit.log']
        before = Log.search_count([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', self.item.id),
        ])
        self.item._log_state_change('concept', 'prototype')
        after = Log.search_count([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', self.item.id),
        ])
        self.assertEqual(after - before, 1)

    # ─── write() immutability — exercised on pg.revision (clean allowlist) ──

    def test_write_blocked_on_terminal_state_for_revision(self):
        Rev = self.env['pg.revision']
        # Use a separate sample (pg.item's write is asymmetric; pg.revision
        # uses the mixin's allowlist path cleanly).
        self.item.write({'state': 'prototype'})
        self.item.write({'state': 'engineering'})
        rev = Rev.create({
            'item_id': self.item.id,
            'name': 'A',
            'change_summary': 'Initial revision (10+ chars).',
        })
        # Drive to released via bypass (simulates pg.release flow).
        rev.with_context(_pg_release_bypass=True).write({
            'state': 'released',
        })
        # Without bypass: non-allowlist field write must raise.
        with self.assertRaises(UserError) as ctx:
            rev.write({'change_summary': 'rewrite'})
        self.assertIn('locked state', str(ctx.exception).lower())

    def test_write_allowed_chatter_field_on_terminal_for_revision(self):
        Rev = self.env['pg.revision']
        self.item.write({'state': 'prototype'})
        self.item.write({'state': 'engineering'})
        rev = Rev.create({
            'item_id': self.item.id,
            'name': 'A',
            'change_summary': 'Initial revision (10+ chars).',
        })
        rev.with_context(_pg_release_bypass=True).write({
            'state': 'released',
        })
        # Chatter still works on locked records.
        rev.message_post(body='Still works')

    def test_bypass_unlocks_writes_for_release_flow(self):
        Rev = self.env['pg.revision']
        self.item.write({'state': 'prototype'})
        self.item.write({'state': 'engineering'})
        rev = Rev.create({
            'item_id': self.item.id,
            'name': 'A',
            'change_summary': 'Initial revision (10+ chars).',
        })
        rev.with_context(_pg_release_bypass=True).write({
            'state': 'released',
            'effective_date': '2026-01-01',  # bypass-only field
        })
        # With bypass, even non-allowlist fields are writable.
        rev.with_context(_pg_release_bypass=True).write({
            'description': '<p>updated by release flow</p>',
        })

    def test_bypass_only_field_blocked_without_bypass(self):
        # pg.revision's effective_date / released_by_id / release_timestamp
        # are bypass-only — blocked on ANY state without the bypass.
        Rev = self.env['pg.revision']
        rev = Rev.create({
            'item_id': self.item.id,
            'name': 'A',
            'change_summary': 'Initial revision (10+ chars).',
        })
        with self.assertRaises(UserError):
            rev.write({'effective_date': '2026-01-01'})

    # ─── Existing action methods still drive the state machine ──────────────

    def test_existing_action_methods_still_work(self):
        self.item.action_to_prototype()
        self.assertEqual(self.item.state, 'prototype')
        self.item.action_to_engineering()
        self.assertEqual(self.item.state, 'engineering')
