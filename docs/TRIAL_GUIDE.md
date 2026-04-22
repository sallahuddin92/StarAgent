# StarAgent External Technical Trial Guide (10 to 15 minutes)

This guide is designed for a small technical trial with engineers who are comfortable running local developer tools.

## Who StarAgent Is For

- Engineers who want a local control plane over Ollama for:
  - long-horizon memory
  - bounded agent workflows (tasks with artifacts)
  - approval-gated writes and safe resume
  - multi-surface usage (API, CLI, Open WebUI, MCP)

## Prerequisites

- macOS or Linux
- Python 3.9+
- Ollama running locally at `http://127.0.0.1:11434`
- Model installed (default: `gemma4:e2b`)

## Trial Flow (Copy-Paste)

From the repo root:

### 1) Bootstrap (2 minutes)

```bash
./scripts/bootstrap_staragent.sh
```

Expected:
- dependencies installed into `.venv`
- Ollama reachable
- model present (or a clear error)

### 2) Start StarAgent (1 minute)

```bash
./scripts/start_staragent.sh
```

If the process does not stay running in your environment, try foreground mode:

```bash
STARAGENT_FOREGROUND=1 ./scripts/start_staragent.sh
```

Sanity checks:

```bash
curl -sS http://127.0.0.1:8095/health | python3 -m json.tool
curl -sS http://127.0.0.1:8095/v1/models | python3 -m json.tool
open http://127.0.0.1:8095/dashboard
```

### 3) Run a read-only pack (3 to 5 minutes)

Repo onboarding (read-only):

```bash
./scripts/staragent --project trial --conversation onboard-1 preset pack-run repo_onboarding --path . --question "What are the entry points and request flow?"
```

Expected:
- one or more tasks created
- artifacts written under `.runtime/tasks/<task_id>/`
- primary artifact visible in task summary (usually `final_output.md`)

### 4) Run a stateful pack (approval required) (3 to 5 minutes)

Release prep (stateful export to `sandbox_test/`, approval-gated):

```bash
./scripts/staragent --project trial --conversation rel-1 preset pack-run release_prep --path . --output sandbox_test/release_review_trial.md
```

Expected:
- task pauses for approval
- dashboard shows “Approval required” and the grounded target path

Approve in CLI:

```bash
./scripts/staragent --project trial --conversation rel-1 task list --status paused
./scripts/staragent --project trial --conversation rel-1 task approve <task_id>
ls -la sandbox_test/release_review_trial.md
```

### 5) Inspect results in the dashboard (2 minutes)

In the dashboard:
- Filter `project` to `trial`
- Select the completed task
- Click “Open primary” or click the primary artifact in the artifact list
- Preview should show grounded content (paths/snippets from the repo)

## What Feedback To Give

Please share:

- Where you got stuck (exact command and output)
- Whether the artifacts were useful and grounded
- Whether approvals felt safe and understandable
- Whether the dashboard made it easier to operate tasks
- Any “this is confusing” wording or missing step in docs

## Common Failure Points To Note

- Ollama not running or wrong `OLLAMA_BASE_URL`
- model missing in Ollama (`ollama pull gemma4:e2b`)
- port 8095 already in use
- API key mismatch (dashboard uses the same key as CLI)
- slower model: tasks may take longer; try smaller packs first
