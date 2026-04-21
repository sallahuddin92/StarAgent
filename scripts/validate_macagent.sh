#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

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

echo "[validate] Running smoke test..."
"$ROOT/scripts/smoke_test_macagent.sh"

echo
echo "[validate] Basic agent-path regression guard: ensure internal fallback string is not returned."
resp="$(curl -fsS "http://127.0.0.1:${PORT:-8095}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${PROXY_API_KEY:-local-dev-key}" \
  -d "{\"model\":\"${DEFAULT_MODEL:-gemma4:e2b}\",\"stream\":false,\"project_id\":\"validate\",\"conversation_id\":\"validate-1\",\"messages\":[{\"role\":\"user\",\"content\":\"Inspect the app folder and identify the main API entry file.\"}]}")"
python3 - <<'PY' <<<"$resp"
import sys, json
d=json.load(sys.stdin)
content=d["choices"][0]["message"]["content"].strip()
assert content != "Task finished or iteration limit reached.", "Internal fallback leaked"
print("[validate] OK")
PY
