# MacAgent MCP Server Setup

MacAgent MCP server exposes MacAgent capabilities as MCP tools, but **does not replace** the FastAPI runtime.
It calls the running FastAPI API (`MACAGENT_BASE_URL`) as the source of truth.

## Prereqs

1. Start MacAgent:
```bash
./scripts/start_macagent.sh
```

2. Export config (or set in your client config):
```bash
export MACAGENT_BASE_URL="http://127.0.0.1:8095/v1"
export MACAGENT_API_KEY="local-dev-key"
export MACAGENT_DEFAULT_MODEL="gemma4:e2b"
```

## Run the MCP server (stdio)

Most MCP clients (Claude Code, Codex) will spawn the server as a subprocess. For manual debugging:
```bash
python3 -m mcp.server
```

## Install (Optional, enables `macagent-mcp` command)

```bash
python3 -m pip install -e .
macagent-mcp
```

If `macagent-mcp` is not found after install, add:
```bash
export PATH=\"$HOME/Library/Python/3.9/bin:$PATH\"
```

Or use the repo-local wrapper:
```bash
./scripts/macagent-mcp
```

## Tools exposed

- `macagent_ask`
- `macagent_agent`
- `macagent_approve`
- `macagent_reject`
- `macagent_continue`
- `macagent_status`
- `macagent_rollback`
- `macagent_smoke_test`

## Manual local self-test (JSON-RPC framing)

This prints the tool list using LSP-style `Content-Length` framing.
```bash
python3 - <<'PY'\nimport json,sys,subprocess\np=subprocess.Popen([sys.executable,'-m','mcp.server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)\nreq={\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}\nmsg=json.dumps(req).encode('utf-8')\nframe=(f\"Content-Length: {len(msg)}\\r\\n\\r\\n\").encode('utf-8')+msg\np.stdin.write(frame); p.stdin.flush()\n# Read header\nhdr=b\"\"\nwhile b\"\\r\\n\\r\\n\" not in hdr:\n  hdr+=p.stdout.read(1)\ncl=int([l for l in hdr.decode().split('\\r\\n') if l.lower().startswith('content-length')][0].split(':',1)[1])\nbody=p.stdout.read(cl)\nprint(body.decode())\np.terminate()\nPY\n```
