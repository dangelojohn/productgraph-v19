# Decision Log — ProductGraph for Odoo 19 CE

Decisions are immutable once recorded. Reversal requires a new entry that
references the original.

## D1 — southbrook_plm coupling: bridge addon, no absorb (2026-06-09)

**Decision.** Ship a separate `product_graph_southbrook` bridge addon (private
repo). The generic ProductGraph addons never import southbrook_plm symbols.

**Direction of coupling.** One-way: `southbrook_plm.eco` closure may trigger
`pg.release.action_execute_release()`. The reverse — ProductGraph calling into
southbrook_plm — is forbidden.

**Why.** Absorbing the cabinet-shop ECO model would couple a generic LGPL
module to bespoke cabinet logic and block publication. The bridge keeps
ProductGraph genuinely reusable.

**Owner.** Human user (dangelo.john@gmail.com), confirmed in this session.

---

## D2 — Property templates: add them, scoped per item-type (2026-06-09)

**Decision.** `pg.property.template` → `pg.property.field` → `pg.property.value`.
Templates are scoped per `pg.item.item_type`. Values are written against
`pg.revision`, not `pg.item` directly.

**Phase 1 cap.** Schema + definition UI + value entry only. No formula
evaluation. No cross-item rollup.

**Why.** This is the one OpenBOM feature that distinguishes a real catalog
from a glorified product list. Scoping per item-type stops Cabinet-Width from
appearing on BOM-only document items.

**Owner.** Human user.

---

## D3 — AVL/RFQ: model-only stubs in Phase 1 (2026-06-09)

**Decision.** Reserve `pg.vendor` and `pg.vendor.part` as model-only stubs in
`product_graph_base`. No UI, no menus, no procurement hooks. Same pattern as
`pg.asset` for the Digital Twin path.

**Why.** Full AVL/RFQ doubles Phase 1 scope for a feature less urgent than a
solid EBOM + release boundary. Stubs reserve the schema so Phase 2 migration
is additive.

**Owner.** Human user.

---

## D4 — BOM editor: native x2many + CSV, no custom OWL widget (2026-06-09)

**Decision.** Native Odoo `<list editable="bottom">` x2many on `pg.ebom.line`,
plus CSV import/export, plus keyboard-friendly inline reference + qty edit.

**Explicit non-goal.** No custom OWL spreadsheet widget in Phase 1.

**Why.** Custom OWL widgets break on minor Odoo updates and most users fall
back to CSV import anyway. Save 4-6 days for higher-value work. Revisit only
if real users request the spreadsheet feel post-launch.

**Owner.** Human user.

---

## D5 — MCP server: sidecar container with env-var config (2026-06-09)

**Decision.** Standalone Python service in `mcp/`. Talks to Odoo over XML-RPC
+ REST. Containerized independently of the Odoo container.

**Config interface.** Single `.env` file with:

```
PRODUCTGRAPH_ODOO_URL=https://...
PRODUCTGRAPH_API_KEY=...
PRODUCTGRAPH_DB=...
```

Drop-in alongside the existing `alfacore-caddy` stack on the QNAP.

**Why.** Embedded longpolling-style workers inside Odoo are fragile and block
the Odoo process pool. Sidecar isolates failures and lets the MCP server be
versioned and restarted independently.

**Owner.** Human user.

---

## D6 — MCP auth: API keys + cloudflared Zero-Trust + per-tool group check (2026-06-09)

**Decision.** Odoo personal API keys passed in MCP request headers. cloudflared
Zero-Trust gates the public route. Each mutating MCP tool re-verifies the key's
user has `group_pg_engineer` minimum INSIDE the tool body (not just at the
route layer).

**Explicit non-goal.** No OAuth2 device flow. No refresh-token loop.

**Why.** OAuth2 device flow adds a token endpoint, refresh loop, and revocation
surface to maintain. Per-user Odoo API keys give per-user traceability without
that overhead. Per-tool group check enforces the auth boundary at the operation
level, which is where it actually matters.

**Owner.** Human user.

---

## D7 — Module split: 5 modules, not 7 (2026-06-09)

**Decision.** Collapse `_document` into `_revision` (documents are always
revision-scoped). Collapse `_report` into `_release` (reports are release
artifacts). Final Phase 1 layout:

```
product_graph_base
product_graph_revision   (+ documents)
product_graph_ebom
product_graph_release    (+ reports)
product_graph_api
```

Plus the `mcp/` sidecar and the `product_graph_southbrook` bridge (private).

**Why.** 7 modules means 7 upgrade operations and 7 depends chains to maintain.
The collapse preserves the authority boundaries (Bible R1-R5 still hold) while
cutting the upgrade surface to 5.

**Owner.** Human user.

---

## D8 — No raw SQL on pg.* tables — context-flag bypass instead (2026-06-09, agent-flagged)

**Decision.** The seed v19.0.1.0.0 module uses `env.cr.execute("UPDATE pg_revision SET state='released' WHERE id=%s", ...)`
to bypass its own immutability guard during release. The Architecture Bible
forbids raw SQL on pg.* tables (CLAUDE.md §4 P2).

This build uses a context-flag bypass:

```python
revision.with_context(_pg_release_bypass=True).write({...})
```

The `write()` override on `pg.revision` checks for the context flag and only
allows the bypass when present. Only `pg.release.action_execute_release()`
ever sets this flag.

**Why.** Raw SQL skips ORM constraints, tracking, audit hooks, and chatter
posts — every reason the Bible bans it. Context-flag bypass is reviewable and
preserves the full ORM trail.

**Owner.** Agent (claude-code), flagging the violation. Human user to confirm
or flip.

---

*Decisions are sealed by initials + date in commit messages once acted on.*
