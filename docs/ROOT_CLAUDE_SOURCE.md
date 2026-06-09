# CLAUDE.md — ProductGraph for Odoo 19 CE
## Root Context File for Claude Code & AI Agent Sessions

> **Read this file completely before writing any code, proposing any migration,
> or modifying any model.**  
> This file is the canonical alignment document for all AI-assisted development.

---

## 1. Project Identity

| Field | Value |
|---|---|
| Product Name | ProductGraph for Odoo |
| Module Namespace | `product_graph` |
| Target Platform | **Odoo 19 Community Edition only** |
| Architecture Role | Engineering master-data governance layer |
| Repository | `product_graph/` (Forgejo) |
| Spec Version | v0.1 → Architecture Bible v1.0 |
| Language | Python 3.11+, XML, JavaScript (OWL) |
| License | LGPL-3 (Odoo standard) |

---

## 2. One-Paragraph Mission

ProductGraph is a PLM/EDM module for Odoo 19 CE that owns the engineering side of
the product lifecycle — Items, Revisions, Engineering BOMs, and the controlled
Release of those structures into native Odoo MRP. It does **not** replace Odoo MRP,
inventory, purchasing, or maintenance. It feeds them, with full auditability, through
an explicit, permission-gated Release action.

---

## 3. Model Ownership — The Hard Boundary

### ProductGraph OWNS (creates, modifies, controls)
```
pg.item               — Engineering item master
pg.revision           — Revision records (immutable when Released)
pg.ebom               — Engineering BOM header
pg.ebom.line          — Engineering BOM lines
pg.release            — Release records (immutable audit log)
pg.release.line       — Per-line release traceability
pg.eco                — Engineering Change Orders (Phase 2)
pg.eco.line           — ECO affected items (Phase 2)
pg.manufacturer       — Approved Manufacturer List entries
pg.vendor             — Approved Vendor List entries
pg.vendor.part        — Vendor part cross-references
pg.document           — Engineering document attachments
pg.graph.node         — Graph relationship nodes (Phase 2)
pg.graph.edge         — Graph relationship edges (Phase 2)
```

### Odoo OWNS (ProductGraph reads, never writes except via Release)
```
product.template      — Odoo product master (written ONLY during Release)
product.product       — Product variants (written ONLY during Release)
mrp.bom               — Manufacturing BOM (written ONLY during Release)
mrp.bom.line          — MRP BOM lines (written ONLY during Release)
mrp.production        — Manufacturing Orders (ProductGraph NEVER modifies)
purchase.order        — Purchase Orders (ProductGraph NEVER modifies)
stock.quant           — Inventory (ProductGraph NEVER touches)
maintenance.equipment — Assets (Phase 3 read-only link)
```

### The Release boundary is sacred
```
# CORRECT — Release action writes to Odoo
class PgRelease(models.Model):
    def action_execute_release(self):
        # This is the ONLY place mrp.bom is created/written
        bom = self.env['mrp.bom'].create({...})

# WRONG — never do this anywhere else
class PgEbom(models.Model):
    def some_method(self):
        self.env['mrp.bom'].write({...})  # FORBIDDEN
```

---

## 4. Architectural Principles (Non-Negotiable)

### P1 — Never Modify Odoo Core
```python
# FORBIDDEN
# - Patching any Odoo base class in place
# - Using _patch_method on Odoo models
# - Modifying Odoo XML files directly
# - Writing to res_config_settings fields that affect MRP core behavior

# CORRECT
# - Inherit with _inherit and add only new fields/methods
# - Use @api.constrains and @api.onchange on pg.* models only
```

### P2 — ORM Only, No Raw SQL
```python
# FORBIDDEN
self.env.cr.execute("UPDATE pg_item SET state = 'released' WHERE id = %s", [self.id])

# CORRECT
self.state = 'released'  # triggers ORM write(), which triggers audit log
```

### P3 — Security First (ACL before code)
- Every new model gets a `security/ir.model.access.csv` entry before any view XML
- Every sensitive action checks `self.env.user.has_group('product_graph.group_pg_approver')`
- Never use `sudo()` without a comment explaining why and a security review flag
- Released records are write-protected at the ORM level via `_check_released_immutability()`

### P4 — Immutable Releases
```python
# Every pg.revision and pg.release record must implement:
@api.constrains('state')
def _check_released_immutability(self):
    for rec in self:
        if rec._origin.state == 'released' and rec.state != 'released':
            raise ValidationError(
                "Released records are immutable. Create a new revision."
            )

# Every field write on a released revision must be blocked:
def write(self, vals):
    for rec in self:
        if rec.state == 'released' and any(
            k not in ALLOWED_RELEASED_FIELDS for k in vals
        ):
            raise UserError("Cannot modify a Released revision.")
    return super().write(vals)

ALLOWED_RELEASED_FIELDS = {'message_ids', 'activity_ids'}  # chatter only
```

### P5 — Full Auditability, No Hard Deletes
```python
# FORBIDDEN
rec.unlink()  # on any pg.item, pg.revision, pg.ebom, pg.release

# CORRECT — use state transitions to Obsolete
# If unlink() is ever called on a released or historically significant record,
# raise UserError. Implement _check_no_historical_delete() on all core models.

def unlink(self):
    for rec in self:
        if rec.state not in ('concept', 'draft'):
            raise UserError(
                f"Cannot delete {rec.name} in state '{rec.state}'. "
                "Archive or obsolete it instead."
            )
    return super().unlink()
```

### P6 — No Destructive Migrations
```python
# FORBIDDEN in any migration file:
# - DROP COLUMN
# - ALTER TABLE ... DROP
# - Renaming columns without a compatibility alias
# - DELETE FROM on production data tables

# Migration files must be additive or state-transforming only.
```

### P7 — One Model Per XML File
```
views/
  pg_item_views.xml          # Only pg.item views
  pg_revision_views.xml      # Only pg.revision views
  pg_ebom_views.xml          # Only pg.ebom + pg.ebom.line views
  pg_release_views.xml       # Only pg.release views
  pg_eco_views.xml           # Only pg.eco views (Phase 2)
```

### P8 — Separation of EBOM and MBOM
- `pg.ebom` is the engineering structure — it may contain items not yet in Odoo
- `mrp.bom` is the manufacturing structure — it only contains Released, Odoo-linked items
- The Release action is the **only** bridge between these two worlds
- An EBOM line can reference a `pg.item` that has no `product.template` yet
- A `mrp.bom.line` must always reference a `product.product`

---

## 5. Security Groups

```python
# product_graph/security/res_groups.xml
# Groups in ascending privilege order:

group_pg_viewer      = "PG Viewer"      # Read-only on all pg.* models
group_pg_user        = "PG User"        # Read + create drafts
group_pg_engineer    = "PG Engineer"    # Full engineering operations, submit for review
group_pg_approver    = "PG Approver"    # Release, approve, reject revisions
group_pg_mfg         = "PG Manufacturing" # View-only + MO-linked fields
group_pg_admin       = "PG Admin"       # Obsolete, admin settings, delete drafts
```

Access matrix in `security/ir.model.access.csv` — always define before any view.

---

## 6. State Machines (Summary — see 020_State_Machine_Bible.md for full spec)

### pg.item.state
```
concept → prototype → engineering → released → service → obsolete
```
- `released` items: write-blocked at ORM level
- `obsolete` items: read-only, preserved forever

### pg.revision.state
```
draft → review → released → superseded → obsolete
```
- Once `released`: immutable (only chatter/messages allowed)
- Transition `released → superseded` requires the successor revision to already be `released`

### pg.ebom.state
```
draft → review → released → superseded
```

### pg.release.state
```
pending → executing → completed → failed
```
- `completed` releases: immutable

---

## 7. The Release Action — Critical Implementation Notes

The `product_graph_release` module's `PgRelease.action_execute_release()` must:

1. **Validate** — check all EBOM lines reference `pg.item` records in `released` state
2. **Check products** — confirm every `pg.item` has a linked `product.template`
3. **Transact atomically** — wrap entire operation in a savepoint; rollback on any error
4. **Create MRP BOM** — `mrp.bom.create()` with correct `product_tmpl_id`, `type`, `code`
5. **Create MRP BOM lines** — one `mrp.bom.line` per `pg.ebom.line`
6. **Write pg.release** — immutable audit record linking EBOM → MRP BOM
7. **Update pg.revision** — mark as Released (triggers state machine validation)
8. **Notify** — post chatter message to all Manufacturing group members
9. **DO NOT** — modify any open `mrp.production` records

```python
def action_execute_release(self):
    self.ensure_one()
    if self.state != 'pending':
        raise UserError("Release already executed or failed.")
    
    self._validate_pre_release()       # raises on validation failure
    
    with self.env.cr.savepoint():
        bom = self._create_mrp_bom()
        self._create_mrp_bom_lines(bom)
        self._write_release_log(bom)
        self._transition_revision_released()
        self._notify_manufacturing()
        self.state = 'completed'
```

---

## 8. Module Build Order (MVP Phase 1)

Build strictly in this order. Do not skip steps.

```
1. product_graph_base          # pg.item, security groups, base config
2. product_graph_revision      # pg.revision, state machine, immutability
3. product_graph_ebom          # pg.ebom, pg.ebom.line, where-used
4. product_graph_release       # Release action, mrp.bom generation
5. product_graph_document      # Document attachment management
6. product_graph_report        # QWeb reports (indented BOM, where-used, audit)
7. product_graph_api           # REST API layer (controllers/)
```

Phase 2 (after Phase 1 is stable and tested):
```
8. product_graph_eco           # Engineering Change Orders
9. product_graph_supplier      # AVL/AML management
10. product_graph_graph        # Graph relationship engine
```

---

## 9. Directory Structure

```
product_graph/
├── CLAUDE.md                          ← This file
├── __manifest__.py
├── __init__.py
│
├── models/
│   ├── pg_item.py
│   ├── pg_revision.py
│   ├── pg_ebom.py
│   ├── pg_ebom_line.py
│   ├── pg_release.py
│   ├── pg_release_line.py
│   └── pg_document.py
│
├── security/
│   ├── res_groups.xml
│   └── ir.model.access.csv
│
├── views/
│   ├── pg_item_views.xml
│   ├── pg_revision_views.xml
│   ├── pg_ebom_views.xml
│   ├── pg_release_views.xml
│   ├── pg_document_views.xml
│   └── menus.xml
│
├── wizards/
│   ├── pg_release_wizard.py
│   └── pg_where_used_wizard.py
│
├── report/
│   ├── pg_report_indented_bom.xml
│   ├── pg_report_where_used.xml
│   └── pg_report_release_audit.xml
│
├── controllers/
│   └── pg_api_controller.py
│
├── data/
│   └── pg_sequence_data.xml           # Sequences for PG-ASM-, PG-PRT-, etc.
│
├── tests/
│   ├── test_pg_item.py
│   ├── test_pg_revision.py
│   ├── test_pg_release.py
│   └── test_pg_where_used.py
│
└── docs/
    ├── 020_State_Machine_Bible.md
    ├── 021_Odoo_Compatibility_Matrix.md
    ├── 022_UI_Specification.md
    ├── 023_API_Bible.md
    ├── 024_NFR.md
    ├── 025_Digital_Twin_Strategy.md
    ├── 026_Manufacturing_Governance.md
    └── 027_AI_Development_Governance.md
```

---

## 10. Coding Standards

```python
# Field naming: snake_case, prefixed for clarity
pg_item_id = fields.Many2one('pg.item', string="Engineering Item")
ebom_line_ids = fields.One2many('pg.ebom.line', 'ebom_id', string="BOM Lines")

# Always provide string= and help= on all fields
state = fields.Selection(
    selection=[
        ('draft', 'Draft'),
        ('review', 'Under Review'),
        ('released', 'Released'),
        ('superseded', 'Superseded'),
        ('obsolete', 'Obsolete'),
    ],
    string="Revision State",
    default='draft',
    required=True,
    tracking=True,   # Always track state changes
    help="Lifecycle state of this revision. Released revisions are immutable.",
)

# Use tracking=True on all state and critical fields
# Use _sql_constraints for uniqueness (part numbers, revision codes)
# Use @api.constrains for cross-field business rules
# Use ondelete='restrict' on critical Many2one fields (never cascade on pg.item)
```

---

## 11. What AI Agents Must Never Do

1. **Never** write Python that touches `mrp.production`, `purchase.order`, or `stock.quant` outside of the Release action
2. **Never** add a field to `product.template` or `product.product` — use `pg.item` instead
3. **Never** implement soft-delete by setting `active = False` on Released records — use state transitions
4. **Never** generate a migration that drops a column or table
5. **Never** use raw SQL (`env.cr.execute`) on pg.* tables
6. **Never** bypass security groups with `sudo()` on user-triggered actions
7. **Never** create `mrp.bom` records outside of `PgRelease.action_execute_release()`
8. **Never** write a cron job that modifies Released or Superseded records
9. **Never** hardcode IDs, company references, or sequence prefixes
10. **Never** merge Phase 2 features (ECO, Graph, Supplier) into Phase 1 models

---

## 12. Testing Requirements

Every model must have:
- `test_create_[model]` — happy path creation
- `test_state_transitions_[model]` — valid and invalid transitions
- `test_immutability_released` — confirm Released records reject writes
- `test_release_action` — end-to-end Release creates correct `mrp.bom`
- `test_where_used` — graph traversal returns correct parent set
- `test_security_[group]` — each group can/cannot perform key actions

Minimum coverage target: **80%** on all `product_graph_*` modules.

---

## 13. Key Reference Documents

| Document | Location | Purpose |
|---|---|---|
| Architecture Bible | `docs/ProductGraph_Architecture_Bible_v1.0.md` | Full system specification |
| State Machine Bible | `docs/020_State_Machine_Bible.md` | All state/transition rules |
| Odoo Compatibility | `docs/021_Odoo_Compatibility_Matrix.md` | EBOM↔MRP boundary rules |
| UI Specification | `docs/022_UI_Specification.md` | All views, buttons, smart buttons |
| API Bible | `docs/023_API_Bible.md` | All REST endpoints |
| NFR | `docs/024_NFR.md` | Performance & scale targets |
| Digital Twin | `docs/025_Digital_Twin_Strategy.md` | Phase 3+ roadmap |
| Mfg Governance | `docs/026_Manufacturing_Governance.md` | MO collision rules |
| AI Governance | `docs/027_AI_Development_Governance.md` | Coding rules for AI agents |
| UX Simulation | `ui/ProductGraph_UX_v2.html` | Interactive mockup |
| Base Spec | `docs/ProductGraph_Odoo19CE_Base_Specification_v0_1.md` | Original spec |

---

*Last updated: 2026-06-08 | Version: 1.0 | Owner: ProductGraph Architecture Team*
