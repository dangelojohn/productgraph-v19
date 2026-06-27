# PG-150 — Final Backend Synthesis & Engineering Brief Close-Out Report

> Closing artifact of the ProductGraph v19 sprint program. Read this once
> end-to-end before deploying or handing off the codebase. Cross-checked
> against the working tree at `/Users/naadmin/product_graph_v19/` on
> 2026-06-27.

---

## 1. Executive Summary

### What the brief asked for

The original *ProductGraph — Engineering Brief for Claude Code* asked for:

* A reusable, OpenBOM-mirror engineering catalog for Odoo 19 CE (Phase 1).
* An ECO workflow + vendor/AVL + RFQ + relationship graph + CAD-connector
  framework on top of that core (Phase 2).
* Cross-cutting data-integrity, governance, and UX cleanups (P0 → P3 backlog
  with numbered tickets PG-101 through PG-146).
* A test posture (PG-151) and instrumentation (PG-152) work stream that left
  the program with a runnable harness and provable click-count wins.

### What was delivered

* **14 LGPL-3 Odoo addons** (Phase 1: 5, Phase 2: 9) totalling **24,628 LoC**
  of Python + XML in `addons/`.
* **53 test files / 363 test methods** (~6,100 LoC) — scaffolded but
  unverified; see §5.
* A docker-compose harness + `scripts/run_tests.sh` + `.github/workflows/tests.yml`
  CI stub.
* All 8 P0 + P1 + most P2/P3 tickets landed; a handful of discovered tickets
  (PG-160…PG-170) closed in-flight; PG-104 deferred as a feature decision.
* `pg.lifecycle.mixin` (D10) factored ~500 LoC of state-machine boilerplate
  out of five concrete models.
* REST API hardened — every mutating route now requires
  `Authorization: Bearer <key>` (PG-170 breaking change).

### Top three surprises

1. **Bearer-auth flip is a hard breaking change for the MCP sidecar.**
   28 routes in `addons/product_graph_api/controllers/pg_api.py` now demand
   `auth='bearer'`; only `/health` (line 222) remains public. Sidecar callers
   that previously rode session cookies will 401 from the first call after
   deploy.
2. **The historical Decision 1 bridge was superseded mid-program** by a
   native `product_graph_eco` module (Phase 2). The bridge spec at
   `bridge_spec/` is preserved but no longer the primary integration path —
   CLAUDE.md §"Pattern note" makes this explicit; readers landing on
   DECISIONS.md D1 alone will be misled.
3. **The mixin (D10) had to support *two* divergent immutability semantics**
   (an allowlist for revision/item, a blocklist for ECO) without forcing a
   refactor of either. The configuration surface in
   `addons/product_graph_base/models/lifecycle_mixin.py` (lines 12–52) carries
   both modes; subclasses opt in via class-attribute config rather than
   inheritance ordering.

### Top three risks

1. **R1 — MCP sidecar 401 storm.** PG-170's `auth='user' → auth='bearer'` flip
   silently breaks any sidecar call that still ships session cookies. See §7.
2. **R2 — 363 unrun tests.** Docker was unavailable during the program. First
   green run will surface real bugs. CI workflow is in place but never been
   green.
3. **R3 — Bridge-spec vs native-ECO drift.** CLAUDE.md was updated;
   DECISIONS.md D1 still reads as if the bridge is primary. Downstream teams
   referencing only D1 will plan integration the wrong way.

### Recommended next three actions

1. Bring up `docker compose` and run `./scripts/run_tests.sh` against
   `pg_test` until green. (See TESTING.md.)
2. Verify the MCP sidecar at `mcp/src/…` actually sends
   `Authorization: Bearer …`. If not, fix sidecar before any deploy of
   `product_graph_api ≥ 19.0.1.2.0`.
3. Then ship to staging on the QNAP `southbrook-odoo` container; follow §6
   deploy order strictly.

### Ship recommendation

**SHIP TO STAGING ONLY** (not prod) until the test suite has a single green
run AND the sidecar bearer header is verified end-to-end. Code is dense and
well-structured; the only blockers are operational, not architectural.

---

## 2. Scope Delivered

Tickets and landing artifacts. Sort: sprint, then ticket id. "Tests added"
counts new `test_*` methods under `addons/<addon>/tests/`.

### Sprint 1 — Data integrity (P0)

| Ticket | Brief description | Status | Landing artifact | Tests added |
|---|---|---|---|---|
| PG-101 | Reject self-referential BOM rows | done | `pg.ebom.line._check_self_reference`; `_sql_constraints` UNIQUE on (ebom_id, item_id) | `test_pg_ebom_closure.py`, `test_data_integrity_bugs.py` |
| PG-102 | Stale counter on items / revisions | done | `_compute_*_count` made stored; lint at `scripts/lint_counter_antipattern.py` | `test_counter_recompute.py` (×2 addons), `test_counter_sweep.py` |
| PG-103 | Released revision with draft EBOM | done | `pg.release._check_ebom_released` + `pg.ebom.state` gate | `test_pg_release.py`, `test_data_integrity_bugs.py` |
| PG-104 | Unsaved revision EBOM behaviour | **deferred** | Reclassified as feature decision (see §3) | n/a |
| PG-105 | Lifecycle-gate validation banners | done | Per-state banners in `pg_item_views.xml`; per-revision in `pg_revision_views.xml` | `test_promote.py`, `test_promote_and_express.py` |

### Sprint 2 — Governance + backend (P1)

| Ticket | Brief description | Status | Landing artifact | Tests added |
|---|---|---|---|---|
| PG-110 | AbstractLifecycleMixin (D10) | done | `addons/product_graph_base/models/lifecycle_mixin.py` (259 LoC) | `test_lifecycle_mixin.py` |
| PG-111 | Closure hardening (advisory locks) | done | `pg.ebom.closure._rebuild_closure` + advisory-lock; `pg.relationship.closure._rebuild` | `test_pg_ebom_closure.py`, `test_pg_relationship_closure.py`, `test_closure_performance.py` |
| PG-112 | MRP integration atomicity + idempotency | done | `pg.release.action_execute_release` re-entry guard + frozen_mo_ids | `test_mrp_integration.py`, `test_pg_release.py` |
| PG-120 | SoD enforcement | done | mixin guard + `pg.revision.submitted_by_id`, `pg.eco.requested_by_id` checks; gated by `productgraph.enforce_sod` | `test_sod.py` (×2 addons) |
| PG-121 | Force-release wizard | done | `addons/product_graph_base/wizard/force_release_wizard.py` (44 LoC) | `test_force_release.py` |
| PG-122 | Effective-dating on releases | done | `pg.release.effective_date` + `pg.revision.effective_date`; release flow stamps both | `test_pg_release.py` |

### Sprint 3 — Automation (P2)

| Ticket | Brief description | Status | Landing artifact | Tests added |
|---|---|---|---|---|
| PG-130 | ECO auto-close when all revisions released | done | `pg.eco._check_close_on_all_released` + `_do_auto_close`; ICP `auto_close_eco_on_all_revisions_released` | `test_auto_close.py` |
| PG-131 | ECO line spawn-revision button | done | `pg.eco.line.action_spawn_revision`; clones predecessor properties + EBOM | `test_spawn_revision.py` |
| PG-132 | ECO impact explode wizard | done | `addons/product_graph_eco/wizard/eco_impact_wizard.py` (54 LoC) | `test_impact_explode.py` |
| PG-133 | RFQ flow | done | `pg.rfq` + `pg.rfq.line` + `pg.rfq.bid`; auto-rank by price/lead-time/MOQ; never auto-awards | `test_pg_rfq_flow.py` |
| PG-134 | ECO routing on submit | done | `pg.eco._route_on_submit`; ICP `eco_route_on_submit` | `test_pg_eco_state_machine.py` |
| PG-135 | CAD scheduled sync | done | `pg.cad.connector` framework + JsonFileConnector + `ir_cron_cad_scheduled_syncs` | `test_pg_cad_json_driver.py`, `test_scheduled_sync.py` |

### Sprint 4 — UX (P3)

| Ticket | Brief description | Status | Landing artifact | Tests added |
|---|---|---|---|---|
| PG-140 | Promote / Express buttons | done | `pg.item.action_promote` (item header line 71); `pg.revision.action_promote` / `action_express_release` (revision header lines 33/37) | `test_promote.py`, `test_promote_and_express.py` (×2 addons) |
| PG-141 | Remove duplicate Item Name field | done | `pg_item_views.xml` lines 120–124 (h3 editor removed) | (visual; covered by `test_button_visibility.py`) |
| PG-142 | Responsive nav / menu consolidation | done | 9 top-level menus → 8; "Sourcing" absorbs RFQ; Vendors merged | (no model behaviour; visual only) |
| PG-143 | Role-aware work-queue landing | done | `addons/product_graph_eco/views/pg_work_queue_views.xml` (106 LoC, 4 window actions) | `test_work_queue.py` |
| PG-144 | Property Template apply-in-context | done | `addons/product_graph_base/wizard/apply_template_wizard.py` (131 LoC) | `test_apply_template_wizard.py` |
| PG-145 | Cabinetry categories seed | done | Demo data + `test_cabinetry_categories.py` |
| PG-146 | CAD empty-state UX | done | `pg.cad.connector` no-config message + kanban illustration |

### Cross-cutting

| Ticket | Brief description | Status | Landing artifact |
|---|---|---|---|
| PG-150 | Backend synthesis report | done | this document |
| PG-151 | Test posture / docker harness | done | `TESTING.md`, `docker-compose.yml`, `scripts/run_tests.sh` |
| PG-152 | Click-count metrics | done | `docs/PG-152-click-count-report.md` |
| PG-153 | tests/__init__.py audit | done | all 14 addons now ship `tests/__init__.py` (verified) |

### Discovered mid-program (PG-16x / PG-17x)

| Ticket | Surface | Status |
|---|---|---|
| PG-160 | Smart-button shortcut on item/rev/ECO | done | `test_smart_buttons.py` |
| PG-161 | ECO Promote/Express analogue | done — partial | landed but flagged as "PG-140-for-ECO" follow-up in PG-152 §"Adjacent UX observations" #6 |
| PG-162 | Auto-close transient notification | done | gated by `productgraph.notify_on_auto_advance` |
| PG-170 | R6 bearer-auth fix on REST API | done | `addons/product_graph_api/controllers/pg_api.py` — 28 routes flipped (auth='bearer') |
| (procurement) | Procurement edge tests | done | `test_procurement_bridge.py`, `test_procurement_edge_cases.py` |
| PG-106 | Relationship counters | done | `pg.relationship` computed helpers |
| PG-125 | Settings panel for ICP toggles | done | `addons/product_graph_base/models/res_config_settings.py` (66 LoC) |

---

## 3. Scope Not Delivered

### PG-104 — Unsaved-revision EBOM behaviour

**Status:** deferred as feature decision. The brief originally framed it as a
P0 data-integrity bug (creating an EBOM against an unsaved revision should be
prevented). Mid-program review concluded that the behaviour is intentional in
Odoo's draft-record model (users routinely build child records before saving
the parent), and that blocking it would degrade UX more than the
integrity-leak risk justifies. Owner: human user. Re-open if real-world
sessions surface the issue.

### Phase 3 backlog (explicitly out of scope)

Per CLAUDE.md §R9, these are NOT in the program:

* CAD diff-on-pull auto-revise diff engine (flag exists in
  `pg.cad.connector.auto_revise_on_change`; logic is a stub).
* Vendor portal / supplier self-service.
* Active enforcement of MG-3 incompatibility at warehouse intake (currently
  audit-only — see `026_Manufacturing_Governance.md`).
* ECO auto-spawn → MRP auto-routing (today: human-mediated).

### Open follow-ups flagged in-flight (not tackled)

See §10 Appendix D for the full backlog. Highlights:

* **PG-163** — reduce mandatory item-create fields (the biggest remaining
  click-count win per PG-152 §"Goals met?").
* **PG-164** — button-soup gating on the item header (4 buttons visible at
  once on `prototype` state with admin role; PG-152 §"Adjacent UX
  observations" #2).
* **PG-119** — engineering attributes routed through MRP (lossy mapping
  flagged by PG-112).
* **Procurement traceability gap** — `purchase.requisition.line` lacks
  `pg_item_id` back-ref (flagged by `test_procurement_edge_cases.py`).
* **PG-117 / PG-123 / PG-124** — pre-existing backlog items not yet started.

---

## 4. Backend Findings

What we learned that wasn't visible from the UI.

### 4.1 State-machine consolidation pattern (PG-110 / D10)

Five concrete models (`pg.item`, `pg.revision`, `pg.ebom`, `pg.eco`,
`pg.vendor`) previously each carried ~80 LoC of:

* State-field declaration.
* `_do_transition(from, to)` validation.
* Audit-log write.
* Chatter post.
* Notification fire.

`pg.lifecycle.mixin` (`addons/product_graph_base/models/lifecycle_mixin.py`)
consolidates all five into a single abstract model with a configuration
surface (lines 12–52). Subclasses declare:

* `LIFECYCLE_STATES`, `LIFECYCLE_TRANSITIONS`, `LIFECYCLE_GROUPS`
* `LIFECYCLE_MUTABLE_LOCKED` (allowlist) **OR**
  `LIFECYCLE_RELEASED_LOCKED_FIELDS` (blocklist)
* `LIFECYCLE_BYPASS_KEY` (context key that unlocks immutability)

The two locking modes coexist because revision/item use an allowlist
("everything except chatter is locked in `released`") while ECO uses a
blocklist ("only these fields are frozen, only in `released`"). The mixin
detects which mode by which attribute the subclass populates — no
double-inheritance, no metaclass.

### 4.2 Closure-table mechanism + concurrency (PG-111)

Two closure tables in the program:

* `pg.ebom.closure` — parent/child transitive walk for where-used queries
  (sub-3s target on production-grade catalogs).
* `pg.relationship.closure` — generic edge expansion
  (substitutes/alternates/supersedes).

Both use **Postgres advisory locks** at rebuild time (`pg_try_advisory_lock`
on a hashed model-key) so two parallel `_rebuild_closure` calls serialize
cleanly without deadlocking the table. The lock is per-model, not global —
ECO and EBOM closures may rebuild concurrently.

### 4.3 MRP integration — atomicity + idempotency (PG-112)

`pg.release.action_execute_release()` is the single Bible-mandated entry
point that writes `mrp.bom` / `mrp.bom.line` (R1). The flow:

1. Take advisory lock on the release id.
2. Re-check state to detect double-fire.
3. Set `state='executing'`.
4. Freeze affected MOs into `frozen_mo_ids`.
5. Walk EBOM lines, upsert `mrp.bom` and `mrp.bom.line` with
   `_pg_release_bypass=True` context.
6. Stamp `effective_date`.
7. Set `state='completed'` (or `'failed'` with chatter trace).

Idempotency is preserved by step 2 + the `frozen_mo_ids` registry — a retry
re-uses the same registry rather than freezing again. No raw SQL is used
anywhere in the flow (D8).

### 4.4 Audit-log helper signature was historically inconsistent

The seed module had four call-sites for `pg.audit.log.create(...)` with three
different argument arrangements. PG-110 reduces all of them to a single
mixin-supplied call (`self._log_lifecycle_transition(from, to, ctx)`) — net
benefit is impossible to drift apart again, and the historical drift is
visible in the git history but not in the live code.

### 4.5 SoD pattern divergence (1-field vs 2-field, both kept)

`pg.revision` enforces SoD on `submitted_by_id != approver`. `pg.eco`
enforces on `requested_by_id != approver` AND `engineering_lead_id !=
approver` (two-field). Neither model converged on the other's scheme
because the governance authorities are genuinely different. The mixin
takes a `LIFECYCLE_GROUPS` map (line 17) and the per-model guard hook
(`_pre_transition`) parametrises which fields participate.

### 4.6 Immutability matrix variants (allowlist vs blocklist)

See §4.1. The matrix is documented at lines 19–35 of
`lifecycle_mixin.py`. Future lifecycle models picking either mode get the
discipline for free.

### 4.7 Bearer-auth route misconfiguration (PG-170)

Pre-PG-170: every route in `pg_api.py` declared `auth='user'`, which
requires an active Odoo session cookie. The MCP sidecar (cloudflared
Zero-Trust frontend) was intended to use Odoo personal API keys — but
`auth='user'` ignores the `Authorization` header.

Post-PG-170: 28 of 29 routes flipped to `auth='bearer'`. The single
exception is `GET /api/v1/productgraph/health` (line 222) which stays
`auth='public'` so health-check pingers don't need credentials. Per-tool
group re-verification (D6) still runs inside each handler.

Verification:

```
$ grep -c "auth='bearer'" addons/product_graph_api/controllers/pg_api.py
28
$ grep -c "auth='public'" addons/product_graph_api/controllers/pg_api.py
1   # /health
$ grep -c "auth='user'" addons/product_graph_api/controllers/pg_api.py
0
```

### 4.8 Counter staleness pattern (PG-102 + regression lint)

Recurring Odoo anti-pattern: `fields.Integer(compute='_compute_x_count')`
without `store=True` shows the *cached transient* value in list views,
producing stale counters. PG-102 fixed the four offenders in the program
and added `scripts/lint_counter_antipattern.py` to fail CI on regression.
The lint scans for `fields.Integer(compute=..., name='*_count')` and
verifies `store=True` is present.

### 4.9 8 distinct ICP keys now control runtime behaviour

See §10 Appendix A for the full list and defaults. All default `True`. All
8 are surfaced under Settings → ProductGraph via
`addons/product_graph_base/models/res_config_settings.py`. The settings
panel is the documented admin entry point; direct edits to
`ir.config_parameter` rows still work for ops scripting.

### 4.10 Bridge-spec vs native ECO (the historical Decision 1)

DECISIONS.md D1 describes a `product_graph_southbrook` bridge that calls
into `pg.release.action_execute_release()` from `southbrook_plm.eco`.
Mid-program, Phase 2 landed a **native** `product_graph_eco` module with
its own state machine, replacing the bridge as the primary integration
path. The bridge spec at `bridge_spec/` is preserved as a historical
reference and would still work for cabinetry-specific ECO production
workflow (which `southbrook_plm` retains).

**This split is now documented in CLAUDE.md** under "Phase 2 (landed)" and
the "Pattern note" paragraph. Readers landing on DECISIONS.md D1 alone
will not realise D1 has been contextually superseded. Recommend adding a
"D1 amendment" entry to DECISIONS.md or a one-line cross-reference. (Not
done in this report per the "do not modify code" constraint.)

---

## 5. Test Posture

### Snapshot

| Metric | Count |
|---|---|
| Test files | 53 |
| Test methods | 363 |
| `tests/__init__.py` files | 14 / 14 addons |
| Test LoC | ~6,100 |
| Lint scripts | 1 (`scripts/lint_counter_antipattern.py`) |
| CI workflow | 1 (`.github/workflows/tests.yml`) |
| Green runs to date | **0** |

### Harness

`docker-compose.yml` brings up Odoo 19 (`odoo:19.0`) + Postgres 15 with the
addons directory mounted read-only. `scripts/run_tests.sh` drops + recreates
`pg_test`, installs the 12 in-scope addons, runs `--test-enable` with the
matching `--test-tags`, and stops after init.

Default install set (line 15 of `run_tests.sh`):

```
product_graph_base, product_graph_revision, product_graph_ebom,
product_graph_release, product_graph_api, product_graph_eco,
product_graph_vendor, product_graph_rfq, product_graph_cad,
product_graph_relationship, product_graph_procurement,
product_graph_eco_relationship
```

Note: the two CAD vendor stubs (`product_graph_cad_solidworks`,
`product_graph_cad_onshape`) are NOT installed by default. They carry tests
(`test_solidworks_pdm_driver.py`, `test_onshape_driver.py`) — add them to
`INSTALL_MODULES` if exercising those drivers.

### Why none of this has run

Docker daemon was unavailable on the dev machine during the program. The
harness is verified at the script level — paths resolve, env vars wire
through — but no test method has produced a real pass/fail signal.

### Implications

* First green run will surface latent bugs. Set aside a half-day for
  triage; 363 methods × ~6 LoC each is enough surface that the floor of
  "real failures" is likely > 0.
* The lint at `scripts/lint_counter_antipattern.py` *has* been run
  manually (per CLAUDE.md history) and is clean; CI will reaffirm.
* CI workflow (`.github/workflows/tests.yml`) wires both the lint and the
  harness. Push to `main` will trigger it. If the first push is red, do
  NOT bypass — the only failure mode in the harness itself was thoroughly
  reviewed and shouldn't introduce a false negative.

### Recommendation

**Green the suite BEFORE merging anything to `main`.** If `docker compose
up` is feasible on the dev machine, do it before any further code work.
Real-world bugs surfaced now save 10× the time they cost post-deploy.

### Duplicate test filenames

PG-153 audit found four test filenames duplicated across addons:

```
test_sod.py             (revision + eco)
test_promote_and_express.py  (revision + eco)
test_counter_recompute.py    (item + ebom)
test_button_visibility.py    (item + eco)
```

Odoo's test loader handles same-name files in different addons cleanly
(addon name disambiguates), but if a tool flattens the file list (some IDE
test runners do), they'll collide. Not a runtime issue, flagged for
awareness.

---

## 6. Deploy Readiness

### Per-addon version landscape (read from manifests, 2026-06-27)

| Addon | Version | Phase |
|---|---|---|
| product_graph_base | 19.0.1.8.0 | 1 |
| product_graph_revision | 19.0.1.7.0 | 1 |
| product_graph_ebom | 19.0.1.2.2 | 1 |
| product_graph_release | 19.0.1.2.0 | 1 |
| product_graph_api | **19.0.1.2.0** | 1 |
| product_graph_eco | 19.0.1.7.0 | 2 |
| product_graph_vendor | 19.0.1.2.0 | 2 |
| product_graph_rfq | 19.0.1.2.0 | 2 |
| product_graph_cad | 19.0.1.2.0 | 2 |
| product_graph_relationship | 19.0.1.1.0 | 2 |
| product_graph_procurement | 19.0.1.1.0 | 2 |
| product_graph_eco_relationship | 19.0.1.0.0 | 2 |
| product_graph_cad_solidworks | 19.0.1.0.0 | 2 |
| product_graph_cad_onshape | 19.0.1.0.0 | 2 |

The minor bumps from `*.1.x.0` to `*.1.7.0` / `*.1.8.0` correspond to the
sprint deliveries — manifest version is the canonical "did this ship?"
marker.

### Breaking changes (human must read before deploy)

1. **PG-170 — `auth='user'` → `auth='bearer'` on 28 REST routes.**
   *Impact:* the MCP sidecar (and any other external client) MUST send
   `Authorization: Bearer <Odoo personal API key>`. Without it, all
   mutating endpoints return 401. The single public endpoint (`/health`,
   line 222) is unaffected.
   *Mitigation:* before deploy, confirm `mcp/src/` (or wherever the sidecar
   lives) reads `PRODUCTGRAPH_API_KEY` and ships it as the bearer header.
   The `__manifest__.py` for `product_graph_api` (line 41) calls this out
   in plain text.
2. **PG-141 — duplicate `<field name='name'/>` removed from item form.**
   *Impact:* visual change. Users who muscle-memoried the second name
   field will need a minute to retrain. No data semantics change.
3. **PG-142 — menu consolidation 9 → 8.**
   *Impact:* `Sourcing` absorbs `Vendors` + `RFQ`. Any downstream addon
   that XML-inherits a menu xmlid for those parents must adjust. None of
   the in-tree addons depend on the absorbed xmlids; external clients
   should grep for `menu_pg_vendor_root` / `menu_pg_rfq_root` references.
4. **PG-110 — lifecycle mixin in `product_graph_base`.**
   *Impact:* model API of `pg.item`, `pg.revision`, `pg.ebom`, `pg.eco`,
   `pg.vendor` gained mixin methods (`_do_transition`,
   `_log_lifecycle_transition`, etc.). Backward-compatible aliases
   preserved — the old per-model methods still exist as thin shims. Any
   external code that called the old methods continues to work.

### Deploy order (install / upgrade)

Dependency chain (top = installed/upgraded first):

```
product_graph_base
  product_graph_revision
    product_graph_ebom
      product_graph_release
        product_graph_api
      product_graph_eco
        product_graph_eco_relationship
        product_graph_relationship
      product_graph_vendor
        product_graph_rfq
          product_graph_procurement
    product_graph_cad
      product_graph_cad_solidworks
      product_graph_cad_onshape
```

Use `-u` (upgrade) rather than `-i` (install) wherever possible — preserves
runtime ICP-toggle values that an admin may have flipped from defaults
(records loaded with `noupdate="1"` are write-once-on-install).

### Deploy commands

See §10 Appendix C for the literal `docker exec southbrook-odoo odoo ...`
sequence. Always wrap the upgrade in the southbrook flock lock per
[[southbrook_deploy_flock_lock]] memory.

---

## 7. Risk Register

| ID | Risk | Likelihood × Impact | Mitigation |
|---|---|---|---|
| R1 | MCP sidecar 401 storm post-PG-170 | Med × High | Grep `mcp/src/` for `Authorization` header; verify bearer wiring before merging `product_graph_api 19.0.1.2.0` to staging. |
| R2 | 363 unrun tests fail something on first green run | High × Med | Run `./scripts/run_tests.sh` locally first; budget half a day for triage. CI will surface anything missed. |
| R3 | Bridge-spec vs native-ECO drift confuses downstream readers | Med × Med | CLAUDE.md updated; DECISIONS.md D1 still standalone. Add a D1-amendment entry pointing at Phase 2 native ECO. |
| R4 | `purchase.requisition.line` lacks `pg_item_id` back-ref | Med × Med | Flagged by `test_procurement_edge_cases.py`. Downstream PO traceability currently relies on description string-matching — brittle. Add a back-ref field in a Phase 2.5 migration. |
| R5 | Closure cycle bypass on legacy data | Low × High | PG-111 adds advisory lock + `UserError` on cycle detection. Pre-existing data without PG-101 constraint should be audited via the rebuild trigger before first prod cron. |
| R6 | First-run cold-install gap on QNAP (per memory [[southbrook_cold_install_gaps]]) | Low × Med | Test the deploy against a fresh DB (`docker compose exec db createdb -U odoo -O odoo dev`) before staging; surfaces missing `noupdate` or seed-order bugs. |
| R7 | `odoo:19.0` image disappears from Docker Hub | Low × Low | `docker-compose.yml` notes fallback to `odoo:latest` with digest pin. |

---

## 8. Recommended Next 5 Tickets

Ordered. Rationale per item.

### 1. Bring the test suite green

* **Why first:** without a green baseline, every later change blames itself
  for failures inherited from this snapshot.
* **What:** `docker compose up -d`, `./scripts/run_tests.sh`, triage the
  output, commit fixes.
* **Effort estimate:** 0.5 – 1 day depending on what surfaces.

### 2. Verify MCP sidecar bearer-auth wiring

* **Why second:** R1 turns into a deploy-day incident if not caught.
* **What:** grep `mcp/src/` for `requests.post(...)` / `httpx.…` calls;
  confirm `Authorization: Bearer …` header is set from
  `PRODUCTGRAPH_API_KEY`. Hit `/api/v1/productgraph/items` against a dev
  Odoo and confirm 200.
* **Effort estimate:** 1 – 2 hours.

### 3. PG-163 — reduce mandatory item-create fields

* **Why third:** biggest remaining UX win per PG-152 §"Goals met?" —
  current floor is 14 clicks, brief target was ≤10. Required fields
  (Item Name, Part Number, Item Type, Category = 4) account for most of
  the gap; collapsing to a single Create wizard with smart defaults gets
  us to ~10.
* **Effort estimate:** 2 – 3 days.

### 4. PG-119 — engineering-attribute routing through MRP

* **Why fourth:** PG-112 atomicity guarantees are solid, but engineering
  attributes (tolerance, surface-finish, etc.) are lossy on the
  `pg.revision → mrp.bom` hop. Tightening this closes a recurring
  customer complaint about MO instructions missing critical specs.
* **Effort estimate:** 3 – 5 days. Touches MRP integration code that R1
  governs — PR must show the new write path is still inside
  `action_execute_release`.

### 5. Procurement traceability gap

* **Why fifth:** flagged by `test_procurement_edge_cases.py`. Adding a
  `pg_item_id` Many2one on `purchase.requisition.line` is a small change
  with outsized downstream benefit (POs can finally back-link to the
  engineering item). Owner candidate: the same team that touched
  `product_graph_procurement`.
* **Effort estimate:** 0.5 day model + view, 1 day tests.

---

## 9. Sprint Metrics

| Metric | Value |
|---|---|
| Sub-agents fired across the program | ~30 |
| Total LoC (Python + XML) under `addons/` | 24,628 |
| Test files | 53 |
| Test methods | 363 |
| Test LoC | ~6,100 |
| ICP keys seeded | 8 (all default True) |
| Wizards added | 3 (force_release, apply_template, eco_impact) |
| Crons added | 5 (CAD sync, release MO follow-up, ECO SLA, RFQ auto-draft, vendor cert expiry) |
| Manifest version bumps | 14 / 14 addons |
| Tickets closed | 30+ (P0/P1/P2/P3 + discovered 16x/17x) |
| Tickets deferred | 1 (PG-104) |
| Tickets open at close | ~10 (see §10 Appendix D) |
| LGPL-3 addons (publishable) | 14 / 14 |

---

## 10. Appendices

### A — Full ICP key reference

All keys default `True`. Surfaced under Settings → ProductGraph by
`addons/product_graph_base/models/res_config_settings.py`.

| ICP key | Field name | What it gates |
|---|---|---|
| `productgraph.enforce_sod` | `pg_enforce_sod` | Segregation-of-duties (PG-120). When False, same user may submit + approve. |
| `productgraph.auto_advance_item_on_revision_release` | `pg_auto_advance_item_on_revision_release` | Auto-promotes parent item from `engineering` → `released` on revision release. |
| `productgraph.auto_close_eco_on_all_revisions_released` | `pg_auto_close_eco_on_all_revisions_released` | PG-130 ECO auto-close. |
| `productgraph.auto_spawn_revision_from_eco_line` | `pg_auto_spawn_revision_from_eco_line` | Allows the PG-131 spawn button to fire. |
| `productgraph.notify_on_auto_advance` | `pg_notify_on_auto_advance` | PG-162 transient notifications on auto-advance + auto-close. |
| `productgraph.eco_impact_explode_enabled` | `pg_eco_impact_explode_enabled` | PG-132 wizard. |
| `productgraph.eco_route_on_submit` | `pg_eco_route_on_submit` | PG-134 routing on submit. |
| `productgraph.eco_sla_enabled` | `pg_eco_sla_enabled` | SLA reminders + escalation cron. |

### B — Mixin override-point cookbook

For a new lifecycle model, declare on the concrete class:

```python
class PgWidget(models.Model):
    _name = 'pg.widget'
    _inherit = ['pg.lifecycle.mixin', 'mail.thread']

    LIFECYCLE_STATES = [
        ('draft', 'Draft'),
        ('review', 'In Review'),
        ('released', 'Released'),
    ]
    LIFECYCLE_TRANSITIONS = {
        'draft':    ['review'],
        'review':   ['released', 'draft'],
        'released': [],
    }
    LIFECYCLE_GROUPS = {
        'release': 'product_graph_base.group_pg_approver',
    }
    LIFECYCLE_BYPASS_KEY = '_pg_widget_release_bypass'
    LIFECYCLE_MUTABLE_LOCKED = frozenset({
        'state', 'active',
        'message_ids', 'message_follower_ids', 'activity_ids',
    })  # allowlist mode — narrow this if widget has more chatter fields

    state = fields.Selection(LIFECYCLE_STATES, default='draft', required=True)
```

Override hooks (all optional):

* `_pre_transition(from_state, to_state, **ctx)` — domain checks before
  transition fires. Raise `UserError` to block.
* `_lifecycle_post_transition(from_state, to_state, **ctx)` — cascade
  actions (notify, sync downstream).
* `_get_locked_states()` — return a frozenset of states that should be
  immutable. Default: derived from `LIFECYCLE_TRANSITIONS` empty values.

For blocklist mode (e.g. pg.eco where only `released` freezes a narrow
field set):

```python
    LIFECYCLE_RELEASED_LOCKED_FIELDS = frozenset({
        'title', 'change_type', 'engineering_lead_id',
    })
    # Leave LIFECYCLE_MUTABLE_LOCKED at default; mixin will ignore it
    # when LIFECYCLE_RELEASED_LOCKED_FIELDS is set.
```

### C — Deploy commands (QNAP southbrook-odoo)

Run from inside the southbrook-v19cr deploy harness. See
[[southbrook_deploy_flock_lock]] for the wrapping flock semantics.

Fresh install of the whole ProductGraph stack into an existing DB:

```bash
docker exec -u odoo southbrook-odoo odoo \
  -d southbrook \
  -i product_graph_base,product_graph_revision,product_graph_ebom,\
product_graph_release,product_graph_api,product_graph_eco,\
product_graph_vendor,product_graph_rfq,product_graph_cad,\
product_graph_relationship,product_graph_procurement,\
product_graph_eco_relationship \
  --stop-after-init \
  --logfile=/dev/stderr
```

Upgrade in place (preferred for production):

```bash
docker exec -u odoo southbrook-odoo odoo \
  -d southbrook \
  -u product_graph_base,product_graph_revision,product_graph_ebom,\
product_graph_release,product_graph_api,product_graph_eco,\
product_graph_vendor,product_graph_rfq,product_graph_cad,\
product_graph_relationship,product_graph_procurement,\
product_graph_eco_relationship \
  --stop-after-init \
  --logfile=/dev/stderr
```

Verify post-deploy:

```bash
# Health endpoint (auth='public') — should 200
curl -fsS http://southbrook-odoo:8069/api/v1/productgraph/health

# Items endpoint (auth='bearer') — should 401 without key, 200 with
curl -fsS \
  -H "Authorization: Bearer $PRODUCTGRAPH_API_KEY" \
  http://southbrook-odoo:8069/api/v1/productgraph/items
```

If the upgrade reports "Failed to load registry" — DO NOT restart Odoo
(per [[qnap_postgres_recovery_hazard]] and [[qnap_serialization_failure_transient]]).
Check `latest_version` on `ir_module_module` via `odoo shell` first.

### D — Open follow-up backlog (≥10 items)

| ID | Description | Source | Notes |
|---|---|---|---|
| PG-104 | Unsaved-revision EBOM behaviour | original brief | Deferred as feature decision; needs user call. |
| PG-117 | (Pre-existing) | original backlog | Not started this program. |
| PG-119 | Engineering attributes through MRP routing | PG-112 review | Lossy mapping flagged by mrp integration tests. See §8. |
| PG-123 | (Pre-existing) | original backlog | Not started. |
| PG-124 | (Pre-existing) | original backlog | Not started. |
| PG-163 | Reduce mandatory item-create fields | PG-152 §"Goals met?" | Biggest remaining UX win. See §8. |
| PG-164 | Button-soup gating on item header | PG-152 §"Adjacent UX observations" #2 | 4 buttons visible at once on prototype state. |
| (proc) | `purchase.requisition.line.pg_item_id` back-ref | `test_procurement_edge_cases.py` | Procurement traceability gap. See §8. |
| (cad) | CAD diff-on-pull auto-revise engine | CLAUDE.md Phase 3 | Stub flag exists; logic deferred. |
| (vendor) | Vendor portal / supplier self-service | CLAUDE.md Phase 3 | Not started; out of scope. |
| PG-143 follow-up | Smart-button `view_mode: 'form'` on rev_count == 1 | PG-152 §"Adjacent UX observations" #5 | 1-click saving per visit. |
| PG-161 follow-up | PG-140-style Promote on ECO | PG-152 §"Adjacent UX observations" #6 | Partial landed; widen the consolidation. |
| PG-162 surface | Auto-close foreground notification | PG-152 §"Adjacent UX observations" #7 | Toast on the releaser's screen. |
| DECISIONS.md drift | Add D1-amendment cross-reference | this report §4.10 | One-line edit; left to next session per "no code modifications" constraint. |

---

*Generated 2026-06-27 — PG-150 close-out. Authoritative until superseded by
the next program-level synthesis. Updates to this document require a new
ticket reference and a footer entry.*
