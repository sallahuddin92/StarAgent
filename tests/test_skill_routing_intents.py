"""Tests for skill routing intent classification and exclusion/preference rules.

Validates that:
- repo audit tasks classify to correct intents
- excluded skills are blocked for audit/report intents
- preferred skills get boosted
- product generation tasks can still access spec-to-repo
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil

import pytest

from app.skill_router import (
    classify_intent,
    select_skills,
    filter_skills,
    INTENT_EXCLUDED_SKILLS,
    INTENT_PREFERRED_SKILLS,
    INTENT_TO_DOMAINS,
)
from app import skill_registry, skill_ingest


# ---------------------------------------------------------------------------
# Fixture: initialize a temp skill DB with test skills
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def setup_skill_db(tmp_path_factory):
    """Create a temporary skill DB with mock skills for testing."""
    temp_dir = str(tmp_path_factory.mktemp("skill_intents"))
    db_path = os.path.join(temp_dir, "test_intents.db")
    os.environ["STARAGENT_SKILL_DB_PATH"] = db_path
    skill_registry.DB_PATH = db_path
    # Force re-init connection to new DB
    skill_registry._conn = None
    skill_registry.init_db()

    # Create mock repo with diverse skills
    with tempfile.TemporaryDirectory() as repo_path:
        skills = {
            "engineering": [
                ("code-reviewer", "# Code Reviewer\n\nReview code for quality, bugs, and best practices."),
                ("senior-backend", "# Senior Backend\n\nBuild production-grade backends with FastAPI."),
                ("tdd-expert", "# TDD Expert\n\nTest-driven development specialist."),
                ("security-auditor", "# Security Auditor\n\nScan for vulnerabilities and OWASP top 10."),
                ("landing-page-generator", "# Landing Page Generator\n\nGenerate beautiful landing pages."),
                ("spec-to-repo", "# Spec to Repo\n\nConvert product specs into full repo structure."),
                ("fastapi-testing", "# FastAPI Testing\n\nTest FastAPI endpoints with pytest."),
            ],
            "marketing": [
                ("content-strategist", "# Content Strategist\n\nPlan content marketing strategy."),
                ("seo-optimization", "# SEO Optimization\n\nImprove search engine ranking."),
            ],
        }
        for domain, skill_list in skills.items():
            domain_path = os.path.join(repo_path, domain)
            os.makedirs(domain_path)
            for name, content in skill_list:
                skill_path = os.path.join(domain_path, name)
                os.makedirs(skill_path)
                with open(os.path.join(skill_path, "SKILL.md"), "w") as f:
                    f.write(content)

        skill_ingest.ingest_repo(repo_path, "test/skills")

    yield


# ---------------------------------------------------------------------------
# Intent classification tests
# ---------------------------------------------------------------------------

class TestIntentClassification:
    def test_repo_audit(self):
        intents = classify_intent("Existing repo task. Audit the codebase and inspect.")
        assert "existing_repo_audit" in intents

    def test_read_only_report(self):
        intents = classify_intent("Report discovered commands. Do not modify files.")
        assert "read_only_report" in intents

    def test_focused_fix(self):
        intents = classify_intent("Apply one smallest safe fix to the backend.")
        assert "focused_fix" in intents

    def test_product_generation(self):
        intents = classify_intent("Generate a landing page for the new product.")
        assert "product_generation" in intents

    def test_coding_task(self):
        intents = classify_intent("Build a FastAPI backend with /health endpoint")
        assert "coding" in intents or "backend" in intents

    def test_frontend_fix(self):
        intents = classify_intent("Apply a frontend fix to the component")
        assert "frontend_fix" in intents or "frontend" in intents

    def test_all_new_intents_have_domains(self):
        """All new intent types should have INTENT_TO_DOMAINS entries."""
        new_intents = [
            "existing_repo_audit", "read_only_report", "focused_fix",
            "backend_fix", "frontend_fix", "test_repair",
            "docs_grounded_sdk", "product_generation", "marketing_generation",
        ]
        for intent in new_intents:
            assert intent in INTENT_TO_DOMAINS, f"Missing INTENT_TO_DOMAINS for {intent}"


# ---------------------------------------------------------------------------
# Skill exclusion tests
# ---------------------------------------------------------------------------

class TestSkillExclusion:
    def test_repo_audit_excludes_landing_page(self):
        selected = select_skills("Existing repo. Audit the codebase. Read first. Inspect.")
        names = [s["name"].lower() for s in selected]
        assert "landing-page-generator" not in names

    def test_repo_audit_excludes_spec_to_repo(self):
        selected = select_skills("Existing repo. Audit the codebase. Read first. Inspect.")
        names = [s["name"].lower() for s in selected]
        assert "spec-to-repo" not in names

    def test_report_excludes_marketing(self):
        selected = select_skills("Report discovered commands and recommend fix. Do not modify.")
        names = [s["name"].lower() for s in selected]
        assert "content-strategist" not in names
        assert "seo-optimization" not in names

    def test_product_generation_can_use_spec_to_repo(self):
        """Product generation should NOT exclude spec-to-repo."""
        excluded = INTENT_EXCLUDED_SKILLS.get("product_generation", set())
        assert "spec-to-repo" not in excluded

    def test_product_generation_can_use_landing_page(self):
        excluded = INTENT_EXCLUDED_SKILLS.get("product_generation", set())
        assert "landing-page-generator" not in excluded


# ---------------------------------------------------------------------------
# Skill preference boost tests
# ---------------------------------------------------------------------------

class TestSkillPreference:
    def test_audit_prefers_code_reviewer(self):
        preferred = INTENT_PREFERRED_SKILLS.get("existing_repo_audit", [])
        assert "code-reviewer" in preferred
        assert "security-auditor" in preferred

    def test_focused_fix_prefers_backend(self):
        preferred = INTENT_PREFERRED_SKILLS.get("focused_fix", [])
        assert "code-reviewer" in preferred
        assert "senior-backend" in preferred

    def test_backend_fix_prefers_testing(self):
        preferred = INTENT_PREFERRED_SKILLS.get("backend_fix", [])
        assert "fastapi-testing" in preferred
        assert "tdd-expert" in preferred


# ---------------------------------------------------------------------------
# Integration: selection for backend fix
# ---------------------------------------------------------------------------

def test_backend_fix_selection():
    """Backend fix task should prefer code-review/test skills over marketing."""
    selected = select_skills("Fix the backend API endpoint that returns 500 error. Backend fix.")
    if selected:
        names = [s["name"].lower() for s in selected]
        assert "content-strategist" not in names
        assert "seo-optimization" not in names
