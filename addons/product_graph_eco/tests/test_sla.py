# -*- coding: utf-8 -*-
"""PG-134 — Approval routing, SLAs, escalation."""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestEcoSla(TransactionCase):

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
            'productgraph.eco_route_on_submit', 'True',
        )
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.eco_sla_enabled', 'True',
        )

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def _make_item(self, name='SLA Test Item'):
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    def _release_revision(self, item, code='A'):
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'Initial release.',
        })
        rev.action_submit_for_review()
        rev.action_release()
        return rev

    def _make_revision(self, item, code='B'):
        return self.env['pg.revision'].create({
            'item_id': item.id,
            'name': code,
            'change_summary': 'Next rev for SLA test.',
        })

    def _make_eco(self, title='SLA ECO', target_release_date=None,
                  engineering_lead_id=None):
        vals = {
            'title': title,
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        }
        if target_release_date is not None:
            vals['target_release_date'] = target_release_date
        if engineering_lead_id is not None:
            vals['engineering_lead_id'] = engineering_lead_id
        return self.env['pg.eco'].create(vals)

    def _eco_to_approval(self, eco, item):
        rev_a = self._release_revision(item, code='A')
        rev_b = self._make_revision(item, code='B')
        self.env['pg.eco.line'].create({
            'eco_id': eco.id,
            'item_id': item.id,
            'from_revision_id': rev_a.id,
            'to_revision_id': rev_b.id,
            'change_kind': 'revision_bump',
        })
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        return eco

    # ──────────────────────────────────────────────────────────────────
    # Routing tests
    # ──────────────────────────────────────────────────────────────────
    def test_route_on_submit_schedules_activity(self):
        item = self._make_item('Route-Item')
        eco = self._make_eco(
            title='Route ECO',
            engineering_lead_id=self.test_user.id,
        )
        self._eco_to_approval(eco, item)
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'pg.eco'),
            ('res_id', '=', eco.id),
            ('user_id', '=', self.test_user.id),
        ])
        self.assertTrue(
            activities,
            "Submission must schedule a mail.activity on the engineering lead.",
        )

    def test_route_disabled_via_icp(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'productgraph.eco_route_on_submit', 'False',
        )
        try:
            item = self._make_item('Route-Off-Item')
            eco = self._make_eco(
                title='Route-Off ECO',
                engineering_lead_id=self.test_user.id,
            )
            self._eco_to_approval(eco, item)
            activities = self.env['mail.activity'].search([
                ('res_model', '=', 'pg.eco'),
                ('res_id', '=', eco.id),
            ])
            self.assertFalse(
                activities,
                "ICP=False must short-circuit routing — no activity expected.",
            )
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'productgraph.eco_route_on_submit', 'True',
            )

    # ──────────────────────────────────────────────────────────────────
    # Cron tests
    # ──────────────────────────────────────────────────────────────────
    def test_sla_reminder_within_3_days(self):
        item = self._make_item('Reminder-Item')
        eco = self._make_eco(
            title='Reminder ECO',
            target_release_date=fields.Date.today() + timedelta(days=1),
        )
        self._eco_to_approval(eco, item)
        before = len(eco.message_ids)
        self.env['pg.eco']._cron_sla_reminders()
        eco.invalidate_recordset(['message_ids'])
        self.assertGreater(
            len(eco.message_ids), before,
            "SLA cron must post a reminder for ECO within 3 days of target.",
        )

    def test_sla_no_reminder_outside_window(self):
        item = self._make_item('OutOfWindow-Item')
        eco = self._make_eco(
            title='OutOfWindow ECO',
            target_release_date=fields.Date.today() + timedelta(days=10),
        )
        self._eco_to_approval(eco, item)
        before_bodies = set(eco.message_ids.ids)
        self.env['pg.eco']._cron_sla_reminders()
        eco.invalidate_recordset(['message_ids'])
        new_msgs = eco.message_ids.filtered(
            lambda m: m.id not in before_bodies
        )
        sla_marker_msgs = new_msgs.filtered(
            lambda m: m.body and '[pg-eco-sla-reminder]' in m.body
        )
        self.assertFalse(
            sla_marker_msgs,
            "Target outside 3-day window must NOT trigger reminder.",
        )

    def test_sla_no_duplicate_reminder_within_24h(self):
        item = self._make_item('Dedup-Item')
        eco = self._make_eco(
            title='Dedup ECO',
            target_release_date=fields.Date.today() + timedelta(days=2),
        )
        self._eco_to_approval(eco, item)
        self.env['pg.eco']._cron_sla_reminders()
        eco.invalidate_recordset(['message_ids'])
        first_count = len(eco.message_ids.filtered(
            lambda m: m.body and '[pg-eco-sla-reminder]' in m.body
        ))
        # Run again — should NOT add a second reminder.
        self.env['pg.eco']._cron_sla_reminders()
        eco.invalidate_recordset(['message_ids'])
        second_count = len(eco.message_ids.filtered(
            lambda m: m.body and '[pg-eco-sla-reminder]' in m.body
        ))
        self.assertEqual(
            first_count, second_count,
            "Second cron pass within 24h must NOT double-post the reminder.",
        )

    def test_escalation_on_overdue(self):
        item = self._make_item('Overdue-Item')
        eco = self._make_eco(
            title='Overdue ECO',
            target_release_date=fields.Date.today() - timedelta(days=1),
        )
        self._eco_to_approval(eco, item)
        self.env['pg.eco']._cron_sla_reminders()
        eco.invalidate_recordset(
            ['escalation_level', 'escalation_stamped_at', 'message_ids'],
        )
        self.assertEqual(
            eco.escalation_level, 1,
            "Overdue ECO must escalate to level 1 on first cron pass.",
        )
        self.assertTrue(eco.escalation_stamped_at)
        escalation_msgs = eco.message_ids.filtered(
            lambda m: m.body and '[pg-eco-sla-escalation]' in m.body
        )
        self.assertTrue(
            escalation_msgs, "Escalation must leave a chatter note.",
        )

    def test_escalation_stops_at_level_2(self):
        item = self._make_item('Capped-Item')
        eco = self._make_eco(
            title='Capped ECO',
            target_release_date=fields.Date.today() - timedelta(days=5),
        )
        self._eco_to_approval(eco, item)
        # Force the level to 2 first.
        eco.write({'escalation_level': 2})
        self.env['pg.eco']._cron_sla_reminders()
        eco.invalidate_recordset(['escalation_level'])
        self.assertEqual(
            eco.escalation_level, 2,
            "Escalation level must cap at 2; cron must NOT exceed it.",
        )
