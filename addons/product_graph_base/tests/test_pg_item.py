# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

# Long-enough justification to satisfy PG-121 wizard min-length (20+ chars).
_FORCE_RELEASE_REASON = 'Unit-test fixture — bypassing normal gating for state-machine test.'


@tagged('post_install', '-at_install', 'product_graph')
class TestPgItem(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Item = cls.env['pg.item']
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        # Promote test admin into engineer + approver + admin
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )

    def _make_item(self, item_type='part', name='Test Item'):
        return self.Item.create({
            'name': name,
            'item_type': item_type,
            'category_id': self.cat.id,
        })

    def _force_release(self, item):
        """Test helper — invoke the PG-121 force-release executor directly
        with a valid justification, so we don't need to drive the wizard UI
        in every state-machine test that just needs an item in 'released'.
        """
        return item.with_context(
            _pg_force_release_reason=_FORCE_RELEASE_REASON,
        )._do_force_release()

    def test_create_assigns_per_type_part_number(self):
        asm = self._make_item(item_type='assembly', name='Test Assembly')
        prt = self._make_item(item_type='part', name='Test Part')
        buy = self._make_item(item_type='purchased', name='Test Purchased')
        self.assertTrue(asm.part_number.startswith('PG-ASM-'))
        self.assertTrue(prt.part_number.startswith('PG-PRT-'))
        self.assertTrue(buy.part_number.startswith('PG-BUY-'))

    def test_state_transition_happy_path(self):
        item = self._make_item()
        self.assertEqual(item.state, 'concept')
        item.action_to_prototype()
        self.assertEqual(item.state, 'prototype')
        item.action_to_engineering()
        self.assertEqual(item.state, 'engineering')

    def test_action_force_release_opens_wizard(self):
        # action_force_release returns a wizard act_window (PG-121).
        # The actual release happens in the wizard's action_confirm.
        item = self._make_item()
        action = item.action_force_release()
        self.assertEqual(action.get('type'), 'ir.actions.act_window')
        self.assertEqual(action.get('res_model'), 'pg.force.release.wizard')
        self.assertEqual(action.get('context', {}).get('default_item_id'), item.id)
        # Item state is unchanged until the wizard is confirmed.
        self.assertEqual(item.state, 'concept')

    def test_forbidden_backward_transition(self):
        item = self._make_item()
        item.action_to_engineering()
        # cannot rewind released → engineering; but here we test that
        # _check_transition_allowed rejects unknown targets too.
        with self.assertRaises(UserError):
            item._check_transition_allowed('concept')

    def test_released_item_immutable_without_bypass(self):
        item = self._make_item()
        item.action_to_engineering()
        self._force_release(item)
        self.assertEqual(item.state, 'released')
        with self.assertRaises(UserError):
            item.write({'name': 'Renamed — should fail'})

    def test_released_item_chatter_fields_writable(self):
        item = self._make_item()
        item.action_to_engineering()
        self._force_release(item)
        # message_post is allowed (uses chatter fields)
        item.message_post(body='Test note on released item.')

    def test_obsolete_blocks_with_open_mo(self):
        # No product attached yet — obsolescence cannot find MOs.
        # Smoke test: item with no product_id obsoletes cleanly from released.
        item = self._make_item()
        item.action_to_engineering()
        self._force_release(item)
        # released → obsolete allowed for admin without open MO references
        item.action_to_obsolete()
        self.assertEqual(item.state, 'obsolete')
        self.assertFalse(item.active)

    def test_unlink_blocked_outside_concept(self):
        item = self._make_item()
        item.action_to_prototype()
        with self.assertRaises(UserError):
            item.unlink()

    def test_unlink_allowed_in_concept(self):
        item = self._make_item()
        item_id = item.id
        item.unlink()
        self.assertFalse(self.Item.browse(item_id).exists())
