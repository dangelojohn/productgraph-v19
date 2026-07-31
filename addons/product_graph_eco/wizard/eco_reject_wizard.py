# -*- coding: utf-8 -*-
"""PG-XXX — ECO Rejection Reason wizard.

Surfaced from the "Reject" button on the pg.eco form. The prior button
used only an OK/Cancel `confirm=` dialog with no reason input, so
clicking it always raised `ValidationError` because `action_reject()`
requires a non-empty reason. This wizard collects the reason and
delegates to `action_reject()` so the R4 rule (audit log + chatter +
SoD gate) still fires from the same code path.
"""
from odoo import _, fields, models


class PgEcoRejectWizard(models.TransientModel):
    _name = "pg.eco.reject.wizard"
    _description = "ECO Rejection Reason"

    eco_id = fields.Many2one(
        "pg.eco",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    reason = fields.Text(
        string="Rejection Reason",
        required=True,
    )

    def action_confirm_reject(self):
        """Route through pg.eco.action_reject so audit + chatter + SoD fire."""
        self.ensure_one()
        self.eco_id.action_reject(reason=(self.reason or "").strip())
        return {"type": "ir.actions.act_window_close"}
