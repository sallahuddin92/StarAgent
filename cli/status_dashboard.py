"""StarAgent Status Dashboard (v0.8.7).

Compact terminal dashboard showing system readiness, memory health,
prompt efficiency, runtime eval status, and dependency status.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("RUNTIME_EVAL_BASE_URL", "http://127.0.0.1:8081")
API_KEY = os.getenv("PROXY_API_KEY", "local-dev-key")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
PROJECT_ID = os.getenv("STARAGENT_DEFAULT_PROJECT", "default")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

REPORT_PATH = Path("data/runtime_eval_report.json")
AUDIT_PATH = Path("data/last_prompt_audit.json")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class SectionStatus:
    """A named section with a status label and detail lines."""
    title: str
    status: str  # "ok" | "warn" | "error" | "info"
    lines: List[str] = field(default_factory=list)
    details: List[str] = field(default_factory=list)


@dataclass
class DashboardReport:
    """Full dashboard state, serializable to JSON."""
    timestamp: str
    proxy: Dict[str, Any]
    ollama: Dict[str, Any]
    prompt_mode: Dict[str, Any]
    memory_authority: Dict[str, Any]
    prompt_audit: Dict[str, Any]
    runtime_eval: Dict[str, Any]
    doctor: Dict[str, Any]
    suggested_action: str


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

def _get_http_client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=10.0)


def check_proxy_health() -> Dict[str, Any]:
    """Check if the proxy server is running and healthy."""
    try:
        with _get_http_client() as client:
            resp = client.get("/health")
            resp.raise_for_status()
            data = resp.json()
            return {
                "status": "ok",
                "service": data.get("service", "unknown"),
                "version": data.get("version", "?"),
                "detail": f"{data.get('service', 'unknown')} v{data.get('version', '?')}",
            }
    except httpx.ConnectError:
        return {"status": "error", "service": "", "version": "", "detail": f"Server unreachable at {BASE_URL}"}
    except Exception as e:
        return {"status": "error", "service": "", "version": "", "detail": str(e)}


def check_ollama() -> Dict[str, Any]:
    """Check Ollama availability via /api/tags."""
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("models", [])
            model_names = [m.get("name", "") for m in models] if models else []
            return {
                "status": "ok",
                "detail": f"Available ({len(model_names)} models)",
                "models": model_names[:5],  # first 5
                "model_count": len(model_names),
            }
        else:
            return {"status": "skip", "detail": f"HTTP {resp.status_code}", "models": [], "model_count": 0}
    except Exception as e:
        return {"status": "skip", "detail": str(e), "models": [], "model_count": 0}


def check_prompt_mode() -> Dict[str, Any]:
    """Fetch prompt mode config from the API."""
    try:
        with _get_http_client() as client:
            resp = client.get("/v1/config/prompt-mode")
            resp.raise_for_status()
            data = resp.json()
            return {
                "status": "ok",
                "mode": data.get("prompt_mode", "unknown"),
                "max_memory_tokens": data.get("max_memory_tokens", 0),
                "include_historical": data.get("include_historical_default", False),
                "detail": f"mode={data.get('prompt_mode', '?')}, max_tokens={data.get('max_memory_tokens', '?')}",
            }
    except Exception as e:
        return {"status": "error", "mode": "", "max_memory_tokens": 0, "include_historical": False, "detail": str(e)}


def check_memory_authority() -> Dict[str, Any]:
    """Query memory authority counts from the API."""
    counts: Dict[str, int] = {"active": 0, "stale": 0, "superseded": 0, "rejected": 0, "unknown": 0}
    try:
        with _get_http_client() as client:
            resp = client.get(f"/v1/memory/items?project_id={PROJECT_ID}")
            resp.raise_for_status()
            data = resp.json()
            items = data if isinstance(data, list) else data.get("items", [])
            for item in items:
                status = (item.get("status") or "unknown").lower()
                if status in counts:
                    counts[status] += 1
                else:
                    counts["unknown"] += 1
            total = sum(counts.values())
            return {
                "status": "ok" if total > 0 else "info",
                "counts": counts,
                "total": total,
                "detail": f"{total} items across {sum(1 for v in counts.values() if v > 0)} statuses",
            }
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            # Endpoint may not exist; try DB directly
            return {"status": "info", "counts": counts, "total": 0, "detail": "API endpoint not available"}
        return {"status": "error", "counts": counts, "total": 0, "detail": str(e)}
    except Exception as e:
        return {"status": "error", "counts": counts, "total": 0, "detail": str(e)}


def read_prompt_audit_file() -> Dict[str, Any]:
    """Read last prompt audit report from disk if it exists."""
    if not AUDIT_PATH.exists():
        return {"status": "info", "detail": "No audit file found", "audit": {}}
    try:
        data = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
        audit = data.get("audit", data)
        mode = audit.get("mode", "?")
        tokens = audit.get("estimated_tokens_total", "?")
        sections = audit.get("sections_kept", [])
        return {
            "status": "ok",
            "detail": f"mode={mode}, {tokens} tokens, {len(sections)} sections",
            "audit": audit,
        }
    except Exception as e:
        return {"status": "error", "detail": str(e), "audit": {}}


def read_runtime_eval_report() -> Dict[str, Any]:
    """Read the last runtime eval report from disk if it exists."""
    if not REPORT_PATH.exists():
        return {"status": "info", "detail": "No eval report found", "report": {}}
    try:
        data = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        score = data.get("score", "?")
        passed = data.get("passed", 0)
        failed = data.get("failed", 0)
        skipped = data.get("skipped", 0)
        errors = data.get("errors", 0)
        return {
            "status": "ok" if failed == 0 and errors == 0 else "warn" if skipped > 0 else "error",
            "detail": f"PASS={passed} FAIL={failed} SKIP={skipped} ERR={errors} score={score}",
            "report": data,
            "summary": {"passed": passed, "failed": failed, "skipped": skipped, "blocked": data.get("blocked", 0), "errors": errors},
        }
    except Exception as e:
        return {"status": "error", "detail": str(e), "report": {}}


def run_doctor_checks() -> Dict[str, Any]:
    """Run a subset of doctor checks (health, ollama, config)."""
    checks: List[Dict[str, Any]] = []
    passed = 0
    failed = 0

    # Check 1: Proxy health
    proxy = check_proxy_health()
    if proxy["status"] == "ok":
        checks.append({"name": "proxy_health", "status": "pass", "detail": proxy["detail"]})
        passed += 1
    else:
        checks.append({"name": "proxy_health", "status": "fail", "detail": proxy["detail"]})
        failed += 1

    # Check 2: Ollama
    ollama = check_ollama()
    if ollama["status"] == "ok":
        checks.append({"name": "ollama", "status": "pass", "detail": ollama["detail"]})
        passed += 1
    else:
        checks.append({"name": "ollama", "status": "skip", "detail": ollama["detail"]})

    # Check 3: Config endpoint
    pm = check_prompt_mode()
    if pm["status"] == "ok":
        checks.append({"name": "prompt_mode_config", "status": "pass", "detail": pm["detail"]})
        passed += 1
    else:
        checks.append({"name": "prompt_mode_config", "status": "fail", "detail": pm["detail"]})
        failed += 1

    # Check 4: Eval report
    eval_report = read_runtime_eval_report()
    if eval_report["status"] == "ok":
        checks.append({"name": "runtime_eval", "status": "pass", "detail": eval_report["detail"]})
        passed += 1
    elif eval_report["status"] == "info":
        checks.append({"name": "runtime_eval", "status": "info", "detail": eval_report["detail"]})
    else:
        checks.append({"name": "runtime_eval", "status": "fail", "detail": eval_report["detail"]})
        failed += 1

    verdict = "PASS" if failed == 0 else "FAIL"
    return {
        "status": "ok" if failed == 0 else "warn",
        "passed": passed,
        "failed": failed,
        "total": len(checks),
        "verdict": verdict,
        "checks": checks,
        "detail": f"{verdict} ({passed}/{len(checks)} checks passed)",
    }


def _format_status_line(label: str, status_icon: str, detail: str = "") -> str:
    """Format a compact status line."""
    line = f"  [{status_icon}] {label}"
    if detail:
        line += f"  {detail}"
    return line


def _status_icon(status: str) -> str:
    """Map status to a display icon."""
    icons = {
        "ok": "PASS",
        "warn": "WARN",
        "error": "FAIL",
        "info": "INFO",
        "pass": "PASS",
        "skip": "SKIP",
        "fail": "FAIL",
        "available": "PASS",
        "unavailable": "SKIP",
        "unknown": "INFO",
    }
    return icons.get(status, "INFO")


def _build_terminal_output(report: DashboardReport, verbose: bool = False) -> str:
    """Build a compact terminal-friendly status output."""
    lines: List[str] = []
    lines.append("")
    lines.append("=" * 64)
    lines.append("  StarAgent — System Status Dashboard")
    lines.append("=" * 64)
    lines.append(f"  {report.timestamp}")
    lines.append("")

    # Proxy
    p = report.proxy
    lines.append(_format_status_line("Proxy Server", _status_icon(p.get("status", "info")), p.get("detail", "")))
    if verbose and p.get("status") == "error":
        lines.append(f"           URL: {BASE_URL}")
        lines.append(f"           Key: {'configured' if API_KEY else 'not set'}")

    # Ollama
    o = report.ollama
    lines.append(_format_status_line("Ollama", _status_icon(o.get("status", "info")), o.get("detail", "")))
    if verbose and o.get("status") == "ok":
        models = o.get("models", [])
        if models:
            lines.append(f"           Models: {', '.join(models)}")
        if o.get("model_count", 0) > 5:
            lines.append(f"           (+{o['model_count'] - 5} more)")

    # Prompt mode
    pm = report.prompt_mode
    lines.append(_format_status_line("Prompt Mode", _status_icon(pm.get("status", "info")), pm.get("detail", "")))

    # Memory authority
    ma = report.memory_authority
    if ma.get("status") != "error":
        counts = ma.get("counts", {})
        cnt_parts = []
        for st in ("active", "stale", "superseded", "rejected", "unknown"):
            val = counts.get(st, 0)
            if val > 0 or verbose:
                cnt_parts.append(f"{st}={val}")
        cnt_str = ", ".join(cnt_parts) if cnt_parts else "no items"
        lines.append(_format_status_line("Memory Authority", _status_icon(ma.get("status", "info")), cnt_str))

    # Prompt audit
    pa = report.prompt_audit
    lines.append(_format_status_line("Prompt Audit", _status_icon(pa.get("status", "info")), pa.get("detail", "")))

    # Runtime eval
    re_sec = report.runtime_eval
    lines.append(_format_status_line("Runtime Eval", _status_icon(re_sec.get("status", "info")), re_sec.get("detail", "")))

    # Doctor
    d = report.doctor
    lines.append(_format_status_line("Doctor", _status_icon(d.get("status", "info")), d.get("detail", "")))

    # Suggested action
    lines.append("")
    lines.append("-" * 64)
    lines.append(f"  Next: {report.suggested_action}")
    lines.append("=" * 64)
    lines.append("")

    return "\n".join(lines)


def _suggest_next_action(report: DashboardReport) -> str:
    """Based on report state, suggest the most useful next action."""
    if report.proxy.get("status") != "ok":
        return "Start the proxy server: ./scripts/staragent server start"
    if report.ollama.get("status") != "ok":
        return "Start Ollama: ollama serve  (or check OLLAMA_BASE_URL)"
    if report.runtime_eval.get("status") == "info":
        return "Run: ./scripts/staragent eval runtime"
    if report.runtime_eval.get("status") == "warn":
        return "Review runtime eval skips: ./scripts/staragent eval runtime --verbose"
    if report.doctor.get("status") != "ok":
        return "Run: ./scripts/staragent doctor"
    if report.memory_authority.get("total", 0) == 0:
        return "Start a conversation to build working memory"
    if report.prompt_audit.get("status") == "info":
        return "Enable PROMPT_AUDIT_ENABLED=true to track prompt efficiency"
    return "System healthy — no action required"


# ---------------------------------------------------------------------------
# Main dashboard builder
# ---------------------------------------------------------------------------

def build_dashboard(*, verbose: bool = False, as_json: bool = False) -> DashboardReport:
    """Build a complete status dashboard by querying live endpoints and reading local reports.

    Returns a DashboardReport dataclass, or prints JSON if as_json=True.
    """
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    proxy = check_proxy_health()
    ollama = check_ollama() if proxy["status"] == "ok" else {"status": "skip", "detail": "Proxy unavailable", "models": [], "model_count": 0}
    prompt_mode = check_prompt_mode() if proxy["status"] == "ok" else {"status": "skip", "detail": "Proxy unavailable", "mode": "", "max_memory_tokens": 0, "include_historical": False}
    memory = check_memory_authority() if proxy["status"] == "ok" else {"status": "skip", "detail": "Proxy unavailable", "counts": {"active": 0, "stale": 0, "superseded": 0, "rejected": 0, "unknown": 0}, "total": 0}
    prompt_audit = read_prompt_audit_file()
    runtime_eval = read_runtime_eval_report()
    doctor = run_doctor_checks()

    report = DashboardReport(
        timestamp=timestamp,
        proxy=proxy,
        ollama=ollama,
        prompt_mode=prompt_mode,
        memory_authority=memory,
        prompt_audit=prompt_audit,
        runtime_eval=runtime_eval,
        doctor=doctor,
        suggested_action=_suggest_next_action(
            DashboardReport(timestamp, proxy, ollama, prompt_mode, memory, prompt_audit, runtime_eval, doctor, "")
        ),
    )

    return report


def print_dashboard(report: DashboardReport, *, verbose: bool = False, as_json: bool = False) -> None:
    """Print the dashboard to stdout."""
    if as_json:
        print(json.dumps(asdict(report), indent=2, default=str))
    else:
        print(_build_terminal_output(report, verbose=verbose))


def cmd_status(args: Any = None) -> int:
    """CLI entry point for `staragent status`."""
    verbose = getattr(args, "verbose", False)
    as_json = getattr(args, "as_json", False)
    report = build_dashboard(verbose=verbose, as_json=as_json)
    print_dashboard(report, verbose=verbose, as_json=as_json)

    # Exit code: 0 if no real failures, 1 if proxy or doctor has failures
    if report.proxy.get("status") == "error":
        return 2
    if report.doctor.get("failed", 0) > 0:
        return 1
    return 0


def cmd_eval_status(args: Any = None) -> int:
    """CLI entry point for `staragent eval status` — quick eval summary."""
    report = read_runtime_eval_report()
    if report["status"] == "info":
        print("No runtime eval report found. Run: ./scripts/staragent eval runtime")
        return 0
    s = report.get("summary", {})
    print(f"Runtime Eval: PASS={s.get('passed', 0)} FAIL={s.get('failed', 0)} "
          f"SKIP={s.get('skipped', 0)} BLOCKED={s.get('blocked', 0)} ERR={s.get('errors', 0)} "
          f"score={report.get('report', {}).get('score', '?')}")
    return 1 if s.get("failed", 0) > 0 or s.get("errors", 0) > 0 else 0


if __name__ == "__main__":
    sys.exit(cmd_status())
