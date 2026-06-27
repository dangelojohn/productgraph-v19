# -*- coding: utf-8 -*-
"""PG-130 — ECO auto-close on all governed revisions released.
PG-162 — Foreground bus notification on auto-close."""
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestEcoAutoClose(TransactionCase):

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
        # PG-120 SoD off — single test user walks both submit and approve.
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.enforce_sod', 'False',
        )
        # Default the auto-close ICP to True (seed value); each test that
        # toggles it restores in tearDown.
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.auto_close_eco_on_all_revisions_released', 'True',
        )

    # ──────────────────────────────────────────────────────────────────
    # Helpers (mirror test_pg_eco_state_machine helpers)
    # ──────────────────────────────────────────────────────────────────
    def _make_item(self, name='Auto-Close Test Item'):
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    def _make_revision(self, item, code='A'):
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'Initial release of the test item.',
        })

    def _release_revision(self, item, code='A'):
        rev = self._make_revision(item, code=code)
        rev.action_submit_for_review()
        rev.action_release()
        return rev

    def _eco_to_implementation(self, items_and_revs):
        """Build an ECO with one line per (item, from_rev, to_rev) tuple and
        drive it to `implementation`. Returns (eco, lines)."""
        eco = self.env['pg.eco'].create({
            'title': 'Auto-Close ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })
        lines = self.env['pg.eco.line']
        for item, from_rev, to_rev in items_and_revs:
            lines |= self.env['pg.eco.line'].create({
                'eco_id': eco.id,
                'item_id': item.id,
                'from_revision_id': from_rev.id,
                'to_revision_id': to_rev.id,
                'change_kind': 'revision_bump',
            })
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='proceed')
        self.assertEqual(eco.state, 'implementation')
        return eco, lines

    # ──────────────────────────────────────────────────────────────────
    # Tests
    # ──────────────────────────────────────────────────────────────────
    def test_eco_auto_closes_when_all_lines_released(self):
        item1 = self._make_item('Auto-Close Item 1')
        rev1_a = self._release_revision(item1, code='A')
        rev1_b = self._make_revision(item1, code='B')
        item2 = self._make_item('Auto-Close Item 2')
        rev2_a = self._release_revision(item2, code='A')
        rev2_b = self._make_revision(item2, code='B')

        eco, lines = self._eco_to_implementation([
            (item1, rev1_a, rev1_b),
            (item2, rev2_a, rev2_b),
        ])

        # Release revision 1 — ECO must still be in implementation.
        rev1_b.action_submit_for_review()
        rev1_b.action_release()
        self.assertEqual(eco.state, 'implementation',
                         "ECO must not close until ALL lines are released.")

        # Release revision 2 — ECO must auto-close.
        rev2_b.action_submit_for_review()
        rev2_b.action_release()
        self.assertEqual(eco.state, 'released')
        self.assertTrue(eco.actual_release_date,
                        "actual_release_date must be stamped on auto-close.")

    def test_eco_not_closed_when_one_line_pending(self):
        item1 = self._make_item('Pending Item 1')
        rev1_a = self._release_revision(item1, code='A')
        rev1_b = self._make_revision(item1, code='B')
        item2 = self._make_item('Pending Item 2')
        rev2_a = self._release_revision(item2, code='A')
        rev2_b = self._make_revision(item2, code='B')

        eco, _lines = self._eco_to_implementation([
            (item1, rev1_a, rev1_b),
            (item2, rev2_a, rev2_b),
        ])
        rev1_b.action_submit_for_review()
        rev1_b.action_release()
        # rev2_b never released — ECO stays in implementation.
        self.assertEqual(eco.state, 'implementation')
        self.assertFalse(eco.actual_release_date)

    def test_eco_auto_close_ignores_when_icp_disabled(self):
        # Disable the ICP for this test only.
        self.env['ir.config_parameter'].sudo().set_param(
            'productgraph.auto_close_eco_on_all_revisions_released', 'False',
        )
        try:
            item = self._make_item('ICP-off Item')
            rev_a = self._release_revision(item, code='A')
            rev_b = self._make_revision(item, code='B')
            eco, _lines = self._eco_to_implementation([(item, rev_a, rev_b)])
            rev_b.action_submit_for_review()
            rev_b.action_release()
            self.assertEqual(eco.state, 'implementation',
                             "ICP=False must short-circuit auto-close.")
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'productgraph.auto_close_eco_on_all_revisions_released', 'True',
            )

    def test_eco_auto_close_skips_non_implementation_state(self):
        # Create an ECO that stays in `analysis`; revision release MUST NOT
        # short-circuit it to released.
        item = self._make_item('Analysis-state Item')
        rev_a = self._release_revision(item, code='A')
        rev_b = self._make_revision(item, code='B')
        eco = self.env['pg.eco'].create({
            'title': 'Analysis-state ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })
        self.env['pg.eco.line'].create({
            'eco_id': eco.id,
            'item_id': item.id,
            'from_revision_id': rev_a.id,
            'to_revision_id': rev_b.id,
            'change_kind': 'revision_bump',
        })
        eco.action_start_analysis()
        self.assertEqual(eco.state, 'analysis')
        # Now release the target rev — auto-close must skip because ECO is
        # not in implementation.
        rev_b.action_submit_for_review()
        rev_b.action_release()
        self.assertEqual(eco.state, 'analysis')

    # ──────────────────────────────────────────────────────────────────
    # PG-162 — Foreground bus notification on auto-close
    # ──────────────────────────────────────────────────────────────────
    def test_auto_close_sends_bus_notification(self):
        """When auto-close fires, a 'simple_notification' bus message must be
        sent to the triggering user's partner."""
        item = self._make_item('Bus-Notify Item')
        rev_a = self._release_revision(item, code='A')
        rev_b = self._make_revision(item, code='B')
        eco, _lines = self._eco_to_implementation([(item, rev_a, rev_b)])

        captured = []
        BusBus = type(self.env['bus.bus'])
        orig_sendone = BusBus._sendone

        def _capture(self_bus, channel, notif_type, payload):
            captured.append((channel, notif_type, payload))
            return orig_sendone(self_bus, channel, notif_type, payload)

        with patch.object(BusBus, '_sendone', _capture):
            rev_b.action_submit_for_review()
            rev_b.action_release()

        self.assertEqual(eco.state, 'released',
                         "ECO must have auto-closed to trigger the notification.")
        toast_calls = [
            c for c in captured if c[1] == 'simple_notification'
            and isinstance(c[2], dict)
            and c[2].get('title') == 'ECO Closed'
        ]
        self.assertTrue(
            toast_calls,
            "Auto-close must emit a 'simple_notification' bus message.",
        )
        channel, _ntype, payload = toast_calls[0]
        self.assertEqual(payload.get('type'), 'success')
        self.assertEqual(payload.get('sticky'), False)
        self.assertIn(eco.display_name, payload.get('message', ''))
        # Channel must be the triggering user's partner.
        self.assertEqual(channel, self.test_user.partner_id)

    def test_auto_close_notification_skipped_when_icp_off(self):
        """ICP `productgraph.notify_on_auto_advance=False` must silence the
        toast (and the follow-up activity)."""
        self.env['ir.config_parameter'].sudo().set_param(
            'productgraph.notify_on_auto_advance', 'False',
        )
        try:
            item = self._make_item('No-Notify Item')
            rev_a = self._release_revision(item, code='A')
            rev_b = self._make_revision(item, code='B')
            eco, _lines = self._eco_to_implementation([(item, rev_a, rev_b)])

            captured = []
            BusBus = type(self.env['bus.bus'])
            orig_sendone = BusBus._sendone

            def _capture(self_bus, channel, notif_type, payload):
                captured.append((channel, notif_type, payload))
                return orig_sendone(self_bus, channel, notif_type, payload)

            with patch.object(BusBus, '_sendone', _capture):
                rev_b.action_submit_for_review()
                rev_b.action_release()

            self.assertEqual(eco.state, 'released',
                             "ICP gates only the notification, not the close.")
            toast_calls = [
                c for c in captured if c[1] == 'simple_notification'
                and isinstance(c[2], dict)
                and c[2].get('title') == 'ECO Closed'
            ]
            self.assertFalse(
                toast_calls,
                "ICP=False must suppress the auto-close bus notification.",
            )
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'productgraph.notify_on_auto_advance', 'True',
            )

    def test_eco_auto_close_skips_when_only_introduce_lines(self):
        # An ECO whose only line is an `introduce` change has no governed
        # to_revision — auto-close must NOT fire (else it would close
        # immediately on creation of the next unrelated revision).
        item_other = self._make_item('Other Item')
        rev_other_a = self._release_revision(item_other, code='A')
        rev_other_b = self._make_revision(item_other, code='B')

        item_new = self._make_item('New Item')

        eco = self.env['pg.eco'].create({
            'title': 'Introduce-only ECO',
            'change_type': 'adaptive',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })
        self.env['pg.eco.line'].create({
            'eco_id': eco.id,
            'item_id': item_new.id,
            'change_kind': 'introduce',
        })
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='proceed')
        # Release an unrelated revision — must not affect this ECO.
        rev_other_b.action_submit_for_review()
        rev_other_b.action_release()
        self.assertEqual(eco.state, 'implementation')
