# -*- coding: utf-8 -*-
"""PG-105 — UI button-visibility computes mirror server-side gates on pg.item."""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestItemButtonVisibility(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )

    def _make_item(self):
        return self.env['pg.item'].create({
            'name': 'Test Item',
            'item_type': 'part',
            'category_id': self.cat.id,
        })

    def test_to_service_blocked_when_not_released(self):
        item = self._make_item()
        self.assertFalse(item.can_to_service)
        self.assertIn('Released', item.to_service_blocked_reason or '')

    def test_obsolete_blocked_when_not_released_or_service(self):
        item = self._make_item()
        self.assertFalse(item.can_to_obsolete)
        self.assertIn('Released', item.to_obsolete_blocked_reason or '')

    def test_to_service_unblocked_when_released_no_product(self):
        # Force-release an item (admin path that bypasses revision flow).
        item = self._make_item()
        item.action_to_prototype()
        item.action_to_engineering()
        item.with_context(
            _pg_force_release_reason='PG-105 button-visibility fixture — bypassing gates for test setup.'
        )._do_force_release()
        self.assertEqual(item.state, 'released')
        # No linked product means no open MOs by construction.
        self.assertTrue(item.can_to_service)
        self.assertFalse(item.to_service_blocked_reason)

    def test_obsolete_unblocked_when_released_no_product(self):
        item = self._make_item()
        item.action_to_prototype()
        item.action_to_engineering()
        item.with_context(
            _pg_force_release_reason='PG-105 button-visibility fixture — bypassing gates for test setup.'
        )._do_force_release()
        self.assertTrue(item.can_to_obsolete)
        self.assertFalse(item.to_obsolete_blocked_reason)

    def test_server_guard_still_fires_when_not_released(self):
        item = self._make_item()
        with self.assertRaises(UserError):
            item.action_to_service()
        with self.assertRaises(UserError):
            item.action_to_obsolete()
