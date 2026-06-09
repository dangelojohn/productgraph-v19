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

_EBOM_TRANSITIONS = {
    'draft':      ['review'],
    'review':     ['released', 'draft'],
    'released':   ['superseded'],
    'superseded': [],
}

_MUTABLE_ON_LOCKED = frozenset({
    'state', 'active',
    'message_ids', 'message_follower_ids', 'activity_ids',
})


class PgEbom(models.Model):
    _name = 'pg.ebom'
    _description = 'ProductGraph Engineering BOM'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

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
        domain="[('item_id','=',root_item_id)]",
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
    line_count = fields.Integer(compute='_compute_line_count', string='# Lines')

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

    _sql_constraints = [
        ('root_revision_unique',
         'UNIQUE(root_item_id, revision_id)',
         'Only one EBOM per (root item, revision) is allowed.'),
    ]

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

    def write(self, vals):
        bypass = self.env.context.get('_pg_release_bypass')
        for rec in self:
            if rec.state in ('released', 'superseded') and not bypass:
                blocked = set(vals.keys()) - _MUTABLE_ON_LOCKED
                if blocked:
                    raise UserError(_(
                        "EBOM %(name)s is %(state)s and immutable.\n"
                        "Blocked fields: %(fields)s.",
                        name=rec.display_name,
                        state=rec.state,
                        fields=', '.join(sorted(blocked)),
                    ))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state not in ('draft',):
                raise UserError(_(
                    "EBOM %s in state '%s' cannot be deleted.",
                    rec.display_name, rec.state,
                ))
        return super().unlink()

    # ═════════════════════════════════════════════════════════════════════════
    # Transition helpers
    # ═════════════════════════════════════════════════════════════════════════

    def _check_transition_allowed(self, target):
        self.ensure_one()
        allowed = _EBOM_TRANSITIONS.get(self.state, [])
        if target not in allowed:
            raise UserError(_(
                "Cannot move EBOM %(name)s from '%(from)s' to '%(to)s'.\n"
                "Allowed: %(allowed)s",
                name=self.display_name,
                **{'from': self.state},
                to=target,
                allowed=', '.join(allowed) or _('none — terminal'),
            ))

    def _log_state_change(self, from_state, to_state, notes=None):
        self.env['pg.audit.log']._log(
            self,
            action='state_change',
            from_state=from_state,
            to_state=to_state,
            notes=notes,
        )

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

        # Recursion: a root item should not appear in its own descendants
        seen = {self.root_item_id.id}
        for line in self.ebom_line_ids:
            if line.pg_item_id.id in seen:
                errors.append(_("Component %s appears multiple times at top level.")
                              % line.pg_item_id.display_name)
            seen.add(line.pg_item_id.id)

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

    def action_release(self):
        """Review → Released. Approver only. Rebuilds the closure."""
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise UserError(_("Only Approvers may release EBOMs."))
        for rec in self:
            rec._check_transition_allowed('released')
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
