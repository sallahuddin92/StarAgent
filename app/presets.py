from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PresetSpec:
    name: str
    description: str
    read_only: bool
    may_require_approval: bool
    # task_type values are existing task engine profiles (or "agent").
    task_type: str
    default_max_steps: int
    default_max_retries: int
    expected_outputs: List[str]


@dataclass(frozen=True)
class PresetPackSpec:
    name: str
    description: str
    # Ordered list of preset names to run (some packs run multiple presets).
    presets: List[str]
    # Operator guidance (not enforced): whether pack is read-only or may require approval.
    read_only: bool
    may_require_approval: bool
    expected_outputs: List[str]
    primary_artifact: Optional[str] = None


PRESETS: Dict[str, PresetSpec] = {
    "quick_repo_audit": PresetSpec(
        name="quick_repo_audit",
        description="Fast, read-only repo audit producing an audit report artifact.",
        read_only=True,
        may_require_approval=False,
        task_type="repo_audit",
        default_max_steps=12,
        default_max_retries=1,
        expected_outputs=[
            "file_index.json",
            "entry_points.md",
            "architecture_map.md",
            "risk_notes.md",
            "open_questions.md",
            "audit_report.md",
        ],
    ),
    "deep_repo_audit": PresetSpec(
        name="deep_repo_audit",
        description="Deeper, read-only repo audit with more steps and broader coverage.",
        read_only=True,
        may_require_approval=False,
        task_type="repo_audit",
        default_max_steps=40,
        default_max_retries=1,
        expected_outputs=[
            "file_index.json",
            "entry_points.md",
            "architecture_map.md",
            "risk_notes.md",
            "open_questions.md",
            "audit_report.md",
        ],
    ),
    "bug_triage": PresetSpec(
        name="bug_triage",
        description="Read-only issue triage: gather evidence, rank likely causes, and recommend next actions.",
        read_only=True,
        may_require_approval=False,
        task_type="issue_triage",
        default_max_steps=25,
        default_max_retries=1,
        expected_outputs=[
            "issue_summary.md",
            "evidence_table.json",
            "likely_causes.md",
            "reproduction_steps.md",
            "next_actions.md",
        ],
    ),
    "docs_research": PresetSpec(
        name="docs_research",
        description="Read-only document research mode over a folder, producing a final research report artifact.",
        read_only=True,
        may_require_approval=False,
        task_type="research",
        default_max_steps=60,
        default_max_retries=1,
        expected_outputs=[
            "file_index.json",
            "chunk_summaries.json",
            "file_summaries.md",
            "research_brief.md",
            "open_questions.md",
            "final_report.md",
        ],
    ),
    "structured_memo": PresetSpec(
        name="structured_memo",
        description="Read-only writing profile: generate a structured memo artifact from notes/docs without fabricated letterhead.",
        read_only=True,
        may_require_approval=False,
        task_type="writing",
        default_max_steps=25,
        default_max_retries=1,
        expected_outputs=[
            "source_index.json",
            "outline.md",
            "draft.md",
            "final_output.md",
        ],
    ),
    "release_review": PresetSpec(
        name="release_review",
        description="Stateful release review: run a grounded repo audit and (optionally) export the primary report to sandbox_test/ via approval-gated write.",
        read_only=False,
        may_require_approval=True,
        # Implemented as repo_audit + an optional approval-gated export step.
        task_type="repo_audit",
        default_max_steps=25,
        default_max_retries=1,
        expected_outputs=[
            # Task artifacts:
            "audit_report.md",
            "open_questions.md",
            # Export output:
            "sandbox_test/release_review_*.md (approval-gated export)",
        ],
    ),
}

# Preset packs: curated multi-step operator flows built from existing presets.
PACKS: Dict[str, PresetPackSpec] = {
    "repo_onboarding": PresetPackSpec(
        name="repo_onboarding",
        description="Get oriented quickly: run a quick audit, then generate a short structured memo from the audit artifacts.",
        presets=["quick_repo_audit", "structured_memo"],
        read_only=True,
        may_require_approval=False,
        expected_outputs=["audit_report.md", "final_output.md"],
        primary_artifact="final_output.md",
    ),
    "codebase_audit": PresetPackSpec(
        name="codebase_audit",
        description="Deeper read-only audit for architecture + risks + unknowns.",
        presets=["deep_repo_audit"],
        read_only=True,
        may_require_approval=False,
        expected_outputs=["audit_report.md"],
        primary_artifact="audit_report.md",
    ),
    "bug_investigation": PresetPackSpec(
        name="bug_investigation",
        description="Read-only bug investigation: gather evidence and produce grounded next actions.",
        presets=["bug_triage"],
        read_only=True,
        may_require_approval=False,
        expected_outputs=["next_actions.md"],
        primary_artifact="next_actions.md",
    ),
    "docs_digest": PresetPackSpec(
        name="docs_digest",
        description="Read docs in a folder, synthesize a report, then produce a short operator memo from the report artifacts.",
        presets=["docs_research", "structured_memo"],
        read_only=True,
        may_require_approval=False,
        expected_outputs=["final_report.md", "final_output.md"],
        primary_artifact="final_output.md",
    ),
    "release_prep": PresetPackSpec(
        name="release_prep",
        description="Stateful release prep: run audit and export a release review report to sandbox_test/ (approval-gated).",
        presets=["release_review"],
        read_only=False,
        may_require_approval=True,
        expected_outputs=["sandbox_test/release_review_*.md (approval-gated export)"],
        primary_artifact=None,
    ),
}


def list_presets() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for k in sorted(PRESETS.keys()):
        p = PRESETS[k]
        out.append(
            {
                "name": p.name,
                "description": p.description,
                "read_only": p.read_only,
                "may_require_approval": p.may_require_approval,
                "task_type": p.task_type,
                "default_max_steps": p.default_max_steps,
                "default_max_retries": p.default_max_retries,
                "primary_artifact": _primary_artifact_for_task_type(p.task_type),
                "expected_outputs": list(p.expected_outputs),
            }
        )
    return out


def list_preset_packs() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for k in sorted(PACKS.keys()):
        p = PACKS[k]
        out.append(
            {
                "name": p.name,
                "description": p.description,
                "presets": list(p.presets),
                "read_only": p.read_only,
                "may_require_approval": p.may_require_approval,
                "expected_outputs": list(p.expected_outputs),
                "primary_artifact": p.primary_artifact,
            }
        )
    return out


def _primary_artifact_for_task_type(task_type: str) -> Optional[str]:
    tt = (task_type or "").strip().lower()
    if tt == "research":
        return "final_report.md"
    if tt == "repo_audit":
        return "audit_report.md"
    if tt == "issue_triage":
        return "next_actions.md"
    if tt == "writing":
        return "final_output.md"
    if tt == "agent":
        # agent tasks are flexible; preset may specify an output file.
        return None
    return None


def default_release_review_output_path(*, ts: Optional[int] = None) -> str:
    ts = ts or int(time.time())
    Path("sandbox_test").mkdir(parents=True, exist_ok=True)
    return f"sandbox_test/release_review_{ts}.md"
