# ProductGraph Architecture Bible
## Version 1.0 — Authoritative Reference
### ProductGraph for Odoo 19 Community Edition
### Date: 2026-06-08 | Status: Living Document

---

> **This document is the single source of truth for all ProductGraph design decisions.**
> AI coding agents (Claude Code, Codex, Cursor, OpenCode, Qwen) must read this document
> before writing any code. Human developers must review and update it before any
> architectural decision is made. When this document and any other document conflict,
> this document wins.

---

# PART I — VISION AND STRATEGY

## Chapter 1: Executive Summary

ProductGraph is a Product Lifecycle Management (PLM) and Engineering Data Management
(EDM) module built natively on Odoo 19 Community Edition. It provides the engineering
governance layer that Odoo CE lacks: revision control, engineering BOM management,
release-to-manufacturing workflows, and the foundational data model for a long-term
Digital Twin strategy.

ProductGraph does not compete with or replace Odoo. It extends Odoo by adding a
disciplined, auditable engineering domain that feeds Odoo's operational modules through
an explicit, permission-gated Release mechanism.

### One-Line Description
> ProductGraph is the engineering system of record for Odoo 19 CE manufacturing
> organizations — transforming ad-hoc spreadsheet BOMs into governed, traceable,
> revision-controlled engineering data.

### Business Problem Solved
Most SMB manufacturing organizations running Odoo manage engineering data in Excel
spreadsheets and email. This creates:
- BOMs disconnected from product revisions
- No formal change control
- Manufacturing built from outdated engineering data
- No traceability when quality issues arise
- Inability to perform impact analysis before changing a component

ProductGraph eliminates these problems without replacing Odoo — it enhances it.

### Long-Term Vision
```
CAD Design (SolidWorks / Fusion 360 / Onshape)
    ↓ [Phase 2: CAD Connector]
ProductGraph EBOM + Revision Control
    ↓ [Phase 1: Release to MRP]
Odoo Manufacturing (mrp.bom + mrp.production)
    ↓ [Phase 2: Asset Linkage]
Asset Registry (Installed Equipment)
    ↓ [Phase 3: Maintenance Integration]
Odoo Maintenance (maintenance.equipment + spare parts)
    ↓ [Phase 3: IoT Integration]
IoT Device Registry + Telemetry
    ↓ [Phase 4: Digital Twin]
Predictive Maintenance + Product Digital Twin
```

---

## Chapter 2: Market and Competitive Context

### Why Not OpenBOM?
OpenBOM (openbom.com) is the closest commercial analog to ProductGraph. It is a
cloud-native, multi-tenant PLM platform with strong CAD integration.

**OpenBOM strengths:** Multi-tenant SaaS, graph-based BOM model, excellent CAD
connectors, supplier collaboration network.

**ProductGraph advantages over OpenBOM:**
1. Native Odoo integration — no API glue, no data synchronization lag
2. On-premise deployment — data sovereignty for regulated manufacturers
3. Community Edition compatible — no Odoo Enterprise license required
4. Digital Twin roadmap — OpenBOM has no maintenance/IoT layer
5. Manufacturing Governance — explicit rules for MO/PO collision scenarios
6. Cost — open source vs SaaS subscription per user

### Why Not Odoo PLM (Enterprise)?
Odoo Enterprise includes a PLM module. It is not available for Community Edition and
requires an Enterprise subscription (significant annual cost for SMBs). ProductGraph
brings equivalent core functionality to CE deployments.

### Why Not Spreadsheets?
No revision control. No where-used. No release workflow. No audit trail. No integration
with manufacturing. Spreadsheets break at team scale (>2 engineers, >500 parts).

---

## Chapter 3: Jobs To Be Done (JTBD)

Primary user: **Product Engineer** at an SMB manufacturer (10–500 employees).

### JTBD-1: Manage Engineering Structures Without Spreadsheets
> "When I am defining what a product is made of, I want a structured, versioned
> engineering BOM, so that manufacturing doesn't build from my email attachments."

**Success Criteria:** Engineer can create a multi-level EBOM, link items to Odoo
products, and release it to MRP in under 30 minutes for a 50-item assembly.

### JTBD-2: Understand Change Impact Before Making a Change
> "When I am about to change a component, I want to see every assembly that uses it,
> so that I can assess the impact before I cause a manufacturing disruption."

**Success Criteria:** Where-Used analysis returns all parent assemblies and linked
MRP BOMs in under 3 seconds for any item in the catalog.

### JTBD-3: Release Accurate Manufacturing Structures
> "When engineering has approved a design, I want to push it to manufacturing in a
> controlled, auditable way, so that we build the right thing."

**Success Criteria:** Release action creates a correct mrp.bom with zero manual
data entry, in under 10 seconds, with a permanent audit record.

### JTBD-4: Know Exactly What Was Built at Any Point in Time
> "When a quality issue arises in the field, I want to know which revision was
> current when that unit was manufactured, so that I can determine scope of impact."

**Success Criteria:** Given any mrp.production lot number, retrieve the exact
pg.revision that was the engineering baseline within 5 clicks.

### JTBD-5 (Phase 2): Manage Approved Suppliers Formally
> "When procurement is buying a component, I want them to see which suppliers are
> approved for which revision, so that we don't accidentally buy from a disqualified source."

---

## Chapter 4: User Personas

### Persona 1: Product Engineer (Primary)
- **Profile:** BS Mechanical or Electrical Engineering, 3–10 years experience
- **Tools:** SolidWorks / Fusion 360 / Altium, currently Excel for BOMs
- **Odoo Experience:** Minimal — may use manufacturing module peripherally
- **Pain Points:** BOM is always behind the CAD model. Doesn't know what changed between revisions. Finds out about production problems from the shop floor.
- **ProductGraph Value:** Native revision control, automated EBOM from CAD (Phase 2), controlled release, where-used in seconds.

### Persona 2: Engineering Manager (Secondary)
- **Profile:** Senior engineer or technical director
- **Needs:** Visibility into what's released vs. in-progress, approval workflows, audit compliance
- **ProductGraph Value:** Approver role in state machine, release authority, audit log access.

### Persona 3: Manufacturing Engineer (Secondary)
- **Profile:** Interfaces between engineering and production floor
- **Needs:** Knows what revision to build to, understands BOM changes impact
- **ProductGraph Value:** Frozen MO notifications, where-used reports, MO genealogy.

### Persona 4: Procurement Specialist (Secondary)
- **Profile:** Manages supplier relationships and PO creation
- **Needs:** Knows which revision to buy, which suppliers are approved
- **ProductGraph Value:** Revision change notifications, AVL/AML access (Phase 2).

---

# PART II — ARCHITECTURE

## Chapter 5: Architectural Principles

See CLAUDE.md §4 for implementation-level rules. This chapter describes the reasoning.

### AP-1: Engineering First
Engineering data is the upstream source of truth. Manufacturing, procurement, and
maintenance data are derived from it. ProductGraph enforces this directionality:
data flows downstream from engineering, not upstream from operations.

### AP-2: Immutable Releases
A Released revision of any engineering record is a contract. It cannot be silently
changed. This is the foundation of traceability, quality compliance, and regulatory
auditability (ISO 9001, AS9100, FDA 21 CFR Part 11 readiness).

### AP-3: Separation of EBOM and MBOM
The Engineering BOM (EBOM) defines the product as designed. The Manufacturing BOM
(MBOM / mrp.bom) defines the product as built. These are related but distinct. The
Release action is the controlled bridge between them.

### AP-4: Full Traceability
Every manufacturing lot must be traceable to the exact engineering revision under
which it was produced. This chain must survive archival, data migration, and module
upgrades. No hard deletes are permitted on any released or historically significant record.

### AP-5: Multi-Level Product Structures
ProductGraph must handle BOMs of arbitrary depth (3–15 levels is typical in mechanical
manufacturing). The graph model and where-used analysis must work correctly at any depth.

### AP-6: Odoo Native — No Middleware
ProductGraph is an Odoo module, not a standalone application with an Odoo connector.
It uses Odoo's ORM, security, workflow, mail/chatter, and reporting infrastructure.
This eliminates synchronization complexity and data drift.

---

## Chapter 6: System Architecture

### 6.1 Module Structure

```
product_graph/                    ← Monorepo; installable as independent modules
├── product_graph_base/           ← pg.item + security infrastructure
├── product_graph_revision/       ← pg.revision + state machine
├── product_graph_ebom/           ← pg.ebom + pg.ebom.line + where-used
├── product_graph_release/        ← Release action → mrp.bom generation
├── product_graph_document/       ← Engineering document management
├── product_graph_report/         ← QWeb reports
├── product_graph_api/            ← REST API controllers
│   ── (Phase 2) ──
├── product_graph_eco/            ← Engineering Change Orders
├── product_graph_supplier/       ← AVL/AML management
├── product_graph_graph/          ← Graph relationship engine
│   ── (Phase 3) ──
├── product_graph_asset/          ← Asset registry linkage
├── product_graph_digital_twin/   ← Digital Twin data model
```

### 6.2 Data Flow Architecture

```
[CAD System]
     │ (Phase 2: pg.cad.connector)
     ↓
[pg.item]  ←→  [pg.revision]
     │
     ↓
[pg.ebom] ──── [pg.ebom.line]
     │
     │  (pg.release.action_execute_release — the ONLY bridge)
     ↓
[mrp.bom] ──── [mrp.bom.line]
     │
     ↓
[mrp.production]  (ProductGraph reads, never writes)
     │
     ↓
[Finished Lot + pg_revision_code field]
     │  (Phase 3)
     ↓
[maintenance.equipment]
     │  (Phase 4)
     ↓
[Digital Twin + IoT]
```

### 6.3 Security Architecture

```
group_pg_admin       ──►  Full access + obsolete + config
group_pg_approver    ──►  Release + approve + reject revisions
group_pg_engineer    ──►  Create + edit + submit for review
group_pg_mfg         ──►  Read all + view MO traceability
group_pg_user        ──►  Read all + create concept items
group_pg_viewer      ──►  Read-only (all pg.* models)
```

Each group is a superset of the previous (Admin > Approver > Engineer > Mfg > User > Viewer).

---

## Chapter 7: Core Data Model

### 7.1 pg.item — Engineering Item Master

The fundamental unit of ProductGraph. Every part, assembly, document, software
artifact, or service item that engineering creates is a pg.item.

**Key Fields:**
| Field | Type | Description |
|---|---|---|
| `name` | Char | Human-readable item name |
| `part_number` | Char | System-assigned unique number (e.g., PG-ASM-0001) |
| `state` | Selection | Item lifecycle state (concept→obsolete) |
| `item_type` | Selection | assembly, part, purchased, document, software, service |
| `revision_ids` | One2many → pg.revision | All revisions of this item |
| `active_revision_id` | Many2one → pg.revision | Current released revision |
| `product_tmpl_id` | Many2one → product.template | Linked Odoo product (set at release) |
| `ebom_line_ids` | One2many → pg.ebom.line | All EBOM lines where this item appears |
| `description` | Text | Engineering description |
| `engineering_notes` | Text | Internal notes (not exported to MRP) |
| `uom_id` | Many2one → uom.uom | Unit of measure |
| `company_id` | Many2one → res.company | Multi-company support |

**SQL Constraints:**
```sql
UNIQUE(part_number, company_id)   -- Part numbers are unique per company
```

**Sequence Configuration:**
```
PG-ASM-NNNN  → Assemblies
PG-PRT-NNNN  → Manufactured Parts
PG-BUY-NNNN  → Purchased Components
PG-DOC-NNNN  → Documents
PG-SFT-NNNN  → Software
PG-SVC-NNNN  → Service Items
```

### 7.2 pg.revision — Revision Records

A revision is a formal, versioned snapshot of an engineering item's design intent.
Once released, a revision is immutable.

**Key Fields:**
| Field | Type | Description |
|---|---|---|
| `name` | Char | Revision code (A, B, C... or 1, 2, 3...) |
| `pg_item_id` | Many2one → pg.item | Parent item |
| `state` | Selection | draft, review, released, superseded, obsolete |
| `change_summary` | Text | What changed from previous revision (required) |
| `effective_date` | Date | Date revision became effective (stamped at release) |
| `released_by` | Many2one → res.users | Who released it (stamped at release) |
| `release_timestamp` | Datetime | When it was released (stamped at release) |
| `rejection_reason` | Text | Required when rejected from review |
| `successor_revision_id` | Many2one → pg.revision | Which revision superseded this one |
| `ebom_ids` | One2many → pg.ebom | EBOMs created under this revision |
| `release_ids` | One2many → pg.release | MRP releases from this revision |
| `incompatible_with_previous` | Boolean | If True, old inventory cannot be consumed |
| `incompatibility_reason` | Text | Required if incompatible_with_previous=True |

### 7.3 pg.ebom — Engineering BOM

The engineering structure of an assembly. Contains pg.ebom.line records defining
what an assembly is made of at a specific revision.

**Key Fields:**
| Field | Type | Description |
|---|---|---|
| `name` | Char | EBOM identifier (e.g., EBOM-00084) |
| `root_item_id` | Many2one → pg.item | Top-level assembly |
| `revision_id` | Many2one → pg.revision | Which revision this EBOM belongs to |
| `state` | Selection | draft, review, released, superseded |
| `ebom_type` | Selection | production, prototype |
| `ebom_line_ids` | One2many → pg.ebom.line | Child items |
| `release_ids` | One2many → pg.release | MRP releases generated from this EBOM |

### 7.4 pg.ebom.line — BOM Line Items

**Key Fields:**
| Field | Type | Description |
|---|---|---|
| `ebom_id` | Many2one → pg.ebom | Parent EBOM |
| `sequence` | Integer | Line ordering (10, 20, 30...) |
| `pg_item_id` | Many2one → pg.item | Child item |
| `product_qty` | Float | Quantity |
| `uom_id` | Many2one → uom.uom | Unit of measure |
| `is_optional` | Boolean | Optional component flag |
| `alternate_group` | Char | Alternate group identifier (e.g., HINGE-A) |
| `is_primary` | Boolean | True if this is primary within an alternate group |
| `notes` | Text | Engineering notes for this line |

### 7.5 pg.release — Release Audit Record

Immutable record of every EBOM-to-MRP release event.

**Key Fields:**
| Field | Type | Description |
|---|---|---|
| `name` | Char | Release identifier (e.g., REL-2026-001) |
| `pg_ebom_id` | Many2one → pg.ebom | Source EBOM |
| `pg_revision_id` | Many2one → pg.revision | Source revision |
| `mrp_bom_id` | Many2one → mrp.bom | Generated MRP BOM |
| `state` | Selection | pending, executing, completed, failed |
| `released_by` | Many2one → res.users | Who executed the release |
| `release_timestamp` | Datetime | When completed |
| `failure_reason` | Text | Error details if state=failed |
| `frozen_mo_count` | Integer | How many MOs were frozen (see Governance §3) |
| `frozen_mo_ids` | Many2many → mrp.production | The frozen MOs |
| `release_reason` | Text | Business reason for this release |
| `line_ids` | One2many → pg.release.line | Per-line traceability |

---

# PART III — DOCUMENT 020: STATE MACHINE BIBLE

*See full document: `docs/020_State_Machine_Bible.md`*

## Chapter 8: State Machine Summary

### 8.1 pg.item States
`concept → prototype → engineering → released → service → obsolete`

### 8.2 pg.revision States  
`draft → review → released → superseded → obsolete`

### 8.3 pg.ebom States
`draft → review → released → superseded`

### 8.4 Critical Rules
1. Released records are immutable (ORM write block enforced)
2. No hard deletes on released or historically significant records
3. Every transition writes an immutable audit log entry
4. Auto-transition: when pg.revision reaches released, pg.item auto-transitions to released
5. EBOM cannot release unless all child pg.items are released

---

# PART IV — DOCUMENT 021: ODOO COMPATIBILITY MATRIX

## Chapter 9: EBOM ↔ MRP Boundary Rules

### 9.1 Ownership Table

| Data Object | Owner | Notes |
|---|---|---|
| pg.item | ProductGraph | Never written by Odoo standard modules |
| pg.revision | ProductGraph | Immutable when released |
| pg.ebom / pg.ebom.line | ProductGraph | Engineering structure |
| pg.release | ProductGraph | Immutable audit record |
| product.template | Odoo (written by Release only) | ProductGraph reads; Release creates |
| product.product | Odoo (written by Release only) | ProductGraph reads |
| mrp.bom | Odoo (written by Release only) | ProductGraph NEVER writes outside Release |
| mrp.bom.line | Odoo (written by Release only) | Same |
| mrp.production | Odoo (ProductGraph reads only) | ProductGraph adds read-only fields |
| purchase.order | Odoo (ProductGraph reads only) | PG sends notifications, never writes |
| stock.quant | Odoo (ProductGraph reads only) | PG reads for governance checks |

### 9.2 MO / Revision Collision — Formal Rule

**When a new revision is released while open MOs exist:**
- Existing open MOs: FROZEN at current BOM (not updated)
- New MOs created after release: use new BOM automatically
- Notification: sent to all group_pg_mfg members
- Audit: pg.release records frozen MO count and IDs

Full implementation: see `docs/026_Manufacturing_Governance.md` §3.

### 9.3 What ProductGraph Writes to Odoo

The ONLY time ProductGraph writes to Odoo-owned models is during
`PgRelease.action_execute_release()`, and only these specific operations:

```python
# 1. Optionally create product.template if not yet linked
product_tmpl = self.env['product.template'].create({
    'name': pg_item.name,
    'type': 'consu',
    'uom_id': pg_item.uom_id.id,
})

# 2. Create mrp.bom
bom = self.env['mrp.bom'].create({
    'product_tmpl_id': product_tmpl.id,
    'code': f"{pg_ebom.root_item_id.part_number}-{revision.name}",
    'type': 'normal',
    'pg_release_id': self.id,   # traceability field (inherited)
})

# 3. Create mrp.bom.line for each pg.ebom.line
for line in pg_ebom.ebom_line_ids:
    self.env['mrp.bom.line'].create({
        'bom_id': bom.id,
        'product_id': line.pg_item_id.product_tmpl_id.product_variant_id.id,
        'product_qty': line.product_qty,
        'product_uom_id': line.uom_id.id,
    })

# 4. Deactivate previous mrp.bom for same product
old_boms = self.env['mrp.bom'].search([
    ('product_tmpl_id', '=', product_tmpl.id),
    ('id', '!=', bom.id),
    ('active', '=', True),
])
old_boms.write({'active': False})
```

Nothing else. No other Odoo model is touched.

---

# PART V — DOCUMENT 022: UI SPECIFICATION

## Chapter 10: User Interface Architecture

### 10.1 Navigation Structure

```
Top Menu: ProductGraph
├── Engineering
│   ├── Items                    (pg.item list + form)
│   ├── Revisions               (pg.revision list + form)
│   └── Engineering BOMs        (pg.ebom list + form)
├── Release
│   ├── Release to MRP          (pg.release list + wizard)
│   └── MRP BOM Traceability   (read-only view)
├── Analysis
│   ├── Where Used              (wizard-based)
│   └── Impact Analysis         (wizard-based)
├── Reports
│   ├── Indented BOM
│   ├── Flat BOM
│   ├── Where Used Report
│   └── Release Audit Trail
└── Configuration
    ├── Item Types
    ├── Sequence Configuration
    ├── User Groups
    └── Settings
```

### 10.2 pg.item Form — Required Buttons

**Header Buttons (visible based on state):**
- `Submit for Review` — visible when state=engineering, role=PG Engineer
- `Release` — visible when state=review, role=PG Approver  
- `Create Revision` — visible when state=released, role=PG Engineer
- `Obsolete` — visible always (when allowed), role=PG Admin
- `Where Used` — always visible, opens wizard

**Smart Buttons (stat buttons above form):**
- Revisions count → pg.revision list filtered by item
- Active EBOM count → pg.ebom list filtered by root_item_id
- MRP BOMs count → mrp.bom list filtered by pg_item reference
- Documents count → attachment count
- MO History count → mrp.production filtered by product
- Suppliers count → pg.vendor.part count (Phase 2)

**Form Tabs:**
1. General — core fields (name, type, description, notes, UOM)
2. Revisions — pg.revision list for this item
3. EBOM Usage — where this item appears as a child line
4. Manufacturing — linked mrp.bom records + open MO count
5. Documents — ir.attachment list filtered to this record
6. Audit History — pg.audit.log entries for this item

### 10.3 pg.ebom Form — Required Buttons

**Header Buttons:**
- `Validate EBOM` — runs pre-release checks, displays report (no state change)
- `Submit for Review` — draft→review
- `Release to MRP` — opens pg.release wizard (from review state)
- `Where Used` — applies to root item

**EBOM Line Requirements:**
- Inline editable grid (tree view with editable=bottom)
- Sequence reordering via drag handle
- Alternate group displayed as badge
- Optional flag displayed as checkbox
- Child item state shown as colored badge in line

### 10.4 Release Wizard — Step-by-Step

Step 1: Pre-release validation display (pass/fail per check)  
Step 2: Frozen MO warning (if applicable — blocking display, not blocking action)  
Step 3: Release reason input (required text)  
Step 4: Confirm + Execute button  
Step 5: Success screen with links to generated mrp.bom  

---

# PART VI — DOCUMENT 023: API BIBLE

## Chapter 11: REST API Specification

Module: `product_graph_api`  
Authentication: OAuth2 (same as Odoo standard) + API Keys  
Base Path: `/api/v1/productgraph/`  
Format: JSON  

### 11.1 Items Endpoints

```
GET    /api/v1/productgraph/items
       ?state=released&type=assembly&limit=50&offset=0
       Returns: [{id, part_number, name, state, item_type, active_revision}]

GET    /api/v1/productgraph/items/{id}
       Returns: Full item record with related revision list and EBOM links

POST   /api/v1/productgraph/items
       Body: {name, item_type, description, uom_id}
       Returns: {id, part_number, state: 'concept'}
       Requires: PG Engineer role

PATCH  /api/v1/productgraph/items/{id}
       Body: {name, description, engineering_notes}
       Restrictions: Cannot patch state-locked fields on Released items
       Requires: PG Engineer role

DELETE /api/v1/productgraph/items/{id}
       DISABLED — returns 405 Method Not Allowed
       Use PATCH with state transition to obsolete
```

### 11.2 Revisions Endpoints

```
GET    /api/v1/productgraph/revisions?item_id={id}
GET    /api/v1/productgraph/revisions/{id}
POST   /api/v1/productgraph/revisions
       Body: {pg_item_id, name, change_summary}
POST   /api/v1/productgraph/revisions/{id}/submit
POST   /api/v1/productgraph/revisions/{id}/release   (Approver only)
POST   /api/v1/productgraph/revisions/{id}/reject    (Approver only, reason required)
```

### 11.3 EBOMs Endpoints

```
GET    /api/v1/productgraph/eboms?root_item_id={id}
GET    /api/v1/productgraph/eboms/{id}
GET    /api/v1/productgraph/eboms/{id}/explosion
       Returns: Full indented BOM structure as JSON tree
POST   /api/v1/productgraph/eboms
POST   /api/v1/productgraph/eboms/{id}/lines
       Body: {pg_item_id, product_qty, uom_id, sequence, is_optional}
DELETE /api/v1/productgraph/eboms/{id}/lines/{line_id}
       (Only allowed when EBOM state=draft)
```

### 11.4 Releases Endpoints

```
GET    /api/v1/productgraph/releases
GET    /api/v1/productgraph/releases/{id}
POST   /api/v1/productgraph/releases
       Body: {pg_ebom_id, release_reason, effective_date}
       Triggers: Full release workflow (async)
GET    /api/v1/productgraph/releases/{id}/status
       Returns: {state, progress, generated_bom_id, frozen_mo_count}
```

### 11.5 Where Used Endpoint

```
GET    /api/v1/productgraph/items/{id}/where-used
       ?depth=3 (default: unlimited)
       Returns: {item_id, parents: [{ebom_id, parent_item_id, qty, depth, ...}]}
```

### 11.6 Reserved CAD Connector Endpoints (Phase 2)

```
POST   /api/v1/productgraph/cad/sync
       Body: {cad_system, session_token, items: [...], assemblies: [...]}
GET    /api/v1/productgraph/cad/status/{sync_id}
```

---

# PART VII — DOCUMENT 024: NON-FUNCTIONAL REQUIREMENTS

## Chapter 12: Performance and Scale Requirements

### 12.1 Data Volume Targets

| Metric | Phase 1 Target | Phase 2 Target |
|---|---|---|
| Total pg.item records | 100,000 | 500,000 |
| Total pg.ebom.line records | 1,000,000 | 5,000,000 |
| Total pg.revision records | 500,000 | 2,000,000 |
| Assemblies (multi-level) | 10,000 | 50,000 |
| Max BOM depth | 15 levels | 30 levels |
| Concurrent users | 50 | 200 |

### 12.2 Response Time Targets

| Operation | Target | Hard Limit |
|---|---|---|
| Item list view (1000 records) | < 1 second | 2 seconds |
| Item search by part number | < 0.5 seconds | 1 second |
| EBOM display (50-line assembly) | < 2 seconds | 3 seconds |
| Where-Used (3 levels deep) | < 3 seconds | 5 seconds |
| BOM explosion (10-level, 500 parts) | < 5 seconds | 10 seconds |
| Release action (50-line EBOM) | < 10 seconds | 30 seconds |
| Indented BOM report (PDF) | < 15 seconds | 30 seconds |

### 12.3 Required Database Indexes

```sql
-- pg.item
CREATE INDEX pg_item_part_number_idx ON pg_item (part_number, company_id);
CREATE INDEX pg_item_state_idx ON pg_item (state);
CREATE INDEX pg_item_type_idx ON pg_item (item_type);

-- pg.revision
CREATE INDEX pg_revision_item_state_idx ON pg_revision (pg_item_id, state);

-- pg.ebom.line (critical for where-used performance)
CREATE INDEX pg_ebom_line_item_idx ON pg_ebom_line (pg_item_id);
CREATE INDEX pg_ebom_line_ebom_idx ON pg_ebom_line (ebom_id);
```

### 12.4 Auditability Requirements

- **No hard deletes:** All pg.* records are retained permanently (concept/draft only deletable via admin)
- **Every release:** Recorded in pg.release with immutable audit fields
- **Every state transition:** Recorded in pg.audit.log with actor, timestamp, IP
- **Every field change on critical models:** Tracked via Odoo `tracking=True`
- **Audit log retention:** Minimum 10 years (configurable per company compliance requirements)

### 12.5 Upgradeability Requirements

- No destructive migrations (no DROP COLUMN, no DELETE on data tables)
- All migrations must be additive or data-transforming only
- Module must survive Odoo 19→20 upgrade without manual data intervention
- All custom fields on Odoo models prefixed `pg_` to avoid future namespace collision

---

# PART VIII — DOCUMENT 025: DIGITAL TWIN STRATEGY

## Chapter 13: Digital Twin Architecture (Phase 3+)

### 13.1 Why ProductGraph Is the Foundation

The Digital Twin of a physical product requires knowing exactly:
1. What it was designed to be (EBOM + revisions) ← ProductGraph owns this
2. What it was built as (mrp.production + actual components) ← Odoo MRP + PG traceability
3. Where it is installed (asset registry) ← Phase 3 extension
4. How it is performing (IoT telemetry) ← Phase 4 extension

Without ProductGraph establishing the engineering baseline, a Digital Twin has no
authoritative reference design to compare against.

### 13.2 Data Model Extension Path

```
Phase 1 (now):
pg.item → pg.revision → pg.ebom → mrp.bom → mrp.production
                                              ↑
                                    pg_revision_code stamped here

Phase 3 (future):
pg.item + pg.revision
    ↓ (new: pg.item.asset_template)
pg.asset.template    ← "the design of an asset class"
    ↓ (new: pg.installed.asset)
pg.installed.asset   ← "this specific unit, serial number XYZ, at customer ABC"
    ↓ (inherits from maintenance.equipment)
maintenance.equipment ← Odoo native
    ↓
maintenance.request ← Spare part requirements
    ↓
pg.spare.part.list ← Which pg.items are approved as spares for this asset class

Phase 4 (future):
pg.installed.asset
    ↓ (new: pg.iot.device)
pg.iot.device        ← IoT device registration linked to installed asset
    ↓
pg.telemetry.reading ← Time-series sensor data
    ↓
pg.maintenance.prediction ← ML-driven maintenance scheduling
```

### 13.3 Reserved Schema (Stub Now, Implement Later)

These fields should be stubbed as `fields.Char` or `fields.Many2one` stubs on
pg.item in Phase 1, so Phase 3 migration is additive rather than structural:

```python
# Stub in pg.item (Phase 1 — empty, not used):
asset_template_category = fields.Selection(
    selection=[('equipment', 'Equipment'), ('component', 'Component'), ('none', 'N/A')],
    string="Asset Category (Phase 3)",
    default='none',
    help="Reserved for Digital Twin asset classification. Not used in Phase 1.",
)
```

### 13.4 Competitive Differentiation

OpenBOM (the closest commercial alternative) has no maintenance, IoT, or Digital Twin
capability. This Phase 3/4 roadmap is the primary long-term differentiator for
ProductGraph. It transforms ProductGraph from a BOM tool into a product intelligence
platform.

---

# PART IX — DOCUMENT 026: MANUFACTURING GOVERNANCE

*See full document: `docs/026_Manufacturing_Governance.md`*

## Chapter 14: Manufacturing Governance Summary

### 14.1 The Five Core Rules

**Rule MG-1 (MO Freeze):**  
Open Manufacturing Orders are frozen at the BOM revision they were created with.
New releases do not retroactively update existing MOs.

**Rule MG-2 (PO Warning):**  
Open Purchase Orders receive a notification when a component revision changes.
The PO is not automatically amended. Procurement decides.

**Rule MG-3 (Inventory):**  
Existing old-revision inventory may be consumed unless the new revision is explicitly
flagged `incompatible_with_previous = True`.

**Rule MG-4 (Alternates):**  
Alternate part substitution is never automatic. A human must approve per-MO.

**Rule MG-5 (Obsolete Block):**  
An item cannot be obsoleted while open MOs or active MRP BOMs reference it.

---

# PART X — DOCUMENT 027: AI DEVELOPMENT GOVERNANCE

## Chapter 15: Rules for AI-Assisted Development

### 15.1 Mandatory Pre-Coding Steps

Before any AI agent writes code, it must:
1. Read this Architecture Bible (confirm by stating "Architecture Bible read: v1.0")
2. Read `CLAUDE.md` in full
3. Read the relevant specific governance document (020, 021, 022, 023, 024, 025, 026)
4. State the model(s) it will touch and confirm they are in the PG ownership list
5. State any Odoo model it will read from and confirm it will not write to it outside Release

### 15.2 Code Review Checklist (AI Must Self-Check)

Before submitting any code, AI must verify:
- [ ] No raw SQL on pg.* models
- [ ] No modification of Odoo core
- [ ] All state transitions write an audit log
- [ ] Released records reject writes (write() override present)
- [ ] No hard deletes on released records
- [ ] No mrp.bom creation outside PgRelease.action_execute_release()
- [ ] Security group check present on all user-triggered actions
- [ ] `sudo()` usage documented and justified
- [ ] No hardcoded IDs or company references
- [ ] Tests written for happy path, error path, and immutability

### 15.3 Forbidden Patterns (Absolute)

```python
# FORBIDDEN PATTERN 1: Raw SQL
self.env.cr.execute("UPDATE pg_item ...")

# FORBIDDEN PATTERN 2: Write to MRP outside Release
self.env['mrp.bom'].write({'active': False})  # outside action_execute_release

# FORBIDDEN PATTERN 3: Silent state mutation
self.state = 'released'  # without logging, validation, or notification

# FORBIDDEN PATTERN 4: Bypass security
self.sudo().some_dangerous_action()  # without documented justification

# FORBIDDEN PATTERN 5: Cascade delete
pg_item_id = fields.Many2one('pg.item', ondelete='cascade')  # use 'restrict'

# FORBIDDEN PATTERN 6: Phase 2 features in Phase 1 models
# Do not add eco_id, graph_node_id, etc. to Phase 1 models
```

### 15.4 Agent Alignment Protocol

When three or more AI agents work on the same codebase:
1. Each agent reads Architecture Bible before every session
2. Each agent prefixes commits: `[agent:claude-code]`, `[agent:codex]`, etc.
3. Agents do not overwrite each other's completed modules
4. Module boundaries are strict (see CLAUDE.md §3)
5. Any architectural deviation requires a human decision and Bible update

---

# PART XI — ROADMAP

## Chapter 16: Implementation Roadmap

### Phase 1 — MVP (Target: 3 months)

**In Scope:**
- `pg.item` with full lifecycle state machine
- `pg.revision` with immutability enforcement
- `pg.ebom` + `pg.ebom.line` with where-used
- `pg.release` with mrp.bom generation
- `pg.document` for engineering file management
- Security groups (all 6 levels)
- QWeb reports (Indented BOM, Where Used, Release Audit)
- REST API (Items, Revisions, EBOMs, Releases)
- Manufacturing traceability fields on mrp.production

**Excluded from Phase 1:**
- CAD connectors
- ECO / ECR workflows
- AVL / AML supplier management
- Graph relationship engine
- Asset registry linkage
- Digital Twin
- IoT integration

**Phase 1 Success Criteria:**
1. Engineer can create a 50-item multi-level EBOM in < 30 minutes
2. Where-Used returns correct results in < 3 seconds
3. Release generates correct mrp.bom in < 10 seconds
4. 4 open MOs are correctly frozen and notified on new release
5. Released revision rejects all write attempts
6. Full audit trail preserved for 100% of actions
7. 80%+ test coverage

### Phase 2 — Engineering Operations (Target: +3 months after Phase 1)

- Engineering Change Orders (pg.eco)
- AVL / AML management (pg.vendor, pg.manufacturer)
- CAD connector architecture (SOLIDWORKS first)
- Graph relationship engine (complex where-used)
- Multi-site / multi-company BOM support

### Phase 3 — Asset Registry (Target: +6 months after Phase 2)

- Installed asset linkage (pg.installed.asset → maintenance.equipment)
- Spare part catalog (pg.spare.part.list)
- Maintenance BOM integration
- Field service integration

### Phase 4 — Digital Twin (Target: +12 months after Phase 3)

- IoT device registration (pg.iot.device)
- Telemetry ingestion
- Maintenance prediction model
- Product Digital Twin dashboard

---

## Chapter 17: Open Questions (Decision Required Before Phase 1 Completion)

| # | Question | Urgency | Assigned To |
|---|---|---|---|
| Q-1 | Should part number sequences be configurable per item type, or fixed? | High | Architect |
| Q-2 | Should product.template auto-create at pg.item creation, or only at Release? | High | Architect |
| Q-3 | Multi-company: shared item catalog or separate per company? | High | Architect |
| Q-4 | What is the retention policy for pg.audit.log records? | Medium | Compliance |
| Q-5 | Should alternate group resolution be UI-side or ORM-side? | Medium | Engineer |
| Q-6 | CAD connector priority: SolidWorks, Fusion 360, or Onshape first? | Low | Product |

---

# APPENDICES

## Appendix A: Glossary

| Term | Definition |
|---|---|
| EBOM | Engineering BOM — the design-intent structure owned by ProductGraph |
| MBOM | Manufacturing BOM — the build-instruction structure owned by Odoo MRP |
| pg.item | The ProductGraph Engineering Item master record |
| pg.revision | A formal, versioned snapshot of a pg.item's design state |
| Release | The controlled action of converting an EBOM into an mrp.bom |
| AVL | Approved Vendor List — qualified suppliers for a component |
| AML | Approved Manufacturer List — qualified manufacturers |
| ECO | Engineering Change Order — formal process for changing Released items |
| Digital Thread | The data trail connecting design → manufacture → service → end-of-life |
| MO | Manufacturing Order (mrp.production in Odoo) |
| PO | Purchase Order (purchase.order in Odoo) |

## Appendix B: Document Inventory

| # | Document | File | Status |
|---|---|---|---|
| — | Architecture Bible | `ProductGraph_Architecture_Bible_v1.0.md` | ✅ Complete |
| — | CLAUDE.md | `CLAUDE.md` | ✅ Complete |
| — | Base Specification | `ProductGraph_Odoo19CE_Base_Specification_v0_1.md` | ✅ Reference |
| — | UX Simulation | `ProductGraph_UX_v2.html` | ✅ Complete |
| 020 | State Machine Bible | `docs/020_State_Machine_Bible.md` | ✅ Complete |
| 021 | Odoo Compatibility Matrix | `docs/021_Odoo_Compatibility_Matrix.md` | 🔲 To Do |
| 022 | UI Specification | `docs/022_UI_Specification.md` | 🔲 To Do |
| 023 | API Bible | `docs/023_API_Bible.md` | 🔲 To Do |
| 024 | NFR | `docs/024_NFR.md` | 🔲 To Do |
| 025 | Digital Twin Strategy | `docs/025_Digital_Twin_Strategy.md` | 🔲 To Do |
| 026 | Manufacturing Governance | `docs/026_Manufacturing_Governance.md` | ✅ Complete |
| 027 | AI Development Governance | `docs/027_AI_Development_Governance.md` | 🔲 To Do |

## Appendix C: Change Log

| Date | Version | Change | Author |
|---|---|---|---|
| 2026-06-08 | 1.0 | Initial Architecture Bible — consolidated from conversation export, Base Spec v0.1, UX Simulation v2, and all governance documents | Architecture Team |

---

*ProductGraph Architecture Bible v1.0*  
*ProductGraph for Odoo 19 Community Edition*  
*© 2026 ProductGraph Contributors — LGPL-3*
