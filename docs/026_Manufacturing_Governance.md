# 026 — Manufacturing Governance
## ProductGraph for Odoo 19 CE
### Version 1.0 | Priority: CRITICAL | Completeness Target: 100%

> **Purpose:** This document defines the formal, binding rules governing the
> interaction between ProductGraph engineering data and Odoo manufacturing
> execution. Every scenario where an engineering change (revision, release,
> obsolescence) collides with an active operational record must be resolved
> by a rule in this document — not by an ad-hoc developer decision.
>
> **If these rules are not written before Release and MO-linked code is built,
> AI coding agents will produce inconsistent, conflicting implementations.**

---

## Table of Contents

1. Governing Principles
2. Scenario Matrix
3. Scenario 1: New Revision Released — Open Manufacturing Order Exists
4. Scenario 2: New Revision Released — Open Purchase Order Exists
5. Scenario 3: New Revision Released — Existing Inventory in Stock
6. Scenario 4: Alternate Parts — Automatic Substitution Rules
7. Scenario 5: EBOM Supersession — Active MRP BOM Impact
8. Scenario 6: Item Obsoleted — Manufacturing and Procurement Impact
9. Scenario 7: ECO Implementation — Partial Production Run In-Flight
10. Scenario 8: Release Failure — Rollback Behavior
11. Revision Traceability in Manufacturing Records
12. Implementation Requirements
13. Decision Log

---

## 1. Governing Principles

These principles are non-negotiable. All scenario rules below are derived from them.

### GP-1: Engineering Owns the BOM — Manufacturing Owns Execution
ProductGraph defines what the product **should be** (EBOM).  
Odoo MRP defines what the product **is being built as** (mrp.bom + mrp.production).  
These two domains are connected only at the Release boundary.

### GP-2: Open Operational Records Are Frozen at Their Creation State
A Manufacturing Order or Purchase Order that already exists is a **commitment**.  
Engineering changes do not retroactively alter commitments.  
The system FREEZE rule applies universally: existing open records are not automatically updated by new releases.

### GP-3: Traceability Is Inviolable
Every manufactured unit must be traceable to the exact revision of the EBOM under which it was produced. This traceability cannot be broken by any system action, migration, or cleanup job.

### GP-4: Human Decision Required for Exceptions
The system may warn, may notify, may block. But no system action automatically resolves an ambiguity between engineering intent and manufacturing execution. A human must decide and must document the decision.

### GP-5: No Silent Changes
If a ProductGraph action could affect operational data (MOs, POs, inventory), the affected stakeholders must receive an explicit, actionable notification. Silent background updates are prohibited.

---

## 2. Scenario Matrix (Quick Reference)

| # | Trigger | Affected Object | System Action | Human Required |
|---|---|---|---|---|
| 1a | New revision released | Open MO | FREEZE at current BOM | Notify MFG engineer |
| 1b | New revision released | Future MO | Use new BOM automatically | None |
| 2a | New revision released | Open PO | WARN procurement | Human decides: amend or allow Rev A |
| 2b | New revision released | New PO creation | Block Rev A purchase | Procurement chooses Rev B |
| 3a | New revision released | Existing Rev A inventory | ALLOW consumption (with annotation) | Engineering flags if incompatible |
| 3b | Incompatible revision | Existing inventory | BLOCK + WARN | Engineering + procurement decide |
| 4a | Stock-out of primary part | Alternate part available | BLOCK auto-substitution | MFG engineer approves per-MO |
| 4b | AVL-approved alternate | Stock-out (Phase 2) | ALLOW with log | Auto, but logged |
| 5 | New EBOM released | Existing MRP BOM | Supersede old BOM, freeze open MOs | Notify MFG team |
| 6 | Item obsoleted | Open MOs / POs | BLOCK obsolescence | Close orders first |
| 7 | ECO mid-production | Partial lot in flight | FREEZE current lot, new lot uses new rev | ECO coordinator decides |
| 8 | Release fails | EBOM / MRP BOM | Full rollback, EBOM unchanged | Engineer investigates, retry |

---

## 3. Scenario 1: New Revision Released — Open Manufacturing Order Exists

### Context
Engineering releases Revision B of Base Cabinet Assembly.  
4 open Manufacturing Orders (mrp.production) reference mrp.bom derived from Revision A.

### Decision: FREEZE + WARN

**Rule 1.1 — Existing Open MOs Are Frozen**
```
When: pg.release.action_execute_release() completes successfully
      AND mrp.production records exist in state ('draft','confirmed','progress')
      referencing the superseded mrp.bom
Do:   Do NOT update, modify, or re-link those mrp.production records.
      Those MOs proceed to completion against the Revision A BOM.
Do NOT: Automatically reroute MOs to the new BOM.
Do NOT: Display a validation error that blocks the release.
```

**Rule 1.2 — New MOs Use New BOM**
```
When: A new mrp.production is created AFTER the new release date
      for the same product
Do:   The new mrp.production references the new mrp.bom (Revision B).
      This is handled by Odoo's standard BOM selection logic.
      ProductGraph ensures the new mrp.bom is correctly marked active.
```

**Rule 1.3 — Mandatory Notification**
```
When: Rule 1.1 applies (any open MO is frozen)
Send: Notification to all members of group_pg_mfg containing:
      - Item name and revision
      - Number of frozen MOs
      - MO numbers and expected completion dates
      - Link to MO list
      - Instruction: "Review these MOs and determine if a deviation is required."
```

**Rule 1.4 — Chatter Record**
```
On each affected mrp.production record, post a chatter note:
"Engineering Note [auto]: A new revision of [item] has been released (Rev B).
This Manufacturing Order will complete using the original BOM (Rev A mrp.bom #71)
as of the ProductGraph governance policy. If you require a change, contact engineering."
```

**Rule 1.5 — Release Audit**
```
The pg.release record must capture:
- frozen_mo_count: Integer count of frozen MOs
- frozen_mo_ids: Many2many to mrp.production (for audit)
```

**Implementation Check:**
```python
def _notify_frozen_mos(self, old_bom):
    frozen_mos = self.env['mrp.production'].search([
        ('bom_id', '=', old_bom.id),
        ('state', 'in', ('draft', 'confirmed', 'progress')),
    ])
    self.frozen_mo_count = len(frozen_mos)
    self.frozen_mo_ids = [(6, 0, frozen_mos.ids)]
    if frozen_mos:
        # Notify manufacturing group
        self._send_freeze_notification(frozen_mos)
        # Post note on each MO
        for mo in frozen_mos:
            mo.message_post(
                body=self._get_freeze_chatter_message(),
                subtype_xmlid='mail.mt_log_note',
            )
```

---

## 4. Scenario 2: New Revision Released — Open Purchase Order Exists

### Context
Revision B of Soft Close Hinge (PG-BUY-0102) is released.  
An open Purchase Order references the Revision A version (product.product linked to Rev A).

### Decision: WARN + Human Decides

**Rule 2.1 — Open POs Are Not Automatically Updated**
```
ProductGraph does NOT modify open purchase.order or purchase.order.line records.
This is absolute. The purchasing team must manually decide.
```

**Rule 2.2 — Mandatory Procurement Notification**
```
When: A pg.revision release affects a purchased component
      AND open purchase.order.line records exist for this component
Send: Notification to all members of group_pg_mfg + procurement contacts:
      "A purchased component has a new approved revision.
       Open PO [number] for [qty] × [item] Rev A may need amendment.
       Contact supplier to confirm acceptability of Rev A or request Rev B."
```

**Rule 2.3 — Block New PO Creation for Obsolete Revision (Optional — Admin Toggle)**
```
When admin setting pg.config.block_old_revision_po = True (default: False):
      Block creation of new purchase.order.line for a component 
      where a newer released revision exists.
      Display: "A newer revision (Rev B) is available for [item]. 
               Please confirm which revision to purchase."
Note: This is an admin-configurable setting, not a hard block by default,
      because some organizations legitimately run multiple revisions in parallel.
```

**Rule 2.4 — Receipt of Old-Revision Stock**
```
If a PO for Rev A is received into inventory AFTER Rev B is released:
- Allow the receipt (do not block stock.picking)
- Log annotation on the stock.move: "Received Rev A stock. Current engineering 
  revision is B. Verify compatibility before using in production."
- Notify: Warehouse manager and engineering contact
```

---

## 5. Scenario 3: New Revision Released — Existing Inventory in Stock

### Context
100 units of Soft Close Hinge Rev A are in stock (stock.quant).  
Rev B is released. Can Rev A inventory be consumed in manufacturing?

### Decision: ALLOW by default, BLOCK if explicitly marked incompatible

**Rule 3.1 — Default: Allow Consumption**
```
Existing Rev A inventory MAY be consumed in Manufacturing Orders
that reference the Rev B mrp.bom, UNLESS:
  (a) The pg.revision record for Rev B has the flag 
      `incompatible_with_previous = True`
  (b) Engineering has explicitly posted a "Do Not Use" notice

Rationale: Most revision changes are improvements, not incompatibilities.
Requiring inventory disposal or quarantine for every revision change
is operationally impractical.
```

**Rule 3.2 — Consumption Annotation**
```
When Rev A inventory is consumed against a Rev B MO:
Record in the production lot/serial note (if lot tracking enabled):
"Built with Rev A component stock. Rev B BOM in effect at time of build."
This annotation must flow through to the mrp.production finished lot record.
```

**Rule 3.3 — Incompatible Revision Flag**
```python
# On pg.revision:
incompatible_with_previous = fields.Boolean(
    string="Incompatible with Previous Revision",
    help="If True, existing inventory of the previous revision CANNOT be "
         "consumed in new production runs. Engineering must formally declare "
         "this at release time.",
    default=False,
)
incompatibility_reason = fields.Text(
    string="Incompatibility Reason",
    help="Required if incompatible_with_previous=True. "
         "Explain why old-revision stock cannot be used.",
)
```

**Rule 3.4 — System Behavior When Incompatible Flag is Set**
```
When incompatible_with_previous = True on released pg.revision:
1. Set stock.quant quarantine flag on all affected product lots (if lot tracking)
   or post warning on product.template (if no lot tracking)
2. Block stock.move consumption of old-revision stock into new-revision MOs
   (requires custom stock.move validation — Phase 2 feature)
3. Notify: Warehouse, Procurement, Manufacturing
4. Log: pg.audit.log with full details
```

---

## 6. Scenario 4: Alternate Parts — Automatic Substitution Rules

### Context
pg.ebom.line has an alternate group (e.g., HINGE-A: Primary = Hinge-Standard, 
Alternate = Hinge-Premium). Primary is out of stock.

### Decision: BLOCK automatic substitution. Human approval required per MO.

**Rule 4.1 — No Automatic Substitution (Phase 1)**
```
ProductGraph DOES NOT automatically substitute alternate parts in any Phase 1 
manufacturing scenario. A human (Manufacturing Engineer or Production Planner) 
must explicitly approve any alternate part use on a per-MO basis.

Rationale: Alternate parts may have different lead times, costs, or supplier 
certifications. Auto-substitution without human review creates uncontrolled 
manufacturing variations that may violate quality or regulatory requirements.
```

**Rule 4.2 — Alternate Usage Logging**
```
When a manufacturing user manually selects an alternate part for an MO:
1. Record on mrp.production: alternate_used = True, alternate_part_reason = [text]
2. Post chatter note on mrp.production
3. Post chatter note on parent pg.ebom
4. Log in pg.audit.log
```

**Rule 4.3 — Phase 2 AVL-Based Auto-Substitution**
```
In Phase 2, when pg.vendor.part records carry an avl_auto_substitute = True flag
AND the alternate part appears on the Approved Vendor List (AVL):
- System MAY propose (not execute) auto-substitution
- User must confirm the substitution
- Confirmation is logged in pg.audit.log
This behavior requires Phase 2 AVL/AML infrastructure to be complete first.
```

---

## 7. Scenario 5: EBOM Supersession — Active MRP BOM Impact

### Context
EBOM-00084 (Rev B) is released. EBOM-00081 (Rev A) was previously released.
mrp.bom #71 was generated from EBOM-00081 Rev A.

### Decision: Supersede old EBOM and old MRP BOM. Freeze open MOs.

**Rule 5.1 — Old EBOM State Transition**
```
When a new pg.ebom is released for the same root pg.item:
- Previous pg.ebom transitions to state = 'superseded'
- This is automatic, triggered by the release action
```

**Rule 5.2 — Old MRP BOM Handling**
```
When a new mrp.bom is created by a release:
- The previous mrp.bom for the same product.template is set active = False
  (Odoo's standard mechanism for BOM versioning)
- Existing open mrp.production records that reference the old mrp.bom are 
  NOT affected (Rule 1.1 applies: they are frozen)
- New mrp.production records will use the new active mrp.bom automatically
```

**Rule 5.3 — No Overlapping Active BOMs**
```
At any point in time, only ONE mrp.bom should be active for a given 
product.template + BOM type combination.
The release action must deactivate the previous BOM atomically in the same 
database transaction as creating the new one.
```

---

## 8. Scenario 6: Item Obsoleted — Manufacturing and Procurement Impact

### Context
An engineer attempts to obsolete pg.item PG-BUY-0102 (Soft Close Hinge).

### Decision: BLOCK obsolescence if any open records exist.

**Rule 6.1 — Pre-Obsolescence Validation (Hard Block)**
```python
def action_obsolete(self):
    self.ensure_one()
    
    # Check 1: Open Manufacturing Orders
    open_mo_count = self.env['mrp.production'].search_count([
        ('product_id.pg_item_id', '=', self.id),
        ('state', 'in', ('draft', 'confirmed', 'progress')),
    ])
    
    # Check 2: Open Purchase Orders
    open_po_count = self.env['purchase.order.line'].search_count([
        ('product_id.pg_item_id', '=', self.id),
        ('order_id.state', 'in', ('purchase', 'draft')),
    ])
    
    # Check 3: Active in any non-superseded mrp.bom
    active_bom_count = self.env['mrp.bom.line'].search_count([
        ('product_id.pg_item_id', '=', self.id),
        ('bom_id.active', '=', True),
    ])
    
    errors = []
    if open_mo_count:
        errors.append(f"{open_mo_count} open Manufacturing Orders")
    if open_po_count:
        errors.append(f"{open_po_count} open Purchase Orders")
    if active_bom_count:
        errors.append(f"active in {active_bom_count} MRP BOM lines")
    
    if errors:
        raise UserError(
            f"Cannot obsolete '{self.name}'. Active references: "
            + "; ".join(errors)
            + ". Resolve these before obsoleting."
        )
    
    self.state = 'obsolete'
```

**Rule 6.2 — Inventory Warning (Not a Block)**
```
If inventory stock.quant quantity > 0 for an item being obsoleted:
- Do NOT block the obsolescence
- Post a warning notification to warehouse management:
  "[qty] units of [item] remain in inventory. 
   This item has been obsoleted by engineering. 
   Disposition decision required: scrap, return to supplier, or hold."
```

---

## 9. Scenario 7: ECO Implementation — Partial Production Run In-Flight

### Context (Phase 2 — requires pg.eco)
An Engineering Change Order (ECO) is approved and being implemented.
A production lot of 50 units is partially complete: 20 finished, 30 in-progress.

### Decision: Freeze current lot. New lot uses new revision.

**Rule 7.1 — In-Flight Lot Policy**
```
A manufacturing lot that has already started (any qty produced > 0) is 
committed to its original BOM and original revision. The ECO does not 
retroactively change this lot.

The 30 remaining units in the current lot complete against the original BOM.
The next production lot (new mrp.production) uses the new ECO-revised BOM.
```

**Rule 7.2 — ECO Coordinator Notification**
```
The ECO coordinator receives a notification:
"ECO-[number] implementation is effective from the next production lot.
Current lot [lot-number] will complete under the previous revision.
Expected completion: [date]. New revision effective from: next lot."
```

**Rule 7.3 — Genealogy Record**
```
Both the old-revision lots and new-revision lots must be permanently 
traceable to their respective EBOM/revision. This information must 
survive MO archival and long-term data retention.
```

---

## 10. Scenario 8: Release Failure — Rollback Behavior

### Context
pg.release.action_execute_release() fails mid-execution (e.g., database error,
validation failure discovered after transaction start, network issue).

### Decision: Full rollback. EBOM and pg.revision unchanged.

**Rule 8.1 — Atomic Transaction Required**
```python
def action_execute_release(self):
    self.ensure_one()
    self.state = 'executing'
    
    try:
        with self.env.cr.savepoint():
            bom = self._create_mrp_bom()
            self._create_mrp_bom_lines(bom)
            self._update_revision_state()
            self._write_release_log(bom)
            self._notify_stakeholders()
            self.state = 'completed'
    except Exception as e:
        self.state = 'failed'
        self.failure_reason = str(e)
        self.message_post(
            body=f"Release FAILED: {str(e)}. No changes were made to EBOM or MRP.",
            subtype_xmlid='mail.mt_log_note',
        )
        # The savepoint rollback ensures no partial mrp.bom was created
        raise
```

**Rule 8.2 — Idempotent Retry**
```
A failed pg.release may be retried from state 'failed' → 'pending'.
Before retry, the system verifies no partial mrp.bom was created by the 
previous attempt (check for orphaned mrp.bom with the release reference).
```

**Rule 8.3 — Failure Notification**
```
On release failure:
1. Set pg.release.state = 'failed'
2. Log full exception in pg.release.failure_reason
3. Notify: The releasing user + PG Admin group
4. EBOM state remains unchanged (still in 'review')
5. pg.revision state remains unchanged (still in 'review')
```

---

## 11. Revision Traceability in Manufacturing Records

Every mrp.production (Manufacturing Order) that was created under a specific
Engineering revision must carry a permanent, queryable reference to that revision.

### Required Fields on mrp.production (added via _inherit)
```python
class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    pg_release_id = fields.Many2one(
        'pg.release',
        string="ProductGraph Release",
        readonly=True,
        help="The ProductGraph release that generated the MRP BOM used by this MO.",
    )
    pg_ebom_id = fields.Many2one(
        'pg.ebom',
        string="Source EBOM",
        readonly=True,
        related='pg_release_id.pg_ebom_id',
        store=True,
        help="Engineering BOM that defined this Manufacturing Order's structure.",
    )
    pg_revision_code = fields.Char(
        string="Engineering Revision",
        readonly=True,
        related='pg_release_id.pg_revision_id.name',
        store=True,
        help="The revision code (A, B, C...) under which this MO was created.",
    )
```

These fields must be:
- Read-only (never editable by users)
- Indexed (for performance on large datasets)
- Included in all manufacturing reports and genealogy queries

---

## 12. Implementation Requirements Summary

| Rule | Implementation Location | Phase |
|---|---|---|
| MO freeze on release | `pg.release.action_execute_release()` | Phase 1 |
| MO freeze notification | `pg.release._notify_frozen_mos()` | Phase 1 |
| PO warning on release | `pg.release._notify_affected_pos()` | Phase 1 |
| Inventory annotation | `pg.release._annotate_existing_stock()` | Phase 1 |
| Alternate auto-block | `pg.ebom.line` + view constraint | Phase 1 |
| Item obsolete pre-check | `pg.item.action_obsolete()` | Phase 1 |
| Release atomic savepoint | `pg.release.action_execute_release()` | Phase 1 |
| MO traceability fields | `mrp.production` (inherited) | Phase 1 |
| Incompatible revision flag | `pg.revision` | Phase 1 |
| AVL auto-substitute | `pg.vendor.part` | Phase 2 |
| ECO in-flight lot policy | `pg.eco.action_implement()` | Phase 2 |

---

## 13. Decision Log

Record all governance decisions with date and decision-maker.

| Date | Scenario | Decision | Decided By | Rationale |
|---|---|---|---|---|
| 2026-06-08 | Scenario 1: MO Freeze | FREEZE existing open MOs | Architecture Team | Existing MOs are commitments. Engineering intent does not override operational execution. |
| 2026-06-08 | Scenario 2: PO Handling | WARN only, no auto-update | Architecture Team | Procurement relationship with supplier must be managed by humans. |
| 2026-06-08 | Scenario 3: Inventory | ALLOW unless flagged incompatible | Architecture Team | Most revision changes are backward-compatible. Blanket blocks create waste. |
| 2026-06-08 | Scenario 4: Alternates | BLOCK auto-sub in Phase 1 | Architecture Team | Quality and regulatory risk of uncontrolled substitution outweighs convenience. |
| 2026-06-08 | Scenario 6: Obsolete | HARD BLOCK if open records | Architecture Team | Obsoleting an item with active production would corrupt manufacturing execution. |
| 2026-06-08 | Scenario 8: Rollback | Full atomic rollback on failure | Architecture Team | Partial releases create orphaned MRP data. All-or-nothing is safer. |

---

*Document: 026_Manufacturing_Governance.md | Version: 1.0 | Status: COMPLETE*  
*ProductGraph Architecture Series | ProductGraph for Odoo 19 CE*
