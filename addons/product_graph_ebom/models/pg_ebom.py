# -*- coding: utf-8 -*-
"""
pg.ebom — Engineering BOM header.

A pg.ebom is the engineering structure of an assembly: a list of pg.ebom.line
records pointing at child pg.item records, scoped to a specific pg.revision
of the root assembly.

State machine (Bible §3):
    draft → review → released → superseded

An EBOM may not transition to ``released`` until ALL of its child line items
(pg.ebom.line.pg_item_id) are themselves in ``released`` state. This is the
core cross-model gate (Bible §10).

On release the EBOM rebuilds its transitive-closure rows in pg.ebom.closure
so that downstream where-used / BOM-explosion queries are O(rows-returned)
rather than O(tree-walk).
"""
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


EBOM_STATES = [
    ('draft', 'Draft'),
    ('review', 'Under Review'),
    ('released', 'Released'),
    ('superseded', 'Superseded'),
]

# Backwards-compat alias for any sibling code that historically reached in
# for the transition map. The authoritative source is now
# LIFECYCLE_TRANSITIONS on PgEbom (provided by pg.lifecycle.mixin).
_EBOM_TRANSITIONS = {
    'draft':      ['review'],
    'review':     ['released', 'draft'],
    'released':   ['superseded'],
    'superseded': [],
}


class PgEbom(models.Model):
    _name = 'pg.ebom'
    _description = 'ProductGraph Engineering BOM'
    _inherit = ['pg.lifecycle.mixin', 'mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    # ─── Lifecycle mixin config ──────────────────────────────────────────────
    LIFECYCLE_STATES = EBOM_STATES
    LIFECYCLE_TRANSITIONS = _EBOM_TRANSITIONS
    LIFECYCLE_BYPASS_KEY = '_pg_release_bypass'
    LIFECYCLE_MUTABLE_LOCKED = frozenset({
        'state', 'active',
        'message_ids', 'message_follower_ids', 'activity_ids',
    })
    # Both 'released' and 'superseded' must be locked. 'released' has
    # transitions to 'superseded' so the auto-inferred terminal-set logic
    # would skip it — declare explicitly to preserve the prior write() guard.
    LIFECYCLE_TERMINAL_STATES = frozenset({'released', 'superseded'})

    # ─── Identity ────────────────────────────────────────────────────────────
    name = fields.Char(
        string='EBOM Reference',
        required=True,
        copy=False,
        index=True,
        default=lambda self: _('New'),
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)

    root_item_id = fields.Many2one(
        'pg.item',
        string='Root Assembly',
        required=True,
        ondelete='restrict',
        index=True,
        tracking=True,
        domain="[('item_type','in',('assembly','part'))]",
    )
    revision_id = fields.Many2one(
        'pg.revision',
        string='Item Revision',
        required=True,
        ondelete='restrict',
        index=True,
        tracking=True,
        # draft/review allowed so an EBOM can be drafted alongside its
        # parent revision; the real gate is in action_release().
        domain="[('item_id','=',root_item_id),"
               "('state','in',('draft','review','released','superseded'))]",
    )

    # ─── Lifecycle ───────────────────────────────────────────────────────────
    state = fields.Selection(
        EBOM_STATES,
        string='Status',
        default='draft',
        required=True,
        copy=False,
        index=True,
        tracking=True,
    )

    ebom_type = fields.Selection(
        [
            ('production', 'Production'),
            ('prototype', 'Prototype'),
        ],
        string='EBOM Type',
        default='production',
        required=True,
        tracking=True,
    )

    # ─── Lines ───────────────────────────────────────────────────────────────
    ebom_line_ids = fields.One2many(
        'pg.ebom.line',
        'ebom_id',
        string='Components',
        copy=True,
    )
    # store=True is safe: the dependency is a one2many of pg.ebom.line whose
    # write/unlink call super(); ORM invalidation on the o2m triggers recompute.
    line_count = fields.Integer(
        compute='_compute_line_count',
        string='# Lines',
        store=True,
    )

    # ─── Release metadata (stamped on release) ───────────────────────────────
    effective_date = fields.Date(string='Effective Date', readonly=True, copy=False)
    released_by_id = fields.Many2one(
        'res.users',
        string='Released By',
        readonly=True,
        copy=False,
    )
    release_timestamp = fields.Datetime(string='Released At', readonly=True, copy=False)
    successor_ebom_id = fields.Many2one(
        'pg.ebom',
        string='Superseded By',
        readonly=True,
        copy=False,
    )

    description = fields.Html(string='Description / Notes')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(related='root_item_id.company_id', store=True, readonly=True)

    _root_revision_unique = models.Constraint(
        'UNIQUE(root_item_id, revision_id)',
        'Only one EBOM per (root item, revision) is allowed.',
    )

    # ═════════════════════════════════════════════════════════════════════════
    # Computes
    # ═════════════════════════════════════════════════════════════════════════

    @api.depends('name', 'root_item_id.part_number', 'revision_id.name')
    def _compute_display_name(self):
        for rec in self:
            if rec.root_item_id and rec.revision_id:
                rec.display_name = (
                    f'{rec.name} · {rec.root_item_id.part_number} '
                    f'Rev {rec.revision_id.name}'
                )
            else:
                rec.display_name = rec.name or _('New EBOM')

    @api.depends('ebom_line_ids')
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.ebom_line_ids)

    # ═════════════════════════════════════════════════════════════════════════
    # ORM overrides
    # ═════════════════════════════════════════════════════════════════════════

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == _('New'):
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('pg.ebom') or _('New')
                )
        return super().create(vals_list)

    # write() — immutability enforcement provided by pg.lifecycle.mixin.
    # Additive (not a replacement): 'active' sits in LIFECYCLE_MUTABLE_LOCKED,
    # so archiving/unarchiving a released EBOM is a normal write the mixin's
    # guard allows through — but nothing downstream ever learns that active
    # changed. pg.ebom.closure rows are only otherwise touched by
    # action_release(), so an archived-but-released EBOM would silently keep
    # feeding where-used / BOM-explosion queries. Keep closure in sync here.
    def write(self, vals):
        if 'active' not in vals:
            return super().write(vals)

        new_active = bool(vals['active'])
        # Snapshot BEFORE the write lands: only records that are currently
        # 'released' AND whose active flag is actually flipping need closure
        # maintenance. A no-op write (e.g. mass "Set Active" re-applying
        # active=True to already-active records) must not rebuild anything.
        to_clear = self.filtered(
            lambda r: r.state == 'released' and r.active and not new_active
        )
        to_rebuild = self.filtered(
            lambda r: r.state == 'released' and not r.active and new_active
        )

        result = super().write(vals)

        Closure = self.env['pg.ebom.closure']
        for rec in to_clear:
            Closure._clear_for_ebom(rec)
        for rec in to_rebuild:
            Closure._build_for_ebom(rec)

        return result

    def unlink(self):
        for rec in self:
            if rec.state not in ('draft',):
                raise UserError(_(
                    "EBOM %s in state '%s' cannot be deleted.",
                    rec.display_name, rec.state,
                ))
        return super().unlink()

    # _check_transition_allowed / _log_state_change — provided by pg.lifecycle.mixin.

    # ═════════════════════════════════════════════════════════════════════════
    # Action buttons
    # ═════════════════════════════════════════════════════════════════════════

    def action_submit_for_review(self):
        if not self.env.user.has_group('product_graph_base.group_pg_engineer'):
            raise UserError(_("Only Engineers may submit EBOMs for review."))
        for rec in self:
            rec._check_transition_allowed('review')
            if not rec.ebom_line_ids:
                raise UserError(_(
                    "EBOM %s has no lines. Add components before submitting.",
                    rec.display_name,
                ))
            old = rec.state
            rec.write({'state': 'review'})
            rec._log_state_change(old, 'review')
            rec.message_post(body=_("EBOM submitted for review."))
        return True

    def action_validate(self):
        """Run pre-release validation without changing state.
        Returns a summary dict {ok: bool, errors: [...], warnings: [...]}."""
        self.ensure_one()
        errors = []
        warnings = []

        # All child items must be released
        non_released = self.ebom_line_ids.filtered(
            lambda l: l.pg_item_id.state != 'released'
        )
        if non_released:
            items = ', '.join(non_released.mapped('pg_item_id.display_name'))
            errors.append(_("Non-released child items: %s") % items)

        # Direct self-reference: a line's component cannot be the root item.
        for line in self.ebom_line_ids:
            if line.pg_item_id.id == self.root_item_id.id:
                errors.append(_(
                    "Item %s cannot be a component of its own BOM."
                ) % line.pg_item_id.display_name)

        # Duplicate-line detection at top level (excluding the self-ref case
        # above, which is reported with its own clearer message).
        seen = set()
        for line in self.ebom_line_ids:
            if line.pg_item_id.id == self.root_item_id.id:
                continue
            if line.pg_item_id.id in seen:
                errors.append(_(
                    "Component %s appears multiple times."
                ) % line.pg_item_id.display_name)
            seen.add(line.pg_item_id.id)

        # Transitive-cycle detection. The pg.ebom.closure table is built only
        # on release, so at validate-time we must walk released-EBOM children
        # of each line item ourselves. Depth-bounded DFS, set-based visited.
        for line in self.ebom_line_ids:
            if line.pg_item_id.id == self.root_item_id.id:
                continue  # already reported as direct self-ref
            cycle_path = self._walk_descendants_for_cycle(
                line.pg_item_id, self.root_item_id,
            )
            if cycle_path:
                # Prepend the root item so the displayed path begins with it.
                display_path = [self.root_item_id] + cycle_path
                errors.append(_(
                    "Adding %s would create a circular BOM: %s"
                ) % (
                    line.pg_item_id.display_name,
                    ' → '.join(i.display_name for i in display_path),
                ))

        # Alternate group integrity
        groups = {}
        for line in self.ebom_line_ids.filtered('alternate_group'):
            groups.setdefault(line.alternate_group, []).append(line)
        for grp, lines in groups.items():
            primaries = [l for l in lines if l.is_primary]
            if len(primaries) != 1:
                warnings.append(_(
                    "Alternate group %r should have exactly one primary line "
                    "(found %d).") % (grp, len(primaries)))

        return {'ok': not errors, 'errors': errors, 'warnings': warnings}

    def _walk_descendants_for_cycle(self, component_item, root_item,
                                    depth=0, path=None, visited=None):
        """DFS through released EBOMs descending from component_item, looking
        for a path back to root_item. Returns the offending path (list of
        pg.item) or None. Depth-capped at 30 to bound runaway graphs."""
        self.ensure_one()
        if depth > 30:
            return None
        visited = visited if visited is not None else set()
        if component_item.id in visited:
            # Already explored this branch via a different path; no new cycle here.
            return None
        visited = visited | {component_item.id}
        path = (path or []) + [component_item]
        if component_item.id == root_item.id:
            return path
        released_ebom = self.env['pg.ebom'].search([
            ('root_item_id', '=', component_item.id),
            ('state', '=', 'released'),
        ], limit=1)
        for child in released_ebom.ebom_line_ids:
            hit = self._walk_descendants_for_cycle(
                child.pg_item_id, root_item, depth + 1, path, visited,
            )
            if hit:
                return hit
        return None

    # ═════════════════════════════════════════════════════════════════════════
    # Constraints
    # ═════════════════════════════════════════════════════════════════════════

    @api.constrains('ebom_line_ids', 'root_item_id')
    def _check_no_direct_self_reference(self):
        """Eagerly block direct self-reference at save time. Transitive cycles
        are intentionally NOT checked here — the multi-level DFS is too
        expensive for every onchange/save and lives in action_validate()."""
        for rec in self:
            for line in rec.ebom_line_ids:
                if line.pg_item_id.id == rec.root_item_id.id:
                    raise ValidationError(_(
                        "Item %s cannot be a component of its own BOM."
                    ) % line.pg_item_id.display_name)

    def action_release(self):
        """Review → Released. Approver only. Rebuilds the closure."""
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise UserError(_("Only Approvers may release EBOMs."))
        for rec in self:
            rec._check_transition_allowed('released')
            # PG-103 gate: parent revision must itself be released (or already
            # superseded). Releasing an EBOM against a draft/review revision
            # would leave the revision without effective_date/released_by_id.
            if rec.revision_id.state not in ('released', 'superseded'):
                raise UserError(_(
                    "Cannot release EBOM %(ebom)s — parent revision %(rev)s "
                    "is in state %(state)s. Release the revision first.",
                    ebom=rec.display_name,
                    rev=rec.revision_id.display_name,
                    state=rec.revision_id.state,
                ))
            validation = rec.action_validate()
            if not validation['ok']:
                raise UserError(_(
                    "Cannot release EBOM %s:\n• %s",
                    rec.display_name,
                    '\n• '.join(validation['errors']),
                ))
            # Supersede the previous active EBOM for this root_item
            prev = self.search([
                ('root_item_id', '=', rec.root_item_id.id),
                ('state', '=', 'released'),
                ('id', '!=', rec.id),
            ])
            old = rec.state
            rec.with_context(_pg_release_bypass=True).write({
                'state': 'released',
                'effective_date': fields.Date.today(),
                'released_by_id': self.env.uid,
                'release_timestamp': fields.Datetime.now(),
            })
            rec._log_state_change(old, 'released')
            rec.message_post(body=_("EBOM released."))

            for prev_ebom in prev:
                prev_ebom.with_context(_pg_release_bypass=True).write({
                    'state': 'superseded',
                    'successor_ebom_id': rec.id,
                })
                prev_ebom._log_state_change(
                    'released', 'superseded',
                    notes=f'Superseded by {rec.display_name}',
                )
                prev_ebom.message_post(body=_(
                    "EBOM superseded by <b>%s</b>.", rec.display_name,
                ))
                # Clear old closure entries from the superseded EBOM
                self.env['pg.ebom.closure']._clear_for_ebom(prev_ebom)

            # Rebuild closure for the newly released EBOM
            self.env['pg.ebom.closure']._build_for_ebom(rec)
        return True

    def action_reject_to_draft(self):
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise UserError(_("Only Approvers may reject EBOMs."))
        for rec in self:
            rec._check_transition_allowed('draft')
            old = rec.state
            rec.write({'state': 'draft'})
            rec._log_state_change(old, 'draft', notes='EBOM rejected to draft.')
            rec.message_post(body=_("EBOM rejected back to Draft."))
        return True

    # ═════════════════════════════════════════════════════════════════════════
    # Where-used + BOM-explosion (entry points; closure does the heavy lifting)
    # ═════════════════════════════════════════════════════════════════════════

    def action_view_where_used(self):
        """Show all closure rows in which this EBOM's root item is a descendant."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Where Used — %s') % self.root_item_id.display_name,
            'res_model': 'pg.ebom.closure',
            'view_mode': 'list',
            'domain': [('descendant_id', '=', self.root_item_id.id)],
        }

    def action_view_explosion(self):
        """Show full multi-level explosion of this EBOM as closure rows."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('BOM Explosion — %s') % self.display_name,
            'res_model': 'pg.ebom.closure',
            'view_mode': 'list',
            'domain': [('ebom_id', '=', self.id)],
            'context': {'default_ebom_id': self.id},
        }
