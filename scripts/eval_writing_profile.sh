#!/usr/bin/env bash
set -euo pipefail

# Phase 4 evaluation: writing profile end-to-end
#
# Requirements:
# - StarAgent server running (default: http://127.0.0.1:8095)
# - Uses only sandbox_test/ for input documents
# - Verifies artifact outputs exist under .runtime/tasks/<task_id>/

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

BASE_URL="${STARAGENT_HTTP_URL:-http://127.0.0.1:${PORT:-8095}}"
V1_URL="${STARAGENT_BASE_URL:-${BASE_URL}/v1}"
API_KEY="${STARAGENT_API_KEY:-${MACAGENT_API_KEY:-${PROXY_API_KEY:-local-dev-key}}}"
AUTH_HEADER="Authorization: Bearer ${API_KEY}"

echo "[eval_writing] Base: ${BASE_URL}"
echo "[eval_writing] V1:   ${V1_URL}"

curl -fsS "${BASE_URL}/health" >/dev/null
echo "[eval_writing] /health OK"

NOTES_DIR="sandbox_test/writing_eval_notes"
mkdir -p "${NOTES_DIR}"
cat >"${NOTES_DIR}/notes1.md" <<'EOF'
# Notes 1

StarAgent can run iterative tasks with bounded step budgets.
EOF
cat >"${NOTES_DIR}/notes2.md" <<'EOF'
# Notes 2

Writing profile should produce outline, draft, final output.
EOF

PROJECT_ID="eval-phase4"
CONV_ID="eval-writing-$(date +%s)"

GOAL="${1:-Write a short technical README section explaining the writing profile outputs.}"

REQ="$(python3 - <<PY
import json
print(json.dumps({
  "project_id": "${PROJECT_ID}",
  "conversation_id": "${CONV_ID}",
  "path": "${NOTES_DIR}",
  "goal": "${GOAL}",
  "run_now": True,
  "max_steps": 25
}))
PY
)"

echo "[eval_writing] Creating writing task..."
RESP="$(curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
  -d "${REQ}" \
  "${V1_URL}/write/run")"

TASK_ID="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("task_id",""))' <<<"${RESP}")"
if [[ -z "${TASK_ID}" ]]; then
  echo "[eval_writing] Failed to extract task_id from response:"
  echo "${RESP}"
  exit 2
fi

echo "[eval_writing] task_id=${TASK_ID}"
status="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("status",""))' <<<"${RESP}")"
echo "[eval_writing] initial status=${status}"

for i in $(seq 1 30); do
  if [[ "${status}" == "completed" ]]; then
    break
  fi
  if [[ "${status}" == "paused" ]]; then
    echo "[eval_writing] paused (approval required). Rejecting for safety."
    curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
      -d '{"action":"reject","reason":"eval script rejects approvals by default"}' \
      "${V1_URL}/tasks/${TASK_ID}/continue" >/dev/null
    break
  fi
  echo "[eval_writing] continue #${i} ..."
  RESP="$(curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
    -d '{"action":"continue","max_step_advances":3,"max_duration_s":25}' \
    "${V1_URL}/tasks/${TASK_ID}/continue")"
  status="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("status",""))' <<<"${RESP}")"
  echo "[eval_writing] status=${status}"
done

TASK_DIR=".runtime/tasks/${TASK_ID}"
echo "[eval_writing] verifying artifacts in ${TASK_DIR}"

req_files=(
  "source_index.json"
  "outline.md"
  "draft.md"
  "final_output.md"
)

missing=0
for f in "${req_files[@]}"; do
  if [[ ! -f "${TASK_DIR}/${f}" ]]; then
    echo "[eval_writing] MISSING: ${TASK_DIR}/${f}"
    missing=1
  else
    echo "[eval_writing] OK: ${TASK_DIR}/${f}"
  fi
done

if [[ "${missing}" -ne 0 ]]; then
  echo "[eval_writing] FAIL: missing artifacts"
  exit 3
fi

echo "[eval_writing] PASS"

