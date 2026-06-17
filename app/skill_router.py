"""
StarAgent Skill Router — relevance-based skill selection with bias controls.

At task start:
  1. Classify task intent (coding, testing, docs, marketing, etc.)
  2. Search relevant skills
  3. Apply domain filters (disabled/allowed domains)
  4. Select top 1-3 skills by relevance score
  5. Return concise guidance injection text
  6. Log selected skills in trace

Environment controls:
  STARAGENT_DISABLED_SKILL_DOMAINS — comma-separated domains to exclude
  STARAGENT_SKILL_DOMAINS         — comma-separated allowed domains (strict allowlist)
  STARAGENT_SKILL_STRICT_MODE     — only use skills with matching domain/task intent
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import skill_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

INTENT_KEYWORDS = {
    "coding": ["write", "create", "build", "implement", "code", "script", "function", "class",
                "fastapi", "react", "flask", "django", "express", "python", "javascript", "typescript", "app"],
    "testing": ["test", "pytest", "jest", "playwright", "cypress", "coverage", "unittest", "qa", "verify"],
    "devops": ["deploy", "docker", "kubernetes", "ci/cd", "pipeline", "terraform", "ansible", "aws"],
    "security": ["security", "audit", "vulnerability", "cve", "penetration", "owasp"],
    "documentation": ["docs", "documentation", "readme", "changelog", "api-docs", "manual"],
    "frontend": ["react", "vue", "angular", "css", "html", "ui", "ux", "frontend", "tailwind", "jsx", "component"],
    "backend": ["backend", "api", "server", "database", "sql", "endpoint", "fastapi", "flask", "rest"],
    "database": ["database", "sql", "sqlite", "postgresql", "mysql", "schema", "tables", "migration"],
    "architecture": ["architecture", "design", "pattern", "microservice", "refactor", "migration", "blueprint"],
    "marketing": ["seo", "content", "campaign", "copywriting", "brand", "social media", "email marketing"],
    "product": ["product", "roadmap", "feature", "user story", "sprint", "backlog", "requirements", "prd"],
    "finance": ["finance", "revenue", "metrics", "saas", "pricing", "budget"],
    "compliance": ["compliance", "regulatory", "gdpr", "hipaa", "iso", "quality"],
    "c-level": ["strategy", "board", "investor", "cto", "ceo", "cfo", "executive"],
    "fullstack": ["fullstack", "full-stack", "frontend and backend", "web app"],
    # ---- New repo-workflow intents ----
    "existing_repo_audit": ["existing repo", "repo audit", "inspect", "read-only", "read first",
                            "audit", "codebase", "repository inspection"],
    "read_only_report": ["report", "summarize", "recommend", "do not modify",
                         "don't modify", "stop with a report", "otherwise stop"],
    "focused_fix": ["smallest safe fix", "one fix", "focused fix", "patch", "smallest fix",
                    "one smallest", "safe change"],
    "backend_fix": ["backend fix", "api fix", "endpoint fix", "server fix", "backend repair"],
    "frontend_fix": ["frontend fix", "ui fix", "component fix", "css fix", "layout fix"],
    "test_repair": ["test repair", "fix test", "failing test", "broken test", "test failure"],
    "docs_grounded_sdk": ["sdk", "documentation", "api docs", "from docs", "from documentation",
                          "project documentation"],
    "product_generation": ["landing page", "product page", "marketing page", "spec-to-repo",
                           "generate app", "new app from spec"],
    "marketing_generation": ["campaign", "content strategy", "copywriting", "email campaign",
                             "newsletter", "social media post"],
}

# Negative keywords to lower score for certain intents
NEGATIVE_KEYWORDS = {
    "marketing": ["backend", "api", "fastapi", "database", "code", "programming", "react"],
    "coding": ["marketing", "email campaign", "newsletter"],
}

# Mapping from intent to valid skill domains
INTENT_TO_DOMAINS = {
    "coding": ["engineering"],
    "testing": ["engineering"],
    "devops": ["engineering"],
    "security": ["engineering"],
    "documentation": ["engineering", "documentation"],
    "frontend": ["engineering", "product-team"],
    "backend": ["engineering"],
    "database": ["engineering"],
    "fullstack": ["engineering", "product-team"],
    "architecture": ["engineering"],
    "marketing": ["marketing"],
    "product": ["product-team"],
    "finance": ["finance"],
    "compliance": ["compliance"],
    "c-level": ["c-level", "business"],
    # New intents
    "existing_repo_audit": ["engineering"],
    "read_only_report": ["engineering"],
    "focused_fix": ["engineering"],
    "backend_fix": ["engineering"],
    "frontend_fix": ["engineering", "product-team"],
    "test_repair": ["engineering"],
    "docs_grounded_sdk": ["engineering", "documentation"],
    "product_generation": ["engineering", "product-team", "marketing"],
    "marketing_generation": ["marketing"],
}

# ---------------------------------------------------------------------------
# Skill exclusion and preference rules per intent
# ---------------------------------------------------------------------------

INTENT_EXCLUDED_SKILLS = {
    "existing_repo_audit": {
        "landing-page-generator", "spec-to-repo", "content-strategist",
        "email-campaign-builder", "seo-optimization",
    },
    "read_only_report": {
        "landing-page-generator", "spec-to-repo", "content-strategist",
        "email-campaign-builder", "seo-optimization",
    },
    "focused_fix": {
        "landing-page-generator", "spec-to-repo", "content-strategist",
    },
    "backend_fix": {
        "landing-page-generator", "spec-to-repo", "content-strategist",
    },
    "test_repair": {
        "landing-page-generator", "spec-to-repo", "content-strategist",
    },
}

INTENT_PREFERRED_SKILLS = {
    "existing_repo_audit": [
        "code-reviewer", "repo-audit", "senior-qa", "security-auditor",
        "focused-fix", "dependency-audit",
    ],
    "read_only_report": [
        "code-reviewer", "repo-audit", "senior-qa", "security-auditor",
        "dependency-audit",
    ],
    "focused_fix": [
        "code-reviewer", "senior-backend", "tdd-expert", "focused-fix",
    ],
    "backend_fix": [
        "code-reviewer", "senior-backend", "tdd-expert", "fastapi-testing",
    ],
    "test_repair": [
        "tdd-expert", "fastapi-testing", "senior-qa",
    ],
}


def classify_intent(user_input: str) -> List[str]:
    """Classify task intent from user prompt. Returns ranked list of intents."""
    lower = user_input.lower()
    scores = {}
    
    for intent, keywords in INTENT_KEYWORDS.items():
        # Base score from keyword matches
        match_count = sum(1 for kw in keywords if kw in lower)
        if match_count > 0:
            score = match_count * 2.0
            
            # Apply negative keyword penalties
            negatives = NEGATIVE_KEYWORDS.get(intent, [])
            penalty = sum(1 for n in negatives if n in lower)
            score -= penalty * 3.0
            
            if score > 0:
                scores[intent] = score

    # Sort by score descending
    ranked = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    return ranked[:3] if ranked else ["coding"]  # default to coding


def get_disabled_domains() -> set:
    """Read disabled domains from env."""
    raw = os.environ.get("STARAGENT_DISABLED_SKILL_DOMAINS", "")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def get_allowed_domains() -> Optional[set]:
    """Read allowed domains from env. If set, only these domains are used."""
    raw = os.environ.get("STARAGENT_SKILL_DOMAINS", "")
    if not raw.strip():
        return None
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def is_strict_mode() -> bool:
    return os.environ.get("STARAGENT_SKILL_STRICT_MODE", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Skill selection
# ---------------------------------------------------------------------------

def filter_skills(candidates: List[Dict[str, Any]], intents: Optional[List[str]] = None, user_input: str = "") -> List[Dict[str, Any]]:
    """Apply domain filters (disabled/allowed/intent-based) to a list of skill candidates."""
    disabled = get_disabled_domains()
    allowed = get_allowed_domains()
    
    lower_input = user_input.lower()
    is_simple = any(kw in lower_input for kw in ["print", "hello world", "one-liner", "quick script", "simple script"])
    is_simple = is_simple and not any(kw in lower_input for kw in ["fastapi", "react", "database", "api", "architecture", "enterprise", "snowflake", "big data"])

    # Build valid domains from classified intents
    valid_domains = None
    if intents:
        valid_domains = set()
        for intent in intents:
            valid_domains.update(INTENT_TO_DOMAINS.get(intent, []))

    # HEAVY_SKILLS to avoid for simple tasks
    HEAVY_KEYWORDS = ["enterprise", "snowflake", "bigdata", "architecture", "blueprint", "pattern", "design-system"]

    filtered = []
    for skill in candidates:
        domain = skill.get("domain", "unknown").lower()
        name = skill.get("name", "").lower()
        desc = skill.get("description", "").lower()

        if domain in disabled:
            continue
        if allowed and domain not in allowed:
            continue
        if valid_domains is not None and domain not in valid_domains:
            continue
        
        # Simple task bias: avoid heavy engineering skills
        if is_simple:
            if any(kw in name or kw in desc for kw in HEAVY_KEYWORDS):
                continue

        filtered.append(skill)
    return filtered


def select_skills(
    user_input: str,
    max_skills: int = 3,
    task_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Select the top 1-3 most relevant skills for a task.

    Returns list of skill dicts with 'relevance_score' and 'reason' fields.
    """
    intents = classify_intent(user_input)
    logger.info(f"Classified intents: {intents}")

    # Search skills
    candidates = skill_registry.search_skills(user_input, limit=20)

    # Filter by domain controls
    filtered = filter_skills(candidates, intents, user_input)

    # ---- INTENT-BASED EXCLUSIONS ----
    excluded_names: set = set()
    for intent in intents:
        excluded_names.update(INTENT_EXCLUDED_SKILLS.get(intent, set()))
    if excluded_names:
        filtered = [s for s in filtered if s.get("name", "").lower() not in excluded_names]

    # ---- INTENT-BASED PREFERENCE BOOSTS ----
    preferred_names: set = set()
    for intent in intents:
        preferred_names.update(INTENT_PREFERRED_SKILLS.get(intent, []))
    if preferred_names:
        for skill in filtered:
            if skill.get("name", "").lower() in preferred_names:
                skill["relevance_score"] = skill.get("relevance_score", 0) + 20.0

    # Boost senior/architect skills for complex tasks
    lower_input = user_input.lower()
    is_complex = any(kw in lower_input for kw in ["production-style", "issue tracker", "app", "full-stack", "fastapi", "react", "database", "complex"])
    if is_complex:
        for skill in filtered:
            name = skill.get("name", "").lower()
            desc = skill.get("description", "").lower()
            if any(kw in name or kw in desc for kw in ["senior", "architect", "expert", "fullstack", "tdd", "api-test", "senior-frontend", "senior-backend"]):
                skill["relevance_score"] = skill.get("relevance_score", 0) + 15.0

    # Score and rank
    # Re-sort after boosting
    filtered.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    selected = filtered[:max_skills]

    # Add reason for selection
    for skill in selected:
        skill["reason"] = f"Matched intents {intents} with score {skill.get('score', 0)}"
        skill["relevance_score"] = skill.get("score", 0)

        # Log usage
        if task_id:
            try:
                skill_registry.log_skill_usage(
                    task_id=task_id,
                    skill_id=skill["id"],
                    skill_name=skill["name"],
                    relevance_score=skill.get("score", 0),
                    reason=skill["reason"],
                )
            except Exception as e:
                logger.warning(f"Failed to log skill usage: {e}")

    return selected


def build_skill_injection(selected_skills: List[Dict[str, Any]]) -> str:
    """
    Build concise guidance text from selected skills.
    Only includes relevant excerpts, NOT full SKILL.md.
    """
    if not selected_skills:
        return ""

    lines = ["## Relevant Skill Guidance\n"]
    lines.append("The following skills were selected based on task relevance. "
                  "These are guidelines only — local project rules and verifier gates take precedence.\n")

    for skill in selected_skills:
        full = skill_registry.get_skill(skill["name"])
        if not full:
            continue

        # Extract only first 500 chars of SKILL.md as concise guidance
        content = full.get("skill_md_content", "")
        excerpt = content[:500].strip()
        if len(content) > 500:
            excerpt += "\n... (truncated)"

        lines.append(f"### [{skill['domain']}] {skill['name']}")
        lines.append(f"*Source: {full.get('source_repo', 'unknown')} / {full.get('source_path', '')}*")
        lines.append(f"*Relevance: {skill.get('relevance_score', 0):.1f} — {skill.get('reason', '')}*\n")
        lines.append(excerpt)
        lines.append("")

    return "\n".join(lines)
