# Changelog

All notable changes to StarAgent will be documented here.

The runtime version source of truth is `app/version.py` (`__version__`).

## Unreleased

- Docs and demo polish (in progress).

## 2.0.0 (2026-04-23)

- OpenAI-compatible API for Open WebUI and local clients.
- Fast-path routing for normal chat and direct Q&A.
- Agent-path planner/executor for grounded repo inspection and bounded tasks.
- SQLite-backed long-horizon memory (project + conversation scoped).
- Approval-gated writes (no silent filesystem writes) with resume (`yes`) and continuation (`continue`).
- Verification and rollback flows.
- Presets + preset packs for operator-friendly workflows.
- Phase 4 bounded task engine (research, repo audit, issue triage, writing) with artifacts under `.runtime/tasks/<task_id>/`.
- Task observability across API/CLI/MCP (list/inspect/summary/logs/artifacts).
- Lightweight local dashboard for visual operation (`/dashboard`).

