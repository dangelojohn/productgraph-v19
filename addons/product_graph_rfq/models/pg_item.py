# -*- coding: utf-8 -*-
"""W033 (R6.8, 2026-06-27) — auto-RFQ cooldown stamp on pg.item.

The auto-draft cron walks every purchased item whose AVL is empty and
drafts an RFQ. Without a per-item cooldown, a wave of newly-created or
newly-released items (50+ at module install or after a bulk import)
fires 50+ RFQs in the same cron pass — a thunderclap that floods
sourcing engineers' inboxes and the chatter stream.

`last_auto_rfq_on` records when the cron most recently drafted an RFQ
for this item (or chose not to and posted an activity). The cron
filters items by `(last_auto_rfq_on IS NULL OR last_auto_rfq_on <
NOW - cooldown_hours)` so the same item is not re-considered until
the cooldown window elapses.

The cooldown is set by `southbrook.auto_rfq_cooldown_hours` (ICP,
default 24). The per-run batch cap is set by
`southbrook.auto_rfq_max_per_run` (ICP, default 10).
"""
from odoo import fields, models


class PgItem(models.Model):
    _inherit = 'pg.item'

    last_auto_rfq_on = fields.Datetime(
        string='Last Auto-RFQ Considered',
        readonly=True,
        copy=False,
        index=True,
        help='Timestamp of the most recent auto-RFQ cron pass that '
             'considered this item (either drafted an RFQ or posted the '
             'unsourced-item activity). Drives the per-item cooldown so '
             'a single bulk-import wave does not generate a thunderclap '
             'of RFQs. Cleared by hand if you want to force a re-attempt.',
    )
