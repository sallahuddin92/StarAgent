# StarAgent MCP Server Setup (legacy compatible: macagent tools)

StarAgent MCP server exposes StarAgent capabilities as MCP tools, but **does not replace** the FastAPI runtime.
It calls the running FastAPI API (`STARAGENT_BASE_URL`) as the source of truth.

## Prereqs

1. Start StarAgent:
```bash
./scripts/start_staragent.sh
```

2. Export config (or set in your client config):
```bash
export STARAGENT_BASE_URL="http://127.0.0.1:8095/v1"
export STARAGENT_API_KEY="local-dev-key"
export STARAGENT_DEFAULT_MODEL="gemma4:e2b"
```

## Run the MCP server (stdio)

Most MCP clients (Claude Code, Codex) will spawn the server as a subprocess. For manual debugging:
```bash
python3 -m mcp.server
```

## Install (Optional, enables `staragent-mcp` command)

```bash
python3 -m pip install -e .
staragent-mcp
```

If `staragent-mcp` is not found after install, add:
```bash
export PATH=\"$HOME/Library/Python/3.9/bin:$PATH\"
```

Or use the repo-local wrapper:
```bash
./scripts/staragent-mcp
```

## Tools exposed

Primary (preferred):
- `staragent_ask`
- `staragent_agent`
- `staragent_approve`
- `staragent_reject`
- `staragent_continue`
- `staragent_status`
- `staragent_rollback`
- `staragent_smoke_test`

Legacy compatible tool names are still available:
- `macagent_ask`, `macagent_agent`
- `macagent_approve`, `macagent_reject`
- `macagent_continue`
- `macagent_status`, `macagent_smoke_test`

## Manual local self-test (JSON-RPC framing)

This prints the tool list using LSP-style `Content-Length` framing (some clients use newline-delimited JSON-RPC instead).
```bash
python3 - <<'PY'\nimport json,sys,subprocess\np=subprocess.Popen([sys.executable,'-m','mcp.server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)\nreq={\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}\nmsg=json.dumps(req).encode('utf-8')\nframe=(f\"Content-Length: {len(msg)}\\r\\n\\r\\n\").encode('utf-8')+msg\np.stdin.write(frame); p.stdin.flush()\n# Read header\nhdr=b\"\"\nwhile b\"\\r\\n\\r\\n\" not in hdr:\n  hdr+=p.stdout.read(1)\ncl=int([l for l in hdr.decode().split('\\r\\n') if l.lower().startswith('content-length')][0].split(':',1)[1])\nbody=p.stdout.read(cl)\nprint(body.decode())\np.terminate()\nPY\n```
