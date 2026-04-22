# StarAgent Runbook (Recovered Runtime, legacy compatible: MacAgent)

This repo contains a recovered, working StarAgent proxy runtime that exposes an OpenAI-compatible API for Open WebUI and local clients.

## Quick Start

1. Start StarAgent:
```bash
./scripts/start_staragent.sh
```

If this is a fresh checkout, run bootstrap first:
```bash
./scripts/bootstrap_staragent.sh
```

2. Smoke test:
```bash
./scripts/smoke_test_staragent.sh
```

3. Stop StarAgent:
```bash
./scripts/stop_staragent.sh
```

## URLs

- Health: `http://127.0.0.1:${PORT:-8095}/health`
- OpenAI-compatible base: `http://127.0.0.1:${PORT:-8095}/v1`

## Configuration

Configuration is loaded in this order:

1. `.env.local` (recommended for your machine; not committed)
2. `.env` (optional; not committed)
3. defaults in scripts and `app/main.py`

Use [.env.example](./.env.example) as the template.

Key variables:

- `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`)
- `DEFAULT_MODEL` (default `gemma4:e2b`)
- `PROXY_API_KEY` (default `local-dev-key`)
- `HOST` / `PORT` (used by `scripts/start_staragent.sh`)
- `MEMORY_DIR`, `DATABASE_PATH`
- `LOG_LEVEL`

## Operational Scripts

- Start: `./scripts/start_staragent.sh` (legacy: `./scripts/start_macagent.sh`)
- Stop: `./scripts/stop_staragent.sh` (legacy: `./scripts/stop_macagent.sh`)
- Smoke test: `./scripts/smoke_test_staragent.sh` (legacy: `./scripts/smoke_test_macagent.sh`)
- Validation (smoke + small regression guard): `./scripts/validate_staragent.sh` (legacy: `./scripts/validate_macagent.sh`)
- Evaluation pack (API + Open WebUI-style + CLI + Claude MCP): `./scripts/eval_staragent.sh` (see `docs/EVALUATION.md`)
- Release snapshot: `./scripts/release_snapshot.sh`
- MCP start (debug): `./scripts/start_macagent_mcp.sh` (prints StarAgent name; pid/log file names remain legacy)
- MCP stop (debug): `./scripts/stop_macagent_mcp.sh`

## CLI / MCP

See:
- [CLI_SETUP.md](./docs/CLI_SETUP.md)
- [MCP_SETUP.md](./docs/MCP_SETUP.md)

## Logs and Runtime State

- Server logs: `./logs/macagent_${PORT}.log` (legacy filename)
- PID file: `./.runtime/macagent_${PORT}.pid` (legacy filename)
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

Approval and continuation state is now persisted durably in SQLite (canonical), with legacy JSON sidecar used only for backward-compatible fallback/import.

Precise storage locations:
- Core memory is stored in SQLite (`DATABASE_PATH`, default `./data/memory.db`).
- Pending agent state is stored in SQLite in the `conversations` table columns:
  - `pending_approval`, `pending_plan`, `pending_history`, `pending_goal`
  - These are saved/loaded via `app/database.py:DatabaseManager.save_memory_state/get_memory_state`
  - `pending_*` structured fields are JSON-serialized in SQLite.
- Legacy fallback/import (for older runs) may read pending state from the per-conversation JSON file:
  - Path: `${MEMORY_DIR}/${slugify(project_id)}-${slugify(conversation_id)}.json` (see `app/memory.py:MemoryStore._path`)
  - Fields: `pending_approval`, `pending_plan`, `pending_history`, `pending_goal`
  - On load, these fields are imported into SQLite only if the SQLite pending fields are missing (see `app/memory.py:MemoryStore._load_from_db`).

After this change, deleting/moving JSON sidecars should no longer break “yes/continue” as long as SQLite remains intact.

If resume breaks:

1. Re-run the task from scratch in a new `conversation_id`.
2. Ensure `MEMORY_DIR` remains intact and writable.
3. Avoid manually deleting `data/memory/*.json` unless you intend to lose pending state.
