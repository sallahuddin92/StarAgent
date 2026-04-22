# StarAgent Trial Checklist (Host / Maintainer)

This is a lightweight checklist to run a small external technical trial reliably and collect useful feedback.

## Before The Trial

- Verify the repo is a clean checkout (no local-only secrets committed).
- Confirm `docs/TRIAL_GUIDE.md` is current.
- Run locally once:
  - `./scripts/bootstrap_staragent.sh`
  - `./scripts/start_staragent.sh`
  - `./scripts/smoke_test_staragent.sh`
  - `./scripts/stop_staragent.sh`
- Confirm baseline model expectation:
  - `gemma4:e2b` is installed in Ollama (or `DEFAULT_MODEL` clearly documented).
- Confirm sandbox write safety:
  - stateful exports default into `sandbox_test/`
- Decide what you want feedback on:
  - onboarding clarity
  - artifact usefulness/grounding
  - approval trust
  - dashboard usability

## During The Trial

- Ask the participant to follow `docs/TRIAL_GUIDE.md` exactly.
- Encourage copying raw outputs for any failure.
- If they get stuck, collect:
  - `./scripts/staragent status --json`
  - `curl -sS http://127.0.0.1:8095/health | python3 -m json.tool`
  - `ollama list` output (model availability)

## After The Trial

Collect:

- Their `logs/` files:
  - `logs/macagent_8095.log` (or the current `PORT` log)
- Any relevant task artifacts they want to share:
  - `.runtime/tasks/<task_id>/` folder(s)
- The “stateful export” output file if it was created:
  - `sandbox_test/release_review_trial.md`

If they report “approval/continue doesn’t resume”:

- Confirm they used the same `project_id` + `conversation_id`.
- Confirm `DATABASE_PATH` points to the same SQLite file across restarts.
- Ask for:
  - `ls -la data/`
  - `ls -la .runtime/`

## Minimal Bug Report Template

Ask for:

- OS + Python version
- exact commands run
- exact failing command output
- relevant log excerpt (from `logs/`)
- task id (if task engine)
- whether they were using CLI, dashboard, WebUI, MCP (or multiple)

