#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p "$ROOT/.runtime" "$ROOT/logs" "$ROOT/sandbox_test"

PYTHON="${STARAGENT_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

load_env() {
  # Prefer local overrides; do not error if missing.
  if [[ -f "$ROOT/.env.local" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env.local"
    set +a
  fi
  if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
  fi
}

load_env

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8095}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
DEFAULT_MODEL="${DEFAULT_MODEL:-gemma4:e2b}"
PROXY_API_KEY="${PROXY_API_KEY:-local-dev-key}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

PIDFILE="$ROOT/.runtime/macagent_${PORT}.pid"
LOGFILE="$ROOT/logs/macagent_${PORT}.log"

echo "[start] Root: $ROOT"
echo "[start] Ollama: $OLLAMA_BASE_URL"
echo "[start] Model: $DEFAULT_MODEL"
echo "[start] Bind: ${HOST}:${PORT}"
echo "[start] Log: $LOGFILE"

"$PYTHON" - <<'PY' >/dev/null
import importlib.util
mods = ["fastapi", "uvicorn", "httpx", "dotenv", "tenacity"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
  raise SystemExit("Missing Python deps: " + ", ".join(missing))
PY

if ! "$PYTHON" -m uvicorn --version >/dev/null 2>&1; then
  echo "[start] ERROR: uvicorn is not available in python3 environment." >&2
  exit 1
fi

if [[ -f "$PIDFILE" ]]; then
  pid="$(cat "$PIDFILE" || true)"
  if [[ -n "${pid}" ]] && ps -p "$pid" >/dev/null 2>&1; then
    cmd="$(ps -p "$pid" -o command= | tr -d '\n' || true)"
    if [[ "$cmd" == *"uvicorn"* && "$cmd" == *"app.main:app"* && "$cmd" == *"--port $PORT"* ]]; then
      echo "[start] StarAgent already running (pid=$pid)."
      echo "[start] URLs:"
      echo "  Health: http://127.0.0.1:${PORT}/health"
      echo "  OpenAI base: http://127.0.0.1:${PORT}/v1"
      exit 0
    fi
  fi
  rm -f "$PIDFILE"
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  pid_from_port="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true)"
  if [[ -n "$pid_from_port" ]]; then
    cmd="$(ps -p "$pid_from_port" -o command= 2>/dev/null | tr -d '\n' || true)"
    if [[ "$cmd" == *"uvicorn"* && "$cmd" == *"app.main:app"* && "$cmd" == *"--port $PORT"* ]]; then
      echo "[start] StarAgent already running (pid=$pid_from_port)."
      echo "$pid_from_port" > "$PIDFILE"
      echo "[start] URLs:"
      echo "  Health: http://127.0.0.1:${PORT}/health"
      echo "  OpenAI base: http://127.0.0.1:${PORT}/v1"
      exit 0
    fi
  fi
  echo "[start] ERROR: Port $PORT is already in use." >&2
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN || true
  exit 1
fi

ollama_tags() {
  curl -fsS "$OLLAMA_BASE_URL/api/tags"
}

if ! ollama_tags >/dev/null 2>&1; then
  echo "[start] Ollama not reachable at $OLLAMA_BASE_URL."
  if command -v ollama >/dev/null 2>&1; then
    if ! pgrep -f "ollama serve" >/dev/null 2>&1; then
      echo "[start] Attempting to start Ollama via: ollama serve"
      nohup ollama serve > "$ROOT/logs/ollama.log" 2>&1 &
      echo $! > "$ROOT/.runtime/ollama.pid"
    else
      echo "[start] Ollama appears to already be running (ollama serve)."
    fi
    echo "[start] Waiting for Ollama to become reachable..."
    for _ in {1..60}; do
      if ollama_tags >/dev/null 2>&1; then
        break
      fi
      sleep 0.5
    done
  fi
fi

if ! ollama_tags >/dev/null 2>&1; then
  echo "[start] ERROR: Ollama still not reachable at $OLLAMA_BASE_URL." >&2
  echo "[start] Fix: start Ollama manually, then re-run this script." >&2
  exit 1
fi

tags_json="$(ollama_tags || true)"
if [[ -z "$tags_json" ]]; then
  echo "[start] ERROR: Ollama /api/tags returned empty response." >&2
  exit 1
fi

if ! python3 -c 'import sys,json; model=sys.argv[1]; data=json.load(sys.stdin); names=[m.get("name") for m in data.get("models", [])]; sys.exit(0 if model in names else 2)' "$DEFAULT_MODEL" <<<"$tags_json"; then
  echo "[start] ERROR: Model '$DEFAULT_MODEL' not found in Ollama." >&2
  echo "[start] Run: ollama list" >&2
  exit 1
fi

echo "[start] Starting StarAgent (uvicorn)..."
export OLLAMA_BASE_URL DEFAULT_MODEL PROXY_API_KEY LOG_LEVEL
nohup "$PYTHON" -m uvicorn app.main:app --host "$HOST" --port "$PORT" > "$LOGFILE" 2>&1 &
pid=$!
echo "$pid" > "$PIDFILE"

echo "[start] Waiting for /health..."
for _ in {1..120}; do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "[start] OK: StarAgent started (pid=$pid)."
    echo "[start] URLs:"
    echo "  Health: http://127.0.0.1:${PORT}/health"
    echo "  OpenAI base: http://127.0.0.1:${PORT}/v1"
    echo "  API key: ${PROXY_API_KEY}"
    exit 0
  fi
  sleep 0.5
done

echo "[start] ERROR: StarAgent did not become healthy within timeout." >&2
echo "[start] Last log lines:" >&2
tail -n 80 "$LOGFILE" >&2 || true
exit 1
