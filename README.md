# ProductGraph for Odoo 19 CE

OpenBOM-mirror engineering catalog + governed release-to-MRP for Odoo 19 Community Edition. Five generic LGPL addons, one MCP sidecar for LLM agents, and one private bridge addon glueing the existing `southbrook_plm` ECO workflow.

Read [`CLAUDE.md`](CLAUDE.md) first — it locks the architecture decisions for this build and overrides anything in the source bibles when they conflict. Read [`DECISIONS.md`](DECISIONS.md) for the audit trail of why each choice was made.

## What's here

```
addons/                              # 5 Odoo 19 CE addons (LGPL-3)
  product_graph_base/                # pg.item, pg.category, pg.property.*, audit log, security (6 groups), per-type sequences, vendor + asset stubs
  product_graph_revision/            # pg.revision (5-state) + pg.document + pg.property.value
  product_graph_ebom/                # pg.ebom (4-state) + pg.ebom.line + pg.ebom.closure (materialized where-used)
  product_graph_release/             # pg.release + mrp.production traceability + QWeb reports + 5-step wizard
  product_graph_api/                 # REST /api/v1/productgraph/* per the API Bible

mcp/                                 # product-graph-mcp sidecar (separate container)
  src/product_graph_mcp/             # Python package
    tools/                           # catalog / bom / revision / document / release tools
  Dockerfile, docker-compose.yml     # QNAP alfacore-caddy network drop-in
  .env.example                       # PRODUCTGRAPH_ODOO_URL + PRODUCTGRAPH_API_KEY pair

bridge_spec/                         # Spec + reference skeleton for the Southbrook bridge
  SPEC.md                            # What goes where, open questions, deploy notes
  product_graph_southbrook/          # Move to ~/southbrook-v19cr/ private repo before deploying

docs/                                # The Bibles + UX mockup (read-only reference)
  ProductGraph_Architecture_Bible_v1.0.md
  020_State_Machine_Bible.md
  026_Manufacturing_Governance.md
  ProductGraph_UX_v2.html
  ROOT_CLAUDE_SOURCE.md

_seed/                               # The v19.0.1.0.0 seed module + zip we harvested from
```

## Architectural rules (non-negotiable — see CLAUDE.md §"Hard architectural rules")

| Rule | What it means |
|---|---|
| R1 | `mrp.bom` / `mrp.bom.line` are written ONLY in `PgRelease.action_execute_release`. |
| R2 | No raw SQL on `pg.*` tables. Use the `_pg_release_bypass` context flag instead (D8). |
| R3 | No hard deletes on released or historically significant records. |
| R4 | Every state transition writes an audit log + chatter message. |
| R5 | One model per XML view file. |
| R6 | Per-type part-number sequences: PG-ASM, PG-PRT, PG-BUY, PG-DOC, PG-SFT, PG-SVC. |
| R7 | Six security groups, linear hierarchy: viewer ⊂ user ⊂ mfg ⊂ engineer ⊂ approver ⊂ admin. |
| R8 | Bible-faithful state machines on item (6), revision (5), ebom (4), release (4). |
| R9 | Phase 1 ships base+revision+ebom+release+api+mcp+bridge. ECO/AVL/graph/CAD are Phase 2+. |

## Install order

Locally, in an Odoo 19 CE dev instance with the `addons/` dir on the addons path:

```
product_graph_base
product_graph_revision
product_graph_ebom
product_graph_release
product_graph_api
```

Then on the QNAP southbrook stack, install the bridge addon (after moving it to the private repo).

## Running the tests

```bash
odoo-bin -c odoo.conf -d <db> --test-enable \
  --test-tags product_graph \
  -i product_graph_base,product_graph_revision,product_graph_ebom,product_graph_release
```

## Team coding protocol

Co-authored by Claude Code, Claude.ai, and OpenAI agents. Commit messages tag the author: `[agent:claude-code]`, `[agent:claude.ai]`, `[agent:gpt]`. Architectural deviations require a human decision and a `DECISIONS.md` update.

## Status

Phase 1 scaffolding complete. Next steps:
1. Stand up a local Odoo 19 dev instance, install the five addons, run the test suite.
2. Build the MCP sidecar container and run a smoke test against the local Odoo.
3. Move `bridge_spec/product_graph_southbrook/` to `~/southbrook-v19cr/`, confirm the southbrook ECO model name + closure method, deploy to the QNAP southbrook stack.
