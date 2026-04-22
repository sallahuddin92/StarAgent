# StarAgent CLI Setup (legacy compatible: macagent)

StarAgent CLI is a thin client that calls the existing FastAPI runtime (the source of truth).

## Prereqs

1. Start StarAgent:
```bash
./scripts/start_staragent.sh
```

2. Confirm health:
```bash
curl -sS http://127.0.0.1:8095/health
```

## Run Without Installing

```bash
python3 -m cli.staragent status
python3 -m cli.staragent ask "Reply with exactly OK"
python3 -m cli.staragent agent "Inspect the app folder and identify the main API entry file."
```

## Install (Optional, enables `staragent` command)

This repo includes a minimal `pyproject.toml` with console scripts:

```bash
python3 -m pip install -e .
staragent status
staragent ask "Reply with exactly OK"
```

If `staragent` is not found after install, add the user scripts directory to `PATH`:
```bash
export PATH=\"$HOME/Library/Python/3.9/bin:$PATH\"
```

Alternatively, you can always use the repo-local wrapper:
```bash
./scripts/staragent status
./scripts/staragent ask \"Reply with exactly OK\"
```

Note: global CLI flags like `--json` must appear before the subcommand:

```bash
staragent --json ask "Reply with exactly OK"
```

For `staragent task ...` subcommands, task-specific `--json` flags are supported after the subcommand (for example `staragent task list --json`).

## Environment Variables

- `STARAGENT_BASE_URL` (default `http://127.0.0.1:8095/v1`)
- `STARAGENT_API_KEY` (default `local-dev-key`)
- `STARAGENT_DEFAULT_MODEL` (default `gemma4:e2b`)
- `STARAGENT_DEFAULT_PROJECT` (default `default`)
- `STARAGENT_DEFAULT_CONVERSATION_PREFIX` (default `cli`)

Legacy variables are still supported for now:
- `MACAGENT_BASE_URL`, `MACAGENT_API_KEY`, `MACAGENT_DEFAULT_MODEL`, `MACAGENT_DEFAULT_PROJECT`, `MACAGENT_DEFAULT_CONVERSATION_PREFIX`

## Context Persistence

The CLI stores the last used `project_id` and `conversation_id` so that:

- `staragent approve` can resume the most recent pending approval
- `staragent continue` can resume partial completions

State file:
- `~/.staragent/context.json` (override with `STARAGENT_CLI_STATE_DIR`)
- Legacy compatibility: if `~/.staragent/context.json` does not exist but `~/.macagent/context.json` does, the CLI will use the legacy file.

## Phase 4: Tasks + Research Mode

Iterative task engine:

```bash
staragent task create "Inspect the app folder and identify the main API entry file."
staragent task status
staragent task continue
```

Task observability (operator-friendly):

```bash
staragent task list --limit 10
staragent task inspect <task_id>
staragent task summary <task_id>
staragent task logs <task_id> --tail 20
staragent task artifacts <task_id>
staragent task artifact <task_id> final_report.md
staragent task artifact <task_id> file_index.json --format json
```

Document research mode:

```bash
staragent research run --path docs --question "What is this repo and how do I use it?"
staragent research status
```

Notes:

- The CLI remembers the most recent `task_id` as `last_task_id` in the same context file, so `staragent task status` and `staragent task continue` work without retyping the id.
