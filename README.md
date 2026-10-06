# fal.ai MCP integration

This repository is configured to use the [fal.ai](https://fal.ai) MCP server
with Claude Code. The server exposes fal's model-serving tools over an HTTP
transport.

## Skills for generating media

Two Claude Code skills in [`.claude/skills/`](./.claude/skills) teach any Claude
session in this repo how to use the fal MCP well. They are distilled from a real
production: a 60-second, 1080p commercial made with Seedance 2.5, Nano Banana Pro,
Lyria 3.5 and ElevenLabs SFX on fal ($333.92 of generation).

| Skill | Use it for | What it adds |
|---|---|---|
| [`fal-media`](./.claude/skills/fal-media/SKILL.md) | Any fal generation: images, video clips, music, SFX | MCP call pattern (submit, poll, fetch), a budget ledger with hard caps, cheap-draft-then-finish, prompt templates, likeness-filter recipes, review and QA |
| [`fal-commercial`](./.claude/skills/fal-commercial/SKILL.md) | A commercial or brand film, with 30 s / 15 s / 9:16 cutdowns | The production runbook: reference analysis, AI-judged concepts, parallel shot agents, three human gates, data-driven edit, typography, mix and delivery |

Claude loads them automatically when a request matches; you can also ask for one by
name. The local scripts need Python 3 with `numpy`, `opencv-python-headless` and
`pillow`, plus `ffmpeg` on the PATH; the optional music-analysis tools also need
`librosa` and `soundfile`. The cut-list renderer and mix are documented in
[`fal-commercial/reference/cutlist-format.md`](./.claude/skills/fal-commercial/reference/cutlist-format.md).

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
