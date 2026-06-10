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
        """Rebuild the closure rows for a single kind.

        Returns the row count. Wipes existing rows for the kind first
        so the operation is idempotent.
        """
        Rel = self.env['pg.relationship']
        # Read raw edge tuples in one query instead of touching each
        # recordset field — for big graphs this matters.
        edge_rows = Rel.search_read(
            [('kind', '=', kind), ('active', '=', True)],
            ['source_item_id', 'target_item_id'],
        )

        # Adjacency: source -> {targets}. For symmetric kinds, also add the
        # reverse so the BFS walks both directions naturally.
        adj: dict[int, set[int]] = defaultdict(set)
        for row in edge_rows:
            # search_read returns the M2O as [id, name]; index 0 is the id.
            src = row['source_item_id'][0] if isinstance(row['source_item_id'], list) else row['source_item_id']
            tgt = row['target_item_id'][0] if isinstance(row['target_item_id'], list) else row['target_item_id']
            adj[src].add(tgt)
            if kind in SYMMETRIC_KINDS:
                adj[tgt].add(src)

        # BFS from every node that has outgoing edges. Closure semantics:
        # (source, target, depth) where depth is the shortest hop count.
        rows: list[dict] = []
        for source_id in list(adj.keys()):
            visited = {source_id: 0}
            queue = deque([source_id])
            while queue:
                cur = queue.popleft()
                cur_depth = visited[cur]
                if cur_depth >= max_depth:
                    continue
                for nxt in adj[cur]:
                    if nxt in visited:
                        continue
                    visited[nxt] = cur_depth + 1
                    queue.append(nxt)
            for target_id, depth in visited.items():
                if depth == 0:
                    continue  # skip the source itself
                rows.append({
                    'source_item_id': source_id,
                    'target_item_id': target_id,
                    'kind': kind,
                    'depth': depth,
                })

        # Wipe stale rows and write fresh. Use raw SQL for both steps —
        # the closure table has no compute / tracking and is internal-
        # only (rebuilt deterministically), so going through the ORM
        # buys nothing and costs O(rows) time for chatter / cache work.
        cr = self.env.cr
        cr.execute(
            "DELETE FROM pg_relationship_closure WHERE kind = %s",
            (kind,),
        )
        if rows:
            user_id = self.env.user.id or 1
            # psycopg2 execute_values would be ideal; the cursor wrapper
            # supports executemany so we batch through that.
            cr.executemany(
                "INSERT INTO pg_relationship_closure "
                "(source_item_id, target_item_id, kind, depth, "
                " create_uid, write_uid, create_date, write_date) "
                "VALUES (%(source_item_id)s, %(target_item_id)s, "
                " %(kind)s, %(depth)s, "
                f" {user_id}, {user_id}, NOW(), NOW())",
                rows,
            )
            self.env.invalidate_all()
            _logger.info(
                'pg.relationship.closure: rebuilt %d rows for kind=%s',
                len(rows), kind,
            )
        return len(rows)

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
