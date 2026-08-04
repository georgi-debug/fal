# fal.ai MCP integration

This repository is configured to use the [fal.ai](https://fal.ai) MCP server
with Claude Code. The server exposes fal's model-serving tools over an HTTP
transport.

## Configuration

The connection is defined in [`.mcp.json`](./.mcp.json) at the project scope, so
anyone who opens this repo in Claude Code picks it up automatically:

```json
{
  "mcpServers": {
    "fal-ai": {
      "type": "http",
      "url": "https://mcp.fal.ai/mcp",
      "headers": {
        "Authorization": "Bearer ${FAL_MCP_KEY}"
      }
    }
  }
}
```

The authorization token is **not** hardcoded. Claude Code expands
`${FAL_MCP_KEY}` from the environment at connect time, which keeps the secret
out of version control.

## Setup

1. Get your fal.ai key from <https://fal.ai/dashboard/keys>. The value is the
   full token that follows `Bearer ` (format: `<key-id>:<key-secret>`).

2. Make it available to Claude Code, either by exporting it:

   ```bash
   export FAL_MCP_KEY="your-fal-key-id:your-fal-key-secret"
   ```

   or by copying `.env.example` to `.env` and filling it in (`.env` is
   git-ignored).

3. Start Claude Code in this directory. Approve the `fal-ai` server when
   prompted, then verify it connected:

   ```bash
   claude mcp list
   ```

## Adding the server manually

The project config above is equivalent to running:

```bash
claude mcp add --transport http fal-ai \
  https://mcp.fal.ai/mcp \
  --header "Authorization: Bearer $FAL_MCP_KEY"
```

## Security note

Treat the fal.ai key as a secret. If it is ever committed or shared in
plaintext, rotate it from the fal.ai dashboard.
