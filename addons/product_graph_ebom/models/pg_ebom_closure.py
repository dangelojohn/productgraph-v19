# -*- coding: utf-8 -*-
"""
pg.ebom.closure — Materialized transitive closure of every released EBOM.

For every (ancestor_item, descendant_item, depth) triple reachable through
released EBOMs, a closure row exists. The closure makes where-used and
BOM-explosion queries O(rows-returned) rather than O(tree-walk), meeting
the NFR §12.2 targets (sub-3-second where-used at 1M lines).

Closure is rebuilt PER EBOM, only at release time. Draft and review EBOMs
do not contribute. Superseded EBOMs have their closure rows cleared.

Row semantics:
    ancestor_id  — pg.item at the top of the path
    descendant_id — pg.item somewhere below in the BOM tree
    depth        — distance in the tree (1 = direct child; never 0 in Phase 1)
    qty_chain    — product of all per-level qtys along the path
    ebom_id      — the EBOM from which this row was derived

This module uses ORM create() rather than raw SQL (Bible R2).
"""
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# Advisory-lock namespace for closure rebuilds. ASCII "pges" → 0x70676573.
# Reserved per PG-111 to serialize concurrent _build_for_ebom() calls on the
# same EBOM id. pg_advisory_xact_lock(int4, int4) uses (namespace, key) so
# the second arg is the ebom.id.
CLOSURE_LOCK_NAMESPACE = 0x70676573

# Hard depth cap. Exceeding this is treated as a structural BOM defect.
CLOSURE_MAX_DEPTH = 30


class PgEbomClosure(models.Model):
    _name = 'pg.ebom.closure'
    _description = 'ProductGraph EBOM Closure (where-used / explosion)'
    _order = 'ancestor_id, depth, descendant_id'
    _rec_name = 'descendant_id'

    ebom_id = fields.Many2one(
        'pg.ebom',
        string='Source EBOM',
        required=True,
        ondelete='cascade',
        index=True,
    )
    ancestor_id = fields.Many2one(
        'pg.item',
        string='Ancestor',
        required=True,
        ondelete='cascade',
        index=True,
    )
    descendant_id = fields.Many2one(
        'pg.item',
        string='Descendant',
        required=True,
        ondelete='cascade',
        index=True,
    )
    depth = fields.Integer(string='Depth', required=True, index=True)
    qty_chain = fields.Float(string='Chained Qty', default=1.0)

    _closure_unique = models.Constraint(
        'UNIQUE(ebom_id, ancestor_id, descendant_id, depth)',
        'Closure row must be unique per (ebom, ancestor, descendant, depth).',
    )

    def init(self):
        """Create supporting indexes for sub-3-second where-used queries.
        Indexes are additive — safe under Bible R2 (no destructive SQL)."""
        cr = self.env.cr
        cr.execute("""
            CREATE INDEX IF NOT EXISTS pg_ebom_closure_descendant_idx
            ON pg_ebom_closure (descendant_id, depth);
        """)
        cr.execute("""
            CREATE INDEX IF NOT EXISTS pg_ebom_closure_ancestor_idx
            ON pg_ebom_closure (ancestor_id, depth);
        """)
        # Per-EBOM scans dominate explode() and supersede-clear. The bare
        # ebom_id index (from Odoo's index=True) is single-column; adding a
        # composite covers ORDER BY depth and depth predicates without
        # bouncing off the heap.
        cr.execute("""
            CREATE INDEX IF NOT EXISTS pg_ebom_closure_ebom_depth_idx
            ON pg_ebom_closure (ebom_id, depth);
        """)

    # ═════════════════════════════════════════════════════════════════════════
    # Public builders (called from pg.ebom.action_release)
    # ═════════════════════════════════════════════════════════════════════════

    @api.model
    def _build_for_ebom(self, ebom):
        """Build closure rows for the given EBOM.

        Walks the EBOM tree breadth-first, batching one search() per depth
        level (O(depth) queries instead of O(nodes)). For each level, inserts
        one closure row per (ebom.root_item_id, descendant, depth, qty_chain).
        Recurses into any descendant that itself has a released EBOM, so a
        single closure rebuild captures the multi-level structure as one
        flat table.

        Cycle and depth-cap breaches are escalated to UserError — PG-101's
        validate-time check should have caught them, so reaching here means
        the data violated invariants (legacy import, raw write, bypass flag).
        """
        if ebom.state != 'released':
            _logger.info('Skipping closure build for non-released EBOM %s', ebom.display_name)
            return

        # Advisory lock prevents concurrent rebuilds of the same EBOM closure.
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (CLOSURE_LOCK_NAMESPACE, ebom.id),
        )

        ancestor_item = ebom.root_item_id
        # Per-depth aggregated rows: key=(descendant_id, depth) → qty_chain.
        # Aggregating at the level honours the UNIQUE constraint and matches
        # MRP rollup semantics when shared subassemblies appear at the same
        # depth via multiple sibling paths.
        agg = {}

        # BFS frontier. Each entry: (child_item, chain_qty_so_far, path_ids).
        # path_ids is a frozenset of ancestor item ids on the path from root
        # to this child (inclusive of root, exclusive of child). Used for
        # *true* cycle detection (a cycle = child appears in its own ancestor
        # path), as distinct from DAG re-visits (legal: shared subassembly).
        root_path = frozenset({ancestor_item.id})
        frontier = [
            (line.pg_item_id, (line.product_qty or 0.0), root_path)
            for line in ebom.ebom_line_ids
            if line.pg_item_id
        ]

        depth = 1
        # Persistent cache of resolved released EBOMs across depth levels.
        # An item's released EBOM (if any) is unique, so this saves repeated
        # search()s for items that appear at multiple depths via DAG paths.
        # Values: pg.ebom recordset (possibly empty) for negative-cache.
        ebom_cache = {}

        while frontier:
            if depth > CLOSURE_MAX_DEPTH:
                _logger.warning(
                    "pg.ebom.closure: depth cap %d exceeded for EBOM id=%s (%s)",
                    CLOSURE_MAX_DEPTH, ebom.id, ebom.display_name,
                )
                raise UserError(_(
                    "EBOM %(name)s closure exceeded the depth limit of "
                    "%(cap)d. This usually means the BOM is too deeply "
                    "nested — redesign with sub-assemblies, or increase "
                    "CLOSURE_MAX_DEPTH if the structure is intentional.",
                    name=ebom.display_name,
                    cap=CLOSURE_MAX_DEPTH,
                ))

            # True-cycle detection: any frontier child that appears in its
            # OWN ancestor path is a cycle. (A child shared between two
            # sibling subtrees is NOT a cycle — that's a legal DAG.)
            cycle_hits = [c for c, _q, path in frontier if c.id in path]
            if cycle_hits:
                seen_ids = set()
                deduped = []
                for c in cycle_hits:
                    if c.id not in seen_ids:
                        seen_ids.add(c.id)
                        deduped.append(c)
                cycle_path = ' → '.join(c.display_name for c in deduped)
                _logger.warning(
                    "pg.ebom.closure: cycle detected in EBOM id=%s (%s); "
                    "components on their own ancestor path: %s. "
                    "PG-101 should have caught this.",
                    ebom.id, ebom.display_name, cycle_path,
                )
                raise UserError(_(
                    "Cycle detected while building closure for EBOM "
                    "%(name)s.\nComponents on their own ancestor path: "
                    "%(path)s.\nThis indicates a cycle slipped past "
                    "validation — refuse to materialize an incomplete closure.",
                    name=ebom.display_name,
                    path=cycle_path,
                ))

            # Aggregate per-depth rows. Same (descendant, depth) reached via
            # multiple paths sums its qty_chain — that's the rollup count of
            # the descendant needed at that level of the assembly tree.
            for child, qty_chain, _path in frontier:
                key = (child.id, depth)
                agg[key] = agg.get(key, 0.0) + qty_chain

            # Batch-resolve which frontier children have released EBOMs of
            # their own — one search() per depth instead of one per child.
            # Negative-cache misses so repeated DAG visits don't re-search.
            frontier_child_ids = list({c.id for c, _q, _p in frontier})
            uncached_ids = [
                cid for cid in frontier_child_ids if cid not in ebom_cache
            ]
            if uncached_ids:
                child_ebs = self.env['pg.ebom'].search([
                    ('root_item_id', 'in', uncached_ids),
                    ('state', '=', 'released'),
                ])
                for eb in child_ebs:
                    ebom_cache[eb.root_item_id.id] = eb
                # Negative-cache the rest as empty recordset.
                Ebom = self.env['pg.ebom']
                for cid in uncached_ids:
                    if cid not in ebom_cache:
                        ebom_cache[cid] = Ebom

            next_frontier = []
            for child, qty_chain, path in frontier:
                child_ebom = ebom_cache.get(child.id)
                if not child_ebom:
                    continue
                child_path = path | {child.id}
                for line in child_ebom.ebom_line_ids:
                    if not line.pg_item_id:
                        continue
                    next_frontier.append((
                        line.pg_item_id,
                        qty_chain * (line.product_qty or 0.0),
                        child_path,
                    ))

            frontier = next_frontier
            depth += 1

        # Materialize. Wipe stale rows and replace atomically (idempotent).
        rows = [
            {
                'ebom_id': ebom.id,
                'ancestor_id': ancestor_item.id,
                'descendant_id': desc_id,
                'depth': d,
                'qty_chain': qty,
            }
            for (desc_id, d), qty in agg.items()
        ]
        self.search([('ebom_id', '=', ebom.id)]).unlink()
        if rows:
            self.create(rows)
            _logger.info(
                'pg.ebom.closure: built %d rows for EBOM %s',
                len(rows), ebom.display_name,
            )

    @api.model
    def _clear_for_ebom(self, ebom):
        """Remove all closure rows that came from this EBOM."""
        self.search([('ebom_id', '=', ebom.id)]).unlink()

    # ═════════════════════════════════════════════════════════════════════════
    # Read API for the REST / MCP layer
    # ═════════════════════════════════════════════════════════════════════════

    @api.model
    def where_used(self, item_id, max_depth=None):
        """Return where-used closure rows for an item, optionally capped."""
        domain = [('descendant_id', '=', item_id)]
        if max_depth is not None:
            domain.append(('depth', '<=', max_depth))
        return self.search(domain)

    @api.model
    def explode(self, ebom_id, max_depth=None):
        """Return all closure rows of a single EBOM, optionally capped."""
        domain = [('ebom_id', '=', ebom_id)]
        if max_depth is not None:
            domain.append(('depth', '<=', max_depth))
        return self.search(domain)
