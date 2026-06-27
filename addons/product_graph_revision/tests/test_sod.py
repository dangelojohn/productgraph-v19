# -*- coding: utf-8 -*-
"""PG-120 — Segregation of Duties tests for pg.revision.

A user who submitted a revision for review must not be the same user who
releases it. Bypass via the ICP ``productgraph.enforce_sod=False``.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'product_graph_sod')
class TestPgRevisionSoD(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Promote test-runner user to engineer + approver so helper methods
        # like _make_item() can call action_to_engineering without group errors.
        # v19 doesn't auto-imply group_pg_engineer from group_pg_admin.
        eng = cls.env.ref('product_graph_base.group_pg_engineer', raise_if_not_found=False)
        appr = cls.env.ref('product_graph_base.group_pg_approver', raise_if_not_found=False)
        promotions = []
        if eng:
            promotions.append((4, eng.id))
        if appr:
            promotions.append((4, appr.id))
        if promotions:
            cls.env.user.write({'group_ids': promotions})
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        engineer = cls.env.ref('product_graph_base.group_pg_engineer')
        approver = cls.env.ref('product_graph_base.group_pg_approver')
        # Two distinct users, both Engineer + Approver so capability isn't
        # the discriminator — SoD is.
        cls.user_a = cls.env['res.users'].create({
            'name': 'SoD Author',
            'login': 'sod_author_rev@example.com',
            'email': 'sod_author_rev@example.com',
            'group_ids': [(4, engineer.id), (4, approver.id)],
        })
        cls.user_b = cls.env['res.users'].create({
            'name': 'SoD Approver',
            'login': 'sod_approver_rev@example.com',
            'email': 'sod_approver_rev@example.com',
            'group_ids': [(4, engineer.id), (4, approver.id)],
        })

    def _make_item(self):
        item = self.env['pg.item'].create({
            'name': 'SoD Test Item',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    def _make_revision(self, item, user, code='A'):
        return self.env['pg.revision'].with_user(user).create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'Initial release of the SoD test item.',
        })

    def test_submitted_by_stamped_on_submit(self):
        item = self._make_item()
        rev = self._make_revision(item, self.user_a)
        rev.with_user(self.user_a).action_submit_for_review()
        rev.invalidate_recordset()
        self.assertEqual(rev.state, 'review')
        self.assertEqual(rev.submitted_by_id, self.user_a)
        self.assertTrue(rev.submitted_at)

    def test_author_cannot_self_release_revision(self):
        item = self._make_item()
        rev = self._make_revision(item, self.user_a)
        rev.with_user(self.user_a).action_submit_for_review()
        with self.assertRaises(UserError) as cm:
            rev.with_user(self.user_a).action_release()
        self.assertIn('Segregation of Duties', str(cm.exception))

    def test_different_approver_can_release(self):
        item = self._make_item()
        rev = self._make_revision(item, self.user_a)
        rev.with_user(self.user_a).action_submit_for_review()
        rev.with_user(self.user_b).action_release()
        rev.invalidate_recordset()
        self.assertEqual(rev.state, 'released')

    def test_sod_disabled_allows_self_release(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'productgraph.enforce_sod', 'False',
        )
        try:
            item = self._make_item()
            rev = self._make_revision(item, self.user_a)
            rev.with_user(self.user_a).action_submit_for_review()
            rev.with_user(self.user_a).action_release()
            rev.invalidate_recordset()
            self.assertEqual(rev.state, 'released')
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'productgraph.enforce_sod', 'True',
            )
