# -*- coding: utf-8 -*-
"""PG-131 — Auto-spawn target revision from an ECO affected-item line."""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'product_graph')
class TestSpawnRevision(TransactionCase):

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
            'productgraph.auto_spawn_revision_from_eco_line', 'True',
        )

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def _make_item(self, name='Spawn Test Item'):
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
            'change_summary': 'Initial release of the test item.',
        })
        rev.action_submit_for_review()
        rev.action_release()
        return rev

    def _make_eco_with_line(self, item, from_rev, change_kind='revision_bump'):
        eco = self.env['pg.eco'].create({
            'title': 'Spawn ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })
        line = self.env['pg.eco.line'].create({
            'eco_id': eco.id,
            'item_id': item.id,
            'from_revision_id': from_rev.id,
            'change_kind': change_kind,
            'notes': 'Bumping minor revision per CR-42.',
        })
        return eco, line

    # ──────────────────────────────────────────────────────────────────
    # Tests
    # ──────────────────────────────────────────────────────────────────
    def test_spawn_creates_draft_revision_with_cloned_properties(self):
        item = self._make_item('Clone-Props Item')
        rev_a = self._release_revision(item, code='A')
        _eco, line = self._make_eco_with_line(item, rev_a)
        action = line.action_spawn_revision()
        # Returns an act_window pointing at the new revision.
        self.assertEqual(action['res_model'], 'pg.revision')
        new_rev = self.env['pg.revision'].browse(action['res_id'])
        self.assertTrue(new_rev.exists())
        self.assertEqual(new_rev.state, 'draft',
                         "Spawned revision MUST land as draft, never released.")
        self.assertEqual(new_rev.item_id, item,
                         "Spawned revision must belong to the same item.")
        self.assertEqual(new_rev.predecessor_revision_id, rev_a)
        # name auto-incremented A -> B
        self.assertEqual(new_rev.name, 'B')

    def test_spawn_clones_released_ebom_to_draft(self):
        # Only meaningful if product_graph_ebom is installed (it is, given
        # the test addon graph). Build a released EBOM on the source.
        EbomModel = self.env.get('pg.ebom')
        if EbomModel is None:
            self.skipTest("product_graph_ebom not installed in this env.")
        # Build assembly + child item so the EBOM has at least one line.
        child = self._make_item('Child Component')
        child_rev = self._release_revision(child, code='A')
        # released child means its parent item is also released, so that
        # the EBOM release pre-conditions pass.
        parent = self._make_item('Parent Assembly')
        # The parent rev is pre-release; build EBOM in draft and release it.
        parent_rev_a = self.env['pg.revision'].create({
            'item_id': parent.id,
            'name': 'A',
            'change_summary': 'Initial release of parent assembly.',
        })
        ebom = EbomModel.create({
            'root_item_id': parent.id,
            'revision_id': parent_rev_a.id,
            'ebom_type': 'production',
        })
        self.env['pg.ebom.line'].create({
            'ebom_id': ebom.id,
            'pg_item_id': child.id,
            'pg_revision_id': child_rev.id,
            'product_qty': 1.0,
        })
        # Submit + release the parent rev first; then the EBOM.
        parent_rev_a.action_submit_for_review()
        parent_rev_a.action_release()
        ebom.action_submit_for_review()
        ebom.action_release()
        self.assertEqual(ebom.state, 'released')

        # Now spawn a new revision from rev A via an ECO line.
        _eco, line = self._make_eco_with_line(parent, parent_rev_a)
        action = line.action_spawn_revision()
        new_rev = self.env['pg.revision'].browse(action['res_id'])
        # A new draft EBOM should now exist bound to the new revision.
        new_ebom = EbomModel.search([
            ('revision_id', '=', new_rev.id),
        ])
        self.assertEqual(len(new_ebom), 1,
                         "Exactly one cloned EBOM should exist for the new rev.")
        self.assertEqual(new_ebom.state, 'draft')
        self.assertEqual(len(new_ebom.ebom_line_ids), 1)
        self.assertEqual(new_ebom.ebom_line_ids.pg_item_id, child)

    def test_spawn_fails_if_to_revision_exists(self):
        item = self._make_item('Has-target Item')
        rev_a = self._release_revision(item, code='A')
        rev_b = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'B',
            'change_summary': 'Pre-existing target revision.',
        })
        _eco, line = self._make_eco_with_line(item, rev_a)
        line.to_revision_id = rev_b.id
        with self.assertRaises(UserError):
            line.action_spawn_revision()

    def test_spawn_fails_without_from_revision(self):
        item = self._make_item('No-source Item')
        # Build an ECO line without a from_revision.
        eco = self.env['pg.eco'].create({
            'title': 'No-source ECO',
            'change_type': 'corrective',
            'priority': 'normal',
            'approval_board_ids': [(6, 0, [self.test_user.id])],
        })
        line = self.env['pg.eco.line'].create({
            'eco_id': eco.id,
            'item_id': item.id,
            'change_kind': 'revision_bump',
        })
        with self.assertRaises(UserError):
            line.action_spawn_revision()

    def test_spawn_disabled_via_icp(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'productgraph.auto_spawn_revision_from_eco_line', 'False',
        )
        try:
            item = self._make_item('ICP-off Item')
            rev_a = self._release_revision(item, code='A')
            _eco, line = self._make_eco_with_line(item, rev_a)
            with self.assertRaises(UserError):
                line.action_spawn_revision()
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'productgraph.auto_spawn_revision_from_eco_line', 'True',
            )

    def test_spawn_links_to_revision_back_to_line(self):
        item = self._make_item('Backlink Item')
        rev_a = self._release_revision(item, code='A')
        _eco, line = self._make_eco_with_line(item, rev_a)
        action = line.action_spawn_revision()
        new_rev = self.env['pg.revision'].browse(action['res_id'])
        # to_revision_id must be back-stamped on the line.
        self.assertEqual(line.to_revision_id, new_rev,
                         "to_revision_id must be back-stamped on the line.")

    def test_spawn_rejects_obsolete_change_kind(self):
        # obsolete / introduce have no target revision concept — must reject.
        item = self._make_item('Obsolete-kind Item')
        rev_a = self._release_revision(item, code='A')
        _eco, line = self._make_eco_with_line(
            item, rev_a, change_kind='obsolete',
        )
        with self.assertRaises(UserError):
            line.action_spawn_revision()

    def test_next_revision_name_helper(self):
        Rev = self.env['pg.revision']
        self.assertEqual(Rev._next_revision_name_after('A'), 'B')
        self.assertEqual(Rev._next_revision_name_after('F'), 'G')
        self.assertEqual(Rev._next_revision_name_after('Z'), 'AA')
        self.assertEqual(Rev._next_revision_name_after('1'), '2')
        self.assertEqual(Rev._next_revision_name_after('10'), '11')
        self.assertEqual(Rev._next_revision_name_after(''), 'A')
        self.assertEqual(Rev._next_revision_name_after(None), 'A')
        # multi-letter fallback
        self.assertEqual(Rev._next_revision_name_after('AA'), 'AA+1')
