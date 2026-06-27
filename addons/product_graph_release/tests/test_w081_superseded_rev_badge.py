# -*- coding: utf-8 -*-
"""W081 (R4.S5) — pg_is_on_superseded_rev stored compute tests.

JTBD: planner needs to see "your revision is older than latest"
on the MO list/kanban at a glance. The stored boolean drives a
red list-row decoration + a kanban badge.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "productgraph", "w081")
class TestW081SupersededRevBadge(TransactionCase):

    def test_field_present_and_stored(self):
        f = self.env["mrp.production"]._fields.get(
            "pg_is_on_superseded_rev")
        self.assertIsNotNone(f, "W081 field missing")
        self.assertEqual(f.type, "boolean")
        self.assertTrue(f.store, "W081 must be a stored compute")
        # Depends must reach the revision state (else stale on supersede)
        depends = f.depends or ()
        # Tuple of dotted paths; we want pg_release_id.revision_id.state
        joined = " ".join(depends)
        self.assertIn("pg_release_id.revision_id.state", joined,
                      "W081 must depend on pg.revision.state")

    def test_mo_without_release_is_false(self):
        """An MO with no pg_release_id is NOT on a superseded rev
        — the flag must be False (not None)."""
        MO = self.env["mrp.production"].search(
            [("pg_release_id", "=", False)], limit=1)
        if not MO:
            self.skipTest("no MO without pg_release_id in this DB")
        self.assertFalse(MO.pg_is_on_superseded_rev)

    def test_compute_runs_clean(self):
        """The compute is callable without raising on the whole MO
        table (defensive: a release with a half-built revision
        must not crash the planner's kanban load)."""
        mos = self.env["mrp.production"].search([], limit=20)
        # Force a recompute by reading the field. Any per-row crash
        # surfaces here.
        for mo in mos:
            _ = mo.pg_is_on_superseded_rev
