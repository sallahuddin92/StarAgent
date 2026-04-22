#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PIDFILE="$ROOT/.runtime/macagent_mcp.pid"

if [[ ! -f "$PIDFILE" ]]; then
  echo "[mcp-stop] No pidfile found."
  exit 0
fi

pid="$(cat "$PIDFILE" || true)"
rm -f "$PIDFILE"

if [[ -z "${pid}" ]]; then
  echo "[mcp-stop] Empty pidfile."
  exit 0
fi

if ! ps -p "$pid" >/dev/null 2>&1; then
  echo "[mcp-stop] Not running (pid=$pid)."
  exit 0
fi

cmd="$(ps -p "$pid" -o command= || true)"
if [[ "$cmd" != *"python"* || "$cmd" != *"-m mcp.server"* ]]; then
  echo "[mcp-stop] Refusing to kill pid=$pid (not staragent-mcp)."
  echo "[mcp-stop] cmd: $cmd"
  exit 2
fi

echo "[mcp-stop] Stopping staragent-mcp pid=$pid"
kill "$pid" || true
echo "[mcp-stop] OK"
