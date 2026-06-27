# -*- coding: utf-8 -*-
"""PG-125 — Settings panel that surfaces every ProductGraph ICP toggle.

These keys were previously only writable via `ir.config_parameter.set_param`
in tests / shell. Exposing them on the standard Settings → ProductGraph tab
removes the "where do I turn this on" friction without changing semantics:
each `config_parameter=` field round-trips through the same ICP row the
rest of the codebase reads.
"""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Segregation of Duties.
    pg_enforce_sod = fields.Boolean(
        string='Enforce Segregation of Duties',
        config_parameter='productgraph.enforce_sod',
        default=True,
        help='When enabled, the same user cannot both submit/request AND approve/release '
             'a revision or ECO. Admins may bypass via Express Release.',
    )

    # Lifecycle automation.
    pg_auto_advance_item_on_revision_release = fields.Boolean(
        string='Auto-advance item state on revision release',
        config_parameter='productgraph.auto_advance_item_on_revision_release',
        default=True,
        help='When a revision releases, automatically promote the parent item from '
             'Engineering to Released.',
    )

    # ECO automation.
    pg_auto_close_eco_on_all_revisions_released = fields.Boolean(
        string='Auto-close ECO when all affected revisions released',
        config_parameter='productgraph.auto_close_eco_on_all_revisions_released',
        default=True,
    )
    pg_auto_spawn_revision_from_eco_line = fields.Boolean(
        string='Allow auto-spawn revision from ECO line',
        config_parameter='productgraph.auto_spawn_revision_from_eco_line',
        default=True,
    )
    pg_notify_on_auto_advance = fields.Boolean(
        string='Notify users on auto-advance / auto-close',
        config_parameter='productgraph.notify_on_auto_advance',
        default=True,
        help='Post foreground toast and chatter notifications when an item or ECO '
             'advances automatically.',
    )
    pg_eco_impact_explode_enabled = fields.Boolean(
        string='Enable ECO Impact Explode',
        config_parameter='productgraph.eco_impact_explode_enabled',
        default=True,
    )
    pg_eco_route_on_submit = fields.Boolean(
        string='Auto-route submitted ECOs to assigned approver',
        config_parameter='productgraph.eco_route_on_submit',
        default=True,
    )
    pg_eco_sla_enabled = fields.Boolean(
        string='ECO SLA reminders + escalation cron',
        config_parameter='productgraph.eco_sla_enabled',
        default=True,
    )
