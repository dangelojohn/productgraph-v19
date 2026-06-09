# -*- coding: utf-8 -*-
"""
Tests for pg.item — state machine, immutability, revision code generation.
Run with: odoo-bin -c odoo.conf -d <db> --test-enable -i product_graph
"""
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestPgItem(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env['pg.category'].create({
            'name': 'Test Category',
            'code': 'TC-TEST',
        })

    def _make_item(self, name='Test Item', item_type='manufactured'):
        return self.env['pg.item'].create({
            'name': name,
            'item_type': item_type,
            'category_id': self.category.id,
        })

    # ── State machine ──────────────────────────────────────────────

    def test_initial_state_is_draft(self):
        item = self._make_item()
        self.assertEqual(item.state, 'draft')

    def test_transition_draft_to_engineering(self):
        item = self._make_item()
        # Elevate current user to engineer group for this test
        self.env.user.groups_id = [(4, self.env.ref(
            'product_graph.group_pg_engineer'
        ).id)]
        item.action_to_engineering()
        self.assertEqual(item.state, 'engineering')

    def test_invalid_transition_raises(self):
        item = self._make_item()
        with self.assertRaises(UserError):
            item.action_obsolete()  # cannot jump draft → obsolete

    # ── Immutability ───────────────────────────────────────────────

    def test_released_item_immutable(self):
        item = self._make_item()
        # Force state to released directly (bypass guards for test setup)
        self.env.cr.execute(
            "UPDATE pg_item SET state = 'released' WHERE id = %s", (item.id,)
        )
        self.env.invalidate_all()
        with self.assertRaises(UserError):
            item.write({'name': 'New Name After Release'})

    def test_released_item_tag_write_allowed(self):
        """Tags are in the mutable whitelist — should not raise."""
        item = self._make_item()
        tag = self.env['pg.item.tag'].create({'name': 'Test Tag'})
        self.env.cr.execute(
            "UPDATE pg_item SET state = 'released' WHERE id = %s", (item.id,)
        )
        self.env.invalidate_all()
        # Should NOT raise
        item.write({'tag_ids': [(4, tag.id)]})

    # ── Deletion guards ────────────────────────────────────────────

    def test_cannot_delete_engineering_item(self):
        item = self._make_item()
        self.env.cr.execute(
            "UPDATE pg_item SET state = 'engineering' WHERE id = %s", (item.id,)
        )
        self.env.invalidate_all()
        with self.assertRaises(UserError):
            item.unlink()

    def test_can_delete_draft_item(self):
        item = self._make_item()
        self.assertEqual(item.state, 'draft')
        item.unlink()  # Should not raise

    # ── Revision code generation ───────────────────────────────────

    def test_next_revision_code_empty(self):
        item = self._make_item()
        self.assertEqual(item._next_revision_code(), 'A')

    def test_next_revision_code_after_z(self):
        item = self._make_item()
        # Simulate Z existing
        self.env['pg.revision'].create({
            'item_id': item.id,
            'revision_code': 'Z',
            'sequence': 260,
        })
        self.assertEqual(item._next_revision_code(), 'AA')

    # ── Display name ───────────────────────────────────────────────

    def test_display_name_format(self):
        item = self._make_item('Widget')
        self.assertIn(item.item_number, item.display_name_full)
        self.assertIn('Widget', item.display_name_full)
