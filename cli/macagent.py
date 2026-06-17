from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from client.macagent_client import MacAgentClient, MacAgentConfig


def _state_path() -> Path:
    # Persist local operator context so approve/continue works without re-typing ids.
    # Prefer StarAgent directory, but transparently read legacy ~/.macagent if it exists.
    env_dir = os.getenv("STARAGENT_CLI_STATE_DIR") or os.getenv("MACAGENT_CLI_STATE_DIR")
    if env_dir:
        d = env_dir
    else:
        star = Path.home() / ".staragent"
        legacy = Path.home() / ".macagent"
        if (star / "context.json").exists() or not (legacy / "context.json").exists():
            d = str(star)
        else:
            d = str(legacy)
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p / "context.json"


def _load_state() -> Dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    path = _state_path()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _resolve_context(args: argparse.Namespace) -> Dict[str, str]:
    state = _load_state()
    project_id = (
        args.project_id
        or state.get("project_id")
        or os.getenv("STARAGENT_DEFAULT_PROJECT")
        or os.getenv("MACAGENT_DEFAULT_PROJECT")
        or "default"
    )
    conversation_id = args.conversation_id or state.get("conversation_id")
    if not conversation_id:
        prefix = os.getenv("STARAGENT_DEFAULT_CONVERSATION_PREFIX") or os.getenv("MACAGENT_DEFAULT_CONVERSATION_PREFIX") or "cli"
        conversation_id = f"{prefix}-{int(time.time())}"
    # Update state.
    state["project_id"] = project_id
    state["conversation_id"] = conversation_id
    _save_state(state)
    return {"project_id": project_id, "conversation_id": conversation_id}


def _remember_last_task(task_id: str) -> None:
    state = _load_state()
    state["last_task_id"] = task_id
    _save_state(state)


def _resolve_task_id(args: argparse.Namespace) -> Optional[str]:
    # Prefer explicit, else stored context.
    if getattr(args, "task_id", None):
        return args.task_id
    state = _load_state()
    return state.get("last_task_id")


def _print_result(result, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.message)

def _fmt_dt(s: Any) -> str:
    if not s:
        return ""
    return str(s).replace("T", " ").replace("Z", "")

def _fmt_age_s(age_s: Any) -> str:
    try:
        if age_s is None:
            return ""
        s = float(age_s)
    except Exception:
        return ""
    if s < 90:
        return f"{int(s)}s"
    if s < 90 * 60:
        return f"{int(s // 60)}m"
    if s < 72 * 3600:
        return f"{int(s // 3600)}h"
    return f"{int(s // 86400)}d"


def _primary_name_for_type(task_type: Any) -> str:
    tt = str(task_type or "").strip().lower()
    if tt == "research":
        return "final_report.md"
    if tt == "repo_audit":
        return "audit_report.md"
    if tt == "issue_triage":
        return "next_actions.md"
    if tt == "writing":
        return "final_output.md"
    return ""

def _print_dataset_meta(dm: Any) -> None:
    if not isinstance(dm, dict) or not dm:
        return
    print("dataset:")
    lines = dm.get("display_lines")
    if isinstance(lines, list) and lines:
        for ln in lines[:20]:
            if ln is None:
                continue
            print("  " + str(ln))
        return
    if dm.get("dataset_path"):
        print(f"  path:      {dm.get('dataset_path')}")
    if dm.get("json_kind"):
        print(f"  kind:      {dm.get('json_kind')}")
    if dm.get("size_bytes") is not None:
        print(f"  size:      {dm.get('size_bytes')} bytes")
    if dm.get("sample_records_count") is not None:
        print(f"  sampled:   {dm.get('sample_records_count')}")
    if dm.get("planned_batches") is not None:
        print(f"  batches:   {dm.get('planned_batches')}")
    if dm.get("duplicate_ratio") is not None:
        try:
            print(f"  dup_ratio: {float(dm.get('duplicate_ratio')):.3f}")
        except Exception:
            print(f"  dup_ratio: {dm.get('duplicate_ratio')}")
    if dm.get("coverage_ratio") is not None:
        try:
            print(f"  coverage:  {float(dm.get('coverage_ratio')):.3f}")
        except Exception:
            print(f"  coverage:  {dm.get('coverage_ratio')}")
    if dm.get("confidence"):
        print(f"  conf:      {dm.get('confidence')}")
    themes = dm.get("top_themes") or []
    if isinstance(themes, list) and themes:
        parts = []
        for t in themes[:5]:
            if not isinstance(t, dict):
                continue
            nm = str(t.get("name") or "").strip()
            if not nm:
                continue
            pct = t.get("estimated_percentage")
            if isinstance(pct, (int, float)) and int(pct) > 0:
                parts.append(f"{nm} ({int(pct)}%)")
            else:
                parts.append(nm)
        if parts:
            print("  themes:    " + "; ".join(parts))


def _configured_remote_models() -> Dict[str, str]:
    out: Dict[str, str] = {}
    if os.getenv("OPENAI_API_KEY"):
        out["openai"] = os.getenv("OPENAI_MODEL", "gpt-4o")
    if os.getenv("ANTHROPIC_API_KEY"):
        out["anthropic"] = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20240620")
    if os.getenv("GEMINI_API_KEY"):
        out["gemini"] = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")
    longcat_base = (os.getenv("LONGCAT_BASE_URL") or "").rstrip("/")
    if os.getenv("LONGCAT_API_KEY") and os.getenv("LONGCAT_MODEL") and longcat_base == "https://api.longcat.chat/openai":
        out["longcat"] = os.getenv("LONGCAT_MODEL", "LongCat-Flash-Thinking-2601")
    if os.getenv("OPENAI_COMPATIBLE_API_KEY"):
        out["openai_compatible"] = os.getenv("OPENAI_COMPATIBLE_MODEL", "")
    return out


def _longcat_configured() -> bool:
    longcat_base = (os.getenv("LONGCAT_BASE_URL") or "").rstrip("/")
    return bool(
        os.getenv("LONGCAT_API_KEY")
        and os.getenv("LONGCAT_MODEL")
        and longcat_base == "https://api.longcat.chat/openai"
    )

def _acquire_lock(lock_path: str):
    try:
        import fcntl
        f = open(lock_path, "w")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except (IOError, OSError, ImportError):
        return None


def _release_lock(lock_file_obj, lock_path: str):
    if lock_file_obj:
        try:
            lock_file_obj.close()
        except Exception:
            pass
        try:
            os.unlink(lock_path)
        except Exception:
            pass


def _run_doctor(client: MacAgentClient, ctx: Dict[str, str], *, as_json: bool = False) -> int:
    checks: list[Dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    health_payload: Dict[str, Any] = {}
    try:
        health_payload = client.health(timeout=5.0)
        ok = bool(health_payload.get("ok", True))
        add_check("server_health", ok, f"service={health_payload.get('service')} version={health_payload.get('version')}")
    except Exception as e:
        add_check("server_health", False, f"{type(e).__name__}: {e}")

    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from app.model_profiles import get_active_profile
        from app.model_registry import registry

        from app.model_registry import get_effective_model_config
        effective_cfg = get_effective_model_config()
        model_name = effective_cfg["model"]
        provider = effective_cfg["provider"]
        profile = get_active_profile(provider, model_name)
        add_check(
            "active_model_profile",
            True,
            f"profile={profile.name} provider={provider} model={model_name}",
        )

        if provider == "ollama":
            try:
                local = registry.list_local_ollama_models(refresh=True)
                add_check("ollama_reachable", True, f"models_discovered={len(local)}")
            except Exception as e:
                add_check("ollama_reachable", False, f"{type(e).__name__}: {e}")
                local = []

            default_model = model_name
            if default_model:
                installed = registry.is_local_ollama_model_installed(default_model, refresh=False)
                add_check(
                    "default_model_installed",
                    installed,
                    default_model if installed else f"missing: {default_model}",
                )

            missing_assigned = []
            for role, mid in (registry.agent_routing or {}).items():
                if registry.infer_provider(str(mid)) != "ollama":
                    continue
                if not registry.is_local_ollama_model_installed(str(mid), refresh=False):
                    missing_assigned.append(f"{role}={mid}")
            if missing_assigned:
                add_check("agent_models_installed", False, "missing: " + ", ".join(missing_assigned))
            else:
                add_check("agent_models_installed", True, "ok")

            configured = [default_model] if default_model else []
            configured.extend([str(v) for v in (registry.agent_routing or {}).values()])
            warn_missing = []
            for mid in configured:
                if not mid or registry.infer_provider(mid) != "ollama":
                    continue
                if not registry.is_local_ollama_model_installed(mid, refresh=False):
                    warn_missing.append(mid)
            if warn_missing:
                add_check("configured_model_missing_warning", True, "WARN: missing local model(s): " + ", ".join(sorted(set(warn_missing))))
            else:
                add_check("configured_model_missing_warning", True, "none")
        elif provider == "longcat":
            has_key = bool(os.getenv("LONGCAT_API_KEY"))
            add_check("longcat_api_key_configured", has_key, "configured" if has_key else "missing")
            has_url = bool(os.getenv("LONGCAT_BASE_URL"))
            add_check("longcat_base_url_configured", has_url, os.getenv("LONGCAT_BASE_URL") or "missing")
            has_model = bool(os.getenv("LONGCAT_MODEL") or os.getenv("DEFAULT_MODEL"))
            add_check("longcat_model_configured", has_model, os.getenv("LONGCAT_MODEL") or os.getenv("DEFAULT_MODEL") or "missing")
        elif provider == "groq":
            has_key = bool(os.getenv("GROQ_API_KEY"))
            add_check("groq_api_key_configured", has_key, "configured" if has_key else "missing")
            has_url = bool(os.getenv("GROQ_BASE_URL"))
            add_check("groq_base_url_configured", has_url, os.getenv("GROQ_BASE_URL") or "missing")
            has_model = bool(os.getenv("GROQ_MODEL") or os.getenv("DEFAULT_MODEL"))
            add_check("groq_model_configured", has_model, os.getenv("GROQ_MODEL") or os.getenv("DEFAULT_MODEL") or "missing")
    except Exception as e:
        add_check("active_model_profile", False, f"{type(e).__name__}: {e}")

    try:
        r = client._http.get(f"{client.root_base_url}/openapi.json", timeout=5.0)
        r.raise_for_status()
        paths = (r.json() or {}).get("paths", {})
        required_paths = ["/v1/docs/ingest", "/v1/docs/search", "/v1/docs/ask"]
        missing = [p for p in required_paths if p not in paths]
        add_check(
            "docs_api_routes",
            len(missing) == 0,
            "all required routes present" if not missing else f"missing: {', '.join(missing)}",
        )
    except Exception as e:
        add_check("docs_api_routes", False, f"{type(e).__name__}: {e}")

    try:
        trace_dir = Path(os.getcwd()) / ".runtime" / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        probe = trace_dir / f".doctor_probe_{int(time.time())}.tmp"
        probe.write_text("ok\n", encoding="utf-8")
        _ = probe.read_text(encoding="utf-8")
        probe.unlink(missing_ok=True)
        add_check("trace_dir_writable", True, str(trace_dir))
    except Exception as e:
        add_check("trace_dir_writable", False, f"{type(e).__name__}: {e}")

    try:
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lock_dir = Path(root_dir) / ".runtime"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "doctor.lock"

        # Check lock first. If it cannot be acquired, a baseline is already running.
        parent_lock = _acquire_lock(str(lock_path))
        if not parent_lock:
            add_check("eval_baseline_quick_pass", False, "Error: Another doctor or eval baseline process is already running (lock held).")
        else:
            try:
                import uuid
                run_id = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
                env = os.environ.copy()
                env["STARAGENT_LOCK_ACQUIRED_BY_PARENT"] = "1"
                env["STARAGENT_EVAL_RUN_ID"] = run_id

                # Run exact same command as manual: ./scripts/staragent eval baseline
                # Capture full baseline output to .runtime/doctor_baseline_last.log
                log_path = lock_dir / "doctor_baseline_last.log"
                cmd = ["./scripts/staragent", "eval", "baseline"]

                with open(log_path, "w", encoding="utf-8") as log_f:
                    proc = subprocess.run(
                        cmd,
                        cwd=root_dir,
                        stdout=log_f,
                        stderr=subprocess.STDOUT,
                        env=env,
                        timeout=900.0,
                    )

                output = ""
                if log_path.exists():
                    output = log_path.read_text(encoding="utf-8")

                if proc.returncode == 0:
                    add_check("eval_baseline_quick_pass", True, f"exit=0; log={log_path}")
                else:
                    lines = output.splitlines()
                    last_40 = "\n".join(lines[-40:])
                    detail = f"exit={proc.returncode}\nCommand: {' '.join(cmd)}\nLog Path: {log_path}\nLast 40 lines:\n{last_40}"
                    add_check("eval_baseline_quick_pass", False, detail)

                # Cleanup doctor scratch state and traces to avoid leaving stale state
                import shutil
                for d in [f"scratch/eval_simple_{run_id}", f"scratch/eval_backend_{run_id}"]:
                    p = Path(root_dir) / d
                    if p.exists():
                        try:
                            shutil.rmtree(p, ignore_errors=True)
                        except Exception:
                            pass
                # Clean up trace files in .runtime/traces
                traces_dir = Path(root_dir) / ".runtime" / "traces"
                if traces_dir.exists():
                    for f in traces_dir.glob(f"*{run_id}*"):
                        try:
                            f.unlink(missing_ok=True)
                        except Exception:
                            pass

            except subprocess.TimeoutExpired:
                add_check("eval_baseline_quick_pass", False, "TimeoutExpired: command timed out after 900s")
            except Exception as e:
                add_check("eval_baseline_quick_pass", False, f"{type(e).__name__}: {e}")
            finally:
                _release_lock(parent_lock, str(lock_path))

    except Exception as e:
        add_check("eval_baseline_quick_pass", False, f"{type(e).__name__}: {e}")

    passed = sum(1 for c in checks if c["ok"])
    total = len(checks)
    overall_ok = passed == total
    summary = {
        "project_id": ctx.get("project_id"),
        "base_url": client.v1_base_url,
        "passed": passed,
        "total": total,
        "status": "ok" if overall_ok else "failed",
        "checks": checks,
    }

    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("[DOCTOR] StarAgent diagnostics")
        for c in checks:
            mark = "✅" if c["ok"] else "❌"
            print(f"{mark} {c['name']}: {c['detail']}")
        verdict = "PASS ✅" if overall_ok else "FAIL ❌"
        print(f"[DOCTOR] verdict: {verdict} ({passed}/{total} checks passed)")
    return 0 if overall_ok else 1


def build_parser() -> argparse.ArgumentParser:
    prog = os.getenv("STARAGENT_CLI_PROG") or os.getenv("MACAGENT_CLI_PROG") or os.path.basename(sys.argv[0] or "") or "staragent"
    p = argparse.ArgumentParser(prog=prog, description="StarAgent CLI (thin client over the existing FastAPI runtime; legacy compatible: macagent)")
    p.add_argument(
        "--base-url",
        dest="base_url",
        default=os.getenv("STARAGENT_BASE_URL") or os.getenv("MACAGENT_BASE_URL") or "",
        help="Base URL (default: STARAGENT_BASE_URL / MACAGENT_BASE_URL or http://127.0.0.1:8095/v1)",
    )
    p.add_argument(
        "--api-key",
        dest="api_key",
        default=os.getenv("STARAGENT_API_KEY") or os.getenv("MACAGENT_API_KEY") or "",
        help="API key (default: STARAGENT_API_KEY / MACAGENT_API_KEY / PROXY_API_KEY)",
    )
    p.add_argument(
        "--model",
        dest="model",
        default=os.getenv("STARAGENT_DEFAULT_MODEL") or os.getenv("MACAGENT_DEFAULT_MODEL") or "",
        help="Model id (default: STARAGENT_DEFAULT_MODEL / MACAGENT_DEFAULT_MODEL / DEFAULT_MODEL)",
    )
    p.add_argument("--project", dest="project_id", default=None, help="project_id (default: stored context or STARAGENT_DEFAULT_PROJECT / MACAGENT_DEFAULT_PROJECT)")
    p.add_argument("--conversation", dest="conversation_id", default=None, help="conversation_id (default: stored context)")
    p.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")
    p.add_argument("--no-auto-start", dest="no_auto_start", action="store_true", help="Disable automatic server startup")

    sub = p.add_subparsers(dest="cmd", required=True)

    server = sub.add_parser("server", help="Manage StarAgent backend server")
    server_sub = server.add_subparsers(dest="server_cmd", required=True)
    server_sub.add_parser("status", help="Check server status")
    server_sub.add_parser("start", help="Start the server")
    server_sub.add_parser("stop", help="Stop the server")
    server_sub.add_parser("restart", help="Restart the server")
    logs_cmd = server_sub.add_parser("logs", help="Show server startup and runtime logs")
    logs_cmd.add_argument("--tail", type=int, default=50, help="Number of tail lines to display (default: 50)")
    server_sub.add_parser("doctor-startup", help="Check workspace, Python, and model configuration diagnostics prior to server launch")

    model = sub.add_parser("model", help="Manage model configurations")
    model_sub = model.add_subparsers(dest="model_cmd", required=True)
    model_sub.add_parser("list", help="List available models")
    model_sub.add_parser("current", help="Show current model assignments")
    model_sub.add_parser("refresh", help="Refresh local Ollama model cache from /api/tags")
    pull_cmd = model_sub.add_parser("pull", help="Pull a local Ollama model")
    pull_cmd.add_argument("model_id", help="Model ID to pull (e.g. qwen2.5-coder:14b)")
    inspect_cmd = model_sub.add_parser("inspect", help="Inspect one model (installed/provider/context/metadata)")
    inspect_cmd.add_argument("model_id", help="Model ID")
    switch_cmd = model_sub.add_parser("switch", help="Switch global default model")
    switch_cmd.add_argument("model_id", help="Model ID")
    set_cmd = model_sub.add_parser("set", help="Set model for a specific agent")
    set_cmd.add_argument("agent", help="Agent role (e.g. BACKEND_AGENT)")
    set_cmd.add_argument("model_id", help="Model ID")

    ask = sub.add_parser("ask", help="Fast-path ask (forced fast route)")
    ask.add_argument("prompt")

    stream_help = "Stream mode: full|compact|quiet. Using --stream with no value defaults to compact."

    agent = sub.add_parser("agent", help="Agent-path task (forced agent route)")
    agent.add_argument("prompt")
    agent.add_argument(
        "--stream",
        nargs="?",
        const="compact",
        choices=("full", "compact", "quiet"),
        default=None,
        help=stream_help,
    )

    sub.add_parser("approve", help="Approve pending action (send yes)")
    sub.add_parser("reject", help="Reject pending action (send no)")
    sub.add_parser("continue", help="Continue pending partial task (send continue)")

    sub.add_parser("status", help="Print health/models/context")
    sub.add_parser("rollback", help="Best-effort rollback via agent path")
    sub.add_parser("smoke-test", help="Run compact smoke test against the API")
    sub.add_parser("doctor", help="Run release readiness diagnostics (health/profile/docs routes/trace/eval baseline)")

    multi = sub.add_parser("multi-agent", help="Multi-agent orchestration mode")
    multi.add_argument("task")
    multi.add_argument(
        "--stream",
        nargs="?",
        const="compact",
        choices=("full", "compact", "quiet"),
        default=None,
        help=stream_help,
    )

    trace_cmd = sub.add_parser("trace", help="View structured trace for a task")
    trace_cmd.add_argument("task_id", help="Task ID to view trace for")

    eval_cmd = sub.add_parser("eval", help="Run eval suite")
    eval_cmd.add_argument("suite", choices=["all", "baseline", "medium", "repo", "stress", "gates", "skills"], help="Which eval suite to run")

    skills_cmd = sub.add_parser("skills", help="Skill library management")
    skills_sub = skills_cmd.add_subparsers(dest="skills_cmd", required=True)
    sk_ingest = skills_sub.add_parser("ingest", help="Ingest skills from repo")
    sk_ingest.add_argument("--path", required=True, help="Path to claude-skills repo")
    skills_sub.add_parser("list", help="List all ingested skills")
    sk_search = skills_sub.add_parser("search", help="Search skills by query")
    sk_search.add_argument("query", help="Search query")
    sk_show = skills_sub.add_parser("show", help="Show skill details")
    sk_show.add_argument("skill_name", help="Skill name")
    skills_sub.add_parser("eval", help="Run skill-specific tests")

    preset = sub.add_parser("preset", help="Operator-friendly preset workflows (thin wrappers over existing profiles)")
    preset_sub = preset.add_subparsers(dest="preset_cmd", required=True)
    preset_sub.add_parser("list", help="List available presets")
    preset_sub.add_parser("packs", help="List curated preset packs (operator flows)")
    preset_sub.add_parser("examples", help="Print copy-paste examples for common workflows")
    preset_run = preset_sub.add_parser("run", help="Run a preset workflow")
    preset_run.add_argument("name", help="Preset name (e.g. quick_repo_audit)")
    preset_run.add_argument("--path", dest="path", default=None)
    preset_run.add_argument("--question", dest="question", default=None)
    preset_run.add_argument("--issue", dest="issue", default=None)
    preset_run.add_argument("--goal", dest="goal", default=None)
    preset_run.add_argument("--file", dest="files", action="append", default=None)
    preset_run.add_argument("--log", dest="logs", action="append", default=None)
    preset_run.add_argument("--mode", dest="mode", default=None)
    preset_run.add_argument("--output", dest="output_path", default=None, help="Output path (release_review preset)")
    preset_run.add_argument("--max-steps", dest="max_steps", type=int, default=None)
    preset_run.add_argument("--max-retries", dest="max_retries", type=int, default=None)
    preset_run.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")
    preset_run.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")
    preset_run.add_argument("--wait", action="store_true", help="Wait for task completion")
    preset_run.add_argument("--print-primary", action="store_true", help="Print primary artifact content after completion")

    preset_pack_run = preset_sub.add_parser("pack-run", help="Run a curated preset pack (may run multiple presets)")
    preset_pack_run.add_argument("name", help="Pack name (e.g. repo_onboarding, release_prep)")
    preset_pack_run.add_argument("--path", dest="path", default=None)
    preset_pack_run.add_argument("--question", dest="question", default=None)
    preset_pack_run.add_argument("--issue", dest="issue", default=None)
    preset_pack_run.add_argument("--goal", dest="goal", default=None)
    preset_pack_run.add_argument("--file", dest="files", action="append", default=None)
    preset_pack_run.add_argument("--log", dest="logs", action="append", default=None)
    preset_pack_run.add_argument("--mode", dest="mode", default=None)
    preset_pack_run.add_argument("--output", dest="output_path", default=None, help="Output path (release_prep/release_review export)")
    preset_pack_run.add_argument("--max-steps", dest="max_steps", type=int, default=None)
    preset_pack_run.add_argument("--max-retries", dest="max_retries", type=int, default=None)
    preset_pack_run.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")
    preset_pack_run.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")
    preset_pack_run.add_argument("--wait", action="store_true", help="Wait for task completion")
    preset_pack_run.add_argument("--print-primary", action="store_true", help="Print primary artifact content after completion")

    # Phase 4: Task engine + research mode
    task = sub.add_parser("task", help="Iterative task engine (create/run/status/continue)")
    task_sub = task.add_subparsers(dest="task_cmd", required=True)

    task_create = task_sub.add_parser("create", help="Create a task run")
    task_create.add_argument("goal", help="Task goal")
    task_create.add_argument("--type", dest="task_type", default="agent", help="Task type (agent|research)")
    task_create.add_argument("--dod", dest="dod", default=None, help="Definition of done")
    task_create.add_argument("--max-steps", dest="max_steps", type=int, default=25)
    task_create.add_argument("--max-retries", dest="max_retries", type=int, default=2)
    task_create.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")

    task_status = task_sub.add_parser("status", help="Get task status")
    task_status.add_argument("task_id", nargs="?", help="Task id (default: last task)")

    task_continue = task_sub.add_parser("continue", help="Continue a task run (bounded)")
    task_continue.add_argument("task_id", nargs="?", help="Task id (default: last task)")
    task_continue.add_argument("--steps", dest="max_step_advances", type=int, default=3)
    task_continue.add_argument("--duration", dest="max_duration_s", type=float, default=20.0)

    task_approve = task_sub.add_parser("approve", help="Approve a paused task tool action")
    task_approve.add_argument("task_id", nargs="?", help="Task id (default: last task)")

    task_reject = task_sub.add_parser("reject", help="Reject a paused task tool action")
    task_reject.add_argument("task_id", nargs="?", help="Task id (default: last task)")
    task_reject.add_argument("--reason", dest="reason", default="rejected")

    task_artifacts = task_sub.add_parser("artifacts", help="List artifact files for a task")
    task_artifacts.add_argument("task_id", nargs="?", help="Task id (default: last task)")
    task_artifacts.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")

    task_list = task_sub.add_parser("list", help="List recent task runs")
    task_list.add_argument("--status", dest="status", default=None, help="Filter by status")
    task_list.add_argument("--limit", dest="limit", type=int, default=20)
    task_list.add_argument("--offset", dest="offset", type=int, default=0)
    task_list.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")

    task_inspect = task_sub.add_parser("inspect", help="Inspect a task run (task + progress + steps)")
    task_inspect.add_argument("task_id", nargs="?", help="Task id (default: last task)")
    task_inspect.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")

    task_show = task_sub.add_parser("show", help="Alias for inspect (or primary report if available)")
    task_show.add_argument("task_id", nargs="?", help="Task id (default: last task)")
    task_show.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")

    task_summary = task_sub.add_parser("summary", help="Task summary (status/progress/final_summary)")
    task_summary.add_argument("task_id", nargs="?", help="Task id (default: last task)")
    task_summary.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")

    task_logs = task_sub.add_parser("logs", help="Task step logs (tail of steps)")
    task_logs.add_argument("task_id", nargs="?", help="Task id (default: last task)")
    task_logs.add_argument("--tail", dest="tail_steps", type=int, default=30)
    task_logs.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")

    task_artifact = task_sub.add_parser("artifact", help="Preview one artifact file")
    task_artifact.add_argument("task_id", nargs="?", help="Task id (default: last task)")
    task_artifact.add_argument("name", nargs="?", help="Artifact file name (e.g. final_report.md)")
    task_artifact.add_argument("--format", dest="format", default="text", help="text|json")
    # Default is decided at runtime: JSON previews should not be tailed by default
    # (tailing before pretty-printing often breaks parsing).
    task_artifact.add_argument("--tail-lines", dest="tail_lines", type=int, default=None)
    task_artifact.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")

    # Workflow Engine Command Group
    workflow_parser = sub.add_parser("workflow", help="Manage and execute ICM-inspired stage-based workflows")
    workflow_sub = workflow_parser.add_subparsers(dest="workflow_cmd", required=True)

    workflow_sub.add_parser("list", help="List all available workflows")

    inspect_wf = workflow_sub.add_parser("inspect", help="Inspect stages of a workflow")
    inspect_wf.add_argument("name", help="Workflow name")

    run_wf = workflow_sub.add_parser("run", help="Run a workflow task")
    run_wf.add_argument("name", help="Workflow name")
    run_wf.add_argument("--goal", default="Run workflow task", help="User target/goal description")
    run_wf.add_argument("--dod", help="Definition of done")

    create_wf = workflow_sub.add_parser("create", help="Create a custom workflow")
    create_wf.add_argument("name", help="New workflow name")
    create_wf.add_argument("--description", default="", help="Description of custom workflow")

    resume_wf = workflow_sub.add_parser("resume", help="Resume a paused/failed workflow")
    resume_wf.add_argument("task_id", help="Workflow Task ID")
    resume_wf.add_argument("--stage", help="Optionally force resume from specific stage name")

    cp_wf = workflow_sub.add_parser("checkpoints", help="List checkpoints of a workflow task")
    cp_wf.add_argument("task_id", help="Workflow Task ID")

    graph_wf = workflow_sub.add_parser("graph", help="Show ASCII diagram of stages")
    graph_wf.add_argument("name", help="Workflow name")

    workflow_sub.add_parser("runs", help="List all workflow runs")

    status_wf = workflow_sub.add_parser("status", help="Get status of a workflow run")
    status_wf.add_argument("run_id", help="Workflow Run/Task ID")

    trace_wf = workflow_sub.add_parser("trace", help="Get trace of a workflow run")
    trace_wf.add_argument("run_id", help="Workflow Run/Task ID")

    state_wf = workflow_sub.add_parser("state", help="Get current state of a workflow run")
    state_wf.add_argument("run_id", help="Workflow Run/Task ID")

    gates_wf = workflow_sub.add_parser("gates", help="Get evaluated gates of a workflow run")
    gates_wf.add_argument("run_id", help="Workflow Run/Task ID")

    approve_wf = workflow_sub.add_parser("approve", help="Approve a paused stage of a workflow run")
    approve_wf.add_argument("run_id", help="Workflow Run/Task ID")
    approve_wf.add_argument("--stage", help="Stage name to approve")

    reject_wf = workflow_sub.add_parser("reject", help="Reject a paused stage of a workflow run")
    reject_wf.add_argument("run_id", help="Workflow Run/Task ID")
    reject_wf.add_argument("--stage", help="Stage name to reject")

    explain_wf = workflow_sub.add_parser("explain", help="Explain workflow details, stages, and safety rules")
    explain_wf.add_argument("name", help="Workflow name")

    research = sub.add_parser("research", help="Document research mode (runs as a task)")
    research_sub = research.add_subparsers(dest="research_cmd", required=True)

    research_run = research_sub.add_parser("run", help="Run document research on a folder")
    research_run.add_argument("--path", dest="path", required=True, help="Folder path to ingest")
    research_run.add_argument("--question", dest="question", default=None, help="Research question")
    research_run.add_argument("--mode", dest="mode", default="research", help="Mode: summary|research|comparison")
    research_run.add_argument("--max-steps", dest="max_steps", type=int, default=60)
    research_run.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")
    research_run.add_argument("--wait", action="store_true", help="Wait for task completion")
    research_run.add_argument("--print-primary", action="store_true", help="Print primary artifact content after completion")

    research_status = research_sub.add_parser("status", help="Get research task status")
    research_status.add_argument("task_id", nargs="?", help="Task id (default: last task)")

    docs = sub.add_parser("docs", help="Local documentation knowledge base")
    docs_sub = docs.add_subparsers(dest="docs_cmd", required=True)
    
    docs_ingest = docs_sub.add_parser("ingest", help="Ingest a folder containing markdown/html or DevDocs export")
    docs_ingest.add_argument("path", help="Folder path to ingest")
    docs_ingest.add_argument("--type", dest="source_type", default="project_docs", help="Source type label")
    
    docs_ingest_pkg = docs_sub.add_parser("ingest-package", help="Ingest python package documentation via pydoc")
    docs_ingest_pkg.add_argument("package_name", help="Package name")
    docs_ingest_pkg.add_argument("--manager", dest="manager", default="pip", help="Package manager (default: pip)")

    docs_search = docs_sub.add_parser("search", help="Search the local documentation knowledge base")
    docs_search.add_argument("query", help="Search query")
    docs_search.add_argument("--package", dest="package_name", default=None, help="Filter by package name")
    docs_search.add_argument("--limit", dest="max_results", type=int, default=5, help="Max results")
    docs_search.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")

    docs_ask = docs_sub.add_parser("ask", help="Answer a question from project docs evidence only")
    docs_ask.add_argument("question", help="Question to answer")
    docs_ask.add_argument("--package", dest="package_name", default=None, help="Filter by package name")
    docs_ask.add_argument("--limit", dest="max_results", type=int, default=5, help="Max evidence chunks")
    docs_ask.add_argument("--json", dest="as_json", action="store_true", help="Print machine-friendly JSON output")

    repo_audit = sub.add_parser("repo-audit", help="Repo audit profile (runs as a task)")
    repo_audit.add_argument("--path", dest="path", required=True, help="Repository/project path to audit")
    repo_audit.add_argument("--question", dest="question", default=None, help="Optional audit question")
    repo_audit.add_argument("--max-steps", dest="max_steps", type=int, default=25)
    repo_audit.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")
    repo_audit.add_argument("--wait", action="store_true", help="Wait for task completion")
    repo_audit.add_argument("--print-primary", action="store_true", help="Print primary artifact content after completion")

    issue_triage = sub.add_parser("issue-triage", help="Issue triage profile (runs as a task)")
    issue_triage.add_argument("--path", dest="path", required=True, help="Repository/project path")
    issue_triage.add_argument("--issue", dest="issue", required=True, help="Issue description")
    issue_triage.add_argument("--file", dest="files", action="append", default=None, help="Relevant file path (repeatable)")
    issue_triage.add_argument("--log", dest="logs", action="append", default=None, help="Relevant log path (repeatable)")
    issue_triage.add_argument("--max-steps", dest="max_steps", type=int, default=25)
    issue_triage.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")
    issue_triage.add_argument("--wait", action="store_true", help="Wait for task completion")
    issue_triage.add_argument("--print-primary", action="store_true", help="Print primary artifact content after completion")

    writing = sub.add_parser("write", help="Writing profile (runs as a task)")
    writing.add_argument("--path", dest="path", required=True, help="Folder path containing notes/docs")
    writing.add_argument("--goal", dest="goal", required=True, help="Writing objective")
    writing.add_argument("--file", dest="files", action="append", default=None, help="Specific source file path (repeatable)")
    writing.add_argument("--max-steps", dest="max_steps", type=int, default=25)
    writing.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")
    writing.add_argument("--wait", action="store_true", help="Wait for task completion")
    writing.add_argument("--print-primary", action="store_true", help="Print primary artifact content after completion")
    return p


def _client_from_args(args: argparse.Namespace) -> MacAgentClient:
    cfg = MacAgentConfig.from_env()
    if args.base_url:
        cfg.base_url = args.base_url
    if args.api_key:
        cfg.api_key = args.api_key
    if args.model:
        cfg.default_model = args.model
    return MacAgentClient(cfg)


def _wait_for_task(client: MacAgentClient, task_id: str, print_primary: bool, as_json: bool) -> int:
    if as_json:
        # Don't print progress if JSON requested
        pass
    else:
        print(f"Waiting for task {task_id}...", end="", flush=True)

    last_status = None
    while True:
        status_res = client.task_status(task_id)
        task = status_res.get("task") or {}
        current_status = task.get("status")
        
        if current_status != last_status:
            last_status = current_status
            if not as_json:
                print(f"\nStatus: {current_status}", end="", flush=True)
        else:
            if not as_json:
                print(".", end="", flush=True)
        
        if current_status in ("completed", "failed", "cancelled"):
            if not as_json:
                print(f"\nTask {task_id} finished with status: {current_status}")
            
            if print_primary and current_status == "completed":
                summary = client.task_summary(task_id)
                primary_report = summary.get("primary_report")
                if isinstance(primary_report, str) and primary_report.strip():
                    if not as_json:
                        print("\n--- Primary Report ---\n")
                        print(primary_report)
                else:
                    artifacts = client.task_artifacts(task_id)
                    primary = artifacts.get("primary_artifact") or {}
                    if primary.get("exists") and primary.get("name"):
                        preview = client.task_artifact_preview(task_id, str(primary.get("name")))
                        content = preview.get("content")
                        if isinstance(content, str) and content.strip():
                            if not as_json:
                                print(f"\n--- Primary Artifact: {primary['name']} ---\n")
                                print(content)
                        else:
                            print("Task completed but no primary report was stored.")
                            return 1
                    else:
                        print("Task completed but no primary report was stored.")
                        return 1
            
            if as_json:
                print(json.dumps(status_res, ensure_ascii=False, indent=2))
            
            return 0 if current_status == "completed" else 1
            
        time.sleep(2)


def _ensure_server_running(client: MacAgentClient, no_auto_start: bool):
    try:
        health = client.health(timeout=2.0)
        if health.get("ok"):
            return
    except Exception:
        pass

    if no_auto_start:
        print("[staragent] server not running. (auto-start disabled)")
        sys.exit(1)

    print("[staragent] server not running, starting...")
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root_dir, "scripts", "start_staragent.sh")
    
    try:
        subprocess.Popen(["bash", script], cwd=root_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[staragent] failed to execute startup script: {e}")
        sys.exit(1)

    # Poll until ready
    max_attempts = 90
    for _ in range(max_attempts):
        time.sleep(1.0)
        try:
            if client.health(timeout=2.0).get("ok"):
                print("[staragent] server ready ✅")
                return
        except Exception:
            pass
            
    print("[staragent] server failed to start.")
    port = "8095"
    m = re.search(r":(\d+)", client.root_base_url)
    if m:
        port = m.group(1)
    log_file = os.path.join(root_dir, "logs", f"macagent_{port}.log")
    if not os.path.exists(log_file):
        log_file = os.path.join(root_dir, "logs", "macagent_8095.log")
    if os.path.exists(log_file):
        with open(log_file, "r") as f:
            lines = f.readlines()
            for line in lines[-80:]:
                print(line, end="")
    sys.exit(1)


def _run_doctor_startup(client: MacAgentClient, root_dir: str) -> int:
    print("[DOCTOR-STARTUP] Running pre-startup diagnostics...")
    checks_passed = 0
    total_checks = 5
    
    # 1. Python Environment Check
    print("1. Python Environment Check:")
    print(f"   Python Executable: {sys.executable}")
    print(f"   Python Version: {sys.version}")
    try:
        import httpx
        import uvicorn
        import fastapi
        print("   ✅ Essential dependencies (httpx, uvicorn, fastapi) are installed.")
        checks_passed += 1
    except ImportError as e:
        print(f"   ❌ Missing dependency: {e}")

    # 2. Workspace Permissions
    print("2. Workspace Permissions Check:")
    runtime_dir = os.path.join(root_dir, ".runtime")
    logs_dir = os.path.join(root_dir, "logs")
    try:
        os.makedirs(runtime_dir, exist_ok=True)
        os.makedirs(logs_dir, exist_ok=True)
        
        # Test write permission in runtime_dir
        test_file = os.path.join(runtime_dir, ".startup_write_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        
        # Test write permission in logs_dir
        test_log = os.path.join(logs_dir, ".startup_write_test")
        with open(test_log, "w") as f:
            f.write("test")
        os.remove(test_log)
        
        print("   ✅ Workspace directories (.runtime, logs) are writable.")
        checks_passed += 1
    except Exception as e:
        print(f"   ❌ Workspace write check failed: {e}")

    # 3. Port Binding Availability
    print("3. Port Binding Availability Check:")
    port = 8095
    m = re.search(r":(\d+)", client.root_base_url)
    if m:
        port = int(m.group(1))
    
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Check if the port is already in use
        s.bind(("127.0.0.1", port))
        print(f"   ✅ Port {port} is free and available for binding.")
        checks_passed += 1
    except Exception as e:
        print(f"   ⚠️ Port {port} is already in use or unavailable: {e}")
        # Check if server is actually running on that port
        try:
            if client.health(timeout=2.0).get("ok"):
                print(f"   ✅ StarAgent is already running on port {port}.")
                checks_passed += 1
            else:
                print(f"   ❌ Port {port} is blocked by another process.")
        except Exception:
            print(f"   ❌ Port {port} is blocked by another process.")
    finally:
        s.close()

    # 4. Ollama Reachable Tags Check
    print("4. Ollama Service reachability check:")
    try:
        sys.path.insert(0, root_dir)
        from app.model_registry import registry
        ollama_url = registry._ollama_base_url()
        print(f"   Ollama URL: {ollama_url}")
        local_models = registry.list_local_ollama_models(refresh=True)
        model_names = [m.get("name") for m in local_models]
        print(f"   ✅ Ollama reachable. Found {len(local_models)} local models: {', '.join(model_names)}")
        checks_passed += 1
    except Exception as e:
        # Check if remote fallbacks exist
        has_remote = any(os.getenv(k) for k in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "LONGCAT_API_KEY", "GROQ_API_KEY"])
        if has_remote:
            print(f"   ⚠️ Ollama reachability check failed: {e}")
            print("      Proceeding because remote API keys are configured.")
            checks_passed += 1
        else:
            print(f"   ❌ Ollama reachability check failed: {e} and no remote keys configured.")

    # 5. Model Defaults Check
    print("5. Defaults Check:")
    try:
        sys.path.insert(0, root_dir)
        from app.model_registry import registry, get_effective_model_config
        default_model = get_effective_model_config()["model"]
        provider = registry.infer_provider(default_model)
        print(f"   Default Model: {default_model} (Provider: {provider})")
        
        if provider != "ollama":
            print(f"   ✅ Default model is remote ({provider}).")
            checks_passed += 1
        else:
            # Check if local ollama model is installed
            installed = registry.is_local_ollama_model_installed(default_model, refresh=False)
            if installed:
                print(f"   ✅ Local model '{default_model}' is installed.")
                checks_passed += 1
            else:
                has_remote = any(os.getenv(k) for k in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "LONGCAT_API_KEY", "GROQ_API_KEY"])
                if has_remote:
                    print(f"   ⚠️ Local model '{default_model}' is missing, but remote API keys are configured for fallbacks.")
                    checks_passed += 1
                else:
                    print(f"   ❌ Local model '{default_model}' is missing, and no remote keys are configured.")
    except Exception as e:
        print(f"   ❌ Failed to verify local model: {e}")

    print(f"\n[DOCTOR-STARTUP] Verdict: {checks_passed}/{total_checks} checks passed.")
    if checks_passed == total_checks:
        print("🎉 Ready for startup!")
        return 0
    else:
        print("❌ Pre-startup diagnostics failed. Please resolve the issues above before starting.")
        return 1


def load_dotenv_if_present():
    try:
        from dotenv import load_dotenv
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dotenv_path = os.path.join(root_dir, ".env")
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path, override=False)
    except Exception:
        pass


def main(argv: Optional[list[str]] = None) -> int:
    load_dotenv_if_present()
    import subprocess
    args = build_parser().parse_args(argv)
    ctx = _resolve_context(args)
    client = _client_from_args(args)
    
    # Commands that do NOT require the server to be running
    local_cmds = {"server", "model", "trace", "eval", "skills"}
    if args.cmd not in local_cmds:
        _ensure_server_running(client, args.no_auto_start)
        
    try:
        if args.cmd == "server":
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if args.server_cmd == "status":
                try:
                    if client.health(timeout=2.0).get("ok"):
                        print("[staragent] server is running.")
                    else:
                        print("[staragent] server is not responding correctly.")
                except Exception:
                    print("[staragent] server is stopped.")
            elif args.server_cmd == "start":
                _ensure_server_running(client, False)
            elif args.server_cmd == "stop":
                script = os.path.join(root_dir, "scripts", "stop_staragent.sh")
                subprocess.run(["bash", script], cwd=root_dir)
                print("[staragent] server stopped.")
            elif args.server_cmd == "restart":
                script = os.path.join(root_dir, "scripts", "stop_staragent.sh")
                subprocess.run(["bash", script], cwd=root_dir)
                time.sleep(1)
                _ensure_server_running(client, False)
            elif args.server_cmd == "logs":
                port = "8095"
                m = re.search(r":(\d+)", client.root_base_url)
                if m:
                    port = m.group(1)
                log_file = os.path.join(root_dir, "logs", f"macagent_{port}.log")
                if not os.path.exists(log_file):
                    log_file = os.path.join(root_dir, "logs", "macagent_8095.log")
                if os.path.exists(log_file):
                    print(f"Showing last {args.tail} lines of {log_file}:")
                    with open(log_file, "r") as f:
                        lines = f.readlines()
                        for line in lines[-args.tail:]:
                            print(line, end="")
                else:
                    print(f"Log file not found at {log_file}")
            elif args.server_cmd == "doctor-startup":
                return _run_doctor_startup(client, root_dir)
            return 0
            
        if args.cmd == "model":
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from app.model_registry import registry
            if args.model_cmd == "list":
                try:
                    local = registry.list_local_ollama_models(refresh=True)
                except Exception:
                    local = registry.list_local_ollama_models(refresh=False)
                configured_remote = _configured_remote_models()
                installed_names = {str(m.get("name") or "") for m in local}
                suggestions = []
                for m in registry.get_registry_suggestions():
                    if m.provider == "ollama" and m.id in installed_names:
                        continue
                    suggestions.append(m)
                print("LOCAL OLLAMA MODELS:")
                if local:
                    for m in local:
                        print(f"- {m.get('name')}")
                else:
                    print("- (none found)")
                print("")
                print("CONFIGURED REMOTE MODELS:")
                if configured_remote:
                    for provider, mid in configured_remote.items():
                        model_s = mid or "(model not set)"
                        print(f"- {provider}: {model_s}")
                else:
                    print("- (none configured)")
                print("")
                print("REGISTRY SUGGESTIONS:")
                if suggestions:
                    for m in suggestions:
                        print(f"- {m.id} ({m.provider})")
                else:
                    print("- (none)")
            elif args.model_cmd == "current":
                print(f"Global Default: {registry.global_default}")
                if registry.agent_routing:
                    print("Agent Overrides:")
                    for role, mid in registry.agent_routing.items():
                        print(f"  {role}: {mid}")
            elif args.model_cmd == "refresh":
                cache = registry.refresh_ollama_cache()
                models = cache.get("models") if isinstance(cache, dict) else []
                count = len(models) if isinstance(models, list) else 0
                print(f"Refreshed local Ollama model cache: {count} model(s)")
            elif args.model_cmd == "pull":
                model_id = str(args.model_id).strip()
                rc = subprocess.run(["ollama", "pull", model_id]).returncode
                if rc != 0:
                    return rc
                try:
                    registry.refresh_ollama_cache()
                except Exception:
                    pass
                print(f"Pulled model: {model_id}")
            elif args.model_cmd == "inspect":
                model_id = str(args.model_id).strip()
                info = registry.inspect_model(model_id, refresh_local=True)
                print(f"model: {info.get('model_id')}")
                print(f"provider: {info.get('provider')}")
                installed = info.get("installed")
                if installed is not None:
                    if isinstance(installed, bool):
                        print(f"installed: {'yes' if installed else 'no'}")
                    else:
                        print(f"installed: {installed}")
                if info.get("context_estimate") is not None:
                    print(f"context_estimate: {info.get('context_estimate')}")
                if info.get("size") is not None:
                    print(f"size: {info.get('size')}")
                if info.get("modified_at"):
                    print(f"modified_at: {info.get('modified_at')}")
                roles = info.get("recommended_roles") or []
                if roles:
                    print("recommended_roles: " + ", ".join([str(x) for x in roles]))
            elif args.model_cmd == "switch":
                model_id = str(args.model_id).strip()
                provider = registry.infer_provider(model_id)
                if provider == "longcat":
                    if not os.getenv("LONGCAT_API_KEY"):
                        print("LongCat API key is missing (LONGCAT_API_KEY).")
                        return 1
                elif provider == "groq":
                    if not os.getenv("GROQ_API_KEY"):
                        print("Groq API key is missing (GROQ_API_KEY).")
                        return 1
                elif provider == "ollama" and not registry.is_local_ollama_model_installed(model_id, refresh=True):
                    print(f"Model {model_id} is not installed locally.")
                    print("Install with:")
                    print(f"ollama pull {model_id}")
                    return 1
                registry.set_global_default(model_id)
                print(f"Global default model switched to {model_id}")
            elif args.model_cmd == "set":
                model_id = str(args.model_id).strip()
                provider = registry.infer_provider(model_id)
                agent = str(args.agent or "").upper()
                if provider == "longcat":
                    if not os.getenv("LONGCAT_API_KEY"):
                        print("LongCat API key is missing (LONGCAT_API_KEY).")
                        return 1
                elif provider == "groq":
                    if not os.getenv("GROQ_API_KEY"):
                        print("Groq API key is missing (GROQ_API_KEY).")
                        return 1
                elif provider == "ollama" and not registry.is_local_ollama_model_installed(model_id, refresh=True):
                    print(f"Model {model_id} is not installed locally.")
                    print("Install with:")
                    print(f"ollama pull {model_id}")
                    return 1
                registry.set_agent_model(args.agent, model_id)
                print(f"Agent {args.agent} model set to {model_id}")
            return 0

        if args.cmd == "ask":
            res = client.ask(args.prompt, project_id=ctx["project_id"], conversation_id=ctx["conversation_id"], model=args.model or None)
            _print_result(res, as_json=args.as_json)
            return 0
        if args.cmd == "agent":
            stream_mode = getattr(args, "stream", None)
            stream = bool(stream_mode)
            res = client.agent(
                args.prompt,
                project_id=ctx["project_id"],
                conversation_id=ctx["conversation_id"],
                model=args.model or None,
                stream=stream,
                stream_mode=(stream_mode or "compact"),
            )
            if not stream:
                _print_result(res, as_json=args.as_json)
            if not args.as_json and res.agent_status:
                print(f"\n[x_agent_status] {res.agent_status}", file=sys.stderr)
            return 0
        if args.cmd == "multi-agent":
            stream_mode = getattr(args, "stream", None)
            stream = bool(stream_mode)
            res = client.multi_agent(
                args.task,
                project_id=ctx["project_id"],
                conversation_id=ctx["conversation_id"],
                stream=stream,
                stream_mode=(stream_mode or "compact"),
            )
            if not stream:
                _print_result(res, as_json=args.as_json)
            if not args.as_json and res.agent_status:
                print(f"\n[x_agent_status] {res.agent_status}", file=sys.stderr)
            return 0
        if args.cmd == "trace":
            import json as _json
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from app.trace_logger import load_trace, list_traces
            task_id = args.task_id
            if task_id == "list":
                traces = list_traces()
                if not traces:
                    print("No traces found.")
                else:
                    print(f"Available traces ({len(traces)}):")
                    for t in sorted(traces):
                        print(f"  - {t}")
                return 0
            events = load_trace(task_id)
            if not events:
                print(f"No trace found for task_id: {task_id}")
                return 1
            print(f"Trace for {task_id} ({len(events)} events):")
            print("─" * 60)
            for ev in events:
                ts = ev.get('timestamp', 0)
                role = ev.get('role', '?')
                etype = ev.get('event_type', '?')
                tool = ev.get('tool_name', '')
                status = ev.get('status', '')
                preview = ev.get('output_preview', '')[:120]
                icon = "✅" if status == "ok" else "❌" if status == "fail" else "⏳"
                line = f"{icon} [{role}] {etype}"
                if tool:
                    line += f" | {tool}"
                if preview:
                    line += f" | {preview}"
                print(line)
            print("─" * 60)
            return 0
        if args.cmd == "eval":
            import subprocess
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            rc = 0
            if args.suite == "all":
                script = os.path.join(script_dir, "scripts", "eval_staragent_all.sh")
                rc = subprocess.run(["bash", script], cwd=script_dir).returncode
            elif args.suite == "baseline":
                lock_held_by_parent = os.getenv("STARAGENT_LOCK_ACQUIRED_BY_PARENT") == "1"
                lock_file_obj = None
                lock_path = os.path.join(script_dir, ".runtime", "doctor.lock")
                if not lock_held_by_parent:
                    lock_dir = os.path.join(script_dir, ".runtime")
                    os.makedirs(lock_dir, exist_ok=True)
                    lock_file_obj = _acquire_lock(lock_path)
                    if not lock_file_obj:
                        print("Error: Another doctor or eval baseline process is already running (lock held).", file=sys.stderr)
                        return 1
                try:
                    script = os.path.join(script_dir, "scripts", "eval_baseline.sh")
                    rc = subprocess.run(["bash", script], cwd=script_dir).returncode
                finally:
                    if lock_file_obj:
                        _release_lock(lock_file_obj, lock_path)
            elif args.suite == "medium":
                script = os.path.join(script_dir, "scripts", "eval_medium.sh")
                rc = subprocess.run(["bash", script], cwd=script_dir).returncode
            elif args.suite == "repo":
                # The repo tier is managed by eval_tiers.py
                script_path = os.path.join(script_dir, "scripts", "eval_tiers.py")
                rc = subprocess.run(["python3", script_path, "repo"], cwd=script_dir).returncode
            elif args.suite == "stress":
                script = os.path.join(script_dir, "scripts", "eval_stress.sh")
                rc = subprocess.run(["bash", script], cwd=script_dir).returncode
            elif args.suite == "gates":
                test_file = os.path.join(script_dir, "tests", "test_verifier_gates.py")
                rc = subprocess.run(["python3", "-m", "pytest", test_file, "-v"], cwd=script_dir).returncode
            elif args.suite == "skills":
                test_file = os.path.join(script_dir, "tests", "test_skill_library.py")
                rc = subprocess.run(["python3", "-m", "pytest", test_file, "-v"], cwd=script_dir).returncode
            return rc
        if args.cmd == "skills":
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from app import skill_library
            skill_library.init()
            if args.skills_cmd == "ingest":
                stats = skill_library.ingest(args.path)
                print(f"Ingested {stats['total']} skills")
                print(f"Domains: {stats['domains']}")
                if stats['errors']:
                    print(f"Errors: {len(stats['errors'])}")
                    for e in stats['errors'][:5]:
                        print(f"  - {e}")
                return 0
            if args.skills_cmd == "list":
                skills = skill_library.list_all()
                stats = skill_library.get_stats()
                print(f"Total skills: {stats['total_skills']}")
                print(f"Domains: {stats['domains']}")
                print("")
                current_domain = ""
                for s in skills:
                    if s['domain'] != current_domain:
                        current_domain = s['domain']
                        print(f"\n[{current_domain.upper()}]")
                    print(f"  {s['name']}: {s['description'][:80]}")
                return 0
            if args.skills_cmd == "search":
                results = skill_library.search(args.query)
                if not results:
                    print("No skills found.")
                else:
                    print(f"Found {len(results)} skills:")
                    for s in results:
                        print(f"  [{s['domain']}] {s['name']} (score: {s.get('score', 0):.1f})")
                        print(f"    {s['description'][:100]}")
                return 0
            if args.skills_cmd == "show":
                skill = skill_library.show(args.skill_name)
                if not skill:
                    print(f"Skill not found: {args.skill_name}")
                    return 1
                print(f"Name: {skill['name']}")
                print(f"Domain: {skill['domain']}")
                print(f"Source: {skill['source_repo']} / {skill['source_path']}")
                print(f"License: {skill['license']}")
                print(f"Tags: {', '.join(skill.get('tags', []))}")
                print(f"Tools: {len(skill.get('tools', []))}")
                print("\n--- SKILL.md (first 500 chars) ---")
                print(skill.get('skill_md_content', '')[:500])
                return 0
            if args.skills_cmd == "eval":
                import subprocess
                script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                test_file = os.path.join(script_dir, "tests", "test_skill_library.py")
                subprocess.run(["python3", "-m", "pytest", test_file, "-v"], cwd=script_dir)
                return 0
        if args.cmd == "approve":
            res = client.approve(project_id=ctx["project_id"], conversation_id=ctx["conversation_id"], model=args.model or None)
            _print_result(res, as_json=args.as_json)
            return 0
        if args.cmd == "reject":
            res = client.reject(project_id=ctx["project_id"], conversation_id=ctx["conversation_id"], model=args.model or None)
            _print_result(res, as_json=args.as_json)
            return 0
        if args.cmd == "continue":
            res = client.continue_task(project_id=ctx["project_id"], conversation_id=ctx["conversation_id"], model=args.model or None)
            _print_result(res, as_json=args.as_json)
            return 0
        if args.cmd == "rollback":
            res = client.rollback(project_id=ctx["project_id"], conversation_id=ctx["conversation_id"], model=args.model or None)
            _print_result(res, as_json=args.as_json)
            return 0
        if args.cmd == "status":
            health = client.health()
            models = client.models()
            out = {"health": health, "models": models, "context": ctx, "base_url": client.v1_base_url}
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "smoke-test":
            out = client.smoke_test_compact()
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "doctor":
            return _run_doctor(client, ctx, as_json=args.as_json)
        if args.cmd == "preset":
            if args.preset_cmd == "list":
                out = client.presets_list()
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                packs = out.get("packs") or []
                if packs:
                    print("Preset packs:")
                    for pk in packs:
                        ro = "RO" if pk.get("read_only") else "STATEFUL"
                        appr = " (may require approval)" if pk.get("may_require_approval") else ""
                        primary = pk.get("primary_artifact") or "-"
                        presets = ",".join(pk.get("presets") or [])
                        print(f"  {pk.get('name')}  [{ro}]  primary={primary}{appr}  presets={presets}")
                    print("")
                presets = out.get("presets") or []
                if not presets:
                    print("(no presets found)")
                    return 0
                print("Presets:")
                for p in presets:
                    ro = "RO" if p.get("read_only") else "STATEFUL"
                    appr = " (may require approval)" if p.get("may_require_approval") else ""
                    primary = p.get("primary_artifact") or "-"
                    outs = p.get("expected_outputs") or []
                    outs_s = ", ".join(outs[:6]) + (" ..." if len(outs) > 6 else "")
                    print(f"  {p.get('name')}  [{ro}]  primary={primary}{appr}")
                    desc = (p.get("description") or "").strip()
                    if desc:
                        print(f"    - {desc}")
                    if outs_s:
                        print(f"    - outputs: {outs_s}")
                return 0
            if args.preset_cmd == "packs":
                out = client.preset_packs_list()
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                packs = out.get("packs") or []
                if not packs:
                    print("(no preset packs found)")
                    return 0
                for pk in packs:
                    ro = "RO" if pk.get("read_only") else "STATEFUL"
                    appr = " (may require approval)" if pk.get("may_require_approval") else ""
                    primary = pk.get("primary_artifact") or "-"
                    presets = ", ".join(pk.get("presets") or [])
                    outs = ", ".join((pk.get("expected_outputs") or [])[:6])
                    print(f"{pk.get('name')}  [{ro}]  primary={primary}{appr}")
                    if pk.get("description"):
                        print(f"  - {pk.get('description')}")
                    print(f"  - presets: {presets}")
                    if outs:
                        print(f"  - outputs: {outs}")
                return 0
            if args.preset_cmd == "examples":
                base = client.v1_base_url
                print("Examples (CLI):")
                print("")
                print("Read-only repo onboarding (pack):")
                print("  ./scripts/staragent --project demo --conversation onboard-1 preset pack-run repo_onboarding --path . --question \"What are the entry points and request flow?\"")
                print("")
                print("Deep codebase audit (pack):")
                print("  ./scripts/staragent --project demo --conversation audit-1 preset pack-run codebase_audit --path . --question \"Call out risks and unknowns.\"")
                print("")
                print("Bug investigation (pack):")
                print("  ./scripts/staragent --project demo --conversation bug-1 preset pack-run bug_investigation --path . --issue \"Agent path returns fallback message.\"")
                print("")
                print("Docs digest (pack):")
                print("  ./scripts/staragent --project demo --conversation docs-1 preset pack-run docs_digest --path docs --question \"Summarize how to run StarAgent.\"")
                print("")
                print("Release prep (stateful pack; approval-gated export):")
                print("  ./scripts/staragent --project demo --conversation rel-1 preset pack-run release_prep --path . --output sandbox_test/release_prep.md")
                print("  ./scripts/staragent --project demo --conversation rel-1 task approve <task_id>")
                print("")
                print("JSON dataset theme report (preset; read-only):")
                print("  ./scripts/staragent --project demo --conversation ds-1 preset run dataset_theme_report --path /path/to/dataset_folder")
                print("  ./scripts/staragent --project demo --conversation ds-1 task continue <task_id> --steps 3 --duration 20")
                print("")
                print("API base URL:")
                print(f"  {base}")
                return 0
            if args.preset_cmd == "run":
                if args.path is None:
                    args.path = os.getcwd()
                out = client.preset_run(
                    str(args.name),
                    project_id=ctx["project_id"],
                    conversation_id=ctx["conversation_id"],
                    path=args.path,
                    question=args.question,
                    issue=args.issue,
                    goal=args.goal,
                    files=args.files,
                    logs=args.logs,
                    mode=args.mode,
                    output_path=args.output_path,
                    max_steps=args.max_steps,
                    max_retries=args.max_retries,
                    run_now=bool(args.run_now),
                )
                # Remember task id for follow-up.
                tid = (out.get("task") or {}).get("task_id") or (out.get("task_id"))
                if tid:
                    _remember_last_task(str(tid))
                if args.as_json and not getattr(args, "wait", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                preset = out.get("preset") or {}
                task = out.get("task") or {}
                if not getattr(args, "wait", False):
                    print(f"preset: {preset.get('name')}")
                    print(f"task_id: {task.get('task_id')}")
                    print(f"status:  {task.get('status')}")
                    pa = out.get("primary_artifact") or (out.get("data") or {}).get("primary_artifact")  # defensive
                    if pa:
                        print(f"primary: {pa.get('name')} ({'ok' if pa.get('exists') else 'missing'})")
                    if out.get("action_required"):
                        ar = out.get("action_required") or {}
                        print(f"action_required: {ar.get('type')}")
                
                if tid and getattr(args, "wait", False):
                    return _wait_for_task(client, str(tid), getattr(args, "print_primary", False), args.as_json)
                return 0
            if args.preset_cmd == "pack-run":
                if args.path is None:
                    args.path = os.getcwd()
                out = client.preset_pack_run(
                    str(args.name),
                    project_id=ctx["project_id"],
                    conversation_id=ctx["conversation_id"],
                    path=args.path,
                    question=args.question,
                    issue=args.issue,
                    goal=args.goal,
                    files=args.files,
                    logs=args.logs,
                    mode=args.mode,
                    output_path=args.output_path,
                    max_steps=args.max_steps,
                    max_retries=args.max_retries,
                    run_now=bool(args.run_now),
                )
                # Remember last task id: prefer the last run that has a task_id.
                last_tid = None
                for r in (out.get("runs") or [])[::-1]:
                    tid = (r.get("task") or {}).get("task_id")
                    if tid:
                        last_tid = tid
                        break
                if last_tid:
                    _remember_last_task(str(last_tid))
                if args.as_json and not getattr(args, "wait", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                pack = out.get("pack") or {}
                if not getattr(args, "wait", False):
                    print(f"pack: {pack.get('name')}")
                    if pack.get("description"):
                        print(f"desc: {pack.get('description')}")
                    if out.get("action_required"):
                        ar = out.get("action_required") or {}
                        print(f"action_required: {ar.get('type')}")
                    runs = out.get("runs") or []
                    for i, r in enumerate(runs):
                        preset = (r.get("preset") or {}).get("name") or "?"
                        task = r.get("task") or {}
                        print(f"- step {i+1}: {preset}  task_id={task.get('task_id')}  status={task.get('status')}")
                
                if last_tid and getattr(args, "wait", False):
                    return _wait_for_task(client, str(last_tid), getattr(args, "print_primary", False), args.as_json)
                return 0
            raise SystemExit(f"unknown preset command: {args.preset_cmd}")
        if args.cmd == "task":
            if args.task_cmd == "create":
                out = client.task_create(
                    user_goal=args.goal,
                    task_type=args.task_type,
                    definition_of_done=args.dod,
                    project_id=ctx["project_id"],
                    conversation_id=ctx["conversation_id"],
                    max_steps=args.max_steps,
                    max_retries=args.max_retries,
                    run_now=bool(args.run_now),
                )
                # Remember task id for follow-up commands.
                tid = (out.get("task") or {}).get("task_id") or (out.get("task_id"))
                if tid:
                    _remember_last_task(str(tid))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.task_cmd == "status":
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                out = client.task_status(str(tid))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.task_cmd == "continue":
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                out = client.task_action(str(tid), action="continue", max_step_advances=args.max_step_advances, max_duration_s=args.max_duration_s)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.task_cmd == "approve":
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                out = client.task_action(str(tid), action="approve")
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.task_cmd == "reject":
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                out = client.task_action(str(tid), action="reject", reason=args.reason)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.task_cmd == "artifacts":
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                out = client.task_artifacts(str(tid))
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(f"task_id: {out.get('task_id')}")
                print(f"dir:     {out.get('artifact_dir')}")
                primary = out.get("primary_artifact") or {}
                if primary:
                    marker = "ok" if primary.get("exists") else "missing"
                    print(f"primary: {primary.get('name')} ({marker})")
                files = out.get("files") or []
                if not files:
                    print("(no artifacts found)")
                    return 0
                for f in files:
                    name = f.get("name") or ""
                    ftype = f.get("type") or ""
                    size = f.get("size_bytes")
                    size_s = f"{size}B" if isinstance(size, int) else ""
                    prefix = "*" if f.get("is_primary") else " "
                    print(f"{prefix} {name}  {ftype:>8}  {size_s:>10}")
                return 0
            if args.task_cmd == "list":
                out = client.task_list(
                    project_id=ctx["project_id"],
                    conversation_id=args.conversation_id or None,
                    status=args.status,
                    limit=args.limit,
                    offset=args.offset,
                )
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                tasks = out.get("tasks") or []
                if not tasks:
                    print("(no tasks found)")
                    return 0
                for t in tasks:
                    primary = t.get("primary_artifact_name") or _primary_name_for_type(t.get("task_type"))
                    print(
                        f"{t.get('task_id')}  {t.get('status'):>9}  {t.get('task_type'):>10}  step={t.get('current_step_index')}/{t.get('max_steps')}  retry={t.get('retry_count')}  primary={primary or '-'}  updated={_fmt_dt(t.get('updated_at'))}"
                    )
                return 0
            if args.task_cmd in {"inspect", "show"}:
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                out = client.task_inspect(str(tid))
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                task = out.get("task") or {}
                prog = out.get("progress") or {}
                counts = prog.get("counts") or {}
                cur = prog.get("current_step") or {}
                time_meta = out.get("time") or {}
                primary = out.get("primary_artifact") or {}
                meta = out.get("task_meta") or {}
                show_task_id = task.get("task_id") or out.get("task_id") or str(tid)
                show_status = task.get("status") or out.get("status") or "-"
                print(f"task_id: {show_task_id}")
                print(f"status:  {show_status}  type={task.get('task_type') or '-'}  retry={task.get('retry_count') if task.get('retry_count') is not None else '-'}  age={_fmt_age_s(time_meta.get('age_s'))}")
                print(f"steps:   {counts.get('completed', 0)}/{counts.get('total', 0)}  ({prog.get('percent_complete', 0)}%)")
                if primary:
                    marker = "ok" if primary.get("exists") else "missing"
                    print(f"primary: {primary.get('name')} ({marker})")
                _print_dataset_meta((meta.get("dataset_meta")) or out.get("dataset_meta"))
                if cur:
                    print(f"current: #{cur.get('step_index')} {cur.get('step_type')} [{cur.get('status')}]")
                    instr = (cur.get('instruction') or '')
                    if instr:
                        print(f"instr:   {instr[:200]}")
                if args.task_cmd == "show":
                    primary_report = out.get("primary_report")
                    if isinstance(primary_report, str) and primary_report.strip():
                        print("\n" + primary_report)
                return 0
            if args.task_cmd == "summary":
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                out = client.task_summary(str(tid))
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                task = out.get("task") or {}
                prog = out.get("progress") or {}
                counts = prog.get("counts") or {}
                time_meta = out.get("time") or {}
                primary = out.get("primary_artifact") or {}
                meta = out.get("task_meta") or {}
                show_task_id = task.get("task_id") or out.get("task_id") or str(tid)
                show_status = task.get("status") or out.get("status") or "-"
                show_verdict = task.get("final_verdict") or out.get("verdict") or "-"
                print(f"task_id: {show_task_id}")
                print(f"status:  {show_status}  verdict={show_verdict}  retry={task.get('retry_count') if task.get('retry_count') is not None else '-'}  age={_fmt_age_s(time_meta.get('age_s'))}")
                print(f"progress:{counts.get('completed', 0)}/{counts.get('total', 0)} ({prog.get('percent_complete', 0)}%)")
                if primary:
                    marker = "ok" if primary.get("exists") else "missing"
                    print(f"primary: {primary.get('name')} ({marker})")
                _print_dataset_meta((meta.get("dataset_meta")) or out.get("dataset_meta"))
                summary_text = task.get("final_summary") or out.get("summary")
                if summary_text:
                    print("\n" + str(summary_text))
                return 0
            if args.task_cmd == "logs":
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                out = client.task_logs(str(tid), tail_steps=args.tail_steps)
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                logs = out.get("logs")
                if isinstance(logs, str):
                    print(logs)
                    return 0
                if isinstance(logs, list):
                    for item in logs:
                        if isinstance(item, dict):
                            si = item.get("step_index")
                            st = item.get("status")
                            ty = item.get("step_type")
                            att = item.get("attempt_count")
                            print(f"#{si} {ty} [{st}] attempts={att}")
                            osum = item.get("output_summary")
                            if osum:
                                print(str(osum)[:400].rstrip() + ("\n" if len(str(osum)) <= 400 else "\n..."))
                        else:
                            print(str(item))
                    return 0
                if logs is not None:
                    print(str(logs))
                return 0
            if args.task_cmd == "artifact":
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                if not args.name:
                    raise SystemExit("artifact name required (e.g. final_report.md)")
                tail_lines = args.tail_lines
                if tail_lines is None:
                    tail_lines = 0 if (args.format or "text") == "json" else 200
                out = client.task_artifact_preview(str(tid), str(args.name), format=args.format, tail_lines=int(tail_lines))
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(out.get("content") or "")
                return 0
            raise SystemExit(f"unknown task command: {args.task_cmd}")

        if args.cmd == "workflow":
            if args.workflow_cmd == "list":
                out = client.workflows_list()
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                workflows = out.get("workflows") or []
                print("AVAILABLE WORKFLOWS:")
                for w in workflows:
                    print(f"- {w['name']}: {w['description']} ({w['stages_count']} stages)")
                return 0

            if args.workflow_cmd == "inspect":
                out = client.workflow_inspect(args.name)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(f"Workflow: {out.get('name')}")
                print(f"Description: {out.get('description')}")
                print("Stages:")
                for s in out.get("stages") or []:
                    app_req = " (requires approval)" if s.get("approval_required") else ""
                    print(f"  - stage: {s['name']}{app_req}")
                    print(f"    purpose: {s.get('purpose')}")
                    print(f"    allowed tools: {', '.join(s.get('allowed_tools') or [])}")
                    if s.get("required_outputs"):
                        print(f"    required outputs: {', '.join(s.get('required_outputs'))}")
                return 0

            if args.workflow_cmd == "run":
                out = client.workflow_run(
                    name=args.name,
                    project_id=ctx["project_id"],
                    conversation_id=ctx["conversation_id"],
                    goal=args.goal,
                    definition_of_done=args.dod
                )
                task_id = out.get("task_id")
                if task_id:
                    _remember_last_task(task_id)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(f"Started workflow task: {task_id}")
                task_data = out.get("task") or {}
                print(f"Status: {task_data.get('status')}")
                print(f"Summary: {task_data.get('final_summary') or 'Execution started.'}")
                return 0

            if args.workflow_cmd == "create":
                out = client.workflow_create(name=args.name, description=args.description)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                if out.get("ok"):
                    print(f"Successfully created custom workflow '{args.name}' under .staragent/workflows/{args.name}")
                else:
                    print(f"Failed to create workflow: {out.get('detail')}")
                return 0

            if args.workflow_cmd == "resume":
                out = client.workflow_resume(task_id=args.task_id, stage=args.stage)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                task_data = out.get("task") or {}
                print(f"Resumed workflow task: {args.task_id}")
                print(f"Status: {task_data.get('status')}")
                print(f"Summary: {task_data.get('final_summary') or 'Execution continued.'}")
                return 0

            if args.workflow_cmd == "checkpoints":
                out = client.workflow_checkpoints(args.task_id)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                checkpoints = out.get("checkpoints") or []
                print(f"Checkpoints for task {args.task_id}:")
                for cp in checkpoints:
                    print(f"- stage: {cp['stage_name']} (index={cp['stage_index']})")
                    print(f"  status: {cp['status']}")
                    print(f"  created at: {cp['created_at']}")
                    if cp.get("files_produced"):
                        print(f"  files produced: {', '.join(cp['files_produced'])}")
                return 0

            if args.workflow_cmd == "graph":
                out = client.workflow_graph(args.name)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(out.get("graph") or f"No graph for workflow {args.name}")
                return 0

            if args.workflow_cmd == "runs":
                out = client.workflow_runs()
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                runs = out.get("runs") or []
                print("WORKFLOW RUNS:")
                for r in runs:
                    print(f"- Run: {r['run_id']} | Workflow: {r['workflow_name']} | Status: {r['status']} | Stage Index: {r['current_stage_index']}")
                    print(f"  Goal: {r['user_goal']}")
                return 0

            if args.workflow_cmd == "status":
                out = client.workflow_run_status(args.run_id)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(f"Workflow Run: {out.get('run_id')} | Workflow: {out.get('workflow_name')}")
                print(f"Status: {out.get('status')} | Current Stage Index: {out.get('current_stage_index')}")
                print("Stages:")
                for s in out.get("stages") or []:
                    print(f"  - {s['stage_name']}: {s['status']}")
                return 0

            if args.workflow_cmd == "trace":
                out = client.workflow_run_trace(args.run_id)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                events = out.get("events") or []
                print(f"Trace for Run {args.run_id}:")
                for e in events:
                    print(f"[{e.get('timestamp')}] [{e.get('stage_name')}] Tool: {e.get('tool_name')}")
                    print(f"  Args: {e.get('arguments')}")
                    print(f"  Output: {e.get('output')[:100]}...")
                return 0

            if args.workflow_cmd == "state":
                out = client.workflow_run_state(args.run_id)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(f"State for Run {args.run_id}:")
                print("Variables:")
                print(json.dumps(out.get("variables"), indent=2))
                print("Model Selections:")
                print(json.dumps(out.get("model_selections"), indent=2))
                return 0

            if args.workflow_cmd == "gates":
                out = client.workflow_run_gates(args.run_id)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(f"Gate Results for Run {args.run_id}:")
                gate_results = out.get("gate_results") or {}
                for stage_name, res in gate_results.items():
                    print(f"Stage '{stage_name}' Verification:")
                    print(f"  Success: {res.get('success')} | Status: {res.get('status')}")
                    for r in res.get("results", []):
                        icon = "✅" if r["status"] == "pass" else ("⚠️" if r["status"] == "warning" else "❌")
                        print(f"    {icon} {r['type']}: {r['message']}")
                return 0

            if args.workflow_cmd == "approve":
                out = client.workflow_run_approve(args.run_id, stage=args.stage)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(out.get("message") or f"Approved stage for run {args.run_id}")
                return 0

            if args.workflow_cmd == "reject":
                out = client.workflow_run_reject(args.run_id, stage=args.stage)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(out.get("message") or f"Rejected stage for run {args.run_id}")
                return 0

            if args.workflow_cmd == "explain":
                out = client.workflow_explain(args.name)
                if getattr(args, "as_json", False):
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(out.get("explanation") or f"No explanation for workflow {args.name}")
                return 0

        if args.cmd == "research":
            if args.research_cmd == "run":
                out = client.research_run(
                    path=args.path,
                    question=args.question,
                    mode=args.mode,
                    project_id=ctx["project_id"],
                    conversation_id=ctx["conversation_id"],
                    max_steps=args.max_steps,
                    run_now=bool(args.run_now),
                )
                tid = (out.get("task") or {}).get("task_id") or (out.get("task_id"))
                if tid:
                    _remember_last_task(str(tid))
                if tid and getattr(args, "wait", False):
                    return _wait_for_task(client, str(tid), getattr(args, "print_primary", False), args.as_json)
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.research_cmd == "status":
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                out = client.task_status(str(tid))
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            raise SystemExit(f"unknown research command: {args.research_cmd}")

        if args.cmd == "docs":
            if args.docs_cmd == "ingest":
                out = client.docs_ingest(args.path, source_type=args.source_type, project_id=ctx["project_id"])
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.docs_cmd == "ingest-package":
                out = client.docs_ingest_package(args.package_name, manager=args.manager, project_id=ctx["project_id"])
                print(json.dumps(out, ensure_ascii=False, indent=2))
                return 0
            if args.docs_cmd == "search":
                out = client.docs_search(args.query, package_name=args.package_name, max_results=args.max_results, project_id=ctx["project_id"])
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                results = out.get("results", [])
                if not results:
                    print(f"No results found for '{args.query}'")
                    return 0
                for r in results:
                    print(f"\n--- {r.get('title')} ({r.get('source_type')}) ---")
                    if r.get('heading') and r.get('heading') != "None":
                        print(f"Section: {r.get('heading')}")
                    content = r.get('content', '')[:1000]
                    print(content + ("..." if len(r.get('content', '')) > 1000 else ""))
                    if r.get('code_examples'):
                        print("\nCode Examples:")
                        print(r.get('code_examples'))
                return 0
            if args.docs_cmd == "ask":
                out = client.docs_ask(args.question, package_name=args.package_name, max_results=args.max_results, project_id=ctx["project_id"])
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                print(out.get("answer", ""))
                citations = out.get("citations") or []
                if citations:
                    print("\nCitations:")
                    for c in citations:
                        src = c.get("source_path") or c.get("path_or_url")
                        print(f"- {src}#chunk={c.get('chunk_id')}")
                return 0
            raise SystemExit(f"unknown docs command: {args.docs_cmd}")

        if args.cmd == "repo-audit":
            out = client.repo_audit_run(
                path=args.path,
                question=args.question,
                project_id=ctx["project_id"],
                conversation_id=ctx["conversation_id"],
                max_steps=args.max_steps,
                run_now=bool(args.run_now),
            )
            tid = (out.get("task") or {}).get("task_id") or (out.get("task_id"))
            if tid:
                _remember_last_task(str(tid))
            if tid and getattr(args, "wait", False):
                return _wait_for_task(client, str(tid), getattr(args, "print_primary", False), args.as_json)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        if args.cmd == "issue-triage":
            out = client.issue_triage_run(
                path=args.path,
                issue=args.issue,
                files=args.files,
                logs=args.logs,
                project_id=ctx["project_id"],
                conversation_id=ctx["conversation_id"],
                max_steps=args.max_steps,
                run_now=bool(args.run_now),
            )
            tid = (out.get("task") or {}).get("task_id") or (out.get("task_id"))
            if tid:
                _remember_last_task(str(tid))
            if tid and getattr(args, "wait", False):
                return _wait_for_task(client, str(tid), getattr(args, "print_primary", False), args.as_json)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        if args.cmd == "write":
            out = client.write_run(
                path=args.path,
                goal=args.goal,
                files=args.files,
                project_id=ctx["project_id"],
                conversation_id=ctx["conversation_id"],
                max_steps=args.max_steps,
                run_now=bool(args.run_now),
            )
            tid = (out.get("task") or {}).get("task_id") or (out.get("task_id"))
            if tid:
                _remember_last_task(str(tid))
            if tid and getattr(args, "wait", False):
                return _wait_for_task(client, str(tid), getattr(args, "print_primary", False), args.as_json)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        raise SystemExit(f"unknown command: {args.cmd}")
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
