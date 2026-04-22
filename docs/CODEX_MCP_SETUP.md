# Codex MCP Setup (Template)

Codex supports MCP servers in CLI/IDE workflows. StarAgent MCP server runs over stdio and exposes StarAgent as tools.

This document provides a template because exact Codex MCP config formats can vary by version and environment.

## Prereqs

1. Start StarAgent:
```bash
./scripts/start_staragent.sh
```

2. Set env vars for the MCP subprocess:
```bash
export STARAGENT_BASE_URL="http://127.0.0.1:8095/v1"
export STARAGENT_API_KEY="local-dev-key"
export STARAGENT_DEFAULT_MODEL="gemma4:e2b"
```

## MCP server command

```bash
python3 -m mcp.server
```

## Codex CLI (non-interactive) note

Codex `exec` mode may elicit user confirmation for MCP tool calls. In pure `codex exec` automation runs, that elicitation can result in cancelled tool calls.

For strict non-interactive validation, you can run Codex with approvals bypassed and local shell tools disabled (so only MCP tool calls are possible):

```bash
codex exec --ephemeral --dangerously-bypass-approvals-and-sandbox \
  --disable shell_tool --disable unified_exec \
  "Call the MCP tool staragent_status with arguments {project_id:'codex-mcp', conversation_id:'codex-mcp-1'}."
```

If you are using Codex interactively (desktop/CLI TUI), normal confirmation prompts are supported and you do not need the bypass flags.

## What you get

Tools:
- Preferred: `staragent_ask`, `staragent_agent`, `staragent_status` (etc.)
- Legacy compatible: `macagent_ask`, `macagent_agent`, `macagent_status` (etc.)

## Notes

- StarAgent remains the control plane; Ollama remains inference.
- The MCP server is a thin adapter; it does not bypass approval/continuation safety.
