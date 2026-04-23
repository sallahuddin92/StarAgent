from __future__ import annotations

import argparse
import json
import os
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

    sub = p.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="Fast-path ask (forced fast route)")
    ask.add_argument("prompt")

    agent = sub.add_parser("agent", help="Agent-path task (forced agent route)")
    agent.add_argument("prompt")

    sub.add_parser("approve", help="Approve pending action (send yes)")
    sub.add_parser("reject", help="Reject pending action (send no)")
    sub.add_parser("continue", help="Continue pending partial task (send continue)")

    sub.add_parser("status", help="Print health/models/context")
    sub.add_parser("rollback", help="Best-effort rollback via agent path")
    sub.add_parser("smoke-test", help="Run compact smoke test against the API")

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

    research = sub.add_parser("research", help="Document research mode (runs as a task)")
    research_sub = research.add_subparsers(dest="research_cmd", required=True)

    research_run = research_sub.add_parser("run", help="Run document research on a folder")
    research_run.add_argument("--path", dest="path", required=True, help="Folder path to ingest")
    research_run.add_argument("--question", dest="question", default=None, help="Research question")
    research_run.add_argument("--mode", dest="mode", default="research", help="Mode: summary|research|comparison")
    research_run.add_argument("--max-steps", dest="max_steps", type=int, default=60)
    research_run.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")

    research_status = research_sub.add_parser("status", help="Get research task status")
    research_status.add_argument("task_id", nargs="?", help="Task id (default: last task)")

    repo_audit = sub.add_parser("repo-audit", help="Repo audit profile (runs as a task)")
    repo_audit.add_argument("--path", dest="path", required=True, help="Repository/project path to audit")
    repo_audit.add_argument("--question", dest="question", default=None, help="Optional audit question")
    repo_audit.add_argument("--max-steps", dest="max_steps", type=int, default=25)
    repo_audit.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")

    issue_triage = sub.add_parser("issue-triage", help="Issue triage profile (runs as a task)")
    issue_triage.add_argument("--path", dest="path", required=True, help="Repository/project path")
    issue_triage.add_argument("--issue", dest="issue", required=True, help="Issue description")
    issue_triage.add_argument("--file", dest="files", action="append", default=None, help="Relevant file path (repeatable)")
    issue_triage.add_argument("--log", dest="logs", action="append", default=None, help="Relevant log path (repeatable)")
    issue_triage.add_argument("--max-steps", dest="max_steps", type=int, default=25)
    issue_triage.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")

    writing = sub.add_parser("write", help="Writing profile (runs as a task)")
    writing.add_argument("--path", dest="path", required=True, help="Folder path containing notes/docs")
    writing.add_argument("--goal", dest="goal", required=True, help="Writing objective")
    writing.add_argument("--file", dest="files", action="append", default=None, help="Specific source file path (repeatable)")
    writing.add_argument("--max-steps", dest="max_steps", type=int, default=25)
    writing.add_argument("--no-run", dest="run_now", action="store_false", help="Only create; do not run now")
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


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = _resolve_context(args)
    client = _client_from_args(args)
    try:
        if args.cmd == "ask":
            res = client.ask(args.prompt, project_id=ctx["project_id"], conversation_id=ctx["conversation_id"], model=args.model or None)
            _print_result(res, as_json=args.as_json)
            return 0
        if args.cmd == "agent":
            res = client.agent(args.prompt, project_id=ctx["project_id"], conversation_id=ctx["conversation_id"], model=args.model or None)
            _print_result(res, as_json=args.as_json)
            if not args.as_json and res.agent_status:
                print(f"\n[x_agent_status] {res.agent_status}", file=sys.stderr)
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
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                preset = out.get("preset") or {}
                task = out.get("task") or {}
                print(f"preset: {preset.get('name')}")
                print(f"task_id: {task.get('task_id')}")
                print(f"status:  {task.get('status')}")
                pa = out.get("primary_artifact") or (out.get("data") or {}).get("primary_artifact")  # defensive
                if pa:
                    print(f"primary: {pa.get('name')} ({'ok' if pa.get('exists') else 'missing'})")
                if out.get("action_required"):
                    ar = out.get("action_required") or {}
                    print(f"action_required: {ar.get('type')}")
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
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                pack = out.get("pack") or {}
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
            if args.task_cmd == "inspect":
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
                print(f"task_id: {task.get('task_id')}")
                print(f"status:  {task.get('status')}  type={task.get('task_type')}  retry={task.get('retry_count')}  age={_fmt_age_s(time_meta.get('age_s'))}")
                print(f"steps:   {counts.get('completed')}/{counts.get('total')}  ({prog.get('percent_complete')}%)")
                if primary:
                    marker = "ok" if primary.get("exists") else "missing"
                    print(f"primary: {primary.get('name')} ({marker})")
                _print_dataset_meta((meta.get("dataset_meta")) or out.get("dataset_meta"))
                if cur:
                    print(f"current: #{cur.get('step_index')} {cur.get('step_type')} [{cur.get('status')}]")
                    instr = (cur.get('instruction') or '')
                    if instr:
                        print(f"instr:   {instr[:200]}")
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
                print(f"task_id: {task.get('task_id')}")
                print(f"status:  {task.get('status')}  verdict={task.get('final_verdict')}  retry={task.get('retry_count')}  age={_fmt_age_s(time_meta.get('age_s'))}")
                print(f"progress:{counts.get('completed')}/{counts.get('total')} ({prog.get('percent_complete')}%)")
                if primary:
                    marker = "ok" if primary.get("exists") else "missing"
                    print(f"primary: {primary.get('name')} ({marker})")
                _print_dataset_meta((meta.get("dataset_meta")) or out.get("dataset_meta"))
                if task.get("final_summary"):
                    print("\n" + str(task.get("final_summary")))
                return 0
            if args.task_cmd == "logs":
                tid = _resolve_task_id(args)
                if not tid:
                    raise SystemExit("task_id required (no stored last_task_id)")
                out = client.task_logs(str(tid), tail_steps=args.tail_steps)
                if args.as_json:
                    print(json.dumps(out, ensure_ascii=False, indent=2))
                    return 0
                for s in out.get("logs") or []:
                    si = s.get("step_index")
                    st = s.get("status")
                    ty = s.get("step_type")
                    att = s.get("attempt_count")
                    print(f"#{si} {ty} [{st}] attempts={att}")
                    osum = s.get("output_summary")
                    if osum:
                        print(str(osum)[:400].rstrip() + ("\n" if len(str(osum)) <= 400 else "\n..."))
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
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        raise SystemExit(f"unknown command: {args.cmd}")
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
