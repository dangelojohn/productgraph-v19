# -*- coding: utf-8 -*-
import logging

from odoo import _, models

_logger = logging.getLogger(__name__)


class PgEco(models.Model):
    _inherit = 'pg.eco'

    def action_release(self):
        # Let the canonical release flow do its work first; if it raises
        # (e.g. an unreleased to_revision blocks release), we don't run
        # the bridge.
        super().action_release()
        for rec in self:
            rec._write_supersedes_edges()
        return True

    def _write_supersedes_edges(self):
        """Walk every line with change_kind='obsolete' and a
        replacement_item_id set; write a pg.relationship.supersedes
        edge for each. Idempotent — existing edges are not duplicated."""
        self.ensure_one()
        Rel = self.env['pg.relationship']
        edges_written = 0
        for line in self.line_ids:
            if line.change_kind != 'obsolete' or not line.replacement_item_id:
                continue
            existing = Rel.search([
                ('source_item_id', '=', line.replacement_item_id.id),
                ('target_item_id', '=', line.item_id.id),
                ('kind', '=', 'supersedes'),
            ], limit=1)
            if existing:
                # Already wired (e.g. by a prior release attempt or a
                # manual write). Bump the eco_id back-ref if missing.
                if not existing.eco_id:
                    existing.write({'eco_id': self.id})
                continue
            Rel.create({
                'source_item_id': line.replacement_item_id.id,
                'target_item_id': line.item_id.id,
                'kind': 'supersedes',
                'eco_id': self.id,
                'notes': _(
                    "Auto-written by ECO %s on release.") % self.display_name,
            })
            edges_written += 1
        if edges_written:
            self.message_post(
                body=_("ECO release: wrote %d supersedes relationship(s).")
                % edges_written,
                subtype_xmlid='mail.mt_log_note',
            )
            _logger.info(
                "ECO %s release wrote %d supersedes edges",
                self.display_name, edges_written,
            )
