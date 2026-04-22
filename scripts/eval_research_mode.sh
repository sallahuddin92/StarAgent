#!/usr/bin/env bash
set -euo pipefail

# Phase 4 evaluation: Document Research Mode end-to-end
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

echo "[eval_research_mode] Base: ${BASE_URL}"
echo "[eval_research_mode] V1:   ${V1_URL}"

curl -fsS "${BASE_URL}/health" >/dev/null
echo "[eval_research_mode] /health OK"

DOCS_DIR="sandbox_test/research_eval_docs"
mkdir -p "${DOCS_DIR}"
cat >"${DOCS_DIR}/doc1.md" <<'EOF'
# Doc 1

StarAgent Phase 4 research test document.

Key idea: chunking + summaries + synthesis.
EOF
cat >"${DOCS_DIR}/doc2.md" <<'EOF'
# Doc 2

Another document for cross-file synthesis.

Open question: what should be prioritized?
EOF

PROJECT_ID="eval-phase4"
CONV_ID="eval-research-$(date +%s)"

REQ="$(python3 - <<PY
import json
print(json.dumps({
  "project_id": "${PROJECT_ID}",
  "conversation_id": "${CONV_ID}",
  "path": "${DOCS_DIR}",
  "question": "Summarize these docs and produce a short report.",
  "mode": "research",
  "run_now": True,
  "max_steps": 60
}))
PY
)"

echo "[eval_research_mode] Creating research task..."
RESP="$(curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
  -d "${REQ}" \
  "${V1_URL}/research/run")"

TASK_ID="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("task_id",""))' <<<"${RESP}")"

if [[ -z "${TASK_ID}" ]]; then
  echo "[eval_research_mode] Failed to extract task_id from response:"
  echo "${RESP}"
  exit 2
fi

echo "[eval_research_mode] task_id=${TASK_ID}"

status="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("status",""))' <<<"${RESP}")"
echo "[eval_research_mode] initial status=${status}"

for i in $(seq 1 20); do
  if [[ "${status}" == "completed" ]]; then
    break
  fi
  if [[ "${status}" == "paused" ]]; then
    echo "[eval_research_mode] paused (approval required). Rejecting for safety."
    curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
      -d '{"action":"reject","reason":"eval script rejects approvals by default"}' \
      "${V1_URL}/tasks/${TASK_ID}/continue" >/dev/null
    break
  fi
  echo "[eval_research_mode] continue #${i} ..."
  RESP="$(curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
    -d '{"action":"continue","max_step_advances":5,"max_duration_s":25}' \
    "${V1_URL}/tasks/${TASK_ID}/continue")"
  status="$(python3 -c 'import json,sys; data=json.loads(sys.stdin.read() or "{}"); task=data.get("task") or {}; print(task.get("status",""))' <<<"${RESP}")"
  echo "[eval_research_mode] status=${status}"
done

TASK_DIR=".runtime/tasks/${TASK_ID}"
echo "[eval_research_mode] verifying artifacts in ${TASK_DIR}"

req_files=(
  "file_index.json"
  "chunk_summaries.json"
  "file_summaries.md"
  "research_brief.md"
  "open_questions.md"
  "final_report.md"
)

missing=0
for f in "${req_files[@]}"; do
  if [[ ! -f "${TASK_DIR}/${f}" ]]; then
    echo "[eval_research_mode] MISSING: ${TASK_DIR}/${f}"
    missing=1
  else
    echo "[eval_research_mode] OK: ${TASK_DIR}/${f}"
  fi
done

if [[ "${missing}" -ne 0 ]]; then
  echo "[eval_research_mode] FAIL: missing artifacts"
  exit 3
fi

echo "[eval_research_mode] PASS"
