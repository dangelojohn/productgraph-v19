# PG-152 — Click-Count Metrics, Before / After Sprint 4

> Read-only analysis. No source code was modified. Counts derived from the
> current view XML and lifecycle action methods in:
> - `addons/product_graph_base/views/pg_item_views.xml`
> - `addons/product_graph_base/models/pg_item.py`
> - `addons/product_graph_revision/views/pg_revision_views.xml`
> - `addons/product_graph_revision/models/pg_revision.py`
> - `addons/product_graph_eco/views/pg_eco_views.xml`
> - `addons/product_graph_eco/models/pg_eco.py`
> - `addons/product_graph_eco/models/pg_eco_line.py`
> - All `*_menus.xml` files (PG-142 consolidation)

## Methodology

- A "click" = one discrete pointer action OR one discrete form-field interaction
  (open a dropdown, type a value and Tab away, etc.). Keyboard shortcuts are
  NOT counted — power-user shortcuts (Ctrl+S to save, F2 to edit) are excluded
  so the floor matches a realistic mouse-driven session.
- Counts are derived from view XML (which buttons render in which states) and
  action methods (whether one click triggers a multi-step transition).
- "ASSUMED" prefix is used wherever the XML did not pin the answer.
- All counts assume the **admin / engineer + approver role** on the same user
  (SoD off path). The SoD-on path is called out where it materially changes
  the count.

---

## Journey 1: Create Part Through Release

This is the canonical create-part-through-release path: a new physical item
goes from nothing → released revision the rest of the system can consume.

### Before Sprint 4 (pre-PG-140 + pre-PG-141 + pre-PG-142)

| # | Click | Notes |
|---|---|---|
| 1 | Open the (legacy 9-top-level) ProductGraph menu | Vendors + RFQ were separate roots; pre-PG-142 overflow forced a 1-click drill into "more" on standard widths roughly half the time. ASSUMED no overflow here. |
| 2 | Click **Engineering** submenu | |
| 3 | Click **Items** | |
| 4 | Click **Create** (top of list) | |
| 5 | Type Item Name (h1) | |
| 6 | Type/pick Part Number (auto-sequenced but editable in concept) | Pre-PG-141 the Name was rendered TWICE — once in the h1 and once in an h3 editor — so the user had to navigate past or fill the dup. (+1 click) |
| 7 | Re-type/skip the **duplicate** Name field (pre-PG-141) | The doubled field went red on validation and needed acknowledgement. |
| 8 | Pick Item Type | |
| 9 | Pick Category | |
| 10 | Click cloud-save icon (no Ctrl+S allowed) | |
| 11 | Click **To Prototype** | concept → prototype |
| 12 | Click **To Engineering** | prototype → engineering |
| 13 | Click **Revisions** smart button | navigates to filtered revisions list |
| 14 | Click **Create** (top of revisions list) | |
| 15 | Type `change_summary` (min 10 chars) | |
| 16 | (Optional) open Properties tab, add a value row — ASSUMED 1 row, 3 clicks: open field, pick, save | counted as 3 |
| 17 | Click cloud-save | |
| 18 | Click **Submit for Review** | draft → review |
| 19 | (Approver role) Click **Release** | review → released |
| 20 | Confirm dialog "Release this revision? Irreversible." | per pg_revision_views.xml line 49 |

**Total: 20 clicks** (range 18–22 depending on whether the user uses the
inline "New Revision" header button added by the xpath inherit at line 234,
and depending on how many property rows they fill).

### After Sprint 4

PG-140 adds `action_promote` and `action_express_release`. PG-141 removed
the duplicate Item Name. PG-142 collapsed 9 top-level menus → 8 (Sourcing
absorbs RFQ).

| # | Click | Saving |
|---|---|---|
| 1 | Open ProductGraph menu | PG-142: no overflow at standard widths (saves ~0.5 click averaged across sessions, kept at 1 here). |
| 2 | Click **Engineering** | |
| 3 | Click **Items** | |
| 4 | Click **Create** | |
| 5 | Type Item Name | PG-141 removed the dup field (saves 1) |
| 6 | Type/pick Part Number | |
| 7 | Pick Item Type | |
| 8 | Pick Category | |
| 9 | Click cloud-save | |
| 10 | Click **Promote** → concept → prototype | PG-140: single button, gating same |
| 11 | Click **Promote** → prototype → engineering | PG-140 |
| 12 | Click **New Revision** in the item header (PG-140 era inline button) | bypasses the smart-button → list → Create path (saves 1) |
| 13 | Type `change_summary` | |
| 14 | (Property row, single value) | counted as 3 |
| 15 | Click cloud-save | |
| 16 | Click **Express Release** (assumes SoD off OR admin) | PG-140: collapses Submit + Release + confirm into 1+1=2 |
| 17 | Confirm dialog "Express Release will submit AND release… Continue?" | per pg_revision_views.xml line 40 |

**Total: 17 clicks** (counting the property row as 3).

If we collapse the property entry to "skip optional" (the user has no
property templates attached and just submits), the count drops to **14**.

If SoD is on and the user is not admin, Express Release blocks and the
user must take the standard Promote → Promote path (Draft→Review,
Review→Released — but with a second user) — that still beats the original
20 because PG-140's Promote consolidates the gating decision, but the
human-coordination cost dominates so the click delta is smaller.

### Net savings — Journey 1

| Variant | Before | After | Saved | Reduction |
|---|---|---|---|---|
| Full path (1 property row) | **20** | **17** | 3 | 15% |
| Optional-skip (no properties) | **18** | **14** | 4 | 22% |
| SoD-on, two-user path | 20 | 18 | 2 | 10% |

---

## Journey 2: Raise + Process an ECO with 3 Affected Items

### Before Sprint 4 (no PG-130 auto-close, no PG-131 spawn, no PG-132 explode)

| # | Click | Notes |
|---|---|---|
| 1 | Open ProductGraph menu | |
| 2 | Click **ECOs** | |
| 3 | Click **Create** | |
| 4 | Type Title | |
| 5 | Pick Change Type | |
| 6 | Pick Priority | |
| 7 | Pick Requestor | (likely auto-set but counted as a glance) — ASSUMED 0; dropped. |
| 8 | Pick Engineering Lead | |
| 9 | Pick Target Release Date | |
| 10 | Type Description | counted as 1 (rich text, single sentence) |
| 11–13 | Add affected-item line #1: item, change_kind, notes (3 clicks) | |
| 14–16 | Add affected-item line #2 (3 clicks) | |
| 17–19 | Add affected-item line #3 (3 clicks) | |
| 20 | Click cloud-save | |
| 21 | Click **Start Analysis** | draft → analysis |
| 22 | Click **Submit for Approval** | analysis → approval |
| 23 | Click **Approve** | approval → implementation |
| 24+ | **Per-item revision spawn (manual)** — for each of 3 items: open item → smart-button to revisions → create → copy predecessor properties manually → save → submit → release → confirm = ~8 clicks per item × 3 = **24 clicks** |
| 48 | Click **Release** (ECO) | implementation → released, manually after the last revision lands |
| 49 | Confirm dialog | per pg_eco_views.xml line 142 |

**Total: ~49 clicks** for 3 affected items.

The dominant cost is the **8 clicks × N items** to manually create + release
each target revision. Scales linearly.

### After Sprint 4 (PG-130 + PG-131 + PG-132 landed)

| # | Click | Saving |
|---|---|---|
| 1 | Open ProductGraph | |
| 2 | Click **ECOs** | |
| 3 | Click **Create** | |
| 4–10 | Fill basics (title, change type, priority, engineering lead, target date, description) — 7 fields | |
| 11–13 | Add affected-item line #1 (item, change_kind, notes) | |
| 14 | Click **Explode Impact** on line #1 | PG-132: wizard surfaces all where-used assemblies |
| 15 | Select-all candidates in wizard | |
| 16 | Confirm wizard | |
| 17–18 | (Wizard added 2 more lines as items #2 + #3 — saves the manual 3-click-per-line entry, but the user still needs to set each change_kind: 2×1 click = 2) | |
| 19 | Click cloud-save | |
| 20 | Click **Start Analysis** | |
| 21 | Click **Submit for Approval** | |
| 22 | Click **Approve** | |
| 23 | Click **Spawn Target Revision** on line #1 | PG-131: 1 click clones predecessor properties + EBOM into draft |
| 24 | Click **Spawn Target Revision** on line #2 | |
| 25 | Click **Spawn Target Revision** on line #3 | |
| 26–28 | Approver clicks **Express Release** on each of the 3 spawned revisions (3 clicks) | PG-140 |
| 29–31 | Confirm dialog × 3 | |
| — | **ECO auto-closes** when the last revision releases (PG-130) — 0 clicks | |

**Total: ~31 clicks** for 3 affected items — 0 clicks at close.

If the user uses Explode Impact effectively (the second + third items came
from the closure walk, not manual entry), the gap widens further.

### Net savings — Journey 2

| Path | Before | After | Saved | Reduction |
|---|---|---|---|---|
| 3-item ECO, full process | **49** | **31** | 18 | 37% |

---

## Journey 3: Process a 10-Item ECO — Scaling Analysis

Per-item costs scale linearly in both worlds, but PG-131/132/140 shift the
slope sharply.

| Per-item cost | Before | After |
|---|---|---|
| Add line | 3 | 0–3 (1 if exploded, 3 if manual) |
| Spawn target revision | 8 (manual create + properties + EBOM) | 1 (PG-131 button) |
| Release | 3 (Submit + Release + confirm) | 2 (Express Release + confirm) |
| Per-item subtotal | **14** | **3–6** |

For 10 items:

| Stage | Before | After |
|---|---|---|
| Fixed ECO overhead (open, create, basics, transitions) | ~13 | ~13 |
| Per-item × 10 | 140 | 30–60 |
| Final ECO release + confirm | 2 | 0 (auto-close) |
| **Total** | **~155** | **~43–73** |

**Reduction: 53–72%** on a 10-item ECO. Savings scale with line count
because spawn + auto-close are constant-per-item improvements multiplied by N.

---

## Per-ticket attribution

| Ticket | Surface | Clicks saved | Where it shows up |
|---|---|---|---|
| **PG-140 Promote** | `pg.item.action_promote`, `pg.revision.action_promote` | 0–1 per state advancement (same number of button clicks, but one mental model instead of N state-specific buttons; reduces overflow on the header) | Item header (line 71 of pg_item_views.xml), Revision header (line 33 of pg_revision_views.xml) |
| **PG-140 Express Release** | `pg.revision.action_express_release` | 2 per revision (Submit + Release + confirm collapses to Express + confirm) | Revision header line 37; gated on `can_express_release` |
| **PG-141 dup Item Name** | Removed h3 editor in `pg_item_views.xml` | 1 per item create | Comment at lines 120–124 |
| **PG-142 menu nav** | Sourcing absorbs Vendors + RFQ; "Change Orders" → "ECOs"; "Engineering Categories" → "Categories"; "Engineering BOMs" → "EBOMs" | 2–3 per session (no overflow drilling at standard widths) | `pg_vendor_menus.xml`, `pg_rfq_menus.xml`, `pg_eco_menus.xml`, `pg_ebom_menus.xml` |
| **PG-131 Spawn Revision** | `pg.eco.line.action_spawn_revision` | 8 per affected item (manual revision creation + property copy collapses to 1 click) | Line button in ECO form line 201 |
| **PG-132 Explode Impact** | `pg.eco.line.action_explode_impact` + impact wizard | 2 per discovered downstream item (wizard surfaces N candidates in one walk vs N×5 manual where-used clicks) | Line button in ECO form line 208 |
| **PG-130 ECO auto-close** | `pg.eco._check_close_on_all_released` + `_do_auto_close` | 2 per ECO (final Release + confirm dialog gone) | `pg_eco.py` line 454 |

---

## Goals met?

- **Original brief target:** ≤10 clicks for create-part-through-release.
- **Actual after Sprint 4:** **14–17 clicks** (no-properties / with-1-property).
- **Status:** **Partially met.**

We cut 3–4 clicks (15–22%) on the canonical journey, but missed the ≤10 floor.
The remaining clicks fall in these buckets that Sprint 4 did NOT address:

1. **Form-field count** — Item Name, Part Number, Item Type, Category = 4
   field interactions that can't be skipped (Item Name + Part Number are
   `required`; Item Type + Category drive sequence + UI rules). This is
   the irreducible floor unless we collapse to a single creation wizard.
2. **State machine length** — concept → prototype → engineering is 2
   transitions (2 Promote clicks) even with PG-140. The Bible (R8) locks
   the 6-state machine so we can't compress further without a Bible change.
3. **Revision creation hop** — even with the inline "New Revision" button,
   the user lands on a fresh revision form (1 navigation) then must fill
   `change_summary` (required, min 10 chars). That's 2 floor clicks.
4. **Approver confirmation** — Release / Express Release confirmation
   dialogs are mandatory by design (irreversibility).

**Hitting ≤10 would require:** (a) an "Express Create + Release" wizard
that takes a single form from blank to released revision, or (b) PG-143's
work-queue inbox that collapses the navigation hops. PG-143 is held, so
those wins aren't realized yet.

---

## Caveats

**Single most-important limitation:** counts assume no errors and no
rework. In practice every blocked transition (PG-105 banners list the
reason) is a 0-click read but a real time cost; rejection cycles
(`action_reject_to_draft` on a revision) double-trip the path. The "before"
count would balloon faster than the "after" count under rework because
PG-140's promote consolidates the gating decision into one button-state
boolean (`can_promote`) so the user sees blocked-vs-clear more clearly.

Other caveats:
- Counts are admin / single-user (SoD off path). SoD-on adds a
  user-switch step that isn't a click but is a session cost.
- Keyboard shortcuts NOT counted. A power user with Ctrl+S, Alt+arrows,
  and Tab-through-fields would land roughly 3–4 clicks lower in both eras.
- Confirmation dialogs are counted as 1 click each (the click on OK).
- The "open menu" step counts as 1 click on all paths — PG-142's overflow
  win shows up only on narrow viewports.

---

## Adjacent UX observations (from tracing the paths)

1. **The "Revisions" smart button takes two hops, not one.** The button
   opens a filtered `pg.revision` list, not the revision form directly.
   Users wanting to create a new revision must then click Create. The
   inline "New Revision" header button added by
   `view_pg_item_form_inherit_revision` (line 234) bypasses this — but
   it's a header button next to Promote, which is the right place but a
   crowded one. Worth a UX tightening pass.

2. **Item form header is dense.** With PG-140 the header now shows up to 6
   buttons (Promote, To Prototype, To Engineering, Force Release, Move to
   Service, Obsolete) + a status bar + the inline New Revision. Most are
   gated invisible but on a `prototype`-state item with admin role, 4 of
   them render simultaneously. Adding Express Release on the revision form
   is fine because revisions live one-deep, but the item header is
   approaching button-soup. Consider grouping under a "More" menu (Odoo
   header overflow) once we add anything else.

3. **`change_summary` is required + 10 chars min on revisions.** This is a
   non-skippable click+type that ratchets the irreducible floor up by 1.
   Was a deliberate governance choice; flagging for visibility.

4. **The Property tab is a hidden cost.** Adding even a single property
   value is 3 clicks (open dropdown for field, type/pick value, save row)
   and the page is in a notebook tab so the user must click into it
   first. If properties were always-empty in the common case, the count
   floor would be 14, not 17.

5. **`action_view_revisions` returns `view_mode: 'list,form'`** — when
   the user clicks the smart button on an item with exactly one revision,
   Odoo still lands on the list. A `view_mode: 'form'` shortcut when
   `revision_count == 1` would save 1 click per visit.

6. **The ECO form has no equivalent of PG-140 Promote.** ECOs go
   draft → analysis → approval → implementation → released through 4
   separate buttons (Start Analysis, Submit for Approval, Approve,
   Release). A PG-140-style Promote for ECOs would harvest a similar
   savings; not in scope for Sprint 4 but a natural follow-on.

7. **Auto-close (PG-130) is silent.** It works (`_do_auto_close` posts to
   chatter), but the user who released the last revision doesn't get a
   foreground confirmation. They'll only learn the ECO closed from
   chatter or by re-opening it. Worth a transient notification.
