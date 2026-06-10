# ProductGraph for Odoo 19 CE

OpenBOM-mirror engineering catalog + governed release-to-MRP for Odoo
19 Community Edition. Adds the engineering-system-of-record layer
Odoo's stock catalog doesn't have — items, revisions, EBOMs, ECOs,
vendor-AVL, RFQ, relationships, CAD-driven import — and bridges
governed releases into `mrp.bom`.

Read [`CLAUDE.md`](CLAUDE.md) first — it locks the architecture
decisions for this build and overrides anything in the source bibles
when they conflict. Read [`DECISIONS.md`](DECISIONS.md) for the audit
trail of why each choice was made.

## What ships

10 Odoo 19 CE addons + 1 MCP sidecar + 1 deployment-specific bridge
spec. Every addon is LGPL-3.

### Phase 1 (foundation)

| Module | Owns | Key types |
|---|---|---|
| `product_graph_base` | Item master, categories, properties, security, audit log, vendor + vendor-part stubs | `pg.item`, `pg.category`, `pg.property.template`, `pg.audit.log`, `pg.vendor`, `pg.vendor.part` |
| `product_graph_revision` | Revision lifecycle, property values, versioned documents | `pg.revision`, `pg.property.value`, `pg.document` |
| `product_graph_ebom` | Engineering BOM + materialised closure for where-used | `pg.ebom`, `pg.ebom.line`, `pg.ebom.closure` |
| `product_graph_release` | The release boundary — the only path to `mrp.bom` | `pg.release`, `pg.release.line`, release wizard |
| `product_graph_api` | REST `/api/v1/productgraph/*` | controllers |

### Phase 2 (added 2026-06-10)

| Module | Owns | Key types |
|---|---|---|
| `product_graph_eco` | Engineering Change Order lifecycle + Bible §7.3 mail templates | `pg.eco`, `pg.eco.line`, `pg.eco.approval` |
| `product_graph_vendor` | AVL/AML qualification lifecycle | extends `pg.vendor` with state machine, `pg.vendor.qualification` audit |
| `product_graph_rfq` | Engineering RFQ that writes winning bids back to AVL pricing | `pg.rfq`, `pg.rfq.line`, `pg.rfq.bid` |
| `product_graph_relationship` | Sideways graph (substitutes, alternates, supersedes, references, related) + transitive closure | `pg.relationship`, `pg.relationship.closure` |
| `product_graph_cad` | Pluggable CAD connector framework + JSON-file reference driver | `pg.cad.connector`, `pg.cad.import`, `pg.cad.import.line` |
| `product_graph_cad_solidworks` | SOLIDWORKS PDM driver (HTTP-proxy pattern; proxy spec in `docs/`) | `SolidworksPdmConnector` |
| `product_graph_cad_onshape` | OnShape REST driver (pure HTTPS, no vendor libraries) | `OnShapeConnector` |
| `product_graph_procurement` | RFQ award → `purchase.requisition` line items | extends `pg.rfq` |
| `product_graph_eco_relationship` | ECO release → `pg.relationship.supersedes` edges | extends `pg.eco`, `pg.eco.line` |

### MCP sidecar

`mcp/` — standalone Python service (Python `mcp` SDK over XML-RPC to
Odoo). ~20 read-only tools across catalog, BOM, revision, document,
release, vendor, ECO, RFQ, relationship.

### Bridge spec

`bridge_spec/product_graph_southbrook/` — design spec for the private
addon that wires `southbrook_plm.eco` → `pg.release.action_execute_release()`.
Actual code lives in `~/southbrook-v19cr/` for the Southbrook deployment.

## Quick start

### Local dev install

Add `addons/` to Odoo's addons path, then install in dependency order:

```bash
odoo -d <db> -i product_graph_base
odoo -d <db> -i product_graph_revision,product_graph_ebom
odoo -d <db> -i product_graph_release,product_graph_api

# Phase 2 (optional)
odoo -d <db> -i product_graph_eco,product_graph_vendor,product_graph_rfq
odoo -d <db> -i product_graph_relationship,product_graph_cad
odoo -d <db> -i product_graph_procurement,product_graph_eco_relationship
odoo -d <db> -i product_graph_cad_solidworks,product_graph_cad_onshape
```

### Running the test suite

```bash
odoo -d <db> -i <module> --test-enable --test-tags product_graph
```

Performance benchmarks (Bible §12.2 budgets):

```bash
odoo -d <db> -u product_graph_relationship --test-enable --test-tags perf
```

### MCP sidecar

See `mcp/README.md`. Build the image, point `PRODUCTGRAPH_ODOO_URL` +
`PRODUCTGRAPH_API_KEY` at your Odoo, run the container; SSE endpoint
binds to `:9234/sse`.

## Architecture

### Hard rules (from `CLAUDE.md`)

| Rule | What it means |
|---|---|
| R1 | `mrp.bom` / `mrp.bom.line` are written ONLY in `PgRelease.action_execute_release`. |
| R2 | No raw SQL on `pg.*` tables. Use the `_pg_release_bypass` context flag instead (D8). The single exception: `pg.relationship.closure._rebuild_kind()` uses a recursive CTE — safe because the closure is internal, deterministic, has no immutability state. |
| R3 | No hard deletes on released or historically significant records. |
| R4 | Every state transition writes an audit log + chatter message + notification. |
| R5 | One model per XML view file. |
| R6 | Per-type part-number sequences: PG-ASM, PG-PRT, PG-BUY, PG-DOC, PG-SFT, PG-SVC. |
| R7 | Six security groups, linear hierarchy: viewer ⊂ user ⊂ mfg ⊂ engineer ⊂ approver ⊂ admin. |
| R8 | Bible-faithful state machines on every governed model. |
| R9 | Phase boundaries documented per module; Phase 2 modules can be omitted without breaking Phase 1. |

### State machines

| Model | States |
|---|---|
| `pg.item` | concept → prototype → engineering → released → service → obsolete |
| `pg.revision` | draft → review → released → superseded → obsolete |
| `pg.ebom` | draft → review → released → superseded |
| `pg.release` | pending → executing → completed → failed |
| `pg.eco` | draft → analysis → approval → implementation → released / rejected |
| `pg.rfq` | draft → sent → received → awarded / cancelled |
| `pg.vendor` | unqualified → probationary → qualified → suspended → disqualified |

Full transition + authority matrix in `docs/020_State_Machine_Bible.md`.

### Performance targets (Bible §12.2)

| Operation | Target | Hard Limit | Verified |
|---|---|---|---|
| Item list view (1000 records) | < 1 s | 2 s | — (untested) |
| Item search by part number | < 0.5 s | 1 s | — |
| EBOM display (50-line assembly) | < 2 s | 3 s | — |
| Where-Used (3 levels deep) | < 3 s | 5 s | ✓ `test_medium_scale_three_hop_query` |
| 500-item closure rebuild | < 10 s | — | ✓ `test_large_scale_closure_rebuild` |
| Release action (50-line EBOM) | < 10 s | 30 s | — |

## Team coding protocol

Co-authored by Claude Code, Claude.ai, and OpenAI agents. Commit
messages tag the author: `[agent:claude-code]`, `[agent:claude.ai]`,
`[agent:gpt]`. Architectural deviations require a human decision and
a `DECISIONS.md` update.

## Status (2026-06-10)

* All 10 addons + 1 MCP sidecar installed in production on the
  Southbrook QNAP stack (`southbrookcabinetry.space`).
* ~190 tests across all addons, 0 failures, 0 errors on `pg_test_qnap`.
* 13 unique constraints actually enforced in Postgres (after the
  `_sql_constraints → models.Constraint` migration documented in
  `docs/odoo19_constraint_migration.md`).
* Cloudflared tunnel fronts `https://southbrookcabinetry.space` for
  the customer Flutter PWA, `/mcp/sse` for the MCP sidecar, and the
  Odoo web client.

## Documents

* `CLAUDE.md` — build brief (operating frame, decisions D1-D8, hard rules)
* `docs/ProductGraph_Architecture_Bible_v1.0.md` — master spec
* `docs/020_State_Machine_Bible.md` — every transition
* `docs/026_Manufacturing_Governance.md` — MO/PO collision rules
* `docs/solidworks_proxy_spec.md` — Windows-side proxy contract
* `docs/ROOT_CLAUDE_SOURCE.md` — original 13-section base spec

## License

* Addons: LGPL-3
* MCP sidecar: LGPL-3
* `product_graph_southbrook` bridge: proprietary (deployment-specific)
