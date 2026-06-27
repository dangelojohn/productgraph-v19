# -*- coding: utf-8 -*-
"""PG-120 — Segregation of Duties tests for pg.eco.

The user who requested the ECO (or submitted it for approval) must not be
the same user who approves or releases it. Bypass via the ICP
``productgraph.enforce_sod=False``.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'product_graph_sod')
class TestPgEcoSoD(TransactionCase):

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
        # Two distinct, fully-capable users so SoD is the only discriminator.
        cls.user_a = cls.env['res.users'].create({
            'name': 'SoD ECO Author',
            'login': 'sod_author_eco@example.com',
            'email': 'sod_author_eco@example.com',
            'group_ids': [(4, engineer.id), (4, approver.id)],
        })
        cls.user_b = cls.env['res.users'].create({
            'name': 'SoD ECO Approver',
            'login': 'sod_approver_eco@example.com',
            'email': 'sod_approver_eco@example.com',
            'group_ids': [(4, engineer.id), (4, approver.id)],
        })

    def _make_item(self, suffix='eco'):
        item = self.env['pg.item'].create({
            'name': 'SoD ECO Test Item ' + suffix,
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    def _release_revision(self, item, code, user):
        rev = self.env['pg.revision'].with_user(user).create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'Initial release for the SoD ECO test item.',
        })
        # Use bypass to avoid the revision-level SoD (covered by its own tests).
        rev.with_user(user).with_context(
            __pg_eco_sod_bootstrap=True,
        ).action_submit_for_review()
        # SoD on revision.release requires a different user — use B for this
        # bootstrap so the test users don't get cross-contaminated.
        other = self.user_b if user == self.user_a else self.user_a
        rev.with_user(other).action_release()
        return rev

    def _make_draft_revision(self, item, code, user):
        return self.env['pg.revision'].with_user(user).create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'Target revision for the ECO.',
        })

    def _make_eco(self, requestor):
        """Build an ECO in `approval` state owned by `requestor`."""
        item = self._make_item(suffix=requestor.login)
        rev_a = self._release_revision(item, 'A', requestor)
        rev_b = self._make_draft_revision(item, 'B', requestor)
        eco = self.env['pg.eco'].with_user(requestor).create({
            'title': 'SoD Test ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.user_a.id, self.user_b.id])],
        })
        self.env['pg.eco.line'].with_user(requestor).create({
            'eco_id': eco.id,
            'item_id': item.id,
            'from_revision_id': rev_a.id,
            'to_revision_id': rev_b.id,
            'change_kind': 'revision_bump',
        })
        eco.with_user(requestor).action_start_analysis()
        eco.with_user(requestor).action_submit_for_approval()
        return eco, rev_b

    def test_submitted_by_stamped_on_submit_eco(self):
        eco, _rev_b = self._make_eco(requestor=self.user_a)
        eco.invalidate_recordset()
        self.assertEqual(eco.state, 'approval')
        self.assertEqual(eco.requested_by_id, self.user_a)
        self.assertEqual(eco.submitted_by_id, self.user_a)
        self.assertTrue(eco.submitted_at)

    def test_requestor_cannot_approve_own_eco(self):
        eco, _rev_b = self._make_eco(requestor=self.user_a)
        with self.assertRaises(UserError) as cm:
            eco.with_user(self.user_a).action_approve(comment='self approve')
        self.assertIn('Segregation of Duties', str(cm.exception))

    def test_different_user_approves_eco(self):
        eco, _rev_b = self._make_eco(requestor=self.user_a)
        eco.with_user(self.user_b).action_approve(comment='cross-user approve')
        eco.invalidate_recordset()
        self.assertEqual(eco.state, 'implementation')

    def test_sod_disabled_allows_self_approve_eco(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'productgraph.enforce_sod', 'False',
        )
        try:
            eco, _rev_b = self._make_eco(requestor=self.user_a)
            eco.with_user(self.user_a).action_approve(comment='self ok')
            eco.invalidate_recordset()
            self.assertEqual(eco.state, 'implementation')
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'productgraph.enforce_sod', 'True',
            )
