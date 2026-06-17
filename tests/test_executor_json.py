import asyncio
import json
import pytest
from unittest.mock import MagicMock
from app.executor import Executor
from app.workspace_state import WorkspaceTracker
from app.tool_executor import ToolExecutor

@pytest.mark.anyio
async def test_json_tool_parsing():
    # Setup mocks
    llm_client = MagicMock()
    tool_executor = MagicMock()
    approval_policy = MagicMock()
    reflection_layer = MagicMock()
    workspace = WorkspaceTracker()
    
    executor = Executor(llm_client, tool_executor, approval_policy, reflection_layer)
    
    # Test JSON format
    json_step = '{"tool": "write_file", "args": {"path": "test.txt", "content": "hello"}}'
    result = await executor.execute_step(json_step, workspace)
    
    assert result["role"] == "assistant"
    assert result["tool_calls"][0]["function"]["name"] == "write_file"
    args = json.loads(result["tool_calls"][0]["function"]["arguments"])
    assert args["path"] == "test.txt"
    assert args["content"] == "hello"

@pytest.mark.anyio
async def test_invalid_json_fallback():
    llm_client = MagicMock()
    tool_executor = MagicMock()
    approval_policy = MagicMock()
    reflection_layer = MagicMock()
    workspace = WorkspaceTracker()
    
    executor = Executor(llm_client, tool_executor, approval_policy, reflection_layer)
    
    # Invalid JSON should not crash and should not be parsed as a tool call if it doesn't match other patterns
    invalid_json = '{"tool": "missing_args"}'
    result = await executor.execute_step(invalid_json, workspace)
    
    # Should just be treated as content or failed parse
    assert "tool_calls" not in result or result["tool_calls"] == []


class _DummyRegistry:
    def __init__(self):
        self.tools = {
            "run_command": {"handler": self.run_command},
            "write_file": {"handler": self.write_file},
            "create_directory": {"handler": self.create_directory},
            "list_files": {"handler": self.list_files},
        }
        self.last_call = None

    def run_command(self, command: str, cwd: str = "."):
        self.last_call = ("run_command", {"command": command, "cwd": cwd})
        return f"cmd={command} cwd={cwd}"

    def write_file(self, path: str, content: str):
        self.last_call = ("write_file", {"path": path, "content": content})
        return f"write={path}"

    def create_directory(self, path: str):
        self.last_call = ("create_directory", {"path": path})
        return f"mkdir={path}"

    def list_files(self, path: str):
        self.last_call = ("list_files", {"path": path})
        return f"ls={path}"


@pytest.mark.anyio
async def test_tool_executor_run_command_list_command_tokens():
    reg = _DummyRegistry()
    te = ToolExecutor(reg)
    tool_call = {
        "id": "1",
        "function": {
            "name": "run_command",
            "arguments": json.dumps(["python3", "scratch/app/main.py"]),
        },
    }
    out = await te.execute_tool_call(tool_call)
    assert "cmd=python3 scratch/app/main.py cwd=." in out["content"]
    assert reg.last_call == (
        "run_command",
        {"command": "python3 scratch/app/main.py", "cwd": "."},
    )


@pytest.mark.anyio
async def test_tool_executor_run_command_list_command_plus_cwd():
    reg = _DummyRegistry()
    te = ToolExecutor(reg)
    tool_call = {
        "id": "2",
        "function": {
            "name": "run_command",
            "arguments": json.dumps(["python3 main.py", "scratch/app"]),
        },
    }
    out = await te.execute_tool_call(tool_call)
    assert "cmd=python3 main.py cwd=scratch/app" in out["content"]
    assert reg.last_call == (
        "run_command",
        {"command": "python3 main.py", "cwd": "scratch/app"},
    )


@pytest.mark.anyio
async def test_tool_executor_write_file_list_args():
    reg = _DummyRegistry()
    te = ToolExecutor(reg)
    tool_call = {
        "id": "3",
        "function": {
            "name": "write_file",
            "arguments": json.dumps(["path.py", "print('x')"]),
        },
    }
    out = await te.execute_tool_call(tool_call)
    assert "write=path.py" in out["content"]
    assert reg.last_call == (
        "write_file",
        {"path": "path.py", "content": "print('x')"},
    )


@pytest.mark.anyio
async def test_tool_executor_create_directory_list_args():
    reg = _DummyRegistry()
    te = ToolExecutor(reg)
    tool_call = {
        "id": "4",
        "function": {
            "name": "create_directory",
            "arguments": json.dumps(["scratch/app"]),
        },
    }
    out = await te.execute_tool_call(tool_call)
    assert "mkdir=scratch/app" in out["content"]
    assert reg.last_call == ("create_directory", {"path": "scratch/app"})


@pytest.mark.anyio
async def test_tool_executor_invalid_list_args_rejected():
    reg = _DummyRegistry()
    te = ToolExecutor(reg)
    tool_call = {
        "id": "5",
        "function": {
            "name": "write_file",
            "arguments": json.dumps(["only-path.py"]),
        },
    }
    out = await te.execute_tool_call(tool_call)
    assert out["content"].startswith("Error: Invalid list args for write_file; expected [path, content]")


@pytest.mark.anyio
async def test_multiple_json_tool_parsing():
    llm_client = MagicMock()
    tool_executor = MagicMock()
    approval_policy = MagicMock()
    reflection_layer = MagicMock()
    workspace = WorkspaceTracker()
    
    executor = Executor(llm_client, tool_executor, approval_policy, reflection_layer)
    
    # Input has two separate JSON blocks
    multiple_json = """
    {
      "tool": "write_file",
      "params": {
        "path": "a.txt",
        "content": "A"
      }
    }
    {
      "tool": "run_command",
      "params": {
        "command": "cat a.txt"
      }
    }
    """
    
    result = await executor.execute_step(multiple_json, workspace)
    
    assert result["role"] == "assistant"
    assert len(result["tool_calls"]) == 2
    
    # Check first tool call
    assert result["tool_calls"][0]["function"]["name"] == "write_file"
    args0 = json.loads(result["tool_calls"][0]["function"]["arguments"])
    assert args0["path"] == "a.txt"
    assert args0["content"] == "A"
    
    # Check second tool call
    assert result["tool_calls"][1]["function"]["name"] == "run_command"
    args1 = json.loads(result["tool_calls"][1]["function"]["arguments"])
    assert args1["command"] == "cat a.txt"


@pytest.mark.anyio
async def test_truncated_json_healing():
    llm_client = MagicMock()
    tool_executor = MagicMock()
    approval_policy = MagicMock()
    reflection_layer = MagicMock()
    workspace = WorkspaceTracker()
    
    executor = Executor(llm_client, tool_executor, approval_policy, reflection_layer)
    
    truncated_json = '{"tool": "write_file", "path": "abc.txt", "content": "hello'
    result = await executor.execute_step(truncated_json, workspace)
    
    assert result["role"] == "assistant"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["function"]["name"] == "write_file"
    args = json.loads(result["tool_calls"][0]["function"]["arguments"])
    assert args["path"] == "abc.txt"
    assert args["content"] == "hello"

