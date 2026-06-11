# -*- coding: utf-8 -*-
"""ProductGraph REST API v1 — Phase 2 endpoints.

Adds the read-only surface for the Phase-2 modules (eco, vendor, rfq,
relationship, cad). Mutating actions on these models stay human-in-the-
loop via the Odoo UI; this layer is for read/list integration with the
MCP sidecar and external automation.

All routes registered conditionally — if a Phase-2 module isn't
installed, the corresponding endpoints return 404 (they simply don't
register their model handler).
"""
import json
import logging

from odoo import http
from odoo.http import request

from .pg_api import PG_BASE, _check_group, _err, _json, _safe

_logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────
def _eco_dict(eco, detail=False):
    d = {
        'id': eco.id,
        'name': eco.name,
        'title': eco.title,
        'state': eco.state,
        'change_type': eco.change_type,
        'priority': eco.priority,
        'requestor_id': eco.requestor_id.id if eco.requestor_id else None,
        'engineering_lead_id': eco.engineering_lead_id.id if eco.engineering_lead_id else None,
        'effective_date': eco.effective_date,
        'target_release_date': eco.target_release_date,
        'actual_release_date': eco.actual_release_date,
        'line_count': eco.line_count,
        'rejection_reason': eco.rejection_reason,
    }
    if detail:
        d['lines'] = [{
            'id': l.id,
            'item_id': l.item_id.id,
            'item_part_number': l.item_id.part_number,
            'from_revision_id': l.from_revision_id.id if l.from_revision_id else None,
            'to_revision_id': l.to_revision_id.id if l.to_revision_id else None,
            'change_kind': l.change_kind,
            'status': l.status,
            'notes': l.notes,
        } for l in eco.line_ids]
        d['approvals'] = [{
            'id': a.id,
            'decided_by_id': a.user_id.id,
            'decision': a.decision,
            'signed_on': a.signed_on,
            'comment': a.comment,
        } for a in eco.approval_ids]
    return d


def _vendor_dict(vendor, detail=False):
    d = {
        'id': vendor.id,
        'code': vendor.code,
        'name': vendor.name,
        'state': vendor.state,
        'is_approved': vendor.is_approved,
        'qualified_on': vendor.qualified_on,
        'qualification_expires_on': vendor.qualification_expires_on,
        'vendor_part_count': vendor.vendor_part_count,
        'item_count': vendor.item_count,
        'partner_id': vendor.partner_id.id if vendor.partner_id else None,
        'active': vendor.active,
    }
    if detail:
        d['qualification_log'] = [{
            'id': q.id,
            'decision': q.decision,
            'decided_by_id': q.decided_by_id.id,
            'decided_on': q.decided_on,
            'comment': q.comment,
        } for q in vendor.qualification_ids[:20]]
    return d


def _vendor_part_dict(part):
    return {
        'id': part.id,
        'item_id': part.item_id.id,
        'vendor_id': part.vendor_id.id,
        'vendor_part_number': part.vendor_part_number,
        'rank': part.rank,
        'is_preferred': part.is_preferred,
        'is_qualified': part.is_qualified,
        'vendor_state': part.vendor_state,
        'moq': part.moq,
        'unit_price_hint': part.unit_price_hint,
        'currency_id': part.currency_id.id if part.currency_id else None,
        'lead_time_days': part.lead_time_days,
        'active': part.active,
    }


def _rfq_dict(rfq, detail=False):
    d = {
        'id': rfq.id,
        'name': rfq.name,
        'title': rfq.title,
        'state': rfq.state,
        'requestor_id': rfq.requestor_id.id if rfq.requestor_id else None,
        'due_date': rfq.due_date,
        'sent_on': rfq.sent_on,
        'awarded_on': rfq.awarded_on,
        'line_count': rfq.line_count,
        'bid_count': rfq.bid_count,
        'promote_to_preferred': rfq.promote_to_preferred,
    }
    if detail:
        d['lines'] = []
        for line in rfq.line_ids:
            d['lines'].append({
                'id': line.id,
                'item_id': line.item_id.id,
                'item_part_number': line.item_id.part_number,
                'qty': line.qty,
                'target_date': line.target_date,
                'spec_notes': line.spec_notes,
                'bids': [{
                    'id': b.id,
                    'vendor_id': b.vendor_id.id,
                    'vendor_part_number': b.vendor_part_number,
                    'unit_price': b.unit_price,
                    'currency_id': b.currency_id.id if b.currency_id else None,
                    'moq': b.moq,
                    'lead_time_days': b.lead_time_days,
                    'valid_until': b.valid_until,
                    'is_awarded': b.is_awarded,
                    'comment': b.comment,
                } for b in line.bid_ids],
            })
    return d


def _relationship_dict(rel):
    return {
        'id': rel.id,
        'source_item_id': rel.source_item_id.id,
        'target_item_id': rel.target_item_id.id,
        'kind': rel.kind,
        'is_symmetric': rel.is_symmetric,
        'eco_id': rel.eco_id.id if rel.eco_id else None,
        'notes': rel.notes,
        'active': rel.active,
    }


def _cad_connector_dict(conn, detail=False):
    d = {
        'id': conn.id,
        'code': conn.code,
        'name': conn.name,
        'driver_code': conn.driver_code,
        'state': conn.state,
        'last_sync_at': conn.last_sync_at,
        'last_sync_status': conn.last_sync_status,
        'last_sync_detail': conn.last_sync_detail,
        'import_count': conn.import_count,
    }
    if detail:
        d['imports'] = [{
            'id': i.id,
            'create_date': i.create_date,
            'state': i.state,
            'started_at': i.started_at,
            'completed_at': i.completed_at,
            'line_count': i.line_count,
            'created_count': i.created_count,
            'updated_count': i.updated_count,
            'skipped_count': i.skipped_count,
        } for i in conn.import_ids[:20]]
    return d


# ──────────────────────────────────────────────────────────────────────────
# Controller
# ──────────────────────────────────────────────────────────────────────────
class ProductGraphPhase2API(http.Controller):

    # ──────────────────────────────────────────────────────────────────
    # ECO
    # ──────────────────────────────────────────────────────────────────
    @http.route(f'{PG_BASE}/ecos', auth='user', methods=['GET'], csrf=False)
    @_safe
    def list_ecos(self, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        if 'pg.eco' not in request.env:
            return _err('not_installed', 'product_graph_eco not installed', 404)
        domain = []
        if kw.get('state'):
            domain.append(('state', '=', kw['state']))
        if kw.get('open_only'):
            domain.append(('state', 'not in', ('released', 'rejected')))
        limit = min(int(kw.get('limit', 100)), 500)
        ecos = request.env['pg.eco'].search(
            domain, limit=limit, offset=int(kw.get('offset', 0)),
            order='create_date desc',
        )
        return _json({
            'count': len(ecos),
            'ecos': [_eco_dict(e) for e in ecos],
        })

    @http.route(f'{PG_BASE}/ecos/<int:eco_id>',
                auth='user', methods=['GET'], csrf=False)
    @_safe
    def get_eco(self, eco_id, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        if 'pg.eco' not in request.env:
            return _err('not_installed', 'product_graph_eco not installed', 404)
        eco = request.env['pg.eco'].browse(eco_id)
        if not eco.exists():
            return _err('not_found', f'ECO {eco_id} not found', 404)
        return _json(_eco_dict(eco, detail=True))

    # ──────────────────────────────────────────────────────────────────
    # Vendor / AVL
    # ──────────────────────────────────────────────────────────────────
    @http.route(f'{PG_BASE}/vendors', auth='user', methods=['GET'], csrf=False)
    @_safe
    def list_vendors(self, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        domain = []
        if kw.get('state'):
            domain.append(('state', '=', kw['state']))
        if kw.get('qualified_only'):
            domain.append(('state', '=', 'qualified'))
        limit = min(int(kw.get('limit', 100)), 500)
        vendors = request.env['pg.vendor'].search(
            domain, limit=limit, offset=int(kw.get('offset', 0)),
            order='name asc',
        )
        return _json({
            'count': len(vendors),
            'vendors': [_vendor_dict(v) for v in vendors],
        })

    @http.route(f'{PG_BASE}/vendors/<int:vendor_id>',
                auth='user', methods=['GET'], csrf=False)
    @_safe
    def get_vendor(self, vendor_id, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        vendor = request.env['pg.vendor'].browse(vendor_id)
        if not vendor.exists():
            return _err('not_found', f'Vendor {vendor_id} not found', 404)
        return _json(_vendor_dict(vendor, detail=True))

    @http.route(f'{PG_BASE}/items/<int:item_id>/vendor-parts',
                auth='user', methods=['GET'], csrf=False)
    @_safe
    def list_vendor_parts_for_item(self, item_id, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        parts = request.env['pg.vendor.part'].search(
            [('item_id', '=', item_id), ('active', '=', True)],
            order='is_preferred desc, rank asc',
            limit=min(int(kw.get('limit', 100)), 500),
        )
        return _json({
            'count': len(parts),
            'item_id': item_id,
            'vendor_parts': [_vendor_part_dict(p) for p in parts],
        })

    # ──────────────────────────────────────────────────────────────────
    # RFQ
    # ──────────────────────────────────────────────────────────────────
    @http.route(f'{PG_BASE}/rfqs', auth='user', methods=['GET'], csrf=False)
    @_safe
    def list_rfqs(self, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        if 'pg.rfq' not in request.env:
            return _err('not_installed', 'product_graph_rfq not installed', 404)
        domain = []
        if kw.get('state'):
            domain.append(('state', '=', kw['state']))
        if kw.get('open_only'):
            domain.append(('state', 'not in', ('awarded', 'cancelled')))
        limit = min(int(kw.get('limit', 100)), 500)
        rfqs = request.env['pg.rfq'].search(
            domain, limit=limit, offset=int(kw.get('offset', 0)),
            order='create_date desc',
        )
        return _json({
            'count': len(rfqs),
            'rfqs': [_rfq_dict(r) for r in rfqs],
        })

    @http.route(f'{PG_BASE}/rfqs/<int:rfq_id>',
                auth='user', methods=['GET'], csrf=False)
    @_safe
    def get_rfq(self, rfq_id, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        if 'pg.rfq' not in request.env:
            return _err('not_installed', 'product_graph_rfq not installed', 404)
        rfq = request.env['pg.rfq'].browse(rfq_id)
        if not rfq.exists():
            return _err('not_found', f'RFQ {rfq_id} not found', 404)
        return _json(_rfq_dict(rfq, detail=True))

    # ──────────────────────────────────────────────────────────────────
    # Relationship graph
    # ──────────────────────────────────────────────────────────────────
    @http.route(f'{PG_BASE}/items/<int:item_id>/relationships',
                auth='user', methods=['GET'], csrf=False)
    @_safe
    def relationships_for_item(self, item_id, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        if 'pg.relationship' not in request.env:
            return _err(
                'not_installed', 'product_graph_relationship not installed', 404)
        Rel = request.env['pg.relationship']
        outgoing = Rel.search(
            [('source_item_id', '=', item_id), ('active', '=', True)])
        incoming = Rel.search(
            [('target_item_id', '=', item_id), ('active', '=', True)])
        return _json({
            'item_id': item_id,
            'outgoing_count': len(outgoing),
            'incoming_count': len(incoming),
            'outgoing': [_relationship_dict(r) for r in outgoing],
            'incoming': [_relationship_dict(r) for r in incoming],
        })

    @http.route(f'{PG_BASE}/items/<int:item_id>/substitutes',
                auth='user', methods=['GET'], csrf=False)
    @_safe
    def substitute_set_for_item(self, item_id, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        if 'pg.relationship.closure' not in request.env:
            return _err(
                'not_installed',
                'product_graph_relationship closure not installed', 404)
        item = request.env['pg.item'].browse(item_id)
        if not item.exists():
            return _err('not_found', f'Item {item_id} not found', 404)
        Closure = request.env['pg.relationship.closure']
        subs = Closure.substitute_set(item)
        return _json({
            'item_id': item_id,
            'count': len(subs),
            'substitutes': [{
                'id': i.id,
                'part_number': i.part_number,
                'name': i.name,
                'state': i.state,
            } for i in subs],
        })

    @http.route(f'{PG_BASE}/items/<int:item_id>/supersession-chain',
                auth='user', methods=['GET'], csrf=False)
    @_safe
    def supersession_chain_for_item(self, item_id, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        if 'pg.relationship.closure' not in request.env:
            return _err(
                'not_installed',
                'product_graph_relationship closure not installed', 404)
        item = request.env['pg.item'].browse(item_id)
        if not item.exists():
            return _err('not_found', f'Item {item_id} not found', 404)
        Closure = request.env['pg.relationship.closure']
        forward = Closure.supersession_chain(item, direction='forward')
        back = Closure.supersession_chain(item, direction='back')
        return _json({
            'item_id': item_id,
            'predecessors': [{
                'id': i.id, 'part_number': i.part_number, 'name': i.name,
            } for i in back],
            'successors': [{
                'id': i.id, 'part_number': i.part_number, 'name': i.name,
            } for i in forward],
        })

    # ──────────────────────────────────────────────────────────────────
    # CAD
    # ──────────────────────────────────────────────────────────────────
    @http.route(f'{PG_BASE}/cad/connectors',
                auth='user', methods=['GET'], csrf=False)
    @_safe
    def list_cad_connectors(self, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        if 'pg.cad.connector' not in request.env:
            return _err(
                'not_installed', 'product_graph_cad not installed', 404)
        conns = request.env['pg.cad.connector'].search([], order='code')
        return _json({
            'count': len(conns),
            'connectors': [_cad_connector_dict(c) for c in conns],
        })

    @http.route(f'{PG_BASE}/cad/connectors/<int:conn_id>',
                auth='user', methods=['GET'], csrf=False)
    @_safe
    def get_cad_connector(self, conn_id, **kw):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        if 'pg.cad.connector' not in request.env:
            return _err(
                'not_installed', 'product_graph_cad not installed', 404)
        conn = request.env['pg.cad.connector'].browse(conn_id)
        if not conn.exists():
            return _err('not_found', f'Connector {conn_id} not found', 404)
        return _json(_cad_connector_dict(conn, detail=True))

    @http.route(f'{PG_BASE}/cad/connectors/<int:conn_id>/test',
                auth='user', methods=['POST'], csrf=False)
    @_safe
    def test_cad_connector(self, conn_id, **kw):
        err = _check_group('product_graph_base.group_pg_engineer')
        if err:
            return err
        if 'pg.cad.connector' not in request.env:
            return _err(
                'not_installed', 'product_graph_cad not installed', 404)
        conn = request.env['pg.cad.connector'].browse(conn_id)
        if not conn.exists():
            return _err('not_found', f'Connector {conn_id} not found', 404)
        driver = conn._driver()
        meta = driver.fetch_metadata()
        return _json(meta)
