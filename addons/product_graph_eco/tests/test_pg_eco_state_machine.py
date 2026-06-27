# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgEcoStateMachine(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Use base.user_admin (uid=2, active=True) — env.user is OdooBot
        # (uid=1, active=False) by default, and M2M assignments to
        # res.users skip inactive users.
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        # PG-120 SoD off — this test pre-dates the guard and walks the full
        # state machine as a single user. Dedicated SoD coverage lives in
        # test_sod.py.
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.enforce_sod', 'False',
        )
        # PG-130 auto-close also off — this test exercises explicit ECO
        # transitions; auto-close cascading from revision release would
        # release the ECO before the test reaches the manual action_release.
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.auto_close_eco_on_all_revisions_released', 'False',
        )

    def _make_item(self):
        item = self.env['pg.item'].create({
            'name': 'ECO Test Item',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    def _make_revision(self, item, code='A'):
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'Initial release of the test item.',
        })

    def _release_revision(self, item, code='A'):
        rev = self._make_revision(item, code=code)
        rev.action_submit_for_review()
        rev.action_release()
        return rev

    def _make_eco(self, with_line=True):
        item = self._make_item()
        rev_a = self._release_revision(item, code='A')
        rev_b = self._make_revision(item, code='B')
        eco = self.env['pg.eco'].create({
            'title': 'Test ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })
        if with_line:
            self.env['pg.eco.line'].create({
                'eco_id': eco.id,
                'item_id': item.id,
                'from_revision_id': rev_a.id,
                'to_revision_id': rev_b.id,
                'change_kind': 'revision_bump',
            })
        return eco, item, rev_a, rev_b

    # ──────────────────────────────────────────────────────────────────
    # Happy-path: walk every state in turn
    # ──────────────────────────────────────────────────────────────────
    def test_full_happy_path_draft_to_released(self):
        eco, item, rev_a, rev_b = self._make_eco()
        self.assertEqual(eco.state, 'draft')
        self.assertTrue(eco.name.startswith('ECO/'))

        eco.action_start_analysis()
        self.assertEqual(eco.state, 'analysis')

        eco.action_submit_for_approval()
        self.assertEqual(eco.state, 'approval')

        eco.action_approve(comment='LGTM')
        self.assertEqual(eco.state, 'implementation')
        self.assertEqual(len(eco.approval_ids), 1)
        self.assertEqual(eco.approval_ids.decision, 'approved')

        # The to_revision must be released before the ECO can release.
        rev_b.action_submit_for_review()
        rev_b.action_release()
        self.assertEqual(rev_b.state, 'released')

        eco.action_release()
        self.assertEqual(eco.state, 'released')
        self.assertTrue(eco.actual_release_date)

    # ──────────────────────────────────────────────────────────────────
    # Negative paths
    # ──────────────────────────────────────────────────────────────────
    def test_submit_without_lines_raises(self):
        eco, *_ = self._make_eco(with_line=False)
        eco.action_start_analysis()
        with self.assertRaises(ValidationError):
            eco.action_submit_for_approval()

    def test_submit_without_board_raises(self):
        eco, item, rev_a, rev_b = self._make_eco()
        eco.approval_board_ids = [(5, 0, 0)]
        eco.action_start_analysis()
        with self.assertRaises(ValidationError):
            eco.action_submit_for_approval()

    def test_release_blocks_when_to_revision_not_released(self):
        eco, item, rev_a, rev_b = self._make_eco()
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='proceed')
        # rev_b is still draft — releasing the ECO must fail.
        with self.assertRaises(ValidationError):
            eco.action_release()
        self.assertEqual(eco.state, 'implementation')

    def test_reject_requires_reason(self):
        eco, *_ = self._make_eco()
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        with self.assertRaises(ValidationError):
            eco.action_reject()
        self.assertEqual(eco.state, 'approval')

    def test_reject_records_decision_and_reason(self):
        eco, *_ = self._make_eco()
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_reject(reason='Scope too broad — split into two ECOs.')
        self.assertEqual(eco.state, 'rejected')
        self.assertIn('Scope too broad', eco.rejection_reason or '')
        self.assertEqual(eco.approval_ids.decision, 'rejected')

    def test_reset_to_draft_only_from_analysis(self):
        eco, *_ = self._make_eco()
        eco.action_start_analysis()
        eco.action_reset_to_draft()
        self.assertEqual(eco.state, 'draft')

        eco.action_start_analysis()
        eco.action_submit_for_approval()
        with self.assertRaises(UserError):
            eco.action_reset_to_draft()
