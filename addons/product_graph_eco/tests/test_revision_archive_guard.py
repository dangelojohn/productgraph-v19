# -*- coding: utf-8 -*-
"""B6.5 — pg.revision.write({'active': False}) must be blocked while the
revision is still referenced (from_revision_id / to_revision_id) by a
pg.eco.line whose parent ECO is in-flight (not released / rejected).

Lives in product_graph_eco (not product_graph_revision) because it needs
both models installed in the same registry — product_graph_eco depends on
product_graph_revision, never the reverse.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestRevisionArchiveGuard(TransactionCase):

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
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.enforce_sod', 'False',
        )

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def _make_item(self, name='Archive Guard Item'):
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    def _release_revision(self, item, code='A'):
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'Initial release of the test item.',
        })
        rev.action_submit_for_review()
        rev.action_release()
        return rev

    def _make_eco(self):
        return self.env['pg.eco'].create({
            'title': 'Archive Guard ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })

    # ──────────────────────────────────────────────────────────────────
    # Tests
    # ──────────────────────────────────────────────────────────────────
    def test_archive_blocked_when_referenced_by_inflight_eco_to_revision(self):
        item = self._make_item('To-Revision Item')
        rev_a = self._release_revision(item, code='A')
        target = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'B',
            'change_summary': 'Draft target of an in-flight ECO.',
        })
        eco = self._make_eco()
        self.env['pg.eco.line'].create({
            'eco_id': eco.id,
            'item_id': item.id,
            'from_revision_id': rev_a.id,
            'to_revision_id': target.id,
            'change_kind': 'revision_bump',
        })
        eco.action_start_analysis()
        self.assertEqual(eco.state, 'analysis')
        with self.assertRaises(UserError):
            target.write({'active': False})
        self.assertTrue(target.active)

    def test_archive_blocked_when_referenced_by_inflight_eco_from_revision(self):
        item = self._make_item('From-Revision Item')
        rev_a = self._release_revision(item, code='A')
        eco = self._make_eco()
        self.env['pg.eco.line'].create({
            'eco_id': eco.id,
            'item_id': item.id,
            'from_revision_id': rev_a.id,
            'change_kind': 'obsolete',
        })
        eco.action_start_analysis()
        self.assertEqual(eco.state, 'analysis')
        with self.assertRaises(UserError):
            rev_a.write({'active': False})
        self.assertTrue(rev_a.active)

    def test_archive_allowed_when_eco_released_or_rejected(self):
        item = self._make_item('Terminal-State Item')
        rev_a = self._release_revision(item, code='A')
        target = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'B',
            'change_summary': 'Target revision, ECO will be rejected.',
        })
        eco = self._make_eco()
        self.env['pg.eco.line'].create({
            'eco_id': eco.id,
            'item_id': item.id,
            'from_revision_id': rev_a.id,
            'to_revision_id': target.id,
            'change_kind': 'revision_bump',
        })
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_reject(reason='Not needed after all.')
        self.assertEqual(eco.state, 'rejected')
        # Terminal ECO state — archiving the (still-draft) target revision
        # must now be allowed.
        target.write({'active': False})
        self.assertFalse(target.active)

    def test_unarchive_never_blocked(self):
        # Archive the revision FIRST (no ECO references it yet, so the
        # archive itself is unguarded), then reference it from an in-flight
        # ECO line, then confirm re-activating it is never blocked even
        # though the ECO is still in-flight — the guard only fires on
        # active=False.
        item = self._make_item('Unarchive Item')
        rev_a = self._release_revision(item, code='A')
        target = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'B',
            'change_summary': 'Target revision for unarchive check.',
        })
        target.write({'active': False})
        self.assertFalse(target.active)
        eco = self._make_eco()
        self.env['pg.eco.line'].with_context(active_test=False).create({
            'eco_id': eco.id,
            'item_id': item.id,
            'from_revision_id': rev_a.id,
            'to_revision_id': target.id,
            'change_kind': 'revision_bump',
        })
        eco.action_start_analysis()
        self.assertEqual(eco.state, 'analysis')
        target.write({'active': True})
        self.assertTrue(target.active)
