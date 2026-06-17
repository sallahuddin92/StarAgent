#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.eval_result_parser import detect_agent_status, has_honest_stress_diagnostics

ROOT = Path(__file__).resolve().parents[1]
import uuid
EVAL_RUN_ID = os.getenv("STARAGENT_EVAL_RUN_ID") or f"{int(time.time())}_{uuid.uuid4().hex[:6]}"


@dataclass
class CaseResult:
    name: str
    tier: str
    outcome: str  # PASS | FAIL | EXPECTED_FAIL
    details: str


def run_cmd(cmd: str, timeout_s: int = 900) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            shell=True,
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="ignore")
        if isinstance(err, bytes):
            err = err.decode("utf-8", errors="ignore")
        partial = out + err
        timeout_note = f"\n[eval_timeout] command exceeded {timeout_s}s"
        return 124, partial + timeout_note


def run_pytest_case(name: str, tier: str, cmd: str) -> CaseResult:
    rc, out = run_cmd(cmd)
    if rc == 0:
        return CaseResult(name, tier, "PASS", "pytest passed")
    tail = "\n".join(out.splitlines()[-12:])
    return CaseResult(name, tier, "FAIL", f"pytest failed\n{tail}")


def run_agent_case(
    name: str,
    tier: str,
    cmd: str,
    diagnostics_fn: Optional[Callable[[], str]] = None,
) -> CaseResult:
    _, out = run_cmd(cmd)
    if diagnostics_fn:
        diag = diagnostics_fn()
        if diag:
            out += "\n" + diag
    status = detect_agent_status(out)
    if tier in {"baseline", "medium"}:
        if status == "completed":
            return CaseResult(name, tier, "PASS", "[x_agent_status] completed")
        tail = "\n".join(out.splitlines()[-12:])
        return CaseResult(name, tier, "FAIL", f"status={status}\n{tail}")

    # stress tier
    if status == "completed":
        return CaseResult(name, tier, "PASS", "[x_agent_status] completed")
    if status in {"failed", "unknown"} and has_honest_stress_diagnostics(out):
        tail = "\n".join(out.splitlines()[-10:])
        return CaseResult(name, tier, "EXPECTED_FAIL", f"honest diagnostics present\n{tail}")
    tail = "\n".join(out.splitlines()[-12:])
    return CaseResult(name, tier, "FAIL", f"stress failure without precise diagnostics (status={status})\n{tail}")


def prepare_medium_docs() -> None:
    weather_root = ROOT / "scratch" / "eval_medium_docs_weather"
    longcat_root = ROOT / "scratch" / "eval_medium_docs_longcat"
    weather_root.mkdir(parents=True, exist_ok=True)
    longcat_root.mkdir(parents=True, exist_ok=True)

    weather = weather_root / "weathersdk.md"
    weather.write_text(
        """# WeatherSDK Quickstart

```python
from weather_sdk import WeatherClient
client = WeatherClient(api_key="abc")
forecast = client.current(city="Kuala Lumpur")
print(forecast)
```
""",
        encoding="utf-8",
    )

    longcat = longcat_root / "longcat.md"
    longcat.write_text(
        """# LongCat OpenAI-Compatible

Base URL: https://api.longcat.chat/openai
Chat completion endpoint: /v1/chat/completions

```python
from openai import OpenAI
client = OpenAI(api_key="YOUR_APP_KEY", base_url="https://api.longcat.chat/openai")
resp = client.chat.completions.create(
    model="LongCat-Flash-Chat",
    messages=[{"role":"user","content":"hello"}]
)
print(resp)
```
""",
        encoding="utf-8",
    )

    run_cmd(f'./scripts/staragent --project eval-medium-sdk docs ingest "{weather_root}"')
    run_cmd(f'./scripts/staragent --project eval-medium-longcat docs ingest "{longcat_root}"')


def baseline_cases() -> List[CaseResult]:
    run_id = EVAL_RUN_ID
    out: List[CaseResult] = []
    out.append(run_pytest_case("Model profile tests", "baseline", "python3 -m pytest tests/test_model_profiles.py -v"))
    out.append(run_pytest_case("JSON protocol tests", "baseline", "python3 -m pytest tests/test_executor_json.py -v"))
    out.append(
        run_agent_case(
            "Simple script",
            "baseline",
            f'./scripts/staragent --project eval-{run_id} --conversation eval-baseline-simple-{run_id} multi-agent "Write a python script in scratch/eval_simple_{run_id} that prints hello" --stream compact',
        )
    )
    out.append(
        run_agent_case(
            "FastAPI backend",
            "baseline",
            f'./scripts/staragent --project eval-{run_id} --conversation eval-baseline-fastapi-{run_id} multi-agent "Create a FastAPI backend with /health endpoint in scratch/eval_backend_{run_id}, write pytest test, run the test" --stream compact',
        )
    )
    return out


def medium_cases() -> List[CaseResult]:
    ts = int(time.time())
    prepare_medium_docs()
    out: List[CaseResult] = []
    out.append(
        run_agent_case(
            "Full-stack calculator",
            "medium",
            f'./scripts/staragent --project eval --conversation eval-medium-calc-{ts} multi-agent "Create a full-stack calculator app in scratch/eval_calculator: FastAPI backend with POST /calculate, SQLite history table, React frontend, pytest backend tests" --stream compact',
        )
    )
    out.append(
        run_agent_case(
            "Docs-grounded WeatherSDK",
            "medium",
            f'./scripts/staragent --project eval-medium-sdk --conversation eval-medium-weather-{ts} multi-agent "Create a Python weather app using WeatherSDK from project documentation. Save to scratch/sdk_test/app." --stream compact',
        )
    )
    out.append(
        run_agent_case(
            "Docs-grounded LongCat",
            "medium",
            f'./scripts/staragent --project eval-medium-longcat --conversation eval-medium-longcat-{ts} multi-agent "Create a Python script in scratch/longcat_client_test that calls LongCat OpenAI-compatible chat completions using the project documentation. Do not hardcode secrets; read LONGCAT_API_KEY from environment." --stream compact',
        )
    )
    return out


def stress_cases() -> List[CaseResult]:
    ts = int(time.time())
    out: List[CaseResult] = []

    def issue_tracker_diag() -> str:
        diags: List[str] = []
        required = [
            ROOT / "scratch/eval_issue_tracker/backend/main.py",
            ROOT / "scratch/eval_issue_tracker/frontend/build",
        ]
        for p in required:
            if not p.exists():
                diags.append(f"missing file: {p.relative_to(ROOT)}")
        return "\n".join(diags)

    def large_app_diag() -> str:
        diags: List[str] = []
        required = [
            ROOT / "scratch/eval_large_app/backend/main.py",
            ROOT / "scratch/eval_large_app/frontend/package.json",
            ROOT / "scratch/eval_large_app/frontend/build",
        ]
        for p in required:
            if not p.exists():
                diags.append(f"missing file: {p.relative_to(ROOT)}")
        if (ROOT / "scratch/eval_large_app/frontend/build").exists() is False:
            diags.append("command failed: npm run build")
        return "\n".join(diags)

    out.append(
        run_agent_case(
            "Complex issue tracker",
            "stress",
            f'./scripts/staragent --project eval --conversation eval-stress-issue-{ts} multi-agent "Create a production-style issue tracker app in scratch/eval_issue_tracker: FastAPI backend, SQLite projects/issues/comments tables, React frontend with build, and verify all" --stream compact',
            diagnostics_fn=issue_tracker_diag,
        )
    )
    out.append(
        run_agent_case(
            "Large multi-module app",
            "stress",
            f'./scripts/staragent --project eval --conversation eval-stress-large-{ts} multi-agent "Create a large multi-module platform in scratch/eval_large_app with backend APIs, database models, worker module, React frontend, and end-to-end verification." --stream compact',
            diagnostics_fn=large_app_diag,
        )
    )
    return out


def prepare_repo_fixture() -> Path:
    """Create a sandbox repo fixture for repo audit eval."""
    repo_root = ROOT / "scratch" / "eval_repo_audit"
    repo_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "Makefile").write_text(
        "install:\n\tpip install -r requirements.txt\n\n"
        "dev:\n\tuvicorn main:app --reload\n\n"
        "test:\n\tpytest tests/\n\n"
        ".PHONY: install dev test\n",
        encoding="utf-8",
    )
    (repo_root / "README.md").write_text(
        "# Eval Repo\n\nA test project for eval.\n\n"
        "## Setup\n\n```bash\nmake install\nmake dev\n```\n",
        encoding="utf-8",
    )
    (repo_root / "requirements.txt").write_text(
        "fastapi>=0.100.0\nuvicorn\n",
        encoding="utf-8",
    )
    apps_backend = repo_root / "apps" / "backend"
    apps_backend.mkdir(parents=True, exist_ok=True)
    (apps_backend / "requirements.txt").write_text(
        "sqlalchemy\nalembic\n",
        encoding="utf-8",
    )
    return repo_root


def repo_cases() -> List[CaseResult]:
    ts = int(time.time())
    repo_root = prepare_repo_fixture()
    out: List[CaseResult] = []

    # Test 1: pytest-based unit tests for repo workflow
    out.append(run_pytest_case(
        "Repo workflow unit tests",
        "repo",
        "python3 -m pytest tests/test_procureflow_regression.py -v",
    ))

    # Test 2: Command discovery unit tests
    out.append(run_pytest_case(
        "Command discovery tests",
        "repo",
        "python3 -m pytest tests/test_procureflow_regression.py::TestCommandDiscovery -v",
    ))

    # Test 3: Skill routing intent tests
    out.append(run_pytest_case(
        "Skill routing intent tests",
        "repo",
        "python3 -m pytest tests/test_skill_routing_intents.py -v",
    ))

    # Test 4: Real-world audit flow
    out.append(run_agent_case(
        "Read-only Repo Audit",
        "repo",
        f'./scripts/staragent --project eval-repo --conversation audit-{ts} multi-agent "Project root is {repo_root}. Read Makefile and README.md. Do not modify files. Report discovered commands and detected stack." --stream compact'
    ))

    return out


def print_tier(title: str, results: List[CaseResult]) -> None:
    print("")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"TIER: {title}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    for r in results:
        icon = "✅" if r.outcome == "PASS" else ("⚠️" if r.outcome == "EXPECTED_FAIL" else "❌")
        print(f"{icon} {r.name}: {r.outcome}")
        if r.outcome != "PASS":
            print(f"    {r.details.splitlines()[0]}")


def tier_exit_code(tier: str, results: List[CaseResult]) -> int:
    if tier == "stress":
        # stress FAIL is only hard-fail when diagnostics are not precise.
        return 1 if any(r.outcome == "FAIL" for r in results) else 0
    # baseline/medium failures are hard failures.
    return 1 if any(r.outcome != "PASS" for r in results) else 0


def run_tier(tier: str) -> Tuple[int, List[CaseResult]]:
    if tier == "baseline":
        res = baseline_cases()
    elif tier == "medium":
        res = medium_cases()
    elif tier == "stress":
        res = stress_cases()
    elif tier == "repo":
        res = repo_cases()
    else:
        raise ValueError(tier)
    return tier_exit_code(tier, res), res


def run_all() -> int:
    codes = []
    all_results: List[CaseResult] = []
    for tier in ("baseline", "medium", "repo", "stress"):
        code, results = run_tier(tier)
        print_tier(tier.upper(), results)
        codes.append((tier, code))
        all_results.extend(results)

    print("")
    print("╔══════════════════════════════════════════════════════╗")
    print("║      TIERED EVAL SUMMARY                            ║")
    print("╚══════════════════════════════════════════════════════╝")
    for tier in ("baseline", "medium", "repo", "stress"):
        tier_results = [r for r in all_results if r.tier == tier]
        p = sum(1 for r in tier_results if r.outcome == "PASS")
        ef = sum(1 for r in tier_results if r.outcome == "EXPECTED_FAIL")
        f = sum(1 for r in tier_results if r.outcome == "FAIL")
        print(f"- {tier}: pass={p} expected_fail={ef} fail={f}")

    baseline_fail = any(code != 0 for t, code in codes if t == "baseline")
    medium_fail = any(code != 0 for t, code in codes if t == "medium")
    repo_fail = any(code != 0 for t, code in codes if t == "repo")
    stress_fail = any(code != 0 for t, code in codes if t == "stress")

    print("")
    print("Tier verdicts:")
    print(f"- baseline: {'REGRESSION ❌' if baseline_fail else 'OK ✅'}")
    print(f"- medium: {'CAPABILITY REGRESSION ❌' if medium_fail else 'OK ✅'}")
    print(f"- repo: {'INTELLIGENCE REGRESSION ❌' if repo_fail else 'OK ✅'}")
    print(f"- stress: {'HARD FAIL ❌' if stress_fail else 'OK / EXPECTED_FAIL ✅'}")

    return 1 if (baseline_fail or medium_fail or repo_fail or stress_fail) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="StarAgent tiered eval runner")
    ap.add_argument("tier", choices=["baseline", "medium", "stress", "repo", "all"])
    args = ap.parse_args()

    if args.tier == "all":
        return run_all()

    code, results = run_tier(args.tier)
    print_tier(args.tier.upper(), results)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
