# -*- coding: utf-8 -*-
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestPgEcoConstraints(TransactionCase):

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
        cls.item_a = cls.env['pg.item'].create({
            'name': 'Constraints Test Item A',
            'item_type': 'part',
            'category_id': cls.cat.id,
        })
        cls.item_a.action_to_engineering()
        cls.item_b = cls.env['pg.item'].create({
            'name': 'Constraints Test Item B',
            'item_type': 'part',
            'category_id': cls.cat.id,
        })
        cls.item_b.action_to_engineering()
        cls.rev_a = cls.env['pg.revision'].create({
            'item_id': cls.item_a.id,
            'name': 'A',
            'change_summary': 'Initial release of constraints test item A.',
        })

    def _draft_eco(self):
        return self.env['pg.eco'].create({
            'title': 'Constraint Test ECO',
            'change_type': 'corrective',
        })

    # ──────────────────────────────────────────────────────────────────
    # Sequence assignment
    # ──────────────────────────────────────────────────────────────────
    def test_create_assigns_sequence(self):
        eco = self._draft_eco()
        self.assertTrue(eco.name.startswith('ECO/'),
                        f"Expected ECO/YYYY/N sequence, got {eco.name}")

    # ──────────────────────────────────────────────────────────────────
    # Released-immutability
    # ──────────────────────────────────────────────────────────────────
    def test_released_eco_blocks_title_write(self):
        # Force one into 'released' via the bypass context to keep the test
        # focused on the write-guard, not the whole walkthrough.
        eco = self._draft_eco()
        eco.with_context(_pg_eco_release_bypass=True).write({'state': 'released'})
        with self.assertRaises(UserError):
            eco.write({'title': 'new title'})

    def test_chatter_write_allowed_on_released_eco(self):
        eco = self._draft_eco()
        eco.with_context(_pg_eco_release_bypass=True).write({'state': 'released'})
        # Posting a message must not raise.
        eco.message_post(body='Post-release follow-up note')

    # ──────────────────────────────────────────────────────────────────
    # Unlink guards
    # ──────────────────────────────────────────────────────────────────
    def test_unlink_blocked_outside_draft(self):
        eco = self._draft_eco()
        # promoting an empty ECO out of draft requires lines; bypass via context
        eco.with_context(_pg_eco_release_bypass=True).write({'state': 'analysis'})
        with self.assertRaises(UserError):
            eco.unlink()

    def test_unlink_allowed_in_draft(self):
        eco = self._draft_eco()
        eco.unlink()
        self.assertFalse(eco.exists())

    # ──────────────────────────────────────────────────────────────────
    # Line revision/item consistency
    # ──────────────────────────────────────────────────────────────────
    def test_line_rev_must_belong_to_item(self):
        eco = self._draft_eco()
        with self.assertRaises(ValidationError):
            self.env['pg.eco.line'].create({
                'eco_id': eco.id,
                'item_id': self.item_b.id,
                'from_revision_id': self.rev_a.id,  # belongs to item_a
                'change_kind': 'introduce',
            })

    # ──────────────────────────────────────────────────────────────────
    # Approval-row append-only
    # ──────────────────────────────────────────────────────────────────
    def test_approval_row_immutable(self):
        eco = self._draft_eco()
        appr = self.env['pg.eco.approval'].create({
            'eco_id': eco.id,
            'user_id': self.env.user.id,
            'decision': 'approved',
            'comment': 'initial',
        })
        with self.assertRaises(UserError):
            appr.write({'comment': 'changed'})
        with self.assertRaises(UserError):
            appr.unlink()
