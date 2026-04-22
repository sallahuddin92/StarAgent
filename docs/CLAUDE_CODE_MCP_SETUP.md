# Claude Code MCP Setup (Template)

Claude Code supports MCP servers. StarAgent MCP server runs over stdio and exposes StarAgent as tools.

This document provides a conservative template because Claude Code configuration formats can change between versions.

## Prereqs

1. Start StarAgent:
```bash
./scripts/start_staragent.sh
```

2. Ensure env vars are set (Claude Code can pass env into MCP subprocess depending on configuration):
```bash
export STARAGENT_BASE_URL="http://127.0.0.1:8095/v1"
export STARAGENT_API_KEY="local-dev-key"
export STARAGENT_DEFAULT_MODEL="gemma4:e2b"
```

## MCP server command

Use this command for the MCP server:
```bash
python3 -m mcp.server
```

If Claude Code requires an absolute path:
```bash
/usr/bin/python3 -m mcp.server
```

## Notes

- The MCP server does not embed a model. It calls the running StarAgent FastAPI runtime.
- Approval-gated writes remain approval-gated. Use `staragent_approve` (or the legacy `macagent_approve`) or send `yes` through the client workflow.
