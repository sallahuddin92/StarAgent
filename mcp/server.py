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


def _tool_schema(
    name: str,
    description: str,
    props: Dict[str, Any],
    required: Optional[List[str]] = None,
    annotations: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": required or [],
        },
    }
    # Optional MCP tool annotations (clients may use these to decide whether to
    # elicit user confirmation for tool calls).
    if annotations:
        out["annotations"] = annotations
    return out


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
        ro = {"readOnlyHint": True, "idempotentHint": True}
        # StarAgent enforces approval-gated writes internally. For Codex exec-mode
        # compatibility, treat agent calls as "potentially stateful" but not
        # explicitly destructive at the MCP metadata layer.
        agent_ann = {"readOnlyHint": False}
        wr = {"readOnlyHint": False, "destructiveHint": True}
        # Tool names are part of external integrations. Provide StarAgent-prefixed
        # aliases while preserving the original macagent_* tool names.
        tools = [
            _tool_schema("staragent_ask", "Fast-path ask through StarAgent (forced fast route).", {**prompt_props, **common_props}, required=["prompt"], annotations=ro),
            _tool_schema("staragent_agent", "Agent-path task through StarAgent (forced agent route).", {**prompt_props, **common_props}, required=["prompt"], annotations=agent_ann),
            _tool_schema("staragent_approve", "Approve pending action (send yes).", common_props, annotations=wr),
            _tool_schema("staragent_reject", "Reject pending action (send no).", common_props, annotations=wr),
            _tool_schema("staragent_continue", "Continue pending partial task (send continue).", common_props, annotations=wr),
            _tool_schema("staragent_status", "Return health/models and client context.", common_props, annotations=ro),
            _tool_schema("staragent_rollback", "Best-effort rollback through agent path.", common_props, annotations=wr),
            _tool_schema("staragent_smoke_test", "Run compact smoke test against the API.", common_props, annotations=wr),
            _tool_schema(
                "staragent_task_run",
                "Create (and optionally run) an iterative task run.",
                {
                    "goal": {"type": "string", "description": "Task goal"},
                    "task_type": {"type": "string", "description": "Task type (agent|research)", "default": "agent"},
                    "definition_of_done": {"type": "string", "description": "Definition of done"},
                    "max_steps": {"type": "integer", "description": "Max steps", "default": 25},
                    "max_retries": {"type": "integer", "description": "Max retries per step", "default": 2},
                    "run_now": {"type": "boolean", "description": "Run immediately", "default": True},
                    **common_props,
                },
                required=["goal"],
                annotations=wr,
            ),
            _tool_schema(
                "staragent_task_status",
                "Fetch task status and steps.",
                {"task_id": {"type": "string", "description": "Task id"}, **common_props},
                required=["task_id"],
                annotations=ro,
            ),
            _tool_schema(
                "staragent_task_continue",
                "Continue a task run (bounded).",
                {
                    "task_id": {"type": "string", "description": "Task id"},
                    "max_step_advances": {"type": "integer", "default": 3},
                    "max_duration_s": {"type": "number", "default": 20.0},
                    **common_props,
                },
                required=["task_id"],
                annotations=wr,
            ),
            _tool_schema(
                "staragent_task_approve",
                "Approve a paused task tool action.",
                {"task_id": {"type": "string", "description": "Task id"}, **common_props},
                required=["task_id"],
                annotations=wr,
            ),
            _tool_schema(
                "staragent_task_reject",
                "Reject a paused task tool action.",
                {"task_id": {"type": "string", "description": "Task id"}, "reason": {"type": "string"}, **common_props},
                required=["task_id"],
                annotations=wr,
            ),
            _tool_schema(
                "staragent_research_run",
                "Run document research on a local folder (creates a research task).",
                {
                    "path": {"type": "string", "description": "Folder path"},
                    "files": {"type": "array", "items": {"type": "string"}, "description": "Optional relative file list"},
                    "question": {"type": "string", "description": "Research question"},
                    "mode": {"type": "string", "description": "summary|research|comparison", "default": "research"},
                    "max_steps": {"type": "integer", "default": 60},
                    "run_now": {"type": "boolean", "default": True},
                    **common_props,
                },
                required=["path"],
                annotations=wr,
            ),
            _tool_schema(
                "staragent_task_list",
                "List recent task runs.",
                {
                    "status": {"type": "string", "description": "Filter by status"},
                    "limit": {"type": "integer", "default": 50},
                    "offset": {"type": "integer", "default": 0},
                    **common_props,
                },
                annotations=ro,
            ),
            _tool_schema(
                "staragent_task_inspect",
                "Inspect a task run (task + progress + steps).",
                {"task_id": {"type": "string", "description": "Task id"}, **common_props},
                required=["task_id"],
                annotations=ro,
            ),
            _tool_schema(
                "staragent_task_summary",
                "Fetch a task summary (status/progress/final summary).",
                {"task_id": {"type": "string", "description": "Task id"}, **common_props},
                required=["task_id"],
                annotations=ro,
            ),
            _tool_schema(
                "staragent_task_logs",
                "Fetch task step logs (tail).",
                {"task_id": {"type": "string", "description": "Task id"}, "tail_steps": {"type": "integer", "default": 50}, **common_props},
                required=["task_id"],
                annotations=ro,
            ),
            _tool_schema(
                "staragent_task_artifacts",
                "List task artifacts (files).",
                {"task_id": {"type": "string", "description": "Task id"}, **common_props},
                required=["task_id"],
                annotations=ro,
            ),
            _tool_schema(
                "staragent_task_artifact_preview",
                "Preview a task artifact (markdown/text/json).",
                {
                    "task_id": {"type": "string", "description": "Task id"},
                    "artifact_name": {"type": "string", "description": "Artifact file name"},
                    "format": {"type": "string", "default": "text"},
                    "tail_lines": {"type": "integer", "default": 200},
                    **common_props,
                },
                required=["task_id", "artifact_name"],
                annotations=ro,
            ),
            _tool_schema("staragent_presets_list", "List available preset workflows.", {}, annotations=ro),
            _tool_schema("staragent_preset_packs_list", "List curated preset packs (operator flows).", {}, annotations=ro),
            _tool_schema(
                "staragent_preset_pack_run",
                "Run a curated preset pack (may run multiple presets; may pause for approval if stateful).",
                {
                    "pack_name": {"type": "string", "description": "Pack name (e.g. repo_onboarding, release_prep)"},
                    "path": {"type": "string"},
                    "question": {"type": "string"},
                    "issue": {"type": "string"},
                    "goal": {"type": "string"},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "logs": {"type": "array", "items": {"type": "string"}},
                    "mode": {"type": "string"},
                    "output_path": {"type": "string"},
                    "max_steps": {"type": "integer"},
                    "max_retries": {"type": "integer"},
                    "run_now": {"type": "boolean", "default": True},
                    **common_props,
                },
                required=["pack_name"],
                annotations=wr,
            ),
            _tool_schema(
                "staragent_quick_repo_audit",
                "Preset: quick repo audit (read-only).",
                {"path": {"type": "string"}, "question": {"type": "string"}, "max_steps": {"type": "integer"}, "run_now": {"type": "boolean", "default": True}, **common_props},
                required=["path"],
                annotations=ro,
            ),
            _tool_schema(
                "staragent_deep_repo_audit",
                "Preset: deep repo audit (read-only).",
                {"path": {"type": "string"}, "question": {"type": "string"}, "max_steps": {"type": "integer"}, "run_now": {"type": "boolean", "default": True}, **common_props},
                required=["path"],
                annotations=ro,
            ),
            _tool_schema(
                "staragent_bug_triage",
                "Preset: bug triage (read-only).",
                {"path": {"type": "string"}, "issue": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}, "logs": {"type": "array", "items": {"type": "string"}}, "max_steps": {"type": "integer"}, "run_now": {"type": "boolean", "default": True}, **common_props},
                required=["path", "issue"],
                annotations=ro,
            ),
            _tool_schema(
                "staragent_docs_research",
                "Preset: docs research (read-only).",
                {"path": {"type": "string"}, "question": {"type": "string"}, "mode": {"type": "string", "default": "research"}, "files": {"type": "array", "items": {"type": "string"}}, "max_steps": {"type": "integer"}, "run_now": {"type": "boolean", "default": True}, **common_props},
                required=["path"],
                annotations=ro,
            ),
            _tool_schema(
                "staragent_structured_memo",
                "Preset: structured memo (read-only).",
                {"path": {"type": "string"}, "goal": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}, "max_steps": {"type": "integer"}, "run_now": {"type": "boolean", "default": True}, **common_props},
                required=["path"],
                annotations=ro,
            ),
            _tool_schema(
                "staragent_release_review",
                "Preset: release review (stateful; may require approval).",
                {"path": {"type": "string"}, "goal": {"type": "string"}, "output_path": {"type": "string"}, "max_steps": {"type": "integer"}, "run_now": {"type": "boolean", "default": True}, **common_props},
                required=[],
                annotations=wr,
            ),
        ]
        tools.extend(
            [
                _tool_schema("macagent_ask", "Fast-path ask through StarAgent (legacy tool name: macagent_ask).", {**prompt_props, **common_props}, required=["prompt"], annotations=ro),
                _tool_schema("macagent_agent", "Agent-path task through StarAgent (legacy tool name: macagent_agent).", {**prompt_props, **common_props}, required=["prompt"], annotations=agent_ann),
                _tool_schema("macagent_approve", "Approve pending action (legacy tool name: macagent_approve).", common_props, annotations=wr),
                _tool_schema("macagent_reject", "Reject pending action (legacy tool name: macagent_reject).", common_props, annotations=wr),
                _tool_schema("macagent_continue", "Continue pending partial task (legacy tool name: macagent_continue).", common_props, annotations=wr),
                _tool_schema("macagent_status", "Return health/models and client context (legacy tool name: macagent_status).", common_props, annotations=ro),
                _tool_schema("macagent_rollback", "Best-effort rollback (legacy tool name: macagent_rollback).", common_props, annotations=wr),
                _tool_schema("macagent_smoke_test", "Run compact smoke test (legacy tool name: macagent_smoke_test).", common_props, annotations=wr),
                _tool_schema("macagent_task_run", "Create/run task (legacy tool name).", {"goal": {"type": "string"}, "task_type": {"type": "string", "default": "agent"}, "definition_of_done": {"type": "string"}, "max_steps": {"type": "integer", "default": 25}, "max_retries": {"type": "integer", "default": 2}, "run_now": {"type": "boolean", "default": True}, **common_props}, required=["goal"]),
                _tool_schema("macagent_task_status", "Task status (legacy tool name).", {"task_id": {"type": "string"}, **common_props}, required=["task_id"]),
                _tool_schema("macagent_task_continue", "Task continue (legacy tool name).", {"task_id": {"type": "string"}, "max_step_advances": {"type": "integer", "default": 3}, "max_duration_s": {"type": "number", "default": 20.0}, **common_props}, required=["task_id"]),
                _tool_schema("macagent_task_approve", "Task approve (legacy tool name).", {"task_id": {"type": "string"}, **common_props}, required=["task_id"]),
                _tool_schema("macagent_task_reject", "Task reject (legacy tool name).", {"task_id": {"type": "string"}, "reason": {"type": "string"}, **common_props}, required=["task_id"]),
                _tool_schema("macagent_research_run", "Research run (legacy tool name).", {"path": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}, "question": {"type": "string"}, "mode": {"type": "string", "default": "research"}, "max_steps": {"type": "integer", "default": 60}, "run_now": {"type": "boolean", "default": True}, **common_props}, required=["path"]),
                _tool_schema("macagent_task_list", "List recent task runs (legacy tool name).", {"status": {"type": "string"}, "limit": {"type": "integer", "default": 50}, "offset": {"type": "integer", "default": 0}, **common_props}),
                _tool_schema("macagent_task_inspect", "Inspect a task run (legacy tool name).", {"task_id": {"type": "string"}, **common_props}, required=["task_id"]),
                _tool_schema("macagent_task_summary", "Task summary (legacy tool name).", {"task_id": {"type": "string"}, **common_props}, required=["task_id"]),
                _tool_schema("macagent_task_logs", "Task logs (legacy tool name).", {"task_id": {"type": "string"}, "tail_steps": {"type": "integer", "default": 50}, **common_props}, required=["task_id"]),
                _tool_schema("macagent_task_artifacts", "Task artifacts list (legacy tool name).", {"task_id": {"type": "string"}, **common_props}, required=["task_id"]),
                _tool_schema("macagent_task_artifact_preview", "Task artifact preview (legacy tool name).", {"task_id": {"type": "string"}, "artifact_name": {"type": "string"}, "format": {"type": "string", "default": "text"}, "tail_lines": {"type": "integer", "default": 200}, **common_props}, required=["task_id", "artifact_name"]),
            ]
        )
        return tools

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

        # Compatibility: StarAgent-prefixed tool names exist for external
        # integrations, but the server historically implemented legacy
        # macagent_* handlers. Only normalize known 1:1 aliases; do not blanket
        # rewrite all staragent_* tool names (presets/task tooling are real
        # StarAgent tools and must remain addressable).
        staragent_aliases: Dict[str, str] = {
            "staragent_ask": "macagent_ask",
            "staragent_agent": "macagent_agent",
            "staragent_approve": "macagent_approve",
            "staragent_reject": "macagent_reject",
            "staragent_continue": "macagent_continue",
            "staragent_status": "macagent_status",
            "staragent_rollback": "macagent_rollback",
            "staragent_smoke_test": "macagent_smoke_test",
            "staragent_task_run": "macagent_task_run",
            "staragent_task_status": "macagent_task_status",
            "staragent_task_continue": "macagent_task_continue",
            "staragent_task_approve": "macagent_task_approve",
            "staragent_task_reject": "macagent_task_reject",
            "staragent_research_run": "macagent_research_run",
            "staragent_task_list": "macagent_task_list",
            "staragent_task_inspect": "macagent_task_inspect",
            "staragent_task_summary": "macagent_task_summary",
            "staragent_task_logs": "macagent_task_logs",
            "staragent_task_artifacts": "macagent_task_artifacts",
            "staragent_task_artifact_preview": "macagent_task_artifact_preview",
        }
        name = staragent_aliases.get(name, name)

        try:
            if name == "staragent_presets_list":
                out = self.client.presets_list()
                return self._result(status="ok", message="presets_list", data=out)
            if name == "staragent_preset_packs_list":
                out = self.client.preset_packs_list()
                return self._result(status="ok", message="preset_packs_list", data=out)
            if name == "staragent_preset_pack_run":
                out = self.client.preset_pack_run(
                    str(args.get("pack_name") or ""),
                    project_id=project_id,
                    conversation_id=conversation_id,
                    path=args.get("path"),
                    question=args.get("question"),
                    issue=args.get("issue"),
                    goal=args.get("goal"),
                    files=args.get("files"),
                    logs=args.get("logs"),
                    mode=args.get("mode"),
                    output_path=args.get("output_path"),
                    max_steps=args.get("max_steps"),
                    max_retries=args.get("max_retries"),
                    run_now=bool(args.get("run_now", True)),
                )
                return self._result(status="ok", message="preset_pack_run", data=out)
            if name == "staragent_quick_repo_audit":
                out = self.client.preset_run(
                    "quick_repo_audit",
                    project_id=project_id,
                    conversation_id=conversation_id,
                    path=args.get("path"),
                    question=args.get("question"),
                    max_steps=args.get("max_steps"),
                    run_now=bool(args.get("run_now", True)),
                )
                return self._result(status="ok", message="quick_repo_audit", data=out)
            if name == "staragent_deep_repo_audit":
                out = self.client.preset_run(
                    "deep_repo_audit",
                    project_id=project_id,
                    conversation_id=conversation_id,
                    path=args.get("path"),
                    question=args.get("question"),
                    max_steps=args.get("max_steps"),
                    run_now=bool(args.get("run_now", True)),
                )
                return self._result(status="ok", message="deep_repo_audit", data=out)
            if name == "staragent_bug_triage":
                out = self.client.preset_run(
                    "bug_triage",
                    project_id=project_id,
                    conversation_id=conversation_id,
                    path=args.get("path"),
                    issue=args.get("issue"),
                    files=args.get("files"),
                    logs=args.get("logs"),
                    max_steps=args.get("max_steps"),
                    run_now=bool(args.get("run_now", True)),
                )
                return self._result(status="ok", message="bug_triage", data=out)
            if name == "staragent_docs_research":
                out = self.client.preset_run(
                    "docs_research",
                    project_id=project_id,
                    conversation_id=conversation_id,
                    path=args.get("path"),
                    question=args.get("question"),
                    files=args.get("files"),
                    mode=args.get("mode"),
                    max_steps=args.get("max_steps"),
                    run_now=bool(args.get("run_now", True)),
                )
                return self._result(status="ok", message="docs_research", data=out)
            if name == "staragent_structured_memo":
                out = self.client.preset_run(
                    "structured_memo",
                    project_id=project_id,
                    conversation_id=conversation_id,
                    path=args.get("path"),
                    goal=args.get("goal"),
                    files=args.get("files"),
                    max_steps=args.get("max_steps"),
                    run_now=bool(args.get("run_now", True)),
                )
                return self._result(status="ok", message="structured_memo", data=out)
            if name == "staragent_release_review":
                out = self.client.preset_run(
                    "release_review",
                    project_id=project_id,
                    conversation_id=conversation_id,
                    path=args.get("path"),
                    goal=args.get("goal"),
                    output_path=args.get("output_path"),
                    max_steps=args.get("max_steps"),
                    run_now=bool(args.get("run_now", True)),
                )
                return self._result(status="ok", message="release_review", data=out)
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
            if name == "macagent_task_run":
                out = self.client.task_create(
                    user_goal=args["goal"],
                    task_type=args.get("task_type") or "agent",
                    definition_of_done=args.get("definition_of_done"),
                    project_id=project_id,
                    conversation_id=conversation_id,
                    max_steps=int(args.get("max_steps") or 25),
                    max_retries=int(args.get("max_retries") or 2),
                    run_now=bool(args.get("run_now", True)),
                )
                return self._result(status="ok", message="task_run", data=out)
            if name == "macagent_task_status":
                out = self.client.task_status(args["task_id"])
                return self._result(status="ok", message="task_status", data=out)
            if name == "macagent_task_continue":
                out = self.client.task_action(
                    args["task_id"],
                    action="continue",
                    max_step_advances=int(args.get("max_step_advances") or 3),
                    max_duration_s=float(args.get("max_duration_s") or 20.0),
                )
                return self._result(status="ok", message="task_continue", data=out)
            if name == "macagent_task_approve":
                out = self.client.task_action(args["task_id"], action="approve")
                return self._result(status="ok", message="task_approve", data=out)
            if name == "macagent_task_reject":
                out = self.client.task_action(args["task_id"], action="reject", reason=args.get("reason") or "rejected")
                return self._result(status="ok", message="task_reject", data=out)
            if name == "macagent_research_run":
                out = self.client.research_run(
                    path=args["path"],
                    files=args.get("files"),
                    question=args.get("question"),
                    mode=args.get("mode") or "research",
                    project_id=project_id,
                    conversation_id=conversation_id,
                    max_steps=int(args.get("max_steps") or 60),
                    run_now=bool(args.get("run_now", True)),
                )
                return self._result(status="ok", message="research_run", data=out)
            if name == "macagent_task_list":
                out = self.client.task_list(
                    project_id=project_id,
                    conversation_id=conversation_id,
                    status=args.get("status"),
                    limit=int(args.get("limit") or 50),
                    offset=int(args.get("offset") or 0),
                )
                return self._result(status="ok", message="task_list", data=out)
            if name == "macagent_task_inspect":
                out = self.client.task_inspect(args["task_id"])
                return self._result(status="ok", message="task_inspect", data=out)
            if name == "macagent_task_summary":
                out = self.client.task_summary(args["task_id"])
                return self._result(status="ok", message="task_summary", data=out)
            if name == "macagent_task_logs":
                out = self.client.task_logs(args["task_id"], tail_steps=int(args.get("tail_steps") or 50))
                return self._result(status="ok", message="task_logs", data=out)
            if name == "macagent_task_artifacts":
                out = self.client.task_artifacts(args["task_id"])
                return self._result(status="ok", message="task_artifacts", data=out)
            if name == "macagent_task_artifact_preview":
                out = self.client.task_artifact_preview(
                    args["task_id"],
                    args["artifact_name"],
                    format=args.get("format") or "text",
                    tail_lines=int(args.get("tail_lines") or 200),
                )
                return self._result(status="ok", message="task_artifact_preview", data=out)
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
                "serverInfo": {"name": "staragent-mcp", "version": "0.1"},
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
    prog = os.path.basename(sys.argv[0] or "") or "staragent-mcp"
    ap = argparse.ArgumentParser(prog=prog, description="StarAgent MCP server (stdio) (legacy compatible: macagent tools)")
    ap.add_argument("--debug", action="store_true", help="Include raw responses in tool outputs")
    args = ap.parse_args(argv)

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    # Keep stderr quiet by default; enable request-level debugging via env or flag.
    debug = bool(args.debug or os.getenv("STARAGENT_MCP_DEBUG") == "1" or os.getenv("MACAGENT_MCP_DEBUG") == "1")

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
