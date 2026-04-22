#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

load_env() {
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
}

load_env

PORT="${PORT:-8095}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"
MODEL="${DEFAULT_MODEL:-gemma4:e2b}"
API_KEY="${PROXY_API_KEY:-local-dev-key}"

AUTH_HEADER="Authorization: Bearer ${API_KEY}"

pass_count=0
fail_count=0

phase() {
  local name="$1"
  echo
  echo "== ${name} =="
}

ok() {
  pass_count=$((pass_count+1))
  echo "[PASS]"
}

bad() {
  fail_count=$((fail_count+1))
  echo "[FAIL] $1" >&2
}

require_server() {
  if ! curl -fsS "${BASE_URL}/health" >/dev/null 2>&1; then
    bad "Server not reachable at ${BASE_URL} (health failed)"
    exit 1
  fi
}

chat() {
  local project_id="$1"
  local conversation_id="$2"
  local content="$3"
  curl -fsS "${BASE_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "$AUTH_HEADER" \
    -d "{\"model\":\"${MODEL}\",\"stream\":false,\"project_id\":\"${project_id}\",\"conversation_id\":\"${conversation_id}\",\"messages\":[{\"role\":\"user\",\"content\":$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$content")}]}";
}

extract() {
  python3 -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps({"x_agent_status": d.get("x_agent_status"), "x_agent_payload": d.get("x_agent_payload"), "content": d["choices"][0]["message"]["content"]}, ensure_ascii=False))'
}

require_server

ts="$(date +%s)"

phase "1) /health"
if curl -fsS "${BASE_URL}/health" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("ok") is True; print("ok")' >/dev/null; then
  ok
else
  bad "/health did not return ok:true"
fi

phase "2) Fast Path Chat (STARAGENT_OK)"
resp="$(chat "smoke-fast" "smoke-fast-${ts}" "Reply with exactly STARAGENT_OK and nothing else.")"
content="$(printf "%s" "$resp" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"].strip())')"
if [[ "$content" == "STARAGENT_OK" || "$content" == *"STARAGENT_OK"* ]]; then
  ok
else
  bad "Expected STARAGENT_OK, got: ${content}"
fi

phase "3) Memory Continuity (store + recall FastAPI)"
conv_mem="smoke-mem-${ts}"
chat "smoke-mem" "$conv_mem" "Use FastAPI as the backend framework. Reply OK only." >/dev/null
resp="$(chat "smoke-mem" "$conv_mem" "What backend framework did we decide to use? Answer one word only.")"
content="$(printf "%s" "$resp" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"].strip())')"
if [[ "$content" == "FastAPI" || "$content" == *"FastAPI"* ]]; then
  ok
else
  bad "Expected FastAPI recall, got: ${content}"
fi

phase "4) Agent-Path Inspection (app/main.py)"
resp="$(chat "smoke-agent" "smoke-agent-${ts}" "Inspect the app folder and identify the main API entry file.")"
meta="$(printf "%s" "$resp" | extract)"
content="$(printf "%s" "$meta" | python3 -c 'import sys,json; print(json.load(sys.stdin)["content"])')"
if [[ "$content" == *"app/main.py"* && "$content" != "Task finished or iteration limit reached." ]]; then
  ok
else
  bad "Expected grounded mention of app/main.py, got: ${content}"
fi

phase "5) Approval-Gated Write (sandbox_test/)"
conv_write="smoke-write-${ts}"
write_path="sandbox_test/smoke_write_${ts}.txt"
write_content="SMOKE_${ts}"
resp1="$(chat "smoke-write" "$conv_write" "Create a file at ${write_path} with exact content: ${write_content}")"
meta1="$(printf "%s" "$resp1" | extract)"
status1="$(printf "%s" "$meta1" | python3 -c 'import sys,json; print(json.load(sys.stdin)["x_agent_status"])')"
content1="$(printf "%s" "$meta1" | python3 -c 'import sys,json; print(json.load(sys.stdin)["content"])')"
if [[ "$status1" == "approval_required" && "$content1" == *"awaiting_approval"* ]]; then
  ok
else
  bad "Expected approval_required + awaiting_approval, got status=${status1} content=${content1}"
fi

resp2="$(chat "smoke-write" "$conv_write" "yes")"
meta2="$(printf "%s" "$resp2" | extract)"
content2="$(printf "%s" "$meta2" | python3 -c 'import sys,json; print(json.load(sys.stdin)["content"])')"
if [[ "$content2" == *"${write_path}"* && "$content2" != "Task finished or iteration limit reached." ]]; then
  ok
else
  bad "Expected grounded write confirmation, got: ${content2}"
fi

abs_written="$ROOT/${write_path}"
if [[ -f "$abs_written" ]]; then
  on_disk="$(python3 -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]); print(p.read_text(encoding="utf-8"))' "$abs_written" | tr -d '\n')"
  if [[ "$on_disk" == "$write_content" ]]; then
    ok
  else
    bad "File content mismatch. Expected '${write_content}', got '${on_disk}'"
  fi
else
  bad "Expected file to exist: ${abs_written}"
fi

phase "6) Continuation Flow (partial -> continue)"
conv_cont="smoke-cont-${ts}"
prompt_cont="Inspect the app folder, then read these files: app/main.py app/memory.py app/database.py app/retrieval.py app/routing.py app/executor.py app/planner.py app/tools.py. Then summarize how requests flow through the system."
resp1="$(chat "smoke-cont" "$conv_cont" "$prompt_cont")"
meta1="$(printf "%s" "$resp1" | extract)"
status1="$(printf "%s" "$meta1" | python3 -c 'import sys,json; print(json.load(sys.stdin)["x_agent_status"])')"
content1="$(printf "%s" "$meta1" | python3 -c 'import sys,json; print(json.load(sys.stdin)["content"])')"
if [[ "$status1" == "partial" && "$content1" == *"app/main.py"* ]]; then
  ok
else
  bad "Expected partial + grounded output, got status=${status1} content=${content1}"
fi

resp2="$(chat "smoke-cont" "$conv_cont" "continue")"
meta2="$(printf "%s" "$resp2" | extract)"
status2="$(printf "%s" "$meta2" | python3 -c 'import sys,json; print(json.load(sys.stdin)["x_agent_status"])')"
content2="$(printf "%s" "$meta2" | python3 -c 'import sys,json; print(json.load(sys.stdin)["content"])')"
if [[ "$status2" == "completed" && "$content2" == *"app/main.py"* && "$content2" != "Task finished or iteration limit reached." ]]; then
  ok
else
  bad "Expected completed + grounded output, got status=${status2} content=${content2}"
fi

echo
echo "Smoke test summary: PASS=${pass_count} FAIL=${fail_count}"
if [[ "$fail_count" -eq 0 ]]; then
  exit 0
fi
exit 2
