"""Tests for ProcureFlow-style existing-repo workflow.

Fixture-based test using tmp_path to create a fake repo structure
and verify the full read-only-first pipeline:
- correct files read
- no writes
- no npm install/build/pytest
- commands extracted
- report includes recommendation
- skill routing excludes irrelevant skills
"""
from __future__ import annotations

import os
import json
import pytest

from app.multi_agent import (
    _build_existing_repo_read_only_graph,
    _is_read_only_first_prompt,
    _is_report_only_task,
    _parse_user_mentioned_files,
    RepoWorkflowPhase,
)
from app.command_discovery import discover_commands, format_discovered_commands
from app.skill_router import classify_intent, select_skills, INTENT_EXCLUDED_SKILLS


# ---------------------------------------------------------------------------
# Fixture: fake ProcureFlow repo
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_repo(tmp_path):
    """Create a minimal ProcureFlow-like repo structure."""
    root = tmp_path / "procureflow_saas"
    root.mkdir()

    # Makefile
    (root / "Makefile").write_text(
        "install:\n\tpip install -r requirements.txt\n\n"
        "dev:\n\tuvicorn main:app --reload\n\n"
        "test:\n\tpytest tests/\n\n"
        "build:\n\techo build\n\n"
        "lint:\n\tflake8 .\n\n"
        ".PHONY: install dev test build lint\n"
    )

    # README.md
    (root / "README.md").write_text(
        "# ProcureFlow SaaS\n\n"
        "A procurement management platform.\n\n"
        "## Quickstart\n\n"
        "```bash\nmake install\nmake dev\n```\n"
    )

    # apps/backend
    backend = root / "apps" / "backend"
    backend.mkdir(parents=True)
    (backend / "requirements.txt").write_text(
        "fastapi>=0.100.0\nuvicorn\nsqlalchemy\nalembic\n"
    )
    (backend / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
    )

    # apps/frontend
    frontend = root / "apps" / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text(json.dumps({
        "name": "procureflow-frontend",
        "scripts": {
            "dev": "next dev",
            "build": "next build",
            "start": "next start",
            "lint": "eslint .",
            "test": "jest"
        }
    }))

    # pyproject.toml
    (root / "pyproject.toml").write_text(
        "[build-system]\nrequires = [\"setuptools\"]\n\n"
        "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n"
    )

    return str(root)


# ---------------------------------------------------------------------------
# Command Discovery Tests
# ---------------------------------------------------------------------------

class TestCommandDiscovery:
    def test_discovers_makefile_targets(self, fake_repo):
        cmds = discover_commands(fake_repo)
        assert "make install" in cmds["install"]
        assert "make dev" in cmds["dev"]
        assert "make test" in cmds["test"]
        assert "make build" in cmds["build"]

    def test_discovers_npm_scripts(self, fake_repo):
        cmds = discover_commands(fake_repo)
        assert "npm install" in cmds["install"]
        assert "npm run dev" in cmds["dev"]
        assert "npm run build" in cmds["build"]
        assert "npm run test" in cmds["test"]

    def test_discovers_pyproject_pytest(self, fake_repo):
        cmds = discover_commands(fake_repo)
        assert "pytest" in cmds["test"]

    def test_discovers_requirements(self, fake_repo):
        cmds = discover_commands(fake_repo)
        assert any("requirements.txt" in c for c in cmds["install"])

    def test_discovers_readme_commands(self, fake_repo):
        cmds = discover_commands(fake_repo)
        # README has make install and make dev in a bash block
        assert "make install" in cmds["install"]

    def test_format_discovered(self, fake_repo):
        cmds = discover_commands(fake_repo)
        formatted = format_discovered_commands(cmds)
        assert "install:" in formatted
        assert "test:" in formatted

    def test_empty_dir(self, tmp_path):
        cmds = discover_commands(str(tmp_path))
        assert cmds["install"] == []
        assert cmds["test"] == []


# ---------------------------------------------------------------------------
# Read-Only Graph Builder Tests
# ---------------------------------------------------------------------------

class TestReadOnlyGraph:
    def test_graph_has_three_phases(self, fake_repo):
        blueprint = {
            "project_root": fake_repo,
            "existing_repo": True,
            "read_only_first": True,
        }
        graph = _build_existing_repo_read_only_graph(
            "Existing repo task. Read Makefile first.",
            blueprint,
        )
        assert graph is not None
        assert len(graph.subtasks) == 3
        assert graph.subtasks[0].id == "inspect_repo"
        assert graph.subtasks[1].id == "inspect_backend_deps"
        assert graph.subtasks[2].id == "synthesize_report"

    def test_graph_subtasks_have_phases(self, fake_repo):
        blueprint = {
            "project_root": fake_repo,
            "existing_repo": True,
            "read_only_first": True,
        }
        graph = _build_existing_repo_read_only_graph(
            "Existing repo task. Read Makefile first.",
            blueprint,
        )
        assert graph.subtasks[0].requirements["workflow_phase"] == "INSPECT"
        assert graph.subtasks[1].requirements["workflow_phase"] == "INSPECT"
        assert graph.subtasks[2].requirements["workflow_phase"] == "SYNTHESIZE"

    def test_graph_has_discovered_commands(self, fake_repo):
        blueprint = {
            "project_root": fake_repo,
            "existing_repo": True,
            "read_only_first": True,
        }
        graph = _build_existing_repo_read_only_graph(
            "Existing repo task. Read Makefile first.",
            blueprint,
        )
        cmds = graph.blueprint.get("discovered_commands", {})
        assert "make install" in cmds.get("install", [])
        assert "make test" in cmds.get("test", [])

    def test_graph_has_detected_stack(self, fake_repo):
        blueprint = {
            "project_root": fake_repo,
            "existing_repo": True,
            "read_only_first": True,
        }
        graph = _build_existing_repo_read_only_graph(
            "Existing repo task. Read Makefile first.",
            blueprint,
        )
        stack = graph.blueprint.get("detected_stack", [])
        assert "Make" in stack

    def test_no_writes_in_tool_steps(self, fake_repo):
        blueprint = {
            "project_root": fake_repo,
            "existing_repo": True,
            "read_only_first": True,
        }
        graph = _build_existing_repo_read_only_graph(
            "Existing repo task. Read Makefile first.",
            blueprint,
        )
        for st in graph.subtasks:
            for step in st.tool_steps:
                # Check tool call prefixes, not path substrings
                assert not step.startswith("write_file(")
                assert not step.startswith("create_directory(")
                assert not step.startswith("run_command(\"npm install")
                assert not step.startswith("run_command(\"npm run build")
                assert not step.startswith("run_command(\"pytest")


# ---------------------------------------------------------------------------
# Skill Routing Exclusion Tests
# ---------------------------------------------------------------------------

class TestSkillRoutingExclusion:
    def test_repo_audit_intents_detected(self):
        intents = classify_intent("Existing repo task. Audit the codebase.")
        assert "existing_repo_audit" in intents

    def test_report_intents_detected(self):
        intents = classify_intent("Summarize the repo and recommend one smallest safe fix. Do not modify files.")
        assert any(i in intents for i in ["read_only_report", "focused_fix"])

    def test_repo_audit_excludes_landing_page(self):
        excluded = INTENT_EXCLUDED_SKILLS.get("existing_repo_audit", set())
        assert "landing-page-generator" in excluded
        assert "spec-to-repo" in excluded

    def test_read_only_report_excludes_spec_to_repo(self):
        excluded = INTENT_EXCLUDED_SKILLS.get("read_only_report", set())
        assert "spec-to-repo" in excluded
        assert "landing-page-generator" in excluded

    def test_product_generation_not_excluded(self):
        """Product generation tasks should not exclude spec-to-repo."""
        excluded = INTENT_EXCLUDED_SKILLS.get("product_generation", set())
        assert "spec-to-repo" not in excluded


# ---------------------------------------------------------------------------
# Integration: Full pipeline check
# ---------------------------------------------------------------------------

def test_procureflow_full_pipeline(fake_repo):
    """Full pipeline: detection -> graph -> phases -> commands -> no writes."""
    user_input = (
        "Existing repo task. Project root is {root}. "
        "Read Makefile, README.md, and apps/backend/requirements.txt first. "
        "Report discovered commands and recommend one smallest safe fix. "
        "Do not modify files yet."
    ).format(root=fake_repo)

    # 1. Detection
    assert _is_read_only_first_prompt(user_input)
    assert _is_report_only_task(user_input)

    # 2. File parsing
    files = _parse_user_mentioned_files(user_input)
    assert "Makefile" in files
    assert "README.md" in files

    # 3. Graph construction
    blueprint = {
        "project_root": fake_repo,
        "existing_repo": True,
        "read_only_first": True,
    }
    graph = _build_existing_repo_read_only_graph(user_input, blueprint)
    assert graph is not None
    assert len(graph.subtasks) == 3

    # 4. Commands discovered
    cmds = graph.blueprint.get("discovered_commands", {})
    assert len(cmds.get("install", [])) > 0
    assert len(cmds.get("test", [])) > 0

    # 5. Stack detected
    assert len(graph.blueprint.get("detected_stack", [])) > 0

    # 6. No write/build steps
    for st in graph.subtasks:
        for step in st.tool_steps:
            assert "write_file" not in step
            assert "npm install" not in step
