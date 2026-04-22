#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p "$ROOT/release_snapshots" "$ROOT/dist"

version="$(python3 -c 'from app.version import __version__; print(__version__)')"
ts="$(date +%Y%m%d_%H%M%S)"

bundle_dir="$ROOT/dist/staragent_${version}_${ts}"
bundle_tgz="$ROOT/release_snapshots/staragent_release_${version}_${ts}.tgz"

echo "[release] Root: $ROOT"
echo "[release] Version: $version"
echo "[release] Bundle dir: $bundle_dir"
echo "[release] Output: $bundle_tgz"

rm -rf "$bundle_dir"
mkdir -p "$bundle_dir"

if command -v rsync >/dev/null 2>&1; then
  # Copy the minimal distributable set (no local runtime artifacts).
  rsync -a \
    --exclude ".git" \
    --exclude ".runtime" \
    --exclude "logs" \
    --exclude "data/memory.db" \
    --exclude "data/memory" \
    --exclude ".env" \
    --exclude ".env.local" \
    --exclude ".venv" \
    --exclude "dist" \
    --exclude "__pycache__" \
    --exclude ".pytest_cache" \
    ./ "$bundle_dir/"
else
  echo "[release] WARN: rsync not found; using tar copy (slower)."
  tar -czf /tmp/staragent_release_copy.tgz \
    --exclude "./.git" \
    --exclude "./.runtime" \
    --exclude "./logs" \
    --exclude "./data/memory.db" \
    --exclude "./data/memory" \
    --exclude "./.env" \
    --exclude "./.env.local" \
    --exclude "./.venv" \
    --exclude "./dist" \
    --exclude "./__pycache__" \
    --exclude "./.pytest_cache" \
    .
  tar -xzf /tmp/staragent_release_copy.tgz -C "$bundle_dir"
  rm -f /tmp/staragent_release_copy.tgz
fi

tar -czf "$bundle_tgz" -C "$ROOT/dist" "$(basename "$bundle_dir")"

echo "[release] OK"
echo "[release] Next: share $bundle_tgz"
