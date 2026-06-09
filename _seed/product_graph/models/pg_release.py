# -*- coding: utf-8 -*-
"""
pg.release — THE governance gate between engineering and manufacturing.

ARCHITECTURAL CONTRACT (Architecture Bible §4.2):
  The only place mrp.bom is created by ProductGraph code is in
  PgRelease.action_execute_release(). No other method may create mrp.bom.

Release sequence:
  1.  Validate preconditions (revision state, component linkage, etc.)
  2.  Ensure product.product exists or create one.
  3.  Archive the previous BOM (optional).
  4.  Create mrp.bom with all mrp.bom.line records.
  5.  Set pg.revision.mrp_bom_id via raw SQL (bypasses immutability guard).
  6.  Call pg.revision._force_released() to mark it released.
  7.  Call pg.item._auto_release_from_revision().
  8.  Write pg.audit.log.
  9.  Post chatter messages.
"""
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PgRelease(models.Model):
    _name = 'pg.release'
    _description = 'ProductGraph Release Record'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'release_date desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string='Release Reference',
        required=True,
        copy=False,
        index=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('pg.release'),
    )

    # ------------------------------------------------------------------
    # Linked records
    # ------------------------------------------------------------------
    item_id = fields.Many2one(
        'pg.item',
        string='Item',
        required=True,
        ondelete='restrict',
        index=True,
        tracking=True,
    )
    revision_id = fields.Many2one(
        'pg.revision',
        string='Revision Being Released',
        required=True,
        ondelete='restrict',
        index=True,
        tracking=True,
        domain="[('item_id', '=', item_id), ('state', '=', 'approved')]",
    )
    mrp_bom_id = fields.Many2one(
        'mrp.bom',
        string='Created mrp.bom',
        readonly=True,
        copy=False,
        index=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Product Created/Linked',
        readonly=True,
        copy=False,
    )

    # ------------------------------------------------------------------
    # Release state
    # ------------------------------------------------------------------
    state = fields.Selection([
        ('pending',   'Pending'),
        ('executed',  'Executed'),
        ('failed',    'Failed'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='pending', required=True, copy=False, tracking=True)

    # ------------------------------------------------------------------
    # Release parameters
    # ------------------------------------------------------------------
    release_date   = fields.Datetime(string='Release Date/Time', readonly=True, copy=False)
    released_by_id = fields.Many2one('res.users', string='Released By', readonly=True, copy=False)
    release_notes  = fields.Html(string='Release Notes')
    effective_date = fields.Date(
        string='Effective Date',
        default=fields.Date.today,
        help='Date from which the new BOM is effective in manufacturing.',
    )

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------
    create_product = fields.Boolean(
        string='Force New Odoo Product',
        default=False,
        help='If unchecked, the existing product linked to the item will be reused.',
    )
    archive_previous_bom = fields.Boolean(
        string='Archive Previous BOM on Release',
        default=True,
    )

    # ------------------------------------------------------------------
    # Failure info
    # ------------------------------------------------------------------
    failure_reason = fields.Text(string='Failure Reason', readonly=True)

    # ================================================================
    # Core release method — the ONLY place mrp.bom is created
    # ================================================================

    def action_execute_release(self):
        """
        Execute the release gate.

        ARCHITECTURE CONTRACT: this is the single code path that creates
        mrp.bom records from ProductGraph data.
        """
        self.ensure_one()

        if self.state == 'executed':
            raise UserError(_("Release %s has already been executed.") % self.name)
        if self.state == 'cancelled':
            raise UserError(_("Release %s has been cancelled.") % self.name)
        if not self.env.user.has_group('product_graph.group_pg_release_manager'):
            raise UserError(_(
                "Only Release Managers may execute a release.\n"
                "Contact your Release Manager to proceed."
            ))

        revision = self.revision_id
        item     = self.item_id

        try:
            # Step 1 — validate preconditions
            self._validate_release_preconditions(revision, item)

            # Step 2 — ensure product.product
            product = self._ensure_product(item)

            # Step 3 — optionally archive previous BOM
            if self.archive_previous_bom and item.current_revision_id and \
                    item.current_revision_id != revision:
                prev_bom = item.current_revision_id.mrp_bom_id
                if prev_bom:
                    prev_bom.write({'active': False})
                    _logger.info('Archived previous mrp.bom %s', prev_bom.name)

            # Step 4 — create mrp.bom (THE ONLY PLACE)
            mrp_bom = self._create_mrp_bom(revision, product)

            # Step 5 — set revision.mrp_bom_id via raw SQL (bypasses immutability)
            self.env.cr.execute(
                "UPDATE pg_revision SET mrp_bom_id = %s WHERE id = %s",
                (mrp_bom.id, revision.id),
            )
            self.env.invalidate_all()  # Odoo 19: use invalidate_all()

            # Step 6 — mark revision released
            self.env.cr.execute(
                "UPDATE pg_revision SET state = 'released', released_by_id = %s, "
                "released_date = NOW() AT TIME ZONE 'UTC' WHERE id = %s",
                (self.env.uid, revision.id),
            )
            self.env.invalidate_all()

            # Step 7 — auto-release item
            item._auto_release_from_revision(revision)

            # Step 8 — record release details
            self.write({
                'state':         'executed',
                'release_date':  fields.Datetime.now(),
                'released_by_id': self.env.uid,
                'mrp_bom_id':    mrp_bom.id,
                'product_id':    product.id,
            })

            # Step 9 — audit log
            self.env['pg.audit.log'].sudo().create({
                'item_id':              item.id,
                'revision_id':          revision.id,
                'action':               'release',
                'from_state':           'approved',
                'to_state':             'released',
                'user_id':              self.env.uid,
                'triggered_by_model':  'pg.release',
                'triggered_by_res_id': self.id,
                'notes': (
                    f'Release {self.name}: '
                    f'pg.revision {revision.revision_code} → mrp.bom {mrp_bom.name}'
                ),
            })

            # Step 10 — chatter
            item.message_post(
                body=_(
                    "✅ Revision <b>%(rev)s</b> released to manufacturing.<br/>"
                    "mrp.bom created: <b>%(bom)s</b><br/>"
                    "Released by: %(user)s",
                    rev=revision.revision_code,
                    bom=mrp_bom.name,
                    user=self.env.user.name,
                ),
                message_type='notification',
            )
            revision.message_post(
                body=_(
                    "Revision released. mrp.bom reference: <b>%(bom)s</b>",
                    bom=mrp_bom.name,
                ),
                message_type='notification',
            )
            _logger.info(
                'ProductGraph release %s executed: %s Rev %s → mrp.bom %s',
                self.name, item.item_number, revision.revision_code, mrp_bom.name,
            )

        except Exception as exc:
            self.write({
                'state':          'failed',
                'failure_reason': str(exc),
            })
            self.env.cr.rollback()
            raise

    # ================================================================
    # Internal helpers
    # ================================================================

    def _validate_release_preconditions(self, revision, item):
        if revision.state != 'approved':
            raise UserError(_(
                "Revision %s must be in 'Approved' state before release. "
                "Current state: %s",
                revision.display_name_full, revision.state,
            ))
        if revision.item_id != item:
            raise UserError(_(
                "Revision %s does not belong to item %s.",
                revision.display_name_full, item.display_name_full,
            ))
        # Verify all components have linked products (released)
        for line in revision.bom_line_ids:
            comp = line.component_item_id
            if not comp.product_id:
                raise UserError(_(
                    "Component %(comp)s [%(num)s] has no linked Odoo product.\n"
                    "Release the component item first.",
                    comp=comp.name,
                    num=comp.item_number,
                ))
            if comp.state not in ('released', 'engineering'):
                raise UserError(_(
                    "Component %(comp)s [%(num)s] is in state '%(state)s'.\n"
                    "Only 'released' or 'in engineering' components may be used.",
                    comp=comp.name,
                    num=comp.item_number,
                    state=comp.state,
                ))

    def _ensure_product(self, item):
        """Return existing product.product or create one."""
        if item.product_id and not self.create_product:
            return item.product_id
        # Create product.template + product.product
        template = self.env['product.template'].create({
            'name': item.name,
            'type': 'consu',
            'default_code': item.item_number,
            'description': f'ProductGraph item {item.item_number}',
        })
        product = template.product_variant_ids[:1]
        if not product:
            raise UserError(_(
                "Could not create product variant for item %s.", item.display_name_full
            ))
        # Write product_id directly to bypass immutability (item might be 'released')
        self.env.cr.execute(
            "UPDATE pg_item SET product_id = %s WHERE id = %s",
            (product.id, item.id),
        )
        self.env.invalidate_all()
        return product

    def _create_mrp_bom(self, revision, product):
        """Create and return the mrp.bom record. ONLY CALLED FROM action_execute_release."""
        bom_vals = {
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_id':       product.id,
            'type':             'normal',
            'product_qty':      1.0,
            'code':             f'{revision.item_id.item_number}-{revision.revision_code}',
        }
        mrp_bom = self.env['mrp.bom'].create(bom_vals)

        # Create BOM lines
        for line in revision.bom_line_ids:
            line_vals = line._to_mrp_bom_line_vals(mrp_bom.id)
            self.env['mrp.bom.line'].create(line_vals)

        _logger.info('Created mrp.bom %s (id=%s)', mrp_bom.name, mrp_bom.id)
        return mrp_bom

    def action_cancel(self):
        self.ensure_one()
        if self.state == 'executed':
            raise UserError(_("Cannot cancel an already-executed release."))
        self.write({'state': 'cancelled'})

    def action_reset_to_pending(self):
        self.ensure_one()
        if self.state not in ('failed', 'cancelled'):
            raise UserError(_("Only failed or cancelled releases can be reset."))
        self.write({'state': 'pending', 'failure_reason': False})
