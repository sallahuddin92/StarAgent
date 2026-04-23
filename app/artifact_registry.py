from __future__ import annotations

"""
Central registry for task/profile artifact conventions.

This module is intentionally small and dependency-free so it can be safely
imported from API, presets, and any future surfaces without circular deps.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ArtifactConventions:
    primary: Optional[str]
    important: List[str]


_PRIMARY_BY_TASK_TYPE: Dict[str, str] = {
    "research": "final_report.md",
    "repo_audit": "audit_report.md",
    "issue_triage": "next_actions.md",
    "writing": "final_output.md",
}

# Additive: extra operator-important artifacts by task type.
_IMPORTANT_BY_TASK_TYPE: Dict[str, List[str]] = {
    # These are best-effort hints; the file may not exist for partial runs.
    "research": [
        "final_report.md",
        "open_questions.md",
    ],
    "repo_audit": [
        "audit_report.md",
        "risk_notes.md",
        "open_questions.md",
    ],
    "issue_triage": [
        "next_actions.md",
        "likely_causes.md",
        "evidence_table.json",
        "reproduction_steps.md",
    ],
    "writing": [
        "final_output.md",
        "outline.md",
        "draft.md",
    ],
}

# Dataset mode is a specialization of research; these hints are based on the
# preset name stored in artifacts_json["preset"].
_IMPORTANT_BY_PRESET: Dict[str, List[str]] = {
    "dataset_profile": [
        "dataset_facts.json",
        "dataset_profile.json",
        "sample_records.json",
        "batch_summaries.json",
        "dataset_brief.md",
    ],
    "dataset_theme_report": [
        "dataset_theme_report.md",
        "themes.md",
        "themes.json",
        "dataset_facts.json",
        "dataset_brief.md",
        "final_report.md",
        "open_questions.md",
    ],
}

# Presets may override the *operator* primary artifact shown in preset listings.
_PRIMARY_BY_PRESET: Dict[str, str] = {
    "dataset_profile": "dataset_facts.json",
    # Keep runtime task primary as final_report.md (stable), but for preset UX
    # the theme report is the most operator-useful artifact.
    "dataset_theme_report": "dataset_theme_report.md",
}


def preset_primary_artifact(preset_name: Optional[str], task_type: str) -> Optional[str]:
    name = (preset_name or "").strip()
    if name and name in _PRIMARY_BY_PRESET:
        return _PRIMARY_BY_PRESET[name]
    return task_primary_artifact(task_type)


def task_primary_artifact(task_type: Optional[str]) -> Optional[str]:
    tt = (task_type or "").strip().lower()
    return _PRIMARY_BY_TASK_TYPE.get(tt)


def task_conventions(task_type: Optional[str], *, artifacts_json: Optional[Dict[str, Any]] = None) -> ArtifactConventions:
    tt = (task_type or "").strip().lower()
    primary = _PRIMARY_BY_TASK_TYPE.get(tt)
    important: List[str] = list(_IMPORTANT_BY_TASK_TYPE.get(tt, []))

    aj = artifacts_json or {}
    preset = aj.get("preset")
    if isinstance(preset, str) and preset.strip():
        extra = _IMPORTANT_BY_PRESET.get(preset.strip())
        if extra:
            for x in extra:
                if x not in important:
                    important.append(x)
    # Ensure primary is included first if present.
    if primary and primary not in important:
        important.insert(0, primary)
    return ArtifactConventions(primary=primary, important=important)

