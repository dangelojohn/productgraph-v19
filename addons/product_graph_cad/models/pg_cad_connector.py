# -*- coding: utf-8 -*-
import json
import logging
import traceback
from typing import Any

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..connectors import CONNECTOR_REGISTRY, BaseCadConnector, CadDocument

_logger = logging.getLogger(__name__)


def _selection_from_registry(_self=None):
    """Build the driver selection lazily so additional driver modules
    that loaded after this one are visible."""
    # Re-import to pick up any drivers registered by later-loaded addons.
    from .. import connectors  # noqa: F401
    seen: dict[str, str] = {}
    for code, cls in CONNECTOR_REGISTRY:
        seen[code] = cls.label or code
    return [(code, label) for code, label in seen.items()]


class PgCadConnector(models.Model):
    _name = 'pg.cad.connector'
    _description = 'CAD Connector'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'code'

    code = fields.Char(string='Connector Code', required=True, copy=False, index=True)
    name = fields.Char(string='Name', required=True)
    driver_code = fields.Selection(
        selection=_selection_from_registry,
        string='Driver',
        required=True,
        help='Which Python connector class handles this record.',
    )
    state = fields.Selection(
        [('active', 'Active'), ('inactive', 'Inactive')],
        string='State', default='inactive', required=True, tracking=True,
    )
    config_json = fields.Text(
        string='Driver Config (JSON)',
        default='{}',
        help='Driver-specific configuration as a JSON object. For the '
             'json_file driver this is {"path": "/path/to/export.json"}.',
    )
    last_sync_at = fields.Datetime(string='Last Sync', readonly=True, copy=False)
    last_sync_status = fields.Selection(
        [('ok', 'OK'), ('failed', 'Failed'), ('never', 'Never')],
        default='never', readonly=True, copy=False,
    )
    last_sync_detail = fields.Text(string='Last Sync Detail', readonly=True, copy=False)

    import_ids = fields.One2many('pg.cad.import', 'connector_id', string='Imports')
    import_count = fields.Integer(compute='_compute_import_count')

    create_revision = fields.Boolean(
        string='Create Draft Revision',
        default=True,
        help='When pulling, create a draft pg.revision per document so the '
             'imported BOM has somewhere to live. Disable if items already '
             'have a current revision you want to keep using.',
    )
    create_ebom = fields.Boolean(
        string='Create EBOM',
        default=True,
        help='When pulling, create a draft pg.ebom from each document\'s '
             '1-level BOM and attach it to the new revision.',
    )

    _code_unique = models.Constraint(
        'UNIQUE(code)',
        'Connector code must be unique.',
    )

    # ──────────────────────────────────────────────────────────────────
    # Computes
    # ──────────────────────────────────────────────────────────────────
    @api.depends('import_ids')
    def _compute_import_count(self):
        for rec in self:
            rec.import_count = len(rec.import_ids)

    # ──────────────────────────────────────────────────────────────────
    # Driver wiring
    # ──────────────────────────────────────────────────────────────────
    def _config_dict(self) -> dict[str, Any]:
        self.ensure_one()
        try:
            return json.loads(self.config_json or '{}')
        except json.JSONDecodeError as exc:
            raise ValidationError(
                _("Connector %s has invalid config_json: %s")
                % (self.code, exc)
            )

    def _driver(self) -> BaseCadConnector:
        self.ensure_one()
        for code, cls in CONNECTOR_REGISTRY:
            if code == self.driver_code:
                return cls(self)
        raise ValidationError(
            _("Driver %s not in CONNECTOR_REGISTRY.") % self.driver_code
        )

    # ──────────────────────────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────────────────────────
    def action_test_connection(self):
        self.ensure_one()
        driver = self._driver()
        meta = driver.fetch_metadata()
        body = _("Test connection result: %s") % json.dumps(meta)
        self.message_post(body=body, subtype_xmlid='mail.mt_log_note')
        if not meta.get('reachable'):
            raise UserError(_("Connector not reachable: %s") % meta.get('detail', '?'))
        return True

    def action_pull(self):
        """Pull every document the driver reports and create / update
        the matching pg.items + (optionally) draft revisions + EBOMs.

        Whole run is atomic — any failure rolls back the entire pull.
        """
        self.ensure_one()
        run = self.env['pg.cad.import'].create({
            'connector_id': self.id,
            'state': 'running',
            'started_at': fields.Datetime.now(),
        })
        try:
            with self.env.cr.savepoint():
                self._do_pull(run)
                run.write({
                    'state': 'completed',
                    'completed_at': fields.Datetime.now(),
                })
                self.write({
                    'last_sync_at': fields.Datetime.now(),
                    'last_sync_status': 'ok',
                    'last_sync_detail': _('Pulled %d documents.')
                    % len(run.line_ids),
                })
            self.message_post(
                body=_("CAD pull completed — %d documents processed.")
                % len(run.line_ids),
                subtype_xmlid='mail.mt_log_note',
            )
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'pg.cad.import',
                'res_id': run.id,
                'view_mode': 'form',
                'target': 'current',
            }
        except Exception as exc:
            # Swallow the rollback already done; record the failure on its
            # own (fresh) row so the trace survives.
            tb = traceback.format_exc()
            _logger.exception("CAD pull failed for %s", self.code)
            failed = self.env['pg.cad.import'].create({
                'connector_id': self.id,
                'state': 'failed',
                'started_at': run.started_at,
                'completed_at': fields.Datetime.now(),
                'failure_reason': f'{exc}\n\n{tb[:2000]}',
            })
            self.write({
                'last_sync_at': fields.Datetime.now(),
                'last_sync_status': 'failed',
                'last_sync_detail': str(exc),
            })
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'pg.cad.import',
                'res_id': failed.id,
                'view_mode': 'form',
                'target': 'current',
            }

    def _do_pull(self, run):
        driver = self._driver()
        docs = driver.list_documents()
        Item = self.env['pg.item']
        Revision = self.env['pg.revision']
        Ebom = self.env['pg.ebom']
        EbomLine = self.env['pg.ebom.line']
        ImportLine = self.env['pg.cad.import.line']

        # Default category for new items created from CAD.
        cat = self.env.ref(
            'product_graph_base.cat_mechanical', raise_if_not_found=False,
        )

        # Pass 1 — create/update every item (no BOM yet) so the second
        # pass's child lookups always resolve. Track per-doc action so the
        # trace row reflects whether the item record itself was new at
        # this pull, regardless of BOM-side stub creation in pass 2.
        actions: dict[str, str] = {}
        items_by_pn: dict[str, Any] = {}
        for doc in docs:
            existing = Item.search(
                [('part_number', '=', doc.part_number)], limit=1)
            if existing:
                actions[doc.part_number] = 'updated'
                vals = {
                    'name': doc.name,
                    'description': doc.description or False,
                }
                if doc.item_type and existing.item_type != doc.item_type:
                    vals['item_type'] = doc.item_type
                existing.write(vals)
                items_by_pn[doc.part_number] = existing
            else:
                actions[doc.part_number] = 'created'
                vals = {
                    'name': doc.name,
                    'item_type': doc.item_type or 'part',
                    'description': doc.description or False,
                    'part_number': doc.part_number,
                }
                if cat:
                    vals['category_id'] = cat.id
                item = Item.create(vals)
                try:
                    item.action_to_engineering()
                except Exception:
                    pass
                items_by_pn[doc.part_number] = item

        # Pass 2 — revisions + EBOMs. Children that have their own document
        # are already populated; orphans get stubbed.
        for doc in docs:
            item = items_by_pn[doc.part_number]
            rev = None
            if self.create_revision:
                rev = Revision.search(
                    [('item_id', '=', item.id), ('state', '=', 'draft')],
                    limit=1)
                if not rev:
                    rev = Revision.create({
                        'item_id': item.id,
                        'name': self._next_revision_code(item),
                        'change_summary': 'Initial revision imported from CAD.',
                    })

            if self.create_ebom and doc.ebom and rev:
                ebom = Ebom.search(
                    [('root_item_id', '=', item.id),
                     ('revision_id', '=', rev.id)],
                    limit=1)
                if not ebom:
                    ebom = Ebom.create({
                        'root_item_id': item.id,
                        'revision_id': rev.id,
                        'name': f'EBOM {item.part_number} {rev.name}',
                    })
                # Replace lines wholesale for simplicity.
                ebom.ebom_line_ids.unlink()
                for cad_line in doc.ebom:
                    child = items_by_pn.get(cad_line.child_part_number)
                    if not child:
                        child = Item.search([
                            ('part_number', '=', cad_line.child_part_number)
                        ], limit=1)
                    if not child:
                        # Orphan stub — child has no document.
                        child = Item.create({
                            'name': cad_line.child_part_number,
                            'part_number': cad_line.child_part_number,
                            'item_type': 'part',
                            'category_id': cat.id if cat else False,
                        })
                    EbomLine.create({
                        'ebom_id': ebom.id,
                        'pg_item_id': child.id,
                        'product_qty': cad_line.qty,
                        'reference': cad_line.ref_designator or False,
                    })

            ImportLine.create({
                'import_id': run.id,
                'part_number': doc.part_number,
                'action': actions[doc.part_number],
                'item_id': item.id,
                'message': f'{len(doc.ebom)} BOM lines processed.',
            })

    def _next_revision_code(self, item) -> str:
        """Compute the next revision letter for `item`. A, B, C, … then AA."""
        existing = self.env['pg.revision'].search(
            [('item_id', '=', item.id)],
            order='id desc', limit=1,
        )
        if not existing:
            return 'A'
        prev = existing.name or 'A'
        # Single-letter cycle A..Z then double-letter AA..ZZ.
        if len(prev) == 1 and 'A' <= prev < 'Z':
            return chr(ord(prev) + 1)
        return 'AA' if prev == 'Z' else prev + 'A'
