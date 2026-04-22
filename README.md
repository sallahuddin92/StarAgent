# StarAgent

Local, long-horizon AI engineering runtime for Ollama: OpenAI-compatible API, CLI, Open WebUI integration, and an MCP server for Claude Code and Codex.

StarAgent is designed to be a practical control plane over a local model such as `gemma4:e2b` (Ollama stays the inference plane).

## What StarAgent Is

StarAgent sits between your clients and Ollama and provides:

- fast-path chat for normal Q&A
- agent-path execution for repo inspection and multi-step tasks
- long-horizon memory stored in SQLite (project and conversation scoped)
- approval-gated writes (no silent filesystem writes)
- continuation and resume (`yes` / `continue`)
- verification and rollback flows
- a lightweight local dashboard for task browsing, artifacts, logs, and approvals

## Surfaces

| Surface | What you use | Typical use |
|---|---|---|
| OpenAI-compatible API | `http://127.0.0.1:8095/v1` | Open WebUI, local apps |
| CLI | `staragent …` | terminal workflows |
| Open WebUI | point WebUI to StarAgent | chat UI + approvals |
| MCP server | `staragent-mcp` | Claude Code / Codex tool calls |
| Dashboard | `http://127.0.0.1:8095/dashboard` | visual ops: tasks, artifacts, approvals |

## Architecture

```mermaid
flowchart LR
  U["Developer"] -->|CLI| CLI["staragent (CLI)"]
  U -->|Open WebUI| WEBUI["Open WebUI"]
  U -->|Claude Code / Codex| MCPCLIENT["MCP-capable client"]
  U -->|Dashboard| DASH["Dashboard (/dashboard)"]

  CLI --> API["StarAgent FastAPI (/v1)"]
  WEBUI --> API
  MCPCLIENT --> MCP["staragent-mcp (stdio)"]
  MCP --> API
  DASH --> API

  API --> MEM["SQLite Memory + Pending State"]
  API --> EXEC["Planner / Executor\n(agent path)"]
  EXEC --> TOOLS["Tools (repo read, sandbox write w/ approval,\nverification, rollback)"]
  API --> OLLAMA["Ollama (gemma4:e2b)"]
```

## 5-Minute Demo

Prereqs: Ollama running with `gemma4:e2b` installed.

```bash
./scripts/bootstrap_staragent.sh
./scripts/start_staragent.sh
./scripts/smoke_test_staragent.sh
open http://127.0.0.1:8095/dashboard
```

Flagship flows (CLI):

```bash
# 1) Repo onboarding (read-only)
./scripts/staragent --project demo --conversation onboard-1 preset pack-run repo_onboarding --path .

# 2) Docs digest (read-only)
./scripts/staragent --project demo --conversation docs-1 preset pack-run docs_digest --path docs --question "Summarize how to operate StarAgent."

# 3) Release prep (stateful; approval-gated export to sandbox_test/)
./scripts/staragent --project demo --conversation rel-1 preset pack-run release_prep --path . --output sandbox_test/release_review_demo.md
# Approve from CLI (task id is printed by the command)
./scripts/staragent --project demo --conversation rel-1 task approve <task_id>
```

## Workflow: Approval + Resume

```mermaid
sequenceDiagram
  participant Client as "Client (WebUI/CLI/MCP)"
  participant SA as "StarAgent"
  participant FS as "Filesystem (sandbox_test/)"

  Client->>SA: "Create a file …"
  SA-->>Client: "approval_required (proposed diff/action)"
  Client->>SA: "yes"
  SA->>FS: "write"
  SA-->>Client: "completed (grounded result)"
```

## Quickstart (Local)

Prereqs:

- Python 3.9+
- Ollama running on `http://127.0.0.1:11434`
- Model installed (default: `gemma4:e2b`)

Bootstrap and run:

```bash
./scripts/bootstrap_staragent.sh
./scripts/start_staragent.sh
./scripts/smoke_test_staragent.sh
./scripts/stop_staragent.sh
```

Manual sanity checks:

```bash
curl -s http://127.0.0.1:8095/health | python3 -m json.tool
curl -s http://127.0.0.1:8095/v1/models | python3 -m json.tool
```

## Docs

- Quickstart: [docs/QUICKSTART.md](docs/QUICKSTART.md)
- API reference: [docs/API_REFERENCE.md](docs/API_REFERENCE.md)
- Open WebUI: [docs/OPEN_WEBUI_SETUP.md](docs/OPEN_WEBUI_SETUP.md)
- CLI: [docs/CLI_SETUP.md](docs/CLI_SETUP.md)
- MCP: [docs/MCP_SETUP.md](docs/MCP_SETUP.md)
- Dashboard: [docs/DASHBOARD.md](docs/DASHBOARD.md)
- Demo flows: [docs/DEMO.md](docs/DEMO.md)
- External trial: [docs/TRIAL_GUIDE.md](docs/TRIAL_GUIDE.md)
- Presets and packs: [docs/PRESETS.md](docs/PRESETS.md)
- Compatibility: [docs/COMPATIBILITY_MAP.md](docs/COMPATIBILITY_MAP.md)
- Visuals plan (screenshots): [docs/assets/README_VISUALS.md](docs/assets/README_VISUALS.md)

## Screenshots (Optional)

Dashboard screenshots (generated from the local dashboard UI):

![Dashboard Task List](docs/assets/dashboard_tasks.png)

![Dashboard Approval Required](docs/assets/dashboard_approval.png)

![Dashboard Task Detail + Artifact Preview](docs/assets/dashboard_detail.png)

## CLI Examples

```bash
staragent status
staragent ask "Reply with exactly: OK"
staragent agent "Inspect the app folder and identify the main API entry file."
```

Approval-gated write (sandbox only):

```bash
staragent agent "Create sandbox_test/hello.txt with content: hello"
staragent approve
```

## Open WebUI Setup

See [docs/OPEN_WEBUI_SETUP.md](docs/OPEN_WEBUI_SETUP.md).

StarAgent exposes an OpenAI-compatible API. WebUI typically needs:

- Base URL: `http://host.docker.internal:8095/v1` (if WebUI is in Docker)
- API key: your `PROXY_API_KEY` (local default is acceptable for local-only use)
- Model: `gemma4:e2b`

## MCP (Claude Code / Codex) Setup

See:

- [docs/MCP_SETUP.md](docs/MCP_SETUP.md)
- [docs/CLAUDE_CODE_MCP_SETUP.md](docs/CLAUDE_CODE_MCP_SETUP.md)
- [docs/CODEX_MCP_SETUP.md](docs/CODEX_MCP_SETUP.md)

Start MCP server (stdio):

```bash
./scripts/start_staragent_mcp.sh
```

## Repo Layout

- `app/`: FastAPI runtime, memory, planner/executor, tools, approval, verification/rollback
- `client/`: shared HTTP client used by CLI and MCP
- `cli/`: `staragent` and legacy `macagent` CLI entrypoints
- `mcp/`: MCP server and stdio transport
- `scripts/`: bootstrap, start/stop, smoke/validate, release helpers
- `docs/`: setup docs and evaluation notes
- `templates/`: prompt templates

## Compatibility Notes

StarAgent preserves legacy `macagent` CLI/MCP names and `MACAGENT_*` environment variables for now. See [docs/COMPATIBILITY_MAP.md](docs/COMPATIBILITY_MAP.md).

## Known Limitations (Honest)

- Local model quality varies; small models may need careful prompting.
- StarAgent is local-first. Do not expose the API to untrusted networks without hardening auth and transport.
- Persistence has been hardened into SQLite, but operational edge cases should still be validated in your environment.

## Status

Technical release ready: developer-focused, local-first, and meant for iterative hardening with real usage.
