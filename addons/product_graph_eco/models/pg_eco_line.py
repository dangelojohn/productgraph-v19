# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PgEcoLine(models.Model):
    _name = 'pg.eco.line'
    _description = 'ECO Affected Item'
    _order = 'eco_id, sequence, id'

    eco_id = fields.Many2one(
        'pg.eco',
        string='ECO',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(default=10)

    item_id = fields.Many2one(
        'pg.item', string='Item', required=True, ondelete='restrict',
    )

    # The revision the item is at right now (or was at when the ECO was filed).
    from_revision_id = fields.Many2one(
        'pg.revision',
        string='From Revision',
        domain="[('item_id', '=', item_id)]",
        ondelete='restrict',
    )
    # The revision this ECO drives the item to. Must reach `released` before
    # the parent ECO can transition to `released`.
    to_revision_id = fields.Many2one(
        'pg.revision',
        string='To Revision',
        domain="[('item_id', '=', item_id)]",
        ondelete='restrict',
    )

    change_kind = fields.Selection(
        [
            ('revision_bump', 'Revision Bump'),
            ('property_change', 'Property Change'),
            ('bom_change', 'BOM Change'),
            ('obsolete', 'Obsolescence'),
            ('introduce', 'New Item Introduction'),
        ],
        string='Change Kind',
        required=True,
        default='revision_bump',
    )

    status = fields.Selection(
        [
            ('pending', 'Pending'),
            ('in_progress', 'In Progress'),
            ('done', 'Done'),
            ('skipped', 'Skipped'),
        ],
        string='Line Status',
        default='pending',
        required=True,
    )

    notes = fields.Text(string='Notes')

    eco_state = fields.Selection(
        related='eco_id.state', store=True, index=True, string='ECO State',
    )

    # ──────────────────────────────────────────────────────────────────
    # Constraints
    # ──────────────────────────────────────────────────────────────────
    @api.constrains('from_revision_id', 'to_revision_id', 'item_id')
    def _check_revision_pointers_item_match(self):
        for rec in self:
            if rec.from_revision_id and rec.from_revision_id.item_id != rec.item_id:
                raise ValidationError(
                    _("ECO line: from_revision must belong to the same item.")
                )
            if rec.to_revision_id and rec.to_revision_id.item_id != rec.item_id:
                raise ValidationError(
                    _("ECO line: to_revision must belong to the same item.")
                )

    # Released ECOs lock their lines too.
    def write(self, vals):
        if not self.env.context.get('_pg_eco_release_bypass'):
            for rec in self:
                if rec.eco_state == 'released' and set(vals.keys()) - {'status', 'notes'}:
                    raise UserError(
                        _("ECO %s is released — line cannot be edited "
                          "(only status / notes remain writable).")
                        % rec.eco_id.display_name
                    )
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.eco_state not in ('draft', 'analysis'):
                raise UserError(
                    _("ECO lines can only be removed while the ECO is in "
                      "draft or analysis. %s is %s.")
                    % (rec.eco_id.display_name, rec.eco_state)
                )
        return super().unlink()

    # ──────────────────────────────────────────────────────────────────
    # PG-131 — Auto-spawn target revision from an affected-item line
    # ──────────────────────────────────────────────────────────────────
    def action_spawn_revision(self):
        """Create the target revision for this ECO line by cloning the
        predecessor (from_revision_id). The new revision lands in DRAFT —
        humans review/refine and trigger release; no auto-release.

        Only meaningful for change_kinds that carry a to_revision (i.e.
        revision_bump / property_change / bom_change). For introduce/obsolete
        the concept does not apply.
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        if str(ICP.get_param(
            'productgraph.auto_spawn_revision_from_eco_line', 'True',
        )) != 'True':
            raise UserError(_(
                "Auto-spawn revision is disabled in system parameters."
            ))
        if self.change_kind not in (
            'revision_bump', 'property_change', 'bom_change',
        ):
            raise UserError(_(
                "Spawn target revision is only meaningful for change kinds "
                "that target a new revision (Revision Bump, Property Change, "
                "BOM Change)."
            ))
        if self.to_revision_id:
            raise UserError(_(
                "Target revision %s already exists for this ECO line."
            ) % self.to_revision_id.display_name)
        if not self.from_revision_id:
            raise UserError(_(
                "Cannot spawn a target revision without a source (From "
                "Revision) — pick the predecessor first."
            ))
        if self.eco_state in ('released', 'rejected'):
            raise UserError(_(
                "Cannot spawn a revision from an ECO in state '%s'."
            ) % self.eco_state)
        new_rev = self._do_spawn_revision()
        # Two-way bridge — stamp the line so the ECO board can see the link.
        self.to_revision_id = new_rev.id
        self.eco_id.message_post(body=_(
            "Spawned new revision <b>%s</b> for item <b>%s</b> from ECO line "
            "(predecessor: %s)."
        ) % (
            new_rev.display_name, self.item_id.display_name,
            self.from_revision_id.display_name,
        ))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Spawned Revision'),
            'res_model': 'pg.revision',
            'res_id': new_rev.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ──────────────────────────────────────────────────────────────────
    # PG-132 — ECO impact pre-population from closure
    # ──────────────────────────────────────────────────────────────────
    def action_explode_impact(self):
        """Find all assemblies that use this line's item, suggest adding
        them as ECO lines via a transient wizard.

        Reverse-walks `pg.ebom.closure` (descendant_id == item_id) to find
        every released-EBOM ancestor that consumes this item, then opens the
        wizard with those ancestors (minus items already on the ECO) as
        candidates. ICP-gated.
        """
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        if str(ICP.get_param(
            'productgraph.eco_impact_explode_enabled', 'True',
        )) != 'True':
            raise UserError(_(
                "Impact explode is disabled in system parameters."
            ))
        if not self.item_id:
            raise UserError(_("Set an Item first."))
        closure_rows = self.env['pg.ebom.closure'].search([
            ('descendant_id', '=', self.item_id.id),
        ])
        ancestor_items = closure_rows.mapped('ebom_id.root_item_id')
        existing_item_ids = set(self.eco_id.line_ids.mapped('item_id').ids)
        candidates = ancestor_items.filtered(
            lambda i: i.id not in existing_item_ids
        )
        if not candidates:
            self.eco_id.message_post(
                body=_("No additional assemblies use %s.")
                % self.item_id.display_name,
            )
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': _("Impact Analysis: %s") % self.item_id.display_name,
            'res_model': 'pg.eco.impact.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_eco_id': self.eco_id.id,
                'default_source_item_id': self.item_id.id,
                'default_candidate_item_ids': [(6, 0, candidates.ids)],
                'default_source_line_id': self.id,
            },
        }

    def _do_spawn_revision(self):
        """Clone from_revision_id into a fresh draft revision (same item,
        next-name) and clone any released EBOM(s) of the predecessor as a
        draft EBOM bound to the new revision. Returns the new revision.

        Released revisions are immutable; `copy()` respects copy=False on
        release-stamped fields so the new draft starts clean.
        """
        self.ensure_one()
        src = self.from_revision_id
        next_name = self.env['pg.revision']._next_revision_name_after(src.name)
        # Spawned revision lands as DRAFT — never auto-released.
        new_rev = src.copy({
            'name': next_name,
            'state': 'draft',
            'predecessor_revision_id': src.id,
            'change_summary': (
                (self.notes or '').strip()
                or (self.eco_id.title or '')[:200]
                or _("Spawned from ECO %s.") % self.eco_id.display_name
            ),
        })
        # Clone released EBOM(s) of the source revision into draft EBOMs
        # bound to the new revision. We deliberately skip non-released
        # EBOMs (drafts shouldn't proliferate; superseded shouldn't be
        # the starting point).
        EbomModel = self.env.get('pg.ebom')
        if EbomModel is not None:
            for src_ebom in EbomModel.search([
                ('revision_id', '=', src.id),
                ('state', '=', 'released'),
            ]):
                src_ebom.copy({
                    'revision_id': new_rev.id,
                    'state': 'draft',
                })
        # Audit log: new revision creation, triggered by THIS eco line.
        self.env['pg.audit.log'].sudo()._log(
            new_rev, 'state_change',
            from_state=False, to_state='draft',
            notes=_(
                "Spawned from ECO %s line for item %s (predecessor %s)."
            ) % (
                self.eco_id.display_name,
                self.item_id.display_name,
                src.display_name,
            ),
            triggered_by=self,
        )
        return new_rev
