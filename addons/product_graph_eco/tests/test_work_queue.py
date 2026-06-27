# -*- coding: utf-8 -*-
"""PG-143 — Role-aware Work Queue Landing.

Verifies the four window actions surface the correct slice of work for the
current user. Each test evaluates the action's domain (via safe_eval, with
the same `uid` / `context_today` bindings Odoo uses) and runs it through
search() against fixture records.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install', 'product_graph')
class TestWorkQueue(TransactionCase):

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
        cls.other_user = cls.env['res.users'].create({
            'name': 'Other Engineer',
            'login': 'pg_other_engineer_wq',
            'group_ids': [(6, 0, [
                cls.env.ref('product_graph_base.group_pg_engineer').id,
                cls.env.ref('product_graph_base.group_pg_approver').id,
            ])],
        })
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        # SoD off — we're testing queue filters, not the SoD guard itself.
        cls.env['ir.config_parameter'].sudo().set_param(
            'productgraph.enforce_sod', 'False',
        )

    def _eval_domain(self, action_ref):
        action = self.env.ref(action_ref)
        # Mirror Odoo's domain context: uid + context_today() callable.
        eval_ctx = {
            'uid': self.env.user.id,
            'context_today': lambda: fields.Date.from_string(
                fields.Date.context_today(self.env['res.partner'])
            ),
        }
        return safe_eval(action.domain, eval_ctx)

    def _make_item(self):
        item = self.env['pg.item'].create({
            'name': 'WQ Item',
            'item_type': 'part',
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        return item

    # ──────────────────────────────────────────────────────────────────
    # action_pg_my_open_ecos
    # ──────────────────────────────────────────────────────────────────
    def test_my_open_ecos_filters_correctly(self):
        # ECO requested by current user — should appear.
        mine = self.env['pg.eco'].create({
            'title': 'Mine ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'requested_by_id': self.env.user.id,
        })
        # ECO requested by another user — should NOT appear.
        not_mine = self.env['pg.eco'].create({
            'title': 'Not mine ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'requested_by_id': self.other_user.id,
            'requestor_id': self.other_user.id,
        })
        domain = self._eval_domain('product_graph_eco.action_pg_my_open_ecos')
        results = self.env['pg.eco'].search(domain)
        self.assertIn(mine, results)
        self.assertNotIn(not_mine, results)

    def test_my_open_ecos_includes_engineering_lead(self):
        # ECO where I'm the engineering lead but not the requester.
        eco = self.env['pg.eco'].create({
            'title': 'Lead ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'requested_by_id': self.other_user.id,
            'requestor_id': self.other_user.id,
            'engineering_lead_id': self.env.user.id,
        })
        domain = self._eval_domain('product_graph_eco.action_pg_my_open_ecos')
        self.assertIn(eco, self.env['pg.eco'].search(domain))

    # ──────────────────────────────────────────────────────────────────
    # action_pg_revisions_awaiting_me
    # ──────────────────────────────────────────────────────────────────
    def test_revisions_awaiting_me_excludes_my_own(self):
        item = self._make_item()
        # Submitted by current user — should NOT appear (no self-approval).
        rev_self = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'Initial release of the WQ test item.',
        })
        rev_self.action_submit_for_review()
        # Submitted by another user — should appear.
        item2 = self._make_item()
        rev_other = self.env['pg.revision'].with_user(self.other_user).create({
            'item_id': item2.id,
            'name': 'A',
            'change_summary': 'Initial release of the WQ test item (other).',
        })
        rev_other.with_user(self.other_user).action_submit_for_review()
        domain = self._eval_domain(
            'product_graph_eco.action_pg_revisions_awaiting_me'
        )
        results = self.env['pg.revision'].search(domain)
        self.assertNotIn(rev_self, results)
        self.assertIn(rev_other, results)

    # ──────────────────────────────────────────────────────────────────
    # action_pg_overdue_ecos
    # ──────────────────────────────────────────────────────────────────
    def test_overdue_ecos_finds_only_past_target_date(self):
        today = fields.Date.context_today(self.env['res.partner'])
        overdue = self.env['pg.eco'].create({
            'title': 'Overdue ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'target_release_date': today - timedelta(days=1),
        })
        overdue.action_start_analysis()  # move to analysis state
        future = self.env['pg.eco'].create({
            'title': 'Future ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'target_release_date': today + timedelta(days=30),
        })
        future.action_start_analysis()
        domain = self._eval_domain('product_graph_eco.action_pg_overdue_ecos')
        results = self.env['pg.eco'].search(domain)
        self.assertIn(overdue, results)
        self.assertNotIn(future, results)

    # ──────────────────────────────────────────────────────────────────
    # action_pg_my_activities
    # ──────────────────────────────────────────────────────────────────
    def test_my_pg_activities_scoped_to_pg_models(self):
        item = self._make_item()
        eco = self.env['pg.eco'].create({
            'title': 'Activity ECO',
            'change_type': 'corrective',
            'priority': 'normal',
        })
        todo_xmlid = 'mail.mail_activity_data_todo'
        todo = self.env.ref(todo_xmlid)
        # Activity on pg.eco — in scope.
        in_scope = self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get('pg.eco').id,
            'res_model': 'pg.eco',
            'res_id': eco.id,
            'activity_type_id': todo.id,
            'user_id': self.env.user.id,
            'summary': 'In scope',
        })
        # Activity on res.partner — out of scope.
        partner = self.env['res.partner'].create({'name': 'WQ test partner'})
        out_scope = self.env['mail.activity'].create({
            'res_model_id': self.env['ir.model']._get('res.partner').id,
            'res_model': 'res.partner',
            'res_id': partner.id,
            'activity_type_id': todo.id,
            'user_id': self.env.user.id,
            'summary': 'Out of scope',
        })
        domain = self._eval_domain('product_graph_eco.action_pg_my_activities')
        results = self.env['mail.activity'].search(domain)
        self.assertIn(in_scope, results)
        self.assertNotIn(out_scope, results)

    # ──────────────────────────────────────────────────────────────────
    # Default landing
    # ──────────────────────────────────────────────────────────────────
    def test_root_menu_points_at_my_open_ecos(self):
        root_menu = self.env.ref('product_graph_base.menu_pg_root')
        my_open = self.env.ref('product_graph_eco.action_pg_my_open_ecos')
        self.assertTrue(root_menu.action, "Root menu has no default action")
        # The action field on ir.ui.menu stores a "model,id" reference.
        self.assertEqual(root_menu.action.id, my_open.id)
        self.assertEqual(root_menu.action._name, 'ir.actions.act_window')
