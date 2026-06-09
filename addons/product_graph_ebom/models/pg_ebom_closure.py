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

_logger = logging.getLogger(__name__)


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

    _sql_constraints = [
        ('closure_unique',
         'UNIQUE(ebom_id, ancestor_id, descendant_id, depth)',
         'Closure row must be unique per (ebom, ancestor, descendant, depth).'),
    ]

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

    # ═════════════════════════════════════════════════════════════════════════
    # Public builders (called from pg.ebom.action_release)
    # ═════════════════════════════════════════════════════════════════════════

    @api.model
    def _build_for_ebom(self, ebom):
        """Build closure rows for the given EBOM.

        Walks the EBOM tree breadth-first. For each level, inserts one closure
        row per (ebom.root_item_id, descendant, depth, qty_chain).
        Recurses into any descendant that itself has a released EBOM, so a
        single closure rebuild captures the multi-level structure as one
        flat table.
        """
        if ebom.state != 'released':
            _logger.info('Skipping closure build for non-released EBOM %s', ebom.display_name)
            return
        rows = []
        self._walk(
            ebom=ebom,
            ancestor_item=ebom.root_item_id,
            current_lines=ebom.ebom_line_ids,
            depth=1,
            chain_qty=1.0,
            visited={ebom.root_item_id.id},
            rows=rows,
        )
        if rows:
            # Wipe any stale rows for this EBOM first (idempotent rebuild).
            self.search([('ebom_id', '=', ebom.id)]).unlink()
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
    # Internal walker
    # ═════════════════════════════════════════════════════════════════════════

    @api.model
    def _walk(self, ebom, ancestor_item, current_lines, depth, chain_qty, visited, rows,
              max_depth=30):
        """Breadth-walk current_lines. Recurse into any descendant that has
        its own released EBOM."""
        if depth > max_depth:
            _logger.warning(
                'pg.ebom.closure: depth cap %d hit while walking %s',
                max_depth, ebom.display_name,
            )
            return
        for line in current_lines:
            child = line.pg_item_id
            if not child:
                continue
            new_qty = chain_qty * (line.product_qty or 0.0)
            rows.append({
                'ebom_id': ebom.id,
                'ancestor_id': ancestor_item.id,
                'descendant_id': child.id,
                'depth': depth,
                'qty_chain': new_qty,
            })
            # If the child has its own released EBOM, recurse into it.
            if child.id in visited:
                continue  # avoid cycles
            visited.add(child.id)
            child_ebom = self.env['pg.ebom'].search([
                ('root_item_id', '=', child.id),
                ('state', '=', 'released'),
            ], limit=1)
            if child_ebom:
                self._walk(
                    ebom=ebom,
                    ancestor_item=ancestor_item,
                    current_lines=child_ebom.ebom_line_ids,
                    depth=depth + 1,
                    chain_qty=new_qty,
                    visited=visited,
                    rows=rows,
                    max_depth=max_depth,
                )

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
