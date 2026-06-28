# R2 — Shop Floor Operator review

**Scope.** The CNC operator, edge-bander, sander, finisher, assembler, packer at
their workcenter, tablet in hand (or no tablet at all). Time-tracking, scanning,
WO traversal, defect entry, shift handover. This review walks one operator's
shift and grades the surface they actually touch.

**Live-state probe (2026-06-27).** 99 WOs (`blocked=57, ready=36, progress=1,
done=5`), 14 workcenters (every one has `resource_calendar_id`, 5 flagged
`x_sbk_is_bottleneck=t`), and — telling — **0 rows in `southbrook_qr_scan_log`,
0 shift handovers, 0 downtime entries, 0 as-built records, 14 `mi_check`s, 2
production packages, 0 attachments on any of the 99 `mrp.workorder` records**.
The platform is built; the floor is not yet using it. That's the dominant
finding: the surface needs lower-friction entry points, not more fields.

---

## Top 5 Wins

### R2.1 — Promote native Odoo Shop Floor (web_studio module `mrp_workorder`) above the inherited form, OR ship a tablet kanban — the operator has no big-touch view today

**JTBD.** "When I'm at my CNC with gloves on and a panel in the bed, I want to
see my next 3 jobs and tap Start on the top one in <1s, so I don't have to
peck at a tiny form field every cabinet."

**The gap.** The only operator-facing screens currently inherited are
`view_mrp_workorder_form_sbk_kitchen`
(`addons/southbrook_mrp_kitchen_workcenters/views/mrp_workorder_views.xml:9-41`)
which adds a `Southbrook Kitchen Metrics` group of 9 read-only KPI fields to
the native form — **no kanban, no card view, no big-button start/finish UI**.
The base `mrp_production_workorder_form_view_inherit` form (peeked at
`/usr/lib/python3/dist-packages/odoo/addons/mrp/views/mrp_workorder_views.xml`,
lines 134-220) is a desktop form with multi-page notebook (Time Tracking,
Components, Blocked By) — not glove-friendly. The native v19 list view at
priority 100 has Start/Pause/Done buttons but they're 28px icon buttons in a
table row — touch-target #fail. The default action `action_mrp_routing_time`
opens `graph,pivot,list,form,calendar` views — **kanban is absent**.

Native Odoo EE ships a touch-first `mrp_workorder` "Shop Floor" module (the
tablet view, fullscreen, big tiles). On v19 CE you don't get it free, but the
shape is the right answer.

**Effort.** Medium (~1.5 weeks). Build `view_mrp_workorder_kanban_sbk_tablet`:
one card per WO, 220×140px tiles, top-line product name + cabinet code + room,
big Start/Pause/Done in 60px rows, default-filter `workcenter_id == active_id`
and `state in ('ready','progress')`. Set as default kanban on a new
`action_sbk_tablet_workcenter` action plumbed off the workcenter form ("Open
Tablet View" button).

**Impact.** This is the highest-leverage win in the whole module. The current
form requires ~6 taps to start a job (Apps menu → Manufacturing → WOs → Filter
by my WC → Open my WO → Start). A tablet kanban gets it to **2 taps**.

**Files.** `addons/southbrook_mrp_kitchen_workcenters/views/mrp_workorder_views.xml`
(extend with kanban view + action), `mrp_workcenter_views.xml` (add "Open
Tablet" button on workcenter form). Risk **LOW** — additive view, no schema.

**Live-data corroboration.** Today's WO breakdown is 57 blocked / 36 ready / 1
in progress. Even the supervisor can't see at a glance which 36 are eligible
"now" per workcenter — there is no current-workcenter filter on the default
action and no kanban grouping.

---

### R2.2 — Auto-create a scan log entry when the operator opens a WO from the form, not just from `/sb/qr/scan` — so the defect-context window works even for non-scanned WO opens

**JTBD.** "When a defect lands on a part 90 seconds after I opened the WO from
my recent-items list (not by scanning), I want my defect-type beep to still
auto-link to my current WO, so I don't have to type a WO number on a tablet."

**The find.** `DefectQrKind._resolve_workorder_from_context`
(`addons/southbrook_mrp_kitchen_workcenters/models/qr_kind_handlers.py:175-217`)
walks `southbrook.qr.scan.log` for the user's most recent scan in a configurable
window (default 300 s) and uses that as the WO context for a defect-type QR.
This is **good design** — it's the right pattern. But the scan log is **only
written by the controllers** at `qr_scan.py:485` and at
`southbrook_floor_traveler/controllers/scan_endpoint.py:134-147`. If the
operator opens the WO via the kanban from R2.1 (or via "My WOs" recents, or
via a chatter link from a planner), there is **no scan log row** — so a defect
scan a moment later silently fails to auto-resolve the WO and the operator has
to fill in `x_sbk_workorder_id` by hand.

Today the table is empty so we can't see the failure mode in production yet,
but the moment R2.1 ships and operators start using the form/kanban path,
defect entry becomes manual.

**Fix.** Override `mrp.workorder` `web_read` (or add an OWL action on form-load)
to write a synthetic `southbrook.qr.scan.log` row with `action='form_open'`
when an operator opens a WO. Cost: ~30 LOC, tested with one fixture.

**Effort.** SMALL (~1 day). **Impact.** HIGH for defect velocity once R2.1
lands. Without this, R2.1 silently breaks R2.7-style defect context.

**Files.**
`addons/southbrook_mrp_kitchen_workcenters/models/mrp_workorder.py` (new
`@api.model` `_log_form_open` hook), `models/qr_kind_handlers.py:194` (extend
the target_model whitelist to include the `form_open` action). Risk **LOW**.

---

### R2.3 — Wire the WO traveler PDF's printed instructions to fire actions when scanned — today the QR opens the WO form but the printed text says "tap Start/Pause/Finish" which sends the operator on a click hunt

**JTBD.** "When I scan the QR on the printed traveler taped to my cell, I
want the next button I tap to be Start — not have to scroll-and-find the
right state-machine button on a form I just landed on."

**The find.** The traveler PDF
(`addons/southbrook_mrp_kitchen_workcenters/reports/wo_traveler_report.xml:88-99`)
prints "1) Scan QR to open this WO on your tablet. 2) Tap Start when work
begins." The QR encodes `sb://wo/<id>?...` and the scan handler
(`qr_kind_handlers.py:67-87`) supports `action=start|pause|finish` — but the
QR baked into the traveler **does not include the action parameter**. So
scanning lands the operator on the form (`/odoo/action-base.action_open_view/<id>`)
and they still have to find the right button.

The native form button `button_start` exists, but in v19 it's inside the
priority-100 list view (column button, 28px) or — on the form — depends on
state combinatorics (`invisible="production_state in ('draft','done','cancel')
or working_state == 'blocked' or state in ('done','cancel') or is_user_working"`)
that mean it's frequently hidden when the operator expects it to be there.

**Fix.** Print THREE QRs on the traveler instead of one:
- "SCAN TO START" → `sb://wo/<id>?action=start`
- "SCAN TO PAUSE" → `sb://wo/<id>?action=pause`
- "SCAN TO FINISH" → `sb://wo/<id>?action=finish`

The scan controller already wires `action` through to the kind handler
(`qr_scan.py:46-65`). Both the URL-redirect path (GET) and the JSON path
(POST) need a small tweak: when `action != open`, **don't** redirect to the
form — render a `200 OK` confirmation page ("WO XYZ started — close this tab")
so the operator's browser doesn't navigate away while the next part is being
fed.

**Effort.** SMALL (~1 day). 60 LOC of QWeb + 20 LOC of controller branch.

**Impact.** Takes "scan QR → start WO" from ~4 taps to **0 taps**. This is the
flow you actually want.

**Files.** `addons/southbrook_mrp_kitchen_workcenters/reports/wo_traveler_report.xml`
(add 3 QR images via a new computed field on `mrp.workorder` similar to
`qr_image_base64` but parameterised by action). Add
`qr_image_base64_for_action(action)` helper to `southbrook.qr.mixin`. Tweak
`qr_scan.py:scan_get` to return a small confirmation page instead of redirecting
when `action != open`. Risk **MEDIUM** — the existing wo handler's
`if hasattr(record, 'button_start')` swallows errors silently
(`qr_kind_handlers.py:70-87`), so make sure failures surface, otherwise
operators scan, see a green page, and the state never moved.

---

### R2.4 — The Record Scrap button is good, but Scrap, Defect, and Downtime are three separate flows that an operator at the same moment should be able to fire from one screen

**JTBD.** "When I find a 8mm gouge in a door panel I'm sanding, the part has
to be scrapped, an NCR has to capture the defect type, and the 4 minutes I
spent diagnosing is downtime — I want to log all three from one screen at
the cell, not chase three menus."

**The find.** All three pieces exist as separate surfaces:
- `action_open_scrap_wizard` button on the WO form (good, in
  `addons/southbrook_mrp_kitchen_workcenters/views/mrp_workorder_views.xml:32-36`)
- NCR creation via defect-type QR scan
  (`addons/southbrook_mrp_kitchen_workcenters/models/qr_kind_handlers.py:146-282`)
- Downtime: separate menu under Manufacturing → Southbrook Kitchen → Work
  Center Downtime (`southbrook_kitchen_menus.xml:40-44`); operator has to know
  to navigate there and create a record by hand
  (`southbrook_kitchen_workcenter_downtime_views.xml:21-71` — form has 6
  fields, no fast-add).

What's missing: a **"Report a Problem" wizard** on the WO form that asks one
question (Scrap / Defect / Downtime / All three?) and pre-fills as much
context as it can. The Defect QR sheet pattern is good but assumes scanner +
paper sheet; for an operator without a printed defect sheet handy this fails.

**Effort.** MEDIUM (~3 days). Build a new `southbrook.report.problem.wizard`
transient model with a 4-option radio + per-option panel. Pre-fill
`production_id`, `workorder_id`, `workcenter_id`, `user_id`,
`date_start`/`date_end` (downtime path) from the WO context.

**Impact.** Cuts the cognitive load when something goes wrong. Today's empty
downtime + asbuilt tables prove operators aren't logging — partly because the
entry points are buried.

**Files.** New
`addons/southbrook_mrp_kitchen_workcenters/wizards/report_problem_wizard.py`
+ view. Header button on the WO form (next to Record Scrap). Risk **LOW** —
pure UX additive, every underlying model already exists.

---

### R2.5 — Surface drawings/photos on the WO form (and the traveler) automatically — today the operator scans the WO QR, lands on a form, and has zero direct path to the CAD/render that PROVES this is a B3D24 with maple interior

**JTBD.** "When I open the WO on my tablet at edge banding, I want to see the
edge-band side ('top/bottom/L/R') on a CAD render in 2 taps, so I don't
edge-band the wrong face on a finished side."

**The find.** Native v19 `mrp.workorder` form has zero exposure of
`mrp.production` attachments — they're on the MO chatter only. The shop-floor
notes field (`x_sbk_shop_floor_notes` on workcenter,
`addons/southbrook_mrp_kitchen_workcenters/models/mrp_workcenter.py:201`) is
free-text, station-wide, not per-WO. The MO header redesign
(`mrp_production_form_header_redesign.xml`) adds cabinet code / room / due
date — good — but doesn't pipe attachments to the per-WO view.

**Live evidence.** `SELECT count(*) FROM ir_attachment WHERE res_model IN
('mrp.production','mrp.workorder',...) GROUP BY res_model;` returns **30
attachments on mrp.production, 0 on mrp.workorder**. So drawings exist
upstream but the operator has to (a) realize they need them, (b) navigate
out of the WO form, (c) click into the MO, (d) scroll the chatter. That's a
4-tap detour mid-workflow.

**Fix.** Two things:
1. On the WO form, add a stat button showing `attachment_count` aggregated
   from `production_id.message_attachment_count` + `production_id.cabinet_*`
   FreeCAD-bridge attachments. Tap → opens the gallery.
2. On the traveler PDF, embed a thumbnail of the cabinet's primary 3D render
   (FreeCAD bridge writes these to `mrp.production` per the
   `southbrook_freecad_bridge` deploy recipe).

**Effort.** SMALL (~1.5 days). The freecad_bridge already writes attachments
to MO; just pipe them through.

**Impact.** Prevents the #1 mode of cabinet-shop defect: built-to-the-wrong-
spec because the operator never saw the spec.

**Files.**
`addons/southbrook_mrp_kitchen_workcenters/views/mrp_workorder_views.xml`
(new stat button block + xpath). New computed field
`x_sbk_attachment_count` on `mrp.workorder`. Update
`reports/wo_traveler_report.xml` to embed render thumbnail. Risk **LOW**.

---

## Supplementary findings

### R2.6 — There is no operator role; today only `admin` + system users exist
Live query: `SELECT login, share FROM res_users WHERE login NOT LIKE '%@%'`
returns admin, __system__, public, portaltemplate, bridge-svc. **Zero
operator-tier users**, zero shift leads, no QC inspectors. Until John seeds
real operator `res.users` records (or at minimum a generic "floor-tablet" user
the kanban runs as), nothing above this line of the review ships to anyone.

R2.6 sub-item: the `hr.skill` integration on workcenters
(`mrp_workcenter.py:81-89`) and operation templates is wired but unused — no
employees today to assign skills to. Defer the operator-skills feature until
the user list is real.

### R2.7 — `southbrook.shift.handover` model + UI is reasonable; the smoke barrier is the form's free-text Summary field — operators won't type at end of shift on a tablet
`southbrook_shift_handover.py:102-107` and view
`southbrook_shift_handover_views.xml:71-80` ship a `Text` field with a
placeholder. Suggest replacing with structured fields: 4 explicit yes/no/text
prompts ("Any cell currently down?", "Any open NCRs to brief?", "Tomorrow's
priority order?", "Anything else?"). Today the table is empty — proving the
free-text barrier in action.

### R2.8 — The cabinet label QR can carry per-cabinet QR; the operator never has to type a cabinet code
The label
(`addons/southbrook_kitchen_mrp/reports/cabinet_label_report.xml:248-251`)
includes `sbk_label_qr_image` which decodes to the WO/MO context. Good. The
gap is the OPERATOR pathway from "I have a labeled cabinet in my hand" to the
right WO — covered by R2.3 above.

### R2.9 — Floor traveler scan endpoint silently swallows errors on the consumption path; operators get a misleading "ok"
`addons/southbrook_floor_traveler/controllers/scan_endpoint.py:75-97` and
`models/sb_production_package.py:113-132` rely on `wo.sudo().button_finish()`
inside a path that the `southbrook_premium_orchestration` override
documents as "`except Exception` swallows that into a WARNING log". That
matters: a scan returns `{"ok": true, "scan_count": N}` even if the
downstream tool-consumption debit failed. **Fix**: surface a `warnings: []`
key in the scan response when the override caught a debit error, and flash
it amber on whatever client UI lives downstream.

### R2.10 — No keyboard-wedge / Bluetooth scanner support; today QR is camera-only via the browser
The `/sb/qr/scan` GET endpoint
(`addons/southbrook_qr_kit/controllers/qr_scan.py:44-64`) assumes a tablet
camera scan that produces a URL navigation. Most cabinet-shop floors use
keyboard-wedge Bluetooth scanners (Symbol DS2278, Honeywell Voyager) that emit
the URL + CR. The current path works (the URL navigation fires the GET) but
there's no input-buffering UI — meaning if the operator is mid-typing in
another field, the scan keystrokes go to the wrong place. Worth a small OWL
service that globally swallows scanner-prefixed input. Defer to R8 if mobile/
touch covers it.

### R2.11 — Quality Control workcenter has `x_sbk_quality_notes` but the operator's QC checklist (`qc_checklist` on operation template, `southbrook_kitchen_operation_template.py:177-181`) does NOT render on the WO form or traveler
The field is documented in the model but not surfaced anywhere — search
`grep qc_checklist addons/southbrook_mrp_kitchen_workcenters/views/
addons/southbrook_mrp_kitchen_workcenters/reports/` returns no hits. Per the
brief §11 (acceptance gates) this is the gate where the operator ticks
"checked square / cycled drawer / under raking light" before moving the part
on. Currently dead data.

### R2.12 — Native v19 Time Tracking is on the WO form behind `groups="mrp.group_mrp_manager"`
Confirmed at the native form readout above:
`<page string="Time Tracking" name="time_tracking" groups="mrp.group_mrp_manager">`.
This means **operators (group_mrp_user) cannot see their own time entries**.
They can `button_start` and `button_finish`, but they can't audit how long
they spent or correct an erroneous run. For a productivity-tied compensation
model (which the cabinet shop is heading toward per the labor-productivity
KPI in `mrp_workorder.py:105-114`), this needs an inheritance that exposes
the operator's OWN time_ids only (`domain=[('user_id','=',uid)]`).

### R2.13 — Defect-type QR sheet is print-on-demand at `/sb/qr/defect-sheet` — but no menu wires to it from where the operator works
The action exists (`southbrook_ncr_views.xml:143-152`) under "NCR / Rework
Queue" menu — only a QC manager would ever find it. Add the same menu entry
under a new operator-facing "Floor Tools" submenu so anyone at a station can
re-print after a sheet gets wet/torn.

### R2.14 — `x_sbk_active_for_kitchen=False` on 8 of 14 workcenters today
Live: Door Hanging, Hardware Fitting, Pack & Label, Door Shop, Sanding Prep,
Cure/Dry Room, Design Review, CNC Router 02 are all `f`. Either this is
in-progress data hygiene OR the filter `x_sbk_active_for_kitchen=t` quietly
hides them from operator views — risk of operators being unable to find their
WOs once R2.1's tablet kanban filters on that flag. Verify with John before
defaulting the tablet kanban to it.

---

## What's good

- **The QR architecture is honest crypto.** HMAC-SHA256 with per-deployment
  secret rotation (`qr_payload.py:48-62`), TTL-bound expiry per kind, scan log
  with IP/UA, kind handlers as registered abstract models. This is the
  foundation. No other Odoo shop floor I've seen ships this hardened.
- **The defect-type QR pattern is brilliant.** Operator pins 14 paper QRs
  (one per `DEFECT_TYPES` value) on a board, scans `defect + cabinet/WO`, an
  NCR drafts itself with both fields prefilled
  (`qr_kind_handlers.py:146-282`). This is the right shop-floor pattern.
- **`x_sbk_kitchen_expected_min` parallel to native `duration_expected`** is
  the right call (`mrp_workorder.py:45-51`) — keeps the planner's audit trail
  of which estimation engine produced which number.
- **`x_sbk_units_per_op_hour`** stored, computed, on `mrp_workorder.py:105-114`
  — exactly the labor productivity KPI the cabinet shop needs.
- **Live bottleneck flag** (`x_sbk_congestion_level` on workcenter,
  `mrp_workcenter.py:144-184`) — real-time scan-of-pending-WOs with tunable
  warn/alert thresholds via `ir.config_parameter`. The kind of thing most
  shop-floor MES systems would charge $50K/yr for.
- **Header context bar on MO form** (`mrp_production_form_header_redesign.xml:37-79`)
  — Cabinet Code, Room, Install Due, MI status badge all promoted out of
  tabs. Good design choice for an expert operator's MO view.
- **As-built records** auto-create on MO done with BoM version snapshot —
  this is CSA A277 / warranty-traceability gold (`southbrook_asbuilt_views.xml:185-198`).
- **Floor traveler scan auto-advances WO via `button_finish`** without
  duplicate telemetry — sb_production_package.py:113-132 has explicit BUG 1
  (sudo) + BUG 2 (workcenter snapshot) fix comments showing real shop
  hardening already happened.

---

## Click-count baselines

### Today (form-only, no tablet kanban)

| Phase | Click count (experienced operator) | Notes |
|---|---|---|
| Find next WO at my workcenter | 5 (Apps → MRP → WOs → filter WC → open) | No saved-filter URL bookmark; the v19 list default is "Done" filter — operator must clear |
| Start WO | 1 (Start button on list) OR 2 (open form → Start) | Buttons are 28px icons; on touch frequently mis-fires |
| Pause | 1 | Same constraint |
| See drawings | 4 (open form → bread-crumb up to MO → chatter → open attachment) | Attachments not surfaced on WO |
| Record scrap | 2 (form button → wizard confirm) | Good — best flow in the module |
| Report defect (no QR sheet) | 6 (form → menu → NCR queue → create → fill type+severity → save) | Manual NCR is painful |
| Report defect (with QR sheet) | 2 (scan WO QR → scan defect QR) | Best path; only works if printed sheet is on hand |
| Log downtime | 7 (menu → downtime → create → fill WC/reason/responsible/start → save → close) | Buried 2 menus deep |
| Finish WO | 1 (button) | Native works |
| Shift handover | ~6 (menu → handover → create → pick shift/date/WCs → type freeform summary → submit) | Free-text is the barrier |

### After R2.1 + R2.3 + R2.4

| Phase | Click count | Delta |
|---|---|---|
| Find next WO | 1 (tablet kanban auto-filters my WC) | −4 |
| Start WO | 0 (scan QR_START on traveler) | −1 |
| Pause | 0 (scan QR_PAUSE) | −1 |
| See drawings | 2 (form stat button → gallery) | −2 |
| Report defect | 1 (Report a Problem wizard, defect option) | −1 to −5 |
| Log downtime | 1 (Report a Problem, downtime option) | −6 |
| Finish WO | 0 (scan QR_FINISH on traveler) | −1 |

### After R2.2 lands (form-open scan log)

The defect-context window works even for non-scanned WO opens, so the
"with QR sheet" defect path stays at 1 tap even when the kanban is the
entry point — preserves R2.1's value.

---

## Cross-references

- R5 owns the deeper NCR / quality lifecycle — R2 only owns the *entry point*
  on the floor. The Top 5 here doesn't propose new defect taxonomy.
- R8 (mobile/touch) overlaps with R2.1 — the tablet kanban is operator-JTBD
  here, but the keyboard-wedge scanner / haptic feedback work belongs to R8.
- R3 (planner) reads `x_sbk_pending_wo_count` / `x_sbk_congestion_level`. R2
  consumers are read-only; do not propose changes to those fields.
- R1 (MO lifecycle) owns the MO state machine. R2 only touches WO-level
  state via `button_start/pause/finish` paths.
