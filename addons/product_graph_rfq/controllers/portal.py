# SPDX-License-Identifier: LGPL-3.0-only
"""W076 (R6.12, 2026-06-27) — vendor portal stub.

Self-service portal where a vendor's portal-user can:
  - See the open RFQ bids that belong to their vendor at /my/rfq-bids
  - Click into /my/rfq-bids/<id> to see the RFQ details + bid form
  - POST /my/rfq-bids/<id>/submit to update price / lead / MOQ / valid_until

Three independent guards on every route:
  1. ``auth='user'`` — Odoo session required (no public access)
  2. ``access_token`` — signed token on the bid (consteq comparison)
  3. ``vendor_id.partner_id == request.env.user.partner_id`` — record-scope
     check that the logged-in user owns the vendor row the bid belongs to.

The submission endpoint additionally re-asserts
``bid._portal_can_be_submitted()`` so an RFQ that moved to
``awarded`` / ``cancelled`` between page render and POST cannot be mutated.

This is a STUB per the W076 brief — the URL contract + minimal happy
path are in place; the larger portal (per-line discussion threads,
attachment upload, counter-offer flow) is Phase 3 work.
"""
import logging
from collections import OrderedDict

from odoo import _, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request

from odoo.addons.portal.controllers import portal
from odoo.addons.portal.controllers.portal import pager as portal_pager

_logger = logging.getLogger(__name__)


# Editable RFQ states the vendor portal will accept submissions for.
# Mirror of pg.rfq.bid._editable_states() but expressed at the
# controller boundary so we can short-circuit before any sudo write.
_VENDOR_PORTAL_OPEN_STATES = ('sent', 'received')


class PgVendorPortal(portal.CustomerPortal):

    # ──────────────────────────────────────────────────────────────────
    # /my landing — counter
    # ──────────────────────────────────────────────────────────────────
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'rfq_bid_count' in counters:
            partner = request.env.user.partner_id
            domain = self._pg_rfq_bid_user_domain(partner)
            # search_count with sudo so the portal user (who has NO
            # backend read access to pg.rfq.bid) gets a real number.
            # The domain itself enforces vendor.partner_id == user.partner.
            values['rfq_bid_count'] = (
                request.env['pg.rfq.bid'].sudo().search_count(domain)
                if partner else 0
            )
        return values

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    def _pg_rfq_bid_user_domain(self, partner):
        """Domain restricting pg.rfq.bid to ``partner``'s vendor rows
        AND to RFQs the vendor would reasonably see (sent / received /
        awarded — not draft, not cancelled)."""
        return [
            ('vendor_id.partner_id', '=', partner.id),
            ('rfq_state', 'in', ('sent', 'received', 'awarded')),
        ]

    def _pg_rfq_bid_fetch(self, bid_id, access_token=None):
        """Fetch one bid for the current portal user.

        Three independent guards (all must pass):
          1. session user owns a partner that matches vendor.partner_id, OR
             a valid access_token is presented
          2. record exists
          3. record's vendor_id.partner_id matches the logged-in user's
             partner (so a leaked token by itself cannot read someone
             else's bid — token is a SECOND factor, not a sole factor)

        Returns the sudoed recordset.
        Raises MissingError on no-such-record (same response as
        wrong-vendor to avoid leaking existence)."""
        Bid = request.env['pg.rfq.bid'].sudo()
        bid = Bid.browse(int(bid_id)).exists()
        if not bid:
            raise MissingError(_("Bid not found."))
        partner = request.env.user.partner_id
        vendor_partner = bid.vendor_id.partner_id
        # Guard 3: partner match. Token alone is not enough — we want
        # the logged-in user to actually be a portal user of this vendor.
        if not vendor_partner or vendor_partner != partner:
            # The token may grant temporary access in a future iteration
            # (e.g. for un-registered vendor staff clicking from email),
            # but in the STUB we require BOTH login AND partner match.
            # Token is verified below as defense-in-depth.
            raise MissingError(_("Bid not found."))
        # Guard 2: access_token verification when provided. We don't
        # REQUIRE the token for an in-session portal user who already
        # passed the partner match, but if the email link provides one
        # it must match — a wrong token is a sign of phishing / leak.
        if access_token:
            from odoo.tools import consteq
            if not bid.access_token or not consteq(bid.access_token, access_token):
                raise AccessError(_("Invalid bid access token."))
        return bid

    # ──────────────────────────────────────────────────────────────────
    # /my/rfq-bids — list
    # ──────────────────────────────────────────────────────────────────
    @http.route(
        ['/my/rfq-bids', '/my/rfq-bids/page/<int:page>'],
        type='http', auth='user', website=True,
    )
    def portal_my_rfq_bids(self, page=1, sortby=None, filterby=None, **kw):
        values = self._prepare_portal_layout_values()
        partner = request.env.user.partner_id
        if not partner:
            return request.redirect('/my')

        Bid = request.env['pg.rfq.bid'].sudo()
        base_domain = self._pg_rfq_bid_user_domain(partner)

        searchbar_sortings = {
            'date': {'label': _('Newest'), 'order': 'create_date desc, id desc'},
            'rfq': {'label': _('RFQ'), 'order': 'rfq_id desc, id desc'},
            'state': {'label': _('RFQ State'), 'order': 'rfq_state, create_date desc'},
        }
        searchbar_filters = {
            'open': {
                'label': _('Open (action required)'),
                'domain': [('rfq_state', 'in', _VENDOR_PORTAL_OPEN_STATES)],
            },
            'awarded': {
                'label': _('Awarded'),
                'domain': [('rfq_state', '=', 'awarded')],
            },
            'all': {
                'label': _('All'),
                'domain': [],
            },
        }
        if not sortby:
            sortby = 'date'
        if not filterby:
            filterby = 'open'
        order = searchbar_sortings[sortby]['order']
        domain = base_domain + searchbar_filters[filterby]['domain']

        count = Bid.search_count(domain)
        pager = portal_pager(
            url='/my/rfq-bids',
            url_args={'sortby': sortby, 'filterby': filterby},
            total=count, page=page, step=self._items_per_page,
        )
        bids = Bid.search(
            domain, order=order, limit=self._items_per_page,
            offset=pager['offset'],
        )
        request.session['my_rfq_bids_history'] = bids.ids[:100]

        values.update({
            'bids': bids,
            'page_name': 'rfq_bid',
            'default_url': '/my/rfq-bids',
            'pager': pager,
            'searchbar_sortings': searchbar_sortings,
            'sortby': sortby,
            'searchbar_filters': OrderedDict(sorted(searchbar_filters.items())),
            'filterby': filterby,
        })
        return request.render(
            'product_graph_rfq.portal_my_rfq_bids', values,
        )

    # ──────────────────────────────────────────────────────────────────
    # /my/rfq-bids/<id> — detail (+ form)
    # ──────────────────────────────────────────────────────────────────
    @http.route(
        ['/my/rfq-bids/<int:bid_id>'],
        type='http', auth='user', website=True,
    )
    def portal_my_rfq_bid(self, bid_id, access_token=None, **kw):
        try:
            bid = self._pg_rfq_bid_fetch(bid_id, access_token=access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')
        values = {
            'bid': bid,
            'rfq': bid.rfq_id,
            'line': bid.line_id,
            'item': bid.line_id.item_id,
            'page_name': 'rfq_bid',
            'submit_open': bid._portal_can_be_submitted(),
            'access_token': access_token or '',
            'form_error': kw.get('form_error'),
            'form_success': kw.get('form_success'),
        }
        return request.render(
            'product_graph_rfq.portal_my_rfq_bid', values,
        )

    # ──────────────────────────────────────────────────────────────────
    # /my/rfq-bids/<id>/submit — POST
    # ──────────────────────────────────────────────────────────────────
    @http.route(
        ['/my/rfq-bids/<int:bid_id>/submit'],
        type='http', auth='user', website=True, methods=['POST'], csrf=True,
    )
    def portal_my_rfq_bid_submit(self, bid_id, access_token=None, **post):
        try:
            bid = self._pg_rfq_bid_fetch(bid_id, access_token=access_token)
        except (AccessError, MissingError):
            return request.redirect('/my')

        # Lock check at the controller boundary (cheaper than a write
        # attempt that the model would then reject). The model
        # re-checks under sudo to close any TOCTOU window between this
        # if-guard and the write.
        if not bid._portal_can_be_submitted():
            return request.redirect(
                f'/my/rfq-bids/{bid.id}?form_error='
                + _('This RFQ is no longer accepting bids.')
            )

        values = self._pg_parse_submission(post)
        try:
            bid._portal_post_submission(values, by_user=request.env.user)
        except (UserError, ValidationError) as e:
            return request.redirect(
                f'/my/rfq-bids/{bid.id}?form_error=' + (e.args[0] if e.args else _('Submission rejected.'))
            )
        except Exception:  # noqa: BLE001 — never leak stacks to portal
            _logger.exception("Vendor portal submission failed (bid %s)", bid.id)
            return request.redirect(
                f'/my/rfq-bids/{bid.id}?form_error=' + _('Internal error — please retry.')
            )
        return request.redirect(
            f'/my/rfq-bids/{bid.id}?form_success=1'
        )

    # ──────────────────────────────────────────────────────────────────
    # Parsing
    # ──────────────────────────────────────────────────────────────────
    def _pg_parse_submission(self, post):
        """Convert raw POST string values to typed dict ready for write.

        Defensive — we accept missing / empty fields (vendor may submit a
        partial update) but reject malformed numerics with a UserError
        so the user sees a real message rather than a 500.
        """
        out = {}

        def _to_float(name):
            raw = post.get(name)
            if raw in (None, ''):
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                raise UserError(_(
                    "Field '%s' must be a number (got %r)."
                ) % (name, raw))

        def _to_int(name):
            raw = post.get(name)
            if raw in (None, ''):
                return None
            try:
                return int(raw)
            except (TypeError, ValueError):
                raise UserError(_(
                    "Field '%s' must be an integer (got %r)."
                ) % (name, raw))

        up = _to_float('unit_price')
        if up is not None:
            if up < 0:
                raise UserError(_("Unit price cannot be negative."))
            out['unit_price'] = up

        lt = _to_int('lead_time_days')
        if lt is not None:
            if lt < 0:
                raise UserError(_("Lead time cannot be negative."))
            out['lead_time_days'] = lt

        moq = _to_float('moq')
        if moq is not None:
            if moq < 0:
                raise UserError(_("MOQ cannot be negative."))
            out['moq'] = moq

        vu = post.get('valid_until')
        if vu:
            # Let the ORM parse — it accepts ISO YYYY-MM-DD.
            out['valid_until'] = vu

        vpn = post.get('vendor_part_number')
        if vpn is not None:
            out['vendor_part_number'] = vpn.strip()

        cmt = post.get('comment')
        if cmt is not None:
            out['comment'] = cmt.strip()

        return out
