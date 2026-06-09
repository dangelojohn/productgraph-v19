# -*- coding: utf-8 -*-
"""
pg.item — ProductGraph Engineering Item

The central registry record for every engineering item managed by ProductGraph.
An item has a lifecycle independent of Odoo's product.product until a revision
is formally released, at which point a product.product (and mrp.bom) are created
or linked under strict governance rules.

State Machine
-------------
draft → engineering → released → obsolete

Immutability Rules (Architecture Bible §3.1)
--------------------------------------------
- Items in state 'released' are IMMUTABLE. Write attempts raise UserError.
- Obsolescence is blocked when open MOs reference this item.
- The item auto-transitions to 'released' when its first revision is released.
- Only pg.group_engineer or higher may move items out of 'draft'.
"""
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

_ITEM_TRANSITIONS = {
    'draft':       ['engineering'],
    'engineering': ['released', 'draft'],
    'released':    ['obsolete'],
    'obsolete':    [],
}

_MUTABLE_ON_RELEASED = frozenset({
    'state', 'active', 'color', 'tag_ids',
    'message_follower_ids', 'message_ids', 'activity_ids',
    'obsolete_date', 'internal_notes', 'manufacturing_notes',
})


class PgItem(models.Model):
    _name = 'pg.item'
    _description = 'ProductGraph Engineering Item'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'item_number asc'
    _rec_name = 'display_name_full'

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    item_number = fields.Char(
        string='Item Number',
        required=True,
        copy=False,
        index=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('pg.item'),
        tracking=True,
    )
    name = fields.Char(
        string='Item Name',
        required=True,
        tracking=True,
    )
    display_name_full = fields.Char(
        string='Full Name',
        compute='_compute_display_name_full',
        store=True,
        recursive=True,
    )
    description = fields.Html(string='Description')
    category_id = fields.Many2one(
        'pg.category',
        string='Engineering Category',
        required=True,
        index=True,
        tracking=True,
    )
    item_type = fields.Selection([
        ('manufactured', 'Manufactured'),
        ('purchased',    'Purchased'),
        ('phantom',      'Phantom / Virtual'),
        ('service',      'Service'),
        ('document',     'Document / Software'),
    ], string='Item Type', required=True, default='manufactured', tracking=True)

    # ------------------------------------------------------------------
    # Lifecycle state
    # ------------------------------------------------------------------
    state = fields.Selection([
        ('draft',       'Draft'),
        ('engineering', 'In Engineering'),
        ('released',    'Released'),
        ('obsolete',    'Obsolete'),
    ], string='Status', default='draft', required=True,
       copy=False, index=True, tracking=True,
       group_expand='_group_expand_states',
    )

    # ------------------------------------------------------------------
    # Revision relationships
    # ------------------------------------------------------------------
    revision_ids = fields.One2many('pg.revision', 'item_id', string='Revisions')
    revision_count = fields.Integer(compute='_compute_revision_count', string='Revisions')
    current_revision_id = fields.Many2one(
        'pg.revision',
        string='Current Released Revision',
        compute='_compute_current_revision',
        store=True,
        index=True,
    )
    current_revision_name = fields.Char(
        related='current_revision_id.revision_code',
        string='Rev',
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Odoo product linkage (populated on first release)
    # ------------------------------------------------------------------
    product_id = fields.Many2one(
        'product.product',
        string='Linked Product',
        copy=False,
        readonly=True,
        index=True,
        tracking=True,
        help='Populated automatically when a revision is first released.',
    )
    product_template_id = fields.Many2one(
        related='product_id.product_tmpl_id',
        string='Product Template',
        readonly=True,
        store=True,
    )

    # ------------------------------------------------------------------
    # Manufacturing linkage (read-only; mrp owns this data)
    # ------------------------------------------------------------------
    bom_count = fields.Integer(compute='_compute_bom_count', string='BOMs')
    mo_count  = fields.Integer(compute='_compute_mo_count',  string='MOs')

    # ------------------------------------------------------------------
    # Digital Twin stub (Phase 2 — reserved schema path)
    # ------------------------------------------------------------------
    asset_ids   = fields.One2many('pg.asset', 'item_id', string='Installed Assets')
    asset_count = fields.Integer(compute='_compute_asset_count', string='Assets')

    # ------------------------------------------------------------------
    # Change requests
    # ------------------------------------------------------------------
    change_request_ids   = fields.One2many('pg.change.request', 'item_id', string='Change Requests')
    change_request_count = fields.Integer(compute='_compute_cr_count', string='Change Requests')

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------
    released_date   = fields.Datetime(string='First Released', readonly=True, copy=False)
    obsolete_date   = fields.Datetime(string='Obsoleted',      readonly=True, copy=False)
    responsible_id  = fields.Many2one(
        'res.users',
        string='Responsible Engineer',
        default=lambda self: self.env.user,
        index=True,
        tracking=True,
    )
    reviewer_id = fields.Many2one(
        'res.users',
        string='Assigned Reviewer',
        index=True,
        tracking=True,
    )
    image_1920 = fields.Image(string='Item Image', max_width=1920, max_height=1920)
    tag_ids = fields.Many2many(
        'pg.item.tag',
        'pg_item_tag_rel',
        'item_id', 'tag_id',
        string='Tags',
    )
    active = fields.Boolean(default=True)
    color  = fields.Integer(string='Color Index')

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------
    internal_notes      = fields.Html(string='Internal Notes')
    manufacturing_notes = fields.Html(string='Manufacturing Notes')

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    _sql_constraints = [
        ('item_number_unique', 'UNIQUE(item_number)',
         'Item Number must be unique across all items.'),
    ]

    # ================================================================
    # Computes
    # ================================================================

    @api.depends('item_number', 'name')
    def _compute_display_name_full(self):
        for rec in self:
            rec.display_name_full = (
                f'[{rec.item_number}] {rec.name}' if rec.item_number else (rec.name or '')
            )

    @api.depends('revision_ids')
    def _compute_revision_count(self):
        for rec in self:
            rec.revision_count = len(rec.revision_ids)

    @api.depends('revision_ids.state', 'revision_ids.sequence')
    def _compute_current_revision(self):
        for rec in self:
            released = rec.revision_ids.filtered(
                lambda r: r.state == 'released'
            ).sorted('sequence', reverse=True)
            rec.current_revision_id = released[:1] if released else False

    def _compute_bom_count(self):
        BomModel = self.env['mrp.bom']
        for rec in self:
            rec.bom_count = BomModel.search_count([
                ('product_tmpl_id', '=', rec.product_template_id.id)
            ]) if rec.product_template_id else 0

    def _compute_mo_count(self):
        MoModel = self.env['mrp.production']
        for rec in self:
            rec.mo_count = MoModel.search_count([
                ('product_id', '=', rec.product_id.id)
            ]) if rec.product_id else 0

    def _compute_asset_count(self):
        for rec in self:
            rec.asset_count = len(rec.asset_ids)

    def _compute_cr_count(self):
        for rec in self:
            rec.change_request_count = len(rec.change_request_ids)

    @staticmethod
    def _group_expand_states(states, domain, order):
        return ['draft', 'engineering', 'released', 'obsolete']

    # ================================================================
    # ORM overrides — immutability enforcement
    # ================================================================

    def write(self, vals):
        for rec in self:
            if rec.state == 'released':
                disallowed = set(vals.keys()) - _MUTABLE_ON_RELEASED
                if disallowed:
                    raise UserError(_(
                        "Item %(name)s is Released and immutable.\n"
                        "Fields %(fields)s cannot be changed directly.\n"
                        "Please create an Engineering Change Request (ECR) to "
                        "propose modifications.",
                        name=rec.display_name_full,
                        fields=', '.join(sorted(disallowed)),
                    ))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state not in ('draft', 'obsolete'):
                raise UserError(_(
                    "Item %s cannot be deleted in state '%s'.\n"
                    "Only Draft or Obsolete items may be deleted.",
                    rec.display_name_full, rec.state,
                ))
        return super().unlink()

    # ================================================================
    # State transition helpers
    # ================================================================

    def _check_transition_allowed(self, target_state):
        allowed = _ITEM_TRANSITIONS.get(self.state, [])
        if target_state not in allowed:
            raise UserError(_(
                "Cannot move item %(name)s from '%(from)s' to '%(to)s'.\n"
                "Allowed transitions: %(allowed)s",
                name=self.display_name_full,
                **{'from': self.state},
                to=target_state,
                allowed=', '.join(allowed) if allowed else 'none',
            ))

    # ================================================================
    # Action buttons
    # ================================================================

    def action_to_engineering(self):
        for rec in self:
            rec._check_transition_allowed('engineering')
            if not self.env.user.has_group('product_graph.group_pg_engineer'):
                raise UserError(_("Only Engineers may move items to 'In Engineering'."))
            rec.write({'state': 'engineering'})
            rec._log_state_change('draft', 'engineering')
        return True

    def action_reset_to_draft(self):
        for rec in self:
            rec._check_transition_allowed('draft')
            if rec.revision_ids.filtered(lambda r: r.state not in ('draft', 'obsolete')):
                raise UserError(_(
                    "Cannot reset to Draft — item has active revisions.\n"
                    "Obsolete all revisions first."
                ))
            rec.write({'state': 'draft'})
            rec._log_state_change('engineering', 'draft')
        return True

    def action_obsolete(self):
        for rec in self:
            rec._check_transition_allowed('obsolete')
            rec._validate_obsolete_preconditions()
            rec.write({
                'state': 'obsolete',
                'obsolete_date': fields.Datetime.now(),
                'active': False,
            })
            rec._log_state_change('released', 'obsolete')
            for rev in rec.revision_ids.filtered(lambda r: r.state == 'released'):
                rev._force_obsolete_from_item()
        return True

    def _validate_obsolete_preconditions(self):
        """Raise UserError if open MOs block obsolescence (Manufacturing Governance §3.1)."""
        if not self.product_id:
            return
        open_mos = self.env['mrp.production'].search([
            ('product_id', '=', self.product_id.id),
            ('state', 'in', ('draft', 'confirmed', 'progress')),
        ])
        if open_mos:
            raise UserError(_(
                "Cannot obsolete %(item)s — open Manufacturing Orders exist:\n%(mos)s\n\n"
                "Close or cancel those MOs first.",
                item=self.display_name_full,
                mos=', '.join(open_mos.mapped('name')),
            ))
        active_boms = self.env['mrp.bom'].search([
            ('product_tmpl_id', '=', self.product_template_id.id),
            ('active', '=', True),
        ]) if self.product_template_id else self.env['mrp.bom']
        if active_boms:
            raise UserError(_(
                "Cannot obsolete %(item)s — active BOMs still exist:\n%(boms)s\n\n"
                "Archive the BOMs first.",
                item=self.display_name_full,
                boms=', '.join(active_boms.mapped('display_name')),
            ))

    def _auto_release_from_revision(self, revision):
        """Auto-transition item to 'released' when its first revision is released."""
        self.ensure_one()
        if self.state == 'engineering':
            self.write({
                'state': 'released',
                'released_date': fields.Datetime.now(),
            })
            self._log_state_change('engineering', 'released', triggered_by=revision)
            _logger.info(
                'pg.item %s auto-transitioned to released by pg.revision %s',
                self.item_number, revision.revision_code,
            )

    def _log_state_change(self, from_state, to_state, triggered_by=None):
        self.env['pg.audit.log'].sudo().create({
            'item_id': self.id,
            'action': 'state_change',
            'from_state': from_state,
            'to_state': to_state,
            'user_id': self.env.uid,
            'triggered_by_model':  triggered_by._name if triggered_by else False,
            'triggered_by_res_id': triggered_by.id   if triggered_by else False,
            'notes': f'State: {from_state} → {to_state}',
        })

    # ================================================================
    # Smart buttons / actions
    # ================================================================

    def action_view_revisions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Revisions of %s') % self.display_name_full,
            'res_model': 'pg.revision',
            'view_mode': 'list,form',
            'domain': [('item_id', '=', self.id)],
            'context': {'default_item_id': self.id},
        }

    def action_create_revision(self):
        self.ensure_one()
        if self.state not in ('engineering', 'released'):
            raise UserError(_(
                "Item must be 'In Engineering' or 'Released' to create a revision."
            ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Revision'),
            'res_model': 'pg.revision',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_item_id': self.id,
                'default_revision_code': self._next_revision_code(),
            },
        }

    def action_view_boms(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('BOMs'),
            'res_model': 'mrp.bom',
            'view_mode': 'list,form',
            'domain': [('product_tmpl_id', '=', self.product_template_id.id)],
        }

    def action_view_mos(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Manufacturing Orders'),
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            'domain': [('product_id', '=', self.product_id.id)],
        }

    def action_create_change_request(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Change Request'),
            'res_model': 'pg.change.request',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_item_id': self.id},
        }

    # ================================================================
    # Helpers
    # ================================================================

    def _next_revision_code(self):
        """Return next alphabetical revision code (A → B → … → Z → AA → AB …)."""
        existing = self.revision_ids.mapped('revision_code')
        if not existing:
            return 'A'

        def _code_to_num(code):
            n = 0
            for ch in code.upper():
                n = n * 26 + (ord(ch) - ord('A') + 1)
            return n

        def _num_to_code(n):
            code = ''
            while n > 0:
                n, r = divmod(n - 1, 26)
                code = chr(r + ord('A')) + code
            return code

        return _num_to_code(max(_code_to_num(c) for c in existing) + 1)

    @api.model
    def _name_search(self, name='', domain=None, operator='ilike', limit=100, order=None):
        domain = domain or []
        if name:
            domain = ['|', ('item_number', operator, name), ('name', operator, name)] + domain
        return self._search(domain, limit=limit, order=order)
