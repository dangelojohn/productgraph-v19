# SOLIDWORKS PDM Proxy — API Contract

The `product_graph_cad_solidworks` driver is an HTTP client. It talks to
a small Windows-side proxy service that wraps the EPDM COM API
(`IEdmVault5`, `IEdmFile5`, `IEdmBomMgr`) and re-exposes it as JSON.

The proxy is **deployment-specific** (vault layout, auth, host) and is
not shipped as part of ProductGraph. This document is the contract the
driver requires from any proxy implementation — write a C# .NET 6+
service, a PowerShell wrapper, a Go binary with COM bindings, whatever
fits your shop.

## Why a proxy

SOLIDWORKS EPDM COM is Windows-only and requires `pywin32` + the
SOLIDWORKS PDM client installed locally. The Odoo container runs on
Linux. Going through a Windows-side proxy keeps the driver Linux-native
and the COM-heavy code on a host where COM works.

## Endpoints

All endpoints return `application/json`. All accept an optional shared
secret in the `X-Proxy-Key` request header.

### `GET /pdm/info`

Returns the vault status — used by `action_test_connection` in the
ProductGraph CAD form.

```json
{
    "reachable": true,
    "vault":     "MyVault",
    "version":   "2024.2.0",
    "detail":    ""
}
```

* `reachable` (bool, required) — true iff the proxy can talk to the vault
* `vault` (string, optional) — vault display name
* `version` (string, optional) — PDM client / vault version
* `detail` (string, optional) — free-form note shown in the Odoo chatter
  when the test fires

### `GET /pdm/documents`

Returns the lightweight listing of documents the driver should pull.
Used by `list_documents`.

Query params:
* `folder` (string, optional) — EFolder id to scope the listing.
  Omit for the full vault.

Response: JSON array of `{path}` rows.

```json
[
    {"path": "\\\\MyVault\\Projects\\ASM-001.SLDASM"},
    {"path": "\\\\MyVault\\Projects\\ASM-002.SLDASM"}
]
```

The driver discards every other field on this listing — only `path` is
required. Additional fields (`name`, `rev`, etc.) are tolerated for
human readability.

### `GET /pdm/document`

Returns the full document payload — card variables + 1-level BOM — for
one document. Used by `pull_document`.

Query params:
* `path` (string, required) — EPDM path (same shape returned by
  `/pdm/documents`)

Response: full `CadDocument` payload.

```json
{
    "part_number":   "ASM-001",
    "name":          "Top assembly",
    "description":   "<p>...</p>",
    "item_type":     "assembly",
    "card_variables": {
        "width_mm":  600,
        "height_mm": 720,
        "material":  "plywood"
    },
    "bom": [
        {"child_part_number": "PRT-001", "qty": 2.0, "ref": "P1"},
        {"child_part_number": "PRT-002", "qty": 1.0, "ref": ""}
    ]
}
```

Fields:
* `part_number` (string, required) — the canonical part number used as
  `pg.item.part_number`
* `name` (string, required) — display name
* `description` (string, optional) — HTML allowed; copied to
  `pg.item.description` verbatim
* `item_type` (string, optional) — one of `pg.item.item_type` selection
  values (assembly / part / purchased / document / software / service);
  defaults to `part` if omitted
* `card_variables` (object, optional) — keys become candidate property
  values on the item's draft revision; the connector resolves them
  against the item-type's property templates
* `bom` (array, optional) — 1-level BOM; each entry has
  `child_part_number` (required), `qty` (optional, default 1.0),
  `ref` (optional, e.g. R12 for an electronic component)

Return HTTP 404 if `path` is unknown — the driver treats that as a
"document gone" and skips it gracefully.

## Implementation notes

### Walking the EBOM in EPDM

The standard pattern in EPDM COM is:

```csharp
var vault = (IEdmVault5)Activator.CreateInstance(...);
vault.LoginAuto(vaultName, 0);

var file = vault.GetFileFromPath(path, out var folder);
var bomMgr = (IEdmBomMgr)vault.CreateUtility(EdmUtility.EdmUtil_BomMgr);
var bomView = bomMgr.GetActiveBomView(file.ID, folder.ID, ...);
foreach (IEdmBomCell cell in bomView.GetRows()) {
    var childPath = cell.GetVar("Filename");
    var qty = (double)cell.GetVar("Quantity");
    ...
}
```

The driver expects the proxy to surface the *computed BOM*, not the
parts-list driven view. If your shop has a custom BOM type set on the
vault, configure the proxy to use that type id.

### Card variables

Pull the card via `IEdmFile5.GetCard("File").GetVars()` and serialise
every variable on the active config to `card_variables`. The driver
filters to known property-template fields on the Odoo side; sending
extras is harmless.

### Auth

Vault credentials live on the proxy host. The proxy authenticates
**inbound** via the optional `X-Proxy-Key` header — set this to a
strong shared secret if the proxy is reachable over a network the
vault credentials shouldn't traverse.

## Reference implementation

A C# .NET 6+ ASP.NET Minimal API reference implementation should live
under `services/solidworks_proxy/` once SOLIDWORKS PDM access is
available. Until then this document is the contract; any conforming
implementation works with the shipped driver.
