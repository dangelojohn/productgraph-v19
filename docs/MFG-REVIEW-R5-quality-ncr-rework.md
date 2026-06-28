# MFG Review R5 — Quality / NCR / Rework / Quarantine

**Scope:** Southbrook Cabinetry, Odoo 19 CE prod stack, QC inspector + rework lead perspective.
**Date:** 2026-06-27 (live DB probe skipped — prod shell access not authorised; findings derived from source read-through against `~/southbrook-v19cr/addons/`).
**Build target:** `southbrook_mrp_kitchen_workcenters` v19.0.4.17.0 + `southbrook_manufacturing_intelligence` + `southbrook_qr_kit` + `southbrook_kitchen_mrp`.
**Native quality module:** NOT installed (no `quality_*` modules in the manifest depends or seen wired into the NCR queue — Southbrook built a parallel pipeline on `southbrook.mi.check`).

---

## What's good

The NCR backbone is honest and conceptually right:

1. **One-model strategy is correct.** `southbrook.mi.check` was extended (not forked) with the inspector layer (`x_sbk_check_stage`, `x_sbk_defect_type`, `x_sbk_defect_severity`, `x_sbk_result`, `x_sbk_inspector_id`, `x_sbk_workorder_id`) — see `southbrook_mrp_kitchen_workcenters/models/southbrook_mi_check.py:80-159`. That keeps engine-emitted advisories and inspector-recorded NCRs in one queryable table — the right call for pareto.
2. **Defect-type → rework workcenter mapping is declarative.** `southbrook_mi_check.py:386-407` defaults the rework destination by defect type (scratch→SAND, edge_defect→EDGE, hole_position→CNC_BORE, etc.). Inspector can override, but the keypress count drops for the 80% common path.
3. **Auto-quarantine on critical-severity fail is wired.** `southbrook_mi_check.py:201-277` creates a draft `stock.picking` to the Southbrook Quarantine internal location (`data/southbrook_quarantine_location.xml`) whenever `result=fail` AND severity crosses the threshold (`ir.config_parameter` `southbrook.ncr_quarantine.severity_threshold`, default `critical`). Idempotent + try/except-wrapped so the quarantine move CANNOT block the inspector saving the NCR. That's mature.
4. **Defect-type QR sheet is a real shop-floor primitive.** `controllers/defect_sheet.py` prints a wall poster of HMAC-signed QRs (one per defect type). Operator scans defect-QR + WO/MO/asbuilt QR within a 5-minute window (`southbrook.qr_kit.defect_context_window_seconds`) and an NCR drafts itself with `defect_type` pre-filled AND the WO auto-linked from the scan log — `qr_kind_handlers.py:174-282`. This is the cleanest 2-tap NCR-from-floor path in the stack.
5. **As-built closes the loop.** `southbrook_asbuilt.py:211-233` rolls up every linked check into `qc_overall_result` (pass / conditional / fail) and auto-flips to "conditional" when a fail has a closed rework WO. Sealed records are immutable. This is the customer-warranty-traceability path.
6. **Native scrap wizard is one-click from the WO form.** `views/mrp_workorder_views.xml:32-37` surfaces the stock.scrap wizard prefilled with `production_id`+`workorder_id`. Operator picks product, enters qty + reason, confirms — Odoo handles the inventory debit natively. No custom scrap model rebuilt.
7. **Rework cost rolls back to the originating WO** (not the rework WO), at `mrp_workorder.py:157-174` — accurate first-pass-yield economics.

---

## Top 5 Wins

### R5.1 — Photo capture on the NCR form (1 field add, massive impact)

**JTBD:** *"When I find a defect at finishing, I want to photo + tag the affected MO and route to rework in 4 taps, so I don't break flow."*

**Finding:** There is **NO photo / image field on `southbrook.mi.check`** — `grep "Binary\|defect_photo\|defect_image" southbrook_mrp_kitchen_workcenters/models/southbrook_mi_check.py` returns zero hits. The form view (`views/southbrook_ncr_views.xml:42-80`) has no `<field widget="image">` or `<field name="message_main_attachment_id">` either, and the model only inherits `southbrook.qr.mixin` — **not `mail.thread`** (verified: no `_inherit = ["mail.thread", ...]` and no `<chatter/>` parent inherit that adds it). The form template does declare `<chatter/>` but it'll be a no-op or render empty for a model without mail.thread. Net: **inspector cannot attach a defect photo** — the single most-important artefact for a quality record.

**Today's click count to attach a photo:** infeasible — would have to leave the NCR form, navigate to Settings → Attachments, create an attachment with `res_model=southbrook.mi.check` + `res_id=<id>` manually. Floor-realistic count: **never happens**.

**Win:** Add `x_sbk_defect_photo = fields.Image(max_width=1024, max_height=1024)` + `x_sbk_defect_photo_ids = fields.Many2many("ir.attachment", ...)` for the 2nd-Nth photos. Surface as `<field name="x_sbk_defect_photo" widget="image"/>` in the form Findings page and as a `widget="many2many_binary"` for the rest. Also add `_inherit = ["mail.thread", "mail.activity.mixin", "southbrook.qr.mixin"]` so the existing `<chatter/>` actually renders and the mobile camera-input attachment path works.

**Click count after:** Tap NCR queue → tap defect-QR scan flow auto-creates draft → tap `📷` widget → camera opens (mobile Safari/Chrome do this natively for `<input type="file" accept="image/*" capture="environment">` which the image widget emits) → confirm. **4 taps end-to-end.**

**Effort:** S (1 file Python, 1 file XML, 1 migration to add column). **Impact:** XXL — this is table-stakes for a quality module. **Files:**
- `southbrook_mrp_kitchen_workcenters/models/southbrook_mi_check.py:80-83` — add `mail.thread` to the inherit + add the field.
- `southbrook_mrp_kitchen_workcenters/views/southbrook_ncr_views.xml:72-78` — add `<field name="x_sbk_defect_photo" widget="image"/>` inside the Findings page.

**Risk:** Adding `mail.thread` is `_inherit` of an existing model — Odoo 19 supports this cleanly but generates schema for tracking/follower tables. Will need an `-i` not just `-u` to land cleanly on first deploy (warm with `-u` first; tracking columns reconcile). The `web_ribbon` + `widget="image"` combo has a known interaction with `widget="statinfo"` ribbons that requires `<sheet>` wrapping the image — already present here. **No view-validation v19 traps** (per `[[odoo19_view_validation_v19_traps]]`).

---

### R5.2 — NCR pareto graph + pivot view (zero new data, 1 view file)

**JTBD:** *"When 3 cabinets fail edgebanding on the same shift, I want a pareto by tool + material + operator to spot the root cause, so I don't keep producing defects all afternoon."*

**Finding:** `southbrook_ncr_views.xml:88` declares the NCR queue action with `view_mode="list,form"` ONLY. No graph, no pivot. The fields needed for pareto already exist + are indexed:
- `x_sbk_defect_type` (indexed, 14 values)
- `x_sbk_check_stage` (indexed, 9 stages)
- `x_sbk_workcenter_id` (stored related, indexed)
- `x_sbk_inspector_id`
- `x_sbk_defect_severity` (indexed)

Group-by filters exist (`southbrook_ncr_views.xml:121-128`) but the list view can't show 14 stacked bars at-a-glance. Today: supervisor scrolls a list, manually counts. Defects clustered on one workcenter for one operator on one shift = invisible until you've already shipped 5 bad cabinets.

**Today's click count to spot a cluster:** open NCR queue → 1 click filter "last 24h" (doesn't exist yet, would need adding too) → 1 click group-by workcenter → eyeball counts → 1 click drill into a group → manually re-group by inspector → eyeball again → still no severity-weighted view. **6-8 clicks for an inconclusive answer.**

**Win:** Add `graph` + `pivot` to `view_mode`. Add a `southbrook.mi.check.graph` view with default measure `__count` + group-by `x_sbk_defect_type` stacked by `x_sbk_workcenter_id`. Add a `pivot` view with rows=`x_sbk_check_stage`, cols=`x_sbk_workcenter_id`, measure=count. Add a "Last 7 days" filter on `x_sbk_date_checked`.

**Click count after:** Open NCR queue → tap Graph tab → cluster visible. **2 taps.**

**Effort:** S (one XML view file, 50 lines). **Impact:** L — this is the difference between catching the bad-edgebander-blade after 1 bad cabinet vs. 5. **Files:**
- `southbrook_mrp_kitchen_workcenters/views/southbrook_ncr_views.xml:88-92` — add `graph,pivot` to view_mode + new `<record>` blocks for the two views.

**Risk:** None. Selection fields and indexed m2o fields are first-class in Odoo 19 graph/pivot. No v19 view-validation traps. The `x_sbk_workcenter_id` is `store=True related` (line 120-125) so server-side aggregation is fine.

---

### R5.3 — Defect-context window: lengthen + show on NCR; expose to mobile (UX clarity, not a code rewrite)

**JTBD:** *"When I scan a defect QR after fetching a sheet from the wall, I want to be confident the NCR landed on the right WO — without leaving the floor to check."*

**Finding:** The defect-context-window auto-links the most-recent WO/MO/asbuilt/pkg scan within 300 seconds (`qr_kind_handlers.py:172`). It posts a chatter note ("Auto-linked to WO from your previous scan within the defect-context window") — `qr_kind_handlers.py:263-269`. **But the chatter is only visible after you open the NCR form, and the NCR form has no `mail.thread` inherit (see R5.1), so the post may silently no-op.** The scan response JSON does NOT echo back "linked to WO/MO X" — the operator only sees `"NCR drafted for defect 'scratch'"` (line 274). They have no in-the-moment confirmation that the right WO was attached.

5 minutes is also tight. Operator scans WO at station → walks to wall to grab defect-sheet → discusses with shift lead → scans defect QR. If that's >5min, the NCR lands UNLINKED, and the operator doesn't know.

**Today's click count to verify:** scan defect QR → mobile shows generic toast → unlock Odoo backend on the shop tablet → navigate to NCR queue → find the one with no WO → re-link manually. **5+ taps + cognitive load.**

**Win:** Three small changes:
1. Echo the resolved WO into the scan response. `qr_kind_handlers.py:270-282` — add `"linked_wo": wo_id, "linked_wo_name": <browsed WO display_name>` to the return dict so the mobile UI can flash "NCR drafted — linked to WO00123 (Cabinet B-3)" instead of just "NCR drafted".
2. Lengthen default window to 900s (15 min). It's a config_param, one DB write — no code change. Document the trade-off (longer = higher chance of stale context binding when an operator hops stations).
3. Render the chatter note BEFORE attaching mail.thread by writing the auto-link breadcrumb into the NCR's `recommendation` field (already exists) as a one-liner: `"Auto-linked to WO00123 from scan @ 2026-06-27 10:34"`. Visible without mail.thread.

**Click count after:** scan defect QR → mobile toast confirms WO. **1 tap.**

**Effort:** XS (2 dict adds + 1 string concat + 1 config_param). **Impact:** M — closes a confidence gap inspectors will eventually learn to mistrust. **Files:**
- `southbrook_mrp_kitchen_workcenters/models/qr_kind_handlers.py:260-282` — write the breadcrumb into `recommendation` or a dedicated `x_sbk_autolink_note` Char field; echo into return dict.

**Risk:** None functional. The breadcrumb-into-recommendation collides with inspector-typed recommendation text — better to add a Char field `x_sbk_autolink_note` to avoid stomping inspector input.

---

### R5.4 — Engineering deviation approval flow (the missing state)

**JTBD:** *"When a unit fails final QC but the cabinet is still installable with a customer waiver, I want a one-tap path to 'ship with deviation — engineering approved' instead of scrap or rework."*

**Finding:** `CHECK_RESULTS` selection (`southbrook_mi_check.py:72-77`) is `pass / fail / rework / hold`. There is **no `ship_with_deviation` state, no engineering approval workflow, and no waiver record**. `grep -rni "deviation\|waiver" addons/` returns one stray comment in `southbrook_asbuilt.py:6` ("record cut-spec deviations") — the concept is acknowledged but unimplemented. Today the "hold" state is the catch-all, which means the part stays in quarantine indefinitely until somebody manually scraps it or talks the customer into accepting it via email — no system trace.

The as-built `qc_overall_result` (`southbrook_asbuilt.py:213-233`) has 3 buckets — pass / conditional / fail. There's no "conditional-shipped-with-waiver" surface. When that homeowner calls 3 years later about a cabinet with a known cosmetic deviation, the as-built tells the field tech "fail" or "conditional" but not "we knew, the customer was OK with it, here's the engineering sign-off and the photo".

**Today's click count to ship-with-deviation:** undefined — no system path. Inspector emails engineering → engineering replies in chat → inspector marks the check as `hold` → MO sits → somebody manually completes the MO with the part anyway → quarantine quant orphaned. Multi-day, multi-system, **0 traceability**.

**Win:**
1. Add `ship_with_deviation` to `CHECK_RESULTS` (`southbrook_mi_check.py:72`).
2. Add `x_sbk_deviation_approved_by = fields.Many2one("res.users", domain=[("groups_id", "in", group_eng_id)])` + `x_sbk_deviation_approved_at = Datetime` + `x_sbk_deviation_customer_notified = Boolean` + `x_sbk_deviation_rationale = Text`.
3. Add an `action_request_deviation_approval` button that sends a mail.activity to the `mrp.group_mrp_manager` (or a new `group_eng_approver`) — Odoo's activity widget is in the form chatter once R5.1 lands mail.thread.
4. Add `action_approve_deviation` button gated by `groups="mrp.group_mrp_manager"` that stamps the approver fields + auto-cancels the quarantine picking (because the part is shipping).
5. Propagate to `southbrook.asbuilt.qc_overall_result` — add a 4th bucket `waiver` so 3-year-out warranty queries show "shipped with engineering waiver dated 2026-06-27, signed by John Doe, rationale: <text>". One JOIN, one display.

**Click count after:** Inspector finds defect → result=ship_with_deviation + rationale → tap "Request approval" → engineer gets activity in their inbox → opens NCR → tap "Approve" → done. **3 taps from inspector, 2 from engineer.**

**Effort:** M (selection + 4 fields + 2 buttons + 1 view block + 1 access-rule update). **Impact:** XL — this is the single biggest gap between "we have an NCR module" and "we have a quality system". **Files:**
- `southbrook_mrp_kitchen_workcenters/models/southbrook_mi_check.py:72-77` (CHECK_RESULTS extension), `:84-176` (new fields + buttons).
- `southbrook_mrp_kitchen_workcenters/views/southbrook_ncr_views.xml:17-40` (header buttons + Deviation group block).
- `southbrook_mrp_kitchen_workcenters/models/southbrook_asbuilt.py:41-45` (`QC_OVERALL_RESULTS` + new bucket) + `:211-233` (`_compute_qc_overall_result` to surface waiver).
- New security group `group_eng_approver` in `security/groups.xml` (file doesn't exist yet — currently access is via `mrp.group_mrp_manager`).

**Risk:** M. (1) Selection field extension on a stored field in v19 needs the value to land via XML data or a migration — bare Python addition orphans existing rows on `-u`. (2) The chatter-driven activity assignment requires R5.1's mail.thread to be live first — order matters; R5.1 ships before R5.4. (3) `qc_overall_result` is a stored computed field — adding a bucket triggers a full recompute on first `-u` (10k rows = ~30s on the QNAP postgres).

---

### R5.5 — First Article Inspection gate on new BoM versions (FAI lock)

**JTBD:** *"When engineering ships a new BoM version, I want the next MO of that product to require a First Article Inspection sign-off before subsequent MOs can run, so we catch BoM mistakes after 1 cabinet, not 50."*

**Finding:** `grep -rni "first.article\|FAI\|first_article" addons/` returns ZERO hits in the quality stack (the few hits are SCSS class names like `o_sb_floor_eq_fair` — unrelated). The as-built tracks `bom_version_snapshot` (`southbrook_asbuilt.py:96-103`) — so the data is there to detect "this is the first MO of a new BoM version" — but there's no gate, no inspection requirement, no lock. Engineering can ship a BoM with the wrong panel thickness and 30 MOs will produce 30 wrong cabinets before anyone notices.

**Today's click count for FAI:** undefined — no system path. Plant manager has to remember to manually inspect the first unit after every ECO.

**Win:** New small model `southbrook.fai.gate` with:
- `product_id` (Many2one product.product, indexed)
- `bom_version` (Integer, indexed)
- `state` (`pending` / `passed` / `failed`)
- `triggered_by_mo_id` (the first MO that activated the gate)
- `passed_by_check_id` (the southbrook.mi.check that signed off)
- `passed_by_user_id` + `passed_at`

Hook: `mrp.production.action_confirm` checks if a `southbrook.fai.gate` is pending for `(product_id, bom.southbrook_version)` AND `mo != triggered_by_mo_id`. If yes → `raise UserError("FAI pending on BoM v%d — only MO %s may run until QC signs off")`. Override available for `mrp.group_mrp_manager` via context flag.

Inspector workflow: on the trigger MO, the NCR form gets a "Pass FAI" button (visible only when an active FAI gate exists for this MO's BoM version). One tap stamps the gate.

**Click count:** Engineering versions BoM → ships first MO → inspector inspects → 1 tap "Pass FAI" → subsequent MOs unblock automatically. **1 tap per BoM version.**

**Effort:** L (new model + new view + 1 hook on `mrp.production` + 1 button on the NCR form + access rules). **Impact:** XL on the rare bad-ECO event (BoM mistakes are low frequency, catastrophic when missed). **Files (new + edit):**
- New: `southbrook_mrp_kitchen_workcenters/models/southbrook_fai_gate.py`, `views/southbrook_fai_gate_views.xml`, security row.
- Edit: `southbrook_mrp_kitchen_workcenters/models/mrp_production.py` — add `action_confirm` override.
- Edit: `southbrook_mrp_kitchen_workcenters/models/southbrook_mi_check.py` — add `action_pass_fai_gate` button + visibility logic.
- Edit: `views/southbrook_ncr_views.xml:17-40` — new header button.

**Risk:** M. The `action_confirm` override has to be defensive — there's no `southbrook_version` field on stock `mrp.bom` (only when `southbrook_plm` is installed — see `southbrook_asbuilt.py:270 "if "southbrook_version" in bom._fields"`). So the gate must `getattr` and silently no-op if PLM isn't installed. Also, blocking `action_confirm` is high-blast-radius: every planner's "Confirm MO" hits this — needs to be FAST (single SELECT on the gate table with an index, ~1ms). Concurrent MO confirms also race for the same gate — add `FOR UPDATE SKIP LOCKED` on the lookup.

---

## R5.6+ — Supplementary

### R5.6 — Lot/serial actually linked

`southbrook.asbuilt.lot_id` exists (`southbrook_asbuilt.py:106-112`) but **is not auto-populated** from `production_id.lot_producing_id` (the v19 MO's serial-tracking field). The comment says "Optional but strongly recommended" — in practice it'll be empty 95% of the time. Add a `_compute` that defaults from `production_id.lot_producing_id` on create. Effort XS. Without this, the NCR-to-customer warranty trace is broken at the lot-id step.

### R5.7 — Re-inspection after rework

When `action_create_rework_workorder` (`southbrook_mi_check.py:321-373`) spawns a rework WO, there's NO requirement that a NEW `southbrook.mi.check` be created when the rework WO completes. The original NCR's `x_sbk_result` stays as `fail` or `rework` forever. The asbuilt compute (`southbrook_asbuilt.py:211-233`) checks `rework_workorder.state == "done"` — done means produced, NOT inspected. Add an automated activity / required-check on rework-WO completion. Effort S.

### R5.8 — Operator attribution

`x_sbk_inspector_id` defaults to `self.env.user` (`southbrook_mi_check.py:126-130`). But there's NO `x_sbk_operator_who_caused_defect_id` — i.e. the OPERATOR on the originating WO, not the inspector. For pareto by operator, you need this field. The data is reachable via `x_sbk_workorder_id.user_id` (or `x_sbk_workorder_id.time_ids.user_id` for the actual operator who logged time). Add a stored related field. Effort XS. Enables R5.2 pareto to slice by operator.

### R5.9 — Quarantine quants visible somewhere

`stock_location_southbrook_quarantine` exists, the picking lands in draft, but **there's no menu entry that takes the inspector to "all quarantined inventory right now"**. They'd have to navigate Inventory → Locations → Filter → click. Add a menu item under Kitchen Ops → "Quarantine Inventory" with a domain on `stock.quant` filtered to the quarantine location. Effort XS, one menu + action.

### R5.10 — Reason field on Record Scrap

`action_open_scrap_wizard` (`mrp_workorder.py:200-227`) prefills the scrap with `production_id` + `workorder_id` but does NOT prefill `scrap_reason` (v19 stock.scrap has a `reason` field via `stock_scrap.reason_tag_ids` or free `origin`). The scrap path is divorced from the NCR path — if an inspector marks a check as `fail` AND the operator records scrap, those two records have no link. Add `default_origin` from `x_sbk_check_id` and add a back-link `x_sbk_scrap_id` on the NCR. Effort S. Closes the cost/material reconciliation gap.

### R5.11 — Defect-sheet QR is not mobile-optimised

`controllers/defect_sheet.py:54-68` renders 2.5"×3" cells in a flex-wrap layout. On a phone screen, this collapses to one tall column — readable but the operator wants ONE poster on the wall. The page IS designed for print (`@media print`), but the in-browser preview at small viewports looks broken. Add a viewport meta + min-width to keep it print-shaped on mobile. Effort XS.

### R5.12 — `southbrook.mi.check` has no `mail.thread` (root cause of multiple gaps above)

Already covered in R5.1 — but worth flagging that this is the single highest-leverage one-liner change in the addon: it unblocks chatter, activity-mixin, message_post auto-linking, attachment widget, and tracking — all of which R5.4/R5.5/R5.7 depend on. Land this in R5.1 and the others land cheaper.

---

## Baseline Tables

### Defect taxonomy (data, intact)

| Defect type | Default rework workcenter | Configured at |
|---|---|---|
| `wrong_dimension` | `workcenter_saw` | `southbrook_mi_check.py:400` |
| `wrong_material` | `workcenter_saw` | `:401` |
| `grain_direction` | `workcenter_saw` | `:402` |
| `edge_defect` | `workcenter_edge` | `:395` |
| `hole_position` | `workcenter_cnc_bore` | `:396` |
| `finish_defect` | `wc_paint` | `:394` |
| `scratch` | `wc_sand` | `:393` |
| `hardware_missing` | `workcenter_hw` | `:397` |
| `hardware_alignment` | `workcenter_hw` | `:398` |
| `assembly_square` | `workcenter_assy` | `:399` |
| `label_error` | (no default) | — |
| `missing_component` | (no default) | — |
| `packaging_issue` | (no default) | — |
| `other` | (no default) | — |

Gap: 4 of 14 defect types have no default rework workcenter → inspector has to pick manually 4/14 of the time. Cheap fix.

### Inspection stages (data, intact)

`after_cutting`, `after_edge_banding`, `after_cnc_drilling`, `after_sanding`, `after_painting`, `after_assembly`, `hardware_check`, `final_qc`, `packing_check` — `southbrook_mi_check.py:34-44`. 9 stages, covers the value stream end-to-end. Good.

### NCR Queue surfacing

Menu: Kitchen Ops → NCR / Rework Queue (`southbrook_ncr_views.xml:135-139`, parent `menu_sbk_ops_root` from `southbrook_kitchen_menus.xml:35`).
Default filter: `x_sbk_result IN ('fail', 'rework', 'hold')` AND `pending_rework=1` context.
Views shipped: list + form. **Missing: graph, pivot, kanban.**

### Quarantine

Location: `stock_location_southbrook_quarantine` (internal, child of WH/Stock) — `data/southbrook_quarantine_location.xml`.
Trigger: `southbrook_mi_check.write` on `result=fail` + severity ≥ `southbrook.ncr_quarantine.severity_threshold` (default `critical`).
Behaviour: draft `stock.picking` with one move, idempotent, try/except-wrapped, fires `southbrook.ops.event` alert.
**Missing: a menu entry to view all quarantined inventory.**

### Approvals / Deviation

**None.** No engineering-approval workflow, no waiver record, no "ship with deviation" state. (R5.4)

### First Article Inspection

**None.** No FAI gate, no lock on subsequent MOs, no inspector sign-off on new-BoM-version trigger MO. (R5.5)

### Cost capture

- Rework labor: `mrp_workorder.x_sbk_rework_cost` (`mrp_workorder.py:90-96`, `:157-174`) — rolls back to originating WO, uses workcenter hourly rate × rework WO duration. Solid.
- Scrap material: native `stock.scrap` valuation, not linked to NCR. (R5.10)
- Quarantine carry cost: not tracked.

### Operator/inspector attribution

- Inspector recorded: `x_sbk_inspector_id` (default = current user).
- Operator-who-caused: **not stored** — would need a stored related from `x_sbk_workorder_id.user_id` or time-line aggregation. (R5.8)
- Tool/equipment attribution: workcenter level only via `x_sbk_workcenter_id`; no link to specific equipment.id (the `maintenance.equipment` records the PM addon defines).

### Trace to customer

Path: `southbrook.asbuilt.sale_order_id` (computed from `production_id.sale_line_id.order_id`) → `partner_id`. Works when MO was generated from a sale order. NCR → asbuilt linkage is by `production_id` (the asbuilt auto-snapshots all checks for the MO). For a known-bad shipped cabinet, the chain is: `serial_number` → `southbrook.asbuilt` → `qc_check_ids` → see what failed. Good. **Gap:** if `lot_id` isn't populated (R5.6), the lot → asbuilt search fails.

### Mobile-friendliness

- Defect-QR-scan flow: mobile-native (the controller route returns JSON, mobile PWA can call it).
- NCR form view: **standard Odoo form on mobile** — works but cramped on a 5" phone. The `<sheet>` layout doesn't collapse beautifully. No mobile-specific view defined.
- Photo capture: **broken** until R5.1 lands.

---

## Risk-ranked recommendation order

1. R5.1 + R5.12 (photo + mail.thread) — ship first, unblocks 3 downstream wins.
2. R5.2 (pareto views) — XS effort, immediate dashboard value.
3. R5.3 (scan-context confidence) — XS, depends on R5.1 chatter.
4. R5.6, R5.8, R5.9, R5.10 — XS bundle, ship together.
5. R5.4 (deviation approval) — depends on R5.1 mail.thread.
6. R5.5 (FAI gate) — depends on `southbrook_plm` being installed for full effect.
7. R5.7 (re-inspection requirement) — process change as much as code; pair with R5.1 first.
8. R5.11 (defect sheet mobile) — cosmetic.

---

## Out-of-scope confirmations

Per the brief, these were not assessed: MO lifecycle (R1), operator flow (R2), planner (R3), eng handoff (R4), sourcing (R6), dashboards (R7), mobile (R8). Where the quality flow intersects them (e.g. NCR-from-floor on the operator tablet, NCR pareto on the manager dashboard, scrap cost in the planner view), I've flagged the cross-cut and stayed in the R5 lane.
