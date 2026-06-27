# -*- coding: utf-8 -*-
"""Regression tests for PG-101 (self-ref / cycle) and PG-103 (draft-revision
release gate). See the bug brief at the time of fix for context."""
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph', 'product_graph_data_integrity')
class TestPgEbomDataIntegrity(TransactionCase):
    """PG-101 + PG-103 regression coverage."""

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

    # ─── Helpers ────────────────────────────────────────────────────────────

    def _make_item_draft_revision(self, item_type='part', name='Item'):
        """Create an item + a draft revision. No release."""
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': item_type,
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'Initial revision for integrity test.',
        })
        return item, rev

    def _make_released_item(self, item_type='part', name='Item'):
        """Create item + fully released revision."""
        item, rev = self._make_item_draft_revision(item_type, name)
        rev.action_submit_for_review()
        rev.action_release()
        return item, rev

    def _make_released_ebom(self, root_item, root_rev, child_items):
        """Build + release an EBOM(root_item) whose lines are child_items."""
        ebom = self.env['pg.ebom'].create({
            'root_item_id': root_item.id,
            'revision_id': root_rev.id,
            'ebom_line_ids': [
                (0, 0, {'pg_item_id': child.id, 'product_qty': 1.0})
                for child in child_items
            ],
        })
        ebom.action_submit_for_review()
        ebom.action_release()
        return ebom

    # ═══════════════════════════════════════════════════════════════════════
    # PG-101 — Self-reference and transitive cycles
    # ═══════════════════════════════════════════════════════════════════════

    def test_self_ref_blocked_on_validate(self):
        """Direct self-ref reported by action_validate().

        We can't reach action_validate() with a saved self-ref line because
        the @api.constrains companion fires first. Drive the validator
        directly with an unsaved NewId line via the cache so we exercise
        the action_validate() branch independently from the constraint."""
        a, a_rev = self._make_released_item(item_type='assembly', name='A')
        # Build a transient EBOM in cache (no DB save → no constrains trip)
        # using the standard Odoo `new()` helper.
        ebom = self.env['pg.ebom'].new({
            'root_item_id': a.id,
            'revision_id': a_rev.id,
            'ebom_line_ids': [(0, 0, {
                'pg_item_id': a.id,
                'product_qty': 1.0,
            })],
        })
        result = ebom.action_validate()
        self.assertFalse(result['ok'])
        msg = '\n'.join(result['errors'])
        self.assertIn('cannot be a component of its own', msg)

    def test_self_ref_blocked_on_constrains(self):
        """Constrains fires at line create — no need to call action_validate()."""
        a, a_rev = self._make_released_item(item_type='assembly', name='A2')
        with self.assertRaises(ValidationError):
            self.env['pg.ebom'].create({
                'root_item_id': a.id,
                'revision_id': a_rev.id,
                'ebom_line_ids': [(0, 0, {
                    'pg_item_id': a.id,
                    'product_qty': 1.0,
                })],
            })

    def test_transitive_cycle_blocked_A_B_A(self):
        """A→B→A: build EBOM(B) referencing A, then try EBOM(A) referencing B."""
        a, a_rev = self._make_released_item(item_type='assembly', name='A3')
        b, b_rev = self._make_released_item(item_type='assembly', name='B3')
        # EBOM(B) contains A → released.
        self._make_released_ebom(b, b_rev, [a])
        # Now build EBOM(A) containing B. Validate should report a cycle.
        ebom_a = self.env['pg.ebom'].create({
            'root_item_id': a.id,
            'revision_id': a_rev.id,
            'ebom_line_ids': [(0, 0, {
                'pg_item_id': b.id,
                'product_qty': 1.0,
            })],
        })
        result = ebom_a.action_validate()
        self.assertFalse(result['ok'])
        msg = '\n'.join(result['errors'])
        self.assertIn('circular BOM', msg)
        # Path should show A → B → A
        self.assertIn('A3', msg)
        self.assertIn('B3', msg)

    def test_transitive_cycle_blocked_three_level(self):
        """A→B→C→A: deeper graph still detected."""
        a, a_rev = self._make_released_item(item_type='assembly', name='A4')
        b, b_rev = self._make_released_item(item_type='assembly', name='B4')
        c, c_rev = self._make_released_item(item_type='assembly', name='C4')
        # EBOM(C) contains A.
        self._make_released_ebom(c, c_rev, [a])
        # EBOM(B) contains C.
        self._make_released_ebom(b, b_rev, [c])
        # Now try EBOM(A) containing B.
        ebom_a = self.env['pg.ebom'].create({
            'root_item_id': a.id,
            'revision_id': a_rev.id,
            'ebom_line_ids': [(0, 0, {
                'pg_item_id': b.id,
                'product_qty': 1.0,
            })],
        })
        result = ebom_a.action_validate()
        self.assertFalse(result['ok'])
        self.assertIn('circular BOM', '\n'.join(result['errors']))

    def test_no_cycle_allowed_normal(self):
        """A→B→C with no cycle: validate succeeds."""
        a, a_rev = self._make_released_item(item_type='assembly', name='A5')
        b, b_rev = self._make_released_item(item_type='assembly', name='B5')
        c, c_rev = self._make_released_item(item_type='part', name='C5')
        # EBOM(B) contains C — no cycle.
        self._make_released_ebom(b, b_rev, [c])
        ebom_a = self.env['pg.ebom'].create({
            'root_item_id': a.id,
            'revision_id': a_rev.id,
            'ebom_line_ids': [(0, 0, {
                'pg_item_id': b.id,
                'product_qty': 1.0,
            })],
        })
        result = ebom_a.action_validate()
        self.assertTrue(result['ok'], msg=f"unexpected errors: {result['errors']}")

    # ═══════════════════════════════════════════════════════════════════════
    # PG-103 — EBOM release blocked when parent revision is draft
    # ═══════════════════════════════════════════════════════════════════════

    def test_ebom_release_blocked_if_parent_revision_draft(self):
        """EBOM with draft revision_id must refuse to release."""
        # Item with a DRAFT revision (don't release it).
        asm, asm_rev = self._make_item_draft_revision(
            item_type='assembly', name='AsmDraftRev',
        )
        # Need a released child item so the children-released check doesn't
        # mask the parent-revision check.
        comp, _comp_rev = self._make_released_item(item_type='part', name='CompForDraft')
        # Force the asm to engineering state so the revision-release path
        # would otherwise be reachable.
        ebom = self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [(0, 0, {
                'pg_item_id': comp.id,
                'product_qty': 1.0,
            })],
        })
        ebom.action_submit_for_review()
        with self.assertRaises(UserError) as cm:
            ebom.action_release()
        self.assertIn('parent revision', str(cm.exception))

    def test_ebom_release_succeeds_after_revision_release(self):
        """Sanity: once parent revision is released, EBOM release works."""
        asm, asm_rev = self._make_released_item(
            item_type='assembly', name='AsmReleasedRev',
        )
        comp, _comp_rev = self._make_released_item(
            item_type='part', name='CompForReleased',
        )
        ebom = self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [(0, 0, {
                'pg_item_id': comp.id,
                'product_qty': 1.0,
            })],
        })
        ebom.action_submit_for_review()
        ebom.action_release()
        self.assertEqual(ebom.state, 'released')

    def test_chatter_warning_on_revision_release_with_draft_ebom(self):
        """Releasing a revision with a draft child EBOM posts a chatter notice."""
        asm, asm_rev = self._make_item_draft_revision(
            item_type='assembly', name='AsmCascade',
        )
        comp, _comp_rev = self._make_released_item(
            item_type='part', name='CompForCascade',
        )
        # Draft EBOM hanging off the draft revision.
        self.env['pg.ebom'].create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [(0, 0, {
                'pg_item_id': comp.id,
                'product_qty': 1.0,
            })],
        })
        # Release the revision.
        asm_rev.action_submit_for_review()
        asm_rev.action_release()
        # Inspect chatter on the revision for the cascade warning.
        messages = asm_rev.message_ids.mapped('body')
        joined = ' '.join(m or '' for m in messages)
        self.assertIn('Draft EBOM(s) remain', joined)
