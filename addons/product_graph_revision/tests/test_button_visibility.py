# -*- coding: utf-8 -*-
"""PG-105 — UI button-visibility computes mirror server-side gates."""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestRevisionButtonVisibility(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disable SoD by default; tests that exercise SoD opt back in.
        cls.env['ir.config_parameter'].sudo().set_param('productgraph.enforce_sod', 'False')
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )

    def _make_item(self, item_type='part', advance_to='engineering'):
        item = self.env['pg.item'].create({
            'name': 'Test Item',
            'item_type': item_type,
            'category_id': self.cat.id,
        })
        if advance_to in ('prototype', 'engineering'):
            item.action_to_prototype()
        if advance_to == 'engineering':
            item.action_to_engineering()
        return item

    def _make_revision(self, item, code='A', summary='Initial release of the test item.'):
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': summary,
        })

    # ── Submit gate ────────────────────────────────────────────────────────

    def test_submit_blocked_when_item_concept(self):
        item = self.env['pg.item'].create({
            'name': 'Concept Item',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        rev = self._make_revision(item)
        self.assertFalse(rev.can_submit_for_review)
        self.assertIn('Concept', rev.submit_blocked_reason or '')

    def test_submit_unblocked_when_item_engineering(self):
        item = self._make_item(advance_to='engineering')
        rev = self._make_revision(item)
        self.assertTrue(rev.can_submit_for_review)
        self.assertFalse(rev.submit_blocked_reason)

    def test_submit_blocked_short_change_summary(self):
        item = self._make_item(advance_to='engineering')
        rev = self._make_revision(item, summary='TBD')
        self.assertFalse(rev.can_submit_for_review)
        self.assertIn('10 characters', rev.submit_blocked_reason or '')

    # ── Release gate ───────────────────────────────────────────────────────

    def test_release_blocked_when_item_prototype(self):
        # Construct a revision in 'review' state on a prototype-stage item.
        # State machine forbids moving an item backwards, so we build the
        # scenario directly via create() with state='review'. 'review' is not
        # in the immutable-state set so no bypass is needed.
        item = self._make_item(advance_to='prototype')
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'long enough change summary text',
            'state': 'review',
        })
        self.assertFalse(rev.can_release)
        self.assertIn('Engineering', rev.release_blocked_reason or '')

    def test_release_unblocked_after_item_engineering(self):
        item = self._make_item(advance_to='engineering')
        rev = self._make_revision(item)
        rev.action_submit_for_review()
        self.assertEqual(rev.state, 'review')
        self.assertTrue(rev.can_release)
        self.assertFalse(rev.release_blocked_reason)

    def test_release_blocked_when_incompat_no_reason(self):
        item = self._make_item(advance_to='engineering')
        rev = self._make_revision(item)
        rev.write({'incompatible_with_previous': True})
        rev.action_submit_for_review()
        self.assertFalse(rev.can_release)
        self.assertIn('Incompatibility', rev.release_blocked_reason or '')

    # ── Server gate parity ─────────────────────────────────────────────────

    def test_server_guard_still_fires_if_called_directly(self):
        """Confirm the server-side check still rejects even when UI would hide
        the button — defense-in-depth contract."""
        item = self._make_item(advance_to='prototype')
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'long enough change summary text',
            'state': 'review',
        })
        # UI says no
        self.assertFalse(rev.can_release)
        # And so does the server when called directly
        with self.assertRaises(UserError):
            rev.action_release()
