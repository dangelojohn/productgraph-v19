# -*- coding: utf-8 -*-
"""PG-112 — MRP integration hardening tests.

Covers idempotency, MO traceability auto-stamp, supersede semantics, atomic
rollback + audit-log survival, frozen-MO MG-1 semantics, and the
"don't touch external mrp.boms" rule.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.product_graph_release.models.pg_release import RELEASE_LOCK_NAMESPACE


@tagged('post_install', '-at_install', 'product_graph')
class TestMrpIntegration(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disable SoD by default; tests that exercise SoD opt back in.
        cls.env['ir.config_parameter'].sudo().set_param('productgraph.enforce_sod', 'False')
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

    # ───────────────────────────────────────────────────────────────────────
    # Helpers
    # ───────────────────────────────────────────────────────────────────────

    def _release_item(self, name, item_type='part'):
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': item_type,
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'Initial release.',
        })
        rev.action_submit_for_review()
        rev.action_release()
        return item, rev

    def _new_revision(self, item, name, summary='Next revision for test.'):
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': name,
            'change_summary': summary,
        })
        rev.action_submit_for_review()
        rev.action_release()
        return rev

    def _build_and_release_ebom(self, root, root_rev, lines):
        ebom = self.env['pg.ebom'].create({
            'root_item_id': root.id,
            'revision_id': root_rev.id,
            'ebom_line_ids': [
                (0, 0, {'pg_item_id': child.id, 'product_qty': qty})
                for child, qty in lines
            ],
        })
        ebom.action_submit_for_review()
        ebom.action_release()
        return ebom

    def _release(self, ebom, reason='Test release.'):
        rel = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': reason,
        })
        rel.action_execute_release()
        return rel

    # ───────────────────────────────────────────────────────────────────────
    # TASK 7 tests
    # ───────────────────────────────────────────────────────────────────────

    def test_release_creates_mrp_bom_with_correct_lines(self):
        asm, asm_rev = self._release_item('AsmT1', 'assembly')
        comp_a, _ = self._release_item('CompA1')
        comp_b, _ = self._release_item('CompB1')
        comp_c, _ = self._release_item('CompC1')
        ebom = self._build_and_release_ebom(asm, asm_rev, [
            (comp_a, 2.0), (comp_b, 4.0), (comp_c, 1.0),
        ])
        rel = self._release(ebom)
        self.assertEqual(rel.state, 'completed')
        self.assertEqual(len(rel.mrp_bom_id.bom_line_ids), 3)
        qtys = {l.product_id.id: l.product_qty for l in rel.mrp_bom_id.bom_line_ids}
        self.assertEqual(qtys[comp_a.product_id.id], 2.0)
        self.assertEqual(qtys[comp_b.product_id.id], 4.0)
        self.assertEqual(qtys[comp_c.product_id.id], 1.0)
        # Provenance stamp on the new BOM:
        self.assertEqual(rel.mrp_bom_id.pg_release_id, rel)

    def test_release_stamps_mo_traceability(self):
        asm, asm_rev = self._release_item('AsmT2', 'assembly')
        comp, _ = self._release_item('CompT2')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        rel = self._release(ebom)
        mo = self.env['mrp.production'].create({
            'product_id': rel.product_id.id,
            'product_qty': 1.0,
            'bom_id': rel.mrp_bom_id.id,
        })
        self.assertEqual(mo.pg_release_id, rel)
        self.assertEqual(mo.pg_ebom_id, ebom)
        self.assertEqual(mo.pg_revision_code, asm_rev.name)
        self.assertEqual(mo.pg_root_item_id, asm)

    def test_release_is_idempotent_duplicate_blocked(self):
        asm, asm_rev = self._release_item('AsmT3', 'assembly')
        comp, _ = self._release_item('CompT3')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        self._release(ebom, reason='First.')
        # A second release on the same ebom+revision must be blocked.
        rel2 = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'Duplicate attempt.',
        })
        with self.assertRaises(UserError):
            rel2.action_execute_release()
        # And rel2 should still be pending (idempotency check fires before
        # the state flip to 'executing').
        self.assertEqual(rel2.state, 'pending')

    def test_completed_release_state_regression_via_raw_sql_still_detected(self):
        """B6.3 Layer 2 — _check_idempotency must key on mrp_bom_id, not
        just state, so a completed release whose `state` column is
        tampered with directly (bypassing every ORM guard, including the
        mixin's unconditional _check_transition_allowed() from B6.1) still
        surfaces as a prior release. Without this, a duplicate mrp.bom
        could be created for the same (ebom, revision) pair — a direct
        violation of R1 ('single canonical release per revision')."""
        asm, asm_rev = self._release_item('AsmT3b', 'assembly')
        comp, _ = self._release_item('CompT3b')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        rel1 = self._release(ebom, reason='First.')
        self.assertEqual(rel1.state, 'completed')
        self.assertTrue(rel1.mrp_bom_id)
        tmpl_id = rel1.product_id.product_tmpl_id.id
        bom_count_before = self.env['mrp.bom'].search_count([
            ('product_tmpl_id', '=', tmpl_id),
        ])

        # Simulate a state regression that bypasses the ORM (and therefore
        # the mixin's write() guard) entirely — a raw SQL tamper, or a
        # stand-in for a future regression of the LIFECYCLE_MUTABLE_LOCKED
        # allowlist. mrp_bom_id is untouched, exactly as it would be in a
        # real tamper (only `state` regresses).
        self.env.cr.execute(
            "UPDATE pg_release SET state = 'pending' WHERE id = %s",
            (rel1.id,),
        )
        rel1.invalidate_recordset()
        self.assertEqual(rel1.state, 'pending')
        self.assertTrue(rel1.mrp_bom_id, 'mrp_bom_id must survive the tamper')

        rel2 = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'Duplicate attempt after tamper.',
        })
        with self.assertRaises(UserError):
            rel2.action_execute_release()
        self.assertEqual(rel2.state, 'pending')

        bom_count_after = self.env['mrp.bom'].search_count([
            ('product_tmpl_id', '=', tmpl_id),
        ])
        self.assertEqual(
            bom_count_after, bom_count_before,
            'no second mrp.bom may be created for an already-released '
            '(ebom, revision) pair, even after a state tamper',
        )

    def test_release_atomic_rollback(self):
        asm, asm_rev = self._release_item('AsmT4', 'assembly')
        comp, _ = self._release_item('CompT4')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        rel = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'Will fail mid-flight.',
        })

        # NB: Odoo's TransactionCase.assertRaises wraps the block in an
        # outer savepoint and rolls it back when the expected exception
        # fires — that would silently undo the deliberate post-exception
        # failure-state write the test is asserting on. Catch by hand so
        # the failed-state write survives into the assertion phase.
        raised = None
        with patch.object(
            type(rel), '_create_mrp_bom',
            side_effect=RuntimeError('boom from test'),
        ):
            try:
                rel.action_execute_release()
            except RuntimeError as exc:
                raised = exc
        self.assertIsNotNone(raised, 'action_execute_release must re-raise')
        rel.invalidate_recordset()

        self.assertEqual(rel.state, 'failed')
        self.assertIn('boom from test', rel.failure_reason or '')
        # No partial mrp.bom landed referencing this release.
        self.assertEqual(
            self.env['mrp.bom'].search_count([('pg_release_id', '=', rel.id)]),
            0,
        )
        # No release lines either.
        self.assertEqual(len(rel.line_ids), 0)
        # Audit log entry SURVIVED the rollback (the failure-log write is
        # outside the savepoint by design — R4).
        audit = self.env['pg.audit.log'].search([
            ('res_model', '=', 'pg.release'),
            ('res_id', '=', rel.id),
            ('to_state', '=', 'failed'),
        ])
        self.assertTrue(audit, 'failure audit log entry must persist after rollback')

    def test_release_supersedes_previous_bom(self):
        asm, asm_rev = self._release_item('AsmT5', 'assembly')
        comp, _ = self._release_item('CompT5')
        ebom1 = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        rel1 = self._release(ebom1, reason='v1')
        bom_v1 = rel1.mrp_bom_id

        rev_b = self._new_revision(asm, 'B')
        ebom2 = self._build_and_release_ebom(asm, rev_b, [(comp, 2.0)])
        rel2 = self._release(ebom2, reason='v2')

        self.assertFalse(bom_v1.active, 'v1 BOM must be archived')
        self.assertEqual(bom_v1.superseded_by_release_id, rel2)
        self.assertNotEqual(rel1.mrp_bom_id.id, rel2.mrp_bom_id.id)
        self.assertTrue(rel2.mrp_bom_id.active)

    def test_release_freezes_open_mos(self):
        asm, asm_rev = self._release_item('AsmT6', 'assembly')
        comp, _ = self._release_item('CompT6')
        ebom1 = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        rel1 = self._release(ebom1, reason='v1')
        mo = self.env['mrp.production'].create({
            'product_id': rel1.product_id.id,
            'product_qty': 1.0,
            'bom_id': rel1.mrp_bom_id.id,
        })
        # MO is in 'draft' by default — that's already inside the freeze net
        # (draft/confirmed/progress). No need to push state forward.
        self.assertEqual(mo.state, 'draft')

        rev_b = self._new_revision(asm, 'B')
        ebom2 = self._build_and_release_ebom(asm, rev_b, [(comp, 2.0)])
        rel2 = self._release(ebom2, reason='v2')

        self.assertIn(mo, rel2.frozen_mo_ids)
        # MG-1: MO's bom_id is NOT silently swapped.
        self.assertEqual(mo.bom_id, rel1.mrp_bom_id)
        # MO still points at v1 release (it predates the new release).
        self.assertEqual(mo.pg_release_id, rel1)

    def test_release_does_not_touch_external_mrp_boms(self):
        # Step 1: release v1 normally — that creates the product.product and
        # the first pg-owned mrp.bom.
        asm, asm_rev = self._release_item('AsmT7', 'assembly')
        comp, _ = self._release_item('CompT7')
        ebom1 = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        rel1 = self._release(ebom1, reason='v1')
        product = rel1.product_id

        # Step 2: a human manually creates a sibling mrp.bom on the SAME
        # product (e.g. a service/repair BOM, or a misconfigured manual BOM).
        external_bom = self.env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_id': product.id,
            'type': 'normal',
            'product_qty': 1.0,
            'code': 'EXTERNAL-DO-NOT-TOUCH',
        })
        # Step 3: release v2 — the supersede search MUST skip the external
        # BOM and only flip the pg-owned v1.
        rev_b = self._new_revision(asm, 'B')
        ebom2 = self._build_and_release_ebom(asm, rev_b, [(comp, 2.0)])
        rel2 = self._release(ebom2, reason='v2')

        external_bom.invalidate_recordset()
        self.assertTrue(external_bom.active, 'external BOM must remain active')
        self.assertFalse(external_bom.pg_release_id)
        self.assertFalse(external_bom.superseded_by_release_id)
        # v1 BOM was the one archived.
        self.assertFalse(rel1.mrp_bom_id.active)
        self.assertEqual(rel1.mrp_bom_id.superseded_by_release_id, rel2)

    def test_release_audit_entry_on_failure(self):
        asm, asm_rev = self._release_item('AsmT8', 'assembly')
        comp, _ = self._release_item('CompT8')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        rel = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'Will fail.',
        })
        # NB: same gotcha as test_release_atomic_rollback — assertRaises wraps
        # in an outer savepoint and rolls back the failure-audit write. Catch
        # the exception by hand so the audit log entry survives.
        raised = None
        with patch.object(
            type(rel), '_create_mrp_bom',
            side_effect=RuntimeError('forced failure for audit test'),
        ):
            try:
                rel.action_execute_release()
            except RuntimeError as exc:
                raised = exc
        self.assertIsNotNone(raised, 'action_execute_release must re-raise')
        audit = self.env['pg.audit.log'].search([
            ('res_model', '=', 'pg.release'),
            ('res_id', '=', rel.id),
            ('action', '=', 'release'),
            ('from_state', '=', 'executing'),
            ('to_state', '=', 'failed'),
        ])
        self.assertEqual(len(audit), 1)

    # ───────────────────────────────────────────────────────────────────────
    # PG A6.1 — advisory lock + partial UNIQUE backstop
    # ───────────────────────────────────────────────────────────────────────

    def test_release_concurrent_race_partial_unique_catches_dup(self):
        """Even with the app-level idempotency check bypassed (simulating
        two transactions racing past _check_idempotency under READ
        COMMITTED), the partial UNIQUE index on
        pg_release(ebom_id, revision_id) WHERE state='completed' refuses
        the second 'completed' write, and the inner savepoint unwinds
        cleanly — no orphan mrp.bom / mrp.bom.line rows survive."""
        asm, asm_rev = self._release_item('AsmRace1', 'assembly')
        comp, _ = self._release_item('CompRace1')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])

        rel1 = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'Racer 1.',
        })
        rel2 = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'Racer 2.',
        })

        with patch.object(type(rel1), '_check_idempotency', return_value=None):
            rel1.action_execute_release()
            self.assertEqual(rel1.state, 'completed')

            # Catch by hand — see the atomic-rollback tests above for why
            # assertRaises' outer savepoint would be the wrong tool here.
            raised = None
            try:
                rel2.action_execute_release()
            except Exception as exc:  # psycopg2 UniqueViolation, re-raised by ORM
                raised = exc

        self.assertIsNotNone(
            raised, 'second racer must raise on the partial UNIQUE index',
        )
        self.assertIn('pg_release_completed_ebom_rev_uniq_idx', str(raised))

        rel2.invalidate_recordset()
        self.assertNotEqual(rel2.state, 'completed')
        self.assertEqual(
            self.env['mrp.bom'].search_count([('pg_release_id', '=', rel2.id)]),
            0,
            'rel2 savepoint must have rolled back its mrp.bom creation',
        )
        self.assertEqual(len(rel2.line_ids), 0)

    def test_release_advisory_lock_acquired(self):
        """The advisory lock must be acquired before both the idempotency
        search and the state='executing' write."""
        asm, asm_rev = self._release_item('AsmLock1', 'assembly')
        comp, _ = self._release_item('CompLock1')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        rel = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'Lock ordering check.',
        })

        events = []
        original_execute = self.env.cr.execute

        def spy_execute(query, *args, **kwargs):
            if query and 'pg_advisory_xact_lock' in query:
                params = args[0] if args else kwargs.get('params')
                events.append(('lock', params))
            return original_execute(query, *args, **kwargs)

        original_check = type(rel)._check_idempotency

        def spy_check(self_, *args, **kwargs):
            events.append(('check_idempotency', None))
            return original_check(self_, *args, **kwargs)

        original_write = type(rel).write

        def spy_write(self_, vals):
            if vals.get('state') == 'executing':
                events.append(('write_executing', None))
            return original_write(self_, vals)

        with patch.object(self.env.cr, 'execute', side_effect=spy_execute), \
                patch.object(type(rel), '_check_idempotency', spy_check), \
                patch.object(type(rel), 'write', spy_write):
            rel.action_execute_release()

        kinds = [e[0] for e in events]
        self.assertIn('lock', kinds)
        self.assertIn('check_idempotency', kinds)
        self.assertIn('write_executing', kinds)
        self.assertLess(
            kinds.index('lock'), kinds.index('check_idempotency'),
            'advisory lock must be acquired before _check_idempotency',
        )
        self.assertLess(
            kinds.index('lock'), kinds.index('write_executing'),
            "advisory lock must be acquired before the state='executing' write",
        )
        lock_params = events[kinds.index('lock')][1]
        self.assertEqual(lock_params, (RELEASE_LOCK_NAMESPACE, rel.revision_id.id))

    def test_release_lock_key_uses_revision_id_not_ebom_id(self):
        """Regression guard: the advisory lock key must be revision_id, not
        ebom_id, so a Supersede release (new revision) never contends with
        the prior revision's lock."""
        asm, asm_rev = self._release_item('AsmLockKey1', 'assembly')
        comp, _ = self._release_item('CompLockKey1')
        ebom1 = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        self._release(ebom1, reason='v1')

        rev_b = self._new_revision(asm, 'B')
        ebom2 = self._build_and_release_ebom(asm, rev_b, [(comp, 2.0)])
        rel2 = self.env['pg.release'].create({
            'ebom_id': ebom2.id,
            'release_reason': 'v2 supersede.',
        })

        seen_keys = []
        original_execute = self.env.cr.execute

        def spy(query, *args, **kwargs):
            if query and 'pg_advisory_xact_lock' in query:
                params = args[0] if args else kwargs.get('params')
                seen_keys.append(params)
            return original_execute(query, *args, **kwargs)

        with patch.object(self.env.cr, 'execute', side_effect=spy):
            rel2.action_execute_release()

        self.assertEqual(rel2.state, 'completed')
        self.assertEqual(seen_keys[-1], (RELEASE_LOCK_NAMESPACE, rev_b.id))
        self.assertNotEqual(seen_keys[-1][1], ebom2.id)
        self.assertNotEqual(seen_keys[-1][1], asm_rev.id)

    def test_pg_release_init_creates_partial_unique_index(self):
        """PgRelease.init() must create the partial UNIQUE index that backs
        the (ebom_id, revision_id, state='completed') invariant."""
        self.env.cr.execute("""
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'pg_release'
              AND indexname = 'pg_release_completed_ebom_rev_uniq_idx'
        """)
        row = self.env.cr.fetchone()
        self.assertTrue(
            row, 'pg_release_completed_ebom_rev_uniq_idx must exist after module init()',
        )
        indexdef = row[0].replace('"', '')
        self.assertIn('UNIQUE', indexdef.upper())
        self.assertIn('ebom_id', indexdef)
        self.assertIn('revision_id', indexdef)
        self.assertIn("state = 'completed'", indexdef)
