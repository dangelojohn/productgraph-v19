# -*- coding: utf-8 -*-
"""
Extend pg.item with the revision-collection One2many and "Create Revision"
helper. Lives in product_graph_revision because pg.revision is defined here.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class PgItem(models.Model):
    _inherit = 'pg.item'

    revision_ids = fields.One2many(
        'pg.revision',
        'item_id',
        string='Revisions',
    )
    current_revision_id = fields.Many2one(
        'pg.revision',
        string='Current Released Revision',
        compute='_compute_current_revision',
        store=True,
        index=True,
    )
    current_revision_name = fields.Char(
        related='current_revision_id.name',
        string='Current Rev',
        readonly=True,
    )

    @api.depends('revision_ids.state', 'revision_ids.sequence', 'revision_ids.release_timestamp')
    def _compute_current_revision(self):
        for rec in self:
            released = rec.revision_ids.filtered(lambda r: r.state == 'released')
            if released:
                rec.current_revision_id = released.sorted(
                    key=lambda r: r.release_timestamp or fields.Datetime.from_string('1970-01-01'),
                    reverse=True,
                )[:1]
            else:
                rec.current_revision_id = False

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _next_revision_code(self):
        """Return next alphabetical revision code (A → B → … → Z → AA → AB)."""
        self.ensure_one()
        existing = self.revision_ids.mapped('name')
        if not existing:
            return 'A'

        def _code_to_num(code):
            n = 0
            for ch in code.upper():
                if not ch.isalpha():
                    return 0
                n = n * 26 + (ord(ch) - ord('A') + 1)
            return n

        def _num_to_code(n):
            code = ''
            while n > 0:
                n, r = divmod(n - 1, 26)
                code = chr(r + ord('A')) + code
            return code

        return _num_to_code(max((_code_to_num(c) for c in existing if c), default=0) + 1)

    def action_create_revision(self):
        self.ensure_one()
        if self.state not in ('prototype', 'engineering', 'released'):
            raise UserError(_(
                "Item must be in Prototype, Engineering, or Released state to "
                "create a revision. Current state: %s",
                self.state,
            ))
        next_code = self._next_revision_code()
        predecessor = self.current_revision_id
        return {
            'type': 'ir.actions.act_window',
            'name': _('New Revision of %s') % self.display_name,
            'res_model': 'pg.revision',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_item_id': self.id,
                'default_name': next_code,
                'default_predecessor_revision_id': predecessor.id if predecessor else False,
            },
        }
