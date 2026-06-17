import os
import yaml
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from . import skill_registry

logger = logging.getLogger(__name__)

DEFAULT_SKILL_PACKS = {
    "repo_audit": [
        "architecture-review",
        "security-review",
        "dependency-review",
        "documentation-review",
        "performance-review",
        "testing-review",
        "api-review",
        "code-review"
    ],
    "existing_repo_fix": [
        "codebase-refactor",
        "unit-testing",
        "code-review"
    ],
    "feature_build": [
        "api-design",
        "backend-development",
        "frontend-development",
        "integration-testing"
    ],
    "docs_sdk": [
        "sdk-design",
        "documentation-review",
        "api-review"
    ],
    "research": [
        "literature-search",
        "document-analysis"
    ],
    "issue_triage": [
        "bug-analysis",
        "triage-report"
    ],
    "release": [
        "changelog-generation",
        "deployment-check"
    ],
    "bug_fix": [
        "debug-techniques",
        "bug-reproduction"
    ],
    "refactor": [
        "clean-code-principles",
        "design-patterns"
    ]
}

def _get_config_path() -> Path:
    d = os.getenv("STARAGENT_CLI_STATE_DIR") or os.getenv("MACAGENT_CLI_STATE_DIR")
    if d:
        base = Path(d)
    else:
        base = Path.home() / ".staragent"
    base.mkdir(parents=True, exist_ok=True)
    return base / "skill_packs.yaml"

def init_skill_packs():
    """Ensure skill_packs.yaml exists with defaults."""
    path = _get_config_path()
    if not path.exists():
        try:
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(DEFAULT_SKILL_PACKS, f, default_flow_style=False)
            logger.info(f"Initialized default skill packs at {path}")
        except Exception as e:
            logger.error(f"Failed to write default skill packs: {e}")

def load_skill_packs() -> Dict[str, List[str]]:
    init_skill_packs()
    path = _get_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.error(f"Failed to load skill packs from {path}: {e}")
    return DEFAULT_SKILL_PACKS

def get_pack_skills(pack_name: str) -> List[str]:
    packs = load_skill_packs()
    return packs.get(pack_name, DEFAULT_SKILL_PACKS.get(pack_name, []))

def build_pack_injection(pack_name: str) -> str:
    """Build guidance text for a whole skill pack."""
    skill_names = get_pack_skills(pack_name)
    if not skill_names:
        return ""

    lines = [f"## Skill Pack Guidance: {pack_name}\n"]
    lines.append("The following skills have been automatically loaded for this workflow. "
                 "Apply these design principles and practices where applicable:\n")

    found_any = False
    for name in skill_names:
        full = skill_registry.get_skill(name)
        if full:
            found_any = True
            content = full.get("skill_md_content", "")
            excerpt = content[:500].strip()
            if len(content) > 500:
                excerpt += "\n... (truncated)"
            lines.append(f"### {name}")
            lines.append(excerpt)
            lines.append("")
        else:
            # If skill not ingested, provide a placeholder description
            # so the client doesn't get nothing (ensures no dummy/empty results).
            lines.append(f"### {name}")
            lines.append(f"Guidance on {name.replace('-', ' ')} (Refer to project guidelines).")
            lines.append("")
            found_any = True

    if not found_any:
        return ""
    return "\n".join(lines)
