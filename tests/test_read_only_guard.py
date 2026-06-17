"""Tests for read-only-first guard enforcement.

Validates that ALL agents (including ORCHESTRATOR) are blocked from
mutating tools during the read-only-first phase.
"""
from __future__ import annotations

import asyncio
import pytest

from app.multi_agent import (
    AgentRole,
    BaseAgent,
    SubTask,
    _is_read_only_first_prompt,
    _build_existing_repo_read_only_graph,
    _parse_user_mentioned_files,
    _is_read_only_safe_command,
    _is_blocked_mutating_command,
    _is_blocked_directory,
    READ_ONLY_ALLOWED_TOOLS,
)
from app.blueprint import _looks_read_only_first, _parse_blocked_directories


# ---------------------------------------------------------------------------
# Detection tests (Component 1)
# ---------------------------------------------------------------------------

class TestMarkerDetection:
    def test_classic_read_only(self):
        assert _looks_read_only_first("Inspect the repo") is True
        assert _is_read_only_first_prompt("Inspect the repo") is True

    def test_read_first_pattern(self):
        task = "Read Makefile, README.md, and apps/backend/requirements.txt first."
        assert _looks_read_only_first(task) is True
        assert _is_read_only_first_prompt(task) is True

    def test_existing_repo_marker(self):
        assert _looks_read_only_first("Existing repo task.") is True
        assert _is_read_only_first_prompt("Existing repo task.") is True

    def test_do_not_create(self):
        assert _looks_read_only_first("Do not create backend/frontend folders.") is True
        assert _is_read_only_first_prompt("Do not create backend/frontend folders.") is True

    def test_smallest_safe_fix(self):
        assert _looks_read_only_first("If one smallest safe fix is obvious, apply that.") is True

    def test_stop_with_report(self):
        assert _looks_read_only_first("Otherwise stop with a report.") is True

    def test_normal_task_not_detected(self):
        assert _looks_read_only_first("Create a FastAPI backend with /health endpoint") is False
        assert _is_read_only_first_prompt("Build a calculator app") is False


# ---------------------------------------------------------------------------
# Blocked directory parsing (Component 4)
# ---------------------------------------------------------------------------

class TestBlockedDirectories:
    def test_parse_backend_frontend(self):
        dirs = _parse_blocked_directories("Do not create backend/frontend folders.")
        assert "backend" in dirs
        assert "frontend" in dirs

    def test_parse_with_and(self):
        dirs = _parse_blocked_directories("Don't create backend and frontend directories.")
        assert "backend" in dirs
        assert "frontend" in dirs

    def test_no_blocked(self):
        dirs = _parse_blocked_directories("Create a FastAPI backend with /health")
        assert dirs == []

    def test_is_blocked_directory_abs(self):
        assert _is_blocked_directory(
            "/Users/x/repo/backend", "/Users/x/repo", ["backend", "frontend"]
        ) is True
        assert _is_blocked_directory(
            "/Users/x/repo/frontend", "/Users/x/repo", ["backend", "frontend"]
        ) is True
        assert _is_blocked_directory(
            "/Users/x/repo/apps", "/Users/x/repo", ["backend", "frontend"]
        ) is False

    def test_is_blocked_directory_relative(self):
        assert _is_blocked_directory("scratch/repo/backend", "scratch/repo", ["backend"]) is True


# ---------------------------------------------------------------------------
# Command classification (Component 2)
# ---------------------------------------------------------------------------

class TestCommandClassification:
    def test_safe_commands(self):
        assert _is_read_only_safe_command("ls -la") is True
        assert _is_read_only_safe_command("cat README.md") is True
        assert _is_read_only_safe_command("find . -name '*.py'") is True
        assert _is_read_only_safe_command("grep -r TODO .") is True
        assert _is_read_only_safe_command("head -20 Makefile") is True

    def test_unsafe_commands(self):
        assert _is_read_only_safe_command("npm install") is False
        assert _is_read_only_safe_command("python3 main.py") is False
        assert _is_read_only_safe_command("make build") is False

    def test_blocked_mutating(self):
        assert _is_blocked_mutating_command("npm install") is True
        assert _is_blocked_mutating_command("npm run build") is True
        assert _is_blocked_mutating_command("pytest") is True
        assert _is_blocked_mutating_command("python3 main.py") is True
        assert _is_blocked_mutating_command("make") is True
        assert _is_blocked_mutating_command("pip install flask") is True
        assert _is_blocked_mutating_command("cat README.md") is False


# ---------------------------------------------------------------------------
# User-mentioned file parsing (Component 5)
# ---------------------------------------------------------------------------

class TestUserMentionedFiles:
    def test_parse_read_first(self):
        files = _parse_user_mentioned_files(
            "Read Makefile, README.md, and apps/backend/requirements.txt first."
        )
        assert "Makefile" in files
        assert "README.md" in files
        assert "apps/backend/requirements.txt" in files

    def test_no_match(self):
        files = _parse_user_mentioned_files("Create a FastAPI backend")
        assert files == []


# ---------------------------------------------------------------------------
# Step execution guard (Component 2 + 3 — integration)
# ---------------------------------------------------------------------------

class _CaptureLLM:
    provider = "ollama"
    model_name = "gemma4:e2b"

    async def text(self, messages, **kwargs):
        return ""


class _CaptureQueue:
    """Async queue that records all messages."""
    def __init__(self):
        self.messages: list[str] = []

    async def put(self, msg: str):
        self.messages.append(msg)


def _make_read_only_blueprint(project_root: str = "/tmp/test_repo") -> dict:
    return {
        "project_root": project_root,
        "existing_repo": True,
        "read_only_first": True,
        "task_type": "repo_audit",
        "structure": [],
        "required_semantics": [],
        "required_commands": [],
        "required_output_keywords": [],
        "blocked_directories": ["backend", "frontend"],
    }


@pytest.mark.anyio
async def test_orchestrator_cannot_create_backend_in_read_only():
    """ORCHESTRATOR must not create backend/ under read-only-first."""
    queue = _CaptureQueue()
    agent = BaseAgent(
        role=AgentRole.ORCHESTRATOR,
        executor=None,
        tool_executor=None,
        llm_client=_CaptureLLM(),
        stream_queue=queue,
    )
    from app.model_profiles import get_active_profile
    agent.current_model = "gemma4:e2b"
    agent.profile = get_active_profile("ollama", "gemma4:e2b")

    subtask = SubTask(
        id="bad_orch",
        role=AgentRole.ORCHESTRATOR,
        description="Create project structure",
        tool_steps=[
            'create_directory("/tmp/test_repo/backend")',
            'create_directory("/tmp/test_repo/frontend")',
            'read_file("/tmp/test_repo/README.md")',
        ],
        blueprint=_make_read_only_blueprint("/tmp/test_repo"),
    )

    result = await agent._run_inner(subtask)
    msgs = " ".join(queue.messages)
    assert "[GUARD] blocked create_directory" in msgs
    # read_file should NOT be blocked
    assert 'read_file' not in msgs or "[GUARD] blocked read_file" not in msgs


@pytest.mark.anyio
async def test_orchestrator_cannot_run_npm_install_in_read_only():
    """ORCHESTRATOR must not run npm install under read-only-first."""
    queue = _CaptureQueue()
    agent = BaseAgent(
        role=AgentRole.ORCHESTRATOR,
        executor=None,
        tool_executor=None,
        llm_client=_CaptureLLM(),
        stream_queue=queue,
    )
    from app.model_profiles import get_active_profile
    agent.current_model = "gemma4:e2b"
    agent.profile = get_active_profile("ollama", "gemma4:e2b")

    subtask = SubTask(
        id="bad_npm",
        role=AgentRole.ORCHESTRATOR,
        description="Install dependencies",
        tool_steps=[
            'run_command("npm install", "/tmp/test_repo")',
            'run_command("npm run build", "/tmp/test_repo")',
            'run_command("pytest", "/tmp/test_repo")',
            'run_command("python3 main.py", "/tmp/test_repo")',
        ],
        blueprint=_make_read_only_blueprint("/tmp/test_repo"),
    )

    result = await agent._run_inner(subtask)
    msgs = " ".join(queue.messages)
    assert "[GUARD] blocked run_command" in msgs
    assert "npm install" in msgs or "npm run" in msgs


@pytest.mark.anyio
async def test_read_only_allows_read_file():
    """read_file, list_files, get_file_tree must pass through the guard."""
    queue = _CaptureQueue()
    agent = BaseAgent(
        role=AgentRole.ORCHESTRATOR,
        executor=None,
        tool_executor=None,
        llm_client=_CaptureLLM(),
        stream_queue=queue,
    )
    from app.model_profiles import get_active_profile
    agent.current_model = "gemma4:e2b"
    agent.profile = get_active_profile("ollama", "gemma4:e2b")

    subtask = SubTask(
        id="good_read",
        role=AgentRole.ORCHESTRATOR,
        description="Read repo files",
        tool_steps=[
            'read_file("/tmp/test_repo/README.md")',
            'list_files("/tmp/test_repo")',
            'get_file_tree("/tmp/test_repo")',
        ],
        blueprint=_make_read_only_blueprint("/tmp/test_repo"),
    )

    result = await agent._run_inner(subtask)
    msgs = " ".join(queue.messages)
    # None of these should be blocked
    assert "[GUARD] blocked read_file" not in msgs
    assert "[GUARD] blocked list_files" not in msgs
    assert "[GUARD] blocked get_file_tree" not in msgs


@pytest.mark.anyio
async def test_guard_emits_summary():
    """After blocking, guard must emit the summary line."""
    queue = _CaptureQueue()
    agent = BaseAgent(
        role=AgentRole.ORCHESTRATOR,
        executor=None,
        tool_executor=None,
        llm_client=_CaptureLLM(),
        stream_queue=queue,
    )
    from app.model_profiles import get_active_profile
    agent.current_model = "gemma4:e2b"
    agent.profile = get_active_profile("ollama", "gemma4:e2b")

    subtask = SubTask(
        id="guard_summary",
        role=AgentRole.ORCHESTRATOR,
        description="Bad steps",
        tool_steps=[
            'create_directory("/tmp/test_repo/backend")',
            'run_command("npm install")',
        ],
        blueprint=_make_read_only_blueprint("/tmp/test_repo"),
    )

    result = await agent._run_inner(subtask)
    msgs = " ".join(queue.messages)
    assert "[GUARD] no writes performed during read-only-first ✅" in msgs


@pytest.mark.anyio
async def test_backend_agent_also_blocked_in_read_only():
    """The guard must apply to ALL agents, not just ORCHESTRATOR."""
    queue = _CaptureQueue()
    agent = BaseAgent(
        role=AgentRole.BACKEND,
        executor=None,
        tool_executor=None,
        llm_client=_CaptureLLM(),
        stream_queue=queue,
    )
    from app.model_profiles import get_active_profile
    agent.current_model = "gemma4:e2b"
    agent.profile = get_active_profile("ollama", "gemma4:e2b")

    subtask = SubTask(
        id="bad_backend",
        role=AgentRole.BACKEND,
        description="Write code",
        tool_steps=[
            'write_file("/tmp/test_repo/main.py", "print(1)")',
            'run_command("python3 main.py", "/tmp/test_repo")',
        ],
        blueprint=_make_read_only_blueprint("/tmp/test_repo"),
    )

    result = await agent._run_inner(subtask)
    msgs = " ".join(queue.messages)
    assert "[GUARD] blocked write_file" in msgs
    assert "[GUARD] blocked run_command" in msgs
