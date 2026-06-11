# SOLIDWORKS PDM Proxy (reference implementation)

The Windows-side HTTP service the `product_graph_cad_solidworks` Odoo
driver talks to. Wraps the EPDM COM API (`IEdmVault5`, `IEdmFile5`,
`IEdmBomMgr`) and exposes it over HTTP per the contract in
[`docs/solidworks_proxy_spec.md`](../../docs/solidworks_proxy_spec.md).

## Prerequisites

* Windows Server 2019+ or Windows 10/11 Pro
* SOLIDWORKS PDM Client installed (for the EPDM Type Library at
  `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS PDM\EPDM.Interop.epdm.dll`)
* .NET 6 SDK + runtime
* Network reachability from the Odoo container to this host's
  configured `ListenUrl` (default `http://0.0.0.0:9100`)

## Build

On the deployment host (a Windows machine with SOLIDWORKS PDM Client
installed):

```powershell
cd services\solidworks_proxy
dotnet publish -c Release -r win-x64 --self-contained false
```

Output lands in `bin\Release\net6.0-windows\win-x64\publish\`.

## Configure

Copy `appsettings.json` to the publish folder and fill in:

```json
{
  "PG_PROXY": {
    "VaultName":     "MyEpdmVaultName",
    "Username":      "domain\\user",
    "Password":      "secret",
    "SharedSecret":  "long-random-string-shared-with-odoo",
    "ListenUrl":     "http://0.0.0.0:9100"
  }
}
```

Or use environment variables — every setting is overridable as
`PG_PROXY__<key>` (double underscore).

## Run

```powershell
.\solidworks_proxy.exe
```

Or install as a Windows service via `sc.exe create` or `nssm install`.

## Configure the Odoo driver

In Odoo, create a `pg.cad.connector` record:

* **Driver**: SOLIDWORKS PDM
* **Config JSON**:

```json
{
    "proxy_url":      "http://win-pdm-host:9100",
    "vault":          "MyEpdmVaultName",
    "folder_efolder": "12345",
    "api_key":        "long-random-string-shared-with-odoo"
}
```

`folder_efolder` is optional — omit for a full-vault pull. `api_key`
must match `PG_PROXY:SharedSecret` on the proxy side.

## What the proxy does NOT do

* No write-back to the vault. Read-only.
* No vault discovery — `VaultName` is configured per deployment.
* No file content streaming — metadata only. Drawing-export workflows
  belong on the FreeCAD bridge service.
