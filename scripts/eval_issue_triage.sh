#!/usr/bin/env bash
set -euo pipefail

# Phase 4 evaluation: issue_triage profile end-to-end
#
# This script is safe by default (read-only). It runs against the repo path
# and produces artifacts under .runtime/tasks/<task_id>/.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

BASE_URL="${STARAGENT_HTTP_URL:-http://127.0.0.1:${PORT:-8095}}"
V1_URL="${STARAGENT_BASE_URL:-${BASE_URL}/v1}"
API_KEY="${STARAGENT_API_KEY:-${MACAGENT_API_KEY:-${PROXY_API_KEY:-local-dev-key}}}"
AUTH_HEADER="Authorization: Bearer ${API_KEY}"

echo "[eval_issue_triage] Base: ${BASE_URL}"
echo "[eval_issue_triage] V1:   ${V1_URL}"

curl -fsS "${BASE_URL}/health" >/dev/null
echo "[eval_issue_triage] /health OK"

PROJECT_ID="eval-phase4"
CONV_ID="eval-issue-triage-$(date +%s)"

TARGET_PATH="${1:-.}"
ISSUE="${2:-Agent-path request seems to stall or return an internal fallback message. Triage likely causes.}"

REQ="$(python3 - <<PY
import json
print(json.dumps({
  "project_id": "${PROJECT_ID}",
  "conversation_id": "${CONV_ID}",
  "path": "${TARGET_PATH}",
  "issue": "${ISSUE}",
  "files": ["app/main.py", "app/agent.py", "app/executor.py", "app/planner.py"],
  "run_now": True,
  "max_steps": 25
}))
PY
)"

echo "[eval_issue_triage] Creating issue triage task..."
RESP="$(curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
  -d "${REQ}" \
  "${V1_URL}/issue_triage/run")"

TASK_ID="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("task_id",""))' <<<"${RESP}")"
if [[ -z "${TASK_ID}" ]]; then
  echo "[eval_issue_triage] Failed to extract task_id from response:"
  echo "${RESP}"
  exit 2
fi

echo "[eval_issue_triage] task_id=${TASK_ID}"
status="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("status",""))' <<<"${RESP}")"
echo "[eval_issue_triage] initial status=${status}"

for i in $(seq 1 30); do
  if [[ "${status}" == "completed" ]]; then
    break
  fi
  if [[ "${status}" == "paused" ]]; then
    echo "[eval_issue_triage] paused (approval required). Rejecting for safety."
    curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
      -d '{"action":"reject","reason":"eval script rejects approvals by default"}' \
      "${V1_URL}/tasks/${TASK_ID}/continue" >/dev/null
    break
  fi
  echo "[eval_issue_triage] continue #${i} ..."
  RESP="$(curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
    -d '{"action":"continue","max_step_advances":3,"max_duration_s":25}' \
    "${V1_URL}/tasks/${TASK_ID}/continue")"
  status="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("status",""))' <<<"${RESP}")"
  echo "[eval_issue_triage] status=${status}"
done

TASK_DIR=".runtime/tasks/${TASK_ID}"
echo "[eval_issue_triage] verifying artifacts in ${TASK_DIR}"

req_files=(
  "issue_summary.md"
  "evidence_table.json"
  "likely_causes.md"
  "reproduction_steps.md"
  "next_actions.md"
)

missing=0
for f in "${req_files[@]}"; do
  if [[ ! -f "${TASK_DIR}/${f}" ]]; then
    echo "[eval_issue_triage] MISSING: ${TASK_DIR}/${f}"
    missing=1
  else
    echo "[eval_issue_triage] OK: ${TASK_DIR}/${f}"
  fi
done

if [[ "${missing}" -ne 0 ]]; then
  echo "[eval_issue_triage] FAIL: missing artifacts"
  exit 3
fi

echo "[eval_issue_triage] PASS"

