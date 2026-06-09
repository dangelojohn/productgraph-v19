# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgRevision(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        # Promote test admin into all relevant groups
        cls.env.user.groups_id |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
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
