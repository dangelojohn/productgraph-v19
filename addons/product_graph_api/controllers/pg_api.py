# -*- coding: utf-8 -*-
"""
ProductGraph REST API v1
========================

JSON controllers under ``/api/v1/productgraph/``. Follows the API Bible.

Auth — every endpoint except ``/health`` uses ``auth='bearer'`` (Odoo's
personal-API-key flow). Clients send ``Authorization: Bearer <key>``;
Odoo's ``ir.http._auth_method_bearer`` validates the key against
``res.users.apikeys`` and binds ``request.env.user`` to the key's owner.
Group checks are re-validated at each mutating endpoint.

Breaking change (19.0.1.2.0): auth changed from ``'user'`` (session-cookie)
to ``'bearer'`` (Authorization header) per CLAUDE.md R6. The MCP sidecar
and any external clients must now send the bearer header. Browser-form
clients (if any) need to switch to API keys; the legacy session-cookie
path no longer authorises these routes (browsers may still pass via
``auth='bearer'`` when Sec-Fetch navigation headers indicate same-origin
navigation, per Odoo's bearer dual-mode contract).

Output conventions
------------------
* All responses are application/json.
* Error envelope: ``{"error": {"code": "...", "message": "..."}}`` + HTTP status.
* Success envelope on list endpoints: ``{"count": N, "items": [...]}``.
* Success envelope on single-record endpoints: the resource dict directly.
"""
import json
import logging
import traceback
from odoo import http, _
from odoo.exceptions import UserError, ValidationError, AccessError
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

PG_BASE = '/api/v1/productgraph'

# ─── Rate limiting (B8.4) ──────────────────────────────────────────────────
# Defaults, overridable via ir.config_parameter (see _rate_limit_check).
# Bearer callers get the tighter default; the public /health route (and any
# other auth='public' route, present or future) gets a looser one since it's
# meant to be hammered by uptime monitors.
DEFAULT_RATE_LIMIT_PER_MINUTE = 120
DEFAULT_RATE_LIMIT_PER_MINUTE_PUBLIC = 300
RATE_LIMIT_PARAM = 'productgraph.api_rate_limit_per_minute'
RATE_LIMIT_PARAM_PUBLIC = 'productgraph.api_rate_limit_per_minute_public'


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _json(data, status=200):
    return Response(
        json.dumps(data, default=str),
        status=status,
        mimetype='application/json',
    )


def _err(code, message, status=400, extra=None):
    body = {'error': {'code': code, 'message': message}}
    if extra:
        body['error']['extra'] = extra
    return _json(body, status=status)


def _check_group(xml_id):
    if not request.env.user.has_group(xml_id):
        return _err('forbidden', f'Requires group {xml_id}.', status=403)
    return None


def _rate_limit_identity():
    """Bucket key for the caller.

    Bearer auth resolves ``request.env.user`` to the API key's *owner*, but
    ``res.users.apikeys._check_credentials`` only returns the uid — not the
    specific apikeys row — so per-key granularity isn't available post-auth
    without re-parsing and re-verifying the raw token (duplicating the auth
    check on every request). We key on the authenticated user id instead:
    distinct users (and therefore distinct keys belonging to different
    people/services) are always throttled independently; multiple keys
    issued to the SAME user share one bucket.

    Unauthenticated callers (only ``/health``, which is ``auth='public'``)
    are keyed by remote IP so they can't dodge the cap by omitting a token.
    """
    if request.env.uid and not request.env.user._is_public():
        return f'uid:{request.env.uid}'
    ip = request.httprequest.environ.get('REMOTE_ADDR') or 'unknown'
    return f'ip:{ip}'


def _rate_limit_check(handler):
    """Fixed-window (60s) per-caller-per-route rate limit, shared across
    all Odoo worker processes via a Postgres upsert on ``pg.api.rate.limit``
    — an in-memory counter would only be visible within the one prefork
    worker that happened to handle the request.

    Returns an ``_err`` Response (HTTP 429) if the configured cap has been
    exceeded for the current window, else ``None``.
    """
    is_public = not request.env.uid or request.env.user._is_public()
    param, default = (
        (RATE_LIMIT_PARAM_PUBLIC, DEFAULT_RATE_LIMIT_PER_MINUTE_PUBLIC)
        if is_public else
        (RATE_LIMIT_PARAM, DEFAULT_RATE_LIMIT_PER_MINUTE)
    )
    ICP = request.env['ir.config_parameter'].sudo()
    try:
        limit = int(ICP.get_param(param, default))
    except (TypeError, ValueError):
        limit = default
    if limit <= 0:
        # 0 (or negative) is the documented escape hatch to disable
        # throttling for this caller class without a code revert.
        return None
    route = f'{handler.__module__}.{handler.__qualname__}'
    identity = _rate_limit_identity()
    count = request.env['pg.api.rate.limit'].sudo()._hit(identity, route)
    if count > limit:
        _logger.warning(
            'ProductGraph API rate limit exceeded: identity=%s route=%s limit=%s/min',
            identity, route, limit,
        )
        return _err(
            'rate_limited',
            f'Rate limit exceeded ({limit} requests/min). Try again shortly.',
            status=429,
            extra={'limit_per_minute': limit},
        )
    return None


def _safe(handler):
    """Wrap a controller method to enforce the shared rate limit and coerce
    known exceptions into JSON errors."""
    def _wrap(self, *args, **kwargs):
        try:
            limited = _rate_limit_check(handler)
            if limited is not None:
                return limited
            return handler(self, *args, **kwargs)
        except AccessError as e:
            return _err('access_denied', str(e), status=403)
        except (UserError, ValidationError) as e:
            return _err('user_error', str(e), status=400)
        except Exception:
            _logger.exception('ProductGraph API unhandled error')
            return _err(
                'server_error',
                'Internal server error. See server log.',
                status=500,
                extra={'trace_id': request.session.sid if request.session else None},
            )
    _wrap.__name__ = handler.__name__
    _wrap.__doc__ = handler.__doc__
    return _wrap


# ─── Serializers ─────────────────────────────────────────────────────────────

def _item_dict(item, detail=False):
    d = {
        'id': item.id,
        'part_number': item.part_number,
        'name': item.name,
        'item_type': item.item_type,
        'category': item.category_id.complete_name if item.category_id else None,
        'state': item.state,
        'responsible': item.responsible_id.name if item.responsible_id else None,
        'current_revision': item.current_revision_name or None,
        'product_id': item.product_id.id if item.product_id else None,
    }
    if detail:
        d['description'] = item.description
        d['tags'] = item.tag_ids.mapped('name')
        d['property_templates'] = [
            {'id': t.id, 'name': t.name, 'code': t.code}
            for t in item.property_template_ids
        ]
        d['revisions'] = [
            {'id': r.id, 'name': r.name, 'state': r.state}
            for r in item.revision_ids
        ]
        d['counts'] = {
            'revisions': item.revision_count,
            'eboms': item.ebom_count,
            'mrp_boms': item.bom_count,
            'mos': item.mo_count,
            'where_used': item.where_used_count,
        }
    return d


def _revision_dict(rev, detail=False):
    d = {
        'id': rev.id,
        'item_part_number': rev.item_id.part_number if rev.item_id else None,
        'name': rev.name,
        'state': rev.state,
        'effective_date': str(rev.effective_date) if rev.effective_date else None,
        'released_by': rev.released_by_id.name if rev.released_by_id else None,
        'release_timestamp': str(rev.release_timestamp) if rev.release_timestamp else None,
        'incompatible_with_previous': rev.incompatible_with_previous,
    }
    if detail:
        d['change_summary'] = rev.change_summary
        d['property_values'] = [
            {
                'field_id': v.field_id.id,
                'field_key': v.field_key,
                'field_label': v.field_label,
                'field_type': v.field_type,
                'value': v.display_value,
            }
            for v in rev.property_value_ids
        ]
        d['document_count'] = rev.document_count
        d['ebom_count'] = getattr(rev, 'ebom_count', 0)
    return d


def _ebom_dict(ebom, detail=False, with_lines=False):
    d = {
        'id': ebom.id,
        'name': ebom.name,
        'root_item_part_number': ebom.root_item_id.part_number if ebom.root_item_id else None,
        'revision_name': ebom.revision_id.name if ebom.revision_id else None,
        'ebom_type': ebom.ebom_type,
        'state': ebom.state,
        'line_count': ebom.line_count,
        'effective_date': str(ebom.effective_date) if ebom.effective_date else None,
        'released_by': ebom.released_by_id.name if ebom.released_by_id else None,
    }
    if detail or with_lines:
        d['lines'] = [
            {
                'id': l.id,
                'sequence': l.sequence,
                'pg_item_id': l.pg_item_id.id,
                'part_number': l.part_number,
                'product_qty': l.product_qty,
                'uom': l.uom_id.name,
                'reference': l.reference,
                'type': l.bom_line_type,
                'is_optional': l.is_optional,
                'alternate_group': l.alternate_group,
                'is_primary': l.is_primary,
            }
            for l in ebom.ebom_line_ids.sorted('sequence')
        ]
    return d


def _release_dict(release, detail=False):
    d = {
        'id': release.id,
        'name': release.name,
        'state': release.state,
        'ebom_id': release.ebom_id.id,
        'ebom_name': release.ebom_id.name,
        'revision_name': release.revision_id.name if release.revision_id else None,
        'root_item_part_number': release.root_item_id.part_number if release.root_item_id else None,
        'mrp_bom_id': release.mrp_bom_id.id if release.mrp_bom_id else None,
        'product_id': release.product_id.id if release.product_id else None,
        'release_timestamp': str(release.release_timestamp) if release.release_timestamp else None,
        'released_by': release.released_by_id.name if release.released_by_id else None,
        'frozen_mo_count': release.frozen_mo_count,
        'effective_date': str(release.effective_date) if release.effective_date else None,
    }
    if detail:
        d['release_reason'] = release.release_reason
        d['failure_reason'] = release.failure_reason
        d['frozen_mo_ids'] = release.frozen_mo_ids.ids
        d['lines'] = [
            {
                'pg_item_id': l.pg_item_id.id,
                'part_number': l.pg_item_id.part_number,
                'product_id': l.product_id.id,
                'qty': l.product_qty,
                'mrp_bom_line_id': l.mrp_bom_line_id.id if l.mrp_bom_line_id else None,
            }
            for l in release.line_ids
        ]
    return d


# ─── Controller ──────────────────────────────────────────────────────────────

class ProductGraphAPI(http.Controller):

    # ════════════════════════════════════════════════════════════════════════
    # Health
    # ════════════════════════════════════════════════════════════════════════

    @http.route(f'{PG_BASE}/health', auth='public', methods=['GET'], csrf=False)
    @_safe
    def health(self, **kwargs):
        return _json({
            'status': 'ok',
            'module': 'product_graph_api',
            'api_version': 'v1',
            'odoo_version': '19',
        })

    # ════════════════════════════════════════════════════════════════════════
    # Items
    # ════════════════════════════════════════════════════════════════════════

    @http.route(f'{PG_BASE}/items', auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def list_items(self, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        domain = []
        for key in ('state', 'item_type'):
            if kwargs.get(key):
                domain.append((key, '=', kwargs[key]))
        if kwargs.get('q'):
            domain += ['|', ('part_number', 'ilike', kwargs['q']),
                       ('name', 'ilike', kwargs['q'])]
        limit = min(int(kwargs.get('limit', 50)), 500)
        offset = int(kwargs.get('offset', 0))
        items = request.env['pg.item'].search(
            domain, limit=limit, offset=offset, order='part_number asc',
        )
        total = request.env['pg.item'].search_count(domain)
        return _json({
            'count': len(items),
            'total': total,
            'limit': limit,
            'offset': offset,
            'items': [_item_dict(i) for i in items],
        })

    @http.route(f'{PG_BASE}/items/<int:item_id>', auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def get_item(self, item_id, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        item = request.env['pg.item'].browse(item_id)
        if not item.exists():
            return _err('not_found', f'Item {item_id} not found', status=404)
        return _json(_item_dict(item, detail=True))

    @http.route(f'{PG_BASE}/items/<int:item_id>/where-used',
                auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def item_where_used(self, item_id, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        item = request.env['pg.item'].browse(item_id)
        if not item.exists():
            return _err('not_found', f'Item {item_id} not found', status=404)
        max_depth = int(kwargs['depth']) if kwargs.get('depth') else None
        rows = request.env['pg.ebom.closure'].where_used(item_id, max_depth)
        return _json({
            'item_id': item_id,
            'part_number': item.part_number,
            'count': len(rows),
            'rows': [
                {
                    'ebom_id': r.ebom_id.id,
                    'ebom_name': r.ebom_id.name,
                    'ancestor_id': r.ancestor_id.id,
                    'ancestor_part_number': r.ancestor_id.part_number,
                    'depth': r.depth,
                    'qty_chain': r.qty_chain,
                }
                for r in rows
            ],
        })

    @http.route(f'{PG_BASE}/items', auth='bearer', methods=['POST'], csrf=False, type='json')
    @_safe
    def create_item(self, **kwargs):
        err = _check_group('product_graph_base.group_pg_engineer')
        if err:
            return err
        # type='json' unwraps JSON-RPC params into kwargs. Validate required.
        missing = [k for k in ('name', 'category_id') if k not in kwargs]
        if missing:
            return _err('bad_request',
                       f'Missing required fields: {", ".join(missing)}', 400)
        item = request.env['pg.item'].create({
            'name': kwargs['name'],
            'item_type': kwargs.get('item_type', 'part'),
            'category_id': kwargs['category_id'],
            'description': kwargs.get('description'),
        })
        return _item_dict(item, detail=True)

    # ════════════════════════════════════════════════════════════════════════
    # Revisions
    # ════════════════════════════════════════════════════════════════════════

    @http.route(f'{PG_BASE}/revisions', auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def list_revisions(self, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        domain = []
        if kwargs.get('item_id'):
            domain.append(('item_id', '=', int(kwargs['item_id'])))
        if kwargs.get('state'):
            domain.append(('state', '=', kwargs['state']))
        limit = min(int(kwargs.get('limit', 50)), 500)
        offset = int(kwargs.get('offset', 0))
        revs = request.env['pg.revision'].search(
            domain, limit=limit, offset=offset, order='item_id, sequence',
        )
        return _json({
            'count': len(revs),
            'revisions': [_revision_dict(r) for r in revs],
        })

    @http.route(f'{PG_BASE}/revisions/<int:rev_id>',
                auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def get_revision(self, rev_id, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        rev = request.env['pg.revision'].browse(rev_id)
        if not rev.exists():
            return _err('not_found', f'Revision {rev_id} not found', status=404)
        return _json(_revision_dict(rev, detail=True))

    @http.route(f'{PG_BASE}/revisions/<int:rev_id>/submit',
                auth='bearer', methods=['POST'], csrf=False, type='json')
    @_safe
    def submit_revision(self, rev_id, **kwargs):
        err = _check_group('product_graph_base.group_pg_engineer')
        if err:
            return err
        rev = request.env['pg.revision'].browse(rev_id)
        if not rev.exists():
            return _err('not_found', f'Revision {rev_id} not found', status=404)
        rev.action_submit_for_review()
        return _revision_dict(rev, detail=True)

    @http.route(f'{PG_BASE}/revisions/<int:rev_id>/release',
                auth='bearer', methods=['POST'], csrf=False, type='json')
    @_safe
    def release_revision(self, rev_id, **kwargs):
        err = _check_group('product_graph_base.group_pg_approver')
        if err:
            return err
        rev = request.env['pg.revision'].browse(rev_id)
        if not rev.exists():
            return _err('not_found', f'Revision {rev_id} not found', status=404)
        rev.action_release()
        return _revision_dict(rev, detail=True)

    # ════════════════════════════════════════════════════════════════════════
    # EBOMs
    # ════════════════════════════════════════════════════════════════════════

    @http.route(f'{PG_BASE}/eboms', auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def list_eboms(self, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        domain = []
        if kwargs.get('root_item_id'):
            domain.append(('root_item_id', '=', int(kwargs['root_item_id'])))
        if kwargs.get('state'):
            domain.append(('state', '=', kwargs['state']))
        limit = min(int(kwargs.get('limit', 50)), 500)
        offset = int(kwargs.get('offset', 0))
        eboms = request.env['pg.ebom'].search(
            domain, limit=limit, offset=offset, order='create_date desc',
        )
        return _json({
            'count': len(eboms),
            'eboms': [_ebom_dict(e) for e in eboms],
        })

    @http.route(f'{PG_BASE}/eboms/<int:ebom_id>',
                auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def get_ebom(self, ebom_id, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        ebom = request.env['pg.ebom'].browse(ebom_id)
        if not ebom.exists():
            return _err('not_found', f'EBOM {ebom_id} not found', status=404)
        return _json(_ebom_dict(ebom, detail=True, with_lines=True))

    @http.route(f'{PG_BASE}/eboms/<int:ebom_id>/explosion',
                auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def ebom_explosion(self, ebom_id, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        ebom = request.env['pg.ebom'].browse(ebom_id)
        if not ebom.exists():
            return _err('not_found', f'EBOM {ebom_id} not found', status=404)
        max_depth = int(kwargs['depth']) if kwargs.get('depth') else None
        rows = request.env['pg.ebom.closure'].explode(ebom_id, max_depth)
        return _json({
            'ebom_id': ebom_id,
            'ebom_name': ebom.name,
            'count': len(rows),
            'rows': [
                {
                    'ancestor_id': r.ancestor_id.id,
                    'ancestor_part_number': r.ancestor_id.part_number,
                    'descendant_id': r.descendant_id.id,
                    'descendant_part_number': r.descendant_id.part_number,
                    'descendant_name': r.descendant_id.name,
                    'depth': r.depth,
                    'qty_chain': r.qty_chain,
                }
                for r in rows
            ],
        })

    # ════════════════════════════════════════════════════════════════════════
    # Releases
    # ════════════════════════════════════════════════════════════════════════

    @http.route(f'{PG_BASE}/releases', auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def list_releases(self, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        domain = []
        if kwargs.get('state'):
            domain.append(('state', '=', kwargs['state']))
        if kwargs.get('root_item_id'):
            domain.append(('root_item_id', '=', int(kwargs['root_item_id'])))
        limit = min(int(kwargs.get('limit', 50)), 500)
        offset = int(kwargs.get('offset', 0))
        releases = request.env['pg.release'].search(
            domain, limit=limit, offset=offset, order='release_timestamp desc',
        )
        return _json({
            'count': len(releases),
            'releases': [_release_dict(r) for r in releases],
        })

    @http.route(f'{PG_BASE}/releases/<int:release_id>',
                auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def get_release(self, release_id, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        rel = request.env['pg.release'].browse(release_id)
        if not rel.exists():
            return _err('not_found', f'Release {release_id} not found', status=404)
        return _json(_release_dict(rel, detail=True))

    @http.route(f'{PG_BASE}/releases/<int:release_id>/status',
                auth='bearer', methods=['GET'], csrf=False)
    @_safe
    def release_status(self, release_id, **kwargs):
        err = _check_group('product_graph_base.group_pg_viewer')
        if err:
            return err
        rel = request.env['pg.release'].browse(release_id)
        if not rel.exists():
            return _err('not_found', f'Release {release_id} not found', status=404)
        return _json({
            'id': rel.id,
            'name': rel.name,
            'state': rel.state,
            'mrp_bom_id': rel.mrp_bom_id.id if rel.mrp_bom_id else None,
            'frozen_mo_count': rel.frozen_mo_count,
            'failure_reason': rel.failure_reason,
        })

    @http.route(f'{PG_BASE}/releases', auth='bearer', methods=['POST'],
                csrf=False, type='json')
    @_safe
    def create_release(self, **kwargs):
        err = _check_group('product_graph_base.group_pg_approver')
        if err:
            return err
        body = request.get_json_data() if hasattr(request, 'get_json_data') else {}
        if not body and request.httprequest.data:
            body = json.loads(request.httprequest.data)
        rel = request.env['pg.release'].create({
            'ebom_id': body['ebom_id'],
            'release_reason': body['release_reason'],
            'effective_date': body.get('effective_date'),
            'archive_previous_bom': body.get('archive_previous_bom', True),
            'create_product_if_missing': body.get('create_product_if_missing', True),
        })
        if body.get('execute', True):
            rel.action_execute_release()
        return _release_dict(rel, detail=True)
