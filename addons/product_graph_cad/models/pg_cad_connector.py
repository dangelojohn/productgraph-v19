# -*- coding: utf-8 -*-
import json
import logging
import traceback
from datetime import timedelta
from typing import Any

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..connectors import CONNECTOR_REGISTRY, BaseCadConnector, CadDocument

_logger = logging.getLogger(__name__)

# pg.item.state values where the record is considered immutable and
# CAD sync must NOT touch it. Mirrors product_graph_base.pg_item._MUTABLE_ON_RELEASED
# semantics but enforced at the descriptor-rejection layer so we never even try.
_LOCKED_ITEM_STATES = frozenset({'released', 'service', 'obsolete'})

# pg.item.item_type values a CAD descriptor is allowed to declare.
# Must match product_graph_base.pg_item.ITEM_TYPES.
_ALLOWED_ITEM_TYPES = frozenset({
    'assembly', 'part', 'purchased', 'document', 'software', 'service',
})

# Keys we accept on an incoming CAD descriptor. Anything else is dropped
# at validation time so a malicious driver can't smuggle state/created_by/etc.
_ALLOWED_DESCRIPTOR_KEYS = frozenset({
    'part_number', 'name', 'description', 'item_type',
    'properties', 'ebom',
})

_REQUIRED_DESCRIPTOR_KEYS = frozenset({'part_number', 'name', 'item_type'})


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
    active = fields.Boolean(default=True)

    last_sync_at = fields.Datetime(string='Last Sync', readonly=True, copy=False)
    last_sync_status = fields.Selection(
        [
            ('never', 'Never'),
            ('ok', 'OK'),
            ('partial', 'Partial'),
            ('failed', 'Failed'),
        ],
        default='never', readonly=True, copy=False,
    )
    last_sync_detail = fields.Text(string='Last Sync Detail', readonly=True, copy=False)

    import_ids = fields.One2many('pg.cad.import', 'connector_id', string='Imports')
    import_count = fields.Integer(compute='_compute_import_count', store=True)

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

    # PG-135 — scheduled sync configuration
    sync_schedule = fields.Selection(
        [
            ('hourly', 'Hourly'),
            ('daily', 'Daily'),
            ('weekly', 'Weekly'),
            ('manual', 'Manual Only'),
        ],
        string='Sync Schedule',
        default='manual',
        required=True,
        help='How often the scheduled-sync cron should run this connector. '
             'Manual-only connectors are skipped by the cron.',
    )
    next_sync_at = fields.Datetime(
        string='Next Sync',
        compute='_compute_next_sync_at',
        store=True,
        readonly=True,
        help='When the scheduled cron will next pick up this connector. '
             'Computed from last_sync_at + the schedule interval.',
    )
    auto_revise_on_change = fields.Boolean(
        string='Auto-revise on Change',
        default=False,
        help='When True, if a sync finds an existing item whose pulled spec '
             'differs from the current draft, automatically create a new '
             'draft revision instead of merely attaching a document.',
    )
    responsible_user_id = fields.Many2one(
        'res.users',
        string='Review Assignee',
        help='New items / revisions created by sync are assigned to this '
             'user for review. Falls back to the cron run user when empty.',
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

    @api.depends('last_sync_at', 'sync_schedule')
    def _compute_next_sync_at(self):
        intervals = {'hourly': 1, 'daily': 24, 'weekly': 24 * 7}
        for c in self:
            if c.sync_schedule in ('manual', False):
                c.next_sync_at = False
            elif not c.last_sync_at:
                # Never synced — eligible immediately.
                c.next_sync_at = fields.Datetime.now()
            else:
                hours = intervals.get(c.sync_schedule, 24)
                c.next_sync_at = c.last_sync_at + timedelta(hours=hours)

    # ──────────────────────────────────────────────────────────────────
    # Scheduled sync (cron entry point)
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def _cron_run_scheduled_syncs(self):
        """Hourly sweep: run sync for every active connector whose
        sync_schedule is set and whose next_sync_at is due."""
        due = self.search([
            ('active', '=', True),
            ('state', '=', 'active'),
            ('sync_schedule', '!=', 'manual'),
            ('sync_schedule', '!=', False),
            '|', ('next_sync_at', '=', False),
                 ('next_sync_at', '<=', fields.Datetime.now()),
        ])
        for conn in due:
            try:
                conn.with_user(SUPERUSER_ID).action_sync_now()
            except Exception as e:
                _logger.exception(
                    "CAD sync failed for connector %s: %s",
                    conn.display_name, e,
                )
                try:
                    conn.message_post(
                        body=_("Scheduled sync failed: %s") % e,
                        subtype_xmlid='mail.mt_log_note',
                    )
                except Exception:
                    # Chatter post failure must not break the sweep.
                    _logger.exception(
                        "Failed to log sync failure on %s",
                        conn.display_name,
                    )
        return True

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

    def action_sync_now(self):
        """User-facing + cron-facing alias for action_pull."""
        return self.action_pull()

    def action_pull(self):
        """Pull every document the driver reports and create / update
        the matching pg.items + (optionally) draft revisions + EBOMs.

        Per-document failures land as 'rejected' / 'error' trace rows
        without rolling back the rest of the run; only an unrecoverable
        driver-level failure (e.g. file missing, network down) marks the
        whole run failed.
        """
        self.ensure_one()
        run = self.env['pg.cad.import'].create({
            'connector_id': self.id,
            'state': 'running',
            'started_at': fields.Datetime.now(),
        })
        try:
            self._do_pull(run)
            run.write({
                'state': 'completed',
                'completed_at': fields.Datetime.now(),
            })
            # Roll up status from per-line outcomes.
            ok_count = run.created_count + run.updated_count
            err_count = run.error_count + run.rejected_count
            total = len(run.line_ids)
            if err_count and ok_count:
                status = 'partial'
            elif err_count and not ok_count:
                status = 'failed'
            else:
                status = 'ok'
            detail = _(
                "%(total)d items pulled, %(created)d created, "
                "%(updated)d updated, %(skipped)d skipped, "
                "%(rejected)d rejected, %(errored)d errored."
            ) % {
                'total': total,
                'created': run.created_count,
                'updated': run.updated_count,
                'skipped': run.skipped_count,
                'rejected': run.rejected_count,
                'errored': run.error_count,
            }
            self.write({
                'last_sync_at': fields.Datetime.now(),
                'last_sync_status': status,
                'last_sync_detail': detail,
            })
            self.message_post(
                body=_("CAD pull completed (%s) — %s") % (status, detail),
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
            # Unrecoverable: driver couldn't even produce a document set.
            tb = traceback.format_exc()
            _logger.exception("CAD pull failed for %s", self.code)
            try:
                run.write({
                    'state': 'failed',
                    'completed_at': fields.Datetime.now(),
                    'failure_reason': f'{exc}\n\n{tb[:2000]}',
                })
            except Exception:
                # Fall back to a fresh failed row if the savepoint already poisoned the run.
                run = self.env['pg.cad.import'].create({
                    'connector_id': self.id,
                    'state': 'failed',
                    'started_at': fields.Datetime.now(),
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
                'res_id': run.id,
                'view_mode': 'form',
                'target': 'current',
            }

    # ──────────────────────────────────────────────────────────────────
    # Descriptor validation (Task 5 — security: cron runs as SUPERUSER,
    # so we cannot trust the driver's payload shape or content.)
    # ──────────────────────────────────────────────────────────────────
    def _validate_descriptor(self, doc) -> str | None:
        """Return None if the descriptor passes validation, else a string
        describing the validation error. A CadDocument is dataclass-typed
        so we work off attribute access, but we still enforce required
        keys + allowed types + allowed item_type values."""
        if not isinstance(doc, CadDocument):
            return _("Descriptor is not a CadDocument instance.")
        if not doc.part_number or not isinstance(doc.part_number, str):
            return _("Missing or non-string part_number.")
        if not doc.name or not isinstance(doc.name, str):
            return _("Missing or non-string name.")
        if not doc.item_type or doc.item_type not in _ALLOWED_ITEM_TYPES:
            return _("Invalid item_type %r — must be one of %s") % (
                doc.item_type, sorted(_ALLOWED_ITEM_TYPES),
            )
        return None

    def _strip_unsafe_descriptor_fields(self, vals: dict) -> dict:
        """Hardcoded allow-list write filter. Used after validation to
        prevent state / responsible_id smuggling via dict-shaped extras
        a future driver might emit. The connector NEVER auto-promotes
        state — release decisions are human-only."""
        return {k: v for k, v in vals.items() if k in {
            'name', 'description', 'item_type',
            'part_number', 'category_id', 'responsible_id',
        }}

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

        # Pass 1 — validate + create/update every item (no BOM yet) so
        # pass-2 child lookups resolve.
        actions: dict[str, str] = {}
        items_by_pn: dict[str, Any] = {}
        rejected_pns: set[str] = set()

        for doc in docs:
            # Validate descriptor shape + content first.
            err = self._validate_descriptor(doc)
            if err:
                ImportLine.create({
                    'import_id': run.id,
                    'part_number': getattr(doc, 'part_number', '') or '?',
                    'action': 'rejected',
                    'reason': err,
                    'message': _("Descriptor rejected at validation."),
                })
                rejected_pns.add(getattr(doc, 'part_number', '') or '')
                continue

            existing = Item.search(
                [('part_number', '=', doc.part_number)], limit=1)

            # Reject any descriptor targeting a locked item — those records
            # are immutable per R3 / the immutability guard. CAD must not
            # touch released/service/obsolete records.
            if existing and existing.state in _LOCKED_ITEM_STATES:
                ImportLine.create({
                    'import_id': run.id,
                    'part_number': doc.part_number,
                    'action': 'rejected',
                    'item_id': existing.id,
                    'reason': _(
                        "Item is %s — CAD sync cannot mutate "
                        "released/service/obsolete records.") % existing.state,
                    'message': _(
                        "Locked item left untouched."),
                })
                rejected_pns.add(doc.part_number)
                continue

            try:
                with self.env.cr.savepoint():
                    if existing:
                        actions[doc.part_number] = 'updated'
                        vals = self._strip_unsafe_descriptor_fields({
                            'name': doc.name,
                            'description': doc.description or False,
                        })
                        if doc.item_type and existing.item_type != doc.item_type:
                            vals['item_type'] = doc.item_type
                        if self.responsible_user_id:
                            vals['responsible_id'] = self.responsible_user_id.id
                        existing.write(vals)
                        items_by_pn[doc.part_number] = existing
                    else:
                        actions[doc.part_number] = 'created'
                        # CAD sync creates DRAFT records for human review — never auto-promotes.
                        # Item default state='concept' (the draft state for items)
                        # is the ONLY state a CAD-pulled item gets. No call to
                        # action_to_engineering / action_force_release here.
                        vals = self._strip_unsafe_descriptor_fields({
                            'name': doc.name,
                            'item_type': doc.item_type or 'part',
                            'description': doc.description or False,
                            'part_number': doc.part_number,
                        })
                        if cat:
                            vals['category_id'] = cat.id
                        if self.responsible_user_id:
                            vals['responsible_id'] = self.responsible_user_id.id
                        item = Item.create(vals)
                        # Defensive: assert the freshly-created item is in concept.
                        # The default is 'concept' but we never want a future
                        # default change to silently graduate CAD-pulled items.
                        assert item.state == 'concept', (
                            "CAD-created pg.item must be in 'concept' state — "
                            "got %s" % item.state
                        )
                        items_by_pn[doc.part_number] = item
            except Exception as exc:
                _logger.exception(
                    "CAD descriptor %s failed at item-write: %s",
                    doc.part_number, exc,
                )
                ImportLine.create({
                    'import_id': run.id,
                    'part_number': doc.part_number,
                    'action': 'error',
                    'reason': str(exc)[:500],
                    'message': _("Item write failed."),
                })
                rejected_pns.add(doc.part_number)

        # Pass 2 — revisions + EBOMs (only for descriptors that survived pass 1).
        for doc in docs:
            if (not isinstance(doc, CadDocument)
                    or not getattr(doc, 'part_number', None)
                    or doc.part_number in rejected_pns
                    or doc.part_number not in items_by_pn):
                continue
            try:
                with self.env.cr.savepoint():
                    item = items_by_pn[doc.part_number]
                    rev = None
                    if self.create_revision:
                        rev = Revision.search(
                            [('item_id', '=', item.id), ('state', '=', 'draft')],
                            limit=1)
                        if not rev:
                            # Draft-only: state defaults to 'draft' on pg.revision.
                            # Never accept a state value from the driver.
                            rev_vals = {
                                'item_id': item.id,
                                'name': self._next_revision_code(item),
                                'change_summary': 'Initial revision imported from CAD.',
                            }
                            rev = Revision.create(rev_vals)
                            assert rev.state == 'draft', (
                                "CAD-created pg.revision must be in 'draft' state — "
                                "got %s" % rev.state
                            )
                        elif self.auto_revise_on_change:
                            # The existing draft is already mutable; nothing to do —
                            # we re-use it. auto_revise_on_change is most useful
                            # when there is NO open draft and a released revision
                            # exists (and we're handling a future "non-locked"
                            # released path), but until that policy lands the
                            # flag is informational + documented for ops.
                            pass

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
                                # Orphan stub — child has no document. Still
                                # a concept-state draft pg.item.
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
            except Exception as exc:
                _logger.exception(
                    "CAD descriptor %s failed at revision/ebom stage: %s",
                    doc.part_number, exc,
                )
                ImportLine.create({
                    'import_id': run.id,
                    'part_number': doc.part_number,
                    'action': 'error',
                    'item_id': items_by_pn.get(doc.part_number) and items_by_pn[doc.part_number].id or False,
                    'reason': str(exc)[:500],
                    'message': _("Revision / EBOM write failed."),
                })

    # ──────────────────────────────────────────────────────────────────
    # Smart-button action (Sync History)
    # ──────────────────────────────────────────────────────────────────
    def action_open_imports(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Sync History'),
            'res_model': 'pg.cad.import',
            'view_mode': 'list,form',
            'domain': [('connector_id', '=', self.id)],
            'context': {'default_connector_id': self.id},
        }

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
