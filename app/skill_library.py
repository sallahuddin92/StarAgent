"""
StarAgent Skill Library — top-level API.

Provides a unified interface for ingesting, searching, selecting,
and injecting skills into agent tasks.
"""

import logging
from typing import Any, Dict, List, Optional

from . import skill_registry
from . import skill_ingest
from . import skill_router

logger = logging.getLogger(__name__)


def init():
    """Initialize the skill library (create tables if needed)."""
    skill_registry.init_db()


def ingest(repo_path: str, source_repo: str = "alirezarezvani/claude-skills") -> Dict[str, Any]:
    """Ingest a claude-skills compatible repo."""
    return skill_ingest.ingest_repo(repo_path, source_repo)


def list_all(domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all skills, optionally filtered by domain."""
    return skill_registry.list_skills(domain)


def search(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search skills by keyword, respecting domain filters."""
    candidates = skill_registry.search_skills(query, limit=50)
    filtered = skill_router.filter_skills(candidates)
    return filtered[:limit]


def show(skill_name: str) -> Optional[Dict[str, Any]]:
    """Get full details for a skill."""
    return skill_registry.get_skill(skill_name)


def select_for_task(user_input: str, task_id: Optional[str] = None, max_skills: int = 3) -> List[Dict[str, Any]]:
    """Select the most relevant skills for a task."""
    return skill_router.select_skills(user_input, max_skills=max_skills, task_id=task_id)


def get_injection(user_input: str, task_id: Optional[str] = None) -> str:
    """Get concise skill guidance injection text for a task prompt."""
    selected = select_for_task(user_input, task_id=task_id)
    return skill_router.build_skill_injection(selected)


def build_skill_injection(selected_skills: List[Dict[str, Any]]) -> str:
    """Build concise guidance text from selected skills."""
    return skill_router.build_skill_injection(selected_skills)


def get_stats() -> Dict[str, Any]:
    """Get skill library statistics."""
    return {
        "total_skills": skill_registry.get_skill_count(),
        "domains": skill_registry.get_domain_counts(),
    }


def classify_intent(user_input: str) -> List[str]:
    """Classify task intent from user prompt."""
    return skill_router.classify_intent(user_input)
