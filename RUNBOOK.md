# MacAgent Runbook (Recovered Runtime)

This repo contains a recovered, working MacAgent proxy runtime that exposes an OpenAI-compatible API for Open WebUI and local clients.

## Quick Start

1. Start MacAgent:
```bash
./scripts/start_macagent.sh
```

2. Smoke test:
```bash
./scripts/smoke_test_macagent.sh
```

3. Stop MacAgent:
```bash
./scripts/stop_macagent.sh
```

## URLs

- Health: `http://127.0.0.1:${PORT:-8095}/health`
- OpenAI-compatible base: `http://127.0.0.1:${PORT:-8095}/v1`

## Configuration

Configuration is loaded in this order:

1. `.env.local` (recommended for your machine; not committed)
2. `.env` (optional; not committed)
3. defaults in scripts and `app/main.py`

Use [.env.example](/Users/sallahuddin/Desktop/macagent_proxy_starter/.env.example) as the template.

Key variables:

- `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`)
- `DEFAULT_MODEL` (default `gemma4:e2b`)
- `PROXY_API_KEY` (default `local-dev-key`)
- `HOST` / `PORT` (used by `scripts/start_macagent.sh`)
- `MEMORY_DIR`, `DATABASE_PATH`
- `LOG_LEVEL`

## Operational Scripts

- Start: `./scripts/start_macagent.sh`
- Stop: `./scripts/stop_macagent.sh`
- Smoke test: `./scripts/smoke_test_macagent.sh`
- Validation (smoke + small regression guard): `./scripts/validate_macagent.sh`
- Release snapshot: `./scripts/release_snapshot.sh`
- MCP start (debug): `./scripts/start_macagent_mcp.sh`
- MCP stop (debug): `./scripts/stop_macagent_mcp.sh`

## CLI / MCP

See:
- [CLI_SETUP.md](/Users/sallahuddin/Desktop/macagent_proxy_starter/docs/CLI_SETUP.md)
- [MCP_SETUP.md](/Users/sallahuddin/Desktop/macagent_proxy_starter/docs/MCP_SETUP.md)

## Logs and Runtime State

- Server logs: `./logs/macagent_${PORT}.log`
- PID file: `./.runtime/macagent_${PORT}.pid`
- If `scripts/start_macagent.sh` starts Ollama: `./logs/ollama.log` and `./.runtime/ollama.pid`

## Daily Workflow Notes

### Approval-gated writes

When the agent proposes a risky tool call (ex: `write_file`), the response will include:

- `x_agent_status: "approval_required"`
- assistant content containing a JSON blob with `"status": "awaiting_approval"` and `"proposed_action": ...`

To approve and resume, send `yes` in the same `conversation_id` + `project_id`.

To deny, send `no`.

### Continuation / partial completion

Some tasks intentionally return `x_agent_status: "partial"` with remaining work in `x_agent_payload.plan_remaining`.

To continue, send `continue` in the same `conversation_id` + `project_id`.

## Known Caveat: Approval/Continuation Persistence

Approval and continuation state is persisted via the legacy JSON sidecar file under `MEMORY_DIR` and merged on load.

If the JSON sidecar files are deleted or moved, “yes/continue” resume may fail because pending agent state is missing.

If resume breaks:

1. Re-run the task from scratch in a new `conversation_id`.
2. Ensure `MEMORY_DIR` remains intact and writable.
3. Avoid manually deleting `data/memory/*.json` unless you intend to lose pending state.
