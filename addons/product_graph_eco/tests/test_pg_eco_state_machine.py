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

    # ──────────────────────────────────────────────────────────────────
    # B6.1 — raw write({'state': ...}) must respect the state machine,
    # even for records already in a terminal state and even though the
    # legacy BLOCKLIST/ALLOWLIST branches alone never caught this.
    # ──────────────────────────────────────────────────────────────────
    def test_released_eco_blocks_raw_state_write_to_draft(self):
        # Seed a released ECO via create() (not write()) — create() never
        # goes through the mixin's write() guard, so this is the correct
        # way to construct a terminal-state fixture without exercising (or
        # defeating) the transition guard under test.
        eco = self.env['pg.eco'].create({
            'title': 'Released ECO for regression test',
            'change_type': 'corrective',
            'state': 'released',
        })
        with self.assertRaises(UserError):
            eco.write({'state': 'draft'})
        self.assertEqual(eco.state, 'released')

    def test_released_eco_two_step_regress_then_rewrite_lines_blocked(self):
        # Reproduces the full B6.1 exploit chain: (1) regress state off
        # 'released' via a raw write, then (2) rewrite line_ids now that
        # state != 'released' defeats the BLOCKLIST branch. Step (1) must
        # be the one that raises — the record must never actually leave
        # 'released', so step (2) never becomes reachable via this path.
        eco = self.env['pg.eco'].create({
            'title': 'Exploit-chain ECO',
            'change_type': 'corrective',
            'state': 'released',
        })
        with self.assertRaises(UserError):
            eco.write({'state': 'draft'})
        self.assertEqual(eco.state, 'released')
        # Still blocked directly too (belt-and-suspenders BLOCKLIST entry).
        with self.assertRaises(UserError):
            eco.write({'line_ids': [(5, 0, 0)]})

    def test_direct_state_write_skips_stages_blocked(self):
        # A draft ECO jumping straight to 'released' via a raw write must
        # be blocked — proves the guard closes arbitrary-jump bypasses in
        # general, not just released-state regression.
        eco, *_ = self._make_eco()
        self.assertEqual(eco.state, 'draft')
        with self.assertRaises(UserError):
            eco.write({'state': 'released'})
        self.assertEqual(eco.state, 'draft')

    # ──────────────────────────────────────────────────────────────────
    # B6.2 — write() must enforce the state machine on raw {'state': X}
    # writes, independent of the action_* methods. Adversarial review
    # surfaced this as a separate finding from B6.1, but both point at
    # the same gap and B6.1's unconditional _check_transition_allowed()
    # guard in pg.lifecycle.mixin.write() (product_graph_base) already
    # closes it — see test_direct_state_write_skips_stages_blocked and
    # test_released_eco_blocks_raw_state_write_to_draft above, and
    # test_released_eco_blocks_state_write in test_pg_eco_constraints.py.
    # These tests round out that coverage with the exact scenarios named
    # in B6.2's test plan.
    # ──────────────────────────────────────────────────────────────────
    def test_raw_write_state_draft_to_implementation_blocked(self):
        # Multi-hop illegal jump: draft can only reach 'analysis'.
        eco, *_ = self._make_eco()
        self.assertEqual(eco.state, 'draft')
        with self.assertRaises(UserError):
            eco.write({'state': 'implementation'})
        self.assertEqual(eco.state, 'draft')

    def test_raw_write_state_reopen_released_blocked(self):
        # Sharpest instance of the finding: silently reopening a released
        # ECO via a raw write outside the release-bypass context.
        eco, item, rev_a, rev_b = self._make_eco()
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='LGTM')
        rev_b.action_submit_for_review()
        rev_b.action_release()
        eco.action_release()
        self.assertEqual(eco.state, 'released')
        with self.assertRaises(UserError):
            eco.write({'state': 'draft'})
        self.assertEqual(eco.state, 'released')

    def test_raw_write_illegal_backward_jump_blocked_mid_flow(self):
        # B6.2's plan described this scenario as "draft -> analysis via a
        # raw write, skipping action_start_analysis, must still raise
        # even though analysis is a technically-valid next state." That
        # framing doesn't hold against the landed guard (matching B6.1):
        # the guard rejects jumps that are NOT in LIFECYCLE_TRANSITIONS,
        # so a raw write to a state that genuinely is the next legal hop
        # succeeds (see test_bypass_context_still_allows_raw_state_write
        # below for the equivalent case). The real "looks adjacent but is
        # actually illegal" jump in this state machine is a backward move
        # from a non-terminal, mid-flow state — e.g. approval -> draft,
        # which skips action_reset_to_draft's own from-state whitelist
        # (only 'analysis' may reset to draft).
        eco, *_ = self._make_eco()
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        self.assertEqual(eco.state, 'approval')
        with self.assertRaises(UserError):
            eco.write({'state': 'draft'})
        self.assertEqual(eco.state, 'approval')

    def test_bypass_context_still_allows_raw_state_write(self):
        # Regression-guard for the two sanctioned production bypass call
        # sites (action_release, _do_auto_close) and the harness shortcut
        # in test_pg_eco_constraints.py::test_unlink_blocked_outside_draft:
        # a legal single-hop transition performed under the
        # release-bypass context must keep working unchanged. B6.1's
        # guard runs even when the bypass context is set (see
        # lifecycle_mixin.py), but it only rejects ILLEGAL jumps — every
        # legitimate bypass call site performs a legal single-hop move,
        # so none of them are affected.
        eco, item, rev_a, rev_b = self._make_eco()
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='LGTM')
        self.assertEqual(eco.state, 'implementation')
        rev_b.action_submit_for_review()
        rev_b.action_release()
        eco.with_context(_pg_eco_release_bypass=True).write({
            'state': 'released',
        })
        self.assertEqual(eco.state, 'released')

    # ──────────────────────────────────────────────────────────────────
    # B6.7 — action_release() must accept a governed line whose target
    # revision has since been superseded, matching the "finished" semantic
    # already used by _compute_lifecycle_buttons (can_promote) and
    # _check_close_on_all_released (auto-close cascade). Before the fix,
    # action_release() alone required the literal 'released' state, so a
    # legitimately-complete ECO whose target revision was superseded by a
    # second, faster-moving change could not be manually released even
    # though can_promote reported True (a broken/contradictory UX).
    # ──────────────────────────────────────────────────────────────────
    def test_action_release_succeeds_when_target_revision_superseded(self):
        eco, item, rev_a, rev_b = self._make_eco()
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='LGTM')
        self.assertEqual(eco.state, 'implementation')

        # rev_b reaches 'released', then gets superseded by a follow-up
        # revision C *before* this ECO calls action_release().
        rev_b.action_submit_for_review()
        rev_b.action_release()
        rev_c = self._make_revision(item, code='C')
        rev_c.action_submit_for_review()
        rev_c.action_release()
        rev_b.successor_revision_id = rev_c
        rev_b.action_supersede()
        self.assertEqual(rev_b.state, 'superseded')

        # Must succeed, not raise ValidationError.
        eco.action_release()
        self.assertEqual(eco.state, 'released')
        self.assertTrue(eco.actual_release_date)

    def test_can_promote_matches_action_release_outcome(self):
        # Closes the false-positive-button gap that made B6.7 a HIGH: the
        # can_promote compute and action_release()'s own gate must agree
        # for the same fixture, not just happen to both eventually work.
        eco, item, rev_a, rev_b = self._make_eco()
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='LGTM')
        self.assertEqual(eco.state, 'implementation')

        rev_b.action_submit_for_review()
        rev_b.action_release()
        rev_c = self._make_revision(item, code='C')
        rev_c.action_submit_for_review()
        rev_c.action_release()
        rev_b.successor_revision_id = rev_c
        rev_b.action_supersede()
        self.assertEqual(rev_b.state, 'superseded')

        self.assertTrue(
            eco.can_promote,
            "can_promote should report True once the only governed line's "
            "target revision is released/superseded.",
        )
        # action_promote() dispatches to action_release() from
        # 'implementation' — must not raise, matching the can_promote signal.
        eco.action_promote()
        self.assertEqual(eco.state, 'released')
