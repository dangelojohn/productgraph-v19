# R3 — Planning & Scheduling — Review

**Reviewer:** Senior manufacturing systems consultant
**Slice:** R3 — Planner JTBD only (capacity / sequencing / WC loading / bottlenecks / reschedule / project↔MO traceability)
**Stack under review:** Southbrook Cabinetry Odoo 19 CE prod (live), R3-relevant addons in `/Users/naadmin/southbrook-v19cr/addons/` + OpenValue OPL-1 engines installed live but not declared in any in-repo manifest dependency.
**Date:** 2026-06-27
**Live-DB probe:** **NOT obtained** — SSH to `naadmin@ssh.odooiq.com` rejected (Permission denied, publickey/password/keyboard-interactive). All claims are code-grounded; the three SQL probes from the brief are deferred to a follow-up with valid creds. The brief allows this and the gaps below are NOT contingent on the probe.

---

## TL;DR — the planner's reality today

The planner has **no Gantt, no drag-to-reschedule, no rush-MO what-if, no auto-reflow on material slip, and no cross-project conflict view**. What ships today is **list/kanban/pivot only** (`addons/southbrook_mrp_pm/views/pm_menus.xml:114-125`, `pm_capacity.xml:32-60`, `pm_dashboard.xml:13-94`).

A finite-capacity scheduling engine (`mrp_sfc_scheduling_engine`, OPL-1) is **installed live but uncited** — no Southbrook addon `depends` on it, no menu surfaces it, no view exposes `action_open_scheduling_engine`. **Planners cannot run it without typing the model ID into the URL bar.** It is also under a legal hold (`addons/southbrook_premium_orchestration/docs/OPL1_LEGAL_HOLD.md:17-43`) — same status applies to `mrp_sfc_availability_check`, the only material-availability-as-a-feature on the box.

The richest planning signal already lives in the codebase (`x_sbk_congestion_level` on workcenter — alert/warn/clear; `southbrook_not_scheduled`, `southbrook_can_start_today` + `southbrook_start_blocker` on workorder; `current_bottleneck_workcenter_id` on project.task) — but is not summarized in any planner-facing screen.

---

## Top 5 Wins (rank-ordered)

### Win 1 — Surface the OpenValue Scheduling Engine action on every MO list + add a Planning menu

**JTBD:** "When 47 MOs land in the queue Monday morning, I want one click to level-load every WC across the week (respecting working calendars + finite capacity), so I'm not sequencing on a whiteboard."

**Current click count to reach the engine:** Infinite — there is no UI entry point at all. The engine model exists (`/Users/naadmin/Downloads/openvalue/mrp_sfc_scheduling_engine/models/mrp_scheduling_run.py:14-256`), the `action_open_scheduling_engine` method exists on `mrp.production` (`models/mrp_production.py:9-21`), the list/form views exist (`views/mrp_scheduling_run_views.xml`). **None of it is wired into the Southbrook PM menu tree** (`addons/southbrook_mrp_pm/views/pm_menus.xml:1-143`).

**Target click count:** 2 clicks (MO list → multi-select → Action menu → Optimise Schedule).

**Effort:** S (4 hours). A single inheriting view to add the action menu item on `mrp.production` list, plus three `menuitem` records under `menu_southbrook_pm_root` for the run history + a "New Optimisation" launcher.

**Impact:** Unblocks finite-capacity sequencing on day one. Today's `pm_capacity.xml:36-40` pivot shows committed minutes vs the 480-min/day shift but offers no action button to fix overloads.

**Files:**
- `/Users/naadmin/southbrook-v19cr/addons/southbrook_mrp_pm/views/pm_menus.xml:114` — add Scheduling menu next to Floor Load.
- `/Users/naadmin/southbrook-v19cr/addons/southbrook_mrp_pm/__manifest__.py:43-50` — declare `mrp_sfc_scheduling_engine` as a soft (try/except install hook or hard-dep behind a feature flag) once legal hold clears.
- `/Users/naadmin/Downloads/openvalue/mrp_sfc_scheduling_engine/models/mrp_production.py:9-21` — `action_open_scheduling_engine` is ready to bind.

**Risk:** **HIGH legal — OPL-1 LEGAL HOLD.** `OPL1_LEGAL_HOLD.md:25` lists this exact module; `:55-78` requires written authorisation BEFORE further wiring. Do not ship until `hello@openvalue.cloud` has confirmed deployment terms. The engine is technically installed and the planner is already using its side-effects (it has rewritten WO `date_planned_start_wo` / `_finished_wo` per `mrp_scheduling_run.py:186-190`), but the codebase pretends it isn't there. Surfacing it formalises a dependency the legal hold says we may not have the right to formalise. **Engineering fix is the easy part; legal sign-off is the gate.**

---

### Win 2 — A "Today's Plan" planner-home view that fuses ready queue + WC load + start-blockers in one screen

**JTBD:** "When I sit down at 7am, I want one screen that tells me (a) what should run today, (b) at which station, (c) what's blocking each one — so I'm not opening 5 menus and reconciling them in my head."

**Current click count for the morning walk:** **5+ navigation hops**, each in its own action: Ready Queue (`pm_menus.xml:40-62`) → In Production (`:67-78`) → Late Orders (`:82-109`) → Floor Load kanban (`:114-125`) → Capacity pivot (`pm_capacity.xml:66-87`). The PM Dashboard (`pm_dashboard.xml:13-94`) gives per-WC tiles but no queue rollup.

**Target click count:** 1 click → 1 scroll. Single OWL component or a custom kanban grouped by `mrp.workorder.workcenter_id` showing today's WOs, each with the existing `southbrook_can_start_today` badge + `southbrook_start_blocker` reason (`southbrook_project_mrp/models/mrp_workorder.py:12-21,53-94`).

**Effort:** M (1-2 days). The data is already computed. A kanban over `mrp.workorder` filtered to `date_start <= today` + group_by `workcenter_id` + the existing badge fields covers 80% of the JTBD with zero new fields. **Bonus:** the `x_sbk_congestion_level` workcenter badge (`southbrook_mrp_kitchen_workcenters/models/mrp_workcenter.py:144-184`) is already store=True with thresholds in `ir.config_parameter` — surface it in the column header.

**Impact:** Replaces the 5-menu daily walk with a single morning ritual. Every signal needed is already on the model graph.

**Files:**
- New: `addons/southbrook_mrp_pm/views/pm_today.xml` (kanban + action + menu under `menu_southbrook_pm_root` at sequence 4 — above Dashboard).
- `addons/southbrook_project_mrp/models/mrp_workorder.py:12-94` — provides `southbrook_can_start_today`/`southbrook_start_blocker` (note: cross-addon dep — `southbrook_mrp_pm` would need a soft dep on `southbrook_project_mrp` or the field moved up).
- `addons/southbrook_mrp_kitchen_workcenters/models/mrp_workcenter.py:144-184` — `x_sbk_congestion_level` badge.

**Risk:** Low. `southbrook_can_start_today` is a non-stored compute (`mrp_workorder.py:46-51`) that walks `maintenance.request` per WO — at 47 MOs × 6 WOs each = ~280 WOs, that's 280 maintenance searches per kanban render. **Add a search index + memoize or store the field** before grouping by it. Without that, the morning walk becomes a 4-second page load.

---

### Win 3 — Auto-reflow on material slip (PO date change cascades to dependent MO start dates)

**JTBD:** "When the PO for HDF panels slips from Wed to Fri, I want every MO that consumes those panels to push its start to Fri morning automatically and re-cascade downstream, so I'm not hand-moving 15 rows or learning about the slip when an operator finds an empty bin."

**Current click count:** N/A — no such automation exists. The `mrp_sfc_availability_check` module computes shortages on a **single MO** at planner request (`/Users/naadmin/Downloads/openvalue/mrp_sfc_availability_check/models/mrp_production.py:122-138`) and surfaces a per-line `mrp.availability.line` shortage report. There is **no listener on `purchase.order.line.date_planned`** anywhere in the in-scope addons (`grep -rn "date_planned" addons/southbrook_mrp_pm addons/southbrook_mrp_kitchen_workcenters addons/southbrook_project_mrp addons/southbrook_manufacturing_intelligence` returns zero PO-side hits). The planner finds out via the operator.

**Target click count:** 0 — fully automatic; a 1-click "Run availability sweep across the next 14 days" remains a backup (`mrp_mps_report.py:21-38` already does this batch).

**Effort:** L (3-5 days). Needs (a) a `@api.depends`/computed listener on `purchase.order.line.date_planned`, (b) cascade rules to identify dependent MOs via `stock.move` reservations, (c) integration with the `mrp_sfc_scheduling_engine` to re-optimise the affected subset, (d) bus notification to the planner. **Strongly recommended to gate behind a "Suggest" mode first** — auto-write to MO `date_planned_start` is a high-blast-radius operation if the cascade has a bug.

**Impact:** Highest planner-time savings of any single feature. Material slips are the #1 source of after-the-fact re-planning in cabinet shops.

**Files:**
- New: `addons/southbrook_mrp_pm/models/purchase_order_line.py` (the listener).
- New: `addons/southbrook_mrp_pm/models/mrp_production.py` (the cascade resolver).
- `/Users/naadmin/Downloads/openvalue/mrp_sfc_availability_check/wizards/mrp_mps_report.py:21-38` — wire the existing MPS batch as the "full sweep" fallback.
- `/Users/naadmin/Downloads/openvalue/mrp_sfc_scheduling_engine/models/mrp_scheduling_run.py:86-172` — `action_run` accepts a `production_ids` scope; pass the affected subset.

**Risk:** **HIGH.** (a) OPL-1 legal hold blocks formal use of both OpenValue modules. (b) Auto-mutating `date_planned_start` on a confirmed MO can re-trigger procurement and create cascading PO churn — must be Suggest-only at v1. (c) The bottleneck signal `x_mi_bottleneck_workcenter_id` is reserved on the model (`addons/southbrook_manufacturing_intelligence/models/mrp_production.py:26-28`) but **never written** (`views/mrp_production_views.xml:22-26` confirms it's hidden because nothing populates it) — a cascade that respects bottleneck slack needs that field actually computed first.

---

### Win 4 — One-click "What-if: drop in this rush MO" capacity preview

**JTBD:** "When a customer asks 'can you fit this rush kitchen by Friday?', I want a one-click preview that tells me (a) yes/no, (b) which existing MOs slip, (c) by how many days, (d) what the over-commit cost is — so I don't say yes and discover the cost later."

**Current click count:** N/A — no such preview. The planner today eyeballs `pm_capacity.xml` pivot (per-day minutes per WC) against the 480-min shift, then guesses. The pivot isn't even MO-aware — it shows total committed minutes without identifying which orders would slip.

**Target click count:** 3 clicks — New MO draft → "Capacity Check" button → preview modal with displaced-MO list + slip-days column.

**Effort:** M (2-3 days). `mrp_sfc_availability_check`'s `_check_capacity_availability` (`/Users/naadmin/Downloads/openvalue/mrp_sfc_availability_check/models/mrp_production.py:75-119`) already computes per-WC available hours in the order window — extend it to also identify the marginal MOs that would be displaced (sort planned WOs by `date_planned_start_wo`, pop from the end until shortage clears).

**Impact:** This is the sales-to-planner interface that's currently a phone call. Wins back one sales meeting per week per planner.

**Files:**
- New: `addons/southbrook_mrp_pm/wizards/rush_mo_what_if.py` + `views/`.
- `/Users/naadmin/Downloads/openvalue/mrp_sfc_availability_check/models/mrp_production.py:75-119` — `_check_capacity_availability` is the base; needs an inverse "what would I displace" query.
- `addons/southbrook_mrp_pm/views/pm_menus.xml:32-35` — surface under "Rush MO Check" menu.

**Risk:** OPL-1 hold (same). The fallback (build a `mrp.workcenter.load` aggregator from scratch in LGPL-3 Southbrook code) is L-effort and duplicates OpenValue work.

---

### Win 5 — Cross-project conflict view: "which customers compete for the same WC this week"

**JTBD:** "When kitchen for customer A and kitchen for customer B both hit Edgebanding on Thursday, I want a single view that tells me whose install date is at risk if I prioritise the other — so I'm not making the trade-off blind."

**Current click count:** **6+ clicks** — open task A on `project.task`, read `workcenter_load_summary` (`addons/southbrook_project_mrp/models/project_task.py:572-585`), open task B, read its summary, mentally diff. There is no list view that surfaces `current_bottleneck_workcenter_id` (`project_task.py:259-264`) grouped by WC — and no JOIN of multiple tasks on a shared station.

**Target click count:** 1 click → grouped kanban or pivot at `project.task` grouped by `current_bottleneck_workcenter_id` with `install_due_date` (`project_task.py:186-190`) + `risk_level` (`:213-223`) + `customer_id` (`:172-179`) on the card.

**Effort:** S (1 day). All the fields exist and are computed. Need an action + grouped view. The `_compute_phase1_operational_context` (`project_task.py:824-847`) already populates `current_bottleneck_workcenter_id` and `risk_level` from `_southbrook_current_bottleneck_workcenter` (`:862-873` — picks the WC with the most booked minutes per task).

**Impact:** Closes one of the largest planner blind-spots: cross-job contention. Today the bottleneck is computed **per-task** but never aggregated **per-station**, so the planner can see "task A's bottleneck is EB01" but not "5 tasks all bottleneck on EB01 this week."

**Files:**
- New action/view in `addons/southbrook_project_mrp/views/project_task_views.xml`.
- `addons/southbrook_project_mrp/models/project_task.py:259-264, 824-847, 862-873` — fields ready.

**Risk:** Low. The `_southbrook_current_bottleneck_workcenter` heuristic (`:862-873`) picks the highest-total-minutes WC across the task's WOs — this is "the heaviest" not "the most constrained" (Theory of Constraints proper). Surface the heuristic name on hover so planners understand what they're sorting by. The `x_sbk_is_bottleneck` config flag (`southbrook_mrp_kitchen_workcenters/models/mrp_workcenter.py:123-131`) is a separate concept; the view should let the planner toggle between "live bottleneck" and "ToC-configured bottleneck."

---

## R3.6+ — Supplementary findings (file:line, no narrative bloat)

### R3.6 — Drag-to-reschedule does not exist
`grep -rn "gantt" addons/southbrook_mrp_pm addons/southbrook_mrp_kitchen_workcenters addons/southbrook_project_mrp addons/southbrook_manufacturing_intelligence` returns **zero hits**. Drag-on-kanban is enabled (`southbrook_mrp_pm/views/mrp_production_kanban.xml:45-49` — `records_draggable=1`) **but moves cards between states, not dates** — the MRP state machine enforces invalid drops. There is no list-inline-edit on `mrp.production.date_planned_start` either. **Reschedule = open form → edit date → save** (4 clicks per row).

### R3.7 — The `x_sbk_pending_wo_count` recompute is wired correctly but invisible to planners
`addons/southbrook_mrp_kitchen_workcenters/models/mrp_workcenter.py:136-155, 157-184` — store=True, computes on every WO `state` change, thresholds tunable via `ir.config_parameter`. **Excellent foundation**. But the only surface is the workcenter list/search (`views/mrp_workcenter_views.xml:65-99`); no count surfaces on the PM dashboard (`pm_dashboard.xml:13-94`) and no alert fires when `congestion_level == 'alert'`. **Win:** Add a banner on the PM Today screen when any active WC trips alert.

### R3.8 — `southbrook_not_scheduled` count surfaces on tasks but not as a planner queue
`addons/southbrook_project_mrp/models/mrp_workorder.py:8-26` defines the field. `models/project_task.py:594-602, 1169-1186` aggregates `unscheduled_workorder_count` per task. **There is no list-view of all unscheduled WOs across all jobs** — that's the basic "what do I need to plan next" queue. Take 1 hour: add an action on `mrp.workorder` with `domain=[('southbrook_not_scheduled','=',True),('state','!=','done')]` + a menu under Southbrook PM root.

### R3.9 — Subcontract decision: data exists, decision point doesn't
`addons/southbrook_mrp_kitchen_workcenters/models/southbrook_kitchen_material.py:41-44` flags materials as `is_subcontract_default`; `models/mrp_workcenter.py:38` lists "Subcontract / Vendor Routing" as a station type. **There is no UI that says "WC CNC1 is at 120% — divert this MO's CNC op to subcontractor X."** The data plumbing is there; the decision support isn't.

### R3.10 — Capacity pivot is detached from working calendar
`pm_capacity.xml:36-40` sums `duration_expected` per day but does NOT subtract `resource_calendar_id` non-working hours. A WC with a 4-day week and a holiday Monday still shows headroom on Monday. `mrp_sfc_scheduling_engine` handles this correctly (`mrp_scheduling_run.py:154-160` uses `calendar.plan_hours`), but the planner-facing pivot doesn't.

### R3.11 — Reservation-state-to-deadline race not surfaced
`project_task.py:746-765` flags a task at-risk when an MO is `confirmed`/`progress` AND `reservation_state != 'assigned'` — correct. But the planner-facing PM Ready Queue (`pm_menus.xml:40-62`) sorts by deadline, not by `reservation_state`. Two MOs with the same Tuesday deadline — one fully reserved, one missing parts — render identically in the queue.

### R3.12 — No "what changed since I left yesterday" delta
No daily snapshot table; no `mail.activity` summary for plan changes. End-of-week reconcile JTBD ("what slipped this week?") is unanswerable without diffing the database manually. `mrp_scheduling_run.line_ids` (`mrp_scheduling_run.py:258-279`) snapshots per run but is keyed on run, not on day — a daily snapshot cron would close this for free.

### R3.13 — `kitchen_priority` and Odoo `priority` are parallel, no precedence rule
`southbrook_mrp_kitchen_workcenters/models/mrp_production.py:68-76` adds `x_sbk_priority_level` "distinct from upstream priority" — but no view consolidates them and no scheduling logic prefers one over the other. Planners will have two priority levers that disagree.

### R3.14 — Downtime → reschedule is operator-led, not planner-led
`addons/southbrook_mrp_kitchen_workcenters/models/southbrook_kitchen_workcenter_downtime.py:259-266` lets an operator open/close a downtime. There is no listener that **reschedules every downstream WO on the affected workcenter** when a downtime is opened. Planner finds out when the kanban turns red.

### R3.15 — Project↔MO traceability is excellent on the read side, weak on the write side
`project_task.py:101-104, 679-765, 949-972` is a thoroughgoing read-only rollup (cost, MOs, WOs, components, risk reason, install date). **No planner action originates from the project surface** — no "schedule the unscheduled WOs on this job" button; no "rush this job" pipeline transition that touches the scheduler. The customer-job view is a beautiful dashboard the planner cannot act from.

---

## What's good (don't break it)

1. **`mrp_workcenter._compute_x_sbk_pending_load`** (`southbrook_mrp_kitchen_workcenters/models/mrp_workcenter.py:157-184`) — store=True, depends on `order_ids.state` (correctly identified the v19 trap that broke the registry — see noted 2026-06-26), thresholds in ir.config_parameter. Textbook bottleneck signal.

2. **`southbrook_start_blocker`** (`southbrook_project_mrp/models/mrp_workorder.py:53-94`) — string-coded blocker reason with explicit precedence (done/cancel → waiting → unscheduled → future → components → crew → equipment). Exactly the right shape for a planner badge.

3. **`_southbrook_cabinet_family_progress`** (`southbrook_project_mrp/models/project_task.py:901-947`) — per-family qty / MO count / WO completion / current station / blocker / late flag. Best-in-class read-side rollup; nothing else in the codebase is this dense per row.

4. **`PM Capacity pivot + graph`** (`southbrook_mrp_pm/views/pm_capacity.xml:32-87`) — minutes-per-day-per-WC against the 480-min shift is the right at-a-glance shape for capacity awareness. Just needs calendar-aware denominators.

5. **`mrp_scheduling_run.action_run` heuristic** (`/Users/naadmin/Downloads/openvalue/mrp_sfc_scheduling_engine/models/mrp_scheduling_run.py:86-172`) — list scheduling with 4 dispatching rules (EDD/SPT/LPT/FIFO), calendar-aware, precedence-aware via `sfc_sequence`, with proper guard against cycles. This is a credible v1 scheduler. The shame is no one can reach it from a menu.

---

## Baseline tables

### Planner navigation baseline (clicks to JTBD)

| Phase | JTBD | Clicks today | Where the friction is |
|---|---|---|---|
| 1. Morning planning | See today's plan with risks | 5+ | 5 separate menus; no fused view (`pm_menus.xml:40-141`) |
| 2. Capacity check | "Is WC X overloaded?" | 2 | Pivot is right but calendar-blind (`pm_capacity.xml:36-40`) |
| 3. Sequencing | Drag-to-reschedule | ∞ | No Gantt; no inline date edit; form-only (`grep gantt → 0`) |
| 4. Material availability | "What's blocked + when does material land?" | ∞ | Engine exists but unwired (`mrp_sfc_availability_check`) |
| 5. Slack / buffer | "Realistic earliest-finish?" | ∞ | No surface |
| 6. What-if rush MO | "What slips if I add this?" | ∞ | No surface |
| 7. Cross-project view | "Whose finish slips first?" | 6+ | Per-task bottleneck computed; never aggregated |
| 8. Subcontract decision | "Divert to subcontractor X?" | ∞ | Material flag exists; no decision UI |
| 9. Day-of reactive | "Operator paused — reshuffle" | ∞ | Downtime logs; no reschedule listener |
| 10. End-of-week reconcile | "What slipped + why?" | ∞ | No snapshot table; no diff |

### Information density per current planner screen

| Screen | Records / row | Fields / row | Aggregations | Calendar-aware | Drillable | Right shape for JTBD? |
|---|---|---|---|---|---|---|
| PM Dashboard kanban (`pm_dashboard.xml:13-94`) | 1 WC | 4 KPIs + OEE | per-WC counts | No | Yes | Partial — no queue context |
| Ready Queue (`pm_menus.xml:40-62`) | 1 MO | core MO fields | group by state | No | Yes | Partial — no reservation indicator |
| Capacity pivot (`pm_capacity.xml:32-42`) | minutes | WC × day | sum(duration_expected) | No | No | Right shape, wrong denominator |
| Floor Load (`pm_menus.xml:114-125`) | 1 WC | name only | group by active | No | Yes | Weak — no load context |
| MO Kanban (`mrp_production_kanban.xml`) | 1 MO | redesigned card | group by state | N/A | Yes | Good for state pipeline, not planning |
| Project task (`project_task.py`) | 1 customer job | 50+ fields | extensive rollup | Partial | Yes | Excellent read-side dashboard |

### OpenValue OPL-1 modules touching R3 (live, legal-hold gated)

| Module | R3 role | In Southbrook manifest? | Surfaced in UI? | Legal status |
|---|---|---|---|---|
| `mrp_shop_floor_control` | Provides `sfc_sequence` + `date_planned_start_wo` / `date_planned_finished_wo` fields | No | Indirectly (fields exist) | OPL-1 HOLD |
| `mrp_sfc_scheduling_engine` | Finite-capacity optimiser (EDD/SPT/LPT/FIFO) | No | **No** | OPL-1 HOLD |
| `mrp_sfc_availability_check` | Per-MO material + capacity shortage; MPS batch wizard | No | **No** | OPL-1 HOLD |
| `openvalue_mrp_planning_capacity_load` | Capacity load surface | No | No (apparent) | OPL-1 HOLD |
| `openvalue_mrp_planning_engine` | Higher-level planning | No | No (apparent) | OPL-1 HOLD |
| `openvalue_mrp_planning_mto` / `_multilevel` / `_subcontracting` | Specialised planning | No | No (apparent) | OPL-1 HOLD |

**This is the most significant single finding.** Six-plus OPL-1 planning modules run on the prod box, but the planner has no menu paths to any of them. Either the licence is settled and we should be using the UI those modules ship, or we should be replacing them — but the current state (installed, paid silently in legal risk, invisible in UI) is worst-of-both-worlds for the planner JTBD.

---

## Constraints applied

- READ-ONLY — no files modified.
- Planner JTBD only — no operator, eng, quality, sourcing, mobile or dashboard claims.
- Every claim cites file:line.
- No emojis.
- Live DB probe not obtained (SSH rejected); flagged in TL;DR.
