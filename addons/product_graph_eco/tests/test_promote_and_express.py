# -*- coding: utf-8 -*-
"""PG-161 — One-click Promote + Express Release for pg.eco.

Covers:
- action_promote routes via the state→method PROMOTION_MAP and inherits
  every guard of the underlying transitions (lines, board, SoD, revision
  readiness).
- can_promote / promote_blocked_reason mirror those guards for the UI.
- action_express_release fast-tracks draft → released in one shot,
  honoring the SoD bypass context flag, and is admin-only when SoD is
  enforced.
- can_express_release reflects the admin / line-presence gate.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'product_graph_promote')
class TestPgEcoPromoteAndExpress(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        engineer = cls.env.ref('product_graph_base.group_pg_engineer')
        approver = cls.env.ref('product_graph_base.group_pg_approver')
        admin = cls.env.ref('product_graph_base.group_pg_admin')

        # Admin user — full capability; satisfies the admin-bypass branch
        # in Express Release. Use base.user_admin (active) rather than
        # OdooBot which is inactive and silently skipped from M2Ms.
        cls.user_admin = cls.env.ref('base.user_admin')
        cls.user_admin.group_ids |= (engineer | approver | admin)

        # A second engineer+approver (NOT in admin) — used for the
        # SoD-blocks-non-admin Express Release test and for the
        # cross-user SoD-passes Promote test.
        cls.user_other = cls.env['res.users'].create({
            'name': 'PG-161 Other Engineer',
            'login': 'pg161_other@example.com',
            'email': 'pg161_other@example.com',
            'group_ids': [(4, engineer.id), (4, approver.id)],
        })

        cls.env = cls.env(user=cls.user_admin)
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        # Make sure auto-close is on for the express-flow tests so
        # revision releases done before the ECO release don't trip us.
        cls.ICP.set_param(
            'productgraph.auto_close_eco_on_all_revisions_released', 'True',
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _make_item(self, suffix=''):
        item = self.env['pg.item'].create({
            'name': 'PG-161 Item ' + suffix,
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    def _make_revision(self, item, code='A',
                       summary='Initial release of the PG-161 item.'):
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': summary,
        })

    def _release_revision(self, item, code='A'):
        # SoD off for the test setup helper so the same admin can
        # submit and release the bootstrap revision. Restore the prior
        # value so the surrounding test's SoD assertion stands.
        prior_sod = self.ICP.get_param('productgraph.enforce_sod', 'True')
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        try:
            rev = self._make_revision(item, code=code)
            rev.action_submit_for_review()
            rev.action_release()
        finally:
            self.ICP.set_param('productgraph.enforce_sod', str(prior_sod))
        return rev

    def _make_eco(self, with_line=True, board=None, suffix=''):
        item = self._make_item(suffix=suffix)
        rev_a = self._release_revision(item, code='A')
        rev_b = self._make_revision(
            item, code='B', summary='Target revision for the PG-161 ECO.',
        )
        if board is None:
            board = [self.user_admin.id, self.user_other.id]
        eco = self.env['pg.eco'].create({
            'title': 'PG-161 Test ECO ' + suffix,
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, board)],
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

    # ── action_promote ────────────────────────────────────────────────────

    def test_eco_promote_draft_to_analysis(self):
        eco, *_ = self._make_eco(suffix='draft-to-analysis')
        self.assertEqual(eco.state, 'draft')
        self.assertTrue(eco.can_promote)
        eco.action_promote()
        self.assertEqual(eco.state, 'analysis')

    def test_eco_promote_blocked_without_lines(self):
        eco, *_ = self._make_eco(with_line=False, suffix='no-lines')
        eco.invalidate_recordset()
        self.assertFalse(eco.can_promote)
        self.assertIn('affected-item line', eco.promote_blocked_reason or '')
        with self.assertRaises(UserError):
            eco.action_promote()

    def test_eco_promote_approval_blocked_by_sod(self):
        """ECO submitted by current user → Promote in approval state is SoD-blocked."""
        self.ICP.set_param('productgraph.enforce_sod', 'True')
        eco, *_ = self._make_eco(suffix='sod-block')
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.invalidate_recordset()
        self.assertEqual(eco.state, 'approval')
        self.assertEqual(eco.submitted_by_id, self.user_admin)
        self.assertFalse(eco.can_promote)
        self.assertIn('SoD', eco.promote_blocked_reason or '')
        with self.assertRaises(UserError) as cm:
            eco.action_promote()
        self.assertIn('Segregation of Duties', str(cm.exception))

    def test_eco_promote_approval_works_for_different_user(self):
        """Different user from requestor/submitter → Promote in approval succeeds."""
        self.ICP.set_param('productgraph.enforce_sod', 'True')
        eco, *_ = self._make_eco(suffix='sod-pass')
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco_other = eco.with_user(self.user_other)
        eco_other.invalidate_recordset()
        self.assertTrue(eco_other.can_promote)
        eco_other.action_promote()
        eco.invalidate_recordset()
        self.assertEqual(eco.state, 'implementation')

    def test_eco_promote_implementation_blocked_by_pending_revisions(self):
        """In implementation, can_promote is False until the target revision releases."""
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        eco, item, rev_a, rev_b = self._make_eco(suffix='impl-pending')
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='advance')
        eco.invalidate_recordset()
        self.assertEqual(eco.state, 'implementation')
        # rev_b is still draft → can't promote yet.
        self.assertFalse(eco.can_promote)
        self.assertIn('Waiting', eco.promote_blocked_reason or '')
        # The underlying action also raises.
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            eco.action_promote()

    def test_eco_promote_implementation_works_when_all_released(self):
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        eco, item, rev_a, rev_b = self._make_eco(suffix='impl-ok')
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='advance')
        # Release rev_b so the line gate clears.
        rev_b.action_submit_for_review()
        rev_b.action_release()
        # rev_b.action_release() may auto-close the ECO via PG-130
        # (auto_close_eco_on_all_revisions_released). Tolerate either:
        # if already released, can_promote is False (final state). If still
        # in implementation, action_promote releases it.
        eco.invalidate_recordset()
        if eco.state == 'released':
            self.assertFalse(eco.can_promote)
            return
        self.assertEqual(eco.state, 'implementation')
        self.assertTrue(eco.can_promote)
        eco.action_promote()
        self.assertEqual(eco.state, 'released')

    def test_eco_promote_released_raises(self):
        """Released ECO → Promote raises a UserError."""
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        eco, item, rev_a, rev_b = self._make_eco(suffix='released-raises')
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='advance')
        rev_b.action_submit_for_review()
        rev_b.action_release()
        if eco.state != 'released':
            eco.action_release()
        eco.invalidate_recordset()
        self.assertEqual(eco.state, 'released')
        self.assertFalse(eco.can_promote)
        with self.assertRaises(UserError) as cm:
            eco.action_promote()
        self.assertIn('cannot be promoted further', str(cm.exception))

    def test_eco_promote_rejected_raises(self):
        """Rejected ECO → Promote raises a UserError."""
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        eco, *_ = self._make_eco(suffix='rejected-raises')
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.rejection_reason = 'Wrong scope — not now.'
        eco.action_reject(reason='Wrong scope — not now.')
        eco.invalidate_recordset()
        self.assertEqual(eco.state, 'rejected')
        self.assertFalse(eco.can_promote)
        with self.assertRaises(UserError):
            eco.action_promote()

    # ── action_express_release ────────────────────────────────────────────

    def test_eco_express_release_full_flow(self):
        """SoD off + draft ECO whose target revision is already released
        → Express Release lands on released in one click."""
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        eco, item, rev_a, rev_b = self._make_eco(suffix='express-full')
        # Release the target revision up front so the Release leg passes.
        rev_b.action_submit_for_review()
        rev_b.action_release()
        eco.invalidate_recordset()
        # Auto-close may already have flipped the ECO if it was in
        # implementation — but ours is still in draft, so it can't have.
        self.assertEqual(eco.state, 'draft')
        self.assertTrue(eco.can_express_release)
        eco.action_express_release()
        self.assertEqual(eco.state, 'released')
        self.assertTrue(eco.actual_release_date)

    def test_eco_express_release_blocked_by_sod_non_admin(self):
        """SoD on + non-admin user → can_express_release is False and the
        action raises with the SoD message."""
        self.ICP.set_param('productgraph.enforce_sod', 'True')
        eco, *_ = self._make_eco(suffix='express-sod')
        eco_other = eco.with_user(self.user_other)
        eco_other.invalidate_recordset()
        self.assertFalse(eco_other.can_express_release)
        self.assertIn('SoD', eco_other.express_release_blocked_reason or '')
        with self.assertRaises(UserError) as cm:
            eco_other.action_express_release()
        self.assertIn('Segregation of Duties', str(cm.exception))

    def test_eco_express_release_admin_bypasses_sod(self):
        """SoD on + admin user → Express Release succeeds end-to-end."""
        self.ICP.set_param('productgraph.enforce_sod', 'True')
        eco, item, rev_a, rev_b = self._make_eco(suffix='express-admin')
        # Bootstrap rev_b through to released (helper toggled SoD off,
        # but reset it after; for rev_b we need a different user to
        # release. Easier: SoD-off bypass via context bypass.)
        # Cleanest path: temporarily disable SoD for the bootstrap.
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        rev_b.action_submit_for_review()
        rev_b.action_release()
        self.ICP.set_param('productgraph.enforce_sod', 'True')

        eco.invalidate_recordset()
        # Admin user has the admin group, so Express is allowed even under SoD.
        self.assertTrue(eco.can_express_release)
        eco.action_express_release()
        self.assertEqual(eco.state, 'released')

    def test_eco_express_release_only_from_draft(self):
        """Express Release from a non-draft state raises UserError."""
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        eco, *_ = self._make_eco(suffix='express-non-draft')
        eco.action_start_analysis()
        self.assertEqual(eco.state, 'analysis')
        with self.assertRaises(UserError) as cm:
            eco.action_express_release()
        self.assertIn('Draft', str(cm.exception))

    def test_eco_express_release_blocked_no_lines(self):
        """Draft ECO with no lines → can_express_release False; action raises."""
        self.ICP.set_param('productgraph.enforce_sod', 'False')
        eco, *_ = self._make_eco(with_line=False, suffix='express-no-lines')
        eco.invalidate_recordset()
        self.assertFalse(eco.can_express_release)
        self.assertIn('line', eco.express_release_blocked_reason or '')
        # The underlying cascade raises (ValidationError from
        # action_submit_for_approval — no lines).
        with self.assertRaises(Exception):
            eco.action_express_release()
