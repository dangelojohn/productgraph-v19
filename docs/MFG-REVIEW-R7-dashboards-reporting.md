# R7 — Manager Dashboards & Reporting review

**Scope.** The plant / operations manager at 7am with coffee, the same manager
at 4pm signing off shift, and the GM at month-end asking "did we ship to plan?"
This review walks the manager's day — coffee glance, risk scan, drill-in, trend,
crew, capacity, action items, comms, shift review, monthly close — and grades
the surface they actually touch.

**Live-state probe.** The sandbox blocks the requested SQL probes against
`southbrook-prod` (ssh + docker exec into Production Reads not authorized for
read-only review). Quantitative baselines below are flagged `[needs live
probe]`; the qualitative findings are grounded in code reads of every cited
file:line. The R2 sibling review observed 99 WOs, 14 workcenters, 0 downtime
entries, 0 shift handovers, 14 MI checks, 2 production packages on the same DB
on 2026-06-27 — that floor adoption state is the reference frame for whether
the manager dashboards have data to render.

**Headline.** Southbrook has built a thoughtful, layered manager surface —
`southbrook_mrp_pm` (M10-M18 KPI tiles + portal + kiosk), the MI Production
Board on `sb.production.package`, the Kitchen Ops POC dashboard in
`southbrook_premium_orchestration`, and the just-deployed PG-143 work queue.
The pieces are individually correct; the problem is **none of them is the "one
screen"** the JTBD frames demand. A plant manager opening Odoo at 7am has to
visit 4-5 different menus to assemble the picture. Worse, every KPI is
"current-state-only" — there is **no on-time-delivery percentage, no
week-over-week throughput trend, no shift comparison, no yield trend, no
month-end actual-vs-plan**. The dashboards answer "what's the snapshot right
now" but not "are we trending up or down" or "did we hit the number".

---

## Top 5 Wins

### R7.1 — Ship a true single-screen "Is today OK?" landing page that fuses Floor Load + Family Throughput + MI status + Late count + activity feed into ONE dashboard, not five menu hops

**JTBD.** "When I arrive at 7am, I want one screen telling me what's at risk
today, so I can call the shift lead before standup."

**The gap.** Today the manager lands on whatever the last app was, then has to
visit:
1. `Southbrook PM → Dashboard` — workcenter KPI cards
   (`southbrook_mrp_pm/views/pm_dashboard.xml:77-94`),
2. `Southbrook PM → Family Throughput` — cabinet-family cards (same file
   `:154-171`),
3. `Southbrook PM → MI Production Board` — package-level blocker/warning
   counts
   (`southbrook_manufacturing_intelligence/views/manager_dashboard_views.xml:73-78`),
4. `Southbrook PM → Late Orders` — domain-filtered MO list
   (`southbrook_mrp_pm/views/pm_menus.xml:83-103`),
5. `Manufacturing → Kitchen Operations` — the TransientModel POC
   (`southbrook_premium_orchestration/views/kitchen_ops_dashboard_view.xml:11-78`).

Each is correctly built; none is the "one screen". The Kitchen Ops POC is the
closest match in spirit but ships **only three tiles**: WIP count, FreeCAD
bridge status, and a 10-row activity feed
(`southbrook_premium_orchestration/models/southbrook_kitchen_ops_dashboard.py:43-66`).
It deliberately defers everything the manager needs (the file's docstring at
`:19-27` lists what was cut: cron observability, integration hub, SSE
auto-refresh, permission tiers, shift awareness). The Kitchen Ops dashboard is
also a `TransientModel` so it loses every session — no bookmark of "my
favourite cabinet families" or "my watched workcenters".

The PM Dashboard at `pm_dashboard.xml:19-74` is the strongest piece — 4 KPI
tiles per workcenter (In Queue, Done Today, Late, Equipment Alerts), live
compute (`mrp_workcenter.py:89-119`), `decoration` traffic-light potential. But
it's workcenter-scoped: 14 cards filling the screen and **no roll-up tile** at
the top saying "Plant total: 47 in queue, 12 late, 3 equipment alerts, OTD
this week 87%".

**Effort.** MEDIUM (~2-3 weeks). Promote `southbrook.kitchen.ops.dashboard`
from POC to the real M10. Move it from `TransientModel` to a singleton
`Model` (one record per user, persists their tile layout / filters). Add tiles
for the metrics the current siblings already compute: plant-total WIP, late
count, OTD% (new — see R7.2), equipment alerts roll-up, MI blockers
plant-wide, downtime hours today, top-3 bottleneck workcenters, top-3
slipping MOs, activity feed (existing). Each tile is a click-through to the
existing scoped list/kanban. Re-point the Southbrook PM root menu
(`pm_menus.xml:31-35`) at this action so 7am-open lands here.

**Impact.** This is the single highest-leverage win in R7. Replaces 5 hops
with 1. Every JTBD downstream (R7.2/3/4/5) becomes a tile composition rather
than a new dashboard build.

**Files.**
`addons/southbrook_premium_orchestration/models/southbrook_kitchen_ops_dashboard.py`
(promote from Transient + add ~12 compute fields),
`addons/southbrook_premium_orchestration/views/kitchen_ops_dashboard_view.xml`
(redesign as a tile grid), `addons/southbrook_mrp_pm/views/pm_menus.xml:31-35`
(re-point root menu action). Risk **LOW** — additive, all backing data already
exists.

**Click-count.** Before: 5 menu hops + 5 page loads to assemble the
"is-today-OK" picture. After: 1 click into the menu, then 1 click per tile to
drill (2 clicks to root cause vs. today's 4-6).

---

### R7.2 — Compute and surface on-time-delivery % (rolling 7d / 30d / MTD) — the single most-asked manager KPI in cabinetry, and Southbrook does not measure it today

**JTBD.** "When OTD drops below 95% for the week, I want to see the
contributing late MOs ranked by lateness × customer-priority, so I know where
to spend my hour."

**The gap.** Grep across the four target addons for `on_time`, `OTD`, or
`on-time` returns **zero hits** other than the comment in `pm_menus.xml:88`
defining the Late filter. Southbrook tracks `date_deadline` on `mrp.production`
and `southbrook_pm_late_count` per workcenter
(`southbrook_mrp_pm/models/mrp_workcenter.py:69-77,110-114`) and a Late Orders
list (`pm_menus.xml:83-103`), but **never computes a percentage** and never
keeps history. A manager seeing "12 late today" cannot tell whether that's
better or worse than last Tuesday, or whether the trend is improving. The CFO
/ GM asking "what was our OTD last quarter?" has no surface to answer from.

**Effort.** MEDIUM (~1 week). New stored daily-aggregate model
`southbrook.otd.daily` populated by a 1am cron that snapshots the previous
day's MO closure outcomes: `count_done_on_time` (`date_finished <=
date_deadline`), `count_done_late`, `count_overdue_open` (`date_deadline <
midnight AND state not in (done, cancel)`). Add three computed scalars to the
R7.1 dashboard: `otd_7d_pct`, `otd_30d_pct`, `otd_mtd_pct`. Build a graph view
for trend. Drill-in opens the existing Late Orders list pre-sorted by lateness
descending.

**Impact.** Closes the most-cited cabinetry-manager KPI gap. Unblocks
year-over-year reporting, customer-facing service-level commitments, and
contract-bid evidence. With history captured the manager can also see "OTD
dropped 8 points after the Maple finish line started" — root-cause attribution
becomes possible.

**Files.** NEW
`addons/southbrook_mrp_pm/models/southbrook_otd_daily.py` (~120 LoC, stored
model + nightly cron + 3 computed rolling windows),
`addons/southbrook_mrp_pm/views/otd_views.xml` (pivot + graph + list for
trends), tile in the R7.1 dashboard. Risk **LOW** — additive, no existing-
data mutation.

**Click-count.** Before: not computable (data not aggregated). After: 0
clicks for the headline (it's a tile), 1 click for the trend graph, 2 clicks
to the contributing-MO list.

**Live-data hook (when probe is permitted).**
```sql
SELECT
  count(*) FILTER (WHERE date_finished IS NOT NULL AND date_finished <= date_deadline) AS on_time,
  count(*) FILTER (WHERE date_finished IS NOT NULL AND date_finished > date_deadline) AS late,
  count(*) FILTER (WHERE date_finished IS NULL AND date_deadline < NOW()) AS overdue_pending
FROM mrp_production WHERE date_deadline IS NOT NULL;
```
That query — the one in the R7 brief — is precisely the daily snapshot the new
model should write.

---

### R7.3 — Build a "Risk Scan" view that ranks at-risk MOs by lateness × customer-priority × MI severity, so the manager spends their hour where it counts

**JTBD.** "Show me the 10 things at greatest risk of slipping this week,
ranked by how much I'll regret missing them, so my one-hour-before-standup
walk is targeted."

**The gap.** Today the manager has 3 partial views, none ranked:
1. Late Orders list (`pm_menus.xml:83-103`) — sorted by Odoo default, no
   priority weighting, no MI context, no customer-priority cue.
2. MI Production Board (`manager_dashboard_views.xml:46-63`) — color-coded
   blocked/review/ok, but unsorted-by-impact and scoped to packages not MOs.
3. MO Kanban (`southbrook_mrp_pm/views/mrp_production_kanban.xml`) — pipeline
   view, no risk-rank.

The audit's "lateness × customer-priority × MI severity" composite does not
exist. Customer-priority can be derived (sale_order_id → partner_id → channel
from the Estimating model — `dealer`/`tradesperson`/`bigbox` carry implicit
priorities; high-tier dealers should rank above big-box). Lateness is
`date_deadline - now()`. MI severity is already on `x_mi_blocker_count` and
`x_mi_warning_count` (`mrp_production.py:21-22`).

**Effort.** MEDIUM (~1 week). New stored computed field
`x_sb_risk_score` on `mrp.production` (Float, indexed) with a daily-recompute
cron and on-write trigger when `date_deadline`/`state`/`x_mi_status` change.
Formula proposal: `risk = max(0, days_late + days_to_deadline_inverted) *
channel_weight * (1 + 3*blocker_count + warning_count)`. Build a list view
sorted by score desc with the same `decoration-danger/warning/success` style
already used in the MI list (`manager_dashboard_views.xml:8-10`). Wire as a
top-of-dashboard "At Risk Today" tile.

**Impact.** Converts the late-MO list from a bucket into a queue. The 80/20
hour the manager spends becomes targeted. Also surfaces near-late MOs whose
MI flags suggest they're about to become late — preventive vs reactive.

**Files.** `addons/southbrook_mrp_pm/models/mrp_production.py` (new — currently
absent; the addon has `sale_order.py` but no `mrp_production.py`),
`addons/southbrook_mrp_pm/views/at_risk_views.xml` (new — list + search). Risk
**LOW** — stored field is additive, recompute cron is bounded.

**Click-count.** Before: 3 separate lists, manager mentally cross-references.
After: 1 list, sorted.

---

### R7.4 — Build a downtime/yield/throughput weekly trend report — manager has zero week-over-week or month-over-month surface today

**JTBD.** "When a workcenter's downtime spikes, I want to see if it's the
same tool / operator / shift, so I can act on a pattern not a one-off." +
"At month-end, I want actual-vs-plan yield % and throughput count per
workcenter, with last month's comparison."

**The gap.** Three pieces of trend-data are already captured but never
trended:
1. **Downtime** — every `sbk_workcenter_downtime` row carries `date_start`,
   `workcenter_id`, `reason`, `duration_min`, `downtime_cost`
   (`southbrook_mrp_kitchen_workcenters/models/southbrook_kitchen_workcenter_downtime.py:80-150`).
   The list view (`southbrook_kitchen_workcenter_downtime_views.xml:7-19`)
   sums Total min and Total cost across the FILTERED list, but offers no
   pivot, no graph, no rolling window. A manager wanting "downtime by reason
   this month vs last month" has to export to Excel.
2. **MI yield/waste** — `x_mi_yield_pct` and `x_mi_waste_area_m2` live on
   every MO and every production package (`mi_engine.py:378-383, 624-628`).
   They're surfaced as numbers on the MO form's Intelligence tab
   (`mrp_production_views.xml:30-33`) but **never aggregated**. Plant-average
   sheet yield this week is uncomputable from the current UI.
3. **Throughput** — `southbrook_pm_throughput_today` is computed per
   workcenter (`mrp_workcenter.py:59-68`) but stamped only for today. Yesterday's
   number is gone the moment midnight ticks.

There is one (1) trend view in the whole R7 set: the Capacity pivot
(`pm_capacity.xml:32-42`) over `mrp.workorder.duration_expected`. It's read-
only future-looking ("what's committed next 7d") — not historical.

**Effort.** MEDIUM-LARGE (~2 weeks). Two-part:
- **(a) Downtime trend** — add pivot + graph views to the existing downtime
  action (`southbrook_kitchen_workcenter_downtime_views.xml:95-99` →
  add `view_mode="list,form,pivot,graph"`). Group by workcenter ×
  week × reason. Zero new model code; ~30 LoC of view XML.
- **(b) Daily history snapshot** — new model
  `southbrook.shop.daily` keyed `(date, workcenter_id, cabinet_family_id)`
  capturing `wo_started`, `wo_finished`, `mo_finished`, `panels_processed`,
  `avg_yield_pct`, `downtime_min`, `scrap_count`, `ncr_opened`. Populated by
  a 12:30am cron from the existing data. Pivot views give week-over-week,
  month-over-month, per-shift breakdowns once shift attribution lands
  (see R7.5).

**Impact.** Closes the month-end-close JTBD frame entirely. Converts every
"we feel like we're getting better" conversation into evidence. Unlocks
investment cases ("the new edge bander reduced downtime 38% MoM").

**Files.**
`addons/southbrook_mrp_kitchen_workcenters/views/southbrook_kitchen_workcenter_downtime_views.xml`
(extend action), NEW
`addons/southbrook_mrp_pm/models/southbrook_shop_daily.py` + matching
`addons/southbrook_mrp_pm/views/shop_daily_views.xml` + cron in
`southbrook_mrp_pm/data/`. Risk **LOW** — read-only, additive.

**Click-count.** Before: not possible without Excel export. After: 1 click
to the trend.

---

### R7.5 — Make the dashboards shift-aware (operator/shift/lead-time grouping) — today every KPI is "since midnight" with zero crew attribution

**JTBD.** "Which shift / team / operator is leading? Lagging? So I can shift-
hand-over the right context and run a meaningful weekly leader-review."

**The gap.** Every PM KPI today is single-shift "since midnight today" — see
the `today_start = midnight()` calculation in
`southbrook_mrp_pm/models/mrp_workcenter.py:95-97` and
`southbrook_cabinet_family.py:87-89`. No shift attribution, no operator
attribution, no team attribution.

Two adjacent pieces exist:
- **Shift Handover** (`southbrook_shift_handover.py`,
  `southbrook_shift_handover_views.xml`) captures `shift` (selection) +
  `shift_date` + `from_user_id` + `to_user_id` per handover record, with a
  state machine. Good model. But it's a **documentation artefact** — nothing
  reads it to attribute KPIs.
- **WO `user_id`** — Odoo native, the operator who started/finished the WO.
  No view currently aggregates by it.

The "shift awareness" deferred-item in
`southbrook_kitchen_ops_dashboard.py:26` explicitly calls this gap out as
week-4-not-done. The Kitchen Ops POC docstring says it.

**Effort.** MEDIUM (~1-1.5 weeks). Three pieces:
- (a) On `mrp.workorder`, derive `x_sb_shift` from `date_start` (06-14 =
  Day, 14-22 = Eve, 22-06 = Night) as a stored computed field. Make it
  configurable via `ir.config_parameter` for stations that run a different
  shift pattern.
- (b) On the R7.1 dashboard add a `shift_filter` + `operator_filter` plus
  a "Shift Leaderboard" tile: WOs completed this shift, average WO cycle
  time vs target, downtime opened this shift, NCRs opened this shift,
  ranked by operator.
- (c) Cross-reference with the shift_handover record: if today's day-shift
  handover ID exists, render a "Handover from previous shift" alert with
  the summary text — manager sees what the outgoing lead flagged without
  clicking through.

**Impact.** Converts the "weekly leader review" from anecdote to data.
Reveals operator training gaps (one operator's WOs run 18% slower → coaching
not blame). Validates whether the shift-handover process is actually being
used (today, R2 found 0 handover rows — so part of this win is that simply
surfacing the data will drive the behaviour).

**Files.** `addons/southbrook_mrp_kitchen_workcenters/models/mrp_workorder.py`
(extend with shift compute),
`addons/southbrook_premium_orchestration/models/southbrook_kitchen_ops_dashboard.py`
(add shift filter + leaderboard compute),
`addons/southbrook_premium_orchestration/views/kitchen_ops_dashboard_view.xml`
(add tile). Risk **LOW** — shift bracketing is convention-driven, easy to
configure.

**Click-count.** Before: not possible (data not attributed). After: 1 click
to the leaderboard tile, with operator and shift visible inline.

---

## R7.6 — Supplementary findings (lower-effort polish)

These are the smaller wins that didn't make the top 5 but the manager will
notice on day one.

- **Bottleneck field reserved but never written.** The MI form view
  intentionally hides `x_mi_bottleneck_workcenter_id`
  (`mrp_production_views.xml:22-26`) because no engine writes it. Either
  populate it from `mrp.workcenter.x_sbk_is_bottleneck` walks on the MO's
  WO chain, or drop the field. Today it ships dead schema.
- **`x_mi_workcenter_blocker_count` chip on PM dashboard.** Already wired
  in `pm_kanban_inherit.xml:13-27` — good integration. Recommend extending
  the chip with the corresponding `x_mi_workcenter_warning_count` total
  count badge and a click-through to the MI-Production-Board pre-filtered
  to that workcenter (currently the chip is decorative, not clickable).
- **Floor Manager portal kiosk** (`floor_template.xml:220-305`) is a strong
  surface for the shop wall TV but is **operator-oriented**, not manager-
  oriented. Add a sister `portal_manager_kiosk` template for the manager's
  office TV: OTD trend chart, top-3 risk MOs, today vs yesterday delta,
  shift leaderboard. Use the same 30s auto-refresh mechanism already
  established (`floor_template.xml:34-37`).
- **MI auto-recompute frequency.** The MI engine recomputes only on the
  Recompute Intelligence button (`mrp_production_views.xml:10-13`) or
  presumably on cutlist write — no scheduled refresh. A 5min cron sweeping
  in-flight MOs would make the dashboard genuinely "live". Today a manager
  acting on a tile may be acting on stale data.
- **No mobile-friendly surface for the manager.** The Floor portal is
  responsive (the operator gets it); the backend kanbans + pivots are
  desktop-only. A walking-the-floor manager wants the R7.1 dashboard on
  a phone. Add a `frontend_layout` route + minimal SCSS wrap.
- **Activity feed is unbounded.** `southbrook_ops_event.py:30-34` deliberately
  defers a retention cron. With the volume the platform will generate (every
  MO confirm, every CAD render, every install risk), the table will be 10M+
  rows by year-end. Add a 90-day retention cron now while it's cheap.
- **`southbrook.kitchen.ops.dashboard` is TransientModel.** Any "my preferred
  workcenters / cabinet families / shift" preference resets every session.
  Promote to `Model` keyed by `(user_id)` with a singleton lookup so prefs
  persist.
- **Data Quality Dry Run** (`data_quality_report_views.xml:5-46`) is a powerful
  audit tool that surfaces 6 issue types (blank kitchen project, missing
  install due, queue overlap, etc.) but is reachable only via the project_mrp
  menu, not the manager dashboard. Add a `data_quality_blocker_count` tile to
  the R7.1 dashboard so the manager sees data hygiene degradation as it
  happens, not at quarterly review.
- **Activity feed has no severity-filter chip.** The Kitchen Ops dashboard
  shows all 10 most recent events
  (`southbrook_kitchen_ops_dashboard.py:101-109`) — a manager wants "alerts
  only" view. Add severity quick-filter buttons above the list.

---

## What's good (and should not be touched in pursuit of the wins above)

- **`mrp.workcenter` compute fields** (`mrp_workcenter.py:46-119`) — 4 KPI
  fields, depends_context-uid to avoid stampede recomputes, clear docstrings
  explaining why depends are loose. Good Odoo idioms.
- **MI engine** (`mi_engine.py`) is genuinely sophisticated — recipe-grammar
  influenced check creation, P3 auto-remediation of missing cutlists, T1
  open-side detection, severity-derived status rollup. The plant manager gets
  real, actionable, color-coded MI tiles on every package. The data model
  generalises well — adding new checks is one method.
- **Workcenter kanban inheritance pattern**
  (`pm_kanban_inherit.xml:1-31`) — clean module-boundary inheritance,
  additive chips, no view-replacement. Other modules should mirror.
- **Equipment → impacted MO smart button**
  (`maintenance_equipment.py:99-174`) — exactly the kind of cross-module
  link the JTBD frames demand. "Boring machine condition critical → click →
  see the 6 MOs about to slip." Underused — manager doesn't know it exists.
  Promote in R7.1 by adding "Equipment alerts" as a click-through tile.
- **Cabinet-family KPI cards** (`southbrook_cabinet_family.py`) are the right
  orthogonal axis to workcenter — "how are we doing on bases vs walls today"
  is the floor-walk question.
- **MO form Intelligence tab** (`mrp_production_views.xml`) is well-scoped:
  status badge, next action, blocker/warning counts, recommendation list.
  Operator clicking into an MO from any dashboard lands here and sees what
  to do next. Cited memory `[[southbrook_manufacturing_intelligence]]` is
  earned.
- **Shift handover model** (`southbrook_shift_handover.py`) — clean state
  machine, search defaults to "awaiting ack", chatter on the form, the right
  immutable-audit-trail shape. The model is good; the gap (R7.5) is that
  nothing reads it.

---

## Baseline tables

### R7-relevant fields on `mrp.production`

| Field | Source | Used by | Stored? |
|---|---|---|---|
| `x_mi_status` | `mrp_production.py:8` | MI form, package list decorations | yes |
| `x_mi_blocker_count` | `mrp_production.py:21` | MI form | yes |
| `x_mi_warning_count` | `mrp_production.py:22` | MI form | yes |
| `x_mi_next_action` | `mrp_production.py:23` | MI form | yes |
| `x_mi_yield_pct` | `mrp_production.py:24` | MI form | yes |
| `x_mi_waste_area_m2` | `mrp_production.py:25` | MI form | yes |
| `x_mi_bottleneck_workcenter_id` | `mrp_production.py:26` | hidden | yes, never written |

### R7 surfaces inventory

| Surface | Model | Type | Where | What it answers |
|---|---|---|---|---|
| PM Dashboard | `mrp.workcenter` | kanban | `pm_dashboard.xml:77` | per-station snapshot |
| Family Throughput | `southbrook.cabinet.family` | kanban | `pm_dashboard.xml:154` | per-family snapshot |
| Ready Queue | `mrp.production` | list | `pm_menus.xml:40` | confirmed/progress MOs |
| In Production | `mrp.production` | list | `pm_menus.xml:67` | currently-running MOs |
| Late Orders | `mrp.production` | list | `pm_menus.xml:83` | past-deadline MOs |
| Floor Load | `mrp.workcenter` | kanban | `pm_menus.xml:114` | workcenter list |
| Capacity | `mrp.workorder` | pivot+graph | `pm_capacity.xml:66` | 7d committed minutes |
| MI Checks | `southbrook.mi.check` | list | `manager_dashboard_views.xml:65` | severity-grouped check log |
| MI Production Board | `sb.production.package` | list | `manager_dashboard_views.xml:73` | per-package MI rollup |
| Kitchen Ops POC | `southbrook.kitchen.ops.dashboard` | form (Transient) | `kitchen_ops_dashboard_view.xml:11` | 3 tiles + activity feed |
| Floor portal | (portal template) | webpage | `floor_template.xml:27` | operator station queue |
| Floor kiosk | (portal template) | webpage | `floor_template.xml:220` | factory-TV WC grid |
| Downtime list | `southbrook.kitchen.workcenter.downtime` | list | `southbrook_kitchen_workcenter_downtime_views.xml:95` | downtime log |
| Shift Handover | `southbrook.shift.handover` | list+form | `southbrook_shift_handover_views.xml:113` | shift-change documentation |
| Data Quality | `southbrook.project.data.quality.report` | form | `data_quality_report_views.xml:4` | one-off integrity dry-run |
| PG Work Queue | `pg.eco`, `pg.revision`, etc. | list+kanban | `pg_work_queue_views.xml` | engineering-only |

### Manager-JTBD coverage matrix

| JTBD frame | Today | Gap | Win # |
|---|---|---|---|
| 1. Coffee + glance — "is today OK?" | 5 menu hops | No single screen | R7.1 |
| 2. Risk scan — what's at risk? | Late list (unranked) | No risk score | R7.3 |
| 3. Drill-in — root cause in 2 hops | MI form is good | Need rollup → drill | R7.1 |
| 4. Trend — WoW throughput/yield/downtime | None | No history | R7.2, R7.4 |
| 5. Crew performance | None | No shift attribution | R7.5 |
| 6. Capacity heatmap | Capacity pivot (future-only) | Read-only OK | covered |
| 7. Action items — NCRs/ECOs/approvals | Scattered across modules | No my-work feed for managers | R7.1, partly R7.6 |
| 8. Comms — chat shift lead | Chatter on records | OK | covered |
| 9. End-of-shift review | Shift handover form | KPIs not joined to handover | R7.5 |
| 10. Monthly close — actual vs plan | None | No monthly snapshot | R7.2, R7.4 |

---

## Closing read

R7's bones are good — Southbrook has the data, the right models, the right
decoration patterns, the right cross-module inheritance discipline. What's
missing is **synthesis** (R7.1), **history** (R7.2, R7.4), **ranking** (R7.3),
and **crew attribution** (R7.5). All four are additive — none requires
rewriting what's there. The "Mfg. Intelligence" tab on `mrp.production`
delivers on its per-MO promise (cited memory is earned); the gap is that **its
own engine never rolls the results up to a manager view** beyond the
package-level board. The 5 wins close that loop.
