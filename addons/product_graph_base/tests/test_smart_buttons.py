# -*- coding: utf-8 -*-
"""
PG-160 — Smart-button shortcut behavior.

When a pg.item has exactly one related revision (or one EBOM), the smart
button action returns a form view targeting that single record instead of
the list view — saves the user a click.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestSmartButtonShortcuts(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

    def _make_item(self, name='Smart Button Test'):
        return self.env['pg.item'].create({
            'name': name,
            'item_type': 'part',
            'category_id': self.cat.id,
        })

    def _make_revision(self, item, code='A'):
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'seed revision for smart-button test',
        })

    def _make_ebom(self, item, name, revision=None):
        rev = revision or self._make_revision(item, code=name)
        return self.env['pg.ebom'].create({
            'root_item_id': item.id,
            'revision_id': rev.id,
            'name': name,
        })

    # ──────────────────────────────────────────────────────────────────
    # Revisions
    # ──────────────────────────────────────────────────────────────────
    def test_revisions_button_lands_on_form_when_one(self):
        item = self._make_item('One-Rev Item')
        rev = self._make_revision(item, code='A')
        action = item.action_view_revisions()
        self.assertEqual(action.get('res_model'), 'pg.revision')
        self.assertEqual(action.get('res_id'), rev.id,
                         "Single-revision shortcut must target that revision.")
        self.assertEqual(action.get('view_mode'), 'form',
                         "Single-revision shortcut must land on the form view.")
        # Should NOT have a multi-record domain payload.
        self.assertNotIn('domain', action,
                         "Form-shortcut action should not carry a list domain.")

    def test_revisions_button_lands_on_list_when_many(self):
        item = self._make_item('Multi-Rev Item')
        for code in ('A', 'B', 'C'):
            self._make_revision(item, code=code)
        action = item.action_view_revisions()
        self.assertEqual(action.get('res_model'), 'pg.revision')
        self.assertEqual(action.get('view_mode'), 'list,form',
                         "Multiple-revision case must keep the list view.")
        self.assertNotIn('res_id', action,
                         "List-view action must not pre-select a single record.")
        self.assertIn(('item_id', '=', item.id), action.get('domain', []))

    def test_revisions_button_zero_revisions(self):
        # No revisions yet — the action must still resolve (returns the
        # filtered list, which will simply render empty).
        item = self._make_item('No-Rev Item')
        action = item.action_view_revisions()
        self.assertEqual(action.get('res_model'), 'pg.revision')
        self.assertEqual(action.get('view_mode'), 'list,form')
        self.assertNotIn('res_id', action)

    # ──────────────────────────────────────────────────────────────────
    # EBOMs
    # ──────────────────────────────────────────────────────────────────
    def test_eboms_button_lands_on_form_when_one(self):
        item = self._make_item('One-Ebom Item')
        ebom = self._make_ebom(item, name='EBOM-A')
        action = item.action_view_eboms()
        self.assertEqual(action.get('res_model'), 'pg.ebom')
        self.assertEqual(action.get('res_id'), ebom.id)
        self.assertEqual(action.get('view_mode'), 'form')

    def test_eboms_button_lands_on_list_when_many(self):
        item = self._make_item('Multi-Ebom Item')
        # Each EBOM needs a distinct revision (UNIQUE(root_item, revision)).
        self._make_ebom(item, name='EBOM-A')
        self._make_ebom(item, name='EBOM-B')
        action = item.action_view_eboms()
        self.assertEqual(action.get('view_mode'), 'list,form')
        self.assertNotIn('res_id', action)
