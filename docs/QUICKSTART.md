# StarAgent Quickstart

StarAgent is a local control-plane proxy that exposes an OpenAI-compatible API and routes to:

- fast path chat (default)
- agent path tooling (repo inspection, approval-gated writes, continuation)

It uses Ollama as the inference plane.

## 1) Bootstrap (recommended)

From the repo root:
```bash
./scripts/bootstrap_staragent.sh
```

This will:
- create a local `.venv` if missing
- install `requirements.txt`
- verify Ollama is reachable
- verify `DEFAULT_MODEL` exists in Ollama
- create `.env.local` from `.env.example` if neither `.env` nor `.env.local` exist

## 2) Start / Stop

Start:
```bash
./scripts/start_staragent.sh
```

Stop:
```bash
./scripts/stop_staragent.sh
```

Health:
```bash
curl -sS http://127.0.0.1:8095/health | python3 -m json.tool
```

## 3) CLI

Status:
```bash
./scripts/staragent status
```

Ask:
```bash
./scripts/staragent ask "Reply with exactly STARAGENT_OK"
```

Agent:
```bash
./scripts/staragent agent "Inspect the app folder and identify the main API entry file."
```

Approve / Continue:
```bash
./scripts/staragent approve
./scripts/staragent continue
```

## 4) Open WebUI Hookup

See: `docs/OPEN_WEBUI_SETUP.md`

## 5) MCP (Claude Code / Codex)

See:
- `docs/CLAUDE_CODE_MCP_SETUP.md`
- `docs/CODEX_MCP_SETUP.md`

## 6) Smoke / Validation / Evaluation

Smoke test:
```bash
./scripts/smoke_test_staragent.sh
```

Validation:
```bash
./scripts/validate_staragent.sh
```

Evaluation pack (API + Open WebUI-style + CLI + Claude MCP):
```bash
./scripts/eval_staragent.sh
```

## 7) Release Bundles

Version source of truth: `app/version.py` (`__version__`)

Create a distributable bundle:
```bash
./scripts/release_staragent.sh
```

## Troubleshooting

- If you see `Ollama not reachable`: ensure `ollama serve` is running and `OLLAMA_BASE_URL` matches.
- If model is missing: `ollama pull gemma4:e2b` (or set `DEFAULT_MODEL`).
- If approval/continue resume breaks unexpectedly, check `DATABASE_PATH` points to the same SQLite file across restarts.
