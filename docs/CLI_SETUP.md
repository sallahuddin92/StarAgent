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

Note: CLI flags like `--json` must appear before the subcommand:

```bash
staragent --json ask "Reply with exactly OK"
```

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
