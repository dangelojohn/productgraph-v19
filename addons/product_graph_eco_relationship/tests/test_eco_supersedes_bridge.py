# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestEcoSupersedesBridge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disable SoD by default; tests that exercise SoD opt back in.
        cls.env['ir.config_parameter'].sudo().set_param('productgraph.enforce_sod', 'False')
        cls.test_user = cls.env.ref('base.user_admin')
        cls.test_user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.env = cls.env(user=cls.test_user)
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')

        def mk_item(name):
            it = cls.env['pg.item'].create({
                'name': name,
                'item_type': 'part',
                'category_id': cls.cat.id,
            })
            it.action_to_engineering()
            return it

        # Old item being obsoleted; new item taking its place.
        cls.old = mk_item('Old Item')
        cls.new = mk_item('New Replacement Item')

        # Released revisions for both items (needed for ECO release).
        cls.rev_old_A = cls.env['pg.revision'].create({
            'item_id': cls.old.id,
            'name': 'A',
            'change_summary': 'Initial revision of the old item.',
        })
        cls.rev_old_A.action_submit_for_review()
        cls.rev_old_A.action_release()

        cls.rev_new_A = cls.env['pg.revision'].create({
            'item_id': cls.new.id,
            'name': 'A',
            'change_summary': 'Initial revision of the new item.',
        })
        cls.rev_new_A.action_submit_for_review()
        cls.rev_new_A.action_release()

    def _make_obsolete_eco(self, replacement=None):
        eco = self.env['pg.eco'].create({
            'title': 'Obsolete old, use new',
            'change_type': 'corrective',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })
        line_vals = {
            'eco_id': eco.id,
            'item_id': self.old.id,
            'from_revision_id': self.rev_old_A.id,
            'change_kind': 'obsolete',
        }
        if replacement:
            line_vals['replacement_item_id'] = replacement.id
        self.env['pg.eco.line'].create(line_vals)
        return eco

    # ──────────────────────────────────────────────────────────────────
    # Constraint
    # ──────────────────────────────────────────────────────────────────
    def test_replacement_only_on_obsolete(self):
        eco = self.env['pg.eco'].create({
            'title': 'Wrong kind',
            'change_type': 'corrective',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })
        with self.assertRaises(ValidationError):
            self.env['pg.eco.line'].create({
                'eco_id': eco.id,
                'item_id': self.old.id,
                'from_revision_id': self.rev_old_A.id,
                'to_revision_id': self.rev_old_A.id,
                'change_kind': 'revision_bump',
                'replacement_item_id': self.new.id,
            })

    def test_replacement_cannot_equal_item(self):
        eco = self.env['pg.eco'].create({
            'title': 'Self loop',
            'change_type': 'corrective',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })
        with self.assertRaises(ValidationError):
            self.env['pg.eco.line'].create({
                'eco_id': eco.id,
                'item_id': self.old.id,
                'change_kind': 'obsolete',
                'replacement_item_id': self.old.id,
            })

    # ──────────────────────────────────────────────────────────────────
    # Release writes the edge
    # ──────────────────────────────────────────────────────────────────
    def test_release_writes_supersedes_edge(self):
        eco = self._make_obsolete_eco(replacement=self.new)
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='ok')
        eco.action_release()
        rel = self.env['pg.relationship'].search([
            ('source_item_id', '=', self.new.id),
            ('target_item_id', '=', self.old.id),
            ('kind', '=', 'supersedes'),
        ])
        self.assertEqual(len(rel), 1)
        self.assertEqual(rel.eco_id, eco)

    def test_release_without_replacement_writes_nothing(self):
        eco = self._make_obsolete_eco(replacement=None)
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='ok')
        eco.action_release()
        rel = self.env['pg.relationship'].search([
            ('target_item_id', '=', self.old.id),
            ('kind', '=', 'supersedes'),
        ])
        self.assertFalse(rel)

    def test_release_is_idempotent_for_supersedes_edge(self):
        """If the edge already exists (e.g. someone wrote it manually
        before the release), release does not duplicate it."""
        self.env['pg.relationship'].create({
            'source_item_id': self.new.id,
            'target_item_id': self.old.id,
            'kind': 'supersedes',
        })
        eco = self._make_obsolete_eco(replacement=self.new)
        eco.action_start_analysis()
        eco.action_submit_for_approval()
        eco.action_approve(comment='ok')
        eco.action_release()
        rels = self.env['pg.relationship'].search([
            ('source_item_id', '=', self.new.id),
            ('target_item_id', '=', self.old.id),
            ('kind', '=', 'supersedes'),
        ])
        self.assertEqual(len(rels), 1)
        # The eco_id back-ref should now be populated.
        self.assertEqual(rels.eco_id, eco)
