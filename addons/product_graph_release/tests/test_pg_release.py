# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgRelease(TransactionCase):

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
            'change_summary': 'Initial release for release test.',
        })
        rev.action_submit_for_review()
        rev.action_release()
        return item, rev

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

    def test_release_creates_mrp_bom(self):
        asm, asm_rev = self._release_item('Cab', 'assembly')
        comp, _ = self._release_item('Hinge')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 4.0)])
        release = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'First release of cab.',
        })
        release.action_execute_release()
        self.assertEqual(release.state, 'completed')
        self.assertTrue(release.mrp_bom_id)
        self.assertEqual(len(release.mrp_bom_id.bom_line_ids), 1)
        self.assertEqual(release.mrp_bom_id.bom_line_ids[0].product_qty, 4.0)

    def test_release_validation_blocks_unreleased_child(self):
        asm, asm_rev = self._release_item('Cab2', 'assembly')
        # Unreleased child
        comp = self.env['pg.item'].create({
            'name': 'DraftHinge',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        # Manually create EBOM in draft (cannot release per pg.ebom check)
        ebom = self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [(0, 0, {'pg_item_id': comp.id, 'product_qty': 1.0})],
        })
        ebom.action_submit_for_review()
        # Cannot release ebom with draft child
        with self.assertRaises(UserError):
            ebom.action_release()

    def test_completed_release_immutable(self):
        asm, asm_rev = self._release_item('Cab3', 'assembly')
        comp, _ = self._release_item('Hinge3')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        release = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'Test.',
        })
        release.action_execute_release()
        with self.assertRaises(UserError):
            release.write({'release_reason': 'Mutated'})

    def test_completed_release_blocks_raw_state_regression(self):
        # B6.1 — 'state' is inside LIFECYCLE_MUTABLE_LOCKED (ALLOWLIST mode)
        # so a raw write({'state': 'pending'}) on a completed release used
        # to slip through untouched. The mixin's unconditional
        # _check_transition_allowed() guard must now block it ('completed'
        # has no outgoing transitions at all).
        asm, asm_rev = self._release_item('Cab3b', 'assembly')
        comp, _ = self._release_item('Hinge3b')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        release = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'Test.',
        })
        release.action_execute_release()
        with self.assertRaises(UserError):
            release.write({'state': 'pending'})
        self.assertEqual(release.state, 'completed')

    def test_release_reset_to_pending_still_works_from_failed(self):
        # B6.3 regression guard: removing 'state' from
        # LIFECYCLE_MUTABLE_LOCKED must NOT affect action_reset_to_pending.
        # 'failed' is not in LIFECYCLE_TERMINAL_STATES, so a 'failed'
        # release is never in a locked state to begin with — the ALLOWLIST
        # branch never even evaluates for it, regardless of what fields are
        # in the allowlist.
        asm, asm_rev = self._release_item('Cab3c', 'assembly')
        comp, _ = self._release_item('Hinge3c')
        ebom = self._build_and_release_ebom(asm, asm_rev, [(comp, 1.0)])
        release = self.env['pg.release'].create({
            'ebom_id': ebom.id,
            'release_reason': 'Will fail mid-flight.',
        })
        raised = None
        with patch.object(
            type(release), '_create_mrp_bom',
            side_effect=RuntimeError('boom from test'),
        ):
            try:
                release.action_execute_release()
            except RuntimeError as exc:
                raised = exc
        self.assertIsNotNone(raised)
        release.invalidate_recordset()
        self.assertEqual(release.state, 'failed')

        release.action_reset_to_pending()
        self.assertEqual(release.state, 'pending')
        self.assertFalse(release.failure_reason)

    def test_mo_freeze_records_open_mos(self):
        # Release once to create mrp.bom, then create an MO, then release again.
        asm, asm_rev = self._release_item('Cab4', 'assembly')
        comp, _ = self._release_item('Hinge4')
        ebom_a = self._build_and_release_ebom(asm, asm_rev, [(comp, 2.0)])
        rel_a = self.env['pg.release'].create({
            'ebom_id': ebom_a.id, 'release_reason': 'First.',
        })
        rel_a.action_execute_release()
        # Create an open MO on the new bom
        mo = self.env['mrp.production'].create({
            'product_id': rel_a.product_id.id,
            'product_qty': 1.0,
            'bom_id': rel_a.mrp_bom_id.id,
        })
        # Create a new revision + new EBOM
        rev_b = self.env['pg.revision'].create({
            'item_id': asm.id,
            'name': 'B',
            'change_summary': 'Revised assembly drawing for v2.',
        })
        rev_b.action_submit_for_review()
        rev_b.action_release()
        ebom_b = self._build_and_release_ebom(asm, rev_b, [(comp, 3.0)])
        rel_b = self.env['pg.release'].create({
            'ebom_id': ebom_b.id, 'release_reason': 'Rev B release.',
        })
        rel_b.action_execute_release()
        self.assertIn(mo, rel_b.frozen_mo_ids)
        self.assertEqual(rel_b.frozen_mo_count, 1)
        # Old MO must NOT be modified — its bom_id remains the old one
        self.assertEqual(mo.bom_id, rel_a.mrp_bom_id)
