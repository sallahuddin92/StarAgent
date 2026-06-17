"""
StarAgent Trace Logger — structured JSONL trace output.

Writes per-event trace records to .runtime/traces/<task_id>.jsonl
"""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TRACE_DIR = os.path.join(os.getcwd(), ".runtime", "traces")


@dataclass
class TraceEvent:
    timestamp: float
    task_id: str
    role: str
    event_type: str  # plan | step | result | error | verifier
    tool_name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    output_preview: str = ""
    status: str = ""  # ok | fail | pending

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class TraceLogger:
    """Append-only JSONL trace writer for a single task."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.events: List[TraceEvent] = []
        os.makedirs(TRACE_DIR, exist_ok=True)
        self.path = os.path.join(TRACE_DIR, f"{task_id}.jsonl")

    def _write(self, event: TraceEvent):
        self.events.append(event)
        try:
            with open(self.path, "a") as f:
                f.write(event.to_json() + "\n")
        except Exception as e:
            logger.warning(f"Trace write failed: {e}")

    def log_plan(self, role: str, plan_steps: List[str], status: str = "ok"):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role=role,
            event_type="plan",
            output_preview="\n".join(f"- {s}" for s in plan_steps),
            status=status,
        ))

    def log_step(self, role: str, tool_name: str, args: Dict[str, Any], status: str = "pending"):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role=role,
            event_type="step",
            tool_name=tool_name,
            args=args,
            status=status,
        ))

    def log_result(self, role: str, tool_name: str, output: str, status: str = "ok"):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role=role,
            event_type="result",
            tool_name=tool_name,
            output_preview=output[:500],
            status=status,
        ))

    def log_error(self, role: str, error_msg: str, tool_name: str = ""):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role=role,
            event_type="error",
            tool_name=tool_name,
            output_preview=error_msg[:500],
            status="fail",
        ))

    def log_verifier(self, checks: Dict[str, bool], status: str = "ok"):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role="VERIFIER_AGENT",
            event_type="verifier",
            args=checks,
            output_preview=f"{sum(checks.values())}/{len(checks)} checks passed",
            status=status,
        ))

    def log_skill(self, skill_name: str, domain: str, score: float, reason: str):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role="ORCHESTRATOR",
            event_type="skill_selection",
            args={"skill_name": skill_name, "domain": domain, "score": score, "reason": reason},
            output_preview=f"Selected skill: {skill_name} ({domain})",
            status="ok",
        ))

    def log_final_report(self, report: str, files_read: List[str] = None,
                         files_modified: List[str] = None, status: str = "ok"):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role="ORCHESTRATOR",
            event_type="final_report",
            args={
                "files_read": files_read or [],
                "files_modified": files_modified or [],
            },
            output_preview=report[:1000],
            status=status,
        ))

    def log_model_selected(self, role: str, model_name: str, provider: str = ""):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role=role,
            event_type="model_selected",
            args={"model": model_name, "provider": provider},
            output_preview=f"Selected model: {model_name} ({provider})",
            status="ok",
        ))

    def log_model_switch(self, role: str, from_model: str, to_model: str, reason: str = ""):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role=role,
            event_type="model_switch",
            args={"from": from_model, "to": to_model, "reason": reason},
            output_preview=f"Switched {from_model} -> {to_model}: {reason}",
            status="ok",
        ))

    def log_fallback_used(self, role: str, original_model: str, fallback_model: str, error: str = ""):
        self._write(TraceEvent(
            timestamp=time.time(),
            task_id=self.task_id,
            role=role,
            event_type="fallback_used",
            args={"original": original_model, "fallback": fallback_model, "error": error},
            output_preview=f"Fallback: {original_model} -> {fallback_model}: {error[:200]}",
            status="ok",
        ))

    def get_summary(self) -> Dict[str, Any]:
        """Return a summary dict for dashboard display."""
        steps = [e for e in self.events if e.event_type == "step"]
        results = [e for e in self.events if e.event_type == "result"]
        errors = [e for e in self.events if e.event_type == "error"]
        verifier = [e for e in self.events if e.event_type == "verifier"]
        roles = list(set(e.role for e in self.events))
        files_created = []
        commands_run = []
        for e in self.events:
            if e.tool_name == "write_file" and e.event_type == "result":
                files_created.append(e.args.get("path", e.output_preview[:80]))
            if e.tool_name == "run_command" and e.event_type == "result":
                commands_run.append(e.output_preview[:120])
        return {
            "task_id": self.task_id,
            "trace_file": self.path,
            "total_events": len(self.events),
            "steps": len(steps),
            "results": len(results),
            "errors": len(errors),
            "verifier_checks": len(verifier),
            "roles": roles,
            "files_created": files_created,
            "commands_run": commands_run,
            "final_status": verifier[-1].status if verifier else "unknown",
        }


def load_trace(task_id: str) -> List[Dict[str, Any]]:
    """Load a trace file and return list of event dicts."""
    path = os.path.join(TRACE_DIR, f"{task_id}.jsonl")
    if not os.path.exists(path):
        return []
    events = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def list_traces() -> List[str]:
    """List all available trace IDs."""
    if not os.path.isdir(TRACE_DIR):
        return []
    return [f.replace(".jsonl", "") for f in os.listdir(TRACE_DIR) if f.endswith(".jsonl")]
