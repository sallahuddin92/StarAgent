from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .artifact_registry import preset_primary_artifact


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
    # Optional override for listing/UX; if unset, derived from task_type.
    primary_artifact: Optional[str] = None
    # Optional operator defaults for preset-run inputs (used by API handler).
    default_question: Optional[str] = None
    default_issue: Optional[str] = None
    default_goal: Optional[str] = None


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
        primary_artifact=None,
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
        primary_artifact=None,
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
        primary_artifact=None,
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
        primary_artifact=None,
        expected_outputs=[
            "file_index.json",
            "chunk_summaries.json",
            "file_summaries.md",
            "research_brief.md",
            "open_questions.md",
            "final_report.md",
        ],
    ),
    "dataset_profile": PresetSpec(
        name="dataset_profile",
        description="Read-only JSON dataset mode: profile a dominant .json/.jsonl dataset and plan bounded batch analysis.",
        read_only=True,
        may_require_approval=False,
        task_type="research",
        default_max_steps=40,
        default_max_retries=1,
        primary_artifact="dataset_facts.json",
        expected_outputs=[
            "dataset_profile.json",
            "dataset_facts.json",
            "sample_records.json",
            "batch_summaries.json",
            "dataset_brief.md",
        ],
        default_question=(
            "Profile this JSON dataset in bounded batches.\n"
            "- Detect JSON kind and sample records\n"
            "- Estimate coverage and duplicates (URL-based when available)\n"
            "Return grounded artifacts only; do not fabricate."
        ),
    ),
    "dataset_theme_report": PresetSpec(
        name="dataset_theme_report",
        description="Read-only JSON dataset mode: run dataset analysis through theme extraction and produce a grounded report.",
        read_only=True,
        may_require_approval=False,
        task_type="research",
        default_max_steps=80,
        default_max_retries=1,
        primary_artifact="dataset_theme_report.md",
        expected_outputs=[
            "dataset_profile.json",
            "dataset_facts.json",
            "sample_records.json",
            "batch_summaries.json",
            "dataset_brief.md",
            "themes.json",
            "themes.md",
            "dataset_theme_report.md",
            "final_report.md",
            "open_questions.md",
        ],
        default_question=(
            "Analyze this JSON dataset in bounded batches.\n"
            "- Profile schema/shape\n"
            "- Summarize sampled records\n"
            "- Detect duplicates (URL-based) and estimate coverage\n"
            "- Extract dominant themes with grounded examples\n"
            "Return grounded artifacts only; do not fabricate."
        ),
    ),
    "structured_memo": PresetSpec(
        name="structured_memo",
        description="Read-only writing profile: generate a structured memo artifact from notes/docs without fabricated letterhead.",
        read_only=True,
        may_require_approval=False,
        task_type="writing",
        default_max_steps=25,
        default_max_retries=1,
        primary_artifact=None,
        expected_outputs=[
            "source_index.json",
            "outline.md",
            "draft.md",
            "final_output.md",
        ],
        default_goal=(
            "Write a short structured memo with clear section headings and bullet points.\n"
            "Ground the memo strictly in the provided sources/artifacts.\n"
            "- If information is missing, say 'Unknown' rather than guessing.\n"
            "- Do not include TO/FROM/DATE/SUBJECT headers or bracket placeholders."
        ),
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
        primary_artifact=None,
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
        primary = p.primary_artifact or preset_primary_artifact(p.name, p.task_type)
        out.append(
            {
                "name": p.name,
                "description": p.description,
                "read_only": p.read_only,
                "may_require_approval": p.may_require_approval,
                "task_type": p.task_type,
                "default_max_steps": p.default_max_steps,
                "default_max_retries": p.default_max_retries,
                "primary_artifact": primary,
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
    # Legacy helper preserved for compatibility; new code should use artifact_registry.
    return preset_primary_artifact(None, task_type)


def default_release_review_output_path(*, ts: Optional[int] = None) -> str:
    ts = ts or int(time.time())
    Path("sandbox_test").mkdir(parents=True, exist_ok=True)
    return f"sandbox_test/release_review_{ts}.md"
