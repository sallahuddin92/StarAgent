import os
import json
import time
import logging
from typing import List, Dict, Any, Optional
from .trace_logger import TraceLogger, TraceEvent, TRACE_DIR

logger = logging.getLogger(__name__)

class WorkflowTraceLogger(TraceLogger):
    """
    Extends TraceLogger with rich workflow-specific structural events:
    Workflow -> Stage -> Model -> Tools -> Verifier -> Checkpoint.
    """
    def log_workflow_start(self, workflow_name: str, goal: str):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role="WORKFLOW_ENGINE",
            event_type="workflow_start",
            tool_name=workflow_name,
            args={"goal": goal},
            status="ok"
        ))

    def log_stage_start(
        self,
        stage_name: str,
        stage_index: int,
        model: str,
        allowed_tools: List[str],
        blocked_tools: List[str]
    ):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role="STAGE_ENGINE",
            event_type="stage_start",
            tool_name=stage_name,
            args={
                "stage_index": stage_index,
                "model": model,
                "allowed_tools": allowed_tools,
                "blocked_tools": blocked_tools
            },
            status="ok"
        ))

    def log_model_routing(self, stage_name: str, selected_model: str):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role="MODEL_ROUTER",
            event_type="model_routing",
            tool_name=stage_name,
            args={"selected_model": selected_model},
            status="ok"
        ))

    def log_tool_sandbox_check(self, tool_name: str, allowed: bool, reason: str = ""):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role="TOOL_RUNTIME",
            event_type="sandbox_check",
            tool_name=tool_name,
            args={"allowed": allowed, "reason": reason},
            status="ok" if allowed else "blocked"
        ))

    def log_stage_verifier(self, verifier_name: str, ok: bool, output: str):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role="STAGE_VERIFIER",
            event_type="stage_verifier",
            tool_name=verifier_name,
            args={"ok": ok},
            output_preview=output[:1000],
            status="ok" if ok else "fail"
        ))

    def log_checkpoint_saved(self, stage_name: str, cp_dir: str):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role="CHECKPOINT_MANAGER",
            event_type="checkpoint_saved",
            tool_name=stage_name,
            args={"checkpoint_directory": cp_dir},
            status="ok"
        ))

    def log_workflow_end(self, status: str, summary: str):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role="WORKFLOW_ENGINE",
            event_type="workflow_end",
            output_preview=summary[:1000],
            status=status
        ))

def render_workflow_trace_tree(events: List[Dict[str, Any]]) -> str:
    """Renders a beautiful hierarchical ASCII tree representation of a workflow trace."""
    lines = []
    for e in events:
        etype = e.get("event_type")
        role = e.get("role")
        ts = time.strftime('%H:%M:%S', time.localtime(e.get("timestamp", time.time())))

        if etype == "workflow_start":
            lines.append(f"[{ts}] 🌐 Workflow: {e.get('tool_name')} Started")
            lines.append(f" └─ Goal: {e.get('args', {}).get('goal')}")
        elif etype == "stage_start":
            lines.append(f"[{ts}]   ├── ⚙️ Stage: {e.get('tool_name')} (index={e.get('args', {}).get('stage_index')})")
            lines.append(f"   │    ├── Model: {e.get('args', {}).get('model')}")
            lines.append(f"   │    ├── Allowed Tools: {', '.join(e.get('args', {}).get('allowed_tools') or [])}")
            lines.append(f"   │    └── Blocked Tools: {', '.join(e.get('args', {}).get('blocked_tools') or [])}")
        elif etype == "sandbox_check":
            status = "PASS" if e.get("status") == "ok" else "BLOCKED"
            lines.append(f"[{ts}]   │    ├── 🛡️ Tool Check: {e.get('tool_name')} -> {status} ({e.get('args', {}).get('reason')})")
        elif etype == "step":
            lines.append(f"[{ts}]   │    ├── 🛠️ Tool Call: {e.get('tool_name')} with args {json.dumps(e.get('args'))}")
        elif etype == "result":
            lines.append(f"[{ts}]   │    ├── 📥 Tool Result: {e.get('tool_name')} -> {e.get('status')}")
            lines.append(f"   │    │    └── Output: {e.get('output_preview')}")
        elif etype == "stage_verifier":
            icon = "✅" if e.get("status") == "ok" else "❌"
            lines.append(f"[{ts}]   │    ├── {icon} Verifier: {e.get('tool_name')} -> {e.get('status').upper()}")
            lines.append(f"   │    │    └── Verdict: {e.get('output_preview')}")
        elif etype == "checkpoint_saved":
            lines.append(f"[{ts}]   │    └── 💾 Checkpoint Created: {e.get('args', {}).get('checkpoint_directory')}")
        elif etype == "workflow_end":
            lines.append(f"[{ts}] 🏁 Workflow: End -> {e.get('status').upper()}")
            lines.append(f" └─ Summary: {e.get('output_preview')}")
            
    return "\n".join(lines)
