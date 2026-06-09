# -*- coding: utf-8 -*-
"""
ProductGraph REST API v1

Base URL: /api/v1/productgraph/

Endpoints:
  GET  /health                           — public health check
  GET  /items                            — list items (paginated)
  GET  /items/<item_number>              — item detail
  GET  /items/<item_number>/revisions    — item's revisions
  GET  /revisions/<id>/bom               — revision's engineering BOM
  POST /items                            — create item (engineer+)
"""
import json
import logging
from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

_PG_BASE = '/api/v1/productgraph'


def _json_response(data, status=200):
    return Response(
        json.dumps(data, default=str),
        status=status,
        mimetype='application/json',
    )


def _error(message, status=400):
    return _json_response({'error': message}, status=status)


def _require_group(group_xml_id):
    """Return True if current user has the group, else False."""
    return request.env.user.has_group(group_xml_id)


class ProductGraphController(http.Controller):

    # ================================================================
    # Health
    # ================================================================

    @http.route(f'{_PG_BASE}/health', auth='public', methods=['GET'], csrf=False)
    def health(self, **kwargs):
        return _json_response({
            'status': 'ok',
            'module': 'product_graph',
            'version': '19.0.1.0.0',
        })

    # ================================================================
    # Items
    # ================================================================

    @http.route(f'{_PG_BASE}/items', auth='user', methods=['GET'], csrf=False)
    def list_items(self, limit=50, offset=0, state=None, **kwargs):
        if not _require_group('product_graph.group_pg_user'):
            return _error('Forbidden', 403)
        domain = []
        if state:
            domain.append(('state', '=', state))
        items = request.env['pg.item'].search(
            domain,
            limit=int(limit),
            offset=int(offset),
            order='item_number asc',
        )
        return _json_response({
            'count': len(items),
            'items': [self._item_to_dict(i) for i in items],
        })

    @http.route(f'{_PG_BASE}/items/<string:item_number>', auth='user', methods=['GET'], csrf=False)
    def get_item(self, item_number, **kwargs):
        if not _require_group('product_graph.group_pg_user'):
            return _error('Forbidden', 403)
        item = request.env['pg.item'].search(
            [('item_number', '=', item_number)], limit=1
        )
        if not item:
            return _error(f'Item {item_number!r} not found', 404)
        return _json_response(self._item_to_dict(item, detail=True))

    @http.route(
        f'{_PG_BASE}/items/<string:item_number>/revisions',
        auth='user', methods=['GET'], csrf=False,
    )
    def get_item_revisions(self, item_number, **kwargs):
        if not _require_group('product_graph.group_pg_user'):
            return _error('Forbidden', 403)
        item = request.env['pg.item'].search(
            [('item_number', '=', item_number)], limit=1
        )
        if not item:
            return _error(f'Item {item_number!r} not found', 404)
        return _json_response({
            'item_number': item_number,
            'revisions': [self._revision_to_dict(r) for r in item.revision_ids],
        })

    @http.route(
        f'{_PG_BASE}/revisions/<int:revision_id>/bom',
        auth='user', methods=['GET'], csrf=False,
    )
    def get_revision_bom(self, revision_id, **kwargs):
        if not _require_group('product_graph.group_pg_user'):
            return _error('Forbidden', 403)
        rev = request.env['pg.revision'].browse(revision_id)
        if not rev.exists():
            return _error(f'Revision {revision_id} not found', 404)
        return _json_response({
            'revision_id': rev.id,
            'revision_code': rev.revision_code,
            'item_number': rev.item_id.item_number,
            'bom_lines': [
                {
                    'sequence': line.sequence,
                    'component_item_number': line.component_item_id.item_number,
                    'component_name': line.component_item_id.name,
                    'qty': line.product_qty,
                    'uom': line.product_uom_id.name,
                    'reference': line.reference,
                    'type': line.bom_line_type,
                }
                for line in rev.bom_line_ids.sorted('sequence')
            ],
        })

    @http.route(f'{_PG_BASE}/items', auth='user', methods=['POST'], csrf=False,
                type='json')
    def create_item(self, **kwargs):
        if not _require_group('product_graph.group_pg_engineer'):
            return {'error': 'Forbidden — Engineer role required'}
        body = request.jsonrequest
        try:
            item = request.env['pg.item'].create({
                'name':        body['name'],
                'item_type':   body.get('item_type', 'manufactured'),
                'category_id': body['category_id'],
            })
        except Exception as exc:
            return {'error': str(exc)}
        return self._item_to_dict(item)

    # ================================================================
    # Serialisers
    # ================================================================

    @staticmethod
    def _item_to_dict(item, detail=False):
        d = {
            'id':            item.id,
            'item_number':   item.item_number,
            'name':          item.name,
            'item_type':     item.item_type,
            'state':         item.state,
            'category':      item.category_id.complete_name,
            'current_rev':   item.current_revision_name,
            'responsible':   item.responsible_id.name,
        }
        if detail:
            d['revisions'] = [
                {'id': r.id, 'code': r.revision_code, 'state': r.state}
                for r in item.revision_ids
            ]
            d['bom_count'] = item.bom_count
            d['mo_count']  = item.mo_count
        return d

    @staticmethod
    def _revision_to_dict(rev):
        return {
            'id':            rev.id,
            'revision_code': rev.revision_code,
            'state':         rev.state,
            'bom_line_count': rev.bom_line_count,
            'released_date': str(rev.released_date) if rev.released_date else None,
            'mrp_bom_id':    rev.mrp_bom_id.id if rev.mrp_bom_id else None,
        }
