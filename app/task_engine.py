from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .database import DatabaseManager
from .memory import MemoryStore
from .planner import Planner
from .executor import Executor
from .workspace_state import WorkspaceTracker
from .approval import ApprovalPolicy
from .intake import classify_input
from .research_mode import ResearchPipeline, ResearchInputs
from .repo_audit import RepoAuditPipeline, RepoAuditInputs
from .issue_triage import IssueTriagePipeline, IssueTriageInputs
from .writing_profile import WritingPipeline, WritingInputs

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _artifact_dir(task_id: str) -> Path:
    return Path(".runtime") / "tasks" / task_id


@dataclass
class TaskRunResult:
    task: Dict[str, Any]
    steps: List[Dict[str, Any]]
    action_required: Optional[Dict[str, Any]] = None  # e.g. {"type":"approval", ...}

    def to_dict(self) -> Dict[str, Any]:
        out = {"task": self.task, "steps": self.steps}
        if self.action_required:
            out["action_required"] = self.action_required
        return out


class TaskEngine:
    """
    Controlled iterative task runner with durable persistence in SQLite.

    Design goals:
    - bounded looping (max steps, max duration, max retries)
    - resumable state (task_runs + task_steps)
    - small-model-friendly: deterministic planning where possible
    """

    def __init__(
        self,
        *,
        db: DatabaseManager,
        store: MemoryStore,
        planner: Planner,
        executor: Executor,
        workspace: WorkspaceTracker,
        approval_policy: ApprovalPolicy,
        research: Optional[ResearchPipeline] = None,
        repo_audit: Optional[RepoAuditPipeline] = None,
        issue_triage: Optional[IssueTriagePipeline] = None,
        writing: Optional[WritingPipeline] = None,
    ):
        self.db = db
        self.store = store
        self.planner = planner
        self.executor = executor
        self.workspace = workspace
        self.approval_policy = approval_policy
        self.research = research
        self.repo_audit = repo_audit
        self.issue_triage = issue_triage
        self.writing = writing

    def _tool_ok(self, tool_call: Dict[str, Any], tool_output: str) -> bool:
        """
        Task-engine verifier for tool calls.

        Important: we must not treat the substring "error" inside normal source code
        (e.g. `logger.error`) as a failure. Only tool-level error sentinels count.
        """
        out = (tool_output or "").strip()
        if not out:
            return False
        if out.lower().startswith("error:"):
            return False

        fn = ((tool_call or {}).get("function") or {}).get("name") or ""
        if fn == "list_files":
            return out.startswith("[") and out.endswith("]")
        if fn == "read_file":
            return True
        if fn == "search_files":
            # "No matches found." is a valid outcome.
            return True
        if fn == "write_file":
            return out.lower().startswith("successfully wrote to ")
        return True

    def create_task(
        self,
        *,
        project_id: str,
        conversation_id: str,
        task_type: str,
        user_goal: str,
        definition_of_done: Optional[str] = None,
        max_steps: int = 25,
        max_retries: int = 2,
        artifacts_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tr = self.db.create_task_run(
            {
                "project_id": project_id,
                "conversation_id": conversation_id,
                "task_type": task_type,
                "user_goal": user_goal,
                "definition_of_done": definition_of_done,
                "status": "pending",
                "current_step_index": 0,
                "max_steps": max_steps,
                "max_retries": max_retries,
                "retry_count": 0,
                "artifacts_json": artifacts_json or {},
            }
        )
        # Ensure artifact dir exists.
        _artifact_dir(tr["task_id"]).mkdir(parents=True, exist_ok=True)

        # Adaptive intake preflight for path-based tasks.
        # This is intentionally lightweight and bounded (stat-only directory scan).
        try:
            aj = tr.get("artifacts_json") or {}
            if "input_intake" not in aj:
                root_path = aj.get("root_path") or aj.get("path")
                if root_path:
                    intake = classify_input(str(root_path)).to_dict()
                    aj = {**aj, "input_intake": intake}
                    self.db.update_task_run(tr["task_id"], {"artifacts_json": aj})
                    # Persist a task-local artifact for operator visibility.
                    (_artifact_dir(tr["task_id"]) / "input_intake.json").write_text(
                        json.dumps(intake, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    tr = self.db.get_task_run(tr["task_id"]) or tr
        except Exception as e:
            logger.warning("intake classification failed: %s", e)

        return tr

    def get_task(self, task_id: str) -> Optional[TaskRunResult]:
        tr = self.db.get_task_run(task_id)
        if not tr:
            return None
        steps = self.db.list_task_steps(task_id)
        return TaskRunResult(task=tr, steps=steps)

    def _ensure_steps_planned(self, tr: Dict[str, Any]) -> None:
        task_id = tr["task_id"]
        existing = self.db.list_task_steps(task_id)
        if existing:
            return

        task_type = tr.get("task_type") or "agent"
        goal = tr.get("user_goal") or ""
        project_id = tr.get("project_id") or "default"
        conversation_id = tr.get("conversation_id") or "default"

        steps_to_create: List[Dict[str, Any]] = []
        if task_type == "research":
            if not self.research:
                raise RuntimeError("Research pipeline not configured")
            inputs = tr.get("artifacts_json") or {}
            intake = (inputs.get("input_intake") or {}) if isinstance(inputs, dict) else {}
            input_type = str(intake.get("input_type") or "") or None
            dominant = intake.get("details", {}).get("dominant_file") if isinstance(intake.get("details"), dict) else None
            dataset_path = None
            if isinstance(dominant, dict) and dominant.get("abs_path"):
                dataset_path = str(dominant.get("abs_path"))
            plan = self.research.plan_steps(
                ResearchInputs(
                    root_path=str(inputs.get("root_path") or "."),
                    files=inputs.get("files"),
                    question=inputs.get("question"),
                    mode=str(inputs.get("mode") or "research"),
                    input_type=input_type,
                    dataset_path=dataset_path,
                )
            )
            for idx, s in enumerate(plan[: int(tr.get("max_steps") or 25)]):
                steps_to_create.append(
                    {
                        "step_index": idx,
                        "step_type": s.get("step_type", "generic"),
                        "instruction": s.get("instruction", ""),
                        "status": "pending",
                    }
                )
        elif task_type == "repo_audit":
            if not self.repo_audit:
                raise RuntimeError("Repo audit pipeline not configured")
            inputs = tr.get("artifacts_json") or {}
            plan = self.repo_audit.plan_steps(
                RepoAuditInputs(
                    root_path=str(inputs.get("root_path") or "."),
                    question=inputs.get("question"),
                )
            )
            for idx, s in enumerate(plan[: int(tr.get("max_steps") or 25)]):
                steps_to_create.append(
                    {
                        "step_index": idx,
                        "step_type": s.get("step_type", "generic"),
                        "instruction": s.get("instruction", ""),
                        "status": "pending",
                    }
                )
        elif task_type == "issue_triage":
            if not self.issue_triage:
                raise RuntimeError("Issue triage pipeline not configured")
            inputs = tr.get("artifacts_json") or {}
            plan = self.issue_triage.plan_steps(
                IssueTriageInputs(
                    root_path=str(inputs.get("root_path") or "."),
                    issue=str(inputs.get("issue") or ""),
                    files=inputs.get("files"),
                    logs=inputs.get("logs"),
                )
            )
            for idx, s in enumerate(plan[: int(tr.get("max_steps") or 25)]):
                steps_to_create.append(
                    {
                        "step_index": idx,
                        "step_type": s.get("step_type", "generic"),
                        "instruction": s.get("instruction", ""),
                        "status": "pending",
                    }
                )
        elif task_type == "writing":
            if not self.writing:
                raise RuntimeError("Writing pipeline not configured")
            inputs = tr.get("artifacts_json") or {}
            plan = self.writing.plan_steps(
                WritingInputs(
                    root_path=str(inputs.get("root_path") or "."),
                    goal=str(inputs.get("goal") or tr.get("user_goal") or ""),
                    files=inputs.get("files"),
                )
            )
            for idx, s in enumerate(plan[: int(tr.get("max_steps") or 25)]):
                steps_to_create.append(
                    {
                        "step_index": idx,
                        "step_type": s.get("step_type", "generic"),
                        "instruction": s.get("instruction", ""),
                        "status": "pending",
                    }
                )
        else:
            memory = self.store.load(conversation_id, project_id)
            plan = self.planner.create_plan(goal, memory, self.workspace)  # type: ignore
            # Planner.create_plan is async in current runtime; but it is deterministic and fast.
            # We store placeholder steps; runtime caller must use the async path to plan.
            # This method is only called from async run(), so we should never land here.
            raise RuntimeError("planner planning must be performed from async run()")

        self.db.create_task_steps(task_id, steps_to_create)

    async def _plan_agent_steps_async(self, tr: Dict[str, Any]) -> None:
        task_id = tr["task_id"]
        if self.db.list_task_steps(task_id):
            return

        goal = tr.get("user_goal") or ""
        project_id = tr.get("project_id") or "default"
        conversation_id = tr.get("conversation_id") or "default"
        memory = self.store.load(conversation_id, project_id)
        plan = await self.planner.create_plan(goal, memory, self.workspace)

        max_steps = int(tr.get("max_steps") or 25)
        plan = list(plan)[:max_steps]
        steps_to_create = []
        for idx, s in enumerate(plan):
            steps_to_create.append({"step_index": idx, "step_type": "agent_step", "instruction": str(s), "status": "pending"})
        self.db.create_task_steps(task_id, steps_to_create)

    def _mark_completed_if_done(self, task_id: str) -> Optional[Dict[str, Any]]:
        steps = self.db.list_task_steps(task_id)
        if steps and all(s.get("status") == "completed" for s in steps):
            tr = self.db.get_task_run(task_id) or {}
            artifacts = tr.get("artifacts_json") or {}

            # Preset hook: release_review exports the primary report to an
            # operator-chosen output_path via approval-gated write_file.
            if artifacts.get("preset") == "release_review" and not artifacts.get("release_review_exported"):
                out_path = artifacts.get("output_path")
                if out_path:
                    report_path = _artifact_dir(task_id) / "audit_report.md"
                    if report_path.exists():
                        try:
                            content = report_path.read_text(encoding="utf-8")
                        except Exception:
                            content = report_path.read_text(errors="ignore")

                        import uuid

                        step_id = str(uuid.uuid4())
                        export_step_index = len(steps)
                        self.db.create_task_steps(
                            task_id,
                            [
                                {
                                    "step_id": step_id,
                                    "step_index": export_step_index,
                                    "step_type": "export_release_review",
                                    "instruction": f"Export audit_report.md to {out_path}",
                                    "status": "paused",
                                }
                            ],
                        )

                        tc = {
                            "id": "call_write_file",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": out_path, "content": content}),
                            },
                        }
                        artifacts["pending_approval"] = {"tool_call": tc, "step_id": step_id, "note": "export_release_review"}
                        self.db.update_task_run(
                            task_id,
                            {
                                "status": "paused",
                                "current_step_index": export_step_index,
                                "final_summary": f"approval_required: write_file {out_path}",
                                "final_verdict": "approval_required",
                                "artifacts_json": artifacts,
                            },
                        )
                        return self.db.get_task_run(task_id)

            # Synthesize a minimal final summary from step outputs.
            summaries = [s.get("output_summary") for s in steps if s.get("output_summary")]
            final = summaries[-1] if summaries else "Task completed."
            self.db.update_task_run(task_id, {"status": "completed", "final_summary": final, "final_verdict": "completed"})
            return self.db.get_task_run(task_id)
        return None

    async def run(
        self,
        task_id: str,
        *,
        max_step_advances: int = 3,
        max_duration_s: float = 20.0,
    ) -> TaskRunResult:
        start = _now()
        tr = self.db.get_task_run(task_id)
        if not tr:
            raise KeyError(f"task not found: {task_id}")

        if tr.get("status") in {"completed", "failed"}:
            steps = self.db.list_task_steps(task_id)
            return TaskRunResult(task=tr, steps=steps)

        self.db.update_task_run(task_id, {"status": "running"})
        tr = self.db.get_task_run(task_id) or tr

        task_type = (tr.get("task_type") or "agent")
        if task_type == "agent":
            await self._plan_agent_steps_async(tr)
        else:
            self._ensure_steps_planned(tr)

        steps = self.db.list_task_steps(task_id)
        if not steps:
            self.db.update_task_run(task_id, {"status": "failed", "final_verdict": "no_steps"})
            tr = self.db.get_task_run(task_id) or tr
            return TaskRunResult(task=tr, steps=[])

        advances = 0
        action_required: Optional[Dict[str, Any]] = None

        # Pending approval state is stored in artifacts_json to keep it task-scoped.
        artifacts = tr.get("artifacts_json") or {}
        pending = artifacts.get("pending_approval")
        if pending:
            self.db.update_task_run(task_id, {"status": "paused"})
            action_required = {"type": "approval", **pending}
            return TaskRunResult(task=self.db.get_task_run(task_id) or tr, steps=steps, action_required=action_required)

        current_idx = int(tr.get("current_step_index") or 0)
        max_steps = int(tr.get("max_steps") or 25)

        while advances < max_step_advances and (_now() - start) < max_duration_s:
            # Find the next actionable step.
            if current_idx >= len(steps) or current_idx >= max_steps:
                break

            step = steps[current_idx]
            if step.get("status") == "completed":
                current_idx += 1
                continue

            # Execute the step.
            advance_index = True
            self.db.update_task_step(step["step_id"], {"status": "running", "attempt_count": int(step.get("attempt_count") or 0) + 1})

            try:
                if (tr.get("task_type") or "agent") == "research":
                    if not self.research:
                        raise RuntimeError("Research pipeline not configured")
                    aj = tr.get("artifacts_json") or {}
                    intake = (aj.get("input_intake") or {}) if isinstance(aj, dict) else {}
                    input_type = str(intake.get("input_type") or "") or None
                    dominant = intake.get("details", {}).get("dominant_file") if isinstance(intake.get("details"), dict) else None
                    dataset_path = None
                    if isinstance(dominant, dict) and dominant.get("abs_path"):
                        dataset_path = str(dominant.get("abs_path"))
                    inputs = ResearchInputs(
                        root_path=str(aj.get("root_path") or "."),
                        files=aj.get("files"),
                        question=aj.get("question"),
                        mode=str(aj.get("mode") or "research"),
                        input_type=input_type,
                        dataset_path=dataset_path,
                    )

                    step_type = step.get("step_type") or "generic"
                    # Expand per-file steps after discovery.
                    if step_type == "expand_file_steps":
                        paths = self.research._paths(task_id)  # intentional internal reuse
                        if not paths["file_index"].exists():
                            raise RuntimeError("file_index.json missing; cannot expand file steps")
                        idx = json.loads(paths["file_index"].read_text(encoding="utf-8"))
                        files = idx.get("files") or []
                        # Insert summarize_file steps after this expand step, up to max_steps.
                        to_insert = []
                        base_index = current_idx + 1
                        for i, f in enumerate(files):
                            if base_index + i >= max_steps - 2:  # leave room for synthesis + final_report
                                break
                            to_insert.append(
                                {
                                    "step_index": base_index + i,
                                    "step_type": f"summarize_file:{f.get('path')}",
                                    "instruction": f"Summarize file: {f.get('path')}",
                                    "status": "pending",
                                }
                            )
                        # Shift existing later steps by len(to_insert).
                        later = [s for s in steps if int(s["step_index"]) > current_idx]
                        shift = len(to_insert)
                        for s in later:
                            self.db.update_task_step(s["step_id"], {"step_index": int(s["step_index"]) + shift})
                        self.db.create_task_steps(task_id, to_insert)
                        self.db.update_task_step(step["step_id"], {"status": "completed", "output_summary": f"Expanded {len(to_insert)} file steps."})
                        advances += 1
                    elif step_type == "expand_dataset_steps":
                        # Expand dataset batch steps after dataset_profile has written plan metadata.
                        paths = self.research._paths(task_id)  # intentional internal reuse
                        if not paths.get("dataset_profile") or not paths["dataset_profile"].exists():
                            raise RuntimeError("dataset_profile.json missing; cannot expand dataset steps")
                        prof = json.loads(paths["dataset_profile"].read_text(encoding="utf-8"))
                        batches = prof.get("planned_batches") or []
                        to_insert = []
                        base_index = current_idx + 1
                        for i, b in enumerate(batches):
                            if base_index + i >= max_steps - 3:  # leave room for synthesis + theme_extraction + final_report
                                break
                            to_insert.append(
                                {
                                    "step_index": base_index + i,
                                    "step_type": f"dataset_batch:{int(b.get('batch_index', i))}",
                                    "instruction": f"Summarize dataset batch {int(b.get('batch_index', i))}",
                                    "status": "pending",
                                }
                            )
                        later = [s for s in steps if int(s["step_index"]) > current_idx]
                        shift = len(to_insert)
                        for s in later:
                            self.db.update_task_step(s["step_id"], {"step_index": int(s["step_index"]) + shift})
                        self.db.create_task_steps(task_id, to_insert)
                        self.db.update_task_step(step["step_id"], {"status": "completed", "output_summary": f"Expanded {len(to_insert)} dataset batch steps."})
                        advances += 1
                    else:
                        out = await self.research.run_step(task_id, step, inputs)
                        ok = bool(out.get("ok"))
                        if not ok:
                            raise RuntimeError(str(out.get("error") or "research_step_failed"))

                        # If the pipeline indicates partial completion, keep the step pending and
                        # return control quickly. This prevents long blocking continues on huge inputs.
                        if out.get("partial") is True:
                            self.db.update_task_step(
                                step["step_id"],
                                {
                                    "status": "pending",
                                    "output_summary": json.dumps(out, ensure_ascii=False)[:4000],
                                    "verifier_result": json.dumps({"ok": True, "partial": True}),
                                },
                            )
                            advance_index = False
                            advances += 1
                        else:
                            self.db.update_task_step(
                                step["step_id"],
                                {
                                    "status": "completed",
                                    "output_summary": json.dumps(out, ensure_ascii=False)[:4000],
                                    "verifier_result": json.dumps({"ok": True}),
                                    "artifact_path": out.get("artifact_path") or out.get("final_report") or out.get("research_brief"),
                                },
                            )
                            advances += 1
                elif (tr.get("task_type") or "agent") == "repo_audit":
                    if not self.repo_audit:
                        raise RuntimeError("Repo audit pipeline not configured")
                    inputs = RepoAuditInputs(
                        root_path=str((tr.get("artifacts_json") or {}).get("root_path") or "."),
                        question=(tr.get("artifacts_json") or {}).get("question"),
                    )
                    out = await self.repo_audit.run_step(task_id, step, inputs)
                    ok = bool(out.get("ok"))
                    if not ok:
                        raise RuntimeError(str(out.get("error") or "repo_audit_step_failed"))
                    self.db.update_task_step(
                        step["step_id"],
                        {
                            "status": "completed",
                            "output_summary": json.dumps(out, ensure_ascii=False)[:4000],
                            "verifier_result": json.dumps({"ok": True}),
                            "artifact_path": out.get("artifact_path") or out.get("audit_report") or out.get("architecture_map") or out.get("entry_points"),
                        },
                    )
                    advances += 1
                elif (tr.get("task_type") or "agent") == "issue_triage":
                    if not self.issue_triage:
                        raise RuntimeError("Issue triage pipeline not configured")
                    aj = tr.get("artifacts_json") or {}
                    inputs = IssueTriageInputs(
                        root_path=str(aj.get("root_path") or "."),
                        issue=str(aj.get("issue") or tr.get("user_goal") or ""),
                        files=aj.get("files"),
                        logs=aj.get("logs"),
                    )
                    out = await self.issue_triage.run_step(task_id, step, inputs)
                    ok = bool(out.get("ok"))
                    if not ok:
                        raise RuntimeError(str(out.get("error") or "issue_triage_step_failed"))
                    self.db.update_task_step(
                        step["step_id"],
                        {
                            "status": "completed",
                            "output_summary": json.dumps(out, ensure_ascii=False)[:4000],
                            "verifier_result": json.dumps({"ok": True}),
                            "artifact_path": out.get("artifact_path"),
                        },
                    )
                    advances += 1
                elif (tr.get("task_type") or "agent") == "writing":
                    if not self.writing:
                        raise RuntimeError("Writing pipeline not configured")
                    aj = tr.get("artifacts_json") or {}
                    inputs = WritingInputs(
                        root_path=str(aj.get("root_path") or "."),
                        goal=str(aj.get("goal") or tr.get("user_goal") or ""),
                        files=aj.get("files"),
                    )
                    out = await self.writing.run_step(task_id, step, inputs)
                    ok = bool(out.get("ok"))
                    if not ok:
                        raise RuntimeError(str(out.get("error") or "writing_step_failed"))
                    self.db.update_task_step(
                        step["step_id"],
                        {
                            "status": "completed",
                            "output_summary": json.dumps(out, ensure_ascii=False)[:4000],
                            "verifier_result": json.dumps({"ok": True}),
                            "artifact_path": out.get("artifact_path"),
                        },
                    )
                    advances += 1
                else:
                    exec_result = await self.executor.execute_step(step.get("instruction") or "", self.workspace)
                    tool_calls = exec_result.get("tool_calls") or []
                    content = exec_result.get("content")
                    if tool_calls:
                        # For now we allow at most one tool call per step (small-model control).
                        tc = tool_calls[0]
                        if self.approval_policy.is_approval_required(tc):
                            # Pause and require explicit approval via task endpoint.
                            artifacts = tr.get("artifacts_json") or {}
                            artifacts["pending_approval"] = {"tool_call": tc, "step_id": step["step_id"], "note": "approval_required"}
                            self.db.update_task_run(task_id, {"status": "paused", "artifacts_json": artifacts})
                            self.db.update_task_step(step["step_id"], {"status": "paused", "output_summary": "approval_required"})
                            action_required = {"type": "approval", "tool_call": tc, "task_id": task_id, "step_id": step["step_id"]}
                            break

                        tool_result = await self.executor.tool_executor.execute_tool_call(tc)
                        ok = self._tool_ok(tc, tool_result.get("content") or "")
                        self.db.update_task_step(
                            step["step_id"],
                            {
                                "status": "completed" if ok else "failed",
                                "output_summary": (tool_result.get("content") or "")[:4000],
                                "verifier_result": json.dumps({"ok": ok}),
                            },
                        )
                        if not ok:
                            raise RuntimeError("tool_step_failed")
                    else:
                        msg = str(content or "").strip()
                        ok = bool(msg) and "error" not in msg.lower()
                        self.db.update_task_step(
                            step["step_id"],
                            {
                                "status": "completed" if ok else "failed",
                                "output_summary": msg[:4000],
                                "verifier_result": json.dumps({"ok": ok}),
                            },
                        )
                        if not ok:
                            raise RuntimeError("step_failed")
                    advances += 1
            except Exception as e:
                # Retry logic: keep bounded and durable.
                step_after = [s for s in self.db.list_task_steps(task_id) if s.get("step_id") == step["step_id"]]
                attempt = int(step_after[0].get("attempt_count") if step_after else (step.get("attempt_count") or 0))
                max_retries = int(tr.get("max_retries") or 2)

                if attempt <= max_retries:
                    # Mark pending again for a later retry.
                    self.db.update_task_step(step["step_id"], {"status": "pending", "output_summary": f"error: {e}"[:4000], "verifier_result": json.dumps({"ok": False, "error": str(e)[:200]})})
                    self.db.update_task_run(task_id, {"retry_count": int(tr.get("retry_count") or 0) + 1})
                    advance_index = False
                    # Count the failed attempt against the per-call budget to avoid tight loops.
                    advances += 1
                else:
                    self.db.update_task_step(step["step_id"], {"status": "failed", "output_summary": f"error: {e}"[:4000], "verifier_result": json.dumps({"ok": False, "error": str(e)[:200]})})
                    self.db.update_task_run(task_id, {"status": "failed", "final_verdict": "failed_step"})
                    break

            # Refresh state and move forward.
            steps = self.db.list_task_steps(task_id)
            if advance_index:
                current_idx += 1
                self.db.update_task_run(task_id, {"current_step_index": current_idx})
            else:
                # Stay on the same step_index for the next continue call.
                self.db.update_task_run(task_id, {"current_step_index": current_idx})
            tr = self.db.get_task_run(task_id) or tr
            if not advance_index:
                # Return control to the operator/runtime rather than retrying indefinitely.
                break

        # Update completion status.
        done = self._mark_completed_if_done(task_id)
        tr = done or (self.db.get_task_run(task_id) or tr)
        steps = self.db.list_task_steps(task_id)

        # If completion logic queued a task-scoped approval, surface it.
        artifacts = tr.get("artifacts_json") or {}
        pending = artifacts.get("pending_approval")
        if pending and not action_required:
            action_required = {"type": "approval", **pending}

        if tr.get("status") == "running" and any(s.get("status") != "completed" for s in steps):
            # If we stopped due to budgets but there is more work, mark partial.
            self.db.update_task_run(task_id, {"status": "partial"})
            tr = self.db.get_task_run(task_id) or tr

        return TaskRunResult(task=tr, steps=steps, action_required=action_required)

    async def approve(self, task_id: str) -> TaskRunResult:
        """Approve and execute the pending tool call for this task run."""
        tr = self.db.get_task_run(task_id)
        if not tr:
            raise KeyError(f"task not found: {task_id}")
        artifacts = tr.get("artifacts_json") or {}
        pending = artifacts.get("pending_approval")
        if not pending or not pending.get("tool_call") or not pending.get("step_id"):
            return TaskRunResult(task=tr, steps=self.db.list_task_steps(task_id))

        tc = pending["tool_call"]
        step_id = pending["step_id"]
        tool_result = await self.executor.tool_executor.execute_tool_call(tc)
        ok = self._tool_ok(tc, tool_result.get("content") or "")
        self.db.update_task_step(step_id, {"status": "completed" if ok else "failed", "output_summary": (tool_result.get("content") or "")[:4000], "verifier_result": json.dumps({"ok": ok})})
        if ok and pending.get("note") == "export_release_review":
            artifacts["release_review_exported"] = True
        artifacts.pop("pending_approval", None)
        self.db.update_task_run(task_id, {"status": "running", "artifacts_json": artifacts})
        return await self.run(task_id, max_step_advances=2, max_duration_s=20.0)

    async def reject(self, task_id: str, *, reason: str = "rejected") -> TaskRunResult:
        tr = self.db.get_task_run(task_id)
        if not tr:
            raise KeyError(f"task not found: {task_id}")
        artifacts = tr.get("artifacts_json") or {}
        pending = artifacts.get("pending_approval")
        if pending and pending.get("step_id"):
            self.db.update_task_step(pending["step_id"], {"status": "failed", "output_summary": f"approval_rejected: {reason}"})
        artifacts.pop("pending_approval", None)
        self.db.update_task_run(task_id, {"status": "failed", "final_verdict": "approval_rejected", "artifacts_json": artifacts})
        return TaskRunResult(task=self.db.get_task_run(task_id) or tr, steps=self.db.list_task_steps(task_id))
