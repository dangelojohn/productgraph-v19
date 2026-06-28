# MFG-REVIEW-R4 — Engineering ↔ Manufacturing Handoff

> Read-only review of the **interface** between engineering authority (ProductGraph
> pg.item / pg.revision / pg.ebom / pg.release / pg.eco + southbrook_plm
> cut-spec / ECO) and shop production (mrp.bom, mrp.production, sb.cutlist,
> sb.production.package, southbrook.asbuilt). Walks the forward path (release
> → planner → MO), the reverse path (operator finds a problem → ECO →
> in-flight MOs), and the as-built reconciliation back to engineering.
>
> Date: 2026-06-27. Trees inspected: `~/product_graph_v19/` (ProductGraph,
> all 14 addons + bridge_spec) and `~/southbrook-v19cr/` (southbrook_plm,
> southbrook_kitchen_mrp, southbrook_mrp_kitchen_workcenters,
> southbrook_manufacturing_intelligence, southbrook_freecad_bridge).
> Other agents own R1/R2/R3/R5/R6/R7/R8 — this review touches MO-side
> models only at the interface seam.

---

## TL;DR — the seam is half-built

The PG side of the seam (release → mrp.bom write boundary, MO stamp,
frozen-MO registry, cron follow-up) is genuinely well-engineered. R1 is
respected; provenance is captured; idempotency is real. Read
`product_graph_release/models/pg_release.py:209-338` in isolation and it
holds up.

The Southbrook side of the seam is **not wired to it**. The bridge addon
that DECISIONS.md D1 mandates (and the elearning module's slide deck
demonstrates as if it were live) does not exist on disk in either repo.
`southbrook.eco` carries no `pg_ebom_id` field, never calls
`pg.release.action_execute_release`, and has its own parallel
governance vocabulary (`bom_id` → versioned BoM, `cut_spec_id`,
`affected_mo_ids`). The Southbrook cabinet label, cutlist, hardware
package, and asbuilt models have ZERO references to `pg_release_id`,
`pg_revision_code`, `pg_ebom_id` or `pg_root_item_id` — fields PG goes
to real trouble to compute and store on every mrp.production.

The result: two complete eng→mfg loops running side by side, neither
aware of the other. From an operator's vantage, this is a single
governance gap dressed up as two redundant audit trails. The asbuilt
captures `bom_version_snapshot` (Southbrook ECO's `southbrook_version`
counter) and silently drops the PG revision code; the planner's MO
view shows `Eng Rev` from one source and the kanban from another;
ECO impact analysis runs twice on different sets.

This is the central R4 finding and it conditions everything below.

---

## Top 5 Wins

### W1 — Bridge addon: implement (or formally retire) the spec at `bridge_spec/SPEC.md`

**Surface.** `/Users/naadmin/product_graph_v19/bridge_spec/SPEC.md:1-153`
documents `product_graph_southbrook` (note: SPEC says
`southbrook_plm_productgraph`; the README at `bridge_spec/SPEC.md:1-15`
flips it again). The spec is **out of date in two ways**: it targets
`_inherit = 'southbrook_plm.eco'` but the actual live model is
`southbrook.eco` (`/Users/naadmin/southbrook-v19cr/addons/southbrook_plm/models/southbrook_eco.py:23`),
and it overrides `action_close` while the real close method is
`action_apply` (`southbrook_eco.py:384`). PG-150 §4.10 calls D1 "contextually
superseded" by the native `product_graph_eco` module — but the native
module does not bridge to `southbrook.eco`, only governs PG's own
EBOMs.

**Smoking gun.** `southbrook_elearning_internal/data/elearning_slides_09.xml:1864-1950`
documents `pg_ebom_id` and `pg_release_id` on `southbrook.eco` as if
they were live fields. They aren't:

```
$ grep -n "pg_ebom_id\|pg_release_id" \
    ~/southbrook-v19cr/addons/southbrook_plm/models/southbrook_eco.py
(no matches)
```

Operators who ran the training will believe the integration is live.
It isn't. The whole bridge — fields, action_apply override, smart
button to view the PG release, the auto-derived release-reason from
ECO title — is documentation only.

**Cost of leaving it.** Every Southbrook cabinet ECO applied today
performs a `mrp.bom.copy` against the configurator-mrp BoM
(`southbrook_eco.py:_apply_bom` line 450-481) WITHOUT a `pg.release`
record. The MO that consumes that BoM gets no
`pg_release_id` stamp from the create override at
`product_graph_release/models/mrp_production.py:51-74` (the search
only matches `mrp.bom.pg_release_id IS NOT NULL`). PG's frozen-MO
governance (MG-1) silently does not fire for ECO-versioned BoMs. The
PG side's beautiful audit trail is empty by construction for
Southbrook's primary engineering surface.

**JTBD.** "When I close a cabinet ECO that swaps the door style, I
want every in-flight MO using the old door to be flagged in MO
chatter AND the planner queue AND the PG audit log, so I don't ship
the wrong door."

**Recommendation.** Either:
- **W1a — Implement the bridge.** Targeted addon `product_graph_southbrook`,
  3 fields on `southbrook.eco` (`pg_ebom_id`, `pg_release_id`,
  `pg_auto_release`), override `action_apply` to fire
  `pg.release.action_execute_release` after `_apply_bom`. ~80 LoC.
  Single smart button on the ECO form linking to the PG release.
- **W1b — Formally retire it.** Move `bridge_spec/SPEC.md` to
  `docs/archived/` with a HEADER section reading "ABANDONED 2026-06-27
  — see DECISIONS.md amendment D1bis." Then write D1bis: PG and
  southbrook_plm govern disjoint domains (PG: catalog parts;
  southbrook_plm: cabinet configurator template BoMs). Then patch
  `southbrook_elearning_internal/data/elearning_slides_09.xml` to
  reflect reality before the next operator-training rollout.

Either works. The current state — spec preserved, deck shipped, code
absent — is the worst of all worlds.

---

### W2 — Cabinet label has no revision code, no QR-embedded rev — installer cannot verify match-to-spec

**Surface.** `southbrook_kitchen_mrp/reports/cabinet_label_report.xml:209-310`
+ `southbrook_kitchen_mrp/models/mrp_production.py:34-256`.

The cabinet label exposes 11 fields: Customer, PO, TAG, Door Name,
Door Color, Interior, Code (SKU), Hinge, Fin, plus the QR image. The
QR (line 218-245) encodes `{base_url}/odoo/action-mrp.mrp_production_action/{rec.id}` —
the backend-MO URL only. **No revision code is on the label, no
revision code is in the QR.** `product_graph_release/models/mrp_production.py:33-37`
adds `pg_revision_code` (stored, indexed, related to
`pg_release_id.revision_id.name`) precisely so this could be label-able,
and `southbrook_plm/models/mrp_bom.py:25-31` adds
`southbrook_version` for the cabinet ECO worldview. Neither field
appears anywhere in the label template or its compute methods.

The kitchen-MRP MO inherit at `southbrook_mrp_kitchen_workcenters/models/mrp_production.py:30-77`
adds 7 more `x_sbk_*` fields (kitchen project, room, cabinet code,
install due date, complexity factor, priority) — again, no rev. The
ProductGraph MO inherit at `product_graph_release/views/mrp_production_inherit_views.xml:10-25`
DOES show `pg_revision_code` on the MO list and form ("Eng Rev"
column, `optional="show"`), but the COMPONENT it shows for is the
backend MO, not the customer-facing label.

**Cost.** Installer at site receives cabinet, scans QR with phone,
lands on the Odoo MO backend behind auth — useless on the truck.
Even if they could authenticate, the MO doesn't show the spec sheet
the customer signed. If a B30 from yesterday's run and a B30 from
today's run differ by rev (e.g. a 2mm change to the reveal that
landed via an active cut spec), there is no way to tell two cabinets
apart at the install site.

**JTBD (from the brief).** "When I print a cabinet label, I want
the rev code embedded in the QR, so installers can verify
match-to-spec at site without calling the office."

**Recommendation.**
1. Add `sbk_label_rev_code` compute on `mrp.production` that returns
   `f"{pg_revision_code}.{southbrook_version}"` (PG-eng-rev + cabinet
   ECO version), falling back to whichever side is set. ~10 LoC.
2. Render it on the label between the Code/Hinge/Fin footer rows
   (`cabinet_label_report.xml:299-308`) — one new `<tr>` with
   centered "REV: <code>" cell. Bumps version number ALSO printed
   so site-vs-shop drift is visible at a glance.
3. Embed the rev in the QR payload: extend the URL with a query
   param `?rev={code}`, OR switch to a signed `sb://cab/{lot}?rev={code}`
   scheme aligned with the rest of southbrook_qr_kit. The QR is
   already a Binary field computed at print time (line 218-245); the
   change is one `f"{url}?rev={rev}"` in `_compute_sbk_label_qr`.
4. As-built record sealed at MO done already captures
   `bom_version_snapshot` (asbuilt line 96-103); add `pg_revision_code`
   capture in `_snapshot_build_context` at line 262-287. Once a
   customer calls in 2 years about a cabinet under warranty, the
   answer is one query: `southbrook.asbuilt WHERE serial_number = X`.

---

### W3 — In-flight MO collision: TWO impact-analysis paths that don't talk

**Surface.** The system runs ECO-affected-MO scans in two completely
disjoint code paths:

| Path | Trigger | Stores in | View | Cron |
|---|---|---|---|---|
| PG side | `pg.release.action_execute_release` | `pg.release.frozen_mo_ids` (m2m) + `frozen_mo_count` | `pg_release_views.xml:101-112` (page "Frozen MOs (MG-1)") | `_cron_followup_frozen_mos` daily (`pg_release.py:587-650`) |
| Southbrook side | `southbrook.eco.action_apply` | `southbrook.eco.affected_mo_ids` (m2m) + `affected_mo_count` | smart button on ECO (`southbrook_eco.py:574-582`) | none |

`pg_release._freeze_open_mos` (`pg_release.py:502-531`) walks `mrp.production`
filtered by `bom_id == old_bom.id AND state IN (draft, confirmed,
progress)`. `southbrook.eco._scan_and_flag_affected_mos`
(`southbrook_eco.py:536-564`) walks `mrp.production` filtered by
`bom_id == self.bom_id AND state IN (confirmed, progress, to_close)`.
Different state sets. Different chatter messages (PG message at
`pg_release.py:510-518` is template-styled; southbrook one is delegated
to `southbrook_mrp_pm` M20 override per `southbrook_eco.py:548`).
Different audit-log shapes. Different cron behaviour.

A planner looking at a frozen MO in the kanban doesn't know which
governance fired — could be both, could be neither, could be a
PG release that DID fire on a non-ECO'd PG.ebom while a sibling
southbrook ECO ran on the configurator BoM and got missed.

**Cost.** Operator opens MO chatter → sees one "engineering note"
banner. Doesn't know whether the OTHER side is also flagging. If
both fire (which they should, eventually, post-bridge), the chatter
has two near-identical banners with subtly different wording and
no shared correlator. Activity-based follow-up (`_cron_followup_frozen_mos`
creates `mail.activity` records `summary='PG-FROZEN: review MO vs
release X'` on line 638 — Southbrook's path has no equivalent so an
ECO-flagged MO never auto-creates a follow-up to-do for Mfg).

**JTBD.** "When I release rev B of a cabinet, I want every in-flight
MO using rev A to be flagged in the planner's queue AND on the MO
chatter AND as a to-do on the Mfg user's dashboard — once, with the
same correlator, regardless of which engineering surface authored
the change."

**Recommendation.**
1. Once W1 is decided, the southbrook scan becomes a thin wrapper
   that delegates to PG's `_freeze_open_mos` (already idempotent and
   audit-logged). The southbrook `affected_mo_ids` m2m stays as the
   ECO-form smart-button surface, but the writer is PG.
2. Until W1 lands, at minimum **align state sets** — PG should add
   `'to_close'` to its scan filter; Southbrook should add `'draft'`
   to its filter — and embed a correlator string in both chatter
   posts (e.g. `[FROZEN-MO-CORR=<eco/release reference>]`) so the
   planner can grep two posts to one root cause.
3. Bring Southbrook's path into the cron family — extend
   `_cron_followup_frozen_mos` to also walk
   `southbrook.eco.affected_mo_ids` for ECOs in state='applied'
   within last 30 days. ~25 LoC. Without this, the southbrook path's
   `affected_mo_ids` is a one-shot snapshot — an MO that stays open
   for two weeks gets one chatter line at apply time and zero
   reminders thereafter.

---

### W4 — Asbuilt does not back-reference the engineering revision; loop never closes

**Surface.** `southbrook_mrp_kitchen_workcenters/models/southbrook_asbuilt.py:48-329`
captures: `production_id`, `production_package_id`, `product_id`,
`bom_id`, `bom_version_snapshot`, `lot_id`, `serial_number`,
`qc_check_ids`, `attachment_ids`, `built_at`, `built_by`,
`sealed_at`, `sealed_by`. The `_snapshot_build_context` helper at
line 262-287 reads `bom.southbrook_version` (the cabinet ECO
counter) and saves it.

What's missing: `pg_revision_id`, `pg_release_id`, `pg_eco_id`
(the southbrook ECO that produced the BoM the MO consumed),
`pg_ebom_id`. None of these are captured. The asbuilt is a
"snapshot" model that omits the engineering provenance side of the
hand-off — it captures what shipped (product + QC) but not WHY
that version of the product was the one that shipped.

**Cost.** Warranty call 18 months from now: "Cabinet B30 serial
0001 has a hairline crack at the dado." Field service queries
`southbrook.asbuilt`, sees BoM version 4, but cannot answer "what
ECO produced version 4?" without:
- opening the (archived) `mrp.bom.id == bom_id`, then
- following `mrp.bom.southbrook_eco_history_ids` (the inverse-of-
  `new_bom_id` link added at `southbrook_plm/models/mrp_bom.py:57-61`), then
- finding the ECO's `description` for the change rationale.

That's 3 hops + 2 archived-record lookups + manual reasoning to
answer a basic "what did engineering change about this serial."
And if a parallel PG release on a child component (drawer slide,
hinge, finish-material) was the actual culprit, the asbuilt has no
trail at all.

**Recommendation.**
1. Add to `southbrook.asbuilt`:
   - `pg_release_id` (related from `production_id.pg_release_id`,
     stored, readonly) — direct link to the PG release if any.
   - `southbrook_eco_history_ids` (m2m, captured at create time
     from `production_id.bom_id.southbrook_eco_history_ids` — the
     ECOs that produced this BoM version).
   - `pg_revision_code` (related from `production_id.pg_revision_code`,
     stored).
   - extension to `_snapshot_build_context` (line 262-287) to also
     snapshot the resolved `southbrook.cut.spec` ID at build time —
     the cabinet might have been built under cut spec rev 7 which is
     now superseded by rev 9, and field service needs spec rev 7's
     reveal value to recreate the discrepancy.
2. ~30 LoC + 1 view edit. Closes the loop W4 → W1 → audit trail
   single-walk.

---

### W5 — CAD diff path is "operator must scan a photo" with zero pipeline (PG-150's "manual workaround")

**Surface.** Per CLAUDE.md §R9 Phase 3 and PG-150 §3 the CAD diff-on-pull
auto-revise engine is deferred — only the `auto_revise_on_change`
flag exists at `product_graph_cad/models/pg_cad_connector.py` as a
stub. PG-150 calls out "what's the manual workaround today?" as an
open question. Looking at the actual code: there is no workaround
path. There is no operator-side button. There is no formal "I found
a CAD discrepancy" entry point.

The reverse-path JTBD ("operator at the CNC, panel won't fit, wants
to scan-photograph the discrepancy and have an ECO drafted with the
note") has zero implementation. The closest things on disk:
- `southbrook_freecad_bridge/` (renders FCStd masters → STEP at
  MO time; one-way, no operator input).
- `southbrook_mrp_kitchen_workcenters/controllers/defect_sheet.py`
  (exists per directory listing; not inspected — but it's a QC
  defect surface, not an ECO surface).
- `southbrook.mi.check` (manufacturing intelligence quality
  checks, again QC not engineering-change).

**Cost.** Operator's options today: (a) call engineering on the
shop phone, (b) leave a sticky on the panel, (c) post in the MO
chatter (which engineering may or may not read). None of these
produce a southbrook.eco draft. None capture the photo. Engineering
finds out by accident.

**JTBD.** "When I'm at the CNC and the panel won't fit, I want to
scan-photograph the discrepancy and have an ECO drafted with my
note, so engineering sees the problem with full context."

**Recommendation (incremental, NOT the full Phase 3 CAD pipeline).**
1. Add `action_raise_eco_from_workorder` button on the WorkOrder
   form for the kitchen station-type — collects: photo via
   `ir.attachment`, free-text "what's wrong", current MO →
   resolved `bom_id`, current operator. ~40 LoC.
2. Creates a `southbrook.eco` in state='open' on the first stage
   with `target_kind='document'` (or 'bom' if `bom_id` matches a
   known versioned template), pre-populates `document_ids`,
   posts the photo + note to chatter, schedules a `mail.activity`
   on the engineering lead.
3. Reuse `mi_check` if defect-sheet route is the right entry
   point; this is a 1-2 day spike, not a Phase-3 program.
4. **Crucially:** if W1 lands, the ECO can pre-populate
   `pg_ebom_id` from `production_id.pg_ebom_id` (the new MO
   stamp), so engineering sees the photo IN THE CONTEXT of the
   PG engineering record — not floating in the configurator BoM.

---

## Supplementary findings

### S1 — `mrp.production.create` race against the release-create order
`product_graph_release/models/mrp_production.py:51-74` stamps
`pg_release_id` from `bom_id` at MO create time. The lookup at
line 63-66 filters on `('state', '=', 'completed')`. If an MO is
created (e.g. by an asynchronous procurement run) DURING the brief
window when a release is in `state='executing'`, the create override
finds no matching `pg.release` and the MO is born without the
stamp. The backfill helper `_stamp_pg_release_from_bom` exists
(line 76-86) but is not wired to any cron. Suggest a daily sweep
that runs `_stamp_pg_release_from_bom` over MOs created in last
24h with `pg_release_id IS NULL AND bom_id IS NOT NULL`. ~15 LoC.

### S2 — `_freeze_open_mos` skips `'to_close'` state
`pg_release.py:506` filters `state IN (draft, confirmed, progress)`.
Odoo 19 added `to_close` as a final pre-done state; an MO that's
been "to_close" for a week (e.g. backlog QC pending) doesn't get
frozen, but it IS still consuming the old BoM. Misalignment with
`southbrook_eco.py:560` which scans `[confirmed, progress, to_close]`
(no `draft`). Settle on a canonical set including all four; PG
side is more conservative which is incorrect for the freeze case.

### S3 — `_check_idempotency` is per-(ebom, revision) but ECOs can re-release the same revision
`pg_release.py:351-378` blocks re-release of the same (ebom_id,
revision_id) pair with a UserError instructing "Use Supersede on
the EBOM." Sound for normal flow. But: if a southbrook ECO of
target_kind='cut_spec' activates a new cut spec, the same PG
revision/EBOM is now governed by different cut math — and the
release flow would correctly refuse to re-release. Today this
doesn't matter (no bridge), but once W1 lands, the bridge needs
either:
- to first **supersede** the EBOM in PG when the cut spec changes,
  generating a new pg.revision with a new pg.ebom, OR
- to embed cut-spec-version into the idempotency key, so a new
  cut spec on the same EBOM triggers a fresh release.
Architecturally cleaner: cut-spec ECOs should auto-spawn a PG
revision-bump for any affected pg.ebom. PG-119 (lossy mapping)
adjacent.

### S4 — `bom_version_snapshot` falls back to 1 silently
`southbrook_asbuilt.py:268-271` reads `bom.southbrook_version` if
the field exists, defaults to 1 otherwise. This is the right
defensive code, but it means an asbuilt sealed BEFORE
`southbrook_plm` is installed shows version=1, while one sealed
after install shows the real version — with no way to distinguish
"never had ECO governance" from "first version of the BoM." Add
a `bom_version_snapshot_source` Char ('plm' / 'unknown' / 'pg-release')
to disambiguate.

### S5 — Planner does not see frozen state in MO list/kanban
`pg_release.py:537-557` does `self.message_subscribe` for
`group_pg_mfg` users on release completion AND posts a chatter
message AND triggers `_cron_followup_frozen_mos` which creates
`mail.activity` records ONE DAY LATER. There is no UI badge on the
MO list view that shows "this MO is frozen on a superseded BoM"
at a glance. The MO inherit at
`product_graph_release/views/mrp_production_inherit_views.xml:30-38`
shows `pg_revision_code` as a column but doesn't compute "your
revision is older than the latest." Add `pg_is_on_superseded_rev`
(stored compute) and a list decoration `decoration-warning="pg_is_on_superseded_rev"`.
~25 LoC. Same idea on the kanban card.

### S6 — `_validate_pre_release` creates products inside validate but doesn't refresh after
`pg_release.py:380-420` calls `_ensure_product(comp)` inside the
"pre-validation" pass for child items lacking a product. The
function creates the product (line 422-454). But the release flow
THEN re-reads `ebom.ebom_line_ids` at line 476 to build the
mrp.bom — the lines from before the create still reference the
old `pg_item.product_id` (False at first read). Because `comp` is
the same recordset and Odoo caches `comp.product_id`, the SECOND
read may or may not pick up the new product depending on the
invalidation. Test under heavy load. (Minor; the integration test
suite is unrun per PG-150 §5.)

### S7 — `frozen_mo_count` is set inside the savepoint but read by view outside
`pg_release.py:259-260` writes `self.frozen_mo_count` and
`self.frozen_mo_ids` inside the savepoint. If the savepoint
rolls back at step 5/6/7 (mrp.bom create / archive / write),
those writes also roll back — the user sees `frozen_mo_count=0`
on the failed release even though `_freeze_open_mos` did execute
and post chatter to the MOs. The chatter posts are in MO records
OUTSIDE the savepoint (separate transaction scope) so they
survive the rollback as orphaned "engineering note" banners on
MOs whose freeze never actually happened. Either post chatter
INSIDE a sub-savepoint that rolls back with the parent, or skip
the chatter until after the parent savepoint commits.

### S8 — `southbrook.cut.spec` has no version field
`southbrook_plm/models/southbrook_cut_spec.py:39-194` tracks state
(draft/active/superseded) but no version number — supersession
chain is "follow the chatter" only. The asbuilt `bom_version_snapshot`
captures `mrp.bom.southbrook_version` but the cut-spec at build
time (which influences panel dimensions per
`southbrook_plm/models/mrp_bom.py:106-126`) is not snapshotted at
all. Two cabinets built 6 months apart with different cut specs
will look identical in the asbuilt record but cut to different
dimensions. Add `cut_spec_version = fields.Integer()` to
`southbrook.cut.spec` and snapshot on asbuilt.

### S9 — `southbrook.eco.action_reject` correctly blocks rejecting an applied ECO; PG's `action_reject` does not have an equivalent guard
`southbrook_eco.py:294-351` (the comments around line 311-330) document
a beautiful bug-find from 2026-06-01: rejecting an already-applied
ECO silently flipped state while leaving the apply-effect in place.
PG's `pg.eco.action_reject` at `product_graph_eco/models/pg_eco.py:522-543`
only allows reject from state='approval' (the `_check_transition`
on line 526). But there's no equivalent guard against
`action_reset_to_draft` on a released ECO — verify by reading
`pg_eco.py:577-583`: only `('analysis',)` is the from-state, so
released ECOs can't reset. Good. Different shapes of the same
trap; both teams independently noticed; both are now safe.

### S10 — `pg_release.py:559-581` `_notify_affected_pos` mentions MG-2 but only posts chatter
The cron `_cron_followup_frozen_mos` exists for MOs. There's no
equivalent for POs. The chatter posted at line 573-581 will not
generate a `mail.activity` to the buyer; it sits in the chatter
of the PO and may not be seen. Mirror the cron pattern for POs in
state in `('draft', 'sent', 'to approve', 'purchase')` to surface
as activities to the buyer group.

---

## What's good (carry-forward strengths)

1. **R1 keystone is sacred.** `pg.release.action_execute_release`
   (`pg_release.py:209-338`) is the single sanctioned mrp.bom writer,
   the savepoint discipline at line 239 is correct, the
   provenance-fields-via-mrp.bom-inherit pattern at
   `mrp_bom.py:23-38` is exactly the right idiom. R1 is genuinely
   non-negotiable in the code, not just the docs.
2. **MO traceability stamp is automatic.** The create override at
   `mrp_production.py:51-74` means a planner does ZERO clicks to get
   `pg_revision_code` on a new MO — it materialises from `bom_id`.
   When the bridge is wired, this extends to southbrook flows for free.
3. **Frozen-MO follow-up is a real cron, not vapor.** `_cron_followup_frozen_mos`
   (`pg_release.py:587-650`) actually creates `mail.activity` records
   on the affected MOs (Mfg user as assignee). Idempotent via the
   `summary='PG-FROZEN%'` dedup. This is the kind of bookkeeping
   that almost always gets skipped.
4. **Southbrook ECO's terminal-stage guard at `southbrook_eco.py:191-238`**
   is a fantastic defense against the workflow-bypass pattern (write
   stage_id directly to a terminal stage). Demo bug found and fixed.
5. **Cut-spec activation pattern (`southbrook_cut_spec.py:177-194`)**
   is the right shape: one active record, supersession via state
   flip, audit via chatter, idempotent re-activate. The
   `_check_single_active` constraint at line 123-133 is a portable
   alternative to a partial unique index — well chosen.
6. **`southbrook.asbuilt.qc_overall_result` compute (line 211-233)**
   correctly distinguishes 'pass' vs 'conditional (rework done)' vs
   'fail' — the conditional bucket means a defect that was reworked
   to spec is auditable separately from a defect that's still open.
7. **The bridge SPEC itself is rigorous.** Its "MUST NOT" list at
   `bridge_spec/SPEC.md:45-48` correctly forbids the bridge from
   adding fields to `pg.item`, `pg.revision`, etc. — preserving PG's
   publishable LGPL shape. When (if) it's implemented, the discipline
   pre-exists.

---

## Baseline tables

### Forward path — release → planner → MO (click count)

| Step | Actor | Surface | Clicks | Information density |
|---|---|---|---|---|
| 1. Finalize revision | Engineer | `pg.revision` form, draft → review → released | 2 (Promote) | High — releases blocked by validation, chatter explains why |
| 2. Author EBOM | Engineer | `pg.ebom` form, x2many lines | N+1 (1 per line + Release EBOM) | High — closure rebuild advisory-locked |
| 3. Open release wizard | Approver | "Release to MRP" smart button on EBOM (`pg_release_views.xml:155-158`) | 1 | High — pre-flight validation HTML lists every issue |
| 4. Confirm + execute | Approver | Wizard "Confirm Release" button | 2 (reason text + button) | High — `validation_html` enumerates blockers + warnings |
| 5. Planner sees BOM | Planner | `mrp.bom` list, filter by `pg_release_id IS NOT NULL` | (not surfaced as default filter) | Low — no badge on list; `pg_release_id` column hidden by default |
| 6. Planner creates MO | Planner | `mrp.production`, pick bom_id | 0 extra (stamp is automatic) | Med — `Eng Rev` column visible (optional="show") but `pg_release_id` hidden |
| 7. Operator at MO | Operator | MO form | 0 to see, ∞ to act | Low — `pg_revision_code` is read-only; no link to engineering doc |
| **Total** | | | **5+N clicks** | |

### Forward path — Southbrook ECO → cabinet shop floor

| Step | Actor | Surface | Clicks | Information density |
|---|---|---|---|---|
| 1. Author cabinet ECO | Engineer | `southbrook.eco` form | 4-6 (title, type, target, description, advance, approve) | High — change_type + target_kind drive validation |
| 2. Apply (BoM kind) | Approver | `southbrook.eco.action_apply` | 1 | High — copies BoM, archives prior, scans affected MOs |
| 3. Affected MOs flagged | (auto) | `affected_mo_ids` m2m + chatter post on each | 0 (auto) | Med — chatter on each MO; **no cron follow-up** |
| 4. Operator sees ECO note | Operator | MO chatter | 0 to see | Med — banner present; no link to ECO from MO form by default |
| 5. As-built sealed at MO done | (auto) | `southbrook.asbuilt` auto-created `southbrook_mrp_kitchen_workcenters/models/mrp_production.py:183-204` | 0 | **Low — no PG revision link, no ECO link, only bom_version_snapshot** |
| **Total** | | | **5-7 clicks** | |

### Reverse path — operator finds problem → engineering sees it

| Step | Actor | Surface | Clicks | Notes |
|---|---|---|---|---|
| 1. Discover defect | Operator | (no formal entry point) | — | Today: call engineering OR post in MO chatter |
| 2. Capture context (photo, dimensions) | Operator | `ir.attachment` (manual upload to MO chatter) | 3+ | No camera-direct workflow |
| 3. Engineer notified | (passive) | Follower on MO | 0 | Only if engineer is a follower; not by default |
| 4. ECO drafted from defect | Engineer | New `southbrook.eco` or `pg.eco`, manual data entry | 6-8 | **No "from MO" prefill** |
| 5. MO link captured on ECO | (none today) | — | — | `southbrook.eco` has no `production_id` field; `pg.eco` has no `production_id` field |
| **Total** | | | **9-11+ clicks, with manual transcription** | |

### Lost-in-translation surface

| Datum | Authored at | Consumed at | Loss path |
|---|---|---|---|
| Cut-spec reveal (mm) | `southbrook.cut.spec` (singleton-active) | `mrp.bom._get_cut_constants` | If spec changes mid-MO, the consumed value is whatever was active at compute time, not at MO start. Not snapshotted. |
| `pg.revision.incompatible_with_previous` | PG revision flag | PG release annotation, audit log payload | Surfaces nowhere in the southbrook label, southbrook MO, southbrook asbuilt. Warehouse-side enforcement noted as deferred per PG-150 §3. |
| ECO `description` (the WHY) | Engineer in HTML | Chatter post on affected MOs | Truncated/styled differently; HTML in chatter does not render inside cabinet label PDF |
| PG `pg.eco.line.change_kind` | PG ECO line | Promotion gating in PG | Never surfaces to southbrook MO; planner doesn't know whether the change is corrective vs adaptive vs regulatory |
| `southbrook.eco.target_kind == 'document'` | Southbrook ECO type | Engineering doc updated | The MO that consumes the document gets no notification; document ECOs are silent to shop floor |

---

## Recommended next 7 tickets (R4 scope)

In priority order. Estimates are after R5/R6 are settled — this list assumes
no overlap with the quality/sourcing agent's recommendations.

1. **R4-001 — Decide W1 (bridge implement vs retire).** Spec patch +
   D1 amendment + elearning slide patch. 0.5 day either way. **Unblocks
   R4-002, R4-004, R4-006.**
2. **R4-002 — Cabinet label rev code + QR-embedded rev (W2).** 1 day.
3. **R4-003 — Asbuilt PG back-references (W4).** 1 day after R4-001
   decided.
4. **R4-004 — Frozen-MO correlator + unified state set (W3 minimal).**
   0.5 day even without W1.
5. **R4-005 — Operator "raise ECO from WO" button (W5).** 1-2 days.
6. **R4-006 — `pg_is_on_superseded_rev` MO list/kanban badge (S5).**
   0.5 day.
7. **R4-007 — `mrp.production.create` race backfill cron (S1).**
   0.5 day; or fold into the existing `_cron_followup_frozen_mos`.

Total: ~5 person-days for the R4 slice, dwarfed by the operational
visibility unlock per cabinet shipped.

---

*Generated 2026-06-27 by R4 review agent. Audit run on commits at
HEAD of both `~/product_graph_v19/` (Phase 2 landed per PG-150) and
`~/southbrook-v19cr/` (southbrook_plm 2026-06-26 commits inclusive).
Cross-references to other R-stream reviews intentionally light — this
slice is the interface only; the planner/operator/quality/sourcing
agents own the surfaces this seam feeds into.*
