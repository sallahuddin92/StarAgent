#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p "$ROOT/.runtime" "$ROOT/logs"

PIDFILE="$ROOT/.runtime/macagent_mcp.pid"
LOGFILE="$ROOT/logs/macagent_mcp.log"

if [[ -f "$PIDFILE" ]]; then
  pid="$(cat "$PIDFILE" || true)"
  if [[ -n "${pid}" ]] && ps -p "$pid" >/dev/null 2>&1; then
    echo "[mcp-start] macagent-mcp already running (pid=$pid)"
    exit 0
  fi
  rm -f "$PIDFILE"
fi

echo "[mcp-start] Starting macagent-mcp (stdio server)."
echo "[mcp-start] Log: $LOGFILE"

nohup python3 -m mcp.server >"$LOGFILE" 2>&1 &
pid=$!
echo "$pid" > "$PIDFILE"
echo "[mcp-start] Started (pid=$pid)"
echo "[mcp-start] Note: Most MCP clients will spawn this server themselves; this script is mainly for debugging."

