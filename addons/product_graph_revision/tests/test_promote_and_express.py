# -*- coding: utf-8 -*-
"""PG-140 — One-click Promote + Express Release for pg.revision.

Covers:
- action_promote routes via the state→method PROMOTION_MAP.
- The new can_promote / promote_blocked_reason mirrors the underlying guards.
- Express Release succeeds when SoD is off (or the actor is an admin),
  and fully drives draft → review → released in one transaction.
- Express Release respects SoD when enforced (non-admin must use the
  two-step path).
- Express Release rejects non-draft starting states.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'product_graph_promote')
class TestRevisionPromoteAndExpress(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        engineer = cls.env.ref('product_graph_base.group_pg_engineer')
        approver = cls.env.ref('product_graph_base.group_pg_approver')
        admin = cls.env.ref('product_graph_base.group_pg_admin')
        # Admin tester (capability for everything; also satisfies the admin
        # bypass branch in Express Release).
        cls.env.user.group_ids |= (engineer | approver | admin)

        # A non-admin user with engineer+approver capability — used for the
        # SoD-blocks-non-admin Express Release test.
        cls.user_nonadmin = cls.env['res.users'].create({
            'name': 'PG-140 Non-Admin',
            'login': 'pg140_nonadmin@example.com',
            'email': 'pg140_nonadmin@example.com',
            'group_ids': [(4, engineer.id), (4, approver.id)],
        })

        cls.ICP = cls.env['ir.config_parameter'].sudo()
        # Make absolutely sure auto-advance is enabled for the express-flow tests.
        cls.ICP.set_param(
            'productgraph.auto_advance_item_on_revision_release', 'True',
        )

    def _make_engineering_item(self, name='Promote Rev Item'):
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        item.action_to_prototype()
        item.action_to_engineering()
        return item

    def _make_revision(self, item, code='A',
                       summary='Initial release of the promote-test item.'):
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': summary,
        })

    # ── action_promote (revision) ──────────────────────────────────────────

    def test_revision_promote_draft_to_review(self):
        item = self._make_engineering_item()
        rev = self._make_revision(item)
        self.assertEqual(rev.state, 'draft')
        rev.action_promote()
        self.assertEqual(rev.state, 'review')

    def test_revision_promote_blocked_by_short_summary(self):
        item = self._make_engineering_item()
        rev = self._make_revision(item, summary='TBD')
        self.assertFalse(rev.can_promote)
        self.assertIn('10 characters', rev.promote_blocked_reason or '')
        with self.assertRaises(UserError):
            rev.action_promote()

    def test_revision_promote_released_blocked(self):
        """Released revisions can't be promoted — supersession is explicit."""
        # Disable SoD so the same admin user can submit + release.
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        try:
            item = self._make_engineering_item()
            rev = self._make_revision(item)
            rev.action_promote()  # -> review
            rev.action_promote()  # -> released
            self.assertEqual(rev.state, 'released')
            self.assertFalse(rev.can_promote)
            with self.assertRaises(UserError) as cm:
                rev.action_promote()
            self.assertIn('Supersession', str(cm.exception))
        finally:
            self.ICP.set_param('productgraph.enforce_sod', 'True')

    # ── action_express_release ─────────────────────────────────────────────

    def test_express_release_full_flow(self):
        """With SoD off, draft revision on an engineering item goes to
        released and the parent item auto-advances (auto_advance ICP=True)."""
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        try:
            item = self._make_engineering_item(name='Express Item')
            rev = self._make_revision(item)
            self.assertTrue(rev.can_express_release)
            rev.action_express_release()
            self.assertEqual(rev.state, 'released')
            self.assertEqual(item.state, 'released')
        finally:
            self.ICP.set_param('productgraph.enforce_sod', 'True')

    def test_express_release_blocked_by_sod(self):
        """SoD on + non-admin user => UI flag and server both reject."""
        self.ICP.set_param('productgraph.enforce_sod', 'True')
        item = self._make_engineering_item(name='SoD Blocked Item')
        rev = self._make_revision(item).with_user(self.user_nonadmin)
        # Compute must reflect the blocked state for THIS user.
        rev.invalidate_recordset()
        self.assertFalse(rev.can_express_release)
        self.assertIn('SoD', rev.express_release_blocked_reason or '')
        with self.assertRaises(UserError) as cm:
            rev.action_express_release()
        self.assertIn('Segregation', str(cm.exception))

    def test_express_release_admin_bypasses_sod(self):
        """SoD on + admin user => server allows the express path."""
        self.ICP.set_param('productgraph.enforce_sod', 'True')
        item = self._make_engineering_item(name='Admin Express Item')
        rev = self._make_revision(item)
        # The class-level user is in group_pg_admin.
        self.assertTrue(rev.can_express_release)
        rev.action_express_release()
        self.assertEqual(rev.state, 'released')

    def test_express_release_only_from_draft(self):
        """Express Release on a non-draft revision must raise."""
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        try:
            item = self._make_engineering_item(name='Wrong State Item')
            rev = self._make_revision(item)
            rev.action_submit_for_review()
            self.assertEqual(rev.state, 'review')
            with self.assertRaises(UserError) as cm:
                rev.action_express_release()
            self.assertIn('Draft', str(cm.exception))
        finally:
            self.ICP.set_param('productgraph.enforce_sod', 'True')
