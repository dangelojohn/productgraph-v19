// SPDX-License-Identifier: LGPL-3.0-only
//
// EpdmGateway — wraps IEdmVault5 + IEdmFile5 + IEdmBomMgr.
//
// All vault calls go through this class so the HTTP handlers stay shape-
// only. The gateway maintains a lazy IEdmVault5 instance per process —
// EPDM is reference-counted so reusing the connection across requests is
// safe and standard.

#pragma warning disable CA1416  // platform-specific (Windows only)

using EPDM.Interop.epdm;
using Microsoft.Extensions.Options;

namespace ProductGraph.SolidworksProxy;

public sealed class EpdmGateway
{
    private readonly ProxyOptions _opts;
    private readonly ILogger<EpdmGateway> _log;
    private IEdmVault5? _vault;
    private readonly object _vaultLock = new();

    public EpdmGateway(IOptions<ProxyOptions> opts, ILogger<EpdmGateway> log)
    {
        _opts = opts.Value;
        _log = log;
    }

    // ──────────────────────────────────────────────────────────────────
    // Vault attachment
    // ──────────────────────────────────────────────────────────────────
    private IEdmVault5 GetVault()
    {
        if (_vault is not null) return _vault;
        lock (_vaultLock)
        {
            if (_vault is not null) return _vault;
            if (string.IsNullOrWhiteSpace(_opts.VaultName))
                throw new InvalidOperationException(
                    "PG_PROXY__VAULTNAME not configured");
            var v = (IEdmVault5)new EdmVault5();
            if (!string.IsNullOrEmpty(_opts.Username))
            {
                v.Login(_opts.Username, _opts.Password, _opts.VaultName);
            }
            else
            {
                // Pass-through windows-auth.
                v.LoginAuto(_opts.VaultName, 0);
            }
            _vault = v;
        }
        return _vault;
    }

    // ──────────────────────────────────────────────────────────────────
    // /pdm/info
    // ──────────────────────────────────────────────────────────────────
    public object Info()
    {
        try
        {
            var vault = GetVault();
            return new
            {
                reachable = true,
                vault = _opts.VaultName,
                version = ((IEdmVault7)vault).Version,
                detail = "",
            };
        }
        catch (Exception exc)
        {
            return new
            {
                reachable = false,
                vault = _opts.VaultName,
                detail = exc.Message,
            };
        }
    }

    // ──────────────────────────────────────────────────────────────────
    // /pdm/documents
    // ──────────────────────────────────────────────────────────────────
    public IEnumerable<object> ListDocuments(string? folderRef)
    {
        var vault = GetVault();
        IEdmFolder5? root;
        if (string.IsNullOrEmpty(folderRef))
        {
            root = vault.RootFolder;
        }
        else if (int.TryParse(folderRef, out var folderId))
        {
            root = (IEdmFolder5)vault.GetObject(EdmObjectType.EdmObject_Folder, folderId);
        }
        else
        {
            // Treat as path
            root = vault.GetFolderFromPath(folderRef);
        }
        if (root is null) yield break;

        var pos = root.GetFiles();
        while (!pos.IsNull)
        {
            var file = (IEdmFile5)pos.GetNext(out _);
            if (file is null) continue;
            yield return new
            {
                path = file.GetLocalPath(root.ID),
                name = file.Name,
                rev = file.CurrentRevision,
            };
        }
    }

    // ──────────────────────────────────────────────────────────────────
    // /pdm/document
    // ──────────────────────────────────────────────────────────────────
    public object? GetDocument(string path)
    {
        var vault = GetVault();
        IEdmFolder5? folder;
        IEdmFile5 file;
        try
        {
            file = (IEdmFile5)vault.GetFileFromPath(path, out folder);
        }
        catch
        {
            return null;
        }
        if (file is null) return null;

        // Card variables — read the active configuration's data card.
        var cardVariables = new Dictionary<string, object?>();
        try
        {
            var card = file.GetCard("File");
            foreach (IEdmCardVarValue v in card.GetVars())
            {
                cardVariables[v.VariableName] = v.GetValueAsString();
            }
        }
        catch (Exception exc)
        {
            cardVariables["__card_read_error"] = exc.Message;
        }

        // BOM rows.
        var bom = new List<object>();
        try
        {
            var bomMgr = (IEdmBomMgr)vault.CreateUtility(EdmUtility.EdmUtil_BomMgr);
            var bomTypes = bomMgr.GetBomLayouts();
            int bomTypeId = bomTypes.Length > 0 ? bomTypes[0] : 0;
            var view = (IEdmBomView)file.GetActiveBOM(bomTypeId, file.CurrentVersion);
            if (view is not null)
            {
                var rows = view.GetRows();
                foreach (IEdmBomCell row in rows)
                {
                    string? childPath = row.GetVar("Filename")?.ToString();
                    string qty = row.GetVar("Quantity")?.ToString() ?? "1.0";
                    string refDes = row.GetVar("Reference")?.ToString() ?? "";
                    if (string.IsNullOrEmpty(childPath)) continue;
                    bom.Add(new
                    {
                        child_part_number = childPath,
                        qty = double.TryParse(qty, out var q) ? q : 1.0,
                        @ref = refDes,
                    });
                }
            }
        }
        catch (Exception exc)
        {
            _log.LogWarning(exc, "BOM read failed for {Path}", path);
        }

        return new
        {
            part_number = TryStringVar(cardVariables, "Number")
                          ?? Path.GetFileNameWithoutExtension(path),
            name = TryStringVar(cardVariables, "Description") ?? file.Name,
            description = TryStringVar(cardVariables, "Description") ?? "",
            item_type = InferItemType(file.Name),
            card_variables = cardVariables,
            bom,
        };
    }

    private static string? TryStringVar(Dictionary<string, object?> d, string key)
    {
        if (d.TryGetValue(key, out var v) && v is string s && s.Length > 0)
            return s;
        return null;
    }

    private static string InferItemType(string filename)
    {
        var ext = Path.GetExtension(filename).ToLowerInvariant();
        return ext switch
        {
            ".sldasm" => "assembly",
            ".sldprt" => "part",
            ".slddrw" => "document",
            _ => "part",
        };
    }
}
