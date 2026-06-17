from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.blueprint import generate_blueprint
from app.multi_agent import AgentRole, BaseAgent, SubTask, decompose_task_llm


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_blueprint_uses_existing_absolute_repo_root(tmp_path):
    repo = tmp_path / "procureflow_saas"
    repo.mkdir()
    (repo / "README.md").write_text("hello", encoding="utf-8")
    (repo / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")

    llm = MagicMock()
    llm.text = AsyncMock(
        return_value='{"project_root":"scratch/ignored","structure":["main.py"],"required_semantics":[],"required_commands":[],"required_output_keywords":[],"task_type":"script"}'
    )
    task = f"Read-only first inspect and audit {repo}. Do not modify files."
    bp = await generate_blueprint(task, llm)

    assert bp["project_root"] == str(repo)
    assert bp["existing_repo"] is True
    assert bp["read_only_first"] is True


class _NoopLLM:
    provider = "ollama"
    model_name = "gemma4:e2b"

    async def text(self, messages, **kwargs):
        return '{"subtasks": [], "checklist": {}}'


@pytest.mark.anyio
async def test_decompose_existing_repo_read_only_avoids_writes_and_scratch(tmp_path):
    repo = tmp_path / "procureflow_saas"
    (repo / "apps" / "backend").mkdir(parents=True)
    (repo / "README.md").write_text("hello", encoding="utf-8")
    (repo / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    (repo / "apps" / "backend" / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

    llm = _NoopLLM()
    task = f"Read-only audit and inspect {repo}. Identify risks and next safe fix. Do not modify files."
    graph = await decompose_task_llm(task, llm)

    assert graph.blueprint.get("project_root") == str(repo)
    assert graph.blueprint.get("read_only_first") is True

    all_steps = [step for st in graph.subtasks for step in (st.tool_steps or [])]
    assert all_steps
    assert any("get_file_tree" in s for s in all_steps)
    assert any("read_file" in s for s in all_steps)
    assert not any("write_file" in s for s in all_steps)
    assert not any("create_directory" in s for s in all_steps)
    assert not any("run_command" in s for s in all_steps)
    assert not any("scratch/repo" in s for s in all_steps)


class _ExecutorStub:
    async def execute_step(self, step, workspace):
        if "list_files" in step:
            return {
                "tool_calls": [
                    {
                        "function": {"name": "list_files", "arguments": '{"path": "/tmp"}'},
                    }
                ]
            }
        return {"tool_calls": []}


class _ToolExecutorStub:
    async def execute_tool_call(self, tc):
        return {"content": "[Success] ok"}


@pytest.mark.anyio
async def test_path_guard_blocks_scratch_write_for_existing_repo():
    agent = BaseAgent(
        role=AgentRole.BACKEND,
        executor=_ExecutorStub(),
        tool_executor=_ToolExecutorStub(),
        llm_client=None,
    )
    subtask = SubTask(
        id="x",
        role=AgentRole.BACKEND,
        description="inspect existing repo",
        tool_steps=[
            'write_file("scratch/repo/main.py", "print(1)")',
            'list_files("/tmp")',
        ],
        requirements={"project_root": "/tmp"},
        blueprint={"project_root": "/tmp", "existing_repo": True, "read_only_first": False, "allow_scratch_writes": False},
    )
    out = await agent._run_inner(subtask)
    assert out.status == "completed"

