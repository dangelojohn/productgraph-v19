# 020 — State Machine Bible
## ProductGraph for Odoo 19 CE
### Version 1.0 | Priority: CRITICAL | Completeness Target: 100%

> **Purpose:** This document is the single authoritative reference for all
> state machines in ProductGraph. Every state, every transition, every
> allowed and forbidden action is defined here. Odoo ORM code must implement
> these rules exactly. No deviation without a documented architecture decision.

---

## Table of Contents

1. pg.item State Machine
2. pg.revision State Machine
3. pg.ebom State Machine
4. pg.release State Machine
5. pg.eco State Machine (Phase 2)
6. Transition Authority Matrix
7. Required Actions for Every Transition
8. Forbidden Actions by State
9. ORM Implementation Patterns
10. Inter-Model State Dependencies

---

## 1. pg.item State Machine

### 1.1 State Definitions

```
CONCEPT → PROTOTYPE → ENGINEERING → RELEASED → SERVICE → OBSOLETE
```

#### STATE: concept
| Attribute | Value |
|---|---|
| Label | Concept |
| DB Value | `'concept'` |
| Color (Odoo) | Purple / `color=5` |
| Description | Early-stage idea. Created for planning, estimation, or roadmap purposes. Not yet assigned to any engineering effort. No manufacturing impact. |
| Who Creates | PG User, PG Engineer, PG Admin |
| Typical Duration | Days to weeks |

**Allowed Actions:**
- Create item record
- Edit all fields (name, description, type, notes)
- Attach documents
- Link to project or initiative
- Delete record (soft via unlink, only in this state)
- Add chatter notes

**Forbidden Actions:**
- Include in any pg.ebom as a BOM line
- Create pg.revision record
- Link to product.template
- Trigger MRP release
- Add to Approved Vendor List

**Odoo Implementation:**
```python
@api.constrains('state')
def _check_concept_restrictions(self):
    for rec in self:
        if rec.state == 'concept':
            if rec.ebom_line_ids:
                raise ValidationError(
                    "Concept items cannot be included in Engineering BOMs."
                )
```

---

#### STATE: prototype
| Attribute | Value |
|---|---|
| Label | Prototype |
| DB Value | `'prototype'` |
| Color (Odoo) | Orange / `color=3` |
| Description | Experimental. Used in prototype/development builds only. Changes are expected and frequent. Not a stable engineering baseline. |
| Who Transitions To | PG Engineer (from concept) |
| Typical Duration | Weeks to months |

**Allowed Actions:**
- All concept actions remain
- Create pg.revision (Draft only)
- Include in prototype-flagged pg.ebom (`ebom_type = 'prototype'`)
- Supplier sampling and quote requests
- CAD file attachment
- Edit item type and classification

**Forbidden Actions:**
- Include in production-type pg.ebom (`ebom_type = 'production'`)
- MRP release of any kind
- Link to open Manufacturing Orders
- Appear in Approved Manufacturer List (AVL)

---

#### STATE: engineering
| Attribute | Value |
|---|---|
| Label | Engineering |
| DB Value | `'engineering'` |
| Color (Odoo) | Blue / `color=1` |
| Description | Active design. Item has a formal engineering identity. Revision(s) exist. Item may appear in Engineering BOMs under development. |
| Who Transitions To | PG Engineer (from prototype or concept) |
| Typical Duration | Weeks to months |

**Allowed Actions:**
- Create pg.revision (Draft, Review cycles)
- Include in production pg.ebom (while ebom is in Draft/Review state)
- Full field editing (all non-locked fields)
- Document attachment (drawings, specs, models)
- Supplier qualification process
- Where-Used analysis

**Forbidden Actions:**
- MRP release (until a revision reaches Released state — the item transitions to Released automatically)
- Include in a Released pg.ebom (item must be Released first)
- Appear in active mrp.bom

**Auto-Transition Rule:**
When the first pg.revision for this item transitions to `released`, the pg.item
state automatically transitions to `released`. This is enforced by the revision
model's `action_release()` method.

---

#### STATE: released
| Attribute | Value |
|---|---|
| Label | Released |
| DB Value | `'released'` |
| Color (Odoo) | Green / `color=10` |
| Description | Approved for production. Stable engineering baseline. All downstream manufacturing, purchasing, and inventory operations reference Released items. IMMUTABLE except via explicit new revision. |
| Who Transitions To | Automatic (when revision releases) or PG Approver (manual override with justification) |
| Typical Duration | Months to years |

**Allowed Actions:**
- Include in all pg.ebom types
- MRP BOM generation via Release action
- Purchasing / procurement
- Inventory receipt and consumption
- Maintenance and service linkage
- Where-Used analysis
- Read all fields
- Add chatter notes
- Create new pg.revision (via "Create Revision" button — requires PG Engineer role)
- View full audit history

**Forbidden Actions:**
- Direct modification of any non-chatter field (enforced at ORM write() level)
- Deletion (enforced at ORM unlink() level)
- State rollback to Engineering or lower (except via Admin with documented reason)

**ORM Write Block:**
```python
# In PgItem.write():
RELEASED_ALLOWED_FIELDS = {
    'message_ids', 'activity_ids', 'message_follower_ids'
}

def write(self, vals):
    for rec in self:
        if rec.state == 'released':
            blocked = set(vals.keys()) - RELEASED_ALLOWED_FIELDS
            if blocked:
                raise UserError(
                    f"Cannot modify Released item '{rec.name}'. "
                    f"Blocked fields: {', '.join(blocked)}. "
                    "Create a new revision to make changes."
                )
    return super().write(vals)
```

---

#### STATE: service
| Attribute | Value |
|---|---|
| Label | Service |
| DB Value | `'service'` |
| Color (Odoo) | Teal / `color=6` |
| Description | Production of new units discontinued. Item is maintained as a service/spare part for existing installed equipment. |
| Who Transitions To | PG Approver (from released) |
| Typical Duration | Years (product end-of-life support period) |

**Allowed Actions:**
- Spare part ordering and purchasing
- Maintenance BOM inclusion
- Service documentation updates
- Read all fields
- Where-Used analysis (historical)

**Forbidden Actions:**
- Include in new production pg.ebom
- New production Manufacturing Orders
- Edit engineering fields (treated as immutable)
- Create new pg.revision without PG Approver justification

---

#### STATE: obsolete
| Attribute | Value |
|---|---|
| Label | Obsolete |
| DB Value | `'obsolete'` |
| Color (Odoo) | Gray / `color=0` |
| Description | Retired. No new use in any context. Record preserved permanently for traceability, compliance, and genealogy. |
| Who Transitions To | PG Admin (from service or released) |
| Typical Duration | Permanent |

**Allowed Actions:**
- Read all fields (read-only forever)
- View full audit history
- Include in historical Where-Used reports

**Forbidden Actions:**
- Any field modification
- Any new usage in pg.ebom, mrp.bom, purchase.order, stock.move
- Deletion — **records are preserved forever**
- State reversal of any kind

**Validation:**
```python
def action_obsolete(self):
    self.ensure_one()
    # Check no active MOs reference this item
    active_mo_count = self.env['mrp.production'].search_count([
        ('product_id.pg_item_id', '=', self.id),
        ('state', 'in', ('draft', 'confirmed', 'progress'))
    ])
    if active_mo_count:
        raise UserError(
            f"Cannot obsolete '{self.name}': {active_mo_count} open "
            "Manufacturing Orders reference this item. Close them first."
        )
    self.state = 'obsolete'
```

### 1.2 pg.item Transition Table

| From | To | Required Role | Trigger | Validation Required |
|---|---|---|---|---|
| concept | prototype | PG Engineer | Manual button | None |
| concept | engineering | PG Engineer | Manual button | None |
| prototype | engineering | PG Engineer | Manual button | None |
| engineering | released | **Automatic** | Revision release | First revision must reach released |
| engineering | released | PG Approver | Manual override | Written justification required |
| released | service | PG Approver | Manual button | No new-production MOs open |
| released | obsolete | PG Admin | Manual button | No open MOs, no open POs |
| service | obsolete | PG Admin | Manual button | No open service orders |
| any → concept | ❌ | — | — | **Never allowed** |
| released → engineering | ❌ | — | — | **Never allowed** |

---

## 2. pg.revision State Machine

### 2.1 State Definitions

```
DRAFT → REVIEW → RELEASED → SUPERSEDED → OBSOLETE
```

#### STATE: draft
- Active engineering work. All fields editable.
- Can be included in draft/review pg.ebom.
- Can be deleted by PG Engineer (before first review submission).

#### STATE: review
- Submitted for approval. Fields locked for editing.
- Reviewer may reject back to Draft with mandatory reason.
- Only PG Approver may transition to Released.

#### STATE: released
- **IMMUTABLE.** All field writes blocked at ORM level.
- Linked pg.item auto-transitions to released.
- Linked pg.ebom may now proceed to release.
- `effective_date` is stamped at this transition.
- `released_by` (res.users) is stamped at this transition.
- `release_timestamp` is stamped at this transition.

#### STATE: superseded
- A newer revision of the same pg.item has been released.
- This revision is preserved for traceability.
- Historical mrp.bom records that used this revision remain linked.
- Read-only. No modifications.

**Supersession Rule:** A revision can only be superseded AFTER its successor revision is Released. You cannot supersede in anticipation.

#### STATE: obsolete
- Administrative end-of-life for a revision.
- Typically applied to old superseded revisions after a defined retention period.
- Fully read-only. Never deleted.

### 2.2 pg.revision Transition Table

| From | To | Required Role | Required Conditions | Audit Action |
|---|---|---|---|---|
| draft | review | PG Engineer | All required fields populated; linked pg.item exists | Log: submitted_by, submitted_at |
| review | released | PG Approver | EBOM validated (if EBOM linked); pg.item in engineering+ state | Log: released_by, released_at, effective_date |
| review | draft | PG Approver | Rejection reason field populated (required) | Log: rejected_by, rejected_at, rejection_reason |
| released | superseded | PG Approver | Successor revision must already be in released state | Log: superseded_by, superseded_at, successor_revision_id |
| superseded | obsolete | PG Admin | No open MOs referencing this revision's mrp.bom | Log: obsoleted_by, obsoleted_at |
| draft | obsolete | PG Engineer | Manual cleanup of abandoned drafts | Log: obsoleted_by, obsoleted_at |

### 2.3 Required Fields at Release

These fields must be non-null before a pg.revision can transition to `released`:
- `name` (revision code: A, B, C... or 1, 2, 3... for documents)
- `pg_item_id` (linked Engineering Item)
- `change_summary` (min 10 characters — what changed from previous revision)
- `effective_date` (stamped at release, cannot be future-dated without PG Admin)
- `released_by` (auto-stamped from `self.env.user`)

---

## 3. pg.ebom State Machine

```
DRAFT → REVIEW → RELEASED → SUPERSEDED
```

| State | Description | Can Edit Lines | Can Release to MRP |
|---|---|---|---|
| draft | Working EBOM, changes expected | ✓ Yes | ✗ No |
| review | Submitted for approval, locked | ✗ No | ✗ No |
| released | Approved. MRP BOM generated. | ✗ No | N/A (done) |
| superseded | A newer EBOM has been released for this root item | ✗ No | ✗ No |

**Critical constraint:** An EBOM cannot reach `released` state unless ALL of its
child pg.items are in `released` state. This is enforced by `_validate_pre_release()`.

```python
def _validate_pre_release(self):
    non_released = self.ebom_line_ids.filtered(
        lambda l: l.pg_item_id.state != 'released'
    )
    if non_released:
        items = ', '.join(non_released.mapped('pg_item_id.name'))
        raise ValidationError(
            f"Cannot release EBOM: the following child items are not Released: {items}"
        )
```

---

## 4. pg.release State Machine

```
PENDING → EXECUTING → COMPLETED
                    → FAILED
```

| State | Description | Editable | Retryable |
|---|---|---|---|
| pending | Release queued, not yet executed | Some fields | ✓ Yes |
| executing | Release in progress (atomic transaction) | ✗ No | N/A |
| completed | Release succeeded. mrp.bom created. **Immutable.** | ✗ No | ✗ No |
| failed | Release failed. Error logged. EBOM unchanged. | ✗ No | ✓ Yes (retry from pending) |

**COMPLETED releases are immutable audit records.** They can never be edited,
deleted, or voided. If a release needs to be "undone," the correct process is:
1. Create a new revision of the EBOM
2. Release the new revision (which supersedes the old MRP BOM)
3. Document the reason in the chatter

---

## 5. pg.eco State Machine (Phase 2)

```
DRAFT → ANALYSIS → APPROVAL → IMPLEMENTATION → RELEASED
                             → REJECTED
```

| State | Description |
|---|---|
| draft | ECO created, impact assessment not yet started |
| analysis | Engineering analyzing affected items, BOMs, MOs |
| approval | Change board review |
| implementation | Engineering executing the approved changes |
| released | ECO closed. All affected revisions released. |
| rejected | ECO rejected by approval board. No changes made. |

---

## 6. Transition Authority Matrix (Complete)

| Model | Transition | Minimum Role | Notes |
|---|---|---|---|
| pg.item | concept→prototype | PG Engineer | |
| pg.item | *→engineering | PG Engineer | |
| pg.item | engineering→released | PG Approver | Or automatic via revision release |
| pg.item | released→service | PG Approver | Requires MO check |
| pg.item | *→obsolete | PG Admin | Requires MO + PO check |
| pg.revision | draft→review | PG Engineer | |
| pg.revision | review→released | PG Approver | Triggers pg.item auto-release |
| pg.revision | review→draft | PG Approver | Rejection reason required |
| pg.revision | released→superseded | PG Approver | Successor must be released |
| pg.revision | *→obsolete | PG Admin | |
| pg.ebom | draft→review | PG Engineer | |
| pg.ebom | review→released | PG Approver | All child items must be released |
| pg.ebom | review→draft | PG Approver | Rejection reason required |
| pg.ebom | released→superseded | Automatic | When new EBOM releases for same root |
| pg.release | pending→executing | PG Approver | System-triggered, not manual |
| pg.release | executing→completed | System | |
| pg.release | executing→failed | System | Error logged, EBOM unchanged |

---

## 7. Required Actions for Every Transition

Every state transition must perform ALL of the following:

### 7.1 Audit Log Entry
```python
# Always call this in every action_* method:
self._log_state_transition(
    from_state=old_state,
    to_state=new_state,
    actor=self.env.user,
    reason=reason or '',   # required for rejections, optional elsewhere
    timestamp=fields.Datetime.now(),
)
```

### 7.2 Chatter Message
```python
# Post a chatter message on every transition:
self.message_post(
    body=_("State changed: %s → %s by %s") % (
        old_state_label, new_state_label, self.env.user.name
    ),
    subtype_xmlid='mail.mt_log_note',
)
```

### 7.3 Notification
| Transition | Who to Notify |
|---|---|
| *→review | All members of `group_pg_approver` |
| review→released | The submitting engineer + `group_pg_mfg` |
| review→draft (rejection) | The submitting engineer + their manager |
| *→released (pg.revision) | All members of `group_pg_mfg` + procurement |
| *→obsolete | PG Admin + all followers of the record |
| EBOM released→superseded | All manufacturing stakeholders |

### 7.4 Validation Checks (must all pass before transition writes)
1. User has required security group
2. All required fields are populated (model-specific)
3. Cross-model dependencies satisfied (e.g., all child items released)
4. No blocking records exist (e.g., open MOs before obsoleting)
5. Successor revision exists (before superseding)

---

## 8. Forbidden Actions Summary (Quick Reference)

| Action | States Where Forbidden |
|---|---|
| Edit any non-chatter field | released, superseded, obsolete (pg.revision) |
| Edit any non-chatter field | released, obsolete (pg.item) |
| unlink() / delete | Any state except concept/draft |
| Include in production EBOM | concept, obsolete |
| Create mrp.bom | Any action other than pg.release.action_execute_release() |
| Transition state backward | released→review, released→draft (any model) |
| Supersede before successor released | pg.revision |

---

## 9. ORM Implementation Patterns

### 9.1 Immutability Enforcement Pattern
```python
class PgRevision(models.Model):
    _name = 'pg.revision'

    IMMUTABLE_STATES = {'released', 'superseded', 'obsolete'}
    CHATTER_FIELDS = {'message_ids', 'activity_ids', 'message_follower_ids'}

    def write(self, vals):
        for rec in self:
            if rec.state in self.IMMUTABLE_STATES:
                blocked = set(vals.keys()) - self.CHATTER_FIELDS
                if blocked:
                    raise UserError(_(
                        "Cannot modify revision '%s' in state '%s'. "
                        "Blocked fields: %s"
                    ) % (rec.name, rec.state, ', '.join(sorted(blocked))))
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state not in ('draft', 'obsolete'):
                raise UserError(_(
                    "Cannot delete revision '%s' in state '%s'. "
                    "Only Draft revisions can be deleted."
                ) % (rec.name, rec.state))
        return super().unlink()
```

### 9.2 State Transition Method Pattern
```python
def action_submit_for_review(self):
    """Draft → Review"""
    self.ensure_one()
    self._check_submission_requirements()
    old_state = self.state
    self.write({'state': 'review'})
    self._log_transition(old_state, 'review')
    self._notify_approvers()
    return True

def _check_submission_requirements(self):
    """Raise if submission requirements are not met."""
    if not self.change_summary or len(self.change_summary) < 10:
        raise UserError(
            "A change summary of at least 10 characters is required before submission."
        )
    if not self.pg_item_id:
        raise UserError("Revision must be linked to an Engineering Item.")
```

### 9.3 Auto-Transition Trigger Pattern
```python
# In PgRevision.action_release():
def action_release(self):
    """Review → Released (and triggers pg.item state update)"""
    self.ensure_one()
    self._validate_release_requirements()
    old_state = self.state
    self.write({
        'state': 'released',
        'effective_date': fields.Date.today(),
        'released_by': self.env.user.id,
        'release_timestamp': fields.Datetime.now(),
    })
    self._log_transition(old_state, 'released')
    # Auto-transition parent item if this is first release
    if self.pg_item_id.state in ('engineering', 'prototype'):
        self.pg_item_id.state = 'released'
        self.pg_item_id.message_post(
            body=_("Item auto-released: first revision '%s' released.") % self.name
        )
    self._notify_manufacturing_team()
```

---

## 10. Inter-Model State Dependencies

```
pg.item.state ← driven by → pg.revision.state
pg.ebom.state ← requires → pg.item.state (all lines = released)
pg.release.state ← requires → pg.ebom.state (= review or released)
mrp.bom ← created by → pg.release.state (= executing)
mrp.bom.active ← affected by → pg.release (new release supersedes old BOM)
```

### Dependency Validation Matrix

| Action | Pre-Condition | Error if Violated |
|---|---|---|
| pg.revision: draft→review | pg.item exists and state ≥ engineering | "Item must be in Engineering state or higher" |
| pg.revision: review→released | pg.item.state ≥ engineering | "Item must be Engineering or higher to release a revision" |
| pg.ebom: review→release | All pg.ebom.line.pg_item_id.state = released | "All child items must be Released" |
| pg.ebom: release | All pg.item have product.template linked | "All items must be linked to an Odoo product" |
| pg.item: released→service | No open mrp.production referencing item | "Close open Manufacturing Orders first" |
| pg.item: *→obsolete | No open mrp.production, purchase.order | "Close all open orders first" |

---

## Appendix A: State Color Coding

| State | Odoo Color | Hex Reference | Badge Class |
|---|---|---|---|
| concept | purple | `#8b5cf6` | `p-concept` |
| prototype | amber | `#d97706` | `p-proto` |
| engineering | blue | `#2563eb` | `p-eng` |
| draft | gray | `#64748b` | `p-draft` |
| review | amber | `#d97706` | `p-review` |
| released | green | `#12a150` | `p-released` |
| superseded | light-blue | `#0369a1` | `p-superseded` |
| service | teal | `#06b6d4` | `p-service` |
| obsolete | dark-gray | `#374151` | `p-obsolete` |

---

## Appendix B: Audit Log Schema

```python
# All transition logs must capture:
{
    'model': 'pg.revision',          # Which model
    'record_id': 42,                  # Which record
    'record_name': 'PG-ASM-0001 Rev B',
    'from_state': 'review',
    'to_state': 'released',
    'actor_id': 7,                    # res.users id
    'actor_name': 'A. Chen',
    'timestamp': '2026-06-08 09:14:22',
    'reason': '',                     # required for rejections
    'ip_address': '10.0.1.44',       # from request.httprequest.remote_addr
    'session_id': 'abc123',
}
# Stored in: pg.audit.log (separate model, never deleteable)
```

---

*Document: 020_State_Machine_Bible.md | Version: 1.0 | Status: COMPLETE*  
*ProductGraph Architecture Series | ProductGraph for Odoo 19 CE*
