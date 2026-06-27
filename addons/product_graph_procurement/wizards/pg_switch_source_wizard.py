# -*- coding: utf-8 -*-
"""W030 (R6.5) — Switch Source wizard.

JTBD: "When I need to one-time source this batch from vendor B (preferred is
on vacation), I want a 3-click wizard that swaps THIS req/PO's vendor without
disturbing the preferred-vendor flag for future demand."

Surface:
  * New TransientModel ``pg.switch.source.wizard`` carrying either a
    ``requisition_line_id`` or a ``po_line_id`` (exactly one), plus a list of
    candidate vendors derived from the AVL for that line's pg.item.
  * Wizard opens from a "Switch Source" button on either form.
  * On confirm:
      - updates THIS line's ``partner_id`` / supplier reference,
      - posts a chatter note on both the old and new vendor records,
      - does NOT flip any ``pg.vendor.part.is_preferred`` (one-time override).
      - posts a chatter note on the parent requisition / PO documenting the
        switch (audit trail).

Constraints honoured:
  * Wizard refuses to operate on a line whose parent doc is already locked
    (PO state in done/cancel; requisition state in done/cancel).
  * If the candidate vendor has no linked res.partner, refuses with an
    actionable UserError.
  * If the line carries no pg_item_id back-ref, the wizard still works — the
    candidate list will be empty (no AVL), but the user can confirm a custom
    pick by typing a partner; we keep that path simple by requiring the AVL.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


# Doc-level states that lock a line from vendor switching.
LOCKED_PO_STATES = ('done', 'cancel')
LOCKED_REQ_STATES = ('done', 'cancel')


class PgSwitchSourceWizard(models.TransientModel):
    _name = 'pg.switch.source.wizard'
    _description = 'Switch Source on Req / PO Line (one-time override)'

    requisition_line_id = fields.Many2one(
        'purchase.requisition.line',
        string='Requisition Line',
        ondelete='cascade',
    )
    po_line_id = fields.Many2one(
        'purchase.order.line',
        string='PO Line',
        ondelete='cascade',
    )

    pg_item_id = fields.Many2one(
        'pg.item',
        string='Engineering Item',
        compute='_compute_pg_item',
        store=False,
        help='Pulled from the line back-ref (W003).',
    )
    current_vendor_id = fields.Many2one(
        'pg.vendor',
        string='Current Vendor',
        compute='_compute_current_vendor',
        store=False,
    )
    current_partner_id = fields.Many2one(
        'res.partner',
        string='Current Partner',
        compute='_compute_current_vendor',
        store=False,
    )

    candidate_vendor_part_ids = fields.Many2many(
        'pg.vendor.part',
        string='Candidate Vendor Parts (from AVL)',
        compute='_compute_candidates',
        store=False,
    )

    new_vendor_part_id = fields.Many2one(
        'pg.vendor.part',
        string='Switch To',
        required=True,
        domain="[('id', 'in', candidate_vendor_part_ids)]",
        help='AVL entry whose vendor will replace the current one on this '
             'line. The preferred-source flag on this AVL entry is NOT '
             'flipped — this switch is a one-time override.',
    )
    new_vendor_id = fields.Many2one(
        'pg.vendor',
        related='new_vendor_part_id.vendor_id',
        string='New Vendor',
        readonly=True,
    )
    new_partner_id = fields.Many2one(
        'res.partner',
        related='new_vendor_part_id.vendor_id.partner_id',
        string='New Partner',
        readonly=True,
    )

    reason = fields.Text(
        string='Switch Reason',
        required=True,
        help='Logged on both vendor chatters + the parent doc chatter as '
             'the audit trail justification for the one-time override.',
    )

    # ──────────────────────────────────────────────────────────────────
    # Compute
    # ──────────────────────────────────────────────────────────────────
    @api.depends('requisition_line_id', 'po_line_id')
    def _compute_pg_item(self):
        for rec in self:
            line = rec.requisition_line_id or rec.po_line_id
            rec.pg_item_id = line.pg_item_id if line else False

    @api.depends('requisition_line_id', 'po_line_id')
    def _compute_current_vendor(self):
        for rec in self:
            partner = False
            if rec.requisition_line_id:
                req = rec.requisition_line_id.requisition_id
                partner = req.vendor_id if 'vendor_id' in req._fields else False
            elif rec.po_line_id:
                partner = rec.po_line_id.order_id.partner_id
            rec.current_partner_id = partner or False
            # Reverse-resolve a pg.vendor from the partner if possible.
            if partner:
                vendor = self.env['pg.vendor'].search(
                    [('partner_id', '=', partner.id)], limit=1,
                )
                rec.current_vendor_id = vendor or False
            else:
                rec.current_vendor_id = False

    @api.depends('pg_item_id', 'current_vendor_id')
    def _compute_candidates(self):
        for rec in self:
            if not rec.pg_item_id:
                rec.candidate_vendor_part_ids = self.env['pg.vendor.part']
                continue
            # AVL candidates: active vendor-parts on this item EXCEPT the
            # current vendor's row, sorted by rank, lead-time, price.
            domain = [
                ('item_id', '=', rec.pg_item_id.id),
                ('active', '=', True),
            ]
            if rec.current_vendor_id:
                domain.append(('vendor_id', '!=', rec.current_vendor_id.id))
            parts = self.env['pg.vendor.part'].search(
                domain, order='rank, lead_time_days, unit_price_hint',
            )
            rec.candidate_vendor_part_ids = parts

    # ──────────────────────────────────────────────────────────────────
    # Defaults / open-from-button
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_id = self.env.context.get('active_id')
        if active_model == 'purchase.requisition.line' and active_id:
            vals['requisition_line_id'] = active_id
        elif active_model == 'purchase.order.line' and active_id:
            vals['po_line_id'] = active_id
        return vals

    # ──────────────────────────────────────────────────────────────────
    # Validation
    # ──────────────────────────────────────────────────────────────────
    def _validate_lock_state(self):
        """Reject if the parent doc is already locked."""
        self.ensure_one()
        if self.requisition_line_id:
            state = self.requisition_line_id.requisition_id.state
            if state in LOCKED_REQ_STATES:
                raise UserError(_(
                    "Cannot switch source: the parent requisition is in "
                    "state %s (locked).") % state)
        elif self.po_line_id:
            state = self.po_line_id.order_id.state
            if state in LOCKED_PO_STATES:
                raise UserError(_(
                    "Cannot switch source: the parent purchase order is "
                    "in state %s (locked).") % state)
        else:
            raise UserError(_(
                "Switch-source wizard requires either a requisition line "
                "or a PO line context."))

    def _validate_target_vendor(self):
        self.ensure_one()
        vendor = self.new_vendor_part_id.vendor_id
        if not vendor.partner_id:
            raise UserError(_(
                "Vendor %s has no linked res.partner — cannot switch the "
                "line to a partnerless vendor. Link the partner on the "
                "vendor record first.") % vendor.display_name)
        if vendor.state in ('disqualified',):
            raise UserError(_(
                "Vendor %s is disqualified — cannot switch a live "
                "req/PO line to it.") % vendor.display_name)

    # ──────────────────────────────────────────────────────────────────
    # Confirm
    # ──────────────────────────────────────────────────────────────────
    def action_confirm(self):
        self.ensure_one()
        self._validate_lock_state()
        self._validate_target_vendor()

        old_vendor = self.current_vendor_id
        new_vendor = self.new_vendor_id
        new_partner = self.new_partner_id
        line = self.requisition_line_id or self.po_line_id

        # Capture pre-switch flag state for assertion clarity — the wizard
        # MUST NOT flip is_preferred. Preserved by simply not writing it.
        pre_preferred = self.new_vendor_part_id.is_preferred

        # 1) Update the line's effective vendor/partner.
        if self.po_line_id:
            order = self.po_line_id.order_id
            if order.state not in ('draft', 'sent'):
                raise UserError(_(
                    "PO %s is not in a draft/sent state — cannot switch "
                    "the vendor on its lines. Cancel and recreate or "
                    "operate on the upstream requisition.") % order.name)
            # Rewriting partner_id on the order is the cleanest way; line
            # carries the partner indirectly through order_id.
            order.write({'partner_id': new_partner.id})
        elif self.requisition_line_id:
            req = self.requisition_line_id.requisition_id
            if 'vendor_id' in req._fields:
                req.write({'vendor_id': new_partner.id})

        # 2) Post audit chatter on OLD and NEW vendors (per spec).
        body_template = _(
            "Switch Source (W030): %(doc)s line for item <b>%(item)s</b> "
            "moved from <b>%(old)s</b> → <b>%(new)s</b>.<br/>"
            "Reason: %(reason)s<br/>"
            "<i>One-time override — preferred-source flag NOT modified.</i>"
        )
        doc_repr = (
            self.po_line_id.order_id.display_name
            if self.po_line_id else
            self.requisition_line_id.requisition_id.display_name
        )
        item_repr = self.pg_item_id.display_name if self.pg_item_id else _("(no pg.item)")
        body = body_template % {
            'doc': doc_repr,
            'item': item_repr,
            'old': old_vendor.display_name if old_vendor else _("(no prior pg.vendor)"),
            'new': new_vendor.display_name,
            'reason': self.reason,
        }
        if old_vendor and old_vendor != new_vendor:
            old_vendor.message_post(
                body=body, subtype_xmlid='mail.mt_log_note',
            )
        new_vendor.message_post(
            body=body, subtype_xmlid='mail.mt_log_note',
        )

        # 3) Mirror the chatter on the parent doc itself for the buyer.
        parent_doc = (
            self.po_line_id.order_id
            if self.po_line_id else
            self.requisition_line_id.requisition_id
        )
        parent_doc.message_post(
            body=body, subtype_xmlid='mail.mt_log_note',
        )

        # 4) Audit-log entry tied to pg.item for the engineering side.
        if self.pg_item_id:
            self.env['pg.audit.log'].sudo()._log(
                self.pg_item_id, 'other',
                notes=_("W030 source switch: %(old)s → %(new)s on %(doc)s") % {
                    'old': old_vendor.display_name if old_vendor else '—',
                    'new': new_vendor.display_name,
                    'doc': doc_repr,
                },
                payload={
                    'wizard': 'pg.switch.source.wizard',
                    'doc_model': parent_doc._name,
                    'doc_id': parent_doc.id,
                    'line_id': line.id,
                    'old_vendor_id': old_vendor.id if old_vendor else None,
                    'new_vendor_id': new_vendor.id,
                    'new_vendor_part_id': self.new_vendor_part_id.id,
                    'preserved_preferred_flag': pre_preferred,
                    'reason': self.reason,
                },
            )

        # 5) Hard-assert the invariant: the AVL row's is_preferred flag is
        # unchanged. If a subclass / future override violated this, raise
        # so the test catches the regression.
        self.new_vendor_part_id.invalidate_recordset(['is_preferred'])
        if self.new_vendor_part_id.is_preferred != pre_preferred:
            raise UserError(_(
                "Internal: W030 wizard inadvertently flipped the "
                "preferred-source flag. This is a bug — please report."))

        return {'type': 'ir.actions.act_window_close'}
