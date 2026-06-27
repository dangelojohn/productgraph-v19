# -*- coding: utf-8 -*-
"""PG-140 — One-click Promote for pg.item.

Covers:
- The opinionated state→method PROMOTION_MAP routing.
- That action_promote reuses (and respects) the existing transition guards.
- The new can_promote / promote_blocked_reason compute fields.
- The PG-140 follow-up: _auto_release_from_revision honors the
  productgraph.auto_advance_item_on_revision_release ICP.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


_FORCE_RELEASE_REASON = (
    'Unit-test fixture — bypassing normal gating for state-machine test.'
)


@tagged('post_install', '-at_install', 'product_graph', 'product_graph_promote')
class TestPgItemPromote(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Item = cls.env['pg.item']
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )

    def _make_item(self, name='Promote Test Item'):
        return self.Item.create({
            'name': name,
            'item_type': 'part',
            'category_id': self.cat.id,
        })

    def _force_release(self, item):
        return item.with_context(
            _pg_force_release_reason=_FORCE_RELEASE_REASON,
        )._do_force_release()

    # ── Routing ────────────────────────────────────────────────────────────

    def test_item_promote_concept_to_prototype(self):
        item = self._make_item()
        self.assertEqual(item.state, 'concept')
        item.action_promote()
        self.assertEqual(item.state, 'prototype')

    def test_item_promote_prototype_to_engineering(self):
        item = self._make_item()
        item.action_promote()  # concept -> prototype
        item.action_promote()  # prototype -> engineering
        self.assertEqual(item.state, 'engineering')

    def test_item_promote_engineering_blocked(self):
        item = self._make_item()
        item.action_to_prototype()
        item.action_to_engineering()
        with self.assertRaises(UserError) as cm:
            item.action_promote()
        self.assertIn('revision', str(cm.exception).lower())

    def test_item_promote_obsolete_blocked(self):
        item = self._make_item()
        item.action_to_prototype()
        item.action_to_engineering()
        self._force_release(item)
        item.action_to_service()
        item.action_to_obsolete()
        with self.assertRaises(UserError):
            item.action_promote()

    # ── Compute parity with transition guards ──────────────────────────────

    def test_item_can_promote_compute_concept(self):
        item = self._make_item()
        self.assertTrue(item.can_promote)
        self.assertFalse(item.promote_blocked_reason)

    def test_item_can_promote_compute_engineering_blocked(self):
        item = self._make_item()
        item.action_to_prototype()
        item.action_to_engineering()
        self.assertFalse(item.can_promote)
        self.assertIn('Revision', item.promote_blocked_reason or '')

    def test_item_promote_honors_to_service_precondition(self):
        """When can_to_service is False (e.g. open MOs) the promote button
        from 'released' must also report blocked, mirroring the underlying
        guard rather than introducing parallel logic."""
        item = self._make_item()
        item.action_to_prototype()
        item.action_to_engineering()
        self._force_release(item)
        self.assertEqual(item.state, 'released')
        # No linked product => no open MOs => can_to_service is True
        # => can_promote follows can_to_service.
        self.assertEqual(item.can_promote, item.can_to_service)
        if not item.can_to_service:
            self.assertEqual(
                item.promote_blocked_reason,
                item.to_service_blocked_reason,
            )

    # ── PG-130+131 follow-up: ICP gates auto-advance ──────────────────────

    def test_auto_advance_item_icp_off(self):
        """When productgraph.auto_advance_item_on_revision_release=False,
        releasing the first revision must NOT auto-promote the parent item
        from engineering to released."""
        # Build an engineering item with a draft revision.
        item = self._make_item(name='ICP Gated Item')
        item.action_to_prototype()
        item.action_to_engineering()
        Revision = self.env.get('pg.revision')
        if Revision is None:
            self.skipTest('product_graph_revision not installed.')
        rev = Revision.create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'First revision — ICP gating test fixture.',
        })
        # Flip ICP off, then release the revision through the standard flow.
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param(
            'productgraph.auto_advance_item_on_revision_release', 'False',
        )
        try:
            rev.action_submit_for_review()
            # Need a different user for SoD or disable SoD for this test.
            ICP.set_param('productgraph.enforce_sod', 'False')
            try:
                rev.action_release()
            finally:
                ICP.set_param('productgraph.enforce_sod', 'True')
            self.assertEqual(rev.state, 'released')
            # The crux: item must stay in 'engineering' because ICP was off.
            self.assertEqual(item.state, 'engineering')
        finally:
            ICP.set_param(
                'productgraph.auto_advance_item_on_revision_release', 'True',
            )

    def test_auto_advance_item_icp_on_default(self):
        """Default behavior (ICP unset => 'True') still auto-promotes."""
        item = self._make_item(name='ICP Default Item')
        item.action_to_prototype()
        item.action_to_engineering()
        Revision = self.env.get('pg.revision')
        if Revision is None:
            self.skipTest('product_graph_revision not installed.')
        rev = Revision.create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'First revision — default ICP fixture.',
        })
        ICP = self.env['ir.config_parameter'].sudo()
        # Ensure default in case earlier tests left it flipped.
        ICP.set_param(
            'productgraph.auto_advance_item_on_revision_release', 'True',
        )
        rev.action_submit_for_review()
        ICP.set_param('productgraph.enforce_sod', 'False')
        try:
            rev.action_release()
        finally:
            ICP.set_param('productgraph.enforce_sod', 'True')
        self.assertEqual(rev.state, 'released')
        self.assertEqual(item.state, 'released')
