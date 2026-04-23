#!/usr/bin/env bash
set -euo pipefail

# Live evaluation: JSON dataset mode end-to-end (adaptive intake -> dataset artifacts)
#
# Requirements:
# - StarAgent server running (default: http://127.0.0.1:8095)
# - Uses only sandbox_test/ for input dataset
# - Verifies dataset artifacts exist under .runtime/tasks/<task_id>/

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

BASE_URL="${STARAGENT_HTTP_URL:-http://127.0.0.1:${PORT:-8095}}"
V1_URL="${STARAGENT_BASE_URL:-${BASE_URL}/v1}"
API_KEY="${STARAGENT_API_KEY:-${MACAGENT_API_KEY:-${PROXY_API_KEY:-local-dev-key}}}"
AUTH_HEADER="Authorization: Bearer ${API_KEY}"

echo "[eval_json_dataset_mode] Base: ${BASE_URL}"
echo "[eval_json_dataset_mode] V1:   ${V1_URL}"

curl -fsS "${BASE_URL}/health" >/dev/null
echo "[eval_json_dataset_mode] /health OK"

DATA_DIR="sandbox_test/json_dataset_eval"
mkdir -p "${DATA_DIR}"
DATA_FILE="${DATA_DIR}/dataset.jsonl"

if [[ ! -f "${DATA_FILE}" ]]; then
  echo "[eval_json_dataset_mode] Generating ~30MB NDJSON dataset at ${DATA_FILE} ..."
  python3 - <<'PY'
import json
from pathlib import Path

path = Path("sandbox_test/json_dataset_eval/dataset.jsonl")
path.parent.mkdir(parents=True, exist_ok=True)

with path.open("w", encoding="utf-8") as f:
    i = 0
    while True:
        rec = {"id": i, "category": f"cat{i%10}", "value": i % 97, "text": "x" * 80}
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        i += 1
        if i % 1000 == 0 and path.stat().st_size >= 30 * 1024 * 1024:
            break

print("wrote", path, "bytes", path.stat().st_size)
PY
else
  echo "[eval_json_dataset_mode] Using existing dataset at ${DATA_FILE}"
fi

PROJECT_ID="eval-json-ds"
CONV_ID="eval-json-ds-$(date +%s)"

REQ="$(python3 - <<PY
import json
print(json.dumps({
  "project_id": "${PROJECT_ID}",
  "conversation_id": "${CONV_ID}",
  "path": "${DATA_DIR}",
  "question": "Profile this dataset and produce a short report grounded in the sampled records.",
  "mode": "research",
  "run_now": True,
  "max_steps": 30
}))
PY
)"

echo "[eval_json_dataset_mode] Creating research task..."
RESP="$(curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" -d "${REQ}" "${V1_URL}/research/run")"
TASK_ID="$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print((d.get("task") or {}).get("task_id",""))' <<<"${RESP}")"
if [[ -z "${TASK_ID}" ]]; then
  echo "[eval_json_dataset_mode] Failed to extract task_id from response:"
  echo "${RESP}"
  exit 2
fi
echo "[eval_json_dataset_mode] task_id=${TASK_ID}"

status="$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print((d.get("task") or {}).get("status",""))' <<<"${RESP}")"
echo "[eval_json_dataset_mode] initial status=${status}"

for i in $(seq 1 25); do
  if [[ "${status}" == "completed" ]]; then
    break
  fi
  if [[ "${status}" == "paused" ]]; then
    echo "[eval_json_dataset_mode] paused (approval required). Rejecting for safety."
    curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
      -d '{"action":"reject","reason":"eval script rejects approvals by default"}' \
      "${V1_URL}/tasks/${TASK_ID}/continue" >/dev/null
    break
  fi
  echo "[eval_json_dataset_mode] continue #${i} ..."
  RESP="$(curl -fsS -H "${AUTH_HEADER}" -H "Content-Type: application/json" \
    -d '{"action":"continue","max_step_advances":3,"max_duration_s":25}' \
    "${V1_URL}/tasks/${TASK_ID}/continue")"
  status="$(python3 -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print((d.get("task") or {}).get("status",""))' <<<"${RESP}")"
  echo "[eval_json_dataset_mode] status=${status}"
done

TASK_DIR=".runtime/tasks/${TASK_ID}"
echo "[eval_json_dataset_mode] verifying artifacts in ${TASK_DIR}"

req_files=(
  "input_intake.json"
  "dataset_profile.json"
  "sample_records.json"
  "batch_summaries.json"
  "dataset_brief.md"
  "themes.json"
  "themes.md"
  "open_questions.md"
  "final_report.md"
)

missing=0
for f in "${req_files[@]}"; do
  if [[ ! -f "${TASK_DIR}/${f}" ]]; then
    echo "[eval_json_dataset_mode] MISSING: ${TASK_DIR}/${f}"
    missing=1
  else
    echo "[eval_json_dataset_mode] OK: ${TASK_DIR}/${f}"
  fi
done

if [[ "${missing}" -ne 0 ]]; then
  echo "[eval_json_dataset_mode] FAIL: missing artifacts"
  exit 3
fi

echo "[eval_json_dataset_mode] PASS"
