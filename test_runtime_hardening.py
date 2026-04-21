import asyncio
import json
from pathlib import Path

import httpx

from app.agent import AgentLoop
from app.approval import ApprovalPolicy
from app.database import DatabaseManager
from app.executor import Executor
from app.memory import MemoryStore
from app.models import MemoryState
from app.planner import Planner
from app.reflection import ReflectionLayer
from app.routing import AGENT_PATH, FAST_PATH, determine_execution_route
from app.tool_executor import ToolExecutor
from app.tools import ToolRegistry


class MockReflectionClient:
    async def post(self, url, json=None, **kwargs):
        payload = {
            "success": True,
            "confidence": 0.99,
            "summary": "Mock reflection summary.",
            "should_retry": False,
            "should_replan": False,
            "next_action_hint": "Continue.",
            "memory_worthy": True,
        }
        return type(
            "MockResp",
            (),
            {
                "status_code": 200,
                "json": lambda self: {"message": {"content": json_module.dumps(payload)}},
                "text": "",
            },
        )()


class StaticExecutorClient:
    def __init__(self, responses=None, exception=None):
        self.responses = responses or []
        self.exception = exception
        self.calls = 0

    async def post(self, url, json=None, **kwargs):
        self.calls += 1
        if self.exception is not None:
            raise self.exception
        index = min(self.calls - 1, len(self.responses) - 1)
        content = self.responses[index]
        if isinstance(content, Exception):
            raise content
        return type(
            "MockResp",
            (),
            {"status_code": 200, "json": lambda self: {"message": {"content": content}}, "text": ""},
        )()


class StaticPlanner:
    def __init__(self, plan, revised_plan=None):
        self.plan = plan
        self.revised_plan = revised_plan or plan
        self.revise_calls = 0

    async def generate_plan(self, user_input, memory):
        return json.loads(json.dumps(self.plan))

    async def revise_plan(self, current_plan, failed_step_id, feedback, memory):
        self.revise_calls += 1
        return json.loads(json.dumps(self.revised_plan))


class FailingExecutor:
    def __init__(self, output="Missing dependency", tool_calls_used=1):
        self.output = output
        self.tool_calls_used = tool_calls_used

    async def execute_step(self, step, memory, max_tool_loops=4, resume_tool=None):
        return {"success": False, "output": self.output, "tool_calls_used": self.tool_calls_used}


json_module = json


def run(coro):
    return asyncio.run(coro)


def extract_json(text):
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise AssertionError(f"No JSON payload found in: {text}")


def make_runtime(tmp_path):
    db = DatabaseManager(str(tmp_path / "runtime.db"))
    registry = ToolRegistry(str(tmp_path), db)
    tool_executor = ToolExecutor(registry)
    reflection = ReflectionLayer(MockReflectionClient(), "mock", request_timeout=5.0)
    return db, registry, tool_executor, reflection


def reflect(reflection, step_action, tool_name, tool_args, tool_result, memory):
    return run(reflection.evaluate(step_action, tool_name, tool_args, tool_result, memory))


def test_fast_path_basic_chat_test():
    memory = MemoryState(conversation_id="conv_fast", project_id="proj_fast")

    route = determine_execution_route("What is 2 + 2? Answer in one short sentence.", memory)

    assert route["route"] == FAST_PATH
    assert route["reason"] == "simple_chat_or_direct_qa"


def test_agent_path_tool_task_test():
    memory = MemoryState(conversation_id="conv_agent", project_id="proj_agent")

    route = determine_execution_route(
        "Inspect the app folder, identify the main API entry file, and summarize how requests flow through the system.",
        memory,
    )

    assert route["route"] == AGENT_PATH


def test_schema_repair_test(tmp_path):
    _, _, tool_executor, reflection = make_runtime(tmp_path)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")

    alias_result = tool_executor.execute_tool_call("proj_schema", "list_files", {"directory": "app"}, None)
    assert alias_result["success"] is True
    assert "app/main.py" in alias_result["output"]
    missing_path_result = tool_executor.execute_tool_call("proj_schema", "list_files", {}, None)
    assert missing_path_result["success"] is True
    assert "app/main.py" in missing_path_result["output"]

    executor = Executor(
        StaticExecutorClient(
            [
                json.dumps(
                    {
                        "action_type": "tool_call",
                        "tool_name": "list_files",
                        "arguments": {"path": ["app"]},
                    }
                ),
                json.dumps(
                    {
                        "action_type": "final_response",
                        "output": "Recovered after schema retry.",
                        "success": True,
                    }
                ),
            ]
        ),
        "mock",
        tool_executor,
        reflection,
        ApprovalPolicy(),
        request_timeout=5.0,
    )
    memory = MemoryState(conversation_id="conv_schema", project_id="proj_schema")

    result = run(executor.execute_step({"id": 1, "type": "read_only_tool", "action": "List app files"}, memory))

    assert result["success"] is True
    assert "Recovered after schema retry." in result["output"]
    assert executor.ollama_client.calls == 2


def test_memory_search_live_scope_test(tmp_path):
    db, _, tool_executor, _ = make_runtime(tmp_path)
    db.get_or_create_conversation("conv-a", "project-a")
    db.get_or_create_conversation("conv-b", "project-b")
    db.add_memory_item("conv-a", "project-a", "decision", "Use FastAPI as the backend framework.", memory_type="DECISION", priority=5)
    db.add_memory_item("conv-b", "project-b", "decision", "Use Django as the backend framework.", memory_type="DECISION", priority=5)

    result = tool_executor.execute_tool_call("project-a", "search_memory_or_files", {"query": "FastAPI"}, None)

    assert result["success"] is True
    assert "FastAPI" in result["output"]
    assert "Django" not in result["output"]
    assert result["metadata"]["project_id"] == "project-a"


def test_verification_policy_test(tmp_path):
    _, _, tool_executor, reflection = make_runtime(tmp_path)
    memory = MemoryState(conversation_id="conv_verify", project_id="proj_verify")

    target = tmp_path / "sample.py"
    target.write_text("print('before')\n", encoding="utf-8")

    write_args = {"path": "sample.py", "content": "print('after')\n", "mode": "overwrite", "action_id": "act_verify"}
    write_result = tool_executor.execute_tool_call(memory.project_id, "write_file", write_args, memory)
    write_reflection = reflect(reflection, "write sample file", "write_file", write_args, write_result, memory)

    weak_result = tool_executor.execute_tool_call(memory.project_id, "file_exists", {"path": "sample.py"}, memory)
    weak_reflection = reflect(reflection, "weak verify", "file_exists", {"path": "sample.py", "action_id": "act_verify"}, weak_result, memory)

    strong_result = tool_executor.execute_tool_call(memory.project_id, "syntax_check", {"path": "sample.py", "action_id": "act_verify"}, memory)
    strong_reflection = reflect(reflection, "strong verify", "syntax_check", {"path": "sample.py", "action_id": "act_verify"}, strong_result, memory)

    fail_args = {"path": "sample.py", "content": "print('broken')\n", "mode": "overwrite", "action_id": "act_fail"}
    fail_write = tool_executor.execute_tool_call(memory.project_id, "write_file", fail_args, memory)
    assert fail_write["success"] is True
    failed_verify = tool_executor.execute_tool_call(
        memory.project_id,
        "run_tests",
        {"command": "python3 -c \"import sys; sys.exit(1)\"", "action_id": "act_fail"},
        memory,
    )
    failed_reflection = reflect(reflection, "failing verify", "run_tests", {"action_id": "act_fail"}, failed_verify, memory)

    assert write_reflection["change_status"] == "unverified success"
    assert weak_reflection["change_status"] == "unverified success"
    assert strong_reflection["change_status"] == "verified success"
    assert failed_reflection["change_status"] == "rollback recommended"


def test_approval_resume_live_flow_test(tmp_path):
    db, _, tool_executor, reflection = make_runtime(tmp_path)
    approval = ApprovalPolicy()
    planner = Planner(MockReflectionClient(), "mock", request_timeout=5.0)
    memory = MemoryState(conversation_id="conv_approval", project_id="proj_approval")
    memory.active_plan = {
        "goal": "Write sandbox file",
        "steps": [{"id": 1, "type": "write_tool_requires_approval", "action": "Create sandbox file", "completed": False}],
    }

    target = tmp_path / "sandbox.txt"
    executor = Executor(
        StaticExecutorClient(
            [
                json.dumps(
                    {
                        "action_type": "tool_call",
                        "tool_name": "write_file",
                        "arguments": {"path": "sandbox.txt", "content": "hello\n", "mode": "overwrite"},
                    }
                )
            ]
        ),
        "mock",
        tool_executor,
        reflection,
        approval,
        request_timeout=5.0,
    )
    agent = AgentLoop(planner, executor)

    first_output = run(agent.run("Create the file.", memory, db))
    assert "awaiting_approval" in first_output
    assert target.exists() is False

    reject_output = run(agent.run("no", memory, db))
    assert "Execution rejected natively" in reject_output
    assert target.exists() is False

    memory.active_plan = {
        "goal": "Write sandbox file",
        "steps": [{"id": 1, "type": "write_tool_requires_approval", "action": "Create sandbox file", "completed": False}],
    }
    executor.ollama_client = StaticExecutorClient(
        [
            json.dumps(
                {
                    "action_type": "tool_call",
                    "tool_name": "write_file",
                    "arguments": {"path": "sandbox.txt", "content": "hello\n", "mode": "overwrite"},
                }
            ),
            json.dumps(
                {
                    "action_type": "final_response",
                    "output": "Sandbox file written successfully.",
                    "success": True,
                }
            ),
        ]
    )

    second_output = run(agent.run("Create the file again.", memory, db))
    assert "awaiting_approval" in second_output

    resume_output = run(agent.run("yes", memory, db))
    resume_payload = extract_json(resume_output)
    assert resume_payload["phase"] == "final_summary_ready"
    assert target.read_text(encoding="utf-8") == "hello\n"
    assert memory.workspace_state.get("pending_approval") is None


def test_small_model_timeout_resilience_test(tmp_path):
    _, _, tool_executor, reflection = make_runtime(tmp_path)
    executor = Executor(
        StaticExecutorClient(exception=httpx.TimeoutException("slow model")),
        "mock",
        tool_executor,
        reflection,
        ApprovalPolicy(),
        request_timeout=1.0,
    )
    memory = MemoryState(conversation_id="conv_timeout", project_id="proj_timeout")

    result = run(executor.execute_step({"id": 1, "type": "reasoning_only", "action": "Answer a simple question"}, memory))

    assert result["success"] is False
    assert "Executor timeout waiting for Ollama" in result["output"]


def test_workspace_state_persistence_for_approval_routing(tmp_path):
    db = DatabaseManager(str(tmp_path / "persist.db"))
    store = MemoryStore(base_dir=str(tmp_path / "memory"), db_manager=db)
    memory = MemoryState(conversation_id="conv_persist", project_id="proj_persist")
    memory.active_plan = {"goal": "pending write", "steps": [{"id": 1, "type": "write_tool_requires_approval", "action": "Create file"}]}
    memory.workspace_state = {
        "pending_approval": {
            "step_id": 1,
            "tool_name": "write_file",
            "arguments": {"path": "sandbox.txt", "content": "hello", "mode": "overwrite"},
            "reason": "approval required",
            "diff_summary": "--- diff ---",
        }
    }

    store.save(memory)
    reloaded = store.load("conv_persist", "proj_persist")

    assert reloaded.active_plan == memory.active_plan
    assert reloaded.workspace_state["pending_approval"]["tool_name"] == "write_file"


def test_bounded_repo_inspection_test(tmp_path):
    db, _, tool_executor, reflection = make_runtime(tmp_path)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "def determine_execution_route():\n    pass\n"
        "async def chat_completions():\n    await agent_loop.run('x', None, None)\n"
        "async def _run_fast_path_chat():\n    pass\n",
        encoding="utf-8",
    )
    planner = StaticPlanner(
        {
            "goal": "inspect repo",
            "steps": [{"id": 1, "type": "read_only_tool", "action": "Inspect the app folder, identify the main API entry file, and summarize how requests flow through the system.", "completed": False}],
        }
    )
    executor = Executor(
        StaticExecutorClient(
            [
                json.dumps({"action_type": "tool_call", "tool_name": "list_files", "arguments": {"path": "app"}}),
                json.dumps({"action_type": "tool_call", "tool_name": "read_file", "arguments": {"path": "app/main.py"}}),
            ]
        ),
        "mock",
        tool_executor,
        reflection,
        ApprovalPolicy(),
        request_timeout=5.0,
    )
    agent = AgentLoop(planner, executor, max_steps=3, max_replans=1, max_planner_revisions=1, max_tool_calls=3, max_executor_loops=3, max_duration_seconds=30.0)
    memory = MemoryState(conversation_id="conv_repo", project_id="proj_repo")

    output = run(agent.run("Inspect the app folder, identify the main API entry file, and summarize how requests flow through the system.", memory, db))
    payload = extract_json(output)

    assert payload["phase"] == "final_summary_ready"
    assert "app/main.py" in payload["summary"]
    assert "FastAPI" in payload["summary"]


def test_post_approval_fast_completion_test(tmp_path):
    db, _, tool_executor, reflection = make_runtime(tmp_path)
    approval = ApprovalPolicy()
    planner = Planner(MockReflectionClient(), "mock", request_timeout=5.0)
    memory = MemoryState(conversation_id="conv_post_approval", project_id="proj_post_approval")
    memory.active_plan = {
        "goal": "Write sandbox file",
        "steps": [{"id": 1, "type": "write_tool_requires_approval", "action": "Create sandbox file", "completed": False}],
    }
    target = tmp_path / "sandbox.txt"
    executor = Executor(
        StaticExecutorClient(
            [
                json.dumps({"action_type": "tool_call", "tool_name": "write_file", "arguments": {"path": "sandbox.txt", "content": "hello\n", "mode": "overwrite"}}),
            ]
        ),
        "mock",
        tool_executor,
        reflection,
        approval,
        request_timeout=5.0,
    )
    agent = AgentLoop(planner, executor, max_steps=3, max_replans=1, max_planner_revisions=1, max_tool_calls=3, max_executor_loops=2, max_duration_seconds=30.0)

    first_output = run(agent.run("Create the file.", memory, db))
    assert "awaiting_approval" in first_output

    resume_output = run(agent.run("yes", memory, db))
    payload = extract_json(resume_output)

    assert payload["phase"] == "final_summary_ready"
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_max_replan_limit_test(tmp_path):
    db, _, _, _ = make_runtime(tmp_path)
    planner = StaticPlanner(
        {"goal": "inspect repo", "steps": [{"id": 1, "type": "read_only_tool", "action": "Inspect app", "completed": False}]}
    )
    executor = FailingExecutor("Still missing dependency", tool_calls_used=1)
    agent = AgentLoop(planner, executor, max_steps=4, max_replans=1, max_planner_revisions=1, max_tool_calls=4, max_executor_loops=1, max_duration_seconds=30.0)
    memory = MemoryState(conversation_id="conv_replan", project_id="proj_replan")

    output = run(agent.run("Inspect app", memory, db))
    payload = extract_json(output)

    assert payload["status"] == "partial_complete"
    assert payload["stop_reason"] == "replan_limit"
    assert memory.active_plan is not None


def test_partial_answer_on_timeout_test(tmp_path):
    _, _, tool_executor, reflection = make_runtime(tmp_path)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
    executor = Executor(
        StaticExecutorClient(
            [
                json.dumps({"action_type": "tool_call", "tool_name": "list_files", "arguments": {"path": "app"}}),
                httpx.TimeoutException("slow followup"),
            ]
        ),
        "mock",
        tool_executor,
        reflection,
        ApprovalPolicy(),
        request_timeout=1.0,
    )
    memory = MemoryState(conversation_id="conv_partial_timeout", project_id="proj_partial_timeout")

    result = run(executor.execute_step({"id": 1, "type": "read_only_tool", "action": "Inspect the app folder and identify the main API entry file."}, memory, max_tool_loops=3))
    payload = extract_json(result["output"])

    assert result["success"] is True
    assert payload["status"] == "partial_complete"
    assert payload["phase"] in {"inspection_complete", "analysis_pending"}


def test_resume_after_partial_completion_test(tmp_path):
    db = DatabaseManager(str(tmp_path / "resume.db"))
    store = MemoryStore(base_dir=str(tmp_path / "memory"), db_manager=db)
    registry = ToolRegistry(str(tmp_path), db)
    tool_executor = ToolExecutor(registry)
    reflection = ReflectionLayer(MockReflectionClient(), "mock", request_timeout=5.0)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\nasync def chat_completions():\n    pass\n",
        encoding="utf-8",
    )
    planner = StaticPlanner(
        {
            "goal": "inspect repo",
            "steps": [
                {"id": 1, "type": "read_only_tool", "action": "Inspect app folder", "completed": False},
                {"id": 2, "type": "read_only_tool", "action": "Read the contents of app/main.py and summarize request flow", "completed": False},
            ],
        }
    )
    executor = Executor(
        StaticExecutorClient([json.dumps({"action_type": "tool_call", "tool_name": "list_files", "arguments": {"path": "app"}})]),
        "mock",
        tool_executor,
        reflection,
        ApprovalPolicy(),
        request_timeout=5.0,
    )
    agent = AgentLoop(planner, executor, max_steps=3, max_replans=1, max_planner_revisions=1, max_tool_calls=3, max_executor_loops=1, max_duration_seconds=30.0)
    memory = MemoryState(conversation_id="conv_resume", project_id="proj_resume")

    first_output = run(agent.run("Inspect the app folder", memory, db))
    first_payload = extract_json(first_output)
    assert first_payload["phase"] == "inspection_complete"

    store.save(memory)
    reloaded = store.load("conv_resume", "proj_resume")
    route = determine_execution_route("continue", reloaded)
    assert route["route"] == AGENT_PATH

    executor.ollama_client = StaticExecutorClient([json.dumps({"action_type": "tool_call", "tool_name": "read_file", "arguments": {"path": "app/main.py"}})])
    second_output = run(agent.run("continue", reloaded, db))
    second_payload = extract_json(second_output)

    assert second_payload["phase"] == "final_summary_ready"
