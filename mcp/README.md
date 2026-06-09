# product-graph-mcp

MCP server that exposes ProductGraph (Odoo 19 CE) to LLM agents — Claude, GPT, and any other Model Context Protocol-compatible client.

This is the **sidecar** half of the ProductGraph deployment (Decision D5). It runs in its own container, alongside the Odoo container, and talks to Odoo over XML-RPC using a personal API key.

## Tools exposed

Read-only:

| Tool | Description |
|---|---|
| `list_items` | List engineering items, filtered by state/type/category. |
| `get_item` | Get a single item with its revisions. |
| `search_catalog` | Free-text search over part_number and name. |
| `where_used` | All parent EBOMs of an item, from the materialized closure. |
| `bom_explode` | Multi-level flat explosion of an EBOM. |
| `validate_ebom` | Pre-release validation report. |
| `simulate_release` | Dry-run of a release — errors and warnings, no side effects. |
| `get_release` | Inspect a `pg.release` record. |

Mutating (requires `group_pg_engineer` on the operating user — re-checked at the tool boundary per Decision D6):

| Tool | Description |
|---|---|
| `propose_revision` | Create a new draft revision against an item. |
| `comment_on_revision` | Post a chatter note on a revision. |
| `attach_document` | Upload a binary document to a revision. |

The full release flow is intentionally NOT a single-call tool. Executing a release writes `mrp.bom`, freezes MOs, and posts notifications — that's a human-in-the-loop action through the wizard or the REST `/releases` endpoint.

## Configuration

Drop a `.env` file in this directory (see `.env.example`):

```env
PRODUCTGRAPH_ODOO_URL=https://southbrook.example.com
PRODUCTGRAPH_DB=southbrook
PRODUCTGRAPH_ODOO_USER=mcp-bot@southbrook.example.com
PRODUCTGRAPH_API_KEY=...
PRODUCTGRAPH_MCP_TRANSPORT=sse
PRODUCTGRAPH_MCP_HOST=0.0.0.0
PRODUCTGRAPH_MCP_PORT=9234
```

Generate the API key in Odoo: My Profile → Account Security → New API Key. The user must have at least `group_pg_engineer` for mutating tools to work.

## Run locally

```bash
pip install -e .
PRODUCTGRAPH_MCP_TRANSPORT=stdio product-graph-mcp
```

Or with Claude Desktop, add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "product-graph": {
      "command": "product-graph-mcp",
      "env": {
        "PRODUCTGRAPH_ODOO_URL": "...",
        "PRODUCTGRAPH_DB": "...",
        "PRODUCTGRAPH_ODOO_USER": "...",
        "PRODUCTGRAPH_API_KEY": "..."
      }
    }
  }
}
```

## Run on the QNAP

```bash
docker build -t product-graph-mcp:0.1.0 .
docker compose up -d
```

The container joins the existing `alfacore-caddy` network and exposes SSE on `:9234`. Route to it via cloudflared with Zero-Trust on the public hostname (e.g. `mcp.productgraph.southbrook.example.com`).

## Auth model (Decision D6)

1. Cloudflared Zero-Trust gates the public SSE route.
2. Odoo API key authenticates the XML-RPC session.
3. Each mutating tool re-checks `group_pg_engineer` at the call site — defense in depth.

OAuth2 device flow is explicitly out of scope for Phase 1.
