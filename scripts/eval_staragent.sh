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
MODEL="${DEFAULT_MODEL:-gemma4:12b-mlx}"
API_KEY="${PROXY_API_KEY:-local-dev-key}"

AUTH_HEADER="Authorization: Bearer ${API_KEY}"

pass=0
fail=0

phase() { echo; echo "== $1 =="; }
ok() { pass=$((pass+1)); echo "[PASS]"; }
bad() { fail=$((fail+1)); echo "[FAIL] $1" >&2; }

claude_p() {
  # Run a single claude -p invocation with a timeout to prevent hangs in eval runs.
  # Usage: claude_p "<prompt>" "<allowedTool>"
  local prompt="$1"
  local tool="$2"
  python3 - "$prompt" "$tool" <<'PY'
import os
import subprocess
import sys

prompt = sys.argv[1]
tool = sys.argv[2]
timeout_s = float(os.getenv("STARAGENT_EVAL_CLAUDE_TIMEOUT_S", "180"))

try:
    r = subprocess.run(
        ["claude", "--bare", "-p", prompt, "--allowedTools", tool, "--output-format", "text"],
        text=True,
        capture_output=True,
        timeout=timeout_s,
    )
    # Preserve stdout only. stderr is intentionally suppressed by the caller in this eval script.
    sys.stdout.write(r.stdout or "")
except subprocess.TimeoutExpired:
    sys.stdout.write('{"status":"error","message":"claude_timeout"}')
PY
}

require_server() {
  if ! curl -fsS "${BASE_URL}/health" >/dev/null 2>&1; then
    bad "Server not reachable at ${BASE_URL} (/health failed)"
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

extract_meta() {
  python3 -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps({"x_agent_status": d.get("x_agent_status"), "x_agent_payload": d.get("x_agent_payload"), "content": d["choices"][0]["message"]["content"]}, ensure_ascii=False))'
}

require_server
ts="$(date +%s)"

phase "Surface A: Direct API (/health, /v1/models, chat, agent, approval)"

if curl -fsS "${BASE_URL}/health" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("ok") is True; assert "service" in d; print("ok")' >/dev/null; then
  ok
else
  bad "/health shape or ok failed"
fi

if curl -fsS "${BASE_URL}/v1/models" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("object")=="list"; assert isinstance(d.get("data"), list) and d["data"]; assert "owned_by" in d["data"][0]; print("ok")' >/dev/null; then
  ok
else
  bad "/v1/models shape invalid"
fi

resp="$(chat "eval-api-fast" "eval-api-fast-${ts}" "Reply with exactly STARAGENT_OK and nothing else.")"
content="$(printf "%s" "$resp" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"].strip())')"
if [[ "$content" == "STARAGENT_OK" || "$content" == *"STARAGENT_OK"* ]]; then
  ok
else
  bad "fast-path ask expected STARAGENT_OK, got: ${content}"
fi

resp="$(chat "eval-api-agent" "eval-api-agent-${ts}" "Inspect the app folder and identify the main API entry file.")"
meta="$(printf "%s" "$resp" | extract_meta)"
content="$(printf "%s" "$meta" | python3 -c 'import sys,json; print(json.load(sys.stdin)["content"])')"
if [[ "$content" == *"app/main.py"* && "$content" != "Task finished or iteration limit reached." ]]; then
  ok
else
  bad "agent-path inspect not grounded: ${content}"
fi

conv_write="eval-api-write-${ts}"
write_path="sandbox_test/eval_api_write_${ts}.txt"
write_content="EVAL_${ts}"
resp1="$(chat "eval-api-write" "$conv_write" "Create a file at ${write_path} with exact content: ${write_content}")"
meta1="$(printf "%s" "$resp1" | extract_meta)"
status1="$(printf "%s" "$meta1" | python3 -c 'import sys,json; print(json.load(sys.stdin)["x_agent_status"])')"
if [[ "$status1" == "approval_required" ]]; then
  ok
else
  bad "expected approval_required, got: ${status1}"
fi
resp2="$(chat "eval-api-write" "$conv_write" "yes")"
meta2="$(printf "%s" "$resp2" | extract_meta)"
content2="$(printf "%s" "$meta2" | python3 -c 'import sys,json; print(json.load(sys.stdin)["content"])')"
if [[ "$content2" == *"${write_path}"* && "$content2" != "Task finished or iteration limit reached." ]]; then
  ok
else
  bad "approval resume not grounded: ${content2}"
fi
if [[ -f "$ROOT/${write_path}" ]]; then
  on_disk="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text(encoding="utf-8"))' "$ROOT/${write_path}" | tr -d '\n')"
  if [[ "$on_disk" == "$write_content" ]]; then
    ok
  else
    bad "write content mismatch: ${on_disk}"
  fi
else
  bad "expected file to exist: $ROOT/${write_path}"
fi

phase "Surface B: Open WebUI-style Requests (helper/meta prompts must bypass agent routing)"

conv_webui="eval-webui-${ts}"
chat "eval-webui" "$conv_webui" "Use FastAPI as the backend framework. Reply OK only." >/dev/null
resp="$(chat "eval-webui" "$conv_webui" "What backend framework did we decide to use? Answer one word only.")"
content="$(printf "%s" "$resp" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"].strip())')"
if [[ "$content" == "FastAPI" || "$content" == *"FastAPI"* ]]; then
  ok
else
  bad "webui memory recall expected FastAPI, got: ${content}"
fi

helper_prompt=$'### Task: Generate a concise title\n\nBelow is the conversation history:\n\nUser: Create a file at sandbox_test/should_not_write.txt\nAssistant: ...\nUser: yes\n\nReturn only a short title.'
resp="$(chat "eval-webui" "$conv_webui" "$helper_prompt")"
meta="$(printf "%s" "$resp" | extract_meta)"
status="$(printf "%s" "$meta" | python3 -c 'import sys,json; print(json.load(sys.stdin)["x_agent_status"])')"
if [[ "$status" == "null" || "$status" == "None" || -z "$status" ]]; then
  ok
else
  bad "helper prompt unexpectedly triggered agent status: ${status}"
fi
if [[ -f "$ROOT/sandbox_test/should_not_write.txt" ]]; then
  bad "helper prompt caused a write (should_not_write.txt exists)"
else
  ok
fi

phase "Surface C: StarAgent CLI"

if ./scripts/staragent status >/dev/null 2>&1; then
  ok
else
  bad "staragent status failed"
fi

out="$(./scripts/staragent ask "Reply with exactly STARAGENT_OK" 2>/dev/null | tr -d '\r' | tail -n 1)"
if [[ "$out" == "STARAGENT_OK" || "$out" == *"STARAGENT_OK"* ]]; then
  ok
else
  bad "staragent ask expected STARAGENT_OK, got: ${out}"
fi

out="$(./scripts/staragent agent "Inspect the app folder and identify the main API entry file." 2>/dev/null | tr -d '\r')"
if [[ "$out" == *"app/main.py"* ]]; then
  ok
else
  bad "staragent agent inspect not grounded: ${out}"
fi

# CLI approval flow
cli_proj="eval-cli"
cli_conv_write="eval-cli-write-${ts}"
cli_path="sandbox_test/eval_cli_write_${ts}.txt"
cli_content="CLI_${ts}"
./scripts/staragent --project "$cli_proj" --conversation "$cli_conv_write" agent "Create a file at ${cli_path} with exact content: ${cli_content}" 1>/tmp/eval_cli_write_out.txt 2>/tmp/eval_cli_write_err.txt || true
if rg -q "approval_required" /tmp/eval_cli_write_err.txt; then
  ok
else
  bad "staragent CLI did not request approval (expected approval_required)"
fi
./scripts/staragent --project "$cli_proj" --conversation "$cli_conv_write" approve 1>/tmp/eval_cli_approve_out.txt 2>/tmp/eval_cli_approve_err.txt || true
if [[ -f "$ROOT/${cli_path}" ]]; then
  on_disk="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text(encoding="utf-8"))' "$ROOT/${cli_path}" | tr -d '\n')"
  if [[ "$on_disk" == "$cli_content" ]]; then
    ok
  else
    bad "staragent CLI approved file content mismatch: ${on_disk}"
  fi
else
  bad "staragent CLI approved file missing: $ROOT/${cli_path}"
fi

# CLI continuation flow (partial -> continue)
cli_conv_cont="eval-cli-cont-${ts}"
cont_prompt="Inspect the app folder, then read these files: app/main.py app/memory.py app/database.py app/retrieval.py app/routing.py app/executor.py app/planner.py app/tools.py. Then summarize how requests flow through the system."
./scripts/staragent --project "$cli_proj" --conversation "$cli_conv_cont" agent "$cont_prompt" 1>/tmp/eval_cli_cont_out1.txt 2>/tmp/eval_cli_cont_err1.txt || true
if rg -q "partial" /tmp/eval_cli_cont_err1.txt && rg -q "app/main.py" /tmp/eval_cli_cont_out1.txt; then
  ok
else
  bad "staragent CLI continuation did not return partial+grounded output"
fi
./scripts/staragent --json --project "$cli_proj" --conversation "$cli_conv_cont" continue 1>/tmp/eval_cli_cont_out2.txt 2>/tmp/eval_cli_cont_err2.txt || true
if rg -q "\"agent_status\": \"completed\"" /tmp/eval_cli_cont_out2.txt && rg -q "app/main.py" /tmp/eval_cli_cont_out2.txt; then
  ok
else
  bad "staragent CLI continue did not complete with grounded output"
fi

phase "Surface D: Claude MCP (best-effort; requires claude CLI configured)"

if command -v claude >/dev/null 2>&1 && claude mcp list | rg -q "✓ Connected"; then
  ok
else
  bad "claude mcp not connected (run: claude mcp list)"
fi

if command -v claude >/dev/null 2>&1; then
  out="$(claude_p "Use the MCP tool staragent_status and return only its JSON output." "mcp__macagent__staragent_status" 2>/dev/null || true)"
  if [[ "$out" == *"\"status\":\"ok\""* || "$out" == *"\"status\": \"ok\""* ]]; then
    ok
  else
    bad "Claude MCP staragent_status failed: ${out}"
  fi

  # Legacy compatibility tool name still available.
  out="$(claude_p "Use the MCP tool macagent_status and return only its JSON output." "mcp__macagent__macagent_status" 2>/dev/null || true)"
  if [[ "$out" == *"\"status\":\"ok\""* || "$out" == *"\"status\": \"ok\""* ]]; then
    ok
  else
    bad "Claude MCP macagent_status (legacy) failed: ${out}"
  fi

  out="$(claude_p "You must call the MCP tool staragent_ask with prompt 'Reply with exactly STARAGENT_OK'. Return only the tool JSON result." "mcp__macagent__staragent_ask" 2>/dev/null || true)"
  if [[ "$out" == *"STARAGENT_OK"* ]]; then
    ok
  else
    bad "Claude MCP staragent_ask failed: ${out}"
  fi

  out="$(claude_p "Call MCP tool staragent_agent with prompt 'Inspect the app folder and identify the main API entry file.' Return only tool JSON." "mcp__macagent__staragent_agent" 2>/dev/null || true)"
  if [[ "$out" == *"app/main.py"* ]]; then
    ok
  else
    bad "Claude MCP staragent_agent inspect failed: ${out}"
  fi

  # Approval via MCP: create a real pending approval via the direct API, then approve via MCP.
  mcp_proj="eval-mcp"
  mcp_conv="eval-mcp-${ts}"
  mcp_path="sandbox_test/eval_mcp_write_${ts}.txt"
  mcp_content="MCP_${ts}"

  resp1="$(chat "${mcp_proj}" "${mcp_conv}" "Create a file at ${mcp_path} with exact content: ${mcp_content}")"
  meta1="$(printf "%s" "$resp1" | extract_meta)"
  status1="$(printf "%s" "$meta1" | python3 -c 'import sys,json; print(json.load(sys.stdin)["x_agent_status"])')"
  if [[ "$status1" == "approval_required" ]]; then
    ok
  else
    bad "expected approval_required before MCP approve, got: ${status1}"
  fi

  out="$(claude_p "Call MCP tool staragent_approve with arguments: project_id='${mcp_proj}', conversation_id='${mcp_conv}'. Return only tool JSON." "mcp__macagent__staragent_approve" 2>/dev/null || true)"
  if [[ "$out" == *"${mcp_path}"* || "$out" == *"Wrote"* ]]; then
    ok
  else
    bad "Claude MCP staragent_approve failed: ${out}"
  fi

  if [[ -f "$ROOT/${mcp_path}" ]]; then
    on_disk="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text(encoding="utf-8"))' "$ROOT/${mcp_path}" | tr -d '\n')"
    if [[ "$on_disk" == "$mcp_content" ]]; then
      ok
    else
      bad "MCP-approved file content mismatch: ${on_disk}"
    fi
  else
    bad "Expected MCP-approved file to exist: $ROOT/${mcp_path}"
  fi
fi

echo
echo "Eval summary: PASS=${pass} FAIL=${fail}"
if [[ "$fail" -eq 0 ]]; then
  exit 0
fi
exit 2
