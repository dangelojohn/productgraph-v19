# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PgEcoLine(models.Model):
    _name = 'pg.eco.line'
    _description = 'ECO Affected Item'
    _order = 'eco_id, sequence, id'

    eco_id = fields.Many2one(
        'pg.eco',
        string='ECO',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(default=10)

    item_id = fields.Many2one(
        'pg.item', string='Item', required=True, ondelete='restrict',
    )

    # The revision the item is at right now (or was at when the ECO was filed).
    from_revision_id = fields.Many2one(
        'pg.revision',
        string='From Revision',
        domain="[('item_id', '=', item_id)]",
        ondelete='restrict',
    )
    # The revision this ECO drives the item to. Must reach `released` before
    # the parent ECO can transition to `released`.
    to_revision_id = fields.Many2one(
        'pg.revision',
        string='To Revision',
        domain="[('item_id', '=', item_id)]",
        ondelete='restrict',
    )

    change_kind = fields.Selection(
        [
            ('revision_bump', 'Revision Bump'),
            ('property_change', 'Property Change'),
            ('bom_change', 'BOM Change'),
            ('obsolete', 'Obsolescence'),
            ('introduce', 'New Item Introduction'),
        ],
        string='Change Kind',
        required=True,
        default='revision_bump',
    )

    status = fields.Selection(
        [
            ('pending', 'Pending'),
            ('in_progress', 'In Progress'),
            ('done', 'Done'),
            ('skipped', 'Skipped'),
        ],
        string='Line Status',
        default='pending',
        required=True,
    )

    notes = fields.Text(string='Notes')

    eco_state = fields.Selection(
        related='eco_id.state', store=True, index=True, string='ECO State',
    )

    # ──────────────────────────────────────────────────────────────────
    # Constraints
    # ──────────────────────────────────────────────────────────────────
    @api.constrains('from_revision_id', 'to_revision_id', 'item_id')
    def _check_revision_pointers_item_match(self):
        for rec in self:
            if rec.from_revision_id and rec.from_revision_id.item_id != rec.item_id:
                raise ValidationError(
                    _("ECO line: from_revision must belong to the same item.")
                )
            if rec.to_revision_id and rec.to_revision_id.item_id != rec.item_id:
                raise ValidationError(
                    _("ECO line: to_revision must belong to the same item.")
                )

    # Released ECOs lock their lines too.
    def write(self, vals):
        if not self.env.context.get('_pg_eco_release_bypass'):
            for rec in self:
                if rec.eco_state == 'released' and set(vals.keys()) - {'status', 'notes'}:
                    raise UserError(
                        _("ECO %s is released — line cannot be edited "
                          "(only status / notes remain writable).")
                        % rec.eco_id.display_name
                    )
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.eco_state not in ('draft', 'analysis'):
                raise UserError(
                    _("ECO lines can only be removed while the ECO is in "
                      "draft or analysis. %s is %s.")
                    % (rec.eco_id.display_name, rec.eco_state)
                )
        return super().unlink()
