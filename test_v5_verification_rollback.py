import asyncio
import json
from pathlib import Path

from app.approval import ApprovalPolicy
from app.executor import Executor
from app.models import MemoryState
from app.reflection import ReflectionLayer
from app.tool_executor import ToolExecutor
from app.tools import ToolRegistry
from app.database import DatabaseManager


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


class MockExecutorClient:
    def __init__(self, responses):
        self.responses = responses
        self.index = 0

    async def post(self, url, json=None, **kwargs):
        content = self.responses[self.index]
        self.index = min(self.index + 1, len(self.responses) - 1)
        return type(
            "MockResp",
            (),
            {"status_code": 200, "json": lambda self: {"message": {"content": content}}, "text": ""},
        )()


json_module = json


def make_runtime(tmp_path):
    db = DatabaseManager("sqlite:///:memory:")
    registry = ToolRegistry(str(tmp_path), db)
    tool_executor = ToolExecutor(registry)
    reflection = ReflectionLayer(MockReflectionClient(), "mock")
    return db, registry, tool_executor, reflection


def reflect(reflection, step_action, tool_name, tool_args, tool_result, memory):
    return asyncio.run(reflection.evaluate(step_action, tool_name, tool_args, tool_result, memory))


def test_write_then_test_success(tmp_path):
    _, _, tool_executor, reflection = make_runtime(tmp_path)
    memory = MemoryState(conversation_id="conv_success", project_id="proj_success")

    write_args = {"path": "app/example.py", "content": "print('ok')\n", "mode": "overwrite", "action_id": "act_success"}
    write_result = tool_executor.execute_tool_call(memory.project_id, "write_file", write_args, memory)
    write_reflection = reflect(reflection, "write file", "write_file", write_args, write_result, memory)

    assert write_result["success"] is True
    assert write_reflection["change_status"] == "unverified success"
    assert memory.workspace_state["modified_files_by_action"]["act_success"] == ["app/example.py"]

    test_result = tool_executor.execute_tool_call(
        memory.project_id,
        "run_tests",
        {"command": "python3 -c \"print('tests ok')\"", "action_id": "act_success"},
        memory,
    )
    verify_reflection = reflect(reflection, "verify write", "run_tests", {"action_id": "act_success"}, test_result, memory)

    assert test_result["success"] is True
    assert verify_reflection["change_status"] == "verified success"
    assert memory.workspace_state["verification_results"]["act_success"]["success"] is True


def test_write_then_test_fail(tmp_path):
    _, _, tool_executor, reflection = make_runtime(tmp_path)
    memory = MemoryState(conversation_id="conv_fail", project_id="proj_fail")

    write_args = {"path": "app/example.py", "content": "print('broken')\n", "mode": "overwrite", "action_id": "act_fail"}
    write_result = tool_executor.execute_tool_call(memory.project_id, "write_file", write_args, memory)
    assert write_result["success"] is True

    fail_result = tool_executor.execute_tool_call(
        memory.project_id,
        "run_tests",
        {"command": "python3 -c \"import sys; sys.exit(1)\"", "action_id": "act_fail"},
        memory,
    )
    fail_reflection = reflect(reflection, "verify write", "run_tests", {"action_id": "act_fail"}, fail_result, memory)

    assert fail_result["success"] is False
    assert fail_reflection["change_status"] == "rollback recommended"
    assert fail_reflection["rollback_available"] is True
    assert memory.workspace_state["verification_results"]["act_fail"]["success"] is False


def test_rollback_after_failed_write(tmp_path):
    _, _, tool_executor, reflection = make_runtime(tmp_path)
    memory = MemoryState(conversation_id="conv_rb", project_id="proj_rb")

    file_path = tmp_path / "feature.py"
    file_path.write_text("print('before')\n", encoding="utf-8")

    write_args = {"path": "feature.py", "content": "print('after')\n", "mode": "overwrite", "action_id": "act_rb"}
    write_result = tool_executor.execute_tool_call(memory.project_id, "write_file", write_args, memory)
    assert write_result["success"] is True

    fail_result = tool_executor.execute_tool_call(
        memory.project_id,
        "run_tests",
        {"command": "python3 -c \"import sys; sys.exit(1)\"", "action_id": "act_rb"},
        memory,
    )
    fail_reflection = reflect(reflection, "verify write", "run_tests", {"action_id": "act_rb"}, fail_result, memory)
    assert fail_reflection["change_status"] == "rollback recommended"

    rollback_result = tool_executor.execute_tool_call(memory.project_id, "rollback_last_action", {"action_id": "act_rb"}, memory)
    assert rollback_result["success"] is True
    assert file_path.read_text(encoding="utf-8") == "print('before')\n"


def test_rollback_restores_previous_content(tmp_path):
    _, _, tool_executor, _ = make_runtime(tmp_path)
    memory = MemoryState(conversation_id="conv_restore", project_id="proj_restore")

    target = tmp_path / "notes.txt"
    target.write_text("original content\n", encoding="utf-8")

    edit_result = tool_executor.execute_tool_call(
        memory.project_id,
        "edit_file",
        {"path": "notes.txt", "find": "original", "replace": "updated", "action_id": "act_restore"},
        memory,
    )
    assert edit_result["success"] is True
    assert target.read_text(encoding="utf-8") == "updated content\n"

    rollback_result = tool_executor.execute_tool_call(memory.project_id, "rollback_last_action", {"action_id": "act_restore"}, memory)
    assert rollback_result["success"] is True
    assert target.read_text(encoding="utf-8") == "original content\n"


def test_diff_preview_before_apply(tmp_path):
    db = DatabaseManager("sqlite:///:memory:")
    registry = ToolRegistry(str(tmp_path), db)
    tool_executor = ToolExecutor(registry)
    approval = ApprovalPolicy()
    reflection = ReflectionLayer(MockReflectionClient(), "mock")
    executor = Executor(
        MockExecutorClient(
            [
                json.dumps(
                    {
                        "action_type": "tool_call",
                        "tool_name": "write_file",
                        "arguments": {
                            "path": "preview.txt",
                            "content": "new line\n",
                            "mode": "overwrite",
                        },
                    }
                )
            ]
        ),
        "mock",
        tool_executor,
        reflection,
        approval,
    )
    memory = MemoryState(conversation_id="conv_preview", project_id="proj_preview")
    (tmp_path / "preview.txt").write_text("old line\n", encoding="utf-8")

    result = asyncio.run(executor.execute_step({"id": 1, "type": "write_tool_requires_approval", "action": "Update preview"}, memory))

    assert result["output"] == "AWAITING_APPROVAL"
    assert result["approval_msg"]["diff_summary"] is not None
    assert "-old line" in result["approval_msg"]["diff_summary"]
    assert "+new line" in result["approval_msg"]["diff_summary"]
    assert (tmp_path / "preview.txt").read_text(encoding="utf-8") == "old line\n"


def test_multi_project_rollback_isolation(tmp_path):
    _, _, tool_executor, _ = make_runtime(tmp_path)
    memory_a = MemoryState(conversation_id="conv_a", project_id="project_a")
    memory_b = MemoryState(conversation_id="conv_b", project_id="project_b")

    path_a = tmp_path / "project_a" / "shared.txt"
    path_b = tmp_path / "project_b" / "shared.txt"
    path_a.parent.mkdir(parents=True, exist_ok=True)
    path_b.parent.mkdir(parents=True, exist_ok=True)
    path_a.write_text("alpha\n", encoding="utf-8")
    path_b.write_text("beta\n", encoding="utf-8")

    result_a = tool_executor.execute_tool_call(
        memory_a.project_id,
        "write_file",
        {"path": "project_a/shared.txt", "content": "alpha changed\n", "mode": "overwrite", "action_id": "act_a"},
        memory_a,
    )
    result_b = tool_executor.execute_tool_call(
        memory_b.project_id,
        "write_file",
        {"path": "project_b/shared.txt", "content": "beta changed\n", "mode": "overwrite", "action_id": "act_b"},
        memory_b,
    )

    assert result_a["success"] is True
    assert result_b["success"] is True

    rollback_a = tool_executor.execute_tool_call(memory_a.project_id, "rollback_last_action", {"action_id": "act_a"}, memory_a)
    assert rollback_a["success"] is True
    assert path_a.read_text(encoding="utf-8") == "alpha\n"
    assert path_b.read_text(encoding="utf-8") == "beta changed\n"
    assert memory_b.workspace_state["actions"]["act_b"]["rollback_available"] is True
