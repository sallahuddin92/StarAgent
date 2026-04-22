# README Visuals Plan

This repo includes a small set of dashboard screenshots for demoability.

If you want to add visuals to the GitHub README, these are the recommended captures:

0. StarAgent dashboard:
   - task list with status badges + filters visible
   - a paused task showing the “Approval required” banner
   - artifact list with primary artifact highlighted

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

- `docs/assets/dashboard_tasks.png`
- `docs/assets/dashboard_approval.png`
- `docs/assets/dashboard_detail.png`
- `docs/assets/webui_approval.png`
- `docs/assets/cli_demo.png`
- `docs/assets/claude_mcp.png`
- `docs/assets/banner.png` (optional)

Then reference them from `README.md` using relative paths:

```md
![Open WebUI Approval](docs/assets/webui_approval.png)
```

## Notes (Safety)

- If you automate dashboard screenshot capture, prefer passing the API key via the URL fragment (hash) instead of query params so it is not sent to the server or recorded in access logs:
  - Good: `/dashboard#api_key=...`
  - Avoid: `/dashboard?api_key=...`
