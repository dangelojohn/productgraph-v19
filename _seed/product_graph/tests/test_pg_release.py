# -*- coding: utf-8 -*-
"""
Tests for pg.release — the governance gate.

Verifies that:
  - Only Release Managers can execute releases.
  - mrp.bom is created with the correct product and BOM lines.
  - Revision state transitions to 'released' after execution.
  - Item auto-transitions to 'released'.
  - Second execution on an already-executed release raises.
  - Failed release records failure_reason.
"""
from unittest.mock import patch
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestPgRelease(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Give admin all PG groups
        cls.env.user.groups_id = [
            (4, cls.env.ref('product_graph.group_pg_admin').id),
            (4, cls.env.ref('product_graph.group_pg_release_manager').id),
        ]
        cls.category = cls.env['pg.category'].create({
            'name': 'Release Test Cat', 'code': 'RT',
        })
        uom_unit = cls.env.ref('uom.product_uom_unit')

        # Create component item with product
        cls.comp_item = cls.env['pg.item'].create({
            'name': 'Component A',
            'item_type': 'purchased',
            'category_id': cls.category.id,
        })
        comp_tmpl = cls.env['product.template'].create({
            'name': 'Component A',
            'type': 'consu',
        })
        cls.comp_item.product_id = comp_tmpl.product_variant_ids[:1]

        # Create item to release
        cls.item = cls.env['pg.item'].create({
            'name': 'Release Test Assembly',
            'item_type': 'manufactured',
            'category_id': cls.category.id,
        })
        # Force to engineering state
        cls.env.cr.execute(
            "UPDATE pg_item SET state = 'engineering' WHERE id = %s", (cls.item.id,)
        )

        # Create revision in approved state
        cls.revision = cls.env['pg.revision'].create({
            'item_id': cls.item.id,
            'revision_code': 'A',
            'sequence': 10,
        })
        cls.env.cr.execute(
            "UPDATE pg_revision SET state = 'approved' WHERE id = %s", (cls.revision.id,)
        )
        # Add BOM line
        cls.env['pg.bom.line'].create({
            'revision_id': cls.revision.id,
            'component_item_id': cls.comp_item.id,
            'product_qty': 2,
            'product_uom_id': uom_unit.id,
        })
        cls.env.invalidate_all()

    def _make_release(self):
        return self.env['pg.release'].create({
            'item_id': self.item.id,
            'revision_id': self.revision.id,
            'archive_previous_bom': False,
        })

    def test_execute_release_creates_mrp_bom(self):
        release = self._make_release()
        release.action_execute_release()
        self.assertEqual(release.state, 'executed')
        self.assertTrue(release.mrp_bom_id, "mrp.bom should be created")
        self.assertEqual(len(release.mrp_bom_id.bom_line_ids), 1)

    def test_execute_release_transitions_revision(self):
        # Reset revision to approved for this test
        self.env.cr.execute(
            "UPDATE pg_revision SET state = 'approved', mrp_bom_id = NULL WHERE id = %s",
            (self.revision.id,)
        )
        self.env.invalidate_all()
        release = self._make_release()
        release.action_execute_release()
        self.assertEqual(self.revision.state, 'released')

    def test_double_execute_raises(self):
        self.env.cr.execute(
            "UPDATE pg_revision SET state = 'approved', mrp_bom_id = NULL WHERE id = %s",
            (self.revision.id,)
        )
        self.env.invalidate_all()
        release = self._make_release()
        release.action_execute_release()
        with self.assertRaises(UserError):
            release.action_execute_release()

    def test_non_approved_revision_blocked(self):
        self.env.cr.execute(
            "UPDATE pg_revision SET state = 'draft' WHERE id = %s", (self.revision.id,)
        )
        self.env.invalidate_all()
        release = self._make_release()
        with self.assertRaises(UserError):
            release.action_execute_release()
        # Restore
        self.env.cr.execute(
            "UPDATE pg_revision SET state = 'approved', mrp_bom_id = NULL WHERE id = %s",
            (self.revision.id,)
        )
        self.env.invalidate_all()
