# MacAgent CLI Setup

MacAgent CLI is a thin client that calls the existing FastAPI runtime (the source of truth).

## Prereqs

1. Start MacAgent:
```bash
./scripts/start_macagent.sh
```

2. Confirm health:
```bash
curl -sS http://127.0.0.1:8095/health
```

## Run Without Installing

```bash
python3 -m cli.macagent status
python3 -m cli.macagent ask "Reply with exactly OK"
python3 -m cli.macagent agent "Inspect the app folder and identify the main API entry file."
```

## Install (Optional, enables `macagent` command)

This repo includes a minimal `pyproject.toml` with console scripts:

```bash
python3 -m pip install -e .
macagent status
macagent ask "Reply with exactly OK"
```

Note: CLI flags like `--json` must appear before the subcommand:

```bash
macagent --json ask "Reply with exactly OK"
```

## Environment Variables

- `MACAGENT_BASE_URL` (default `http://127.0.0.1:8095/v1`)
- `MACAGENT_API_KEY` (default `local-dev-key`)
- `MACAGENT_DEFAULT_MODEL` (default `gemma4:e2b`)
- `MACAGENT_DEFAULT_PROJECT` (default `default`)
- `MACAGENT_DEFAULT_CONVERSATION_PREFIX` (default `cli`)

## Context Persistence

The CLI stores the last used `project_id` and `conversation_id` so that:

- `macagent approve` can resume the most recent pending approval
- `macagent continue` can resume partial completions

State file:
- `~/.macagent/context.json` (override with `MACAGENT_CLI_STATE_DIR`)
