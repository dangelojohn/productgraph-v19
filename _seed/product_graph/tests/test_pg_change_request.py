# -*- coding: utf-8 -*-
"""Tests for pg.change.request — ECR workflow."""
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestPgChangeRequest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id = [
            (4, cls.env.ref('product_graph.group_pg_admin').id),
        ]
        cls.category = cls.env['pg.category'].create({
            'name': 'ECR Test Cat', 'code': 'ECR-TC',
        })
        cls.item = cls.env['pg.item'].create({
            'name': 'ECR Test Item',
            'item_type': 'manufactured',
            'category_id': cls.category.id,
        })

    def _make_ecr(self, state='draft'):
        ecr = self.env['pg.change.request'].create({
            'title': 'Test Change',
            'item_id': self.item.id,
            'change_type': 'design',
            'description': '<p>Test change description</p>',
        })
        if state != 'draft':
            self.env.cr.execute(
                "UPDATE pg_change_request SET state = %s WHERE id = %s",
                (state, ecr.id)
            )
            self.env.invalidate_all()
        return ecr

    def test_initial_state_draft(self):
        ecr = self._make_ecr()
        self.assertEqual(ecr.state, 'draft')

    def test_submit_transitions_state(self):
        ecr = self._make_ecr()
        ecr.action_submit()
        self.assertEqual(ecr.state, 'submitted')

    def test_invalid_transition_raises(self):
        ecr = self._make_ecr()
        with self.assertRaises(UserError):
            ecr.action_mark_implemented()

    def test_approve_sets_approver(self):
        ecr = self._make_ecr(state='impact_review')
        ecr.action_approve()
        self.assertEqual(ecr.state, 'approved')
        self.assertEqual(ecr.approved_by_id, self.env.user)

    def test_start_implementing_creates_revision(self):
        ecr = self._make_ecr(state='approved')
        ecr.action_start_implementing()
        self.assertEqual(ecr.state, 'implementing')
        self.assertTrue(ecr.new_revision_id, "Should auto-create a new revision")

    def test_close_after_implemented(self):
        ecr = self._make_ecr(state='implemented')
        ecr.action_close()
        self.assertEqual(ecr.state, 'closed')

    def test_sequence_is_unique(self):
        ecr1 = self._make_ecr()
        ecr2 = self._make_ecr()
        self.assertNotEqual(ecr1.name, ecr2.name)
