#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p "$ROOT/.runtime" "$ROOT/logs" "$ROOT/sandbox_test" "$ROOT/data"

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

VENV_DIR="${STARAGENT_VENV_DIR:-$ROOT/.venv}"
PYTHON_BIN="$VENV_DIR/bin/python"

echo "[bootstrap] Root: $ROOT"
echo "[bootstrap] Target bind: ${HOST}:${PORT}"
echo "[bootstrap] Ollama: $OLLAMA_BASE_URL"
echo "[bootstrap] Model: $DEFAULT_MODEL"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[bootstrap] ERROR: python3 not found." >&2
  exit 1
fi

python3 - <<'PY'
import sys
minv=(3,9)
if sys.version_info < minv:
  raise SystemExit(f"Python {minv[0]}.{minv[1]}+ required; found {sys.version.split()[0]}")
print("ok")
PY

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[bootstrap] Creating venv: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

echo "[bootstrap] Using python: $PYTHON_BIN"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[bootstrap] ERROR: venv python missing at $PYTHON_BIN" >&2
  exit 1
fi

echo "[bootstrap] Installing Python deps (requirements.txt)..."
"$PYTHON_BIN" -m pip -q install -U pip >/dev/null
"$PYTHON_BIN" -m pip -q install -r "$ROOT/requirements.txt"

echo "[bootstrap] Checking Ollama reachability..."
if ! /usr/bin/curl -fsS "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
  echo "[bootstrap] ERROR: Ollama not reachable at $OLLAMA_BASE_URL" >&2
  echo "[bootstrap] Fix: start Ollama, then re-run bootstrap." >&2
  echo "[bootstrap] Example: ollama serve" >&2
  exit 1
fi

tags_json="$(/usr/bin/curl -fsS "$OLLAMA_BASE_URL/api/tags" || true)"
if [[ -z "$tags_json" ]]; then
  echo "[bootstrap] ERROR: Ollama /api/tags returned empty response." >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys,json; model=sys.argv[1]; data=json.load(sys.stdin); names=[m.get("name") for m in data.get("models", [])]; sys.exit(0 if model in names else 2)' "$DEFAULT_MODEL" <<<"$tags_json"; then
  echo "[bootstrap] ERROR: Model '$DEFAULT_MODEL' not found in Ollama." >&2
  echo "[bootstrap] Run: ollama list" >&2
  exit 1
fi

if [[ ! -f "$ROOT/.env.local" && ! -f "$ROOT/.env" ]]; then
  echo "[bootstrap] No .env/.env.local found. Creating .env.local from .env.example"
  cp "$ROOT/.env.example" "$ROOT/.env.local"
  echo "[bootstrap] Created: $ROOT/.env.local"
fi

echo
echo "[bootstrap] OK"
echo "[bootstrap] Next steps:"
echo "  1) Start: ./scripts/start_staragent.sh"
echo "  2) Smoke test: ./scripts/smoke_test_staragent.sh"
echo "  3) OpenAI base URL: http://127.0.0.1:${PORT}/v1"
echo "  4) API key: ${PROXY_API_KEY}"

