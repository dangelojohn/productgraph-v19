// SPDX-License-Identifier: LGPL-3.0-only
namespace ProductGraph.SolidworksProxy;

public sealed class ProxyOptions
{
    public string VaultName { get; set; } = "";
    public string Username { get; set; } = "";
    public string Password { get; set; } = "";
    public string SharedSecret { get; set; } = "";
    public string ListenUrl { get; set; } = "http://0.0.0.0:9100";
}
