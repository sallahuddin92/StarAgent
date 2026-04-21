#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p "$ROOT/release_snapshots"

ts="$(date +%Y%m%d_%H%M%S)"
out="$ROOT/release_snapshots/macagent_snapshot_${ts}.tgz"

# By default, snapshot code and docs only. Runtime artifacts can be included explicitly.
include_runtime="${INCLUDE_RUNTIME:-0}"

echo "[snapshot] Root: $ROOT"
echo "[snapshot] Output: $out"
echo "[snapshot] Include runtime artifacts: $include_runtime"

exclude_args=(
  --exclude "./.git"
  --exclude "./.runtime"
  --exclude "./logs"
  --exclude "./data/memory.db"
  --exclude "./data/memory"
  --exclude "./.env"
  --exclude "./.env.local"
  --exclude "./__pycache__"
  --exclude "./.pytest_cache"
)

if [[ "$include_runtime" == "1" ]]; then
  # Keep logs and runtime state out of default archive but allow opt-in.
  exclude_args=(
    --exclude "./.git"
    --exclude "./data/memory.db"
    --exclude "./data/memory"
    --exclude "./.env"
    --exclude "./.env.local"
    --exclude "./__pycache__"
    --exclude "./.pytest_cache"
  )
fi

tar -czf "$out" "${exclude_args[@]}" .

echo "[snapshot] OK"

