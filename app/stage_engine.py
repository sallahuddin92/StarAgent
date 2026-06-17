import os
import json
import logging
import time
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from .llm_client import LLMClient
from .executor import Executor
from .database import DatabaseManager
from .context_loader import context_loader
from .tool_runtime import verify_tool_permission
from .skill_packs import build_pack_injection
from .checkpoint import save_stage_checkpoint
from .workflow_trace import WorkflowTraceLogger

logger = logging.getLogger(__name__)

class StageEngine:
    """
    Modular execution runtime for a single workflow stage.
    """
    def __init__(self, llm: LLMClient, executor: Executor, db: DatabaseManager):
        self.llm = llm
        self.executor = executor
        self.db = db

    async def execute_stage(
        self,
        task_id: str,
        workflow_name: str,
        stage_config: Dict[str, Any],
        stage_index: int,
        variables: Dict[str, Any],
        trace_logger: WorkflowTraceLogger,
        user_goal: str,
        workflow_dir: Path,
        progress_queue: Optional[asyncio.Queue] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Runs the stage. Returns (status, updated_variables).
        Status can be "completed", "paused" (requires approval), or "failed".
        """
        stage_name = stage_config["name"]
        purpose = stage_config.get("purpose", "")
        allowed_tools = stage_config.get("allowed_tools")
        blocked_tools = stage_config.get("blocked_tools") or []
        required_outputs = stage_config.get("required_outputs") or []
        approval_required = stage_config.get("approval_required", False)

        from .workflow_engine import get_workflow_runtime_dir, append_tool_event
        wf_runtime_dir = get_workflow_runtime_dir(task_id)

        # 1. Resolve Model via Capability Router
        from .workflow_packs import get_workflow_pack
        pack = get_workflow_pack(workflow_name)
        capabilities = stage_config.get("capabilities") or pack.get("capabilities") or ["local_fast"]
        
        from .model_router import resolve_capabilities
        privacy_mode = variables.get("privacy_mode", False)
        latency_pref = variables.get("latency_preference", "low")
        model_id = resolve_capabilities(capabilities, privacy_mode=privacy_mode, latency_preference=latency_pref)
        
        trace_logger.log_model_routing(stage_name, model_id)
        if progress_queue:
            await progress_queue.put(f"[STAGE_ENGINE] Starting Stage: {stage_name}\n")
            await progress_queue.put(f"[MODEL_ROUTER] Capabilities: {capabilities} -> Model: {model_id}\n")

        # Save model selection
        wf_runtime_dir.mkdir(parents=True, exist_ok=True)
        model_sel_file = wf_runtime_dir / "model_selection.json"
        model_sel = {}
        if model_sel_file.exists():
            try:
                model_sel = json.loads(model_sel_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        model_sel[stage_name] = {
            "requested_capabilities": capabilities,
            "model_id": model_id
        }
        model_sel_file.write_text(json.dumps(model_sel, indent=2), encoding="utf-8")

        # 2. Build Layered Context
        docs_context = variables.get("docs_context", "")
        
        context = context_loader.load_layered_context(
            workflow_dir=workflow_dir,
            stage_name=stage_name,
            stage_purpose=purpose,
            project_id=variables.get("project_id", "default"),
            user_goal=user_goal,
            docs_context=docs_context,
            model_id=model_id
        )

        from .model_registry import is_compact_prompts_enabled
        is_compact = is_compact_prompts_enabled(model_id)

        # 3. Load Skill Pack Guidance
        skill_guidance = "" if is_compact else build_pack_injection(workflow_name)
        if skill_guidance:
            context = f"{context}\n\n{skill_guidance}"
            if progress_queue:
                await progress_queue.put(f"[SKILL_PACKS] Loaded skill pack for workflow '{workflow_name}'.\n")

        # Injected variables and exit instruction
        if is_compact:
            # Generate compact signatures for only the allowed tools
            tool_signatures = []
            if allowed_tools and hasattr(self.executor.tool_executor, "registry"):
                reg = self.executor.tool_executor.registry
                for tname in allowed_tools:
                    if tname in reg.tools:
                        defn = reg.tools[tname].get("definition", {})
                        func = defn.get("function", {})
                        desc = func.get("description", "")
                        params = func.get("parameters", {})
                        properties = params.get("properties", {})
                        required = params.get("required", [])
                        
                        arg_parts = []
                        for prop_name, prop_info in properties.items():
                            prop_type = prop_info.get("type", "any")
                            if prop_name in required:
                                arg_parts.append(f"{prop_name}: {prop_type}")
                            else:
                                arg_parts.append(f"{prop_name}?: {prop_type}")
                        sig = f"{tname}({', '.join(arg_parts)})"
                        tool_signatures.append(f" - {sig}: {desc}")
            
            allowed_tools_str = "\n".join(tool_signatures) if tool_signatures else (', '.join(allowed_tools) if allowed_tools else 'all')
            context = (
                f"{context}\n\n"
                f"=== STAGE INSTRUCTIONS ===\n"
                f"Objective: {purpose}\n"
                f"Required outputs: {', '.join(required_outputs) if required_outputs else 'none'}\n"
                f"Enforced Allowed Tools:\n{allowed_tools_str}\n"
                f"Instruction: Output ONLY JSON tool call or final answer."
            )
        else:
            context = (
                f"{context}\n\n"
                f"=== STAGE INSTRUCTIONS ===\n"
                f"You are running the '{stage_name}' stage of the '{workflow_name}' workflow.\n"
                f"Your objective: {purpose}\n"
                f"Required outputs: {', '.join(required_outputs) if required_outputs else 'none'}\n"
                f"State variables: {json.dumps(variables)}\n"
                f"Enforced Allowed Tools: {', '.join(allowed_tools) if allowed_tools else 'all'}\n"
                f"Enforced Blocked Tools: {', '.join(blocked_tools) if blocked_tools else 'none'}\n"
                f"When you have completed your objective and produced required outputs, output a final report summarizing your findings."
            )

        # Save context snapshot
        wf_runtime_dir.mkdir(parents=True, exist_ok=True)
        ctx_snap_file = wf_runtime_dir / "context_snapshot.json"
        ctx_snap = {}
        if ctx_snap_file.exists():
            try:
                ctx_snap = json.loads(ctx_snap_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        ctx_snap[stage_name] = context
        ctx_snap_file.write_text(json.dumps(ctx_snap, indent=2), encoding="utf-8")

        # 4. Human Approval Gate BEFORE running (if configured)
        has_human_gate = any(g.get("type") == "human_approval" for g in stage_config.get("gates", []))
        from .approval import ApprovalPolicy
        policy = ApprovalPolicy()
        if not policy.auto_approve and (approval_required or has_human_gate) and not variables.get(f"approved_{stage_name}"):
            logger.info(f"Stage '{stage_name}' requires human approval before starting.")
            trace_logger.log_stage_start(stage_name, stage_index, model_id, allowed_tools or [], blocked_tools)
            if progress_queue:
                await progress_queue.put(f"[STAGE_ENGINE] Stage '{stage_name}' requires human approval before starting.\n")
            return "paused", variables

        trace_logger.log_stage_start(stage_name, stage_index, model_id, allowed_tools or [], blocked_tools)

        # 5. Iterative Step Execution Loop (simulate planning and action)
        max_steps = 5
        if stage_name == "inspect":
            is_repo_task = False
            goal_lower = user_goal.lower()
            if any(w in goal_lower for w in ["repo", "repository", "codebase", "git", "project", "dir", "directory", "folder", "/"]):
                is_repo_task = True
            max_steps = 2 if is_repo_task else 1

        step_idx = 0
        stage_status = "completed"
        files_produced = []

        messages = [
            {"role": "system", "content": context},
            {"role": "user", "content": f"Execute stage: {stage_name}. Objective: {purpose}."}
        ]

        while step_idx < max_steps:
            logger.info(f"Running step {step_idx + 1}/{max_steps} for stage '{stage_name}'")
            
            # Call model
            response_text = await self.llm.text(messages, model=model_id, temperature=0.1)
            logger.info(f"Model response for stage '{stage_name}' step {step_idx + 1}:\n{response_text}")
            messages.append({"role": "assistant", "content": response_text})
            
            # Use Executor to inspect/detect tool calls
            step_result = await self.executor.execute_step(
                response_text, 
                self.executor.reflection_layer.llm,
                fuzzy_fallbacks=False
            )
            
            tool_calls = step_result.get("tool_calls")
            if not tool_calls:
                logger.info(f"Stage '{stage_name}' execution step finished with reasoning content.")
                break
                
            # Execute tool calls with sandbox permission checks
            for tc in tool_calls:
                func_info = tc.get("function", {})
                tool_name = func_info.get("name")
                
                # Check tool permissions, passing the registry
                is_allowed = verify_tool_permission(stage_config, tool_name, registry=self.executor.tool_executor.registry)
                trace_logger.log_tool_sandbox_check(tool_name, is_allowed, reason="Stage profiles enforcement")
                
                if progress_queue:
                    await progress_queue.put(f"[STAGE_ENGINE] Running Tool: {tool_name} (allowed={is_allowed})\n")
                
                if not is_allowed:
                    err_msg = f"Error: Tool '{tool_name}' is blocked/unauthorized in stage '{stage_name}'."
                    messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": err_msg})
                    logger.warning(err_msg)
                    continue
                
                # Run tool
                tool_res = await self.executor.tool_executor.execute_tool_call(tc)
                content = tool_res.get("content", "")
                if is_compact and len(content) > 4000:
                    content = content[:1000] + "\n... [TRUNCATED] ...\n" + content[-3000:]
                messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": content})
                
                # Log event to tool_events.jsonl
                append_tool_event(task_id, {
                    "stage_name": stage_name,
                    "tool_name": tool_name,
                    "arguments": func_info.get("arguments"),
                    "output": content[:2000]
                })

                # Track files produced
                if tool_name == "write_file":
                    try:
                        args = json.loads(func_info.get("arguments", "{}"))
                        if args.get("path"):
                            files_produced.append(args["path"])
                    except Exception:
                        pass
                        
            step_idx += 1

        # 6. Run verification via GateEngine
        from .checkpoint import list_task_checkpoints
        task_files = []
        try:
            for cp in list_task_checkpoints(task_id):
                task_files.extend(cp.get("files_produced") or [])
        except Exception:
            pass
        combined_files = list(set(files_produced + task_files))

        verifier_ok, verifier_msg, gate_res = self.run_verification_v050(
            stage_config=stage_config,
            task_id=task_id,
            files_produced=combined_files,
            stage_output=messages[-1]["content"] if messages else "",
            citations=[], # Can extract citations or verify
            variables=variables
        )
        
        # Save gate results
        wf_runtime_dir.mkdir(parents=True, exist_ok=True)
        gate_res_file = wf_runtime_dir / "gate_results.json"
        gate_results = {}
        if gate_res_file.exists():
            try:
                gate_results = json.loads(gate_res_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        gate_results[stage_name] = gate_res
        gate_res_file.write_text(json.dumps(gate_results, indent=2), encoding="utf-8")

        trace_logger.log_stage_verifier(stage_config.get("verifier", "always_pass"), verifier_ok, verifier_msg)
        
        if progress_queue:
            icon = "✅" if verifier_ok else "❌"
            await progress_queue.put(f"[STAGE_VERIFIER] {icon} Verifier Result ({stage_config.get('verifier')}): {verifier_msg}\n")
        
        if not verifier_ok:
            stage_status = "failed"
            logger.error(f"Stage '{stage_name}' verification failed: {verifier_msg}")
        
        # 7. Generate Stage Report
        report = (
            f"# Stage Report: {stage_name}\n\n"
            f"**Status:** {stage_status.upper()}\n"
            f"**Objective:** {purpose}\n"
            f"**Verifier:** {stage_config.get('verifier')} ({'Passed' if verifier_ok else 'Failed'})\n"
            f"**Verifier Message:** {verifier_msg}\n\n"
            f"## Produced Files\n"
        )
        if files_produced:
            for f in files_produced:
                report += f"- `{f}`\n"
        else:
            report += "*No files produced.*\n"

        # 8. Save Checkpoint
        stage_trace = {
            "stage_name": stage_name,
            "model": model_id,
            "steps_executed": step_idx,
            "verifier_result": {"ok": verifier_ok, "message": verifier_msg},
            "history": messages
        }
        
        cp_dir = save_stage_checkpoint(
            task_id=task_id,
            workflow_name=workflow_name,
            stage_name=stage_name,
            stage_index=stage_index,
            status=stage_status,
            variables=variables,
            files_produced=files_produced,
            trace_data=stage_trace,
            report_content=report
        )
        trace_logger.log_checkpoint_saved(stage_name, str(cp_dir))
        if progress_queue:
            await progress_queue.put(f"[CHECKPOINT_MANAGER] Checkpoint Saved under checkpoints/{stage_index:02d}_{stage_name}\n")

        return stage_status, variables

    def run_verification_v050(
        self, 
        stage_config: Dict[str, Any], 
        task_id: str, 
        files_produced: List[str],
        stage_output: str,
        citations: List[Any],
        variables: Dict[str, Any]
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Evaluate declarative gates using GateEngine.
        Returns: (success, message, gate_results_dict)
        """
        from .gate_engine import GateEngine
        engine = GateEngine()
        
        # Build gates list
        gates = stage_config.get("gates")
        if not gates:
            # Fallback/translate verifier to gates
            verifier = stage_config.get("verifier", "always_pass")
            gates = []
            if verifier == "file_exists":
                req = stage_config.get("required_outputs") or []
                gates.append({"type": "file_exists", "arguments": {"paths": req}})
            elif verifier == "pytest":
                gates.append({"type": "test_pass", "arguments": {}})
            elif verifier == "always_pass":
                pass
                
        # If no gates, it passes
        if not gates:
            return True, "No verification gates specified.", {"success": True, "status": "pass", "results": []}
            
        # Build evaluation context
        context = {
            "workspace_root": os.getcwd(),
            "files_produced": files_produced,
            "modified_files": files_produced, 
            "stage_output": stage_output,
            "citations": citations,
            "stage_name": stage_config.get("name", ""),
            "variables": variables,
            "run_id": task_id
        }
        
        res = engine.evaluate_gates(gates, context)
        
        # Format message summarizing results
        messages = []
        for r in res["results"]:
            icon = "✅" if r["status"] == "pass" else ("⚠️" if r["status"] == "warning" else "❌")
            messages.append(f"{icon} {r['type']}: {r['message']}")
            
        summary = "\n".join(messages)
        return res["success"], summary, res
