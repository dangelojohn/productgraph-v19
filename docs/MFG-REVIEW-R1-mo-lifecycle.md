# R1: MO Lifecycle Review

**Scope:** Manufacturing Order journey on `mrp.production` — from origination
through close-out — through the lens of a kitchen production planner / shop
manager. Read-only review of the southbrook_* MRP stack on the live Odoo 19 CE
deployment.

**Reviewer baseline:** 13 MOs / 99 WOs in prod. 3 draft, 2 confirmed, 8
progress. Zero done. 6 of 13 MOs carry MI status = blocked
(7 "Missing cutlist" blockers, 7 "CAD not complete" warnings — 100% of
non-draft MOs have at least one MI issue).

**Key data-quality finding driving Wins below:**

| Field                                        | Populated | Empty | % empty |
|----------------------------------------------|-----------|-------|---------|
| `sale_line_id` (canonical SO→MO link)        | 0         | 13    | 100%    |
| `origin` (legacy SO ref string)              | 13        | 0     | 0%      |
| `project_task_id` (Customer Job link)        | 9         | 4     | 31%     |
| `x_sbk_cabinet_code`                         | 0         | 13    | 100%    |
| `x_sbk_install_due_date`                     | 0         | 13    | 100%    |
| `x_sbk_kitchen_project_id`                   | 0         | 13    | 100%    |
| `x_sbk_kitchen_room`                         | 0         | 13    | 100%    |
| `date_deadline`                              | 10        | 3     | 23%     |
| `bom_id`                                     | 13        | 0     | 0%      |

Production-approval gate: of 5 source SOs with MOs, only 2 went through
`production_approval_state = approved`; the other 3 (which produced 6 of 13
MOs) bypassed the gate entirely with `state = none`. The state machine is
implemented but not enforced — MOs can land in the queue without an approver.

---

## Top 5 Wins (rank-ordered by impact-per-effort)

### Win 1 — Backfill the kitchen-context fields the redesign already plans on

- **JTBD:** When I open an MO, I want to immediately see which cabinet
  (B30, W2430, TP3084) for which room of which kitchen project — so I can
  triage without drilling.
- **Today:** 100% of live MOs have `x_sbk_cabinet_code = NULL`,
  `x_sbk_kitchen_room = NULL`, `x_sbk_install_due_date = NULL`, and
  `x_sbk_kitchen_project_id = NULL`. The Path-B header context bar
  (`/Users/naadmin/southbrook-v19cr/addons/southbrook_mrp_kitchen_workcenters/views/mrp_production_form_header_redesign.xml:46-53`)
  renders these fields as blank labels — the redesign cost was paid but the
  data is missing. The fields are also surfaced in the list view
  (`/Users/naadmin/southbrook-v19cr/addons/southbrook_mrp_kitchen_workcenters/views/mrp_production_views.xml:66-72`),
  same outcome: blank columns. Planner has to open the source SO to find the
  customer + room.
- **Recommendation:** Populate these at MO create time from the
  `sale.order.line` chain. The hooks already exist in two places — extend
  `SaleOrder.action_send_to_production`
  (`/Users/naadmin/southbrook-v19cr/addons/southbrook_mrp_pm/models/sale_order.py:223`)
  and the existing `project_task_id` auto-link
  (`/Users/naadmin/southbrook-v19cr/addons/southbrook_project_mrp/models/mrp_production.py:18-33`)
  to also derive: `x_sbk_cabinet_code` from `product.default_code` (already on
  the variant), `x_sbk_kitchen_room` from a new SO-line `zone_label` or
  `zone` selection (Q21 — exists per CLAUDE.md), `x_sbk_install_due_date`
  from `sale.order.commitment_date`, and `x_sbk_kitchen_project_id` from the
  task's parent project lookup. Also seed-backfill the 13 existing MOs in a
  post-init script (one-time data fix).
- **Effort:** S (~6 hours code + 2 hours one-time backfill script).
- **Impact:** Removes 2-3 clicks per MO inspection (open MO → open SO →
  click partner → read commitment_date → tab back). Multiplied across
  ~30 daily MO opens per planner = ~75 clicks/day saved. Also unlocks every
  "group by room / sort by install date" search the planner needs.
- **Files:** `southbrook_mrp_pm/models/sale_order.py:223`,
  `southbrook_project_mrp/models/mrp_production.py:18`, a new
  `southbrook_mrp_kitchen_workcenters/migrations/19.0.4.18.0/post-migration.py`.
- **Risk:** Low. Compute is at-create only; no recompute storm. The only
  partial risk is if `sale.order.commitment_date` is also empty (probable —
  check), in which case fall back to `date_deadline`.

### Win 2 — Collapse the 13-tab notebook to 5 role-based tabs

- **JTBD:** When I'm a shop manager reviewing an MO, I want only the
  information relevant to my decision (release? produce? close?) visible —
  not 13 expert-toggle tabs of which 8 are empty per state.
- **Today:** The MO form has 18 inherited view records stacking 9 added
  tabs on top of the 4 native ones = 13 total: Components, Work Orders,
  By-Products, Admin/Locations (renamed from Misc per `southbrook_kitchen_mrp/views/mrp_production_form_redesign.xml:57-59`),
  Cabinet Label, Mfg. Intelligence, Kitchen, Costs (CR), CAD Artifacts,
  Availability, Costfigures (mrp_product_costing), External (mrp_sfc_external_operation),
  SFC (mrp_shop_floor_control), Substitute (mrp_sfc_bom_component_substitution),
  Intelligence (hidden via priority 21 hack but the page is still loaded into
  DOM — `southbrook_mrp_kitchen_workcenters/views/mrp_production_form_header_redesign.xml:153-165`).
  The "hide via attribute" pattern for the old Intelligence tab is a smell —
  it's literally invisible-but-loaded.
- **Recommendation:** Group tabs by lifecycle phase: (1) **Build** =
  Components + Work Orders + By-Products + Availability + Substitute;
  (2) **Engineering** = Cabinet Label + CAD Artifacts + Kitchen;
  (3) **Quality** = Mfg. Intelligence (drop the duplicate Intelligence
  entirely, not just hide it); (4) **Costing** = Costs + Costfigures + SFC +
  External; (5) **Admin** = current Admin/Locations. Five visible tabs,
  same data, decisions made by phase. The page rename can ship from
  `southbrook_mrp_kitchen_workcenters` (top of dep chain — already established
  per the redesign header comment).
- **Effort:** M (~3 days). Each tab merge needs an xpath that grafts content
  *inside* an existing page rather than adding a new one. Risk of view
  validation churn means test each merge in isolation.
- **Impact:** Cuts horizontal-scroll on the form on a typical 1080p
  monitor — the 13-tab strip currently wraps to two rows, hiding 4 tabs
  below the fold until clicked. Each MO inspection saves ~3-5 seconds of
  visual scanning. The duplicate Intelligence elimination removes a
  documented refactor smell.
- **Files:** Consolidate inherits in
  `southbrook_mrp_kitchen_workcenters/views/mrp_production_views.xml` and
  `southbrook_kitchen_mrp/views/mrp_production_form_redesign.xml`. Drop the
  `view_mrp_production_form_sbk_hide_upstream_mi` record entirely; remove
  the duplicate "Intelligence" page from
  `southbrook_manufacturing_intelligence/views/mrp_production_views.xml`.
- **Risk:** Medium. xpath grafts can conflict with other addons'
  inherits. The priority cascade (16 → 17 → 18 → 19 → 20 → 21) is already
  load-bearing — re-orderings will need careful inheritance audit.

### Win 3 — Make the Production Approval gate actually gate

- **JTBD:** When a sales rep confirms a sale, I want every MO that lands in
  my queue to have been deliberately released by an approver — so the queue
  is a curated work-list, not whatever the system happened to spawn.
- **Today:** The state machine is fully implemented on `sale.order`
  (`southbrook_mrp_pm/models/sale_order.py:54-170`) — `none → pending →
  approved/rejected`. But **3 of 5** source SOs in prod created MOs without
  ever entering `pending` (`production_approval_state = none` on S01243,
  S00450, S00445). This means MOs are being created via routes that bypass
  `action_send_to_production` entirely — likely manual MO creation, OCA
  `product_configurator_mrp` post-confirm hooks, or sale_mrp's procurement
  rules. The button gate stops one path; the data shows the other paths are
  the bulk.
- **Recommendation:** Add a constraint, not just a button. Either (a) raise
  on `mrp.production.create` if the linked SO's `production_approval_state
  != 'approved'` (with a system-parameter escape hatch for one-off MOs), or
  (b) auto-set `production_approval_state = approved` on confirm of any SO
  below the approval threshold so the gate's universally true. Option (b)
  matches the docstring intent ("Gate between a confirmed sale order and
  the manufacturing queue") and gives the planner a single
  guaranteed-true field to filter on. Either way: the Ready Queue action
  (`southbrook_mrp_pm/views/pm_menus.xml:40-56`) should add
  `('sale_id.production_approval_state', '=', 'approved')` to its domain
  so the queue *means* approved-and-ready.
- **Effort:** S (~4 hours).
- **Impact:** Eliminates ghost MOs in the planner queue. Today 60% of
  source SOs bypass the gate so the existing approval surface is
  cosmetic — fixing it converts "Production Approval" from a label to a
  contract.
- **Files:** `southbrook_mrp_pm/models/sale_order.py` (add an SO-confirm
  override + constraint) and `southbrook_mrp_pm/views/pm_menus.xml:44`
  (domain tightening).
- **Risk:** Medium. Procurement-created MOs (e.g. component sub-assemblies)
  may legitimately not link back to a single SO — exempt MOs where
  `procurement_group_id` resolves through a non-customer route. Need to
  test on the current S00235 case which produced 6 MOs from one SO with
  approval already done.

### Win 4 — Replace the Ready Queue list with a deadline-priority kanban filtered to "actionable now"

- **JTBD:** When I plan tomorrow's shop, I want to see — in deadline order —
  every MO that is approved, has materials, has a cutlist, and is not yet
  in progress — so I release in the right order and skip the ones that
  aren't truly ready.
- **Today:** `action_pm_ready_queue` is a list view with
  `domain=[('state','in',['confirmed','progress'])]` and a default
  group-by-state context (`southbrook_mrp_pm/views/pm_menus.xml:40-56`).
  It surfaces in-progress MOs (wrong — they're not "ready", they're
  running) and doesn't filter out blocked MOs. In the live data, 6 of 8
  "progress" MOs are `x_mi_status = blocked` — the queue is misleading the
  planner. The kanban redesign at
  `southbrook_mrp_pm/views/mrp_production_kanban.xml` is the right
  primitive (state-pipeline columns, deadline-aware via
  `widget="remaining_days"`) but the Ready Queue action points at the list,
  not the kanban.
- **Recommendation:** Split into two actions. **Ready to Release** =
  `[('state','=','confirmed'), ('x_mi_status','!=','blocked'),
  ('reservation_state','=','assigned')]`, default view = kanban grouped by
  `x_sbk_install_due_date` week, with the existing card. **Blocked** =
  `[('state','in',['draft','confirmed']),('x_mi_status','=','blocked')]`,
  default view = list with `x_mi_next_action` as the headline column. The
  Ready-to-Release queue is the daily planning surface; Blocked is the
  inbox for engineering / procurement follow-up.
- **Effort:** S (~3 hours — two actions + a kanban field-set addition to
  surface MI status + cabinet code on the card).
- **Impact:** Today 75% of MOs in the "Ready Queue" are not actually
  ready (blocked-on-cutlist). Splitting makes the planner's mental model
  match reality — one queue you release from, one queue you chase.
- **Files:** `southbrook_mrp_pm/views/pm_menus.xml:40-78`,
  `southbrook_mrp_pm/views/mrp_production_kanban.xml` (add cabinet_code +
  MI status to the card field-set).
- **Risk:** Low. Domain change only; the underlying records don't move.

### Win 5 — Inline-fix the 7 "Missing cutlist" + 7 "CAD not complete"
checks instead of just surfacing them

- **JTBD:** When I see a Blocked MO, I want one click to get unblocked —
  either by triggering the auto-fix the engine already knows about, or by
  navigating directly to the missing artifact — not by reading prose and
  hunting through three menus.
- **Today:** The MI tab
  (`southbrook_mrp_kitchen_workcenters/views/mrp_production_form_header_redesign.xml:92-134`)
  shows the check list with `severity`, `message`, `recommendation`. The
  `recommendation` is a sentence ("Create or link a production package
  with a cutlist."). There is already a P3 auto-remediation flag in the
  engine (`southbrook_manufacturing_intelligence/models/mi_engine.py:317-319`,
  `_p3_try_emit_package` at :546) that can build a cutlist from the order
  line — but it's **default OFF** (`get_param('...auto_remediate_cutlist',
  default='False')`). The 7 prod MOs with Missing-cutlist blockers could
  self-heal if the flag were on — instead the planner reads "Create or
  link a production package" and has no in-form action.
- **Recommendation:** Two-pronged. (a) Add an action button per MI check
  row: "Auto-fix" (visible when `category='cut'` and the source order line
  has complete config — leverage `_p3_config_is_complete`),
  "Open CAD" (when `category='cad'` — jumps to the CAD Artifacts tab),
  "Open Hardware" (when `category='hardware'`). (b) Flip the
  `auto_remediate_cutlist` flag to `True` and re-run the engine on the
  7 blocked MOs as a one-time data fix — the safety net (Door
  Style = "Custom (Signature)" disqualifier at
  `mi_engine.py:533-537`) is already in place, so blast radius is bounded.
- **Effort:** M (~2 days). Action buttons on a One2many row need an
  `action_*` method per check category on `southbrook.mi.check`.
- **Impact:** Today 6 of 8 in-progress MOs carry a Missing-cutlist
  blocker. Auto-remediation unblocks at least the non-Custom ones — likely
  4-6 of them — and the planner gets one-click resolution on the rest.
  Each blocker today represents ~10 minutes of cross-menu hunting.
- **Files:** `southbrook_manufacturing_intelligence/models/mi_check.py`
  (add `action_remediate_cut`, `action_open_cad`, `action_open_hardware`),
  `southbrook_mrp_kitchen_workcenters/views/mrp_production_form_header_redesign.xml:121-133`
  (add button columns to the check list).
- **Risk:** Medium. P3 auto-remediate's `_p3_config_is_complete` heuristic
  is undertested in prod (default off since shipped). Run the flag on for
  one MO via shell first to confirm the build doesn't produce a bad cutlist
  before bulk-flipping.

---

## All findings (numbered, less-prioritized but documented)

### R1.6 — `date_deadline` shadows `x_sbk_install_due_date`; planners need to know which is authoritative

The Path-B redesign promotes both `date_deadline` and `x_sbk_install_due_date`
to the header (`mrp_production_form_header_redesign.xml:51-62`) — the field
help text on the model
(`southbrook_mrp_kitchen_workcenters/models/mrp_production.py:54-57`) explains
they're different ("install date" vs "production start date") but the form
labels are unclear: "Install Due" and "Deadline". Recommendation: rename
`date_deadline` to "Ship by" (planner mental model) and put a tooltip on
Install Due explaining it's the customer-facing on-site date. Effort: S.

### R1.7 — The "Recompute Intelligence" button does the planner's job manually

`action_recompute_manufacturing_intelligence`
(`southbrook_manufacturing_intelligence/models/mrp_production.py:30-34`) is
the only way to refresh MI status today. There is no automatic trigger on
relevant state changes (cutlist created, CAD attached, ECO closed). Result:
the badge can be stale and the planner has to remember to hit the button.
Recommendation: hook the recompute into write triggers on `sb.cutlist`,
`x_cad_status`, and the `sb.production.package.state` flips. Also add a
nightly cron pass for the long tail.

### R1.8 — Send-to-Production is per-line but creates no batching context

`SaleOrder.action_send_to_production`
(`southbrook_mrp_pm/models/sale_order.py:172-244`) creates one MO per line
with no shared `procurement_group_id` and no shared batch reference. A
13-cabinet kitchen produces 13 MOs that look unrelated in the queue — the
planner can't easily group them on the kanban or release them as a unit.
Recommendation: create a `procurement.group` named like the SO and set
`mo.procurement_group_id` per Odoo convention; this auto-groups in the
kanban and the standard MO views.

### R1.9 — `x_sbk_kitchen_project_id` is dead data: 0 of 0 projects exist

`SELECT count(*) FROM sb_kitchen_project` = 0. The workspace addon
(`southbrook_kitchen_workspace`) ships the entity but it is unused in prod.
The MO's `x_sbk_kitchen_project_id` field, the lookup in the Kitchen tab,
and the search/group-by all reference a model with no rows. Either start
populating it (per Win 1), wire it to auto-create from approved SO, or
deprecate the field. Today it's UI noise without a payoff.

### R1.10 — `button_mark_done` override silently swallows as-built failures

`MrpProduction.button_mark_done`
(`southbrook_mrp_kitchen_workcenters/models/mrp_production.py:183-204`)
wraps the as-built creation in a bare `except Exception: pass` with no log
line. The comment says "Never block an MO mark_done on an as-built failure"
which is right policy, but silent failure means the planner won't know
their as-builts are missing. Recommendation: log at WARNING, post a
mail.message to the MO, and add a cron-based gap-detector that re-runs for
MOs that closed without an as-built.

### R1.11 — Floor Manager portal index auto-refreshes every 30s — server load risk

`southbrook_mrp_pm/views/floor_template.xml:35-37` sets
`<meta http-equiv="refresh" content="30"/>` on the portal index. With N
tablets open this is N full-page requests every 30s against the portal
controller (which appears to do an aggregate query per refresh — not
sampled here but worth confirming). Either drop to 5min or move to a
lightweight polling endpoint that returns only the changed counts.

### R1.12 — Cabinet Label uses MO id in the QR URL — id changes are catastrophic

`_compute_sbk_label_qr`
(`southbrook_kitchen_mrp/models/mrp_production.py:218-245`) builds
`/odoo/action-mrp.mrp_production_action/{rec.id}`. If an MO is recreated
(rare but possible during ECO re-flow), the printed label points at a
deleted record. The signed `sb://mo/<id>` pattern referenced in the
docstring is the right answer and the qr_kit addon is already a dependency
(`__manifest__.py`). Migrating the URL builder is a 1-line change.

### R1.13 — No MO-create-from-estimate UI; planner relies on either SO approval or manual MO

There is no "Create MO" surface in the planner's menu that isn't (a) an
SO-driven action or (b) the bare Odoo "Manufacturing > Operations > New".
For one-off rework MOs not tied to an SO, the planner must use the bare
form and fill in `product_id`, `bom_id`, `qty`, `date`, etc. by hand — the
Southbrook context fields all stay empty (this is exactly the current 13
prod MOs all empty pattern). Recommendation: wizard "Plan a Cabinet
Rebuild" that prompts for source cabinet (defaulting cabinet_code, room,
kitchen project from the original MO) so the rework MO inherits context.

### R1.14 — MI status of "review" surfaces nowhere in any list — only blocked is visible

The Path-B header badge uses `decoration-warning="x_mi_status == 'review'"`
but the Ready Queue, In Production, and the kanban (apart from the MI chip
in pm_kanban_inherit.xml) don't show MI status at all. A "review" MO with
2 warnings reads identically to a clean MO in the queue. Add MI status
as a default-visible kanban field and an optional list column.

### R1.15 — There is no MO-level cost variance threshold escalation

The cost-variance compute
(`southbrook_mrp_kitchen_workcenters/models/mrp_production.py:155-170`)
flags `>25% = ECO` per docstring but nothing actually fires when an MO
crosses that. The view decorations (>10 warning, >25 danger) are visual
only. Wire an automated activity / mail post to the MO's PM at
threshold crossings — currently the variance is computed live and never
acted on.

---

## What's GOOD about the current state — do not break

- **The kanban redesign at `southbrook_mrp_pm/views/mrp_production_kanban.xml`
  is sharp:** state-as-pipeline columns, deadline-aware via
  `widget="remaining_days"`, integer-quantity formatting, accessible state
  badges, OWL-tokenizer-safe expressions (well-documented hard-won
  knowledge in the comments). This is the right pattern; extend it, don't
  rebuild it.
- **The MI engine separation
  (`southbrook_manufacturing_intelligence/models/mi_engine.py`) is well
  structured:** AbstractModel, per-MO and per-package recompute paths,
  category-organized checks. The P3 auto-remediation hooks already exist
  and are correctly defaulted-off pending validation — turning them on is a
  config flip, not a code change.
- **The Production Approval state machine on `sale.order` is complete and
  audit-trail-clean** (request → approve|reject, both branches tracked).
  The plumbing is right; the gating is not currently enforced (see Win 3).
- **The Path-B header context bar lives at the top of the dep chain on
  purpose** (per the load-comment in `mrp_production_form_header_redesign.xml`)
  and the priority cascade (17 header → 18 rename → 19 label → 20 MI tab →
  21 hide-upstream) is deliberate. Future inherits should slot in
  *between* this band, not replace it.

---

## Click-count baseline tables

### Canonical "Open MO, see context, decide next action"

| Step | Action                                                       | Click # |
|------|--------------------------------------------------------------|---------|
| 1    | Manufacturing menu                                           | 1       |
| 2    | Manufacturing Orders                                         | 2       |
| 3    | Click an MO row                                              | 3       |
| 4    | Read header context (cabinet code currently empty)           | (read)  |
| 5    | Tab to "Kitchen" to look for install due (currently empty)   | 4       |
| 6    | Tab to "Mfg. Intelligence" to see MI status                  | 5       |
| 7    | Click "Recompute Intelligence" because badge is stale        | 6       |
| 8    | Read recommendation text                                     | (read)  |
| 9    | Open new tab → navigate to mentioned cutlist / CAD artifact  | 7-9     |

**Baseline = 7-9 clicks** to triage a single blocked MO.
**With Wins 1+2+4+5 applied = 2-3 clicks** (kanban card shows cabinet code +
MI status + remaining days, click → form pre-positioned on
consolidated tab, "Auto-fix" inline).

### MO confirmation + release (SO approver path)

| Step | Action                                                | Click # |
|------|-------------------------------------------------------|---------|
| 1    | Sales menu → Quotations                               | 1-2     |
| 2    | Open SO                                               | 3       |
| 3    | Request Production (button)                           | 4       |
| 4    | (Different user) re-open SO from approval queue       | 5-7     |
| 5    | Approve Production (button — fires MO create)         | 8       |
| 6    | Switch to Manufacturing → Ready Queue                 | 9-10    |
| 7    | Find the just-created MO (no filter for "this SO")    | 11+     |

**Baseline = 11+ clicks** SO-to-queued. The lack of an "MOs created" toast /
direct-navigation after approve is the main friction.

### MO completion (planner path)

| Step | Action                              | Click # |
|------|-------------------------------------|---------|
| 1    | Open MO                             | 1-3     |
| 2    | Header: Produce / Produce All       | 4       |
| 3    | (Native flow) lot/serial wizard     | 5-6     |
| 4    | Confirm / Done                      | 7       |
| 5    | Print Cabinet Label (tab → button)  | 8-9     |
| 6    | Close + return to queue             | 10      |

**Baseline = 10 clicks**. Bulk-close from the list (select 5 done-ready MOs +
single Produce action) is not currently a surface — the standard list has no
multi-row Produce.

### Live counters informing the above

```
MO state distribution        Data quality (13 MOs in prod)
-----------------------      -----------------------------
progress  : 8                cabinet_code:    100% NULL
draft     : 3                install_due:     100% NULL
confirmed : 2                kitchen_room:    100% NULL
done      : 0                kitchen_project: 100% NULL (also 0 rows in model)
                             sale_line_id:    100% NULL (origin is set)
MI status                    project_task_id:  31% NULL
ok        : 7                deadline:         23% NULL
blocked   : 6
                             6 of 8 in-progress MOs are MI-blocked
                             7 "Missing cutlist" + 7 "CAD not complete"
```
