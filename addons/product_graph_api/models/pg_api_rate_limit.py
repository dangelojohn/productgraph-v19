# -*- coding: utf-8 -*-
"""
pg.api.rate.limit — Postgres-backed fixed-window request counter.

Used by the shared ``_safe`` wrapper in ``controllers/pg_api.py`` (and, by
extension, ``controllers/phase2_api.py``, which imports the same wrapper) to
throttle the ``/api/v1/productgraph/*`` REST surface per B8.4: a single
leaked or compromised bearer API key must not be able to hammer expensive
routes (``ebom_explosion``, ``item_where_used``, ``create_release``) at
unbounded QPS against the fixed Odoo worker pool.

Deliberately NOT built on the usual ``pg.*`` audit-log conventions: this
table is high-write, short-lived, and has no user-facing UI or chatter. A
single row exists per (identity, route, 60-second window) and is written
via an atomic ``INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING`` so
concurrent requests landing on different Odoo prefork workers can't race
past the cap — an in-memory counter would only be visible within the one
worker that handled the request, which is useless under Odoo's multi-worker
deployment model.

A cron (``data/ir_cron_prune_rate_limit.xml``) deletes windows older than
``PRUNE_AFTER_SECONDS`` so the table never grows unbounded under sustained
traffic.
"""
import time

from odoo import api, fields, models


class PgApiRateLimit(models.Model):
    _name = 'pg.api.rate.limit'
    _description = 'ProductGraph API Rate Limit Counter'
    _log_access = False
    _rec_name = 'identity'

    identity = fields.Char(
        required=True, index=True,
        help="Caller bucket key. 'uid:<res.users id>' for authenticated "
             "bearer callers, 'ip:<remote addr>' for unauthenticated "
             "callers on auth='public' routes (e.g. /health).",
    )
    route = fields.Char(
        required=True, index=True,
        help="Fully-qualified controller handler identifying the logical "
             "endpoint, e.g. "
             "'odoo.addons.product_graph_api.controllers.pg_api.list_items'.",
    )
    window_start = fields.Integer(
        required=True, index=True,
        help="Unix epoch seconds, floored to the start of the current "
             "fixed window (see WINDOW_SECONDS).",
    )
    request_count = fields.Integer(default=0)

    _identity_route_window_uniq = models.Constraint(
        'unique(identity, route, window_start)',
        'One counter row per identity/route/window.',
    )

    WINDOW_SECONDS = 60
    # Keep a little history past the window for post-incident debugging;
    # the prune cron sweeps anything older than this.
    PRUNE_AFTER_SECONDS = 600

    @api.model
    def _hit(self, identity, route):
        """Atomically increment (creating if absent) the counter for the
        current fixed window and return the post-increment count.

        Uses a single upsert rather than SELECT-then-write so the check is
        safe under concurrency: two requests racing to create the same
        (identity, route, window_start) row both land correctly-counted
        instead of one clobbering the other's insert.
        """
        now = int(time.time())
        window_start = now - (now % self.WINDOW_SECONDS)
        self.env.cr.execute(
            """
            INSERT INTO pg_api_rate_limit
                (identity, route, window_start, request_count)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (identity, route, window_start)
            DO UPDATE SET request_count = pg_api_rate_limit.request_count + 1
            RETURNING request_count
            """,
            (identity, route, window_start),
        )
        return self.env.cr.fetchone()[0]

    @api.model
    def _cron_prune_expired(self):
        """Delete windows older than PRUNE_AFTER_SECONDS.

        A cheap periodic batch delete (indexed on window_start) rather than
        a per-request DELETE, per the deploy-risk mitigation in B8.4.
        """
        cutoff = int(time.time()) - self.PRUNE_AFTER_SECONDS
        self.env.cr.execute(
            "DELETE FROM pg_api_rate_limit WHERE window_start < %s",
            (cutoff,),
        )
