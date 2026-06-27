# -*- coding: utf-8 -*-
"""PG-144 — Property Template Apply-in-Context.

Opens from the Properties tab on a pg.item or a pg.revision. Suggests
templates that match the item's item_type (and are active), lets the user
pick one or more, and applies them:

1. Links each chosen template to ``pg.item.property_template_ids`` (additive).
2. If launched from a revision, also creates empty ``pg.property.value``
   rows on that revision for every field defined by each chosen template,
   skipping fields that already have a value (idempotent).

Apply Template is a metadata edit (item ↔ template link + empty value rows),
not a lifecycle transition, so it does NOT participate in the PG-120 SoD
guard — same author can apply templates they didn't author.
"""
from odoo import _, api, fields, models


class PgApplyTemplateWizard(models.TransientModel):
    _name = 'pg.apply.template.wizard'
    _description = 'Apply Property Template'

    item_id = fields.Many2one(
        'pg.item',
        string='Item',
        required=True,
        ondelete='cascade',
    )
    revision_id = fields.Many2one(
        'pg.revision',
        string='Target Revision',
        ondelete='cascade',
        help="Optional. When set, empty pg.property.value rows are created "
             "on this revision for every field in each applied template.",
    )

    template_ids = fields.Many2many(
        'pg.property.template',
        relation='pg_apply_template_wizard_rel',
        column1='wizard_id',
        column2='template_id',
        string='Templates to Apply',
        domain="[('active','=',True),('item_type','=',item_type)]",
    )

    suggested_template_ids = fields.Many2many(
        'pg.property.template',
        relation='pg_apply_template_wizard_suggested_rel',
        column1='wizard_id',
        column2='template_id',
        string='Suggested for this Item',
        compute='_compute_suggested',
    )

    # Convenience related field so the template_ids domain can filter by
    # item_type without an `eval` round-trip in the view arch.
    item_type = fields.Selection(
        related='item_id.item_type',
        readonly=True,
    )

    @api.depends('item_id', 'item_id.item_type')
    def _compute_suggested(self):
        Template = self.env['pg.property.template']
        for wiz in self:
            if not wiz.item_id or not wiz.item_id.item_type:
                wiz.suggested_template_ids = False
                continue
            already_linked = wiz.item_id.property_template_ids.ids
            wiz.suggested_template_ids = Template.search([
                ('active', '=', True),
                ('item_type', '=', wiz.item_id.item_type),
                ('id', 'not in', already_linked),
            ])

    def action_apply(self):
        self.ensure_one()
        if not self.template_ids:
            return {'type': 'ir.actions.act_window_close'}

        # 1. Link templates to item (additive; (4, id) is a no-op when
        #    already linked, so the operation is idempotent).
        self.item_id.write({
            'property_template_ids': [(4, t.id) for t in self.template_ids],
        })

        # 2. If a target revision is provided, create empty value rows for
        #    every field — skipping any (revision_id, field_id) pair that
        #    already exists (idempotent).
        values_created = 0
        if self.revision_id:
            PgValue = self.env['pg.property.value']
            existing_field_ids = set(self.revision_id.property_value_ids.mapped(
                'field_id'
            ).ids)
            to_create = []
            for tmpl in self.template_ids:
                for fld in tmpl.field_ids:
                    if fld.id in existing_field_ids:
                        continue
                    to_create.append({
                        'revision_id': self.revision_id.id,
                        'field_id': fld.id,
                    })
                    existing_field_ids.add(fld.id)
            if to_create:
                PgValue.create(to_create)
                values_created = len(to_create)

        applied = ', '.join(self.template_ids.mapped('name'))
        if self.revision_id:
            msg = _(
                "Applied %(count)d template(s) — created %(rows)d empty "
                "property value row(s): %(names)s"
            ) % {
                'count': len(self.template_ids),
                'rows': values_created,
                'names': applied,
            }
            self.revision_id.message_post(body=msg)
        else:
            msg = _(
                "Applied %(count)d property template(s): %(names)s"
            ) % {
                'count': len(self.template_ids),
                'names': applied,
            }
            self.item_id.message_post(body=msg)

        return {'type': 'ir.actions.act_window_close'}
