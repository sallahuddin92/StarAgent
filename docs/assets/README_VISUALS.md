# README Visuals Plan

This repo does not currently include polished screenshots.

If you want to add visuals to the GitHub README, these are the recommended captures:

1. Open WebUI connected to StarAgent showing:
   - a normal fast-path chat
   - an approval-gated write request returning an approval payload
   - a `yes` resume completing the write
2. CLI demo:
   - `staragent status`
   - `staragent ask "Reply with exactly: OK"`
   - `staragent agent "Inspect the app folder …"`
3. Claude Code MCP demo:
   - MCP server registered
   - one tool call (ex: `staragent_status`)

Place images here:

- `docs/assets/webui_approval.png`
- `docs/assets/cli_demo.png`
- `docs/assets/claude_mcp.png`

Then reference them from `README.md` using relative paths:

```md
![Open WebUI Approval](docs/assets/webui_approval.png)
```

