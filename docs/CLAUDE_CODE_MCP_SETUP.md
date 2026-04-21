# Claude Code MCP Setup (Template)

Claude Code supports MCP servers. MacAgent MCP server runs over stdio and exposes MacAgent as tools.

This document provides a conservative template because Claude Code configuration formats can change between versions.

## Prereqs

1. Start MacAgent:
```bash
./scripts/start_macagent.sh
```

2. Ensure env vars are set (Claude Code can pass env into MCP subprocess depending on configuration):
```bash
export MACAGENT_BASE_URL="http://127.0.0.1:8095/v1"
export MACAGENT_API_KEY="local-dev-key"
export MACAGENT_DEFAULT_MODEL="gemma4:e2b"
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

- The MCP server does not embed a model. It calls the running MacAgent FastAPI runtime.
- Approval-gated writes remain approval-gated. Use `macagent_approve` or send `yes` through the client workflow.

