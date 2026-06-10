# -*- coding: utf-8 -*-
"""pg.relationship.closure — Materialised transitive closure of the
relationship graph, per kind.

For every reachable pair (source_item, target_item) under a given kind,
one row exists with the shortest path's depth. The closure makes
multi-hop substitute / supersession queries O(rows-returned) instead of
O(graph-walk), the same pattern as ``pg.ebom.closure``.

Row semantics
-------------

* ``source_item_id`` — the starting item of the traversal
* ``target_item_id`` — the item reachable from source
* ``kind`` — the edge kind being traversed
* ``depth`` — number of edges traversed (always >= 1)

Symmetric kinds (substitutes / alternates)
------------------------------------------

For symmetric kinds the closure materialises both directions so any
query against the closure is a one-direction lookup. We store
``(a, b, kind)`` AND ``(b, a, kind)``.

Maintenance
-----------

The closure is rebuilt **per kind** in response to any pg.relationship
create / write / unlink that touches that kind. Rebuild is the full
relevant subgraph — O(active edges of that kind). For typical
engineering catalogs (hundreds-to-thousands of edges per kind) this is
sub-second.

The closure is never written by user code directly — the only ORM
path that creates closure rows is the rebuild method below.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Symmetric kinds need both directions materialised. Keep in sync with
# pg_relationship.SYMMETRIC_KINDS — this is the same set, duplicated here
# only so importing pg_relationship_closure doesn't require importing
# pg_relationship to access the constant.
SYMMETRIC_KINDS = frozenset({'substitutes', 'alternates'})


class PgRelationshipClosure(models.Model):
    _name = 'pg.relationship.closure'
    _description = 'ProductGraph Relationship Closure'
    _order = 'kind, source_item_id, depth, target_item_id'

    source_item_id = fields.Many2one(
        'pg.item', string='From',
        required=True, ondelete='cascade', index=True,
    )
    target_item_id = fields.Many2one(
        'pg.item', string='To',
        required=True, ondelete='cascade', index=True,
    )
    kind = fields.Selection(
        [
            ('substitutes', 'Substitutes'),
            ('alternates', 'Alternates'),
            ('supersedes', 'Supersedes'),
            ('references', 'References'),
            ('related', 'Related'),
        ],
        string='Kind', required=True, index=True,
    )
    depth = fields.Integer(string='Depth', required=True, index=True)

    _closure_unique = models.Constraint(
        'UNIQUE(source_item_id, target_item_id, kind)',
        'Closure row must be unique per (source, target, kind).',
    )

    def init(self):
        """Composite indexes for the two hottest query shapes."""
        cr = self.env.cr
        cr.execute("""
            CREATE INDEX IF NOT EXISTS pg_relationship_closure_kind_source_idx
            ON pg_relationship_closure (kind, source_item_id, depth);
        """)
        cr.execute("""
            CREATE INDEX IF NOT EXISTS pg_relationship_closure_kind_target_idx
            ON pg_relationship_closure (kind, target_item_id, depth);
        """)

    # ──────────────────────────────────────────────────────────────────
    # Rebuild
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def _rebuild_kind(self, kind: str, max_depth: int = 200) -> int:
        """Rebuild the closure rows for a single kind, entirely in
        Postgres via a recursive CTE.

        The previous Python-BFS + executemany approach was O(rows) Python
        work plus O(rows) round-trips for the INSERT — at 500 items /
        ~250k closure rows it was minutes. The CTE keeps every row in
        Postgres' query planner and executes one INSERT INTO ... SELECT.

        Returns the row count. Wipes existing rows for the kind first so
        the operation is idempotent.
        """
        cr = self.env.cr
        user_id = self.env.user.id or 1

        # Wipe stale rows.
        cr.execute(
            "DELETE FROM pg_relationship_closure WHERE kind = %s",
            (kind,),
        )

        # For symmetric kinds, the edge set is doubled in the seed CTE
        # so the walk goes both directions naturally.
        symmetric = kind in SYMMETRIC_KINDS
        edge_select = """
            SELECT source_item_id, target_item_id
              FROM pg_relationship
             WHERE kind = %(kind)s AND active = TRUE
        """
        if symmetric:
            edge_select += """
            UNION
            SELECT target_item_id, source_item_id
              FROM pg_relationship
             WHERE kind = %(kind)s AND active = TRUE
            """

        # UNION ALL + array-tracked visited set is the idiomatic
        # Postgres recursive graph traversal. UNION (with dedup at
        # each iteration) churns through O(N^2) comparisons; UNION ALL
        # with a visited-array filter is O(N * avg_degree * depth).
        # The path array also doubles as the cycle guard for symmetric
        # kinds where A→B→A would otherwise loop forever.
        sql = f"""
            WITH RECURSIVE edges AS (
                {edge_select}
            ),
            walk AS (
                SELECT source_item_id AS source_id,
                       target_item_id AS target_id,
                       1 AS depth,
                       ARRAY[source_item_id, target_item_id] AS path
                  FROM edges
                UNION ALL
                SELECT w.source_id,
                       e.target_item_id AS target_id,
                       w.depth + 1,
                       w.path || e.target_item_id
                  FROM walk w
                  JOIN edges e ON e.source_item_id = w.target_id
                 WHERE w.depth < %(max_depth)s
                   AND NOT (e.target_item_id = ANY(w.path))
            )
            INSERT INTO pg_relationship_closure
                (source_item_id, target_item_id, kind, depth,
                 create_uid, write_uid, create_date, write_date)
            SELECT source_id, target_id, %(kind)s, MIN(depth),
                   %(uid)s, %(uid)s, NOW(), NOW()
              FROM walk
             GROUP BY source_id, target_id
        """
        cr.execute(sql, {
            'kind': kind,
            'max_depth': max_depth,
            'uid': user_id,
        })
        row_count = cr.rowcount
        self.env.invalidate_all()
        if row_count:
            _logger.info(
                'pg.relationship.closure: rebuilt %d rows for kind=%s',
                row_count, kind,
            )
        return row_count

    @api.model
    def _rebuild_all(self) -> dict[str, int]:
        out = {}
        for kind in ('substitutes', 'alternates', 'supersedes',
                     'references', 'related'):
            out[kind] = self._rebuild_kind(kind)
        return out

    # ──────────────────────────────────────────────────────────────────
    # Query API
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def get_reachable(self, item, kind: str, max_depth: int | None = None):
        """Return the pg.item recordset reachable from `item` under `kind`."""
        domain = [
            ('source_item_id', '=', item.id),
            ('kind', '=', kind),
        ]
        if max_depth is not None:
            domain.append(('depth', '<=', max_depth))
        rows = self.search(domain)
        return rows.mapped('target_item_id')

    @api.model
    def substitute_set(self, item):
        """The full transitive substitute set for `item` (symmetric)."""
        return self.get_reachable(item, 'substitutes')

    @api.model
    def supersession_chain(self, item, direction: str = 'forward'):
        """Walk the supersession chain.

        ``direction='forward'`` returns items that supersede ``item``
        (newer in lineage). ``direction='back'`` returns items ``item``
        supersedes (older). Order returned is by depth ascending — closest
        first.
        """
        if direction == 'forward':
            rows = self.search(
                [('source_item_id', '=', item.id),
                 ('kind', '=', 'supersedes')],
                order='depth',
            )
        else:
            rows = self.search(
                [('target_item_id', '=', item.id),
                 ('kind', '=', 'supersedes')],
                order='depth',
            )
        return rows.mapped('source_item_id' if direction == 'back'
                           else 'target_item_id')
