# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgVendorStateMachine(TransactionCase):

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

    def _make_vendor(self, code='V001', name='Test Vendor'):
        return self.env['pg.vendor'].create({
            'code': code,
            'name': name,
        })

    # ──────────────────────────────────────────────────────────────────
    # Happy path: unqualified → probationary → qualified → suspended → qualified → disqualified
    # ──────────────────────────────────────────────────────────────────
    def test_full_lifecycle(self):
        v = self._make_vendor()
        self.assertEqual(v.state, 'unqualified')
        self.assertFalse(v.is_approved)
        self.assertFalse(v.qualified_on)

        v.action_to_probationary()
        self.assertEqual(v.state, 'probationary')

        v.action_qualify()
        self.assertEqual(v.state, 'qualified')
        self.assertTrue(v.is_approved)
        self.assertTrue(v.qualified_on)
        self.assertEqual(v.qualified_by_id, self.test_user)

        v.with_context(default_qualification_comment='Audit failed').action_suspend()
        self.assertEqual(v.state, 'suspended')
        self.assertFalse(v.is_approved)
        # qualified_on stays (audit trail of first qualification)
        self.assertTrue(v.qualified_on)

        v.action_qualify()
        self.assertEqual(v.state, 'qualified')

        v.with_context(default_qualification_comment='Closed shop').action_disqualify()
        self.assertEqual(v.state, 'disqualified')

        # 5 entries: probationary, qualified, suspended, qualified-reinstate, disqualified
        self.assertEqual(len(v.qualification_ids), 5)

    # ──────────────────────────────────────────────────────────────────
    # Forbidden transitions
    # ──────────────────────────────────────────────────────────────────
    def test_cannot_skip_probationary(self):
        v = self._make_vendor()
        with self.assertRaises(UserError):
            v.action_qualify()

    def test_disqualified_is_terminal(self):
        v = self._make_vendor()
        v.action_to_probationary()
        v.with_context(default_qualification_comment='Bad').action_disqualify()
        with self.assertRaises(UserError):
            v.action_to_probationary()
        with self.assertRaises(UserError):
            v.action_qualify()

    # ──────────────────────────────────────────────────────────────────
    # Required-comment guards
    # ──────────────────────────────────────────────────────────────────
    def test_suspend_requires_comment(self):
        v = self._make_vendor()
        v.action_to_probationary()
        v.action_qualify()
        with self.assertRaises(ValidationError):
            v.action_suspend()

    def test_disqualify_requires_comment(self):
        v = self._make_vendor()
        v.action_to_probationary()
        with self.assertRaises(ValidationError):
            v.action_disqualify()

    # ──────────────────────────────────────────────────────────────────
    # is_approved sync
    # ──────────────────────────────────────────────────────────────────
    def test_is_approved_tracks_state(self):
        v = self._make_vendor()
        v.action_to_probationary()
        self.assertFalse(v.is_approved)
        v.action_qualify()
        self.assertTrue(v.is_approved)
        v.with_context(default_qualification_comment='x').action_suspend()
        self.assertFalse(v.is_approved)
