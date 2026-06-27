# -*- coding: utf-8 -*-
"""PG-144 — Apply Property Template wizard."""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestApplyTemplateWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        cls.item_part = cls.env['pg.item'].create({
            'name': 'Apply Template Item',
            'item_type': 'part',
            'category_id': cls.cat.id,
        })

        # Two templates of matching type, one of non-matching type.
        cls.tmpl_part_a = cls.env['pg.property.template'].create({
            'name': 'Wizard Test Part A',
            'code': 'WIZ_TEST_PART_A',
            'item_type': 'part',
        })
        cls.field_a1 = cls.env['pg.property.field'].create({
            'template_id': cls.tmpl_part_a.id,
            'key': 'wiz_a1', 'label': 'Wiz A1', 'field_type': 'char',
        })
        cls.field_a2 = cls.env['pg.property.field'].create({
            'template_id': cls.tmpl_part_a.id,
            'key': 'wiz_a2', 'label': 'Wiz A2', 'field_type': 'integer',
        })
        cls.tmpl_part_b = cls.env['pg.property.template'].create({
            'name': 'Wizard Test Part B',
            'code': 'WIZ_TEST_PART_B',
            'item_type': 'part',
        })
        cls.tmpl_other = cls.env['pg.property.template'].create({
            'name': 'Wizard Test Assembly',
            'code': 'WIZ_TEST_ASSY',
            'item_type': 'assembly',
        })

    def _open_wizard(self, item, revision=None):
        ctx = {'default_item_id': item.id}
        if revision is not None:
            ctx['default_revision_id'] = revision.id
        return self.env['pg.apply.template.wizard'].with_context(**ctx).create({})

    # ──────────────────────────────────────────────────────────────────
    def test_apply_template_links_to_item(self):
        wiz = self._open_wizard(self.item_part)
        wiz.template_ids = [(6, 0, [self.tmpl_part_a.id])]
        result = wiz.action_apply()
        self.assertEqual(result.get('type'), 'ir.actions.act_window_close')
        self.assertIn(self.tmpl_part_a, self.item_part.property_template_ids)

    def test_apply_template_creates_values_on_revision(self):
        revision = self.env['pg.revision'].create({
            'item_id': self.item_part.id,
            'name': 'A',
            'change_summary': 'Initial release of the wizard test item.',
        })
        wiz = self._open_wizard(self.item_part, revision=revision)
        wiz.template_ids = [(6, 0, [self.tmpl_part_a.id])]
        wiz.action_apply()
        field_ids_with_values = revision.property_value_ids.mapped('field_id')
        self.assertIn(self.field_a1, field_ids_with_values)
        self.assertIn(self.field_a2, field_ids_with_values)
        # Empty value: no typed slot populated yet.
        for v in revision.property_value_ids:
            self.assertFalse(v.value_char)
            self.assertFalse(v.value_integer)

    def test_apply_template_idempotent(self):
        revision = self.env['pg.revision'].create({
            'item_id': self.item_part.id,
            'name': 'A',
            'change_summary': 'Initial release of the wizard test item.',
        })
        # First apply.
        wiz1 = self._open_wizard(self.item_part, revision=revision)
        wiz1.template_ids = [(6, 0, [self.tmpl_part_a.id])]
        wiz1.action_apply()
        n_after_first = len(revision.property_value_ids)
        # Pre-populate one of the values so we can verify idempotency
        # doesn't clobber it.
        v1 = revision.property_value_ids.filtered(
            lambda v: v.field_id == self.field_a1
        )
        v1.value_char = 'hand-set'
        # Second apply with the same template — must NOT create duplicates
        # nor wipe the value we just set.
        wiz2 = self._open_wizard(self.item_part, revision=revision)
        wiz2.template_ids = [(6, 0, [self.tmpl_part_a.id])]
        wiz2.action_apply()
        self.assertEqual(len(revision.property_value_ids), n_after_first)
        v1_reloaded = revision.property_value_ids.filtered(
            lambda v: v.field_id == self.field_a1
        )
        self.assertEqual(v1_reloaded.value_char, 'hand-set')

    def test_suggested_filters_by_item_type(self):
        wiz = self._open_wizard(self.item_part)
        suggested = wiz.suggested_template_ids
        self.assertIn(self.tmpl_part_a, suggested)
        self.assertIn(self.tmpl_part_b, suggested)
        # Non-matching item_type must NOT appear.
        self.assertNotIn(self.tmpl_other, suggested)

    def test_suggested_excludes_already_linked(self):
        # Pre-link tmpl_part_a; suggested should drop it.
        self.item_part.write({
            'property_template_ids': [(4, self.tmpl_part_a.id)],
        })
        wiz = self._open_wizard(self.item_part)
        self.assertNotIn(self.tmpl_part_a, wiz.suggested_template_ids)
        self.assertIn(self.tmpl_part_b, wiz.suggested_template_ids)

    def test_suggested_filters_inactive_templates(self):
        self.tmpl_part_b.active = False
        wiz = self._open_wizard(self.item_part)
        self.assertNotIn(self.tmpl_part_b, wiz.suggested_template_ids)

    def test_apply_empty_selection_is_noop(self):
        wiz = self._open_wizard(self.item_part)
        # No template_ids set; action_apply returns close, makes no change.
        before = set(self.item_part.property_template_ids.ids)
        wiz.action_apply()
        after = set(self.item_part.property_template_ids.ids)
        self.assertEqual(before, after)

    def test_action_open_wizard_from_item(self):
        action = self.item_part.action_open_apply_template_wizard()
        self.assertEqual(action['res_model'], 'pg.apply.template.wizard')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action['context'].get('default_item_id'),
                         self.item_part.id)
        self.assertNotIn('default_revision_id', action['context'])

    def test_action_open_wizard_from_revision(self):
        revision = self.env['pg.revision'].create({
            'item_id': self.item_part.id,
            'name': 'A',
            'change_summary': 'Initial release of the wizard test item.',
        })
        action = revision.action_open_apply_template_wizard()
        self.assertEqual(action['res_model'], 'pg.apply.template.wizard')
        self.assertEqual(action['context'].get('default_item_id'),
                         self.item_part.id)
        self.assertEqual(action['context'].get('default_revision_id'),
                         revision.id)
