# CLAUDE.md — ProductGraph for Odoo 19 CE (build root)

> Read this file completely before writing any code. It locks the architecture
> decisions for this build and points you at the authoritative source docs.

## Identity

| Field | Value |
|---|---|
| Product | ProductGraph for Odoo 19 CE |
| Target | Odoo 19.0 Community Edition (Python 3.11+) |
| License | LGPL-3 (the generic addons + sidecar) |
| Bridge license | Proprietary / Southbrook private repo |
| Spec source | `docs/ProductGraph_Architecture_Bible_v1.0.md` |
| Mission | OpenBOM-mirror engineering catalog + governed release-to-MRP for Odoo 19 CE |

## What this build adds on top of the bibles

The user team locked these decisions in conversation on 2026-06-09. They override
anything in the source bibles where they conflict.

### Decision 1 — Coexistence with southbrook_plm
ProductGraph and `southbrook_plm` are siblings.

- `product_graph` owns: engineering catalog, properties, EBOM, release-to-MRP.
- `southbrook_plm` (already deployed) owns: cut specs, ECO production workflow.
- A bridge addon `product_graph_southbrook` (private) wires
  `southbrook_plm.eco` closure → `pg.release.action_execute_release()`.
- **The bridge is one-way.** ProductGraph addons NEVER import southbrook symbols.

### Decision 2 — Property templates (OpenBOM mirror)
Add `pg.property.template` → `pg.property.field` → `pg.property.value` to base.
Templates are scoped per `pg.item.item_type`. Values attach to a `pg.revision`.
Phase 1 cap: schema + UI for definition and value entry only. NO formula
evaluation, NO cross-item rollup yet.

### Decision 3 — Vendor catalog
Model-only stubs in base: `pg.vendor` and `pg.vendor.part`. No UI, no
procurement hooks, no menus. Reserves the schema path the same way
`pg.asset` reserves the Digital Twin path.

### Decision 4 — BOM editor UX
Native Odoo x2many editable list + CSV import/export + keyboard-friendly
inline reference & qty edit. NO custom OWL widget in Phase 1. Revisit only
if real users ask for a spreadsheet widget post-launch.

### Decision 5 — MCP server
Standalone sidecar Python service in `mcp/`. Talks to Odoo over XML-RPC + REST.
Config via env vars `PRODUCTGRAPH_ODOO_URL`, `PRODUCTGRAPH_API_KEY`,
`PRODUCTGRAPH_DB`. Container ships independently of the Odoo container.

### Decision 6 — MCP auth
Odoo personal API keys + cloudflared Zero-Trust on the public route. Each
mutating MCP tool re-verifies the key's user has `group_pg_engineer` minimum
at the tool boundary (not just the route). No OAuth2 device flow in Phase 1.

### Decision 7 — Module split (final)
Five Odoo modules + 1 sidecar + 1 bridge.

```
addons/
  product_graph_base/        pg.item, pg.category, pg.property.*, pg.audit.log,
                             pg.vendor stubs, security (6 groups), sequences
  product_graph_revision/    pg.revision, pg.document (versioned attachments)
  product_graph_ebom/        pg.ebom, pg.ebom.line, pg.ebom.closure
  product_graph_release/     pg.release, mrp.production traceability, QWeb reports
  product_graph_api/         REST /api/v1/productgraph/*

mcp/                         product-graph-mcp sidecar (separate container)
bridge_spec/                 product_graph_southbrook bridge addon (spec only;
                             actual code lives in ~/southbrook-v19cr/ private repo)
```

`_document` collapsed into `_revision` (documents are always revision-scoped).
`_report` collapsed into `_release` (reports are release artifacts).

## Hard architectural rules (non-negotiable)

These come from the Bibles and apply to every line of code in this repo.

### R1 — Release boundary is sacred
`mrp.bom` and `mrp.bom.line` are written ONLY inside
`PgRelease.action_execute_release()`. Every other code path is forbidden from
touching MRP. Bridge addon may CALL this method, never duplicate it.

### R2 — No raw SQL on pg.* tables
The seed module uses `env.cr.execute("UPDATE pg_revision SET ...")` to bypass
the immutability guard during release. **This is a Bible violation and must
not be carried forward.**

Use a context-flag bypass instead:

```python
# In pg.revision.write():
def write(self, vals):
    bypass = self.env.context.get('_pg_release_bypass')
    for rec in self:
        if rec.state in IMMUTABLE_STATES and not bypass:
            blocked = set(vals.keys()) - CHATTER_FIELDS
            if blocked:
                raise UserError(...)
    return super().write(vals)

# In pg.release.action_execute_release():
revision.with_context(_pg_release_bypass=True).write({
    'state': 'released',
    'effective_date': fields.Date.today(),
    ...
})
```

Only the release flow may set the bypass key. Check with `inspect` or a
private marker if defense-in-depth is needed.

### R3 — No hard deletes on released or historically significant records
`unlink()` overrides on pg.item / pg.revision / pg.ebom / pg.release reject
deletion outside the (draft, obsolete) states.

### R4 — All transitions emit an audit log + chatter
Every `action_*` state transition writes a `pg.audit.log` entry AND posts
a chatter message AND fires the relevant notification. No silent state mutation.

### R5 — One model per XML view file
`views/pg_item_views.xml` only defines pg.item views. No mixing.

### R6 — Per-item-type sequence prefixes
```
PG-ASM-NNNN  assemblies
PG-PRT-NNNN  manufactured parts
PG-BUY-NNNN  purchased
PG-DOC-NNNN  documents
PG-SFT-NNNN  software
PG-SVC-NNNN  service
```
(NOT the flat `PG-NNNNN` used by the seed module.)

### R7 — 6 security groups (Bible naming)
`group_pg_viewer < group_pg_user < group_pg_engineer < group_pg_approver < group_pg_mfg < group_pg_admin`

(The seed module used `group_pg_release_manager` — that role is folded into
`group_pg_approver`. The new `group_pg_mfg` role is for read + MO-trace
visibility.)

### R8 — 6 item states + 5 revision states (Bible)
- `pg.item.state`: concept → prototype → engineering → released → service → obsolete
- `pg.revision.state`: draft → review → released → superseded → obsolete
- `pg.ebom.state`: draft → review → released → superseded
- `pg.release.state`: pending → executing → completed → failed

### R9 — Phase 1 (CLOSED) + Phase 2 (LANDED) + Phase 3 (Future)

**Phase 1 (closed) — Core engineering catalog & release pipeline:**
- `product_graph_base` — pg.item, pg.category, pg.property.*, pg.audit.log, 6 security groups, per-type sequences
- `product_graph_revision` — pg.revision, pg.document
- `product_graph_ebom` — pg.ebom, pg.ebom.line, pg.ebom.closure (advisory-locked rebuild)
- `product_graph_release` — pg.release, mrp.production traceability, mrp.bom provenance fields
- `product_graph_api` — REST controllers under /api/v1/productgraph/*
- mcp/ sidecar (separate container)
- `bridge_spec/` — southbrook_plm.eco → pg.release wire spec (bridge addon lives in southbrook private repo)

**Phase 2 (landed) — ECO workflow + full procurement + CAD + relationships:**
- `product_graph_eco` — pg.eco 5-state machine (draft → analysis → approval → implementation → released + rejected), pg.eco.line, pg.eco.approval, SoD enforcement, ICP-gated automation
- `product_graph_vendor` — pg.vendor 5-state qualification lifecycle, pg.vendor.part AVL, pg.vendor.qualification audit, expiring-cert cron
- `product_graph_rfq` — pg.rfq + pg.rfq.line + pg.rfq.bid, auto-rank by price/lead-time/MOQ (NEVER auto-awards)
- `product_graph_procurement` — bridge from RFQ to purchase.requisition
- `product_graph_relationship` — pg.relationship (substitutes/alternates/supersedes/references/related edges) + computed helpers on items
- `product_graph_eco_relationship` — bridge: on ECO release, auto-write supersession edges
- `product_graph_cad` — pg.cad.connector framework + JsonFileConnector + scheduled-sync cron + 5-layer draft-only enforcement
- `product_graph_cad_solidworks`, `product_graph_cad_onshape` — vendor-specific driver stubs

**Phase 3 (future / out of scope):**
- Phase-2 module ECO auto-spawn → MRP auto-routing (currently human-mediated)
- Active enforcement of MG-3 incompatibility at warehouse intake (currently audit-only)
- res.config.settings panel was added for all 8 ICP toggles in Phase 2.5
- Vendor portal / supplier self-service
- CAD-driver auto-revise diff engine (the `auto_revise_on_change` flag exists but the diff logic is a stub)

**Pattern note:** What WAS the bridge `product_graph_southbrook` plan is now superseded by the native `product_graph_eco` module + `product_graph_eco_relationship`. The seed module's `pg.change.request` was correctly NOT carried forward. southbrook_plm.eco still exists in the southbrook private repo for cabinetry-specific ECO production workflow; the two ECO surfaces coexist (one cabinetry-specific, one PLM-generic).

### Runtime configuration (ir.config_parameter)

Eight ICP keys control Phase 2 automation defaults. All default `True`. Admins toggle via Settings → ProductGraph (added by PG-125) or directly in ir.config_parameter:

- `productgraph.enforce_sod` — segregation of duties
- `productgraph.auto_advance_item_on_revision_release` — item state auto-promotion
- `productgraph.auto_close_eco_on_all_revisions_released`
- `productgraph.auto_spawn_revision_from_eco_line`
- `productgraph.notify_on_auto_advance`
- `productgraph.eco_impact_explode_enabled`
- `productgraph.eco_route_on_submit`
- `productgraph.eco_sla_enabled`

## What we harvest from the seed module (`_seed/product_graph/`)

Keep the patterns, refactor the structure:

| Seed file | Disposition |
|---|---|
| `models/pg_audit_log.py` | Keep as-is in `_base`. Immutable write/unlink overrides are correct. |
| `models/pg_category.py` | Keep as-is in `_base`. |
| `models/pg_asset.py` | Keep as-is in `_base` (Digital Twin stub). |
| `models/pg_item.py` | Refactor: 4 → 6 states, add per-type sequence, drop pg.change.request linkage, drop raw-SQL hooks. |
| `models/pg_revision.py` | Move to `_revision`. Refactor: 5-state (no `in_review`/`approved`; add `superseded`). Strip embedded `bom_line_ids`. Context-flag bypass instead of raw SQL. |
| `models/pg_bom_line.py` | Move + rename to `pg.ebom.line` in `_ebom`. Parent becomes `pg.ebom`, not `pg.revision`. |
| `models/pg_release.py` | Move to `_release`. Strip raw SQL. Add MO-freeze + frozen_mo_ids. State enum changes to pending/executing/completed/failed. |
| `models/pg_change_request.py` | **Drop.** Bridge addon connects southbrook_plm.eco instead. |
| `controllers/pg_api.py` | Move to `_api`. Expand to match API Bible (eboms, where-used, releases/status). |
| `security/pg_security.xml` | Refactor: 5 → 6 groups, rename release_manager → approver semantics. |
| `wizards/pg_release_wizard.py` | Move to `_release`. Adopt 5-step Bible flow. |
| `data/pg_sequence.xml` | Refactor: per-item-type sequences. |
| Views, demo | Rewrite per `_base/_revision/_ebom/_release` split. |

## OpenBOM-mirror feature checklist (Phase 1)

- [x] Catalog with per-type sequences, properties, search, tags
- [x] Multi-level BOM (separate header + lines)
- [x] Property templates per item-type (Decision 2)
- [x] Where-used (materialized closure, sub-3s target)
- [x] Document attachments (revision-scoped, versioned)
- [x] Released-revision immutability with audit log
- [x] Release-to-MRP with MO freeze + PO warn + incompatibility flag
- [x] CSV import/export for items and BOMs
- [x] REST API for headless integration
- [x] MCP server for LLM-agent integration (this is our OpenBOM differentiator)
- [x] Phase 2: full vendor catalog + RFQ + AVL/AML (landed Sprint 3)
- [x] Phase 2: ECO workflow with auto-spawn + impact explode + SLA + escalation (landed Sprints 3-4)
- [x] Phase 2: graph relationship engine (substitutes/alternates/supersedes)
- [x] Phase 2: CAD connectors (framework + JSON driver shipped; SolidWorks + Onshape stubs)
- [ ] Phase 3: CAD diff-on-pull → auto-revise (flag exists, logic deferred)
- [ ] Phase 3: Vendor portal / supplier self-service
- [ ] Phase 3: Active MG-3 incompatibility enforcement at warehouse intake

## Team coding protocol

This codebase is co-authored by Claude Code, Claude.ai, and OpenAI agents.

- Commit message tag: `[agent:claude-code]`, `[agent:claude.ai]`, `[agent:gpt]`
- Don't overwrite another agent's completed module without leaving a note.
- Module boundaries are strict — base never imports from revision/ebom/release,
  the bridge never imports any pg.* code in the generic addons.
- Architectural deviations require a human decision and an update to this file.

## Authoritative source documents

| Doc | Path | Purpose |
|---|---|---|
| Architecture Bible | `docs/ProductGraph_Architecture_Bible_v1.0.md` | Master spec |
| State Machine Bible | `docs/020_State_Machine_Bible.md` | All transitions |
| Manufacturing Governance | `docs/026_Manufacturing_Governance.md` | MO/PO collision rules |
| UX Mockup | `docs/ProductGraph_UX_v2.html` | Interactive UI reference |
| Original CLAUDE.md | `docs/ROOT_CLAUDE_SOURCE.md` | The 13-section base spec |

## Deployment target

The Southbrook Cabinetry QNAP stack at 192.168.68.108:

- `southbrook-odoo` container (Odoo 19 CE)
- `southbrook-postgres`
- Mounted addons path: `/mnt/extra-addons/`
- Already deployed siblings: `southbrook_plm`, `southbrook_estimating`,
  `southbrook_estimating_website`, `southbrook_configurator_ux`,
  `southbrook_mrp_pm`, `mrp_product_costing`, `mrp_shop_floor_control`,
  the OCA `product_configurator*` suite.
- Forgejo git on the same QNAP under org `qnap` for staging.

## Build status

All Phase 1 + Phase 2 addons shipped. Tests scaffolded across 14 addons (~270 tests, ~6,100 LoC) but not yet run in CI — pending Docker harness execution (see `TESTING.md` at repo root, added by PG-151).

| Phase | Addon | Status |
|---|---|---|
| 1 | product_graph_base | live |
| 1 | product_graph_revision | live |
| 1 | product_graph_ebom | live |
| 1 | product_graph_release | live |
| 1 | product_graph_api | live |
| 1 | mcp/ sidecar | live (separate container) |
| 1 | bridge_spec/ | spec only — bridge lives in southbrook-v19cr private repo |
| 2 | product_graph_eco | live |
| 2 | product_graph_vendor | live |
| 2 | product_graph_rfq | live |
| 2 | product_graph_procurement | live |
| 2 | product_graph_relationship | live |
| 2 | product_graph_eco_relationship | live |
| 2 | product_graph_cad | live |
| 2 | product_graph_cad_solidworks | stub |
| 2 | product_graph_cad_onshape | stub |

---

*Last updated: 2026-06-27 (Phase 2 landed) | Decisions: D1-D8 + ICP toggles | Bibles: v1.0*
