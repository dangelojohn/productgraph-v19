// SPDX-License-Identifier: LGPL-3.0-only
//
// SOLIDWORKS PDM proxy — reference implementation of the contract in
// docs/solidworks_proxy_spec.md. Wraps the EPDM COM API
// (IEdmVault5 / IEdmFile5 / IEdmBomMgr) and exposes it over HTTP so the
// Linux-side product_graph_cad_solidworks driver can read engineering
// metadata without local COM dependencies.
//
// One-file Minimal API for easy deployment. Config via appsettings.json
// or environment variables:
//   PG_PROXY__VAULTNAME        — the EPDM vault to attach to (required)
//   PG_PROXY__USERNAME         — vault credentials (optional;
//   PG_PROXY__PASSWORD           omit for Windows-auth pass-through)
//   PG_PROXY__SHAREDSECRET     — X-Proxy-Key header value the driver must send
//   PG_PROXY__LISTENURL        — default http://0.0.0.0:9100
//
// Build (on a host with SOLIDWORKS PDM Client installed):
//   dotnet publish -c Release -r win-x64 --self-contained false
//
// Run:
//   solidworks_proxy.exe

#pragma warning disable CA1416  // platform-specific (Windows only)

using System.Net;
using EPDM.Interop.epdm;
using ProductGraph.SolidworksProxy;

var builder = WebApplication.CreateBuilder(args);

builder.Services.Configure<ProxyOptions>(
    builder.Configuration.GetSection("PG_PROXY"));
builder.Services.AddSingleton<EpdmGateway>();
builder.Services.AddLogging();

var listenUrl = builder.Configuration["PG_PROXY:LISTENURL"]
                ?? "http://0.0.0.0:9100";
builder.WebHost.UseUrls(listenUrl);

var app = builder.Build();

// ─── X-Proxy-Key middleware ─────────────────────────────────────────────
app.Use(async (ctx, next) =>
{
    var configured = app.Services.GetRequiredService<
        Microsoft.Extensions.Options.IOptions<ProxyOptions>>().Value
        .SharedSecret ?? "";
    if (!string.IsNullOrEmpty(configured))
    {
        var supplied = ctx.Request.Headers["X-Proxy-Key"].ToString();
        if (supplied != configured)
        {
            ctx.Response.StatusCode = (int)HttpStatusCode.Unauthorized;
            await ctx.Response.WriteAsJsonAsync(new
            {
                error = "bad_proxy_key",
            });
            return;
        }
    }
    await next();
});

// ─── /pdm/info ──────────────────────────────────────────────────────────
app.MapGet("/pdm/info", (EpdmGateway gw) =>
{
    try
    {
        var info = gw.Info();
        return Results.Json(info);
    }
    catch (Exception exc)
    {
        return Results.Json(new
        {
            reachable = false,
            detail = exc.Message,
        });
    }
});

// ─── /pdm/documents ─────────────────────────────────────────────────────
app.MapGet("/pdm/documents", (string? folder, EpdmGateway gw) =>
{
    try
    {
        var rows = gw.ListDocuments(folder);
        return Results.Json(rows);
    }
    catch (Exception exc)
    {
        return Results.Problem(exc.Message);
    }
});

// ─── /pdm/document ──────────────────────────────────────────────────────
app.MapGet("/pdm/document", (string path, EpdmGateway gw) =>
{
    try
    {
        var doc = gw.GetDocument(path);
        if (doc is null) return Results.NotFound();
        return Results.Json(doc);
    }
    catch (Exception exc)
    {
        return Results.Problem(exc.Message);
    }
});

app.Run();
