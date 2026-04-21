#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

load_env() {
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

PORT="${PORT:-8095}"
PIDFILE="$ROOT/.runtime/macagent_${PORT}.pid"

echo "[stop] Root: $ROOT"
echo "[stop] Target port: $PORT"

kill_pid() {
  local pid="$1"
  if ! ps -p "$pid" >/dev/null 2>&1; then
    return 0
  fi
  local cmd
  cmd="$(ps -p "$pid" -o command= || true)"
  if [[ "$cmd" != *"uvicorn"* || "$cmd" != *"app.main:app"* ]]; then
    echo "[stop] Refusing to stop pid=$pid (not a MacAgent uvicorn process)." >&2
    echo "[stop] cmd: $cmd" >&2
    return 2
  fi
  echo "[stop] Stopping MacAgent pid=$pid"
  kill "$pid" || true
  for _ in {1..40}; do
    if ! ps -p "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  echo "[stop] Escalating to SIGKILL for pid=$pid"
  kill -9 "$pid" || true
  return 0
}

if [[ -f "$PIDFILE" ]]; then
  pid="$(cat "$PIDFILE" || true)"
  if [[ -n "${pid}" ]]; then
    if kill_pid "$pid"; then
      rm -f "$PIDFILE"
      echo "[stop] OK: stopped."
      exit 0
    fi
  fi
  rm -f "$PIDFILE"
fi

pid_from_port="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true)"
if [[ -n "${pid_from_port}" ]]; then
  if kill_pid "$pid_from_port"; then
    echo "[stop] OK: stopped (pid=$pid_from_port)."
    exit 0
  fi
fi

echo "[stop] No running MacAgent process found for port $PORT."

