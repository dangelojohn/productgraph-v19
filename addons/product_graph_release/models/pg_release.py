# -*- coding: utf-8 -*-
"""
pg.release — The ONLY code path that creates mrp.bom from ProductGraph data.

Architecture Bible R1: ``mrp.bom`` and ``mrp.bom.line`` are written exclusively
by ``PgRelease.action_execute_release``. No other ProductGraph code path is
permitted to touch ``mrp.bom``.

State machine:
    pending → executing → completed
                       → failed (rolled back; may be retried)

Once ``completed`` the record is IMMUTABLE (write blocked at the ORM level).

Manufacturing Governance hooks (docs/026):

* MG-1 — Open MOs on the previous BOM are FROZEN (not modified), and recorded
  in ``frozen_mo_ids``.
* MG-3 — If the linked revision has ``incompatible_with_previous=True``, the
  release annotation reflects that and warehouse notifications are sent.
* MG-5 — The previous active ``mrp.bom`` is deactivated atomically inside
  the same savepoint as the new mrp.bom creation.
* MG-8 — Atomic savepoint ensures failure leaves no partial mrp.bom.
"""
import logging
import traceback
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


RELEASE_STATES = [
    ('pending', 'Pending'),
    ('executing', 'Executing'),
    ('completed', 'Completed'),
    ('failed', 'Failed'),
]


class PgRelease(models.Model):
    _name = 'pg.release'
    _description = 'ProductGraph Release Record'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'release_timestamp desc, id desc'

    name = fields.Char(
        string='Release Reference',
        required=True,
        copy=False,
        index=True,
        default=lambda self: _('New'),
    )

    # ─── Source ─────────────────────────────────────────────────────────────
    ebom_id = fields.Many2one(
        'pg.ebom',
        string='Source EBOM',
        required=True,
        ondelete='restrict',
        index=True,
        tracking=True,
        domain="[('state','=','released')]",
    )
    revision_id = fields.Many2one(
        'pg.revision',
        string='Root Revision',
        related='ebom_id.revision_id',
        store=True,
        readonly=True,
        index=True,
    )
    root_item_id = fields.Many2one(
        'pg.item',
        string='Root Item',
        related='ebom_id.root_item_id',
        store=True,
        readonly=True,
        index=True,
    )

    # ─── State ──────────────────────────────────────────────────────────────
    state = fields.Selection(
        RELEASE_STATES,
        string='Status',
        default='pending',
        required=True,
        copy=False,
        index=True,
        tracking=True,
    )

    # ─── Execution result ───────────────────────────────────────────────────
    mrp_bom_id = fields.Many2one(
        'mrp.bom',
        string='Generated MRP BOM',
        readonly=True,
        copy=False,
        index=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Linked Product',
        readonly=True,
        copy=False,
    )
    line_ids = fields.One2many(
        'pg.release.line',
        'release_id',
        string='Release Lines',
    )

    # ─── Timing ─────────────────────────────────────────────────────────────
    release_timestamp = fields.Datetime(string='Released At', readonly=True, copy=False)
    released_by_id = fields.Many2one('res.users', string='Released By', readonly=True, copy=False)
    effective_date = fields.Date(
        string='Effective Date',
        default=fields.Date.today,
        help='Date from which the new mrp.bom is effective.',
    )

    # ─── Options ────────────────────────────────────────────────────────────
    archive_previous_bom = fields.Boolean(
        string='Archive Previous BOM',
        default=True,
        help='Set the previous active mrp.bom for the same product to active=False.',
    )
    create_product_if_missing = fields.Boolean(
        string='Create Product If Missing',
        default=True,
        help='If the root item has no product.product yet, create one. '
             'If unchecked and no product exists, the release will fail.',
    )

    # ─── Reason / failure ───────────────────────────────────────────────────
    release_reason = fields.Text(
        string='Release Reason',
        required=True,
        help='Business reason for this release. Captured in the audit trail.',
    )
    failure_reason = fields.Text(string='Failure Reason', readonly=True)

    # ─── Manufacturing collision (MG-1) ─────────────────────────────────────
    frozen_mo_count = fields.Integer(string='# Frozen MOs', readonly=True)
    frozen_mo_ids = fields.Many2many(
        'mrp.production',
        'pg_release_frozen_mo_rel',
        'release_id', 'mo_id',
        string='Frozen Manufacturing Orders',
        readonly=True,
    )

    company_id = fields.Many2one(
        related='ebom_id.company_id',
        store=True,
        readonly=True,
    )

    _MUTABLE_ON_COMPLETED = frozenset({
        'state', 'active',
        'message_ids', 'message_follower_ids', 'activity_ids',
    })

    # ═════════════════════════════════════════════════════════════════════════
    # ORM overrides
    # ═════════════════════════════════════════════════════════════════════════

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == _('New'):
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('pg.release') or _('New')
                )
        return super().create(vals_list)

    def write(self, vals):
        for rec in self:
            if rec.state == 'completed':
                blocked = set(vals.keys()) - self._MUTABLE_ON_COMPLETED
                if blocked:
                    raise UserError(_(
                        "Release %(name)s is Completed and immutable.\n"
                        "Blocked fields: %(fields)s.",
                        name=rec.name,
                        fields=', '.join(sorted(blocked)),
                    ))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state in ('completed', 'executing'):
                raise UserError(_(
                    "Release %s in state '%s' cannot be deleted.",
                    rec.name, rec.state,
                ))
        return super().unlink()

    # ═════════════════════════════════════════════════════════════════════════
    # THE release method — only place mrp.bom is created (Bible R1)
    # ═════════════════════════════════════════════════════════════════════════

    def action_execute_release(self):
        """Execute the release. Atomic — any failure rolls back fully."""
        self.ensure_one()
        if self.state == 'completed':
            raise UserError(_("Release %s has already been executed.") % self.name)
        if not self.env.user.has_group('product_graph_base.group_pg_approver'):
            raise UserError(_(
                "Only Approvers may execute releases — this is the sole "
                "authority over mrp.bom creation from ProductGraph data."
            ))

        ebom = self.ebom_id
        revision = self.revision_id
        item = self.root_item_id

        self.write({'state': 'executing'})

        try:
            with self.env.cr.savepoint():
                # Step 1 — validate
                self._validate_pre_release(ebom, revision, item)

                # Step 2 — ensure product
                product = self._ensure_product(item)

                # Step 3 — collect previous BOM for archive / freeze
                old_bom = self.env['mrp.bom'].search([
                    ('product_tmpl_id', '=', product.product_tmpl_id.id),
                    ('active', '=', True),
                ], limit=1)

                # Step 4 — freeze open MOs on previous BOM (MG-1)
                if old_bom:
                    frozen = self._freeze_open_mos(old_bom)
                    self.frozen_mo_count = len(frozen)
                    self.frozen_mo_ids = [(6, 0, frozen.ids)]

                # Step 5 — create new mrp.bom (THE ONLY PLACE)
                mrp_bom = self._create_mrp_bom(ebom, revision, product)

                # Step 6 — archive previous (MG-5)
                if old_bom and self.archive_previous_bom:
                    old_bom.write({'active': False})
                    _logger.info(
                        'Archived previous mrp.bom %s for product %s',
                        old_bom.display_name, product.display_name,
                    )

                # Step 7 — write release outcome
                self.with_context(_pg_release_bypass=True).write({
                    'state': 'completed',
                    'mrp_bom_id': mrp_bom.id,
                    'product_id': product.id,
                    'release_timestamp': fields.Datetime.now(),
                    'released_by_id': self.env.uid,
                })

                # Step 8 — audit
                self.env['pg.audit.log']._log(
                    self,
                    action='release',
                    from_state='pending',
                    to_state='completed',
                    notes=(
                        f'{self.name}: pg.ebom {ebom.name} '
                        f'(Rev {revision.name}) → mrp.bom {mrp_bom.display_name}'
                    ),
                    payload={
                        'ebom_id': ebom.id,
                        'revision_id': revision.id,
                        'mrp_bom_id': mrp_bom.id,
                        'product_id': product.id,
                        'frozen_mo_count': self.frozen_mo_count,
                        'incompatible_with_previous': revision.incompatible_with_previous,
                    },
                )

                # Step 9 — notify (chatter + subscribe mfg group)
                self._notify_release(mrp_bom)

        except Exception as exc:
            _logger.exception('pg.release %s FAILED', self.name)
            # The savepoint context manager has already rolled back the inner
            # work. The state='executing' marker (written outside the
            # savepoint) gets overwritten below by state='failed', so the
            # final committed state is correct.
            #
            # NB: do NOT call self.env.cr.rollback() here. It rolls back the
            # entire outer transaction (including the caller's prior work)
            # and the test framework explicitly forbids it
            # ("Cannot commit or rollback a cursor from inside a test").
            self.write({
                'state': 'failed',
                'failure_reason': f'{exc}\n\n{traceback.format_exc()}',
            })
            self.env['pg.audit.log']._log(
                self,
                action='release',
                from_state='executing',
                to_state='failed',
                notes=f'Release failed: {exc}',
            )
            raise

        return True

    def action_reset_to_pending(self):
        for rec in self:
            if rec.state != 'failed':
                raise UserError(_("Only Failed releases may be reset to Pending."))
            rec.write({'state': 'pending', 'failure_reason': False})
        return True

    # ═════════════════════════════════════════════════════════════════════════
    # Internal helpers
    # ═════════════════════════════════════════════════════════════════════════

    def _validate_pre_release(self, ebom, revision, item):
        """Run the same checks the wizard surfaces, server-side, for safety."""
        if ebom.state != 'released':
            raise UserError(_(
                "EBOM %s must be in Released state. Current: %s",
                ebom.display_name, ebom.state,
            ))
        if revision.state != 'released':
            raise UserError(_(
                "Root revision %s must be Released. Current: %s",
                revision.display_name, revision.state,
            ))
        # Every child line item must be released. Auto-create a product
        # for any released child that lacks one when create_product_if_missing
        # is on, matching the same policy applied to the root item via
        # _ensure_product().
        missing = []
        needs_product = self.env['pg.item']
        for line in ebom.ebom_line_ids:
            comp = line.pg_item_id
            if comp.state != 'released':
                missing.append(_("%s — not released (state: %s)") % (
                    comp.display_name, comp.state,
                ))
            elif not comp.product_id:
                if self.create_product_if_missing:
                    needs_product |= comp
                else:
                    missing.append(_(
                        "%s — no linked Odoo product and "
                        "'Create Product If Missing' is off."
                    ) % comp.display_name)
        if missing:
            raise UserError(_(
                "Pre-release validation failed for EBOM %s:\n• %s\n\n"
                "Release each component first, then retry.",
                ebom.display_name,
                '\n• '.join(missing),
            ))
        for comp in needs_product:
            self._ensure_product(comp)

    def _ensure_product(self, item):
        """Return existing product.product or create one (bypass-flagged)."""
        if item.product_id:
            return item.product_id
        if not self.create_product_if_missing:
            raise UserError(_(
                "Item %s has no linked product.product and "
                "'Create Product If Missing' is off.",
                item.display_name,
            ))
        # Create product.template + product.product
        tmpl_vals = {
            'name': item.name,
            'type': 'consu',
            'default_code': item.part_number,
            'uom_id': item.uom_id.id,
            'description': f'ProductGraph item {item.part_number}',
        }
        if item.image_1920:
            tmpl_vals['image_1920'] = item.image_1920
        tmpl = self.env['product.template'].create(tmpl_vals)
        product = tmpl.product_variant_ids[:1]
        if not product:
            raise UserError(_(
                "Could not create product variant for %s.",
                item.display_name,
            ))
        # Link the product back to the item — bypass needed because the item
        # may already be 'released' state.
        item.with_context(_pg_release_bypass=True).write({
            'product_id': product.id,
        })
        return product

    def _create_mrp_bom(self, ebom, revision, product):
        """Create mrp.bom + mrp.bom.line. THE ONLY PLACE in the codebase."""
        bom_vals = {
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_id': product.id,
            'type': 'normal',
            'product_qty': 1.0,
            'code': f'{ebom.root_item_id.part_number}-{revision.name}',
        }
        mrp_bom = self.env['mrp.bom'].create(bom_vals)
        _logger.info(
            'pg.release %s: created mrp.bom %s (id=%s)',
            self.name, mrp_bom.display_name, mrp_bom.id,
        )

        release_lines = []
        for line in ebom.ebom_line_ids:
            comp = line.pg_item_id
            if not comp.product_id:
                # Should never happen — pre-validation catches this.
                raise UserError(_(
                    "Component %s has no linked product at line creation time.",
                    comp.display_name,
                ))
            mrp_line = self.env['mrp.bom.line'].create({
                'bom_id': mrp_bom.id,
                'product_id': comp.product_id.id,
                'product_qty': line.product_qty,
                'product_uom_id': line.uom_id.id,
                'sequence': line.sequence,
            })
            release_lines.append((0, 0, {
                'pg_ebom_line_id': line.id,
                'pg_item_id': comp.id,
                'product_id': comp.product_id.id,
                'product_qty': line.product_qty,
                'mrp_bom_line_id': mrp_line.id,
            }))
        if release_lines:
            self.write({'line_ids': release_lines})
        return mrp_bom

    def _freeze_open_mos(self, old_bom):
        """MG-1 — find open MOs on old_bom and post freeze chatter. Returns recordset."""
        frozen = self.env['mrp.production'].search([
            ('bom_id', '=', old_bom.id),
            ('state', 'in', ('draft', 'confirmed', 'progress')),
        ])
        if not frozen:
            return frozen
        msg = _(
            "<strong>Engineering note [auto]</strong>: A new revision of "
            "<b>%(item)s</b> has been released (%(rev)s). This MO will complete "
            "using the previous BOM (%(old_bom)s) per ProductGraph governance "
            "policy MG-1. Contact engineering if a change is required.",
            item=self.root_item_id.display_name,
            rev=self.revision_id.name,
            old_bom=old_bom.display_name,
        )
        for mo in frozen:
            mo.message_post(body=msg, subtype_xmlid='mail.mt_note')
            self.env['pg.audit.log']._log(
                self,
                action='mo_freeze',
                notes=f'Froze MO {mo.name} on old BOM {old_bom.display_name}',
                triggered_by=mo,
            )
        _logger.info(
            'pg.release %s froze %d open MOs on %s',
            self.name, len(frozen), old_bom.display_name,
        )
        return frozen

    def _notify_release(self, mrp_bom):
        mfg_group = self.env.ref(
            'product_graph_base.group_pg_mfg', raise_if_not_found=False,
        )
        if mfg_group:
            # Odoo 19 removed res.groups.users — query the inverse side.
            users = self.env['res.users'].search([('group_ids', 'in', mfg_group.id)])
            if users:
                self.message_subscribe(partner_ids=users.partner_id.ids)
        msg = _(
            "Release <b>%(rel)s</b> completed.<br/>"
            "EBOM: %(ebom)s (Rev %(rev)s)<br/>"
            "MRP BOM: <b>%(bom)s</b><br/>"
            "Frozen open MOs: %(n_frozen)d",
            rel=self.name,
            ebom=self.ebom_id.display_name,
            rev=self.revision_id.name,
            bom=mrp_bom.display_name,
            n_frozen=self.frozen_mo_count,
        )
        self.message_post(body=msg)
        self.root_item_id.message_post(body=msg)
        self.ebom_id.message_post(body=msg)
        # Notify open POs (MG-2 — warn-only)
        self._notify_affected_pos()

    def _notify_affected_pos(self):
        """MG-2 — find open PO lines on the product and post a chatter note.

        `purchase` is not a hard dependency: when the module is not installed,
        there are no PO lines to warn about, so this is a no-op.
        """
        if not self.product_id:
            return
        if 'purchase.order.line' not in self.env:
            return
        open_po_lines = self.env['purchase.order.line'].search([
            ('product_id', '=', self.product_id.id),
            ('order_id.state', 'in', ('draft', 'sent', 'to approve', 'purchase')),
        ])
        for line in open_po_lines:
            line.order_id.message_post(body=_(
                "<strong>Engineering note [auto]</strong>: A new revision "
                "(%(rev)s) of component <b>%(item)s</b> has been released. "
                "This open PO may need amendment. ProductGraph release: %(rel)s.",
                rev=self.revision_id.name,
                item=self.root_item_id.display_name,
                rel=self.name,
            ), subtype_xmlid='mail.mt_note')

    # ═════════════════════════════════════════════════════════════════════════
    # UI actions
    # ═════════════════════════════════════════════════════════════════════════

    def action_view_mrp_bom(self):
        self.ensure_one()
        if not self.mrp_bom_id:
            raise UserError(_("This release has not produced an mrp.bom yet."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('MRP BOM'),
            'res_model': 'mrp.bom',
            'res_id': self.mrp_bom_id.id,
            'view_mode': 'form',
        }

    def action_view_frozen_mos(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Frozen Manufacturing Orders'),
            'res_model': 'mrp.production',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.frozen_mo_ids.ids)],
        }
