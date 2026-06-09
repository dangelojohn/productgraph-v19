# -*- coding: utf-8 -*-
"""
pg.ebom.line — One component reference within an Engineering BOM.

Each line is a child item + qty + uom + optional designator. Lines support
alternate groups: ``alternate_group`` is a free-form tag identifying a set
of interchangeable parts (e.g. HINGE-A). Within a group, exactly one line
should carry ``is_primary=True``; others are alternates that production
may select manually (Mfg Governance §6 Rule 4.1 — no auto-substitution
in Phase 1).
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


LINE_TYPES = [
    ('normal', 'Normal Component'),
    ('phantom', 'Phantom Sub-assembly'),
    ('optional', 'Optional Component'),
]


class PgEbomLine(models.Model):
    _name = 'pg.ebom.line'
    _description = 'ProductGraph Engineering BOM Line'
    _order = 'sequence, id'

    ebom_id = fields.Many2one(
        'pg.ebom',
        string='EBOM',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(default=10, index=True)

    # The child item
    pg_item_id = fields.Many2one(
        'pg.item',
        string='Component',
        required=True,
        index=True,
        ondelete='restrict',
        help='The child engineering item used in this BOM.',
    )
    pg_revision_id = fields.Many2one(
        'pg.revision',
        string='Specific Revision',
        domain="[('item_id','=',pg_item_id),('state','=','released')]",
        help='Pin to a specific released revision; leave blank to use the '
             'item\'s current released revision at release time.',
    )
    part_number = fields.Char(
        related='pg_item_id.part_number',
        string='Part Number',
        readonly=True,
        store=True,
    )
    item_state = fields.Selection(
        related='pg_item_id.state',
        string='Item State',
        readonly=True,
    )

    # Quantity
    product_qty = fields.Float(
        string='Quantity',
        required=True,
        default=1.0,
        digits='Product Unit of Measure',
    )
    uom_id = fields.Many2one(
        'uom.uom',
        string='UoM',
        required=True,
        default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False),
    )

    # Designator and notes
    reference = fields.Char(string='Designator', help='e.g. R1, C12, U3, "Top Hinge"')
    notes = fields.Char(string='Notes')

    # Type
    bom_line_type = fields.Selection(
        LINE_TYPES,
        string='Type',
        default='normal',
        required=True,
    )
    is_optional = fields.Boolean(
        string='Optional',
        default=False,
        help='Engineering-optional component. Decision deferred to manufacturing.',
    )

    # Alternate group
    alternate_group = fields.Char(
        string='Alternate Group',
        help='Free-form tag identifying a set of interchangeable parts. '
             'Within a group, one line should be marked primary.',
    )
    is_primary = fields.Boolean(
        string='Primary in Alternate Group',
        default=False,
        help='True if this line is the primary choice within its alternate group.',
    )

    @api.onchange('pg_item_id')
    def _onchange_pg_item_id(self):
        if self.pg_item_id and self.pg_item_id.uom_id:
            self.uom_id = self.pg_item_id.uom_id

    # ═════════════════════════════════════════════════════════════════════════
    # Immutability inherited from parent EBOM
    # ═════════════════════════════════════════════════════════════════════════

    def _check_ebom_writable(self):
        bypass = self.env.context.get('_pg_release_bypass')
        for rec in self:
            if rec.ebom_id.state in ('released', 'superseded') and not bypass:
                raise UserError(_(
                    "Cannot edit line on EBOM %(name)s — EBOM is %(state)s.",
                    name=rec.ebom_id.display_name,
                    state=rec.ebom_id.state,
                ))

    def write(self, vals):
        self._check_ebom_writable()
        return super().write(vals)

    def unlink(self):
        self._check_ebom_writable()
        return super().unlink()
