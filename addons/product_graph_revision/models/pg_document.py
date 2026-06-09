# -*- coding: utf-8 -*-
"""
pg.document — Versioned engineering document attached to a revision.

Each document is a binary blob with metadata (name, type, version label).
Documents inherit immutability from their parent revision: once the parent
revision is released, the document can no longer be replaced or removed.
Add a new revision to attach an updated drawing.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


DOCUMENT_TYPES = [
    ('drawing', 'Drawing'),
    ('spec', 'Specification'),
    ('manual', 'Manual'),
    ('3d_model', '3D Model'),
    ('source', 'Source Code'),
    ('test_report', 'Test Report'),
    ('certificate', 'Certificate'),
    ('other', 'Other'),
]


class PgDocument(models.Model):
    _name = 'pg.document'
    _description = 'ProductGraph Engineering Document'
    _inherit = ['mail.thread']
    _order = 'revision_id, sequence, id'

    name = fields.Char(string='Document Name', required=True, tracking=True)
    revision_id = fields.Many2one(
        'pg.revision',
        string='Revision',
        required=True,
        ondelete='cascade',
        index=True,
    )
    item_id = fields.Many2one(
        related='revision_id.item_id',
        store=True,
        readonly=True,
        index=True,
    )

    document_type = fields.Selection(
        DOCUMENT_TYPES,
        string='Type',
        default='drawing',
        required=True,
        tracking=True,
    )
    version_label = fields.Char(
        string='Version Label',
        help='Free-form version label, e.g. "Rev A", "v1.2", "20260609".',
        tracking=True,
    )
    sequence = fields.Integer(default=10)

    file_data = fields.Binary(string='File', attachment=True, required=True)
    file_name = fields.Char(string='File Name')

    description = fields.Text(string='Description')

    uploaded_by_id = fields.Many2one(
        'res.users',
        string='Uploaded By',
        default=lambda self: self.env.user,
        readonly=True,
    )
    uploaded_at = fields.Datetime(
        string='Uploaded At',
        default=fields.Datetime.now,
        readonly=True,
    )

    active = fields.Boolean(default=True)

    # ═════════════════════════════════════════════════════════════════════════
    # Immutability inherited from the parent revision
    # ═════════════════════════════════════════════════════════════════════════

    def _check_revision_writable(self):
        bypass = self.env.context.get('_pg_release_bypass')
        for rec in self:
            if rec.revision_id.state in ('released', 'superseded', 'obsolete') and not bypass:
                raise UserError(_(
                    "Cannot modify document %(doc)s — parent revision "
                    "%(rev)s is %(state)s. Add a new revision instead.",
                    doc=rec.name,
                    rev=rec.revision_id.display_name,
                    state=rec.revision_id.state,
                ))

    def write(self, vals):
        # Allow chatter/activity writes always
        non_chatter = set(vals.keys()) - {
            'message_ids', 'message_follower_ids', 'activity_ids',
        }
        if non_chatter:
            self._check_revision_writable()
        return super().write(vals)

    def unlink(self):
        self._check_revision_writable()
        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            self.env['pg.audit.log']._log(
                rec.revision_id,
                action='document_upload',
                notes=f'Uploaded document: {rec.name} ({rec.document_type})',
                triggered_by=rec,
            )
        return records
