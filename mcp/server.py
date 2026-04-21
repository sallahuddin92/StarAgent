from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from client.macagent_client import MacAgentClient, MacAgentConfig
from mcp.transport_stdio import StdioTransport


logger = logging.getLogger(__name__)


def _tool_schema(name: str, description: str, props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": required or [],
        },
    }


class MacAgentMCPServer:
    def __init__(self, client: MacAgentClient, *, debug: bool = False):
        self.client = client
        self.debug = debug

    def tools(self) -> List[Dict[str, Any]]:
        common_props = {
            "project_id": {"type": "string", "description": "Project scope id"},
            "conversation_id": {"type": "string", "description": "Conversation id"},
            "model": {"type": "string", "description": "Model id (default from config)"},
            "debug_raw": {"type": "boolean", "description": "Include raw response for debugging"},
        }
        prompt_props = {"prompt": {"type": "string", "description": "User prompt"}}
        return [
            _tool_schema("macagent_ask", "Fast-path ask through MacAgent (forced fast route).", {**prompt_props, **common_props}, required=["prompt"]),
            _tool_schema("macagent_agent", "Agent-path task through MacAgent (forced agent route).", {**prompt_props, **common_props}, required=["prompt"]),
            _tool_schema("macagent_approve", "Approve pending action (send yes).", common_props),
            _tool_schema("macagent_reject", "Reject pending action (send no).", common_props),
            _tool_schema("macagent_continue", "Continue pending partial task (send continue).", common_props),
            _tool_schema("macagent_status", "Return health/models and client context.", common_props),
            _tool_schema("macagent_rollback", "Best-effort rollback through agent path.", common_props),
            _tool_schema("macagent_smoke_test", "Run compact smoke test against the API.", common_props),
        ]

    def _result(self, *, status: str, message: str = "", data: Optional[Dict[str, Any]] = None, agent_status: Optional[str] = None, raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {"status": status, "message": message, "data": data or {}}
        if agent_status is not None:
            out["agent_status"] = agent_status
        if self.debug and raw is not None:
            out["raw_response"] = raw
        return out

    def call_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        project_id = args.get("project_id")
        conversation_id = args.get("conversation_id")
        model = args.get("model")
        debug_raw = bool(args.get("debug_raw", False))

        try:
            if name == "macagent_ask":
                res = self.client.ask(args["prompt"], project_id=project_id, conversation_id=conversation_id, model=model, debug_raw=debug_raw)
                return self._result(status="ok", message=res.message, agent_status=res.agent_status, raw=res.raw)
            if name == "macagent_agent":
                res = self.client.agent(args["prompt"], project_id=project_id, conversation_id=conversation_id, model=model, debug_raw=debug_raw)
                return self._result(status="ok", message=res.message, agent_status=res.agent_status, raw=res.raw)
            if name == "macagent_approve":
                res = self.client.approve(project_id=project_id, conversation_id=conversation_id, model=model)
                return self._result(status="ok", message=res.message, agent_status=res.agent_status, raw=res.raw)
            if name == "macagent_reject":
                res = self.client.reject(project_id=project_id, conversation_id=conversation_id, model=model)
                return self._result(status="ok", message=res.message, agent_status=res.agent_status, raw=res.raw)
            if name == "macagent_continue":
                res = self.client.continue_task(project_id=project_id, conversation_id=conversation_id, model=model)
                return self._result(status="ok", message=res.message, agent_status=res.agent_status, raw=res.raw)
            if name == "macagent_rollback":
                res = self.client.rollback(project_id=project_id, conversation_id=conversation_id, model=model)
                return self._result(status="ok", message=res.message, agent_status=res.agent_status, raw=res.raw)
            if name == "macagent_status":
                h = self.client.health()
                m = self.client.models()
                return self._result(status="ok", message="ok", data={"health": h, "models": m, "base_url": self.client.v1_base_url})
            if name == "macagent_smoke_test":
                out = self.client.smoke_test_compact()
                return self._result(status="ok" if out.get("ok") else "error", message="smoke_test", data=out)
            return self._result(status="error", message=f"unknown tool: {name}")
        except Exception as e:
            return self._result(status="error", message=str(e))

    def handle(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # JSON-RPC 2.0 style
        rid = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        # Notifications (no id) must not receive a response.
        if rid is None:
            if method in ("notifications/initialized", "notifications.initialized", "initialized"):
                if self.debug:
                    logger.info("MCP notification received: initialized")
                return None
            # Ignore other notifications silently for compatibility.
            if self.debug:
                logger.info(f"Ignoring MCP notification: {method}")
            return None

        if method == "initialize":
            # Claude Code expects protocolVersion echo + capabilities.
            protocol_version = params.get("protocolVersion") or params.get("protocol_version") or "2024-11-05"
            result = {
                "protocolVersion": protocol_version,
                "serverInfo": {"name": "macagent-mcp", "version": "0.1"},
                "capabilities": {
                    "tools": {"listChanged": False},
                },
            }
            return {"jsonrpc": "2.0", "id": rid, "result": result}

        if method in ("tools/list", "tools.list"):
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": self.tools()}}

        if method in ("tools/call", "tools.call"):
            tool_name = params.get("name")
            args = params.get("arguments") or {}
            result = self.call_tool(tool_name, args)
            # MCP expects tool results as content blocks; we return JSON as text for safety.
            return {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ]
                },
            }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}

        if method == "shutdown":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}

        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="macagent-mcp", description="MacAgent MCP server (stdio)")
    ap.add_argument("--debug", action="store_true", help="Include raw responses in tool outputs")
    args = ap.parse_args(argv)

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    # Keep stderr quiet by default; enable request-level debugging via env or flag.
    debug = bool(args.debug or os.getenv("MACAGENT_MCP_DEBUG") == "1")

    client = MacAgentClient(MacAgentConfig.from_env())
    server = MacAgentMCPServer(client, debug=debug)
    transport = StdioTransport()

    try:
        while True:
            msg = transport.read()
            if msg is None:
                break
            resp = server.handle(msg.payload)
            if resp is not None:
                transport.write(resp)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
