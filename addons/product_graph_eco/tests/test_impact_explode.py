# -*- coding: utf-8 -*-
"""PG-132 — ECO Impact pre-population from closure."""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestEcoImpactExplode(TransactionCase):

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
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.eco_impact_explode_enabled', 'True',
        )

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def _make_item(self, name, item_type='part'):
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': item_type,
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

    def _release_ebom(self, root, root_rev, child_lines):
        ebom = self.env['pg.ebom'].create({
            'root_item_id': root.id,
            'revision_id': root_rev.id,
            'ebom_line_ids': [
                (0, 0, {'pg_item_id': child.id, 'product_qty': qty})
                for child, qty in child_lines
            ],
        })
        ebom.action_submit_for_review()
        ebom.action_release()
        return ebom

    def _make_eco(self, title='Impact ECO'):
        return self.env['pg.eco'].create({
            'title': title,
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })

    def _make_line(self, eco, item):
        return self.env['pg.eco.line'].create({
            'eco_id': eco.id,
            'item_id': item.id,
            'change_kind': 'bom_change',
        })

    # ──────────────────────────────────────────────────────────────────
    # Tests
    # ──────────────────────────────────────────────────────────────────
    def test_explode_finds_no_ancestors_when_item_unused(self):
        item = self._make_item('Lonely Item')
        eco = self._make_eco('Lonely ECO')
        line = self._make_line(eco, item)
        before = len(eco.message_ids)
        result = line.action_explode_impact()
        self.assertFalse(
            result, "explode must return False when there are no candidates.",
        )
        eco.invalidate_recordset(['message_ids'])
        self.assertGreater(len(eco.message_ids), before,
                           "chatter must record the no-candidates result.")

    def test_explode_finds_direct_ancestors(self):
        asm = self._make_item('Asm', 'assembly')
        asm_rev = self._release_revision(asm, code='A')
        leaf = self._make_item('Leaf')
        self._release_revision(leaf, code='A')
        self._release_ebom(asm, asm_rev, [(leaf, 2.0)])

        eco = self._make_eco()
        line = self._make_line(eco, leaf)
        action = line.action_explode_impact()
        self.assertIsInstance(action, dict)
        self.assertEqual(action['res_model'], 'pg.eco.impact.wizard')
        candidate_cmd = action['context']['default_candidate_item_ids']
        # Format: [(6, 0, [ids])]
        candidate_ids = candidate_cmd[0][2]
        self.assertIn(asm.id, candidate_ids)

    def test_explode_excludes_items_already_on_eco(self):
        asm = self._make_item('Asm-Exc', 'assembly')
        asm_rev = self._release_revision(asm, code='A')
        leaf = self._make_item('Leaf-Exc')
        self._release_revision(leaf, code='A')
        self._release_ebom(asm, asm_rev, [(leaf, 1.0)])

        eco = self._make_eco()
        # Add asm to the ECO BEFORE exploding leaf — should be excluded.
        self._make_line(eco, asm)
        leaf_line = self._make_line(eco, leaf)
        action = leaf_line.action_explode_impact()
        # asm is the only ancestor, and already on the ECO, so candidates=[]
        # and we get False back, not a wizard.
        self.assertFalse(action,
                         "Existing items must be excluded from candidates.")

    def test_explode_blocked_when_no_item_set(self):
        eco = self._make_eco()
        # item_id is required, so create a normal line and then null it via
        # a NewId surrogate so we can exercise the "no item" guard without
        # tripping the constrains.
        line = self.env['pg.eco.line'].new({'eco_id': eco.id})
        # NewId records start with empty m2o; assert the guard fires.
        self.assertFalse(line.item_id)
        with self.assertRaises(UserError):
            line.action_explode_impact()

    def test_explode_disabled_via_icp(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'productgraph.eco_impact_explode_enabled', 'False',
        )
        try:
            item = self._make_item('ICP-off-Impact')
            eco = self._make_eco()
            line = self._make_line(eco, item)
            with self.assertRaises(UserError):
                line.action_explode_impact()
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'productgraph.eco_impact_explode_enabled', 'True',
            )

    def test_wizard_adds_selected_items_to_eco(self):
        asm = self._make_item('Asm-Add', 'assembly')
        asm_rev = self._release_revision(asm, code='A')
        leaf = self._make_item('Leaf-Add')
        self._release_revision(leaf, code='A')
        self._release_ebom(asm, asm_rev, [(leaf, 1.0)])

        eco = self._make_eco()
        leaf_line = self._make_line(eco, leaf)
        before_count = len(eco.line_ids)

        wizard = self.env['pg.eco.impact.wizard'].create({
            'eco_id': eco.id,
            'source_item_id': leaf.id,
            'source_line_id': leaf_line.id,
            'candidate_item_ids': [(6, 0, [asm.id])],
            'selected_item_ids': [(6, 0, [asm.id])],
            'default_change_kind': 'bom_change',
        })
        result = wizard.action_add_to_eco()
        self.assertEqual(result.get('type'), 'ir.actions.act_window_close')
        eco.invalidate_recordset(['line_ids'])
        self.assertEqual(
            len(eco.line_ids), before_count + 1,
            "Wizard must add exactly one new line for the selected item.",
        )
        new_line = eco.line_ids.filtered(lambda l: l.item_id == asm)
        self.assertTrue(new_line)
        self.assertEqual(new_line.change_kind, 'bom_change')
