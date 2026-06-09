# -*- coding: utf-8 -*-
"""Extend pg.asset with the installed-revision link.

Lives in product_graph_revision because pg.revision is defined here. Keeps
pg.asset itself in product_graph_base so it can be referenced as a Phase 3
stub without forcing a dependency on _revision.
"""
from odoo import fields, models


class PgAsset(models.Model):
    _inherit = 'pg.asset'

    revision_id = fields.Many2one(
        'pg.revision',
        string='Installed Revision',
        domain="[('item_id', '=', item_id), ('state', '=', 'released')]",
        tracking=True,
    )
