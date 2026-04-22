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

## What you get

Tools:
- Preferred: `staragent_ask`, `staragent_agent`, `staragent_status` (etc.)
- Legacy compatible: `macagent_ask`, `macagent_agent`, `macagent_status` (etc.)

## Notes

- StarAgent remains the control plane; Ollama remains inference.
- The MCP server is a thin adapter; it does not bypass approval/continuation safety.
