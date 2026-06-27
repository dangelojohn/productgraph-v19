# -*- coding: utf-8 -*-
"""
pg.item — ProductGraph Engineering Item Master.

The central registry record for every engineering item managed by
ProductGraph. An item has its own lifecycle independent of Odoo's product
catalog. A `product.product` is created or linked only when the item's first
revision is formally released (see ``product_graph_release``).

State machine (per docs/020_State_Machine_Bible.md §1):

    concept → prototype → engineering → released → service → obsolete

Authority matrix (§6):
    concept→prototype/engineering   : group_pg_engineer
    prototype→engineering           : group_pg_engineer
    engineering→released            : automatic (via pg.revision.action_release)
                                       OR group_pg_approver (manual override)
    released→service                : group_pg_approver (no open new-prod MOs)
    released→obsolete               : group_pg_admin   (no open MOs / POs / active BOMs)
    service→obsolete                : group_pg_admin

Released items are IMMUTABLE except via the controlled context-flag bypass
``_pg_release_bypass`` (used only by ``pg.release.action_execute_release``).
"""
import logging
from markupsafe import Markup
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)


# ─── State machine ───────────────────────────────────────────────────────────

ITEM_STATES = [
    ('concept', 'Concept'),
    ('prototype', 'Prototype'),
    ('engineering', 'In Engineering'),
    ('released', 'Released'),
    ('service', 'Service'),
    ('obsolete', 'Obsolete'),
]

# Allowed forward transitions per State Machine Bible §1.2.
# Reverse transitions are explicitly forbidden.
# Authoritative copy is now PgItem.LIFECYCLE_TRANSITIONS; module-level alias
# preserved for any sibling code that historically reached for it.
_ITEM_TRANSITIONS = {
    'concept':     ['prototype', 'engineering'],
    'prototype':   ['engineering'],
    'engineering': ['released'],
    'released':    ['service', 'obsolete'],
    'service':     ['obsolete'],
    'obsolete':    [],
}

# Fields safe to write on a released item without triggering immutability.
# Anything outside this set requires the _pg_release_bypass context flag,
# which is only set by pg.release.action_execute_release().
# Authoritative copy is PgItem.LIFECYCLE_MUTABLE_LOCKED.
_MUTABLE_ON_RELEASED = frozenset({
    'state',
    'service_date', 'obsolete_date', 'active',
    'color', 'tag_ids',
    'asset_template_category',
    'message_ids', 'message_follower_ids', 'activity_ids',
    'internal_notes', 'manufacturing_notes',
})

# Per-item-type sequence codes (Bible §7.1).
_ITEM_TYPE_SEQUENCE = {
    'assembly':  'pg.item.asm',
    'part':      'pg.item.prt',
    'purchased': 'pg.item.buy',
    'document':  'pg.item.doc',
    'software':  'pg.item.sft',
    'service':   'pg.item.svc',
}

ITEM_TYPES = [
    ('assembly',  'Assembly'),
    ('part',      'Manufactured Part'),
    ('purchased', 'Purchased Component'),
    ('document',  'Document'),
    ('software',  'Software'),
    ('service',   'Service'),
]


class PgItem(models.Model):
    _name = 'pg.item'
    _description = 'ProductGraph Engineering Item'
    _inherit = ['pg.lifecycle.mixin', 'mail.thread', 'mail.activity.mixin']
    _order = 'part_number asc'
    _rec_name = 'display_name'

    # ─── Lifecycle mixin config ──────────────────────────────────────────────
    LIFECYCLE_STATES = ITEM_STATES
    LIFECYCLE_TRANSITIONS = _ITEM_TRANSITIONS
    LIFECYCLE_BYPASS_KEY = '_pg_release_bypass'
    LIFECYCLE_MUTABLE_LOCKED = _MUTABLE_ON_RELEASED
    # pg.item has ASYMMETRIC locked-state rules: 'released' uses the
    # MUTABLE_ON_RELEASED allowlist, but 'obsolete' uses a stricter
    # chatter-only allowlist AND ignores the bypass. The mixin's single-set
    # model can't express that, so we keep the existing write() override
    # here and opt out of the mixin's enforcement.
    LIFECYCLE_ENFORCE_WRITE = False

    # ─── Identity ────────────────────────────────────────────────────────────
    part_number = fields.Char(
        string='Part Number',
        required=True,
        copy=False,
        index=True,
        tracking=True,
        default=lambda self: _('New'),
        help='System-assigned unique part number. Prefix derived from item type.',
    )
    name = fields.Char(string='Item Name', required=True, tracking=True)
    description = fields.Html(string='Description')

    display_name = fields.Char(compute='_compute_display_name', store=True)

    category_id = fields.Many2one(
        'pg.category',
        string='Engineering Category',
        required=True,
        index=True,
        tracking=True,
        ondelete='restrict',
    )
    item_type = fields.Selection(
        ITEM_TYPES,
        string='Item Type',
        required=True,
        default='part',
        tracking=True,
        help='Drives the part-number sequence prefix and the property templates '
             'available to this item.',
    )
    tag_ids = fields.Many2many(
        'pg.item.tag',
        'pg_item_tag_rel',
        'item_id', 'tag_id',
        string='Tags',
    )

    # ─── Lifecycle state ─────────────────────────────────────────────────────
    state = fields.Selection(
        ITEM_STATES,
        string='Status',
        default='concept',
        required=True,
        copy=False,
        index=True,
        tracking=True,
        group_expand='_group_expand_states',
    )

    # ─── Engineering details ─────────────────────────────────────────────────
    uom_id = fields.Many2one(
        'uom.uom',
        string='Unit of Measure',
        required=True,
        default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False),
        ondelete='restrict',
    )
    responsible_id = fields.Many2one(
        'res.users',
        string='Responsible Engineer',
        default=lambda self: self.env.user,
        index=True,
        tracking=True,
    )

    # ─── Odoo product linkage (set only at first Release) ────────────────────
    product_id = fields.Many2one(
        'product.product',
        string='Linked Product',
        copy=False,
        readonly=True,
        index=True,
        tracking=True,
        help='Populated by pg.release.action_execute_release on first release.',
    )
    product_tmpl_id = fields.Many2one(
        related='product_id.product_tmpl_id',
        string='Product Template',
        store=True,
        readonly=True,
    )

    # ─── Property templates (Decision D2) ────────────────────────────────────
    property_template_ids = fields.Many2many(
        'pg.property.template',
        'pg_item_property_template_rel',
        'item_id', 'template_id',
        string='Property Templates',
        domain="[('item_type','=',item_type),('active','=',True)]",
        help='Templates whose fields are available for value entry on this '
             'item\'s revisions. Only templates matching the item type may be '
             'attached.',
    )

    # ─── Phase 3 — Digital Twin reserved stub ───────────────────────────────
    asset_template_category = fields.Selection(
        [
            ('equipment', 'Equipment'),
            ('component', 'Component'),
            ('none', 'N/A'),
        ],
        string='Asset Category (Phase 3 stub)',
        default='none',
        help='Reserved for Digital Twin asset classification. Not used in Phase 1.',
    )

    # ─── Counts (smart-button targets) ───────────────────────────────────────
    revision_count = fields.Integer(compute='_compute_revision_count', string='Revisions')
    ebom_count = fields.Integer(compute='_compute_ebom_count', string='EBOMs')
    bom_count = fields.Integer(compute='_compute_bom_count', string='MRP BOMs')
    mo_count = fields.Integer(compute='_compute_mo_count', string='MOs')
    asset_count = fields.Integer(compute='_compute_asset_count', string='Assets')

    # ─── Lifecycle button gates (PG-105) ─────────────────────────────────────
    # UI-facing mirrors of _check_no_open_new_production / _check_no_open_references.
    # Server-side checks remain authoritative; these only drive button visibility.
    can_to_service = fields.Boolean(
        compute='_compute_lifecycle_buttons',
        string='Can Move to Service',
    )
    to_service_blocked_reason = fields.Char(
        compute='_compute_lifecycle_buttons',
        string='Service Block Reason',
    )
    can_to_obsolete = fields.Boolean(
        compute='_compute_lifecycle_buttons',
        string='Can Obsolete',
    )
    to_obsolete_blocked_reason = fields.Char(
        compute='_compute_lifecycle_buttons',
        string='Obsolete Block Reason',
    )

    # ─── PG-140 — One-click Promote precondition mirrors ────────────────────
    can_promote = fields.Boolean(
        compute='_compute_lifecycle_buttons',
        string='Can Promote',
    )
    promote_blocked_reason = fields.Char(
        compute='_compute_lifecycle_buttons',
        string='Promote Block Reason',
    )

    # ─── Notes & meta ────────────────────────────────────────────────────────
    internal_notes = fields.Html(string='Internal Notes')
    manufacturing_notes = fields.Html(string='Manufacturing Notes')
    image_1920 = fields.Image(string='Item Image', max_width=1920, max_height=1920)
    color = fields.Integer(string='Color Index')
    active = fields.Boolean(default=True)

    # ─── Dates ───────────────────────────────────────────────────────────────
    released_date = fields.Datetime(string='First Released', readonly=True, copy=False)
    service_date = fields.Datetime(string='Entered Service', readonly=True, copy=False)
    obsolete_date = fields.Datetime(string='Obsoleted', readonly=True, copy=False)

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        index=True,
        required=True,
    )

    _part_number_company_unique = models.Constraint(
        'UNIQUE(part_number, company_id)',
        'Part number must be unique per company.',
    )

    # ═════════════════════════════════════════════════════════════════════════
    # Computes
    # ═════════════════════════════════════════════════════════════════════════

    @api.depends('part_number', 'name')
    def _compute_display_name(self):
        for rec in self:
            if rec.part_number and rec.part_number != _('New'):
                rec.display_name = f'[{rec.part_number}] {rec.name or ""}'.strip()
            else:
                rec.display_name = rec.name or _('New Item')

    def _compute_revision_count(self):
        # Live-counted via reverse search; the One2many lives in _revision.
        Revision = self.env.get('pg.revision')
        for rec in self:
            rec.revision_count = (
                Revision.search_count([('item_id', '=', rec.id)]) if Revision is not None else 0
            )

    def _compute_ebom_count(self):
        Ebom = self.env.get('pg.ebom')
        for rec in self:
            rec.ebom_count = (
                Ebom.search_count([('root_item_id', '=', rec.id)]) if Ebom is not None else 0
            )

    def _compute_bom_count(self):
        for rec in self:
            rec.bom_count = self.env['mrp.bom'].search_count([
                ('product_tmpl_id', '=', rec.product_tmpl_id.id),
            ]) if rec.product_tmpl_id else 0

    def _compute_mo_count(self):
        for rec in self:
            rec.mo_count = self.env['mrp.production'].search_count([
                ('product_id', '=', rec.product_id.id),
            ]) if rec.product_id else 0

    def _compute_asset_count(self):
        for rec in self:
            rec.asset_count = self.env['pg.asset'].search_count([
                ('item_id', '=', rec.id),
            ])

    @api.depends('state', 'product_id', 'product_tmpl_id')
    def _compute_lifecycle_buttons(self):
        """Mirrors _check_no_open_new_production / _check_no_open_references.

        Server-side checks remain authoritative. This compute powers the UI:
        invisible-attrs on the lifecycle buttons + an inline reason banner.
        """
        MrpProduction = self.env.get('mrp.production')
        PoLine = self.env.get('purchase.order.line')
        MrpBomLine = self.env.get('mrp.bom.line')
        for rec in self:
            # ── Move-to-Service preconditions ───────────────────────────────
            if rec.state != 'released':
                rec.can_to_service = False
                rec.to_service_blocked_reason = _(
                    "Only Released items may move to Service."
                )
            elif rec.product_id and MrpProduction is not None:
                open_mos = MrpProduction.search_count([
                    ('product_id', '=', rec.product_id.id),
                    ('state', 'in', ('draft', 'confirmed', 'progress')),
                ])
                if open_mos:
                    rec.can_to_service = False
                    rec.to_service_blocked_reason = _(
                        "%d open Manufacturing Order(s) reference this item."
                    ) % open_mos
                else:
                    rec.can_to_service = True
                    rec.to_service_blocked_reason = False
            else:
                rec.can_to_service = True
                rec.to_service_blocked_reason = False

            # ── Obsolete preconditions ──────────────────────────────────────
            if rec.state not in ('released', 'service'):
                rec.can_to_obsolete = False
                rec.to_obsolete_blocked_reason = _(
                    "Only Released or Service items may be obsoleted."
                )
            else:
                errors = []
                if rec.product_id and MrpProduction is not None:
                    n_mo = MrpProduction.search_count([
                        ('product_id', '=', rec.product_id.id),
                        ('state', 'in', ('draft', 'confirmed', 'progress')),
                    ])
                    if n_mo:
                        errors.append(_('%d open MO(s)') % n_mo)
                if rec.product_id and PoLine is not None:
                    n_po = PoLine.search_count([
                        ('product_id', '=', rec.product_id.id),
                        ('order_id.state', 'in', ('draft', 'sent', 'to approve', 'purchase')),
                    ])
                    if n_po:
                        errors.append(_('%d open PO line(s)') % n_po)
                if rec.product_tmpl_id and MrpBomLine is not None:
                    n_bom = MrpBomLine.search_count([
                        ('product_id.product_tmpl_id', '=', rec.product_tmpl_id.id),
                        ('bom_id.active', '=', True),
                    ])
                    if n_bom:
                        errors.append(_('active in %d MRP BOM line(s)') % n_bom)
                if errors:
                    rec.can_to_obsolete = False
                    rec.to_obsolete_blocked_reason = _(
                        "Active references: %s"
                    ) % '; '.join(errors)
                else:
                    rec.can_to_obsolete = True
                    rec.to_obsolete_blocked_reason = False

            # ── Promote (PG-140) — reuses existing transition preconditions ─
            if rec.state == 'concept':
                rec.can_promote = True  # always allowed to go to prototype
                rec.promote_blocked_reason = False
            elif rec.state == 'prototype':
                rec.can_promote = True
                rec.promote_blocked_reason = False
            elif rec.state == 'engineering':
                rec.can_promote = False
                rec.promote_blocked_reason = _(
                    "Release happens by releasing a Revision, not by promoting the Item."
                )
            elif rec.state == 'released':
                rec.can_promote = rec.can_to_service
                rec.promote_blocked_reason = rec.to_service_blocked_reason or False
            elif rec.state == 'service':
                rec.can_promote = rec.can_to_obsolete
                rec.promote_blocked_reason = rec.to_obsolete_blocked_reason or False
            else:
                rec.can_promote = False
                rec.promote_blocked_reason = _("Item is in final state.")

    @staticmethod
    def _group_expand_states(states, domain, order):
        return [s[0] for s in ITEM_STATES]

    # ═════════════════════════════════════════════════════════════════════════
    # ORM overrides
    # ═════════════════════════════════════════════════════════════════════════

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('part_number') or vals.get('part_number') == _('New'):
                seq_code = _ITEM_TYPE_SEQUENCE.get(
                    vals.get('item_type', 'part'),
                    'pg.item.prt',
                )
                vals['part_number'] = (
                    self.env['ir.sequence'].next_by_code(seq_code) or _('New')
                )
        return super().create(vals_list)

    def write(self, vals):
        bypass = self.env.context.get('_pg_release_bypass')
        for rec in self:
            if rec.state == 'released' and not bypass:
                blocked = set(vals.keys()) - _MUTABLE_ON_RELEASED
                if blocked:
                    raise UserError(_(
                        "Item %(name)s is Released and immutable.\n"
                        "Blocked fields: %(fields)s.\n"
                        "Create a new revision to propose modifications.",
                        name=rec.display_name,
                        fields=', '.join(sorted(blocked)),
                    ))
            if rec.state == 'obsolete':
                # Obsolete is final — only chatter / activity / archival flag allowed.
                blocked = set(vals.keys()) - {
                    'active', 'message_ids', 'message_follower_ids', 'activity_ids',
                }
                if blocked:
                    raise UserError(_(
                        "Item %(name)s is Obsolete and permanently read-only.\n"
                        "Blocked fields: %(fields)s.",
                        name=rec.display_name,
                        fields=', '.join(sorted(blocked)),
                    ))
        # Item-type changes invalidate attached property templates.
        if 'item_type' in vals:
            for rec in self:
                if rec.property_template_ids and rec.item_type != vals['item_type']:
                    _logger.info(
                        'pg.item %s changed item_type — clearing %d property templates',
                        rec.part_number, len(rec.property_template_ids),
                    )
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state not in ('concept',):
                raise UserError(_(
                    "Item %(name)s in state '%(state)s' cannot be deleted.\n"
                    "Only Concept items may be hard-deleted; obsolete items "
                    "instead to preserve the audit trail.",
                    name=rec.display_name,
                    state=rec.state,
                ))
        return super().unlink()

    # _check_transition_allowed / _log_state_change — provided by pg.lifecycle.mixin.

    # ═════════════════════════════════════════════════════════════════════════
    # Action buttons
    # ═════════════════════════════════════════════════════════════════════════

    def action_to_prototype(self):
        if not self.env.user.has_group('product_graph_base.group_pg_engineer'):
            raise UserError(_("Only Engineers may move items to Prototype."))
        for rec in self:
            rec._check_transition_allowed('prototype')
            old = rec.state
            rec.write({'state': 'prototype'})
            rec._log_state_change(old, 'prototype')
            rec.message_post(body=_("Item promoted to Prototype."))
        return True

    def action_to_engineering(self):
        if not self.env.user.has_group('product_graph_base.group_pg_engineer'):
            raise UserError(_("Only Engineers may move items to Engineering."))
        for rec in self:
            rec._check_transition_allowed('engineering')
            old = rec.state
            rec.write({'state': 'engineering'})
            rec._log_state_change(old, 'engineering')
            rec.message_post(body=_("Item entered Engineering."))
        return True

    def action_force_release(self):
        """Open the Force Release justification wizard (PG-121).

        Permission and audit happen in the wizard's executor (`_do_force_release`).
        We check the gate here too so an unauthorised user can't even open the
        wizard from the UI.
        """
        self.ensure_one()
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise AccessError(_("Force Release requires the Approver role."))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Force Release: %s") % self.display_name,
            'res_model': 'pg.force.release.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_item_id': self.id,
            },
        }

    def _do_force_release(self):
        """Executor for Force Release — invoked by `pg.force.release.wizard`.

        Bypasses the normal transition matrix (that's the point of "force"),
        but writes a distinct `force_release` audit-log entry plus a warning
        chatter post so the bypass is forensically obvious.
        """
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise AccessError(_("Force Release requires the Approver role."))
        context_reason = self.env.context.get('_pg_force_release_reason', '')
        if not context_reason or len(context_reason.strip()) < 20:
            # Defence-in-depth — wizard already validates, but a direct API
            # caller must not be able to slip a no-justification force-release
            # through by setting an empty context flag.
            raise UserError(_(
                "Force Release requires a justification of at least 20 characters."
            ))
        for rec in self:
            prev_state = rec.state
            rec.write({
                'state': 'released',
                'released_date': fields.Datetime.now(),
            })
            self.env['pg.audit.log']._log(
                rec,
                action='force_release',
                from_state=prev_state,
                to_state='released',
                notes=_('Force-release bypassed normal gating. Reason: %s') % context_reason,
                payload={
                    'reason': context_reason,
                    'forced_by': self.env.user.name,
                    'previous_state': prev_state,
                    'ip_address': self.env.context.get('request_ip', 'unknown'),
                },
            )
            rec.message_post(
                body=Markup("<strong>&#9888; FORCE RELEASE</strong><br/>By: %s<br/>Reason: %s") % (
                    self.env.user.name, context_reason,
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        return True

    def action_to_service(self):
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise UserError(_("Only Approvers may transition items to Service."))
        for rec in self:
            rec._check_transition_allowed('service')
            rec._check_no_open_new_production()
            old = rec.state
            rec.write({
                'state': 'service',
                'service_date': fields.Datetime.now(),
            })
            rec._log_state_change(old, 'service')
            rec.message_post(body=_(
                "Item moved to Service. No new-production MOs may be created."
            ))
        return True

    # ─── PG-140 — One-click Promote ──────────────────────────────────────────
    def action_promote(self):
        """One-click advance to the next valid state. Reuses existing
        transitions + their guards. No new state logic."""
        self.ensure_one()
        PROMOTION_MAP = {
            'concept': 'action_to_prototype',
            'prototype': 'action_to_engineering',
            'engineering': None,  # release is revision-driven, not item-driven
            'released': 'action_to_service',
            'service': 'action_to_obsolete',
            'obsolete': None,
        }
        target_method = PROMOTION_MAP.get(self.state)
        if target_method is None:
            raise UserError(_(
                "%s cannot be promoted from %s. "
                "(Releasing happens by releasing a revision; archiving from "
                "Obsolete requires the unlink flow.)"
            ) % (
                self.display_name,
                dict(self._fields['state'].selection).get(self.state),
            ))
        return getattr(self, target_method)()

    def action_to_obsolete(self):
        if not self.env.user.has_group('product_graph_base.group_pg_admin'):
            raise UserError(_("Only Administrators may obsolete items."))
        for rec in self:
            rec._check_transition_allowed('obsolete')
            rec._check_no_open_references()
            old = rec.state
            rec.write({
                'state': 'obsolete',
                'obsolete_date': fields.Datetime.now(),
                'active': False,
            })
            rec._log_state_change(old, 'obsolete')
            rec.message_post(body=_("Item obsoleted by %s.", self.env.user.name))
        return True

    # ═════════════════════════════════════════════════════════════════════════
    # Pre-condition checks (Manufacturing Governance §8)
    # ═════════════════════════════════════════════════════════════════════════

    def _check_no_open_new_production(self):
        """Service transition: open new-production MOs block."""
        self.ensure_one()
        if not self.product_id:
            return
        open_mos = self.env['mrp.production'].search([
            ('product_id', '=', self.product_id.id),
            ('state', 'in', ('draft', 'confirmed', 'progress')),
        ])
        if open_mos:
            raise UserError(_(
                "Cannot move %(item)s to Service — %(n)d open Manufacturing "
                "Order(s) still reference it:\n%(mos)s",
                item=self.display_name,
                n=len(open_mos),
                mos=', '.join(open_mos.mapped('name')),
            ))

    def _check_no_open_references(self):
        """Obsolete transition: any open operational reference blocks (Mfg §8.1)."""
        self.ensure_one()
        errors = []
        if self.product_id:
            n_mo = self.env['mrp.production'].search_count([
                ('product_id', '=', self.product_id.id),
                ('state', 'in', ('draft', 'confirmed', 'progress')),
            ])
            if n_mo:
                errors.append(_('%d open Manufacturing Order(s)') % n_mo)
            n_po = self.env['purchase.order.line'].search_count([
                ('product_id', '=', self.product_id.id),
                ('order_id.state', 'in', ('draft', 'sent', 'to approve', 'purchase')),
            ])
            if n_po:
                errors.append(_('%d open Purchase Order line(s)') % n_po)
        if self.product_tmpl_id:
            n_bom = self.env['mrp.bom.line'].search_count([
                ('product_id.product_tmpl_id', '=', self.product_tmpl_id.id),
                ('bom_id.active', '=', True),
            ])
            if n_bom:
                errors.append(_('active in %d MRP BOM line(s)') % n_bom)
        if errors:
            raise UserError(_(
                "Cannot obsolete %(item)s — active references:\n• %(errors)s\n\n"
                "Close or cancel these before obsoleting.",
                item=self.display_name,
                errors='\n• '.join(errors),
            ))

    # ═════════════════════════════════════════════════════════════════════════
    # Hooks called by other modules (revision, release)
    # ═════════════════════════════════════════════════════════════════════════

    def _auto_release_from_revision(self, revision):
        """Called by pg.revision.action_release() on the FIRST released revision."""
        self.ensure_one()
        # PG-140 follow-up to PG-130+131: honor the ICP gate that disables
        # automatic item promotion on revision release. Default True keeps
        # the original auto-advance behavior when the ECO module isn't installed.
        ICP = self.env['ir.config_parameter'].sudo()
        if str(ICP.get_param('productgraph.auto_advance_item_on_revision_release', 'True')) != 'True':
            return False
        if self.state == 'engineering':
            old = self.state
            self.with_context(_pg_release_bypass=True).write({
                'state': 'released',
                'released_date': fields.Datetime.now(),
            })
            self._log_state_change(
                old, 'released', triggered_by=revision,
                notes=f'Auto-released by revision {revision.display_name}.',
            )
            self.message_post(body=_(
                "Item auto-released — first revision <b>%s</b> released.",
                revision.display_name,
            ))
            _logger.info(
                'pg.item %s auto-released by pg.revision %s',
                self.part_number, revision.display_name,
            )

    # ═════════════════════════════════════════════════════════════════════════
    # Smart-button actions
    # ═════════════════════════════════════════════════════════════════════════

    def action_view_revisions(self):
        self.ensure_one()
        revisions = self.env['pg.revision'].search([('item_id', '=', self.id)])
        # PG-160: when only one revision exists, skip the list view and land
        # the user directly on the form. Saves a click on the common case.
        if len(revisions) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': revisions.display_name,
                'res_model': 'pg.revision',
                'res_id': revisions.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('Revisions of %s') % self.display_name,
            'res_model': 'pg.revision',
            'view_mode': 'list,form',
            'domain': [('item_id', '=', self.id)],
            'context': {'default_item_id': self.id},
        }

    def action_view_eboms(self):
        self.ensure_one()
        eboms = self.env['pg.ebom'].search([('root_item_id', '=', self.id)])
        # PG-160: single-EBOM shortcut to the form view.
        if len(eboms) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': eboms.display_name,
                'res_model': 'pg.ebom',
                'res_id': eboms.id,
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': _('EBOMs for %s') % self.display_name,
            'res_model': 'pg.ebom',
            'view_mode': 'list,form',
            'domain': [('root_item_id', '=', self.id)],
            'context': {'default_root_item_id': self.id},
        }

    def action_view_mrp_boms(self):
        self.ensure_one()
        if not self.product_tmpl_id:
            raise UserError(_("Item has no linked product — no MRP BOMs exist yet."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('MRP BOMs'),
            'res_model': 'mrp.bom',
            'view_mode': 'list,form',
            'domain': [('product_tmpl_id', '=', self.product_tmpl_id.id)],
        }

    def action_view_mos(self):
        self.ensure_one()
        if not self.product_id:
            raise UserError(_("Item has no linked product — no MOs exist yet."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Manufacturing Orders'),
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            'domain': [('product_id', '=', self.product_id.id)],
        }

    # PG-144 — Open the Apply Template wizard from the Properties tab.
    def action_open_apply_template_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Apply Template — %s') % self.display_name,
            'res_model': 'pg.apply.template.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_item_id': self.id,
            },
        }

    @api.model
    def _name_search(self, name='', domain=None, operator='ilike', limit=100, order=None):
        domain = list(domain or [])
        if name:
            domain = ['|', ('part_number', operator, name), ('name', operator, name)] + domain
        return self._search(domain, limit=limit, order=order)
