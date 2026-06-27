# -*- coding: utf-8 -*-
"""PG-121 — Gate and Audit Force Release.

Exercises the wizard gate (approver role, justification min length, confirm
checkbox), the distinct `force_release` audit-log entry with reason payload,
the chatter warning post, and the bypass-of-normal-gates semantic.
"""
import json

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


_VALID_REASON = (
    'Customer escalation #4567 — physical sample shipping ahead of '
    'engineering close-out per Eng Lead approval.'
)


@tagged('post_install', '-at_install', 'product_graph')
class TestForceRelease(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Item = cls.env['pg.item']
        cls.AuditLog = cls.env['pg.audit.log']
        cls.Wizard = cls.env['pg.force.release.wizard']
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        # Approver actor (used by most tests)
        cls.approver = cls.env['res.users'].create({
            'name': 'PG Approver',
            'login': 'pg-approver-pg121',
            'email': 'approver-pg121@example.com',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('product_graph_base.group_pg_engineer').id,
                cls.env.ref('product_graph_base.group_pg_approver').id,
            ])],
        })
        # Engineer-only actor (no approver role)
        cls.engineer = cls.env['res.users'].create({
            'name': 'PG Engineer',
            'login': 'pg-engineer-pg121',
            'email': 'engineer-pg121@example.com',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('product_graph_base.group_pg_engineer').id,
            ])],
        })

    def _make_item(self, state='engineering'):
        item = self.Item.with_user(self.approver).create({
            'name': 'PG-121 Test Item',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        # Walk forward to requested state (engineer rights cover both hops).
        if state in ('prototype', 'engineering', 'released'):
            item.action_to_prototype()
        if state in ('engineering', 'released'):
            item.action_to_engineering()
        return item

    # ──────────────────────────── permission gate ────────────────────────────

    def test_force_release_blocked_for_non_approver(self):
        """An engineer (no approver role) is rejected at the button gate."""
        item = self.Item.with_user(self.engineer).create({
            'name': 'PG-121 Engineer Item',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        with self.assertRaises(AccessError):
            item.with_user(self.engineer).action_force_release()

    def test_do_force_release_blocked_for_non_approver(self):
        """Direct API caller without approver role is also rejected."""
        item = self.Item.with_user(self.approver).create({
            'name': 'PG-121 API Item',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        with self.assertRaises(AccessError):
            item.with_user(self.engineer).with_context(
                _pg_force_release_reason=_VALID_REASON,
            )._do_force_release()

    # ─────────────────────────── justification gate ───────────────────────────

    def test_force_release_wizard_requires_reason_min_length(self):
        item = self._make_item()
        with self.assertRaises(ValidationError):
            self.Wizard.with_user(self.approver).create({
                'item_id': item.id,
                'reason': 'TBD',  # 3 chars
                'confirm': True,
            })

    def test_force_release_wizard_requires_confirm_check(self):
        item = self._make_item()
        wiz = self.Wizard.with_user(self.approver).create({
            'item_id': item.id,
            'reason': _VALID_REASON,
            'confirm': False,
        })
        with self.assertRaises(UserError):
            wiz.action_confirm()

    def test_do_force_release_rejects_empty_reason_context(self):
        """Defence-in-depth: direct API call without reason is blocked."""
        item = self._make_item()
        with self.assertRaises(UserError):
            item.with_user(self.approver)._do_force_release()

    # ─────────────────────────── audit + chatter ──────────────────────────────

    def test_force_release_creates_distinct_audit_entry(self):
        item = self._make_item()
        before_logs = self.AuditLog.search([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', item.id),
            ('action', '=', 'force_release'),
        ])
        self.assertFalse(before_logs)

        wiz = self.Wizard.with_user(self.approver).create({
            'item_id': item.id,
            'reason': _VALID_REASON,
            'confirm': True,
        })
        wiz.action_confirm()

        log = self.AuditLog.search([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', item.id),
            ('action', '=', 'force_release'),
        ], limit=1)
        self.assertTrue(log, "Expected a distinct force_release audit-log entry.")
        self.assertEqual(log.to_state, 'released')
        self.assertEqual(log.from_state, 'engineering')
        payload = json.loads(log.payload)
        self.assertEqual(payload.get('reason'), _VALID_REASON)
        self.assertEqual(payload.get('forced_by'), self.approver.name)
        self.assertEqual(payload.get('previous_state'), 'engineering')

    def test_force_release_posts_chatter_warning(self):
        item = self._make_item()
        wiz = self.Wizard.with_user(self.approver).create({
            'item_id': item.id,
            'reason': _VALID_REASON,
            'confirm': True,
        })
        wiz.action_confirm()
        # The chatter warning lives on the item record.
        messages = item.message_ids.filtered(lambda m: 'FORCE RELEASE' in (m.body or ''))
        self.assertTrue(
            messages,
            "Expected a chatter post on the item containing 'FORCE RELEASE'.",
        )
        self.assertIn(self.approver.name, messages[0].body)

    # ─────────────────── bypass-of-normal-gates semantic ──────────────────────

    def test_force_release_bypasses_normal_gates(self):
        """An item in 'concept' would normally have to walk through prototype +
        engineering before release. Force release skips that path."""
        item = self.Item.with_user(self.approver).create({
            'name': 'PG-121 Concept Item',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        self.assertEqual(item.state, 'concept')

        wiz = self.Wizard.with_user(self.approver).create({
            'item_id': item.id,
            'reason': _VALID_REASON,
            'confirm': True,
        })
        wiz.action_confirm()

        self.assertEqual(item.state, 'released')
        self.assertTrue(item.released_date)

        # And the bypass was logged with the original state as 'previous_state'.
        log = self.AuditLog.search([
            ('res_model', '=', 'pg.item'),
            ('res_id', '=', item.id),
            ('action', '=', 'force_release'),
        ], limit=1)
        self.assertTrue(log)
        payload = json.loads(log.payload)
        self.assertEqual(payload.get('previous_state'), 'concept')
