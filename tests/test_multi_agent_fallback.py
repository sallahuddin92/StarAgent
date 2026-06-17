from __future__ import annotations

import pytest

from app.multi_agent import (
    AgentRole,
    BaseAgent,
    OrchestratorAgent,
    SubTask,
    decompose_task_llm,
)


class CaptureLLM:
    provider = "ollama"
    model_name = "gemma4:e2b"

    def __init__(self):
        self.last_messages = None

    async def text(self, messages, **kwargs):
        self.last_messages = messages
        return '{"tool":"write_file","args":{"path":"scratch/x/main.py","content":"print(1)"}}'


@pytest.mark.anyio
async def test_fastapi_small_model_decomposition_uses_deterministic_fallback():
    llm = CaptureLLM()
    task = "Create a FastAPI backend with /health endpoint in scratch/eval_backend, write pytest test, run the test"
    graph = await decompose_task_llm(task, llm)

    assert graph.blueprint["project_root"] == "scratch/eval_backend"
    ids = [s.id for s in graph.subtasks]
    assert ids == ["dirs", "write", "run"]

    write = graph.subtasks[1]
    assert write.role == AgentRole.BACKEND
    assert write.requirements.get("required_files") == [
        "scratch/eval_backend/main.py",
        "scratch/eval_backend/test_main.py",
    ]

    run = graph.subtasks[2]
    assert run.tool_steps == ['run_command("PYTHONPATH=. python3 -m pytest -q", "scratch/eval_backend")']


@pytest.mark.anyio
async def test_backend_repair_prompt_contains_missing_required_files():
    llm = CaptureLLM()
    agent = BaseAgent(
        role=AgentRole.BACKEND,
        executor=None,
        tool_executor=None,
        llm_client=llm,
    )
    from app.model_profiles import get_active_profile
    agent.current_model = "gemma4:e2b"
    agent.profile = get_active_profile("ollama", "gemma4:e2b")
    subtask = SubTask(
        id="write",
        role=AgentRole.BACKEND,
        description="Implement backend",
        requirements={"project_root": "scratch/demo", "path": "scratch/demo/main.py"},
        result="Missing required files for this subtask:\n- scratch/demo/main.py\n- scratch/demo/test_main.py",
        blueprint={
            "project_root": "scratch/demo",
            "structure": ["scratch/demo/main.py", "scratch/demo/test_main.py"],
            "required_semantics": [],
            "required_commands": ["python3 main.py"],
        },
    )

    await agent._plan_steps(subtask)
    prompt_text = (llm.last_messages or [{}])[-1].get("content", "")
    assert "PREVIOUS FAILURE CONTEXT" in prompt_text
    assert "scratch/demo/main.py" in prompt_text
    assert "scratch/demo/test_main.py" in prompt_text


def test_missing_expected_files_for_subtask():
    subtask = SubTask(
        id="write",
        role=AgentRole.BACKEND,
        description="Implement backend",
        requirements={"required_files": ["scratch/missing/main.py", "scratch/missing/test_main.py"]},
    )
    missing = OrchestratorAgent._missing_expected_files_for_subtask(subtask, {"project_root": "scratch/missing", "structure": []})
    assert "scratch/missing/main.py" in missing
    assert "scratch/missing/test_main.py" in missing
