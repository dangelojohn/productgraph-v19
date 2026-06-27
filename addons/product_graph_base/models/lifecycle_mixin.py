# -*- coding: utf-8 -*-
"""
pg.lifecycle.mixin — Reusable state-machine + audit + immutability mixin.

Consolidates ~500 lines of duplicated lifecycle infrastructure that was
copy-pasted across pg.item, pg.revision, pg.ebom, pg.eco, pg.release.

Subclasses declare config as class attributes and MUST redeclare the
``state`` field with the proper Selection (a mixin can't infer the per-model
state values).

Configuration surface
─────────────────────

    LIFECYCLE_STATES               list[(key, label)]
    LIFECYCLE_TRANSITIONS          dict[from_state -> list[to_state]]
    LIFECYCLE_GROUPS               dict[transition_name -> group_xmlid]
                                   (optional; used by _do_transition)
    LIFECYCLE_MUTABLE_LOCKED       frozenset[field_name]
                                   ALLOWLIST: in a locked state, only these
                                   field names may be written without bypass.
                                   Defaults to chatter + activity + state +
                                   active.
    LIFECYCLE_RELEASED_LOCKED_FIELDS
                                   frozenset[field_name]
                                   BLOCKLIST: when set, the mixin uses pg.eco
                                   semantics — only these field names are
                                   blocked, and ONLY when state == 'released'.
                                   When set, LIFECYCLE_MUTABLE_LOCKED and
                                   LIFECYCLE_TERMINAL_STATES are ignored for
                                   write enforcement.
    LIFECYCLE_BYPASS_KEY           str — context key that unlocks immutability
                                   for the release flow (e.g.
                                   '_pg_release_bypass', '_pg_eco_release_bypass').
                                   If None, the write override is a no-op.
    LIFECYCLE_BYPASS_ONLY_FIELDS   frozenset[field_name]
                                   Fields blocked from being written on ANY
                                   state unless the bypass context is set
                                   (e.g. release-stamp fields on pg.revision
                                   that the release flow alone may stamp).
    LIFECYCLE_TERMINAL_STATES      frozenset[state]
                                   States in which the record is immutable.
                                   If None, inferred from empty TRANSITIONS.

Hooks subclasses may override
─────────────────────────────

    _pre_transition(from_state, to_state, **ctx)        — domain checks
    _lifecycle_post_transition(from_state, to_state, **ctx)
                                                        — cascade actions
    _get_locked_states()                                — custom locked-set
"""
import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class LifecycleMixin(models.AbstractModel):
    _name = 'pg.lifecycle.mixin'
    _description = 'ProductGraph Lifecycle State Machine'

    # ─── Config — subclass overrides ──────────────────────────────────────────
    LIFECYCLE_STATES = []
    LIFECYCLE_TRANSITIONS = {}
    LIFECYCLE_GROUPS = {}
    LIFECYCLE_MUTABLE_LOCKED = frozenset({
        'state', 'active',
        'message_ids', 'message_follower_ids', 'activity_ids',
        'message_main_attachment_id',
    })
    LIFECYCLE_RELEASED_LOCKED_FIELDS = None  # blocklist mode (pg.eco)
    LIFECYCLE_BYPASS_KEY = None
    LIFECYCLE_BYPASS_ONLY_FIELDS = frozenset()
    LIFECYCLE_TERMINAL_STATES = None
    # When False, mixin's write() is a pass-through. Subclasses with
    # asymmetric per-state lock rules (pg.item locks 'released' AND
    # 'obsolete' with DIFFERENT allowlists) keep their own write() override
    # and disable the mixin's enforcement here.
    LIFECYCLE_ENFORCE_WRITE = True

    # ═══════════════════════════════════════════════════════════════════════════
    # Transition helpers
    # ═══════════════════════════════════════════════════════════════════════════

    def _check_transition_allowed(self, target_state):
        """Raise UserError if current state cannot transition to target_state."""
        self.ensure_one()
        allowed = self.LIFECYCLE_TRANSITIONS.get(self.state, [])
        if target_state not in allowed:
            current_label = dict(self._fields['state'].selection).get(
                self.state, self.state,
            )
            target_label = dict(self._fields['state'].selection).get(
                target_state, target_state,
            )
            raise UserError(_(
                "Cannot transition %(model)s from '%(from_label)s' to "
                "'%(to_label)s'.\nAllowed from this state: %(allowed)s",
                model=self._description or self._name,
                from_label=current_label,
                to_label=target_label,
                allowed=', '.join(allowed) or _('none (terminal state)'),
            ))

    def _check_group_for_transition(self, transition_name):
        """Raise UserError if current user lacks the required group."""
        group_xmlid = self.LIFECYCLE_GROUPS.get(transition_name)
        if group_xmlid and not self.env.user.has_group(group_xmlid):
            raise UserError(_(
                "Action '%(action)s' requires group: %(group)s",
                action=transition_name,
                group=group_xmlid,
            ))

    def _audit_state_change(self, from_state, to_state,
                            action='state_change', notes=None,
                            triggered_by=None, payload=None):
        """Standardized wrapper around pg.audit.log._log() for state changes."""
        self.env['pg.audit.log']._log(
            self,
            action=action,
            from_state=from_state,
            to_state=to_state,
            notes=notes,
            triggered_by=triggered_by,
            payload=payload,
        )

    # Backwards-compat alias — existing action_* methods on subclasses still
    # call self._log_state_change(old, new, notes=..., triggered_by=...).
    # Routing through the mixin avoids touching every action body in this PR
    # (deferred to a Phase 3 cleanup that switches to _do_transition).
    def _log_state_change(self, from_state, to_state, notes=None,
                          triggered_by=None):
        if notes is None:
            notes = f'State: {from_state} → {to_state}'
        self._audit_state_change(
            from_state, to_state, notes=notes, triggered_by=triggered_by,
        )

    # ─── Optional hooks ───────────────────────────────────────────────────────

    def _pre_transition(self, from_state, to_state, **ctx):
        """Subclass override: domain checks before state write."""
        return None

    def _lifecycle_post_transition(self, from_state, to_state, **ctx):
        """Subclass override: cascade actions after state write + audit.

        Named with the ``_lifecycle_`` prefix to avoid colliding with
        pg.eco._post_transition which has different semantics.
        """
        return None

    def _get_locked_states(self):
        """Return frozenset of states where the record is immutable.

        Default: states explicitly declared in LIFECYCLE_TERMINAL_STATES,
        OR (when None) states with empty transition lists.
        """
        if self.LIFECYCLE_TERMINAL_STATES is not None:
            return self.LIFECYCLE_TERMINAL_STATES
        return frozenset(
            state for state, targets in self.LIFECYCLE_TRANSITIONS.items()
            if not targets
        )

    def _do_transition(self, target_state, transition_name=None,
                       extra_vals=None, notes=None, **ctx):
        """One-call orchestrator: group + transition guard + write + audit + hook.

        Subclass action_* methods can call this instead of replicating the
        guard/write/audit pattern. Existing action methods left untouched in
        this PR (deferred cleanup).
        """
        self.ensure_one()
        if transition_name:
            self._check_group_for_transition(transition_name)
        self._check_transition_allowed(target_state)
        self._pre_transition(self.state, target_state, **ctx)
        from_state = self.state
        vals = {'state': target_state}
        if extra_vals:
            vals.update(extra_vals)
        bypass_key = self.LIFECYCLE_BYPASS_KEY
        if bypass_key:
            self.with_context(**{bypass_key: True}).write(vals)
        else:
            self.write(vals)
        self._audit_state_change(from_state, target_state, notes=notes)
        self._lifecycle_post_transition(from_state, target_state, **ctx)
        return True

    # ═══════════════════════════════════════════════════════════════════════════
    # ORM override — immutability guard
    # ═══════════════════════════════════════════════════════════════════════════

    def write(self, vals):
        # OPT-OUT for subclasses with asymmetric per-state lock rules. They
        # keep their own write() override (pg.item locks released + obsolete
        # with two different allowlists; pg.eco's blocklist-on-released
        # semantics are expressed via LIFECYCLE_RELEASED_LOCKED_FIELDS).
        if not self.LIFECYCLE_ENFORCE_WRITE:
            return super().write(vals)

        bypass_key = self.LIFECYCLE_BYPASS_KEY
        bypass = bool(bypass_key) and self.env.context.get(bypass_key)

        if not bypass and self.LIFECYCLE_RELEASED_LOCKED_FIELDS is not None:
            # BLOCKLIST mode (pg.eco): block named fields only on released.
            blocked = self.LIFECYCLE_RELEASED_LOCKED_FIELDS & set(vals.keys())
            if blocked:
                for rec in self:
                    if rec.state == 'released':
                        raise UserError(_(
                            "%(model)s %(name)s is Released and is immutable. "
                            "Fields blocked: %(fields)s",
                            model=rec._description or rec._name,
                            name=rec.display_name,
                            fields=', '.join(sorted(blocked)),
                        ))
        elif bypass_key:
            # ALLOWLIST mode (pg.item / pg.revision / pg.ebom / pg.release).
            if not bypass:
                locked_states = self._get_locked_states()
                allowed = set(self.LIFECYCLE_MUTABLE_LOCKED)
                for rec in self:
                    if rec.state in locked_states:
                        blocked = set(vals.keys()) - allowed
                        if blocked:
                            raise UserError(_(
                                "%(model)s %(name)s is in locked state "
                                "'%(state)s' and immutable.\n"
                                "Blocked fields: %(fields)s.\n"
                                "(Allowed in locked state: %(allowed)s.)",
                                model=rec._description or rec._name,
                                name=rec.display_name,
                                state=rec.state,
                                fields=', '.join(sorted(blocked)),
                                allowed=', '.join(sorted(allowed)),
                            ))
                # Bypass-only fields: blocked on ALL states unless bypass set.
                if self.LIFECYCLE_BYPASS_ONLY_FIELDS:
                    bypass_attempt = (
                        set(vals.keys()) & self.LIFECYCLE_BYPASS_ONLY_FIELDS
                    )
                    if bypass_attempt:
                        for rec in self:
                            raise UserError(_(
                                "Fields %(fields)s on %(model)s %(name)s can "
                                "only be written by the release flow.",
                                fields=', '.join(sorted(bypass_attempt)),
                                model=rec._description or rec._name,
                                name=rec.display_name,
                            ))
        return super().write(vals)
