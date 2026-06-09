# ProductGraph – Engineering Lifecycle & PLM

**Odoo 19.0 Community Edition** · LGPL-3 · Co-developed with Claude AI (Anthropic)

ProductGraph is a Product Lifecycle Management (PLM) module that gives engineering teams a governed, auditable path from design intent to manufacturing — without touching Odoo's core MRP data until a release is formally approved.

---

## Architecture Contracts

| Contract | Rule |
|---|---|
| Ownership | ProductGraph owns `pg.*` — Odoo owns `mrp.*` |
| BOM creation | `mrp.bom` is created **only** in `PgRelease.action_execute_release()` |
| Immutability | Released items/revisions raise `UserError` on any non-whitelisted write |
| Audit | All lifecycle events are logged to `pg.audit.log` (append-only) |
| State guards | Every transition is validated by `_check_transition_allowed()` |

---

## Module Structure

```
product_graph/
├── models/
│   ├── pg_category.py        # Engineering categories + item tags
│   ├── pg_item.py            # Item registry, state machine, immutability
│   ├── pg_revision.py        # Revision lifecycle, BOM lines, release trigger
│   ├── pg_bom_line.py        # Engineering BOM lines, mrp export helper
│   ├── pg_release.py         # THE only mrp.bom creator
│   ├── pg_change_request.py  # ECR/ECO full workflow
│   ├── pg_asset.py           # Digital Twin stubs (Phase 2)
│   └── pg_audit_log.py       # Immutable audit trail
├── views/                    # XML views for all models
├── wizards/
│   ├── pg_release_wizard.py  # Pre-flight checks + release launcher
│   └── pg_ecr_reject_wizard.py
├── controllers/
│   └── pg_api.py             # REST API v1
├── security/
│   ├── pg_security.xml       # 5 hierarchical security groups
│   └── ir.model.access.csv
├── data/
│   ├── pg_sequence.xml       # PG-00001, REL-YYYY-0001, ECR-YYYY-0001
│   └── pg_category_data.xml  # Default engineering categories
├── demo/
│   └── pg_demo_data.xml      # 5 demo items + revisions + 1 ECR
└── tests/                    # TransactionCase tests for all models
```

---

## State Machines

### `pg.item`
```
draft → engineering → released → obsolete
```

### `pg.revision`
```
draft → in_review → approved → released → obsolete
```

### `pg.change.request` (ECR)
```
draft → submitted → impact_review → approved → implementing → implemented → closed
                  ↘ rejected → closed
```

### `pg.release`
```
pending → executed
         → failed → (reset) → pending
         → cancelled
```

---

## Security Groups

| Group | Key Capability |
|---|---|
| `group_pg_user` | Read-only view of all records |
| `group_pg_engineer` | Create/edit items, revisions, BOM lines, ECRs |
| `group_pg_approver` | Approve revisions and ECRs |
| `group_pg_release_manager` | Execute releases (sole authority over `mrp.bom` creation) |
| `group_pg_admin` | Full access including configuration |

---

## REST API

Base: `/api/v1/productgraph/`

| Method | Endpoint | Auth |
|---|---|---|
| GET | `/health` | public |
| GET | `/items` | user |
| GET | `/items/<item_number>` | user |
| GET | `/items/<item_number>/revisions` | user |
| GET | `/revisions/<id>/bom` | user |
| POST | `/items` | engineer |

---

## Release Process

1. Engineer creates `pg.item` and `pg.revision` with BOM lines.
2. Revision is submitted for review → approved by an Approver.
3. Release Manager clicks **Release to Manufacturing…** on the approved revision.
4. `pg.release.wizard` runs pre-flight checks and shows warnings.
5. Release Manager confirms → `PgRelease.action_execute_release()` fires:
   - Validates all preconditions.
   - Creates `product.product` if needed.
   - Optionally archives the previous `mrp.bom`.
   - Creates **one** `mrp.bom` with all lines.
   - Marks revision `released` via raw SQL (bypasses immutability guard).
   - Auto-transitions item to `released`.
   - Writes `pg.audit.log`.
   - Posts chatter on item and revision.

---

## Running Tests

```bash
odoo-bin -c odoo.conf -d <db> --test-enable -i product_graph
```

Or run individual test modules:
```bash
odoo-bin -c odoo.conf -d <db> --test-enable \
  --test-tags product_graph.TestPgItem \
  --test-tags product_graph.TestPgRelease
```

---

## Phase 2 Roadmap

- **Digital Twin** — connect `pg.asset` to IoT telemetry via the reserved `iot_device_id` field.
- **PDF Release Package** — auto-generate a release dossier PDF with BOM, audit trail, and approver signatures.
- **CAD Integration** — webhook triggers for SolidWorks / Fusion 360 REST callbacks.
- **Multi-company** — company-scoped sequences and approval chains.

---

## License

LGPL-3 · © ProductGraph Contributors · Co-developed with Claude AI (Anthropic).
