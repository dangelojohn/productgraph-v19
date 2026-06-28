# R9 — Master Roadmap (Synthesis of R1-R8)

> Read-only synthesis of eight parallel JTBD reviews against the Southbrook
> Cabinetry Odoo 19 CE production stack + ProductGraph v19 sibling repo.
> Single voice. Single prioritized backlog. Single quarter-by-quarter
> sequencing. Author: senior manufacturing systems consultant.
> Date: 2026-06-27.

---

## 1. Executive Summary

**What we reviewed.** Eight independent specialist reviews — MO lifecycle
(R1), shop-floor operator (R2), planning and scheduling (R3), engineering-to-
manufacturing handoff (R4), quality / NCR / rework (R5), procurement and
sourcing (R6), manager dashboards and reporting (R7), and mobile / shop-floor
UX (R8). Each agent walked one JTBD lens end-to-end, read code at file:line
density, and surfaced five top wins plus 8-14 supplementary findings, plus
a "what's good" list per surface. Combined output: roughly 40 ranked wins
plus 75 supplementary findings across 8 documents, against a live deployment
running 13 MOs, 99 WOs, 14 workcenters, two production packages, zero
operator-tier users, and zero rows in the floor telemetry tables (scan log,
downtime, handover, asbuilt) that the platform deliberately built to capture.

**Top three themes the synthesis surfaces.** First, **"shipped form, no data
behind it"**: Path-B MO header redesign (R1) is 100% NULL on every live MO;
dashboards (R7) aggregate fields no UI feeds; the Manufacturing Intelligence
tab (R1, R7) has a beautiful tab and a "review" status that surfaces nowhere
in any list. Second, **"documented but never built"**: the PG↔southbrook
bridge (R4) is in eLearning slides as if it were live but the code does not
exist — `southbrook.eco` has zero PG fields and never calls
`action_execute_release`; the result is two parallel eng→mfg loops with
disjoint audit trails. Third, **"installed but no UI entry"**: six-plus
OpenValue OPL-1 planning modules (R3) are live in production but the planner
cannot reach them from any menu, and the engine that would level-load 47 MOs
across a week is one URL-bar-typed-model-ID away. The bridge spec, the
scheduling engine, the defect-photo path, the FAI gate, the OTD percentage,
and the operator role itself are all in the same condition: scaffolded,
imagined, partially deployed, not yet load-bearing.

**Top three risks (real money / customer-facing exposure).** (1) **Warranty
traceability gap**: as-built records (R4) capture `bom_version_snapshot` but
never capture the PG revision code, PG release ID, or southbrook ECO ID that
produced the BoM — a 3-year-out warranty call cannot answer "what did
engineering change about this serial." (2) **Defect-photo impossibility (R5):
`southbrook.mi.check` has no `fields.Image` field and does not inherit
`mail.thread`, so a quality inspector physically cannot attach a defect
photo without bypassing the form. (3) **OPL-1 legal hold (R3): six planning
modules run on the prod box without sanctioned deployment terms — this is
the worst-of-both-worlds (paying silent legal risk while the planner
cannot use the surfaces).

**Recommended next-quarter program.** Q1: data-behind-the-form sprint —
backfill MO context fields, add defect photos + mail.thread to MI checks,
add `pg_item_id` back-references on requisition lines, and resolve the
bridge decision (build or retire). Q2: synthesis sprint — single
"is-today-OK" manager dashboard, tablet kanban for operators, OTD
percentage with history, surface the scheduling engine. Q3: closing the
loop — FAI gate, ship-with-deviation flow, auto-reflow on PO slip, operator
PIN gate, dark mode for night shift. Q4: scaling — vendor portal, EDI,
service worker offline queue, cross-project conflict views, full
month-over-month trending.

**Ship/don't-ship-as-is.** The platform is genuinely impressive in
architecture (HMAC QR, advisory-locked closures, audit-log discipline, the
MI engine) and genuinely under-loaded in adoption (no operator users, empty
telemetry tables, NULL context fields). Do not roll back; do not re-platform;
**do invest the next quarter in making the data flow through the surfaces
that already exist** before adding any new ones.

---

## 2. Cross-Cutting Themes

### Theme A — "Shipped form, no data behind it"

**Surfaces flagging this:** R1 (MO context fields), R3 (capacity pivot
calendar-blind), R5 (defect photo path infeasible), R7 (bottleneck
workcenter field reserved-never-written, OTD percentage absent),
R8 (Flutter dark theme absent, no shift attribution on KPIs).

**Concrete examples.**
- R1: every live MO has `x_sbk_cabinet_code`, `x_sbk_kitchen_room`,
  `x_sbk_install_due_date`, `x_sbk_kitchen_project_id` = NULL; the
  Path-B header bar renders blank labels.
- R7: `x_mi_bottleneck_workcenter_id` is on the model, hidden in the
  view because no engine writes it; `x_mi_workcenter_blocker_count`
  chip is decorative, not clickable.
- R7: every per-workcenter KPI is "since midnight today"; no history,
  no week-over-week, no MTD.
- R2: 0 rows in scan log, 0 in downtime, 0 in handover, 0 attachments
  on the 99 in-flight WOs.

**Unified fix.** A "data-behind-the-form" hardening pass — for every
form-promoted field, identify the upstream source (sale_order_line for
cabinet code; sale.order.commitment_date for install due;
project.task for kitchen project; PG release for revision code) and wire
the compute or create-time stamp. Where the upstream doesn't exist yet
(operator users, shift attribution), seed before iterating UI further.
Parallel: a one-time backfill cron for every NULL field with a known
derivation, run once against live, snapshotted for forensic rollback.

**Why it's bigger than any single ticket.** Every UI improvement in
R1-R8 silently assumes the underlying data flows. Without backfill,
shipping a "tablet kanban" (R2.1) means operators see blank cards;
shipping OTD % (R7.2) means a percentage of nulls; shipping cabinet
label rev codes (R4.W2) means printing "REV: " followed by nothing.
Backfill is the gate that all UX wins flow through.

### Theme B — "Documented but never built"

**Surfaces flagging this:** R4 (bridge spec advertised in eLearning,
zero PG fields on `southbrook.eco`), R5 (FAI mentioned in CLAUDE.md
heritage but zero hits in code), R8 (Service Worker offline cache
"week 2" in the wall kiosk README, never landed), R7 (Manufacturing
Intelligence claims as documented in cited memory; the engine is real
but the manager-side rollup is partial).

**Concrete examples.**
- R4: `southbrook_elearning_internal/data/elearning_slides_09.xml`
  documents `pg_ebom_id` and `pg_release_id` on `southbrook.eco` as
  live fields. They aren't. Operators who ran training believe the
  integration exists.
- R4: the bridge spec at `bridge_spec/SPEC.md` references
  `_inherit = 'southbrook_plm.eco'` but the actual live model is
  `southbrook.eco`; it overrides `action_close` but the real method
  is `action_apply`; spec is two ways out of date.
- R5: rework workorder completion does not require a new
  `southbrook.mi.check` — the original NCR's `x_sbk_result` stays as
  `fail` or `rework` forever even after rework is done.
- R8: Flutter `/app/` has `themeMode.light` hardcoded; no `darkTheme`
  is built despite being claimed in the design system.

**Unified fix.** A documentation-versus-reality audit pass per major
surface. Either implement the documented feature or formally retire
it in the same commit. Every "in progress" or "scaffolded" file should
have either a target sprint or a `docs/archived/` move date. The
eLearning slides must be the source of truth for what operators were
told they have access to.

**Why it's bigger than any single ticket.** Reality drift erodes user
trust faster than any single missing feature. An operator who finds
that the field their training said was "auto-populated from CAD" is
actually empty learns to disbelieve the whole interface. R4's bridge
fix and R5's mail.thread fix are not just engineering — they're
restoring documented promises.

### Theme C — "Installed but zero UI entry"

**Surfaces flagging this:** R3 (six-plus OPL-1 planning modules with
no menu link), R2 (defect-type QR sheet only reachable from NCR
manager menu, not the floor), R5 (quarantine inventory has no menu
entry, must navigate Inventory → Locations → filter), R6 (no
"Sourcing Inbox" for unsourced items even though the auto-RFQ cron
already detects them), R8 (bin-scan + load-unit endpoints exist but
no UI calls them — must curl or use third-party scanner apps).

**Concrete examples.**
- R3: `mrp_sfc_scheduling_engine` exists, `action_open_scheduling_engine`
  is defined, list/form views exist; none of it is wired into any
  Southbrook PM menu. Planner clicks: infinite. Target: 2.
- R6: when the auto-RFQ cron skips an item for lack of qualified
  vendor, the engineer who owns the item has no chatter post, no
  activity, no inbox marker. Buyer doesn't know either.
- R8: `qr_scan.py:66` (`/sb/qr/shipping/load-unit`) is well-architected
  JSON endpoint — two QRs in, status/fail JSON back. No UI calls it.

**Unified fix.** A "menu and entry-point" sprint that walks every
installed-but-unsurfaced model + every controller endpoint + every
documented capability against the user-role menu tree. For each
orphan, either (a) add a menu entry or button surface, (b) gate behind
a config flag and document the gate, or (c) remove from the install
chain. Pair with the menu-consolidation pattern PG-142 already did
for sourcing.

**Why it's bigger than any single ticket.** Hidden capability is
indistinguishable from missing capability. A scheduling engine you
cannot reach from a menu is, for the planner, a scheduling engine
that doesn't exist. Worse: every unreachable feature you ship adds
to the testing surface, the deployment risk, the legal-exposure
calculation (OPL-1 hold), and the cognitive load on engineers who
maintain a system they cannot see used.

### Theme D — "Engine wrote field, no consumer reads it"

**Surfaces flagging this:** R1 ("review" MI status never decorates
the kanban / list), R3 (`x_sbk_pending_wo_count` excellent foundation
surfaces only on workcenter list — no PM dashboard alert), R3
(`current_bottleneck_workcenter_id` computed per-task, never
aggregated per-station), R5 (lot_id not auto-populated from
production_id.lot_producing_id), R7 (downtime sums in list view —
no pivot, no trend), R7 (every MI yield/waste/throughput piece
stored — none aggregated).

**Unified fix.** A consumer-side completion pass: for each stored
compute, identify the dashboard/badge/decoration that surfaces it
and the historical-trending view that aggregates it. Many of these
are 5-30 line view-XML additions, not new code.

### Theme E — "Two parallel systems doing the same job"

**Surfaces flagging this:** R4 (PG ECO + southbrook ECO both run
impact analyses on different state sets, with different chatter shapes,
with different cron coverage), R4 (asbuilt captures `southbrook_version`
but ignores `pg_revision_code`), R4 (`pg.release._freeze_open_mos`
filters [draft, confirmed, progress] but `southbrook.eco._scan_and_flag_affected_mos`
filters [confirmed, progress, to_close] — different sets, different
results), R3 (`x_sbk_priority_level` parallel to native Odoo `priority`,
no precedence rule).

**Unified fix.** Per pair, declare canonical authority (which system
owns the field) and have the other thin-wrap or delegate. Field-by-
field reconciliation, not big-bang migration. Document in DECISIONS.md.

### Theme F — "Scan endpoint exists, handlers don't dispatch correctly"

**Surfaces flagging this:** R2 (scan log only written by controllers
— form-opens don't write a log row, so defect-context window silently
fails when operator opens WO via kanban), R8 (scan endpoint accepts
`action=start|pause|finish` but `WoActionKind.handle_action` doesn't
exist, so the traveler PDF's "scan to start" instruction lands on the
form for a manual button tap), R5 (floor traveler scan endpoint
swallows debit errors and returns `{ok:true}` anyway, misleading
operator).

**Unified fix.** A scan-handler conformance sprint: for every
documented action in QR docs/traveler PDFs, add the kind handler that
implements it, surface results in the JSON response, and add e2e
tests that fail loud on silent swallowing.

### Theme G — "Defect-photo path is impossible (case study)"

**R5's leading finding.** `southbrook.mi.check` has no `fields.Image`
and does not inherit `mail.thread`. The form template has `<chatter/>`
but it no-ops because the model lacks `mail.thread`. The scan handler
posts an auto-link breadcrumb to chatter — silent no-op for the same
reason. The most-important artefact in a quality system (the photo)
is physically uncreatable from the inspector's surface.

This is a one-liner fix (`_inherit = ["mail.thread", "mail.activity.mixin",
"southbrook.qr.mixin"]`) that unblocks four downstream wins (R5.1
photo capture, R5.3 chatter breadcrumb, R5.4 engineering deviation
approval needs activity assignment, R5.7 re-inspection after rework
needs activity).

**Why it's the canary.** A platform that ships a quality module
without photo capture is a platform that built UI before walking the
workflow. Treat the R5.1 fix as the canary on the whole "data-behind-
the-form" theme — same engineering muscle, ten minutes vs. ten weeks.

### Theme H — "Traceability gap on the requisition line"

**Surfaces flagging this:** R4 (asbuilt has no `pg_release_id`,
`pg_revision_id`, `pg_eco_id`), R6 (`purchase.requisition.line` has
no `pg_item_id`, `pg_rfq_line_id` back-refs — tests at
`test_procurement_edge_cases.py:264-282` actively assert their absence),
R6 (`product.supplierinfo` is not mirrored from `pg.vendor.part` —
auto-PO from MO demand uses native AML and bypasses the AVL),
PG-150 §7 R4 risk doc.

**Unified fix.** Single sprint: add `pg_item_id` + `pg_rfq_line_id`
to `purchase.requisition.line`, related fields onto
`purchase.order.line`, related fields onto `southbrook.asbuilt`,
and a one-time backfill cron from existing `reference` string matches.
This single batch closes the loop from engineering through sourcing
through manufacturing through warranty.

### Theme I — "Dashboard fragmentation"

**Surfaces flagging this:** R7 (5 scattered manager surfaces — PM
Dashboard, Family Throughput, MI Board, Late Orders, Kitchen Ops POC
— no fused "is today OK"), R3 (planner has 5+ navigation hops for
morning walk), R2 (operator has no tablet kanban — 5 taps to find
next WO), R8 (Flutter is customer-side, Odoo is operator-side, no
clear delineation for installers / drivers / temp labor).

**Unified fix.** Define the three primary user-day surfaces — manager
home, planner home, operator home — and treat every existing dashboard
as a candidate tile-source for them. The Kitchen Ops POC, promoted
from `TransientModel` to a singleton persistent model with tiles
sourced from the existing computes, is the right scaffold for the
manager home. For operator, the tablet kanban over `mrp.workorder`
filtered to current workcenter. For planner, the "Today's Plan" fused
view (R3.W2) over ready/can-start/blocker.

---

## 3. Master Prioritized Backlog

Sort key: Quick-Win bucket (Effort=S, Impact=H) first; then by impact-per-
effort; then by dependency clearance. Personas: PM=planner, OP=operator,
QC=quality, ENG=engineer, BUY=buyer, MGR=manager, INSTL=installer/driver,
SHIPMGR=shipping manager.

| ID | Title | Theme | Source | Persona | Effort | Impact | Dependencies | Quarter |
|---|---|---|---|---|---|---|---|---|
| W001 | Add `mail.thread` + `fields.Image` to `southbrook.mi.check` | G,A | R5.1, R5.12 | QC | S | Saves 5+ taps/NCR; enables 4 downstream wins; unblocks W004/W018/W020 | None | Q1 |
| W002 | Backfill MO kitchen context (cabinet_code, room, install_due, kitchen_project) | A | R1.W1 | PM | S | ~75 clicks/day saved/planner; unblocks group-by-room | sale_order_line zone | Q1 |
| W003 | Add `pg_item_id` + `pg_rfq_line_id` back-refs on `purchase.requisition.line` + `purchase.order.line` | H | R6.1, R4.W4 | BUY,QC,ENG | M | 7→2 clicks audit; unblocks vendor scorecard + warranty trace | None | Q1 |
| W004 | Add NCR pareto graph + pivot views (3 lines XML) | D | R5.2 | QC,MGR | S | 6-8 clicks → 2; catches defect-cluster after 1 cabinet vs 5 | W001 | Q1 |
| W005 | Wire scheduling engine into Southbrook PM menu (legal-hold-gated) | C | R3.W1 | PM | S | Engine becomes 2-click vs unreachable; legal sign-off is the actual gate | OPL-1 legal | Q1 |
| W006 | Decide W1 — bridge implement OR formally retire | B,E | R4.W1 | ENG | S | Unblocks W007/W008/W022; ends doc-vs-reality drift | None | Q1 |
| W007 | Cabinet label rev code + QR-embedded rev | A,B | R4.W2 | INSTL,QC | S | Installer can verify match-to-spec at site without calling office | W006 partial | Q1 |
| W008 | Asbuilt PG back-references (`pg_release_id`, `pg_revision_code`, ECO history) | H | R4.W4 | QC,ENG | S | Closes warranty trace from serial→engineering reason in 1 hop vs 3+2-archived | W006 | Q1 |
| W009 | Make Production Approval gate actually gate (constraint, not just button) | A | R1.W3 | PM | S | Eliminates ghost MOs (60% of source SOs bypass gate today) | None | Q1 |
| W010 | Split Ready Queue into "Ready to Release" (kanban) + "Blocked" (list with next-action) | I | R1.W4 | PM | S | Queue now means what label says; 75% of "Ready" today is misleading | W002 | Q1 |
| W011 | Auto-create form-open scan log so defect-context window works for non-scanned WO opens | F | R2.2 | OP,QC | S | Defect-velocity-preserving when tablet kanban (W014) ships | W001 | Q1 |
| W012 | Engineering deviation approval flow (`ship_with_deviation` state + waiver record) | A,G | R5.4 | QC,ENG | M | Closes "we knew, customer was OK" black hole; warranty traceable | W001 | Q1 |
| W013 | Bridge auto-RFQ failure to engineer activity + add Sourcing Inbox queue | C,D | R6.4 | BUY,ENG | M | Silent log line → addressable inbox per item-without-vendor | None | Q1 |
| W014 | Tablet kanban for operator (big-touch WO cards filtered to active WC) | I,A | R2.1, R8.13 | OP | M | Find-next-WO: 5 taps → 1 tap; enables every operator KPI capture | W002 partial | Q2 |
| W015 | Single-screen "Is today OK?" manager dashboard (promote Kitchen Ops POC to Model) | I | R7.1 | MGR | M | 5 hops → 1 screen; unblocks tiles for OTD, risk, leaderboards | W002, W016 | Q2 |
| W016 | Compute and surface OTD % (rolling 7d/30d/MTD) with daily snapshot | A,D | R7.2 | MGR,GM | M | Most-asked cabinetry-manager KPI; currently uncomputable | W002 | Q2 |
| W017 | Make traveler QR carry `?action=start|pause|finish` + implement WoActionKind handler | F,C | R2.3, R8.2 | OP | S | 0 taps to advance WO state; ~180 fewer interactions/op/day | W005 | Q2 |
| W018 | Lengthen defect-context window + echo resolved WO in scan response | G | R5.3 | OP,QC | XS | Mobile toast confirms "linked to WO00123" instead of generic "drafted" | W001, W011 | Q2 |
| W019 | "Today's Plan" planner home view fusing ready queue + WC load + start-blockers | I | R3.W2 | PM | M | 5+ menu hops → 1 click; all signals already computed | W010 | Q2 |
| W020 | Risk Scan view ranking MOs by lateness × customer-priority × MI severity | D | R7.3 | MGR | M | Converts late list to queue; 80/20 hour targeted | W002, W015 | Q2 |
| W021 | Bridge → ready-to-send requisition (stamp date_end, schedule_date, lead-time annotation) | B | R6.2 | BUY | S | 6 clicks per requisition conversion → 1 | None | Q2 |
| W022 | Vendor suspension exposure surface (smart buttons + activity-fire) | E,D | R6.3 | BUY | S | "What's exposed if I suspend X?" 8 clicks → 2 | None | Q2 |
| W023 | Frozen-MO unified correlator + state-set alignment (PG + Southbrook) | E | R4.W3 | PM,ENG | S | Single planner mental model; activity dashboard surfaces both paths | W006 | Q2 |
| W024 | Inline auto-fix buttons on MI check rows + flip cutlist auto-remediate ON | D | R1.W5 | PM | M | 4-6 blocked MOs unblock per cron; one-click for the rest | None | Q2 |
| W025 | First Article Inspection (FAI) gate on new BoM versions | B | R5.5 | QC,ENG | L | Catches bad ECOs after 1 cabinet not 50 | W001, southbrook_plm | Q2 |
| W026 | Workorder traveler PDF: drawings + CAD render thumbnail + 3 scan-QRs | A,F | R2.5, R8.2 | OP | S | Operator never builds-to-wrong-spec from missing drawing | W014 | Q2 |
| W027 | Auto-reflow on PO date slip cascades to dependent MO start dates (Suggest mode) | E | R3.W3 | PM,BUY | L | Highest planner-time-saver of any single feature | OPL-1 legal, W005 | Q3 |
| W028 | "What-if: drop in this rush MO" capacity preview wizard | C | R3.W4 | PM | M | Sales-to-planner phone call → 3-click in-system | W005 | Q3 |
| W029 | Cross-project conflict view (group `project.task` by `current_bottleneck_workcenter_id`) | D | R3.W5 | PM,MGR | S | 6+ clicks → 1; surfaces cross-job contention | None | Q3 |
| W030 | "Switch source" wizard on req + PO lines (re-rank AVL without flipping preferred) | C | R6.5 | BUY | M | 9 clicks → 3 for one-lot deviation | W003 | Q3 |
| W031 | `product.supplierinfo` one-way mirror from `pg.vendor.part` | E,H | R6.6 | BUY,PM | M | MO-triggered auto-PO uses AVL-blessed vendor + price | None | Q3 |
| W032 | Bid expiry cron + `valid_until` zero-out for recommended_bid_id | A,D | R6.7 | BUY | S | Awarded RFQ never uses stale prices | None | Q3 |
| W033 | Auto-RFQ batch cap + cooldown + last_auto_rfq_on dedup | A | R6.8 | BUY | S | Prevents 50-item-release thunderclap | None | Q3 |
| W034 | Engineering raise-ECO from WorkOrder (photo + free-text → eco draft) | C,B | R4.W5 | OP,ENG | M | Reverse-path now has a system route; engineering sees full context | None | Q3 |
| W035 | Operator PIN-gated scan logging (`hr_employee.pin`) | A | R8.14 | OP,MGR | M | Scan log credits employee, not shared session user; forensics work | None | Q3 |
| W036 | Audio confirmation on scan success/fail (50ms beeps) | A | R8.7 | OP | XS | No more silent failures in noisy shop | None | Q3 |
| W037 | Wi-Fi offline scan queue (Service Worker + IndexedDB FIFO) | F | R8.4 | OP | L | No lost Finishes during outages; addresses Q1 fsync trauma | None | Q3 |
| W038 | Dark mode for night shift (density + Flutter darkTheme + POD `@media`) | A | R8.5 | OP,INSTL | M | Felt by 4am-noon shift; right backdrop for renders | W014 | Q3 |
| W039 | Glove-grade tap targets via density SCSS bundle | A,I | R8.1 | OP | S | 8 taps → 3 to record scrap on defective panel | None | Q3 |
| W040 | "Report a Problem" wizard (scrap + defect + downtime in one screen) | I | R2.4 | OP | M | Cuts cognitive load at cell when something goes wrong | W001 | Q3 |
| W041 | Pre-fill rework workcenter for 4 defect types currently undefaulted | A | R5 baseline | QC | XS | label_error / missing_component / packaging_issue / other now default-routed | None | Q3 |
| W042 | Auto-snapshot `lot_id` from `production_id.lot_producing_id` on asbuilt | D,H | R5.6 | QC | XS | Lot-to-asbuilt trace stops failing 95% of the time | None | Q1 |
| W043 | Re-inspection requirement after rework WO completion | B | R5.7 | QC | S | Original NCR result no longer stays `fail` forever | W001 | Q3 |
| W044 | Stored related `x_sbk_operator_who_caused_defect_id` from `x_sbk_workorder_id.user_id` | D | R5.8 | QC,MGR | XS | Enables pareto-by-operator | W035 | Q3 |
| W045 | Quarantine inventory menu entry under Kitchen Ops | C | R5.9 | QC | XS | "All quarantined right now" 1 click vs 4-5 | None | Q1 |
| W046 | Surface `recommended_bid_id` in RFQ list view | D | R6.11 | BUY | XS | Saves form-open per RFQ during triage | None | Q2 |
| W047 | Downtime trend pivot + graph views (add to existing action) | D,A | R7.4 | MGR | S | "Downtime by reason this month vs last" no longer requires Excel export | None | Q2 |
| W048 | Daily history snapshot model (`southbrook.shop.daily`) | A,D | R7.4 | MGR,GM | M | Unlocks month-end actual-vs-plan, WoW/MoM reporting | None | Q3 |
| W049 | Shift attribution on KPIs (derive `x_sb_shift` on WO from `date_start`) | A,D | R7.5 | MGR | M | "Which shift is leading/lagging" becomes data not anecdote | W035 | Q3 |
| W050 | Procurement group on send-to-production for batch context | A | R1.8 | PM | S | 13 cabinets of one kitchen no longer look unrelated in queue | W002 | Q2 |
| W051 | Equipment alerts clickable + cross-link to impacted MOs (already exists, surface it) | D | R7.6 | MGR | XS | Underused link becomes manager-home tile | W015 | Q2 |
| W052 | MO chatter from `button_mark_done` for asbuilt failures (replace silent swallow) | F | R1.10 | PM | XS | Planner notified when asbuilt creation silently fails | None | Q1 |
| W053 | MI auto-recompute cron (5min sweep) | A,D | R7.6 | PM,MGR | S | Dashboards stop showing stale data | None | Q2 |
| W054 | Operator-visible Time Tracking on WO form (`domain=user_id`) | A | R2.12 | OP | S | Productivity-tied comp needs operator to audit own time | W035 | Q3 |
| W055 | Reactivate cabinet label QR via signed `sb://mo/<id>` (not raw MO id URL) | A | R1.12 | INSTL | S | ECO-rebuilt MO labels still point at valid records | None | Q2 |
| W056 | Floor portal auto-refresh lighter polling endpoint (replace 30s full-page) | A | R1.11 | OP | S | Server load reduction with N tablets open | None | Q3 |
| W057 | Activity feed retention cron (90-day) | A | R7.6 | OPS | XS | Prevents 10M+ row growth by year-end | None | Q1 |
| W058 | Recommended bid surfaced in RFQ list as `decoration-info` | D | R6.11 | BUY | XS | Same as W046 explicit | None | Q2 |
| W059 | MOQ shortfall warning on RFQ line | D | R6.9 | BUY | S | "MOQ 100, you ordered 20" surfaced; buyer accepts or bumps | None | Q3 |
| W060 | Dual-source award (per-bid `awarded_qty`) | E | R6.10 | BUY | M | 50/50 risk-split sourcing becomes possible | None | Q4 |
| W061 | Vendor performance smart buttons (open POs / late POs / defect rate) | D | R6.17 | BUY | M | Awarding ties via recent history vs flying blind | W003 | Q3 |
| W062 | `buyer_id` on `pg.rfq` and `pg.rfq.line` for multi-buyer orgs | A | R6.16 | BUY | S | "RFQs I own" filter works | None | Q4 |
| W063 | Manager portal kiosk template (office TV: OTD trend + risk top-3 + shift leaderboard) | I | R7.6 | MGR | M | Office TV equivalent of floor kiosk | W015, W016, W049 | Q4 |
| W064 | Mobile-friendly manager dashboard (frontend_layout route) | I | R7.6 | MGR | S | Walking-the-floor manager has phone access | W015 | Q4 |
| W065 | Drag-to-reschedule on MO list (inline `date_planned_start` edit) | C | R3.6 | PM | M | Reschedule: 4 clicks per row → drag | W005 | Q4 |
| W066 | Subcontract decision UI ("WC at 120% — divert this MO's CNC op to vendor X") | C | R3.9 | PM,BUY | L | Data plumbing exists; decision support absent | W031 | Q4 |
| W067 | Capacity pivot calendar-aware denominator | A | R3.10 | PM | S | 4-day-week WC with Monday holiday no longer shows headroom | None | Q3 |
| W068 | Daily plan-delta snapshot for end-of-week reconcile | D | R3.12 | PM | S | "What slipped this week" answerable without Excel | W048 | Q4 |
| W069 | Downtime → reschedule listener (auto-cascade affected WOs) | F | R3.14 | PM,OP | M | Planner finds out at open, not at kanban-turn-red | None | Q4 |
| W070 | Spanish translations (i18n/es.po) for operator surfaces | A | R8.9 | OP | S | High Spanish-speaking labor in cabinetry | None | Q3 |
| W071 | Trolley/cart QR — bind pre-staged kit to WO | C | R8.10 | OP | M | Material traceability when wrong-hinge slips through | W035 | Q4 |
| W072 | Public floor action mini-framework (extend POD pattern to install / temp labor) | C,B | R8.3 | INSTL | M | 1-scan + 1-tap for installer; no login wall | None | Q4 |
| W073 | Bin-scan + load-unit OWL companion UI | C | R8.8 | OP,SHIPMGR | M | JSON endpoints become operable | None | Q3 |
| W074 | Bottom-anchored status-bar on mobile (single-hand thumb reach) | A | R8.6 | OP | XS | Tablet held one-handed becomes operable | W039 | Q3 |
| W075 | `onbeforeunload` guard on in-progress dirty WO form | A | R8.12 | OP | XS | Accidental back-swipe no longer loses edits | None | Q3 |
| W076 | Vendor portal stub (`/my/rfqs` for supplier self-service) | C | R6.12 | BUY,vendor | L | Closes email-vs-portal gap; Phase 3 per CLAUDE.md | None | Q4 |
| W077 | EDI vendor part-number to `product.supplierinfo.product_code` | A | R6.13 | BUY | S | UBL `SellersItemIdentification` correct | W031 | Q4 |
| W078 | Subcontract/dropship source-type on `pg.rfq` | E | R6.14 | BUY | M | Hardware subcontract decisions stop bypassing AVL | None | Q4 |
| W079 | `southbrook.cut.spec.version` field + asbuilt snapshot | A | R4.S8 | ENG,QC | S | Two cabinets built under different cut specs are distinguishable | W008 | Q3 |
| W080 | Tab consolidation on MO form (13 → 5 lifecycle-grouped tabs) | I | R1.W2 | PM | M | Eliminates horizontal-scroll; removes invisible-but-loaded duplicate Intelligence | None | Q2 |
| W081 | `pg_is_on_superseded_rev` MO list/kanban badge | D | R4.S5 | PM | S | Planner sees "your revision is older than latest" at glance | W008 | Q3 |
| W082 | `mrp.production.create` race backfill cron for `pg_release_id` | A | R4.S1 | OPS | XS | MOs created during release window get retroactively stamped | None | Q3 |
| W083 | Align freeze state-set (PG + Southbrook both include all 4 states) | E | R4.S2 | ENG | XS | No more MOs skipped from one scan but caught by the other | W006 | Q2 |
| W084 | Settings panel for ICP toggles | C | (CLAUDE.md ref) | OPS | S | Already done in PG-125; verify and document | None | Q1 |
| W085 | Defect-sheet mobile viewport (min-width + meta) | A | R5.11 | QC | XS | In-browser preview survives mobile width | None | Q3 |
| W086 | Seed operator-tier `res.users` and `hr.employee` records | B | R2.6 | OPS,HR | M | Gate-blocks every operator-facing feature; nothing in R2 ships without users | None | Q1 |
| W087 | Structured shift-handover prompts (replace freeform Text with 4 yes/no/text) | A | R2.7 | OP,SHIFTLEAD | S | Free-text barrier is why 0 handovers exist today | None | Q2 |
| W088 | Defect-sheet menu entry under operator "Floor Tools" submenu | C | R2.13 | OP | XS | Reprint sheet after wet/torn; not buried in QC manager menu | None | Q1 |
| W089 | Confirm `x_sbk_active_for_kitchen` flag semantics on 8 of 14 workcenters | A | R2.14 | OPS | XS | Operators cannot find their WOs if filter hides their WC | None | Q1 |
| W090 | Scrap reason field + back-link `x_sbk_scrap_id` on NCR | H,E | R5.10 | QC | S | Closes cost/material reconciliation gap | W001 | Q2 |

Backlog totals: 90 items. Quick-wins (S, H impact, Q1): 13. Q1 set: 21. Q2 set: 22. Q3 set: 27. Q4 set: 20.

---

## 4. Top 10 (the ones you'd actually fund this quarter)

**1. W001 — Add `mail.thread` + `fields.Image` to `southbrook.mi.check`.**
A one-liner Python change plus a view block. Single highest-leverage
change in the entire 90-item backlog. Unblocks photo capture (the
table-stakes artefact of any quality module), enables the chatter
breadcrumb on auto-linked defects, gates the deviation-approval flow
(W012), gates the re-inspection-after-rework requirement (W043), and
restores the auto-link confirmation that operators will eventually
learn to mistrust. If only one ticket ships next sprint, it is this.
**Risk if not done:** quality module continues to ship without its
load-bearing artefact; the gap will be felt at every warranty call.

**2. W002 — Backfill MO kitchen context (cabinet_code, room, install_due, kitchen_project).**
100% of live MOs render the redesigned Path-B header bar with blank
labels. Every downstream UI improvement (tablet kanban, manager
dashboard tile, group-by-room kanban, install-due risk score) silently
assumes these fields are populated. Compute is at-create only — no
recompute storm — and a one-time backfill script handles the existing
13 MOs. Saves ~75 clicks/day per planner immediately; unlocks every
search/group/sort the planner needs. **Risk if not done:** Path-B
redesign cost was paid, redesign value remains zero.

**3. W003 — `pg_item_id` + `pg_rfq_line_id` back-refs on requisition + PO lines.**
The single change that closes the loop from engineering through
sourcing through procurement through warranty. Audit "where did this
defective lot originate?" drops from 7 clicks (brittle reference-string
match through 4 hops) to 2. Tests at
`test_procurement_edge_cases.py:264-282` actively assert their
absence — the tightening is mechanical. Unblocks vendor scorecarding,
defect-rate-per-item, supplier-on-time analysis. **Risk if not done:**
every PO that flows through the system is un-attributable for
audit/defect purposes — PG-150 §7 R4 risk should be elevated from
Med×Med to Med×High.

**4. W006 — Decide W1 (bridge implement OR retire).**
Strategic, not engineering. The bridge spec is in eLearning slides as
if live; the code doesn't exist; the asbuilt drops the PG revision
silently; impact analysis runs twice on different state sets. Either
write the 80-LoC bridge OR move `bridge_spec/SPEC.md` to
`docs/archived/`, write a D1bis amendment, and patch the slide deck.
Both are 0.5 day. The current state is the worst of all worlds.
Unblocks W007, W008, W023, W083. **Risk if not done:** operators
trained on the integration find empty fields; trust in the platform
documentation erodes.

**5. W005 — Wire scheduling engine into Southbrook PM menu (legal-hold gated).**
Six-plus OpenValue OPL-1 planning modules run on prod with zero menu
entries. The scheduling engine — finite-capacity, calendar-aware,
4-dispatching-rules — is one URL-bar-typed model-ID away from the
planner. **Engineering fix is 4 hours: one menu inherit, three
`menuitem` records.** The actual gate is the OPL-1 legal hold —
needs written authorization from hello@openvalue.cloud first. If
legal clears: this is the unlock for finite-capacity planning. If
legal blocks: build the LGPL-3 replacement (R-effort) or remove
the modules from prod. **Risk if not done:** continuing to pay legal
risk while planner cannot use the surfaces.

**6. W014 — Tablet kanban for operator (big-touch WO cards).**
Highest-leverage operator-side win. Today's find-next-WO is 5 taps
through filtered list views; with a workcenter-scoped kanban and
220×140px cards it's 1 tap. Multiplied across 60 WOs/shift, hundreds
of fewer interactions per operator per day. Unblocks every operator
KPI capture pattern (R2.2 form-open scan log, R2.4 report-a-problem,
R2.5 attachments stat button). Native v19 CE doesn't ship the tablet
shopfloor view, so the build is real, but it's a 1.5-week build, not
a sprint-multi-quarter epic. **Risk if not done:** operator role
remains theoretical; telemetry tables stay empty; everything downstream
of operator adoption stalls.

**7. W015 — Single-screen "Is today OK?" manager dashboard.**
The plant manager's morning is 5 menu hops to assemble the picture.
Promoting the Kitchen Ops POC from TransientModel to a singleton
persistent Model with tiles (plant WIP, late count, OTD% from W016,
top-3 bottlenecks, top-3 slipping MOs, equipment alerts roll-up,
activity feed) gives the manager one screen. Re-point the Southbrook
PM root menu and 7am-open lands here. Every other R7 win becomes a
tile composition. **Risk if not done:** five well-built dashboards
that the manager assembles in their head every morning.

**8. W016 — OTD % (rolling 7d / 30d / MTD) with daily snapshot history.**
Most-asked cabinetry-manager KPI; Southbrook does not measure it.
Grep returns zero hits across four target addons. New stored daily
aggregate model populated by a 1am cron snapshots prior day's MO
closure outcomes; three computed scalars surface on the R7.1 dashboard;
a graph view for trend. Unblocks CFO/GM monthly reporting, customer-
facing SLA commitments, contract-bid evidence. **Risk if not done:**
the conversation "are we improving?" stays anecdotal.

**9. W037 — Wi-Fi offline scan queue (Service Worker + IndexedDB).**
The 2026-06-22 fsync outage was 95 minutes of zero-buffer downtime
for floor operators. Service Worker that intercepts `/sb/qr/scan` and
queues to IndexedDB when offline, with background flush + idempotent
retries, addresses both the WAN-flake hit and the QNAP recovery
hazard. ~2-3 day spike for backend + 2 days for Flutter parity.
**Risk if not done:** trust in the scan workflow erodes one missed
finish per shift; next 90-min outage looks the same as the last.

**10. W035 — Operator PIN-gated scan logging (`hr_employee.pin`).**
Today every floor tablet runs under one persistent shared session;
scan log credits "shop_operator@southbrookcabinetry.local" for every
action. Forensics break. Pareto-by-operator (W044), shift leaderboards
(W049), and any compensation-tied productivity calculation depend on
per-operator attribution. ~M effort; PIN cookie pattern is well-
established. **Risk if not done:** can't answer "which operator
caused this defect cluster"; can't ship productivity-tied compensation.

---

## 5. By-Quarter Sequencing (12 months)

### Q1 (next 90 days) — Data behind the form, decisions, traceability foundation

**Theme.** Stop the doc-vs-reality drift; backfill the empty UI; close
the warranty trace; restore basic data hygiene. Every Q2/Q3 UX win
depends on these.

**Items:** W001, W002, W003, W004, W005 (legal-gated start), W006,
W007, W008, W009, W010, W011, W012, W013, W042, W045, W052, W057,
W084 (verify), W086, W088, W089.

**Collective theme.** "Make the fields, audit trails, and decisions
real before adding new UI." Eight of these are sub-day Q1 items; W001
plus W002 plus W003 alone reshape the platform's trustworthiness.

**Why this order.** W001 (mail.thread) unblocks W012 + W043. W006
(bridge decision) unblocks W007 + W008 + W023. W086 (seed operator
users) is the gate everything R2-side depends on. W003 (back-refs)
is the schema change that closes Q3's vendor performance + Q4's
warranty trace.

### Q2 (months 4-6) — Synthesis surfaces + the operator tablet

**Theme.** Now that data flows, build the fused dashboards (manager,
planner, operator) and the per-quarter trend reporting. Lay in the
scan-to-act primitives.

**Items:** W014, W015, W016, W017, W018, W019, W020, W021, W022,
W023, W024, W025 (depends on PLM), W026, W046, W047, W050, W051,
W053, W055, W058, W080, W083, W087, W090.

**Collective theme.** "Stop fragmenting; one screen per persona."
Manager home (W015) + OTD (W016) + risk scan (W020) is the manager
fix. Planner home (W019) + scheduling-engine surfacing (continued
from W005) is the planner fix. Tablet kanban (W014) + traveler-QR-
actions (W017) + Report-a-Problem (deferred to Q3 W040) is the
operator fix.

**Dependency note.** W015 → W016 → W020 chain because risk-rank needs
OTD-history to weight. W019 needs W010's split queues. W023 needs
W006's bridge decision to land first.

### Q3 (months 7-9) — Closing the loop: quality discipline, reverse paths, mobile hardening

**Theme.** Discipline pass — every dangling state machine, every silent
swallow, every shared-session pretense gets fixed. Mobile becomes
glove-grade.

**Items:** W027 (legal-gated), W028, W029, W030, W031, W032, W033,
W034, W035, W036, W037, W038, W039, W040, W041, W043, W044, W048,
W049, W054, W056, W059, W061, W067, W070, W071, W073, W074, W075,
W079, W081, W082, W085, W090.

**Collective theme.** "Make the floor reality match the architecture."
PIN gate (W035) + per-operator pareto (W044) + shift attribution (W049)
+ engineering raise-ECO-from-WO (W034) close R2 + R5 + R7 in one
sprint band. Offline queue (W037) + audio confirmations (W036) +
dark mode (W038) + density bundle (W039) + bottom-anchored status
bars (W074) make the operator tablet finally physical.

**Dependency note.** W044 needs W035 (employee_id on scan log). W049
needs W035 (employee attribution on WO). W031 unblocks W077. W027
needs W005's legal clearance and W083's state-set alignment.

### Q4 (months 10-12) — Scale: vendor portal, EDI, cross-project, AI-ready

**Theme.** Things that compound at scale — supplier self-service,
multi-buyer surfaces, machine-reachable trend data, drag-to-reschedule,
subcontract decision UIs.

**Items:** W060, W062, W063, W064, W065, W066, W068, W069, W072, W076,
W077, W078.

**Collective theme.** "Multi-tenant, multi-buyer, multi-customer
scalability." The vendor portal (W076) closes the email-vs-portal gap.
EDI (W077) closes the UBL identification gap. Drag-to-reschedule
(W065) and subcontract UI (W066) become the planner's day-of reactive
toolkit. Public floor action framework (W072) unlocks installer +
temp-labor flows without login walls.

**Dependency note.** W076 (vendor portal) is Phase 3 per CLAUDE.md
§R9 — needs strategic decision before Q4 start. W065 needs W005's
scheduling engine to absorb the reschedule signal.

---

## 6. Don't-Do List

**DD1 — Do not rebuild floor flows in Flutter.** Several agents (R2,
R8) flagged the temptation. R8 documents the
`[[southbrook_app_safari_blank_white]]` Cloudflare edge minification
trap that ate the Flutter bundle and remains unfixed. Floor screens
cannot live on infrastructure with this risk class; Flutter is
customer-side, Odoo + public HTML mini-pages are operator-side. Keep
the line clean. Reject any "let's port the operator tablet to Flutter"
proposal.

**DD2 — Do not delete the OPL-1 modules without first closing the
legal question.** R3 flags they're installed but unsurfaced. Removing
them without first clearing the licence question may itself be a
contract event; the modules' side-effects (WO `date_planned_start_wo`
rewriting) are already in production data. Either obtain written
authorization OR formally archive the modules with their effects
preserved — never silently uninstall.

**DD3 — Do not chase a custom OWL spreadsheet widget for BOM editing
in Phase 1.** Decision 4 in CLAUDE.md (D4) is explicit: native Odoo
x2many + CSV import/export + keyboard-friendly inline edit. R6 did
not call for this; we mention it because new contributors will
intuitively want it. The cost-benefit on the cabinetry catalog volume
does not justify it before real users ask.

**DD4 — Do not implement R6.10 (dual-source split award) before R3
and W031 (`product.supplierinfo` mirror).** The award flow needs the
upstream PG↔Odoo native-AML reconciliation to be sound before
multi-vendor splits hit the requisition. Premature ordering of W060
will create requisitions whose downstream PO flow depends on AML
records that aren't current.

**DD5 — Do not adopt R8.5's auto-brightness ambient sensor sub-item.**
Most shop TVs don't expose the `AmbientLightSensor` API; this is
investigation for investigation's sake. Keep R8.5 to manual dark
mode and OS-pref follow; defer ambient sensing to "if a TV vendor
ever exposes it."

**DD6 — Do not consolidate `southbrook.eco` into `pg.eco` in Q1.**
R4 surfaces the parallel-systems problem but the right answer is the
bridge (W006a) plus disjoint-domains declaration (D1bis). A migration
that retires `southbrook.eco` is a Q3-or-later project with significant
test surface; do not let it leak into the Q1 bridge-decision ticket.

---

## 7. What's GOOD Across the Platform

Don't lose sight of these — protect, don't refactor:

- **HMAC-signed `sb://` QR architecture (R2, R5, R8).** Honest crypto,
  per-deployment secret rotation, TTL-bound expiry per kind, scan log
  with IP/UA. No other Odoo shop floor in the industry ships this
  hardened. The POD page (`auth='public'` because HMAC is the identity)
  is the canonical pattern for any future installer/temp-labor screen.

- **Release boundary discipline (R4, R6, CLAUDE.md R1).** `mrp.bom`
  is written only inside `pg.release.action_execute_release()`.
  Savepoint discipline, provenance fields, MO-create stamp, frozen-MO
  cron, idempotency by (ebom, revision). This is genuinely
  non-negotiable in the code, not just the docs. Every Q1-Q4 ticket
  is R1-clean.

- **MI engine sophistication (R1, R7).** AbstractModel, per-MO and
  per-package recompute paths, category-organized checks, P3
  auto-remediation hooks (default off pending validation). The MO-form
  Intelligence tab delivers on its per-MO promise; the gap is that
  its results never roll up to a manager view (closing this is W015).

- **State-machine discipline + audit-log everywhere (R4, R6).** Every
  `action_*` writes `pg.audit.log` + chatter + tracked field. Awarded-
  RFQ immutability + `_COMPUTE_ONLY_FIELDS` allowlist is subtle and
  load-bearing. Cut-spec activation pattern (one-active-singleton via
  constraint, supersession via state flip, audit via chatter,
  idempotent re-activate) is portable across the platform.

- **Kanban + decoration patterns (R1, R7).** The MO kanban redesign
  at `southbrook_mrp_pm/views/mrp_production_kanban.xml` (state-as-
  pipeline columns, deadline-aware via `widget="remaining_days"`,
  OWL-tokenizer-safe expressions per the well-documented hard-won
  lessons in the comments) is the right primitive. Extend it; don't
  rebuild it. Same for the workcenter-kanban inheritance pattern.

- **Defect-type QR sheet (R2, R5, R8).** Operator pins 14 paper QRs
  on a board, scans `defect + cabinet/WO`, NCR auto-drafts. Pure
  paper-physical hybrid, exactly the right altitude. This is the
  shop-floor pattern of the decade.

- **CSA A277 / warranty trace from asbuilt (R4, R5).** As-builts
  auto-create on MO done, BoM version snapshotted, immutable seal.
  Once W008 lands (PG back-refs), this is the customer-warranty path
  3 years out — one query, one answer.

- **OdooIQ + Hermes adjacent infrastructure (cited memory).** PG
  bearer auth, FreeCAD bridge, Cloudflared tunnel, the Forgejo
  Actions runner, the QNAP backup cron, the OdooIQ Deck. None of
  this was in the R1-R8 scope but it's the foundation everything
  else runs on. Don't churn the infrastructure during the Q1-Q4
  application sprints.

---

## 8. Decisions That Need Leadership Input (not engineering)

These are strategy questions; engineers cannot resolve them in a
sprint ticket.

**D-L1 — OPL-1 legal.** The OpenValue OPL-1 hold (per
`OPL1_LEGAL_HOLD.md`) blocks formal use of `mrp_sfc_scheduling_engine`,
`mrp_sfc_availability_check`, `mrp_shop_floor_control`, and the four
`openvalue_mrp_planning_*` modules. They are installed live; their
side-effects are in the production data. Three paths: (a) obtain
written authorization from hello@openvalue.cloud and unblock W005 +
W027 + W028; (b) formally retire and replace with LGPL-3 Southbrook
code (L effort, duplicates real work); (c) accept the status quo
and document the legal posture. **Decision needed before Q1 close.**

**D-L2 — Bridge vs retire (W006).** Two complete eng→mfg loops run
side by side. Do we (a) build `product_graph_southbrook` bridge (80
LoC, unifies governance), (b) formally retire `bridge_spec/SPEC.md`
and declare `southbrook.eco` + `pg.eco` as disjoint domains (D1bis),
or (c) migrate `southbrook.eco` into `pg.eco` over Q3 (large effort,
opens test surface)? The current ambiguity is unsustainable per Theme
B. **Decision needed Q1.**

**D-L3 — Operator org chart and role model.** R2 found zero operator-
tier users; R7 found zero shift handovers and zero downtime logs. Is
the cabinet shop committing to (a) per-operator named Odoo users with
PIN scans (W035), (b) per-shift named users with operator attribution
via PIN, or (c) shared session under one user (today's reality, which
breaks forensics)? Comp model is downstream of this decision (W054).
**Decision needed Q1.**

**D-L4 — Document control authority.** R4 W5 (operator raise-ECO-from-
WO) requires deciding whether ECOs from the floor route to
`southbrook.eco` (cabinet-specific), `pg.eco` (generic), or both. The
two ECO systems' coexistence (D-L2) is the upstream decision. Pair
with D-L2.

**D-L5 — Service-state items in auto-RFQ.** R6.18 surfaces that the
auto-RFQ cron's `state IN ('engineering', 'released')` filter excludes
items in `service` state (still purchasable as service parts).
Engineering call: should service-state items be sourceable? One-line
fix either way; needs business answer.

**D-L6 — Manager dashboard for office TV (W063).** This needs a
business decision: does Southbrook want a wall-mounted OTD trend +
risk top-3 + leaderboard in the office? Q4 ticket assumes yes; if
no, drop and reclaim 2 dev-weeks.

**D-L7 — Vendor portal (W076 / R6.12).** Phase 3 per CLAUDE.md.
Strategic question about supplier self-service capacity and the
commercial relationship with vendors. Q4+ decision; do not invest
engineering until business decides.

**D-L8 — Spanish translation scope (W070).** R8.9 calls for `es.po`
for operator surfaces. Business decides: which surfaces, in what
priority order, and is there a bilingual cabinet-maker available to
review domain vocab? "edge banding" is not "encolado de cantos."

---

## 9. Open Risks Beyond This Review

Things the 8 reviews did NOT cover that warrant their own focused
audits.

**OR1 — MES integration.** The R8 review noted native Odoo 19 ships a
`mrp_workorder` shopfloor view that none of the Southbrook addons
extend or hide. Worth a focused review: do we adopt and customize
native MES, build the operator tablet (W014) on top of it, or build
parallel and accept the duplication? Same question for native MRP
mobile.

**OR2 — Costing accuracy.** R1 supplementary noted MO cost variance
is computed live and never acted on; thresholds (>10 warning, >25
danger) are visual only with no escalation. A focused R-stream on
the cost-accounting flow — from MO to as-built to the General Ledger
— could surface significant ROI. Cost variance to ECO threshold
(`>25% = ECO` per docstring) is also unwired.

**OR3 — Payroll bridge / labor productivity to compensation.** R2
flagged `x_sbk_units_per_op_hour` as the right KPI. Going productivity-
tied-comp requires (a) per-operator attribution (W035), (b) labor-rate
table per workcenter (`x_sbk_kitchen_rate_per_hour` exists), (c) HR
integration. Out of scope of all 8 reviews; multi-quarter program if
business decides.

**OR4 — FreeCAD render reliability.** Cited memory notes the bridge
is live but the render pipeline is single-point-of-failure. R4
referenced `southbrook_freecad_bridge` as a one-way feed; an audit
of failure modes (FCStd corruption, render hang, file-system fill)
would close a long-tail customer-facing risk. The Kitchen Ops POC
already monitors `freecad_bridge_status`; promote to a real SLO.

**OR5 — Customer-facing app reliability.** `[[southbrook_app_safari_blank_white]]`
is still open. A dedicated review of the Flutter `/app/` shipping
flow (deploys, CF caching, asset-rewrite traps) is overdue —
postponed by user 2026-06-25, but as customer-app traffic grows the
deferral cost rises.

**OR6 — Sale-order to MO origination paths.** R1 found 3 of 5 source
SOs bypass `production_approval_state`. The other paths — manual MO
creation, OCA `product_configurator_mrp` post-confirm hooks, sale_mrp
procurement rules — need an inventory + decision pass. W009 closes
the constraint side; the origination paths themselves are a separate
audit.

**OR7 — Estimating + Configurator + Storefront integration.** The
configurator UX paused 2026-06-22 (cited memory) is parked. Several
R-streams touch the upstream of MO creation (SO line zone, commitment
date, etc.) and would benefit from coordinated resumption.

**OR8 — Multi-site / multi-warehouse.** All 8 reviews assume single-
plant. If Southbrook scales to a second location (Q4+ business plan),
many of the kanbans, dashboards, and KPI computes need `warehouse_id`
filtering that doesn't exist today. Worth scoping pre-emptively.

**OR9 — Disaster recovery + backup-restore validation.** Cited memory
covers the backup strategy and the QNAP fsync hazard. A real
restore-to-staging dry run is not on the cited cadence. Mfg-systems
review-adjacent: when the recovery scenarios fire, can we get back
to "last good ECO" in under 4 hours?

**OR10 — Onboarding + training for new operators.** The eLearning
slides are out of date (R4); the operator-tier user list is empty
(R2); the documentation-vs-reality gap (Theme B) silently teaches
operators wrong things. A training-content audit pairs naturally with
the W001/W002/W006 truth-restoration sprint.

---

## Closing read

The Southbrook platform is — in its bones — a high-quality industrial
Odoo deployment. The signal density per square inch of code (the QR
architecture, the MI engine, the cut-spec governance, the release
boundary discipline, the closure-rebuild advisory locks) is genuinely
high. What's missing is **load** — operator users, scan rows, downtime
entries, populated context fields, and the dashboards that aggregate
the engine outputs into manager-facing decisions.

The right next quarter is not "build more"; it is "wire up what's
built." Once the data flows and the documented promises are kept,
every Q2-Q4 win in the backlog compounds. Without that foundation
pass, the platform continues to be a beautiful exoskeleton around
13 production MOs that nobody touches the right way.

90 wins across 8 lenses. 21 in Q1. 5 of those are sub-day. Start
with W001.

*Generated 2026-06-27 by R9 master-synthesis agent.
Cross-references: R1-R8 source documents in `/Users/naadmin/product_graph_v19/docs/`.*
