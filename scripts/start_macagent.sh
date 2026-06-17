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
DEFAULT_MODEL="${DEFAULT_MODEL:-gemma4:12b-mlx}"
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

# Check if model verification is skipped
SKIP_CHECK="${STARAGENT_SKIP_MODEL_CHECK:-0}"
for arg in "$@"; do
  if [[ "$arg" == "--no-model-check" ]]; then
    SKIP_CHECK="1"
  fi
done

if [[ "$SKIP_CHECK" == "1" ]]; then
  echo "[start] Skipping model availability checks (STARAGENT_SKIP_MODEL_CHECK=1)."
else
  echo "[start] Verifying model availability..."
  if ! "$PYTHON" -c '
import sys
import os
sys.path.insert(0, os.getcwd())
try:
    from app.model_registry import registry
except ImportError as e:
    print(f"[start] WARNING: could not load model registry: {e}")
    sys.exit(0)
default_model = sys.argv[1]
provider = registry.infer_provider(default_model)
if provider != "ollama":
    print(f"[start] OK: default model {default_model} is remote ({provider}).")
    sys.exit(0)

# Local ollama model check
try:
    if registry.is_local_ollama_model_installed(default_model, refresh=True):
        print(f"[start] OK: local model {default_model} is installed.")
        sys.exit(0)
except Exception as e:
    print(f"[start] WARNING: Ollama reachability check failed: {e}")

# Check if there is any usable fallback model configured (e.g., remote or other installed models)
has_remote = any(os.getenv(k) for k in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "LONGCAT_API_KEY", "GROQ_API_KEY"])
if has_remote:
    print(f"[start] WARNING: local model {default_model} not found in Ollama, but remote fallback keys are configured. Proceeding.")
    sys.exit(0)

print(f"[start] ERROR: default model {default_model} not found in Ollama, and no remote models configured.")
sys.exit(1)
' "$DEFAULT_MODEL"; then
    echo "[start] ERROR: Startup model validation failed. (Ollama not running or model missing and no remote keys found)" >&2
    exit 1
fi
fi

echo "[start] Starting StarAgent (uvicorn)..."
export OLLAMA_BASE_URL DEFAULT_MODEL PROXY_API_KEY LOG_LEVEL

RELOAD_ARG=""
if [[ "${STARAGENT_RELOAD:-0}" == "1" ]]; then
  RELOAD_ARG="--reload"
fi

if [[ "${STARAGENT_FOREGROUND:-0}" == "1" ]]; then
  echo "[start] Foreground mode enabled (STARAGENT_FOREGROUND=1)."
  echo "[start] URLs:"
  echo "  Health: http://127.0.0.1:${PORT}/health"
  echo "  OpenAI base: http://127.0.0.1:${PORT}/v1"
  echo "  Dashboard: http://127.0.0.1:${PORT}/dashboard"
  exec "$PYTHON" -m uvicorn app.main:app --host "$HOST" --port "$PORT" $RELOAD_ARG
fi

nohup "$PYTHON" -m uvicorn app.main:app --host "$HOST" --port "$PORT" $RELOAD_ARG > "$LOGFILE" 2>&1 &
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
