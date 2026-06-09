# -*- coding: utf-8 -*-
"""
pg.bom.line — Engineering BOM Lines

These are the engineering BOM lines attached to a pg.revision.
When a revision is released, these are mapped to mrp.bom.line records
inside PgRelease.action_execute_release().

ProductGraph maintains its own engineering BOM independently of Odoo's mrp.bom.
The two are synchronised at release time only.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PgBomLine(models.Model):
    _name = 'pg.bom.line'
    _description = 'ProductGraph Engineering BOM Line'
    _order = 'sequence asc, id asc'

    revision_id = fields.Many2one(
        'pg.revision',
        string='Revision',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(string='Seq', default=10)

    # Component item (must be a pg.item)
    component_item_id = fields.Many2one(
        'pg.item',
        string='Component Item',
        required=True,
        index=True,
        help='The child item (component) in the engineering BOM.',
    )
    component_revision_id = fields.Many2one(
        'pg.revision',
        string='Specific Revision',
        domain="[('item_id', '=', component_item_id), ('state', '=', 'released')]",
        help='Pin to a specific released revision. Leave blank to use current released revision.',
    )
    component_number = fields.Char(
        related='component_item_id.item_number',
        string='Item Number',
        readonly=True,
        store=True,
    )

    # Quantity
    product_qty = fields.Float(
        string='Quantity',
        required=True,
        default=1.0,
        digits='Product Unit of Measure',
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='Unit of Measure',
        required=True,
        default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False),
    )

    # Reference / notes
    reference = fields.Char(string='Reference Designator', help='e.g. R1, C12, U3')
    notes = fields.Char(string='Notes')

    # Phantom flag (for sub-assemblies that are manufactured as part of parent)
    bom_line_type = fields.Selection([
        ('normal',   'Normal Component'),
        ('phantom',  'Phantom Sub-assembly'),
        ('optional', 'Optional Component'),
    ], string='Line Type', default='normal', required=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _to_mrp_bom_line_vals(self, bom_id):
        """
        Produce vals dict suitable for mrp.bom.line creation.

        Called by PgRelease.action_execute_release() when building the mrp.bom.
        Validates that the component item has an Odoo product linked.
        """
        self.ensure_one()
        component = self.component_item_id
        if not component.product_id:
            raise UserError(_(
                "Component item %(item)s [%(num)s] has no linked Odoo product.\n"
                "Release the component item first before releasing this assembly.",
                item=component.name,
                num=component.item_number,
            ))
        return {
            'bom_id': bom_id,
            'product_id': component.product_id.id,
            'product_qty': self.product_qty,
            'product_uom_id': self.product_uom_id.id,
        }

    # ================================================================
    # ORM override — block edits on released revision's BOM lines
    # ================================================================

    def write(self, vals):
        for rec in self:
            if rec.revision_id.state in ('released', 'approved'):
                raise UserError(_(
                    "BOM line on revision %s cannot be edited — revision is %s.\n"
                    "Create an ECR to propose changes.",
                    rec.revision_id.display_name_full,
                    rec.revision_id.state,
                ))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.revision_id.state in ('released', 'approved'):
                raise UserError(_(
                    "Cannot delete BOM line — revision %s is %s.",
                    rec.revision_id.display_name_full,
                    rec.revision_id.state,
                ))
        return super().unlink()
