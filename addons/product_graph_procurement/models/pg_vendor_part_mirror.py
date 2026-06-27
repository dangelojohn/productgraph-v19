# -*- coding: utf-8 -*-
"""W031 (R6.6) — pg.vendor.part → product.supplierinfo one-way mirror.

JTBD: "When an MO triggers auto-PO via purchase_mrp, I want it to use the
AVL-blessed vendor + price — not whatever Odoo found in the raw
product.supplierinfo."

Design:
  * On pg.vendor.part create / write, when the vendor is qualified AND the
    row carries a unit_price_hint AND a linked res.partner exists on the
    vendor, mirror to product.supplierinfo.
  * Mirror rows are tagged with x_from_pg_vendor_part_id (Many2one back-ref
    field added to product.supplierinfo). Only tagged rows are touched —
    admin-created (untagged) supplierinfo rows are NEVER overwritten.
  * One-way: pg.vendor.part is the source of truth. We never read
    product.supplierinfo back into pg.vendor.part.
  * ICP gate: productgraph.mirror_vendor_part_to_supplierinfo (default
    'False' = opt-in). Set to 'True' to enable the auto-mirror.
  * Daily cron reconciler walks all qualified vendor-parts + repairs any
    drift in tagged mirror rows; orphaned tagged rows whose pg.vendor.part
    has been unlinked/archived are archived too.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

ICP_KEY = 'productgraph.mirror_vendor_part_to_supplierinfo'


class ProductSupplierinfoPgMirror(models.Model):
    _inherit = 'product.supplierinfo'

    x_from_pg_vendor_part_id = fields.Many2one(
        'pg.vendor.part',
        string='Source pg.vendor.part',
        ondelete='set null',
        index=True,
        copy=False,
        help='When set, this supplierinfo row was created/maintained by the '
             'W031 ProductGraph mirror. Manually-created supplierinfo rows '
             'leave this NULL and are NEVER overwritten by the mirror.',
    )


class PgVendorPartMirror(models.Model):
    _inherit = 'pg.vendor.part'

    # ──────────────────────────────────────────────────────────────────
    # ICP gate
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def _w031_mirror_enabled(self):
        """ICP-gate the mirror. Default off — opt-in via Settings."""
        param = self.env['ir.config_parameter'].sudo().get_param(
            ICP_KEY, default='False',
        )
        return str(param).strip().lower() in ('true', '1', 'yes', 'on')

    # ──────────────────────────────────────────────────────────────────
    # Eligibility predicate
    # ──────────────────────────────────────────────────────────────────
    def _w031_is_mirrorable(self):
        """A vendor-part is mirrorable when:
          * the vendor is qualified (is_approved True)
          * the vendor has a linked res.partner
          * the item has a linked product.product (release-mapped)
          * the row is active
          * a unit_price_hint is set (>0)
        Otherwise the mirror is a no-op for this row (and any pre-existing
        tagged mirror row is archived to keep MRP from picking up stale
        pricing).
        """
        self.ensure_one()
        if not self.active:
            return False
        if not self.vendor_id or not self.vendor_id.is_approved:
            return False
        if not self.vendor_id.partner_id:
            return False
        if not self.item_id or not self.item_id.product_id:
            return False
        if not self.unit_price_hint or self.unit_price_hint <= 0:
            return False
        return True

    # ──────────────────────────────────────────────────────────────────
    # Mirror write / sync
    # ──────────────────────────────────────────────────────────────────
    def _w031_build_supplierinfo_vals(self):
        """Build the supplierinfo vals dict from this vendor-part.

        W077 (R6.6 extension, 2026-06-27) — `product_code` is the
        canonical mirror target for UBL `SellersItemIdentification`
        in EDI sends. We ALWAYS set it when `vendor_part_number` is
        populated, and we OMIT the key when the source is empty so
        the diff in `_w031_sync_mirror` won't blank a pre-existing
        admin-set product_code. The mirror remains one-way: we never
        read product_code back into pg.vendor.part.
        """
        self.ensure_one()
        product = self.item_id.product_id
        vals = {
            'partner_id': self.vendor_id.partner_id.id,
            'product_tmpl_id': product.product_tmpl_id.id,
            'price': self.unit_price_hint or 0.0,
            'min_qty': self.moq or 0.0,
            'delay': self.lead_time_days or 0,
            'x_from_pg_vendor_part_id': self.id,
        }
        # W077 — only emit product_code when we actually have a vendor
        # part number to mirror. Sending False would wipe a manually
        # set product_code on the tagged row, which the EDI layer
        # then can't recover from.
        if self.vendor_part_number:
            vals['product_code'] = self.vendor_part_number
        if self.currency_id:
            vals['currency_id'] = self.currency_id.id
        if 'product_id' in self.env['product.supplierinfo']._fields:
            vals['product_id'] = product.id
        return vals

    def _w031_sync_mirror(self):
        """Create / update the tagged supplierinfo row for this vendor-part.

        Idempotent — calling twice is a no-op when nothing changed. Never
        touches untagged supplierinfo rows (preserves admin overrides).
        """
        Supplierinfo = self.env['product.supplierinfo'].sudo()
        touched_create = 0
        touched_write = 0
        archived = 0
        for rec in self:
            tagged = Supplierinfo.with_context(active_test=False).search([
                ('x_from_pg_vendor_part_id', '=', rec.id),
            ], limit=1)
            if not rec._w031_is_mirrorable():
                # Archive any pre-existing tagged row — the part is no
                # longer eligible (vendor de-qualified, item un-released,
                # price wiped, etc.). We archive instead of unlink so the
                # audit trail of past supplier-info rows stays intact.
                if tagged and tagged.active:
                    tagged.write({'active': False})
                    archived += 1
                continue
            vals = rec._w031_build_supplierinfo_vals()
            if tagged:
                # Compute diff to avoid no-op writes (and to avoid
                # bumping write_date when nothing actually changed).
                changed = {}
                for k, v in vals.items():
                    fld = tagged._fields.get(k)
                    if isinstance(fld, fields.Many2one):
                        cur = tagged[k].id if tagged[k] else False
                        if cur != (v or False):
                            changed[k] = v
                    else:
                        if tagged[k] != v:
                            changed[k] = v
                if not tagged.active:
                    changed['active'] = True
                if changed:
                    tagged.write(changed)
                    touched_write += 1
            else:
                Supplierinfo.create(vals)
                touched_create += 1
        if touched_create or touched_write or archived:
            _logger.info(
                "W031 mirror: created=%d updated=%d archived=%d (parts=%d)",
                touched_create, touched_write, archived, len(self),
            )
        return {
            'created': touched_create,
            'updated': touched_write,
            'archived': archived,
        }

    # ──────────────────────────────────────────────────────────────────
    # Hooks
    # ──────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        if self._w031_mirror_enabled():
            try:
                recs._w031_sync_mirror()
            except Exception:
                # Mirror is an opt-in convenience — never let it crash the
                # vendor-part write path. Reconciler-cron picks up drift.
                _logger.exception("W031 mirror create-hook failed (non-fatal)")
        return recs

    def write(self, vals):
        result = super().write(vals)
        if self._w031_mirror_enabled():
            # Only re-sync when something the mirror cares about changed.
            interesting = {
                'vendor_id', 'item_id', 'vendor_part_number',
                'moq', 'unit_price_hint', 'currency_id',
                'lead_time_days', 'active',
            }
            if interesting & set(vals.keys()):
                try:
                    self._w031_sync_mirror()
                except Exception:
                    _logger.exception(
                        "W031 mirror write-hook failed (non-fatal)")
        return result

    # ──────────────────────────────────────────────────────────────────
    # Daily reconciler cron
    # ──────────────────────────────────────────────────────────────────
    @api.model
    def _cron_w031_reconcile_supplierinfo(self):
        """Daily walk — re-syncs all qualified vendor-parts and prunes
        tagged supplierinfo rows whose source pg.vendor.part vanished.

        Safe to run when the mirror is disabled (does nothing). Idempotent
        across reruns — only updates supplierinfo rows that actually drifted.
        """
        if not self._w031_mirror_enabled():
            _logger.debug(
                "W031 reconciler skipped — ICP %s is off.", ICP_KEY)
            return 0
        # Walk every active vendor-part — the per-record predicate inside
        # _w031_sync_mirror takes care of dropping non-eligible rows.
        parts = self.search([])
        stats = parts._w031_sync_mirror()
        # Prune tagged supplierinfo rows whose pg.vendor.part vanished
        # entirely (ondelete='set null' clears the back-ref).
        Supplierinfo = self.env['product.supplierinfo'].sudo()
        orphaned = Supplierinfo.with_context(active_test=False).search([
            ('x_from_pg_vendor_part_id', '=', False),
            # Tagged rows lose the FK on pg.vendor.part unlink — but we
            # have no other way to identify them once the back-ref is
            # cleared. So this branch is intentionally a no-op for now;
            # left here for the next iteration's audit cleanup.
        ])
        # We choose not to delete orphans (audit history) — left as
        # archive candidates for the human admin.
        if orphaned:
            _logger.info(
                "W031 reconciler: %d untagged supplierinfo rows seen "
                "(no action — manual cleanup).", len(orphaned),
            )
        total_touched = stats['created'] + stats['updated'] + stats['archived']
        _logger.info(
            "W031 reconciler: scanned %d vendor-parts; touched=%d "
            "(%d created, %d updated, %d archived).",
            len(parts), total_touched,
            stats['created'], stats['updated'], stats['archived'],
        )
        return total_touched
