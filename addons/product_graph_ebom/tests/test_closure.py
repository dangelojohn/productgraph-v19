# -*- coding: utf-8 -*-
"""PG-111 — Closure audit & hardening tests.

Sibling to test_pg_ebom_closure.py; this file targets the hardening
adjustments specifically:
    * deep-chain qty correctness (depth ≥ 4)
    * cycle escalation (UserError, not silent skip)
    * depth-cap escalation (UserError)
    * advisory-lock query is issued
    * batched search() — query count bounded by depth, not nodes
    * idempotent rebuild after supersede

The fixture helpers mirror the patterns in test_pg_ebom_closure.py so the
two files can coexist without conflicting setUpClass plumbing.
"""
from unittest import mock

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.product_graph_ebom.models.pg_ebom_closure import (
    CLOSURE_LOCK_NAMESPACE,
    CLOSURE_MAX_DEPTH,
)


@tagged('post_install', '-at_install', 'product_graph')
class TestClosureHardening(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Disable SoD by default; tests that exercise SoD opt back in.
        cls.env['ir.config_parameter'].sudo().set_param('productgraph.enforce_sod', 'False')
        cls.env.user.group_ids |= (
            cls.env.ref('product_graph_base.group_pg_engineer')
            | cls.env.ref('product_graph_base.group_pg_approver')
            | cls.env.ref('product_graph_base.group_pg_admin')
        )
        cls.cat = cls.env.ref('product_graph_base.cat_mechanical')
        cls.Closure = cls.env['pg.ebom.closure']
        cls.Ebom = cls.env['pg.ebom']

    # ─── fixture helpers ────────────────────────────────────────────────────

    def _make_released_item(self, name, item_type='part'):
        item = self.env['pg.item'].create({
            'name': name,
            'item_type': item_type,
            'category_id': self.cat.id,
        })
        item.action_to_engineering()
        rev = self.env['pg.revision'].create({
            'item_id': item.id,
            'name': 'A',
            'change_summary': 'Initial release for closure-hardening test.',
        })
        rev.action_submit_for_review()
        rev.action_release()
        return item, rev

    def _release_ebom(self, root, root_rev, child_lines):
        """child_lines: iterable of (item, qty)."""
        ebom = self.Ebom.create({
            'root_item_id': root.id,
            'revision_id': root_rev.id,
            'ebom_line_ids': [
                (0, 0, {'pg_item_id': child.id, 'product_qty': qty})
                for child, qty in child_lines
            ],
        })
        ebom.action_submit_for_review()
        ebom.action_release()
        return ebom

    def _build_chain(self, names_and_qtys):
        """Build a linear chain Top → ... → Leaf where each link's qty is
        given. Returns the released EBOM of the top-most assembly and the
        list of (item, rev) pairs in top-down order.

        names_and_qtys = [('A', None), ('B', 2.0), ('C', 3.0), ...]
        First entry's qty is ignored (it has no parent edge).
        Items at non-leaf positions are assemblies; the final one is a part.
        """
        items = []
        n = len(names_and_qtys)
        for idx, (name, _qty) in enumerate(names_and_qtys):
            kind = 'assembly' if idx < n - 1 else 'part'
            item, rev = self._make_released_item(name, item_type=kind)
            items.append((item, rev))

        # Release bottom-up: deepest first so each parent's child is
        # already released when we release the parent.
        eboms = [None] * n
        for idx in range(n - 2, -1, -1):
            parent_item, parent_rev = items[idx]
            child_item, _crev = items[idx + 1]
            child_qty = names_and_qtys[idx + 1][1]
            eb = self._release_ebom(parent_item, parent_rev,
                                    [(child_item, child_qty)])
            eboms[idx] = eb
        return eboms[0], items

    # ─── Task 1 — chained-qty correctness at depth 4 ────────────────────────

    def test_closure_depth_5_chained_qty(self):
        """A(qty 1) → B(2) → C(3) → D(4) → E(5) — (A,E,depth=4,qty=120)."""
        top_ebom, items = self._build_chain([
            ('CH-A', None),
            ('CH-B', 2.0),
            ('CH-C', 3.0),
            ('CH-D', 4.0),
            ('CH-E', 5.0),
        ])
        a, b, c, d, e = (it for it, _r in items)

        rows = self.Closure.search([
            ('ancestor_id', '=', a.id),
            ('descendant_id', '=', e.id),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.depth, 4)
        self.assertEqual(rows.qty_chain, 120.0)

        # Spot-check intermediate depths.
        ab = self.Closure.search([
            ('ancestor_id', '=', a.id), ('descendant_id', '=', b.id),
        ])
        self.assertEqual(ab.qty_chain, 2.0)
        self.assertEqual(ab.depth, 1)

        ac = self.Closure.search([
            ('ancestor_id', '=', a.id), ('descendant_id', '=', c.id),
        ])
        self.assertEqual(ac.qty_chain, 6.0)  # 2 * 3
        self.assertEqual(ac.depth, 2)

        ad = self.Closure.search([
            ('ancestor_id', '=', a.id), ('descendant_id', '=', d.id),
        ])
        self.assertEqual(ad.qty_chain, 24.0)  # 2 * 3 * 4
        self.assertEqual(ad.depth, 3)

    # ─── Task 6 + Task 2 — cycle escalation via bypass ──────────────────────

    def test_closure_cycle_raises(self):
        """A→B is released; then a release-bypass write tags B's EBOM with
        a line referencing A, simulating a cycle that slipped past
        validation. Calling _build_for_ebom() must raise UserError."""
        a, a_rev = self._make_released_item('CY-A', item_type='assembly')
        b, b_rev = self._make_released_item('CY-B', item_type='assembly')
        # Place B as a child of A (legal).
        a_ebom = self._release_ebom(a, a_rev, [(b, 1.0)])
        self.assertEqual(a_ebom.state, 'released')

        # Build B's EBOM in draft pointing at A (would fail at release
        # validation, but we bypass that by NOT releasing through
        # action_release — we mutate state via the release-bypass context).
        b_ebom = self.Ebom.create({
            'root_item_id': b.id,
            'revision_id': b_rev.id,
            'ebom_line_ids': [(0, 0, {'pg_item_id': a.id, 'product_qty': 1.0})],
        })
        b_ebom.with_context(_pg_release_bypass=True).write({
            'state': 'released',
            'effective_date': fields.Date.today(),
        })

        # B's closure build should walk B→A→B and raise on the revisit of B.
        with self.assertRaises(UserError) as cm:
            self.Closure._build_for_ebom(b_ebom)
        self.assertIn('Cycle', str(cm.exception.args[0]))

    # ─── Task 2 — depth-cap escalation ──────────────────────────────────────

    def test_closure_depth_cap_raises(self):
        """A 31-level deep chain exceeds CLOSURE_MAX_DEPTH (30). Building
        the top EBOM's closure must raise UserError citing the depth limit.

        Constructing 31 real released items+EBOMs is heavy in a transaction
        test, so we patch CLOSURE_MAX_DEPTH down for this test instead.
        """
        # Build a 4-level chain and pretend the cap is 2.
        top_ebom, _items = self._build_chain([
            ('DC-A', None),
            ('DC-B', 1.0),
            ('DC-C', 1.0),
            ('DC-D', 1.0),
        ])
        # Clear what was already built by action_release above, since the
        # cap-of-30 build succeeded. Re-run under a patched cap.
        self.Closure._clear_for_ebom(top_ebom)
        with mock.patch(
            'odoo.addons.product_graph_ebom.models.pg_ebom_closure.CLOSURE_MAX_DEPTH',
            2,
        ):
            with self.assertRaises(UserError) as cm:
                self.Closure._build_for_ebom(top_ebom)
        self.assertIn('depth limit', str(cm.exception.args[0]))

    # ─── Task 3 — advisory-lock query is issued ─────────────────────────────

    def test_closure_advisory_lock_is_taken(self):
        """_build_for_ebom must execute pg_advisory_xact_lock keyed by the
        EBOM id. Verifies the lock query is on the wire (full serialization
        test would need threads + a separate connection)."""
        asm, asm_rev = self._make_released_item('LK-A', item_type='assembly')
        comp, _ = self._make_released_item('LK-B')
        # Pre-release so we control the closure build call below.
        ebom = self.Ebom.create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [(0, 0, {'pg_item_id': comp.id, 'product_qty': 1.0})],
        })
        ebom.action_submit_for_review()
        ebom.action_release()  # exercises the path end-to-end

        # Inspect via spy: rebuild with mocked execute that records calls.
        original_execute = self.env.cr.execute
        seen = []

        def spy(query, *args, **kwargs):
            seen.append((query, args, kwargs))
            return original_execute(query, *args, **kwargs)

        with mock.patch.object(self.env.cr, 'execute', side_effect=spy):
            self.Closure._build_for_ebom(ebom)

        lock_calls = [
            (q, args, kwargs) for q, args, kwargs in seen
            if 'pg_advisory_xact_lock' in (q or '')
        ]
        self.assertTrue(lock_calls, 'expected pg_advisory_xact_lock to be issued')
        # Confirm the (namespace, ebom_id) pair was passed.
        _q, args, kwargs = lock_calls[0]
        # First positional after query is the params tuple.
        params = args[0] if args else kwargs.get('params')
        self.assertEqual(params[0], CLOSURE_LOCK_NAMESPACE)
        self.assertEqual(params[1], ebom.id)

    # ─── Task 7 — incremental supersede preserves only the new closure ──────

    def test_closure_incremental_after_supersede(self):
        asm, asm_rev_a = self._make_released_item('SP-A', item_type='assembly')
        c1, _ = self._make_released_item('SP-C1')
        c2, _ = self._make_released_item('SP-C2')

        ebom_v1 = self._release_ebom(asm, asm_rev_a, [(c1, 1.0)])
        v1_rows = self.Closure.search([('ebom_id', '=', ebom_v1.id)])
        self.assertEqual(len(v1_rows), 1)
        self.assertEqual(v1_rows.descendant_id, c1)

        # New revision and new EBOM superseding v1.
        asm_rev_b = self.env['pg.revision'].create({
            'item_id': asm.id,
            'name': 'B',
            'change_summary': 'Supersede v1.',
        })
        asm_rev_b.action_submit_for_review()
        asm_rev_b.action_release()
        ebom_v2 = self._release_ebom(asm, asm_rev_b, [(c2, 2.0)])

        # v1 closure rows must be gone.
        v1_rows_after = self.Closure.search([('ebom_id', '=', ebom_v1.id)])
        self.assertFalse(v1_rows_after)
        # v2 closure rows present.
        v2_rows = self.Closure.search([('ebom_id', '=', ebom_v2.id)])
        self.assertEqual(len(v2_rows), 1)
        self.assertEqual(v2_rows.descendant_id, c2)
        self.assertEqual(v2_rows.qty_chain, 2.0)
        self.assertEqual(ebom_v1.state, 'superseded')

    # ─── Task 4 — batched search() bounds queries by depth, not nodes ───────

    def test_closure_batched_search_is_bounded(self):
        """A flat 50-line BOM should cause at most O(depth) SELECTs on the
        pg_ebom table during _build_for_ebom (one per depth level; flat = 1).

        We count by scanning cr.execute calls for `FROM pg_ebom` SELECTs.
        Pre-batching the implementation issued one search() per child
        (≥50 SELECTs); after batching it should issue ≤2 (search +
        possible prefetch).
        """
        asm, asm_rev = self._make_released_item('BT-A', item_type='assembly')
        children = [self._make_released_item(f'BT-C{i}')[0] for i in range(50)]
        ebom = self.Ebom.create({
            'root_item_id': asm.id,
            'revision_id': asm_rev.id,
            'ebom_line_ids': [
                (0, 0, {'pg_item_id': c.id, 'product_qty': 1.0})
                for c in children
            ],
        })
        ebom.action_submit_for_review()
        ebom.action_release()
        # Wipe and re-build with a spy on the cursor.
        self.Closure._clear_for_ebom(ebom)

        cr = self.env.cr
        original_execute = cr.execute
        select_counts = {'pg_ebom': 0}

        def spy(query, *args, **kwargs):
            # In Odoo 19 cr.execute may receive a SQL object (odoo.tools.SQL)
            # in addition to raw str. str() works on both.
            q = str(query or '').lower()
            # Count SELECTs that target pg_ebom (the header table) but NOT
            # pg_ebom_line or pg_ebom_closure.
            if 'from "pg_ebom"' in q or 'from pg_ebom ' in q or q.rstrip().endswith('from "pg_ebom"'):
                select_counts['pg_ebom'] += 1
            return original_execute(query, *args, **kwargs)

        with mock.patch.object(cr, 'execute', side_effect=spy):
            self.Closure._build_for_ebom(ebom)

        # Flat BOM = 1 depth iteration → at most 1 pg.ebom search to look
        # up released children. Give some slack for Odoo prefetch (≤3).
        self.assertLessEqual(
            select_counts['pg_ebom'], 3,
            f"expected ≤3 SELECT FROM pg_ebom, got {select_counts['pg_ebom']}",
        )

        # Verify rows present and correct shape.
        rows = self.Closure.search([('ebom_id', '=', ebom.id)])
        self.assertEqual(len(rows), 50)
        self.assertTrue(all(r.depth == 1 for r in rows))


@tagged('post_install', '-at_install', 'product_graph')
class TestClosureConstants(TransactionCase):
    """Sanity checks on the module-level constants the harness relies on."""

    def test_lock_namespace_is_int32(self):
        # pg_advisory_xact_lock(int4, int4) requires signed-32-bit values.
        self.assertIsInstance(CLOSURE_LOCK_NAMESPACE, int)
        self.assertGreater(CLOSURE_LOCK_NAMESPACE, 0)
        self.assertLess(CLOSURE_LOCK_NAMESPACE, 2 ** 31)

    def test_depth_cap_is_reasonable(self):
        self.assertGreaterEqual(CLOSURE_MAX_DEPTH, 10)
        self.assertLess(CLOSURE_MAX_DEPTH, 1000)
