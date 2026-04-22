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


def _print_result(result, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(result.message)


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
        raise SystemExit(f"unknown command: {args.cmd}")
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
