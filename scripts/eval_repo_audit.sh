#!/usr/bin/env bash
set -euo pipefail

# Phase 4 evaluation: repo_audit profile end-to-end
#
# Requirements:
# - StarAgent server running (default: http://127.0.0.1:8095)
# - Runs read-only against the repo path (no project file writes)
# - Verifies required artifacts exist under .runtime/tasks/<task_id>/

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

BASE_URL="${STARAGENT_HTTP_URL:-http://127.0.0.1:${PORT:-8095}}"
V1_URL="${STARAGENT_BASE_URL:-${BASE_URL}/v1}"
API_KEY="${STARAGENT_API_KEY:-${MACAGENT_API_KEY:-${PROXY_API_KEY:-local-dev-key}}}"
AUTH_HEADER="Authorization: Bearer ${API_KEY}"

echo "[eval_repo_audit] Base: ${BASE_URL}"
echo "[eval_repo_audit] V1:   ${V1_URL}"

curl -fsS "${BASE_URL}/health" >/dev/null
echo "[eval_repo_audit] /health OK"

PROJECT_ID="eval-phase4"
CONV_ID="eval-repo-audit-$(date +%s)"

# Default audit target: repo root
AUDIT_PATH="${1:-.}"
QUESTION="${2:-Inspect the repo, identify entry points, summarize architecture, and list risks.}"

REQ="$(python3 - <<PY
import json
print(json.dumps({
  "project_id": "${PROJECT_ID}",
  "conversation_id": "${CONV_ID}",
  "path": "${AUDIT_PATH}",
  "question": "${QUESTION}",
  "run_now": True,
  "max_steps": 25
}))
PY
)"

echo "[eval_repo_audit] Creating repo audit task..."
RESP="$(curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
  -d "${REQ}" \
  "${V1_URL}/repo_audit/run")"

TASK_ID="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("task_id",""))' <<<"${RESP}")"
if [[ -z "${TASK_ID}" ]]; then
  echo "[eval_repo_audit] Failed to extract task_id from response:"
  echo "${RESP}"
  exit 2
fi

echo "[eval_repo_audit] task_id=${TASK_ID}"
status="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("status",""))' <<<"${RESP}")"
echo "[eval_repo_audit] initial status=${status}"

for i in $(seq 1 20); do
  if [[ "${status}" == "completed" ]]; then
    break
  fi
  if [[ "${status}" == "paused" ]]; then
    echo "[eval_repo_audit] paused (approval required). Rejecting for safety."
    curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
      -d '{"action":"reject","reason":"eval script rejects approvals by default"}' \
      "${V1_URL}/tasks/${TASK_ID}/continue" >/dev/null
    break
  fi
  echo "[eval_repo_audit] continue #${i} ..."
  RESP="$(curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
    -d '{"action":"continue","max_step_advances":3,"max_duration_s":25}' \
    "${V1_URL}/tasks/${TASK_ID}/continue")"
  status="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("status",""))' <<<"${RESP}")"
  echo "[eval_repo_audit] status=${status}"
done

TASK_DIR=".runtime/tasks/${TASK_ID}"
echo "[eval_repo_audit] verifying artifacts in ${TASK_DIR}"

req_files=(
  "file_index.json"
  "architecture_map.md"
  "entry_points.md"
  "risk_notes.md"
  "open_questions.md"
  "audit_report.md"
)

missing=0
for f in "${req_files[@]}"; do
  if [[ ! -f "${TASK_DIR}/${f}" ]]; then
    echo "[eval_repo_audit] MISSING: ${TASK_DIR}/${f}"
    missing=1
  else
    echo "[eval_repo_audit] OK: ${TASK_DIR}/${f}"
  fi
done

if [[ "${missing}" -ne 0 ]]; then
  echo "[eval_repo_audit] FAIL: missing artifacts"
  exit 3
fi

echo "[eval_repo_audit] PASS"

