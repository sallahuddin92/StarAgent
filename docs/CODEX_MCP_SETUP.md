# Codex MCP Setup (Template)

Codex supports MCP servers in CLI/IDE workflows. MacAgent MCP server runs over stdio and exposes MacAgent as tools.

This document provides a template because exact Codex MCP config formats can vary by version and environment.

## Prereqs

1. Start MacAgent:
```bash
./scripts/start_macagent.sh
```

2. Set env vars for the MCP subprocess:
```bash
export MACAGENT_BASE_URL="http://127.0.0.1:8095/v1"
export MACAGENT_API_KEY="local-dev-key"
export MACAGENT_DEFAULT_MODEL="gemma4:e2b"
```

## MCP server command

```bash
python3 -m mcp.server
```

## What you get

Tools:
- `macagent_ask`, `macagent_agent`
- `macagent_approve`, `macagent_reject`
- `macagent_continue`
- `macagent_status`, `macagent_smoke_test`

## Notes

- MacAgent remains the control plane; Ollama remains inference.
- The MCP server is a thin adapter; it does not bypass approval/continuation safety.

