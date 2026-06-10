# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PgEcoLine(models.Model):
    _inherit = 'pg.eco.line'

    replacement_item_id = fields.Many2one(
        'pg.item',
        string='Replacement Item',
        ondelete='restrict',
        help='When change_kind=obsolete, the item that takes over for '
             'this one. On ECO release a supersedes relationship is '
             'written: replacement_item supersedes item.',
    )

    @api.constrains('replacement_item_id', 'item_id', 'change_kind')
    def _check_replacement_only_on_obsolete(self):
        for rec in self:
            if not rec.replacement_item_id:
                continue
            if rec.change_kind != 'obsolete':
                raise ValidationError(
                    _("Replacement item is only valid when change_kind "
                      "is 'obsolete'. Line for %s has change_kind=%s.")
                    % (rec.item_id.display_name, rec.change_kind)
                )
            if rec.replacement_item_id == rec.item_id:
                raise ValidationError(
                    _("Replacement item must differ from the affected "
                      "item itself (got %s on both ends).")
                    % rec.item_id.display_name
                )
