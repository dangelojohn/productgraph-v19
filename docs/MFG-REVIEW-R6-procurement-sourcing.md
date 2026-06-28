# MFG-REVIEW-R6 — Procurement / Sourcing (Buyer JTBD)

> Read lens: a buyer / supply-chain planner sits down at 8 a.m. and works the
> day. Where does the ProductGraph procurement stack (pg.vendor / pg.vendor.part
> / pg.rfq / pg.rfq.bid + the purchase.requisition bridge) help them, where
> does it leave them re-keying or hunting for context, and where does it
> drop information on the floor between Engineering, Sourcing, and native
> Odoo Purchase?
>
> **Date:** 2026-06-27. **Status:** READ-ONLY. **Author:** R6 reviewer.
> **Adjacent reviews (out of scope here):** R1 MO lifecycle, R2 operator,
> R3 planner, R4 eng handoff, R5 quality, R7 dashboards, R8 mobile.
>
> **Live DB probe:** denied by auto-mode classifier (Production Read into
> `southbrook-postgres` / `docker exec southbrook-odoo`). All findings below
> are sourced from code + tests + the CLAUDE.md / PG-150 brief. Anything
> that would change based on row counts (e.g. "buyer is drowning in
> auto-RFQs", "no qualified vendors exist") is explicitly flagged.

---

## TL;DR — Top 5 Wins

| # | JTBD | Win | Clicks → | Effort | Impact | Risk |
|---|---|---|---|---|---|---|
| R6.1 | "When a PO is received late or defective, attribute it back to the engineering item / RFQ that ordered it." | Add `pg_item_id` + `pg_rfq_line_id` Many2one back-refs on `purchase.requisition.line`, propagate to `purchase.order.line` on conversion. | 7 → 2 | M | **High** | Low |
| R6.2 | "When I award an RFQ, I want the buyer-facing requisition to be ready-to-send, not a half-typed draft." | Bridge currently leaves the requisition with no `date_end` / no agreement type when multi-vendor / loses `lead_time_days`. Stamp these on award. | 6 → 1 | S | High | Low |
| R6.3 | "When a vendor goes Suspended / Disqualified mid-flight, tell me which open RFQs and which open POs are exposed." | Add a smart-button + activity-fire on the vendor form: "Open RFQs (3)" / "Open POs (12)". Drive from `pg.rfq.bid` and `purchase.order` filtered by `partner_id`. | 8 → 2 | S | High | Low |
| R6.4 | "When the daily auto-RFQ cron decides nothing's qualified, surface the unsourced item with a single button to fix the gap, not just a chatter log line." | Replace the cron's `_logger.warning` with a real `mail.activity` on the `pg.item` for the `responsible_id` (engineering owner) AND a "Sourcing Inbox" queue view. | 5 → 1 | M | Med-High | Low |
| R6.5 | "When my preferred source is the wrong choice for the lot in front of me (MOQ overflow, lead-time, lot-size break), let me switch source without leaving the line." | Add `action_switch_source(item_id)` on `purchase.requisition.line` + `purchase.order.line` that ranks the AVL by score (re-using `pg.rfq.bid._score_for`) and re-binds. | 9 → 3 | M | Med | Low |

Total estimated effort: ~9-12 dev days. The first two are essentially mechanical, the last three include UX.

---

## R6 — Baseline: what's actually wired today

### Models in scope

| Model | File | Lines | Role |
|---|---|---|---|
| `pg.vendor` | `addons/product_graph_vendor/models/pg_vendor.py` | 306 | 5-state qualification lifecycle; partner link; categories |
| `pg.vendor.part` | `addons/product_graph_vendor/models/pg_vendor_part.py` | 53 | AVL row: item × vendor, rank, MOQ, price hint, lead time |
| `pg.vendor.qualification` | `addons/product_graph_vendor/models/pg_vendor_qualification.py` | (audit-only log) | Append-only decision log |
| `pg.rfq` | `addons/product_graph_rfq/models/pg_rfq.py` | 379 | RFQ header, 5-state machine, auto-draft cron, award flow |
| `pg.rfq.line` | `addons/product_graph_rfq/models/pg_rfq_line.py` | 87 | One per (item, qty); computes recommended bid |
| `pg.rfq.bid` | `addons/product_graph_rfq/models/pg_rfq_bid.py` | 153 | Vendor bid; computed `score` (50/30/20 price/lead/MOQ) |
| `pg.rfq` (procurement bridge) | `addons/product_graph_procurement/models/pg_rfq.py` | 144 | Award → `purchase.requisition` create |
| `pg.item` (vendor inherit) | `addons/product_graph_vendor/models/pg_item.py` | 60 | `vendor_part_ids`, `primary_vendor_id` |

### Menus (post-PG-142 consolidation)

`Sourcing` root absorbs all of: Vendors, Vendor Parts (AVL), Qualification Log, RFQs, RFQ Lines, Bids. Native Odoo Purchase menu (`purchase.requisition`, `purchase.order`) is the buyer's other working surface. There is **no unified "Buyer Day" workspace** that combines the two — buyer must mentally bridge the menus.

### What works well

* **State-machine discipline.** Every `pg.rfq` / `pg.vendor` transition emits `pg.audit.log` + chatter + tracked field write. Same pattern as PG-110 lifecycle mixin (though `pg.rfq` does not consume the mixin — pre-mixin code, see `pg_rfq.py:155`). Auditor-friendly.
* **Awarded-RFQ immutability** at `pg_rfq.py:131-142` with a smart `_COMPUTE_ONLY_FIELDS` allowlist at `pg_rfq_bid.py:128` so the recompute storm doesn't trip the guard. This is a non-obvious win — recompute on a sibling bid's `unit_price` would otherwise blow up on an awarded RFQ.
* **`recommended_bid_id`** as suggestion-only at `pg_rfq_line.py:36-44` and `pg_rfq_bid.py:43-91`. The 50/30/20 blend is per-line min-max normalized, so a single outlier bid doesn't distort the rest. **Never auto-awards** — the human Award action is the only `is_awarded=True` write path. This is the right call for buyer trust.
* **AVL uniqueness constraint.** `pg.vendor.part._check_one_preferred_per_item` (`pg_vendor_part.py:38`) enforces a single preferred source per item. No silent dual-preferred drift.
* **`auto_create_requisition` flag** (`product_graph_procurement/models/pg_rfq.py:14`) defaults to True, so the buyer doesn't have to remember the bridge action. Smart-button `action_view_requisition` (line 134) gets them to the requisition in 1 click.
* **Score recompute is store-safe.** The compute on `pg.rfq.bid.score` lists all four siblings' dependencies, so adding/editing one bid re-ranks them all — no stale recommendations.
* **Auto-RFQ cron skips items already in flight.** `_cron_auto_draft_missing_rfqs` (`pg_rfq.py:274`) dedupes via `('state', 'in', RFQ_OPEN_STATES)` lookup. Won't double-paper the same item day-over-day.
* **Category-bound auto-RFQ.** Vendors must carry the item's `category_id` in their `category_ids` to be auto-seeded as a bid (`pg_rfq.py:319-324`). Buyer doesn't have to manually prune candidate lists by speciality.
* **Multi-vendor requisition graceful degradation.** Bridge sets `vendor_id` only when exactly one bid's partner is across the RFQ (`product_graph_procurement/models/pg_rfq.py:76-77`). Multi-vendor RFQs leave it blank rather than picking arbitrarily.

### What hurts on the buyer's day

1. **No "morning material check" view.** Native `purchase.order` list + native MRP demand are not joined to the PG vendor / RFQ surface. Buyer's morning is: open Purchase → filter by today's expected delivery → open MRP demand separately → mentally reconcile. Click count for "what's exposed today?" is easily 15+.
2. **Auto-RFQ failure is silent on the responsible engineer.** When the cron at `pg_rfq.py:313-330` can't find a qualified category-vendor it `_logger.warning(...)` and skips. The engineer who owns the item has no chatter post, no activity, no inbox marker. The buyer also doesn't know — they'd see it only if they happen to drill `pg.item` filtered by `vendor_part_count == 0`.
3. **PG-151 traceability gap is real and bites every PO.** Confirmed by `test_procurement_edge_cases.py:264-282`: `purchase.requisition.line` has **no** `pg_item_id` or `pg_rfq_line_id` back-ref. The only trace path is:
   ```
   purchase.order.line.requisition_line_id
     → purchase.requisition.reference  # string == rfq.name
     → grep pg.rfq for that name
   ```
   That's brittle (rename a requisition.reference and the chain breaks) AND lossy (the bridge only sets reference at line `66`, not on each line). A defective lot 6 months later can't be cleanly attributed back to the engineering revision that approved it.
4. **`unit_price_hint` is misleading on the buyer's PO.** The bridge maps `bid.unit_price → ReqLine.price_unit` (`product_graph_procurement/models/pg_rfq.py:108`) but the buyer's PO is created downstream from the requisition by the standard Purchase flow, and Purchase will re-query `product.supplierinfo` (Odoo native AML). **`pg.vendor.part` is NOT mirrored into `product.supplierinfo`** — see `pg_vendor.py` end. So the PO price defaults to whatever's on the product, not the AVL-blessed bid price. Buyer re-types.
5. **MOQ overflow is invisible.** `pg.vendor.part.moq` is on the AVL row, but neither `pg.rfq.line.qty` nor `purchase.requisition.line.product_qty` warn the buyer "MOQ is 100, you ordered 20, vendor will bill you 100." Nothing inflates qty automatically; nothing flags the gap.
6. **No "switch source" affordance.** If the preferred vendor is late or suspended, the buyer must (a) re-open `pg.item.vendor_part_ids`, (b) flip `is_preferred` on a different row, (c) wait for the next RFQ cycle, (d) hope the requisition picks up the change. There is no `action_switch_source` button on `pg.rfq.line`, `purchase.requisition.line`, or `purchase.order.line`.
7. **Vendor suspension is "fire and forget".** `pg.vendor.action_suspend` writes the state + chatter + audit log. It does NOT scan open RFQs, open requisitions, or open POs to alert the buyer "you have 3 open POs and 2 RFQs in flight with this vendor — review now." Buyer learns when the goods don't show up.
8. **`product_graph_procurement` does NOT depend on `purchase_mrp`.** Manifest at `__manifest__.py:44-47` lists `product_graph_rfq` + `purchase_requisition` only. MO-triggered demand (the native `purchase_mrp` auto-PO path) is therefore not aware of `pg.vendor.part` ranking. When a confirmed MO fires the procurement rule, Odoo picks the supplier by stock `route` / `product.supplierinfo` — bypasses the AVL entirely.
9. **Multi-vendor RFQ award is brittle.** `action_award` requires every line to have **exactly one** `is_awarded=True` bid (`pg_rfq.py:209-216`). For an item the buyer wants to dual-source (50/50 split between two vendors for risk reasons), there is no UI path — the state machine blocks the award.
10. **No quote-validity gate.** `pg.rfq.bid.valid_until` is a date field with no compute / no constraint. Award an RFQ 90 days after the bids expired — the bridge writes the stale price to the requisition without warning.
11. **Quote-expiry is not in the cron sweep.** The vendor module has a 30-day expiring-qualification cron (`pg_vendor.py:209`) but there is no analogous bid-expiry cron. Bids with `valid_until` past today silently keep their `score` and `recommended_bid_id` ranking.
12. **`vendor_part_number` is the only data flowing to the supplier.** The bridge maps `bid.vendor_part_number → ReqLine.product_description_variants` (`product_graph_procurement/models/pg_rfq.py:114-115`). But the supplier's catalog identifier on the PO sent over `purchase_edi_ubl_bis3` will be the Odoo product default code — `vendor_part_number` only appears in a free-text description field. Suppliers using EDI matching against their own catalog will miss it.
13. **The auto-RFQ cron is daily and unbounded.** `_cron_auto_draft_missing_rfqs` walks **every** purchased item in `engineering` or `released` state every day. On a 401-SKU cabinet catalog (per Southbrook hardware), it would consider every unsourced row daily. No throttle, no batch cap, no "skip items younger than N days" guard. First run after a fresh release of 50 items could create 50 draft RFQs in one shot.
14. **`recommended_bid_id` does not appear in the RFQ list view.** Only inside the form. Buyer scanning the RFQ list (`pg_rfq_views.xml:60`) sees `bid_count` and `best_unit_price` but not "is the cheapest also the best score?" — that requires opening each form.

---

## Top 5 Wins — Detail

### R6.1 — Procurement traceability back-ref (PG-151-FOLLOWUP)

**JTBD:** "When a PO is received late, defective, or audited, attribute it back to the engineering item, revision, and RFQ that approved it — without grep-matching a free-text reference string."

**Current state.** `test_procurement_edge_cases.py:272-282` actively asserts the absence of the back-ref:
```python
self.assertNotIn('pg_item_id', ReqLine._fields, ...)
self.assertNotIn('pg_rfq_line_id', ReqLine._fields, ...)
```
The bridge sets `req.reference = self.name` (`product_graph_procurement/models/pg_rfq.py:66`) and that's the ENTIRE downstream attribution chain. A PO that came from RFQ-00042 → requisition → PO can only be back-traced by:
1. Reading `purchase.order.line.requisition_line_id`
2. Reading `purchase.requisition.reference` (a Char that anyone can edit)
3. String-matching against `pg.rfq.name`
4. Walking back to `pg.rfq.line.item_id`

That's 4 manual hops, brittle at step 2, and impossible if `reference` was edited.

**Implementation.** Add to `product_graph_procurement`:
```python
class PurchaseRequisitionLine(models.Model):
    _inherit = 'purchase.requisition.line'
    pg_item_id = fields.Many2one('pg.item', index=True, ondelete='restrict')
    pg_rfq_line_id = fields.Many2one('pg.rfq.line', index=True, ondelete='set null')
    pg_rfq_id = fields.Many2one(related='pg_rfq_line_id.rfq_id', store=True)

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'
    pg_item_id = fields.Many2one(
        related='requisition_line_id.pg_item_id', store=True, index=True)
    pg_rfq_id = fields.Many2one(
        related='requisition_line_id.pg_rfq_id', store=True, index=True)
```
Stamp `pg_item_id` and `pg_rfq_line_id` in `_create_requisition()` at `product_graph_procurement/models/pg_rfq.py:103-117` while building `line_vals`. Tighten the assertions in `test_procurement_edge_cases.py:273-282` to positive assertions.

**Click count.** Audit "where did this defect originate?" goes from 7 (open PO → click req line → click req → read reference → search RFQ → click line → click item) to 2 (open PO → click `pg_item_id`).

**Effort.** M (1 day model + view + tests). Migration: backfill via the `reference` string match for legacy data.

**Impact.** High — unblocks supplier scorecarding, defect-rate-by-item, audit traceability. Closes the PG-150 §8 ticket #5 + the PG-150 §7 R4 risk.

**Risk.** Low. Additive Many2ones with `ondelete='set null'` (rfq line) / `'restrict'` (item, to prevent silent loss). No state-machine change. CLAUDE.md R1 boundary untouched (still no mrp.* writes outside `action_execute_release`).

**Files.** `addons/product_graph_procurement/models/pg_rfq.py:103-117` + new `models/purchase_models.py` + `views/pg_rfq_views.xml` to surface the back-ref.

---

### R6.2 — Bridge → ready-to-send requisition

**JTBD:** "When I award an RFQ, the resulting requisition should be ready for the procurement team to convert to POs immediately, not a partial record I have to top up with the date, vendor, and lead-time."

**Current state.** `_create_requisition()` at `product_graph_procurement/models/pg_rfq.py:43-129` sets:
* `company_id`, `reference`, `user_id` always
* `requisition_type='blanket_order'` when the field exists
* `vendor_id` only if exactly one partner across the RFQ
* per-line: `product_id`, `product_qty`, `product_uom_id`, `price_unit`, optional `product_description_variants`

It does NOT set:
* **`date_end`** (when the requisition expires). Odoo's `purchase.requisition` uses `date_end` to gate conversion to PO and surface it on the requisition list. Bridge could compute from `min(rfq.line.target_date)` or `awarded_on + max(awarded_bid.lead_time_days)`.
* **`schedule_date`** on lines (when they're needed). RFQ line carries `target_date`; bridge drops it.
* The buyer-friendly **lead_time annotation**. Even though `bid.lead_time_days` was the ranking factor, it doesn't survive to the requisition line.
* The **bid's `valid_until`** so the procurement team knows when the price expires.

**Implementation.** In `_create_requisition()`:
```python
req_vals['date_end'] = max(rfq.line_ids.mapped('target_date') or [fields.Date.today()])
# Per-line:
line_vals['schedule_date'] = rfq_line.target_date or req_vals['date_end']
# Chatter on the requisition:
req.message_post(body=_(
    "Quotes valid until %s. Best lead time: %d days."
) % (min(bids.mapped('valid_until') or [...]), ...))
```

**Click count.** "Convert requisition to PO" today: 6 (open req → set date_end → add schedule_date per line → add vendor → save → convert). Post-fix: 1 (convert).

**Effort.** S (half day model write + tests).

**Impact.** High — every awarded RFQ today produces a half-baked requisition the procurement team finishes manually. This is the biggest hidden cost in the current bridge.

**Risk.** Low.

**Files.** `addons/product_graph_procurement/models/pg_rfq.py:62-117`.

---

### R6.3 — Vendor exposure on suspension / disqualification

**JTBD:** "When a vendor goes Suspended or Disqualified, immediately tell me which open RFQs, open requisitions, and open POs are at risk so I can triage."

**Current state.** `pg.vendor.action_suspend` (`pg_vendor.py:172-179`) and `action_disqualify` (`pg_vendor.py:185-192`) require a comment and write state + chatter + audit log. They do NOT:
* Search `pg.rfq.bid` for open RFQs (state in draft/sent/received) referencing this vendor.
* Search `pg.vendor.part` for `is_preferred=True` rows that will silently lose their primary.
* Search `purchase.order` filtered by `partner_id == vendor.partner_id` with state in (draft, sent, to_approve).
* Raise an activity on the buyer / `responsible_id` to triage the exposed records.

A buyer learns the supplier's been suspended only on the next delivery exception.

**Implementation.** Add to `pg.vendor`:
```python
exposure_rfq_count = fields.Integer(compute='_compute_exposure', store=False)
exposure_po_count = fields.Integer(compute='_compute_exposure', store=False)
preferred_part_count = fields.Integer(compute='_compute_exposure', store=False)

def _post_suspend_exposure_warning(self):
    """Inside _transition, on state in (suspended, disqualified), build a
    chatter section listing the exposed RFQs/POs and fire an activity on
    self.responsible_id."""
```
Surface as smart-buttons in `view_pg_vendor_form` (currently only `vendor_part_count` is a smart-button at `pg_vendor_views.xml:122`).

**Click count.** "What's exposed if I disqualify Vendor X?" today: 8 (search RFQs by vendor → count → search Vendor Parts by vendor → count preferred → search POs by partner → count → maybe MOs via supplier route → mentally tally). Post-fix: 2 (open vendor → read smart buttons).

**Effort.** S (1 day compute + view + activity-fire on transition).

**Impact.** High. Suspension/disqualification is exactly the moment a buyer needs the panopticon, and the current code is silent.

**Risk.** Low.

**Files.** `addons/product_graph_vendor/models/pg_vendor.py:172-192` + `views/pg_vendor_views.xml:120-130`.

---

### R6.4 — Auto-RFQ failure surfaces on the responsible engineer + Sourcing Inbox

**JTBD:** "When the system can't auto-draft an RFQ because there's no qualified vendor for an item's category, route the failure to the engineer who owns the item AND show it on my Sourcing Inbox so it doesn't fall through."

**Current state.** `_auto_draft_for_item` (`pg_rfq.py:308-321`) does this when no candidate vendor matches:
```python
_logger.warning(
    "pg.item %s in category %s has no qualified category "
    "vendors — auto-RFQ skipped.", ...)
return False
```
Log-only. The engineer who created the item doesn't know. The buyer doesn't know. The cron returns success the next day too, papering over the gap.

**Implementation.**
1. Inside `_auto_draft_for_item`, on the no-vendor path, post a `mail.activity` on the `pg.item` with `user_id = item.responsible_id or fallback to a group_pg_engineer member`.
2. Add a "Sourcing Inbox" window action under the Sourcing menu, filtering `pg.item` where `vendor_part_count == 0 AND item_type == 'purchased' AND state IN ('engineering', 'released')`. Order by latest revision date desc.
3. Sister filter: `pg.item` where `primary_vendor_part_id.vendor_id.state IN ('suspended', 'disqualified')` — items whose preferred source is no longer good.
4. Add a kanban view grouped by `category_id` so the buyer sees clusters: "Hinges have 12 unsourced, Drawer slides have 3."

**Click count.** "What's the engineering team handing me that I can't source?" today: 5+ (filter pg.item by purchased → group by category → eyeball `vendor_part_count == 0` → cross-reference with active RFQs). Post-fix: 1 (open Sourcing → Inbox).

**Effort.** M (1.5 days — activity-fire + new menu + kanban view).

**Impact.** Med-High. This is where the "engineering threw stuff over the wall" friction lives.

**Risk.** Low. The activity-spam risk is bounded by the cron's existing dedupe (`pg_rfq.py:291-296`).

**Files.** `addons/product_graph_rfq/models/pg_rfq.py:308-330` + new `views/pg_sourcing_inbox.xml`.

---

### R6.5 — "Switch source" affordance on req / PO lines

**JTBD:** "When the preferred vendor on a line is the wrong choice for the lot in front of me (late, MOQ overflow, price spike), let me re-rank the AVL and switch source without leaving the line."

**Current state.** To swap source today on a confirmed requisition / PO line, the buyer must:
1. Open `pg.item.vendor_part_ids` (or navigate via menu).
2. Identify alternative `pg.vendor.part`.
3. Flip `is_preferred` on the new row (constraint at `pg_vendor_part.py:38` enforces single preferred — so the old one auto-clears).
4. Hope the downstream code re-queries the AVL.
5. Manually edit the requisition / PO line's vendor field.
6. Manually edit the price.

That's 9+ clicks AND it permanently re-prefers the new source even if this is a one-lot deviation.

**Implementation.** Add a button on `purchase.requisition.line` and `purchase.order.line` (gated to lines with `pg_item_id != False` — relies on R6.1 landing first):
```python
def action_switch_source(self):
    """Open a wizard listing AVL alternatives for this line's pg_item, ranked
    by score (re-using pg.rfq.bid._score_for or its standalone analogue),
    showing price, lead-time, MOQ, qualification state. User picks one;
    line's vendor + price are re-bound without touching the AVL preferred."""
```
The wizard ranks `pg.vendor.part` rows where `item_id == pg_item_id AND vendor_id.state == 'qualified'`. Score function = same 50/30/20 blend used on bids.

**Click count.** 9 → 3 (open line → click Switch Source → pick from ranked list).

**Effort.** M (2 days — wizard + score helper extraction + UI hook).

**Impact.** Med. Less critical than R6.1-R6.4 because the workaround works, but a real friction-eater on the worst day (vendor outage, expediting scramble).

**Risk.** Low. Doesn't change AVL preferred state, doesn't touch MO/MRP boundary.

**Files.** New `addons/product_graph_procurement/wizards/switch_source_wizard.py` + view + `models/purchase_models.py` (from R6.1).

---

## R6.6 — Supplementary findings (smaller wins, ranked)

| ID | Finding | Effort | Impact |
|---|---|---|---|
| R6.6 | **`product.supplierinfo` mirror.** `pg.vendor.part` is the AVL but Odoo native Purchase queries `product.supplierinfo` (the AML). Add a one-way sync on `pg.vendor.part.write` / `create` that upserts a matching `product.supplierinfo` so MO-triggered procurement (`purchase_mrp`) picks the AVL-blessed price + lead time. | M | High |
| R6.7 | **Bid expiry cron.** Add `_cron_flag_expired_bids` mirroring the qualification-expiry cron at `pg_vendor.py:209`. On bid `valid_until < today`, post chatter on the parent RFQ and zero-out `score` so the `recommended_bid_id` doesn't pick it. | S | Med |
| R6.8 | **Auto-RFQ batch cap + cooldown.** Add `productgraph.auto_rfq_max_per_day` ICP (default 25) and a `last_auto_rfq_on` field on `pg.item` so the cron doesn't re-paper an item more than once per N days. Prevents the 50-item-release thunderclap. | S | Med |
| R6.9 | **MOQ warning on RFQ line.** `pg.rfq.line.qty < bid.moq` should compute a `moq_shortfall` warning and surface a decoration in the list view. Buyer either bumps qty or accepts the overage knowingly. | S | Med |
| R6.10 | **Dual-source award.** Relax `pg_rfq.py:209-216` to allow N awarded bids per line iff a per-bid `awarded_qty` field sums to `line.qty`. The bridge then writes one requisition line per awarded bid. | M | Med (special-case but real for risk-split sourcing) |
| R6.11 | **Recommended bid in list view.** Add `recommended_bid_id` to `view_pg_rfq_list` (`pg_rfq_views.xml:4-26`) as a `decoration-info`-rendered column. Saves a form-open per RFQ during triage. | XS | Low |
| R6.12 | **Vendor portal stub.** CLAUDE.md §R9 lists "Vendor portal / supplier self-service" as Phase 3. Even a minimal `/my/rfqs` portal route for vendors to view + bid would close the email-vs-portal gap that buyers feel every day. Out of scope for R6 but flag it. | L | High (Phase 3) |
| R6.13 | **EDI vendor part-number.** `vendor_part_number` flowing to `product_description_variants` (free-text) means UBL/BIS3 sends a description, not a `supplier_item_id`. If `purchase_edi_ubl_bis3` is in active use, this should populate `product.supplierinfo.product_code` so the UBL `SellersItemIdentification` is correct. | S | Med (only if EDI is live) |
| R6.14 | **Subcontracting / dropship not wired.** `pg.rfq` has no notion of `subcontracting` or `dropship` source-type, even though native Odoo `purchase_mrp` + `mrp_subcontracting` is installed in the deployment stack per `southbrook_cold_install_gaps.md`. A subcontract sourcing decision today bypasses the entire AVL surface. | M | Low-Med (rare for cabinetry direct, very real for hardware) |
| R6.15 | **`pg.rfq` doesn't consume `pg.lifecycle.mixin`.** PG-110 mixin would let RFQ pick up the audit/chatter/SoD discipline for free. Today it ships custom `_transition` (`pg_rfq.py:155-174`) duplicating ~30 LoC the mixin already encapsulates. Refactor candidate for the next sprint. | S | Code-quality, not buyer-facing |
| R6.16 | **`pg.vendor.responsible_id` is the only buyer pointer in the whole stack.** No `buyer_id` on `pg.rfq` or `pg.rfq.line`. Multi-buyer organisations can't filter "RFQs I own" without using `requestor_id` (the engineering requestor, not the buyer). | S | Med |
| R6.17 | **No "vendor performance" surface.** `pg.vendor` has no on-time-delivery rate, no defect rate, no PO count. Buyer awarding RFQ-00100 with two near-tied bids has no recent-history signal. Add stored computes pulling from `purchase.order` (qty received vs qty ordered, on-time vs late). | M | High (after R6.1 lands; depends on the back-ref) |
| R6.18 | **`active=True` filter on the auto-RFQ cron is correct but `state IN ('engineering', 'released')` may be too narrow.** Items in `service` state (still purchasable as service parts) are silently excluded. Confirm with engineering whether service-state items should be sourceable. | XS | Low |

---

## R6 — Buyer-day walk-through (click counts)

These are the JTBDs from the brief, walked end-to-end against current code. "Today" = production live as of 2026-06-27; "After" = after the Top-5 land.

### Walk 1 — Morning material check

**JTBD:** "What arrives today? What's at risk? Anyone on backorder?"

* **Today.** Open Purchase → Filter `state = purchase, date_planned <= today` → eyeball list. Open MRP demand separately to find at-risk items. No PG-aware overlay (no `pg_item_id` per R6.1 gap). ~8 clicks + mental reconciliation.
* **After R6.1 + R6.17.** Open Purchase → Group by `pg_item_id.category_id` → eyeball with vendor on-time-rate colour-coded. ~3 clicks.

### Walk 2 — MO-triggered demand

**JTBD:** "A new MO confirms — do POs auto-fire? What's my view of pending purchase?"

* **Today.** `purchase_mrp` does auto-fire POs from MO demand BUT uses `product.supplierinfo` (Odoo native AML), NOT `pg.vendor.part`. So the auto-PO ignores the engineering-blessed AVL. Buyer sees a PO with the wrong vendor and has to manually re-route. ~5+ clicks to fix per line.
* **After R6.6** (`product.supplierinfo` mirror). `purchase_mrp` picks the AVL-blessed primary automatically. 0 clicks (intended-path).

### Walk 3 — Vendor selection for item X

**JTBD:** "AVL has 3 qualified vendors — which to pick?"

* **Today.** Open `pg.item` → "Approved Sources" tab → eyeball rank, MOQ, lead-time, price hint. The rank field is a user-editable integer with no clear rule. Best vendor signal is "preferred flag" but that doesn't reflect the lot-specific decision (small order vs big order, urgent vs scheduled). ~3 clicks to see, 2 more to switch.
* **After R6.5** (switch-source wizard). Open line → Switch Source → see all 3 AVL rows ranked by the same 50/30/20 score the RFQ uses. ~2 clicks.

### Walk 4 — When to RFQ vs use standing price

**Today.** No rule, no UI signal. Buyer's tacit knowledge. Bids carry `valid_until` but nothing alerts buyer when standing prices expire.

**After R6.7** (bid-expiry cron). Expired bids visually flagged; new RFQ suggested via Sourcing Inbox.

### Walk 5 — PO creation + send

* **From RFQ award:** 1 click smart-button (`action_view_requisition`) to open the requisition, then native Odoo Purchase converts to PO. After R6.2, the requisition is ready-to-convert; today it needs date_end + schedule_date + maybe vendor backfill.
* **From requisition:** native Odoo flow.
* **From scratch:** native Odoo flow, no PG awareness.

### Walk 6 — Receive + GR

Native Odoo `stock.picking` flow. PG procurement does NOT extend receipt. Buyer reconciling a partial delivery against the RFQ that ordered it has no back-ref (R6.1 gap). Quality on receipt is R5 scope.

### Walk 7 — Vendor performance

* **Today.** No surface. `pg.vendor` form has no stat buttons for PO count, on-time-rate, defect-rate.
* **After R6.17.** Smart-buttons on `pg.vendor` form for "Open POs", "Late POs (30d)", "Defect Rate (90d)".

### Walk 8 — Approval ladder

Native Odoo `purchase.order` approval (currency-threshold rule on `purchase.order`'s `_check_approval` if `purchase` group settings has approval-required threshold > 0). PG procurement does NOT extend. Expediting sign-off is not codified.

### Walk 9 — Reorder logic

Native Odoo reordering rules (`stock.warehouse.orderpoint`). Not PG-aware. AVL primary not consulted when reorder fires unless R6.6 (`product.supplierinfo` mirror) lands.

### Walk 10 — MOQ overflow

* **Today.** Invisible. `pg.vendor.part.moq` is on the AVL row, never compared to the RFQ line qty or requisition line qty.
* **After R6.9.** RFQ line shows `moq_shortfall` decoration; buyer decides bump-or-accept.

---

## What's good — preserve in any refactor

* **`pg.rfq.bid._compute_score` per-line normalization** (`pg_rfq_bid.py:60-90`). The min-max normalization within siblings is exactly right for trade-off visibility. Don't replace with a fixed-weight global formula.
* **`recommended_bid_id` is suggestion-only.** Never auto-awards. Preserve this discipline (`pg_rfq.py:201-231`).
* **Awarded RFQ immutability + compute-only allowlist.** `_COMPUTE_ONLY_FIELDS` at `pg_rfq_bid.py:128` is a subtle but load-bearing detail; document it explicitly.
* **`promote_to_preferred` is opt-in on the RFQ header**, not auto. Lets the buyer award an RFQ for a one-off without polluting the AVL preferred state.
* **Auto-RFQ dedupe via `RFQ_OPEN_STATES`** (`pg_rfq.py:291-296`). The cron is idempotent across days.
* **Bridge `_create_requisition` skip-on-no-product** (`product_graph_procurement/models/pg_rfq.py:87-96`). Posts a chatter note and skips rather than crashing. Preserves the engineering-procurement boundary cleanly.
* **`vendor_id.partner_id` decoupling.** `pg.vendor` is the engineering view (categories, qualification state); `res.partner` is the commercial entity. The decoupling means a vendor can be commercially active for non-engineering purchases (office supplies) while disqualified for engineering.
* **`pg.vendor.qualification` is append-only.** The audit log row cannot be edited or deleted (`pg_vendor.py:114-126`). This is the right primitive for compliance.

---

## R6 — Risk & rollback notes (for the implementer)

| Win | Schema change? | Reversible? | Touches MRP/release boundary? |
|---|---|---|---|
| R6.1 back-ref | Yes (2 new Many2ones on `purchase.requisition.line`) | Yes (drop the fields, restart) | No |
| R6.2 ready-to-send | No (writes to existing native fields) | Yes | No |
| R6.3 exposure surface | No (computed, non-stored) | Yes | No |
| R6.4 inbox | No (new view + activity-fire) | Yes | No |
| R6.5 switch-source | No (wizard) | Yes | No |

All 5 wins are CLAUDE.md R1-clean (no writes to `mrp.bom*`). None touches the `pg.release.action_execute_release` flow. None requires raw SQL.

---

## R6 — Reinforcement of PG-150 / PG-151 flagged gaps

* **PG-150 §8 ticket #5** ("Procurement traceability gap") is reinforced as R6.1, with the concrete test it's pinned by (`test_procurement_edge_cases.py:264-282`).
* **PG-150 §7 R4 risk** ("`purchase.requisition.line` lacks `pg_item_id` back-ref") is reinforced as R6.1.
* The R4 risk SHOULD be elevated from "Med × Med" to "Med × High" — every PO that flows through the system today is un-attributable for audit/defect-traceability purposes, and the volume is large.

---

## Appendix — file-and-line index for the implementer

| Concern | File | Line |
|---|---|---|
| RFQ state machine | `addons/product_graph_rfq/models/pg_rfq.py` | 18-24 |
| Awarded RFQ immutability | `addons/product_graph_rfq/models/pg_rfq.py` | 131-142 |
| Award flow | `addons/product_graph_rfq/models/pg_rfq.py` | 202-268 |
| AVL economics write-back | `addons/product_graph_rfq/models/pg_rfq.py` | 233-268 |
| Auto-draft cron | `addons/product_graph_rfq/models/pg_rfq.py` | 274-379 |
| Auto-RFQ silent skip | `addons/product_graph_rfq/models/pg_rfq.py` | 313-330 |
| Bid score compute | `addons/product_graph_rfq/models/pg_rfq_bid.py` | 60-90 |
| Bid `_COMPUTE_ONLY_FIELDS` | `addons/product_graph_rfq/models/pg_rfq_bid.py` | 128 |
| Recommended bid | `addons/product_graph_rfq/models/pg_rfq_line.py` | 64-76 |
| Vendor state machine | `addons/product_graph_vendor/models/pg_vendor.py` | 12-27 |
| Vendor suspend (silent on exposure) | `addons/product_graph_vendor/models/pg_vendor.py` | 172-179 |
| Qualification expiry cron | `addons/product_graph_vendor/models/pg_vendor.py` | 209-305 |
| AVL preferred-source constraint | `addons/product_graph_vendor/models/pg_vendor_part.py` | 38-53 |
| Primary vendor compute | `addons/product_graph_vendor/models/pg_item.py` | 34-48 |
| Bridge requisition create | `addons/product_graph_procurement/models/pg_rfq.py` | 43-129 |
| Bridge `vendor_id` single-partner gate | `addons/product_graph_procurement/models/pg_rfq.py` | 70-77 |
| Bridge `vendor_part_number` → description | `addons/product_graph_procurement/models/pg_rfq.py` | 114-115 |
| Traceability gap (asserted) | `addons/product_graph_procurement/tests/test_procurement_edge_cases.py` | 264-282 |
| Idempotence guard | `addons/product_graph_procurement/tests/test_procurement_edge_cases.py` | 220-242 |
| Sourcing menu consolidation | `addons/product_graph_vendor/views/pg_vendor_menus.xml` | 7-29 |
| Sourcing menu (RFQs) | `addons/product_graph_rfq/views/pg_rfq_menus.xml` | 8-25 |
| AVL form-embed on `pg.item` | `addons/product_graph_vendor/views/pg_item_inherit_views.xml` | 9-37 |

---

*Generated 2026-06-27 — R6 review. Buyer JTBD lens only.
Adjacent reviewers (R1/R2/R3/R4/R5/R7/R8) own their respective surfaces.
For the next sprint, R6.1 + R6.2 + R6.3 are the three I would queue first —
together they take ~2 dev days and close the largest buyer-day friction
points without touching the release boundary.*
