"""
Unit tests for StarAgent Executor tool-call parser.

Covers:
  - run_command: 5 forms (positional, two-positional, mixed, all-keyword, JSON dict)
  - write_file: positional, keyword, JSON dict, multiline
  - create_directory: positional, keyword, bare
  - Unsafe tool name rejection
  - Required arg validation
  - End-to-end execute_step dispatch

Run with: python3 -m pytest tests/test_executor_parser.py -v
"""

import asyncio
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock
from app.executor import Executor


def _make_executor():
    """Create an Executor with mocked dependencies."""
    llm = MagicMock()
    tool_exec = MagicMock()
    approval = MagicMock()
    reflection = MagicMock()
    return Executor(llm, tool_exec, approval, reflection)


# ===================================================================
# run_command — all 5 forms
# ===================================================================

class TestRunCommandParser:
    def setup_method(self):
        self.executor = _make_executor()

    def _parse(self, args_str):
        return self.executor._parse_tool_args("run_command", args_str)

    def test_form1_single_positional(self):
        """run_command("pytest")"""
        result = self._parse('"pytest"')
        assert result == {"command": "pytest", "cwd": "."}

    def test_form2_two_positional(self):
        """run_command("pytest", "scratch/app")"""
        result = self._parse('"pytest", "scratch/app"')
        assert result == {"command": "pytest", "cwd": "scratch/app"}

    def test_form3_positional_plus_keyword(self):
        """run_command("pytest", cwd="scratch/app")"""
        result = self._parse('"pytest", cwd="scratch/app"')
        assert result == {"command": "pytest", "cwd": "scratch/app"}

    def test_form4_all_keyword(self):
        """run_command(command="pytest", cwd="scratch/app")"""
        result = self._parse('command="pytest", cwd="scratch/app"')
        assert result == {"command": "pytest", "cwd": "scratch/app"}

    def test_form5_json_dict(self):
        """run_command({"command":"pytest","cwd":"scratch/app"})"""
        result = self._parse('{"command":"pytest","cwd":"scratch/app"}')
        assert result == {"command": "pytest", "cwd": "scratch/app"}

    def test_json_dict_default_cwd(self):
        """run_command({"command":"pytest"}) should default cwd to ."""
        result = self._parse('{"command":"pytest"}')
        assert result == {"command": "pytest", "cwd": "."}

    def test_complex_command_positional_keyword(self):
        """run_command("pip install -r requirements.txt", cwd="scratch/eval_backend")"""
        result = self._parse('"pip install -r requirements.txt", cwd="scratch/eval_backend"')
        assert result == {"command": "pip install -r requirements.txt", "cwd": "scratch/eval_backend"}

    def test_python3_command(self):
        result = self._parse('"python3 scratch/hello.py"')
        assert result == {"command": "python3 scratch/hello.py", "cwd": "."}

    def test_single_quotes(self):
        result = self._parse("'pytest -v', cwd='scratch/app'")
        assert result == {"command": "pytest -v", "cwd": "scratch/app"}

    def test_default_cwd(self):
        result = self._parse('command="pytest"')
        assert result["cwd"] == "."
        assert result["command"] == "pytest"


# ===================================================================
# write_file — positional, keyword, JSON dict, multiline
# ===================================================================

class TestWriteFileParser:
    def setup_method(self):
        self.executor = _make_executor()

    def _parse(self, args_str):
        return self.executor._parse_tool_args("write_file", args_str)

    def test_positional(self):
        result = self._parse('"scratch/hello.py", "print(\'hello\')"')
        assert result["path"] == "scratch/hello.py"
        assert "hello" in result["content"]

    def test_keyword(self):
        result = self._parse('path="scratch/hello.py", content="print(\'hello\')"')
        assert result["path"] == "scratch/hello.py"
        assert "hello" in result["content"]

    def test_json_dict(self):
        """write_file({"path":"test.py","content":"print('hi')"})"""
        result = self._parse('{"path":"test.py","content":"print(\'hi\')"}')
        assert result["path"] == "test.py"
        assert "hi" in result["content"]

    def test_json_dict_default_content(self):
        """write_file({"path":"test.py"}) should default content to empty."""
        result = self._parse('{"path":"test.py"}')
        assert result["path"] == "test.py"
        assert result["content"] == ""

    def test_multiline_content(self):
        result = self._parse('path="test.py", content="line1\\nline2"')
        assert result["path"] == "test.py"
        assert "line1" in result["content"]
        assert "line2" in result["content"]


# ===================================================================
# create_directory — positional, keyword, bare
# ===================================================================

class TestCreateDirectoryParser:
    def setup_method(self):
        self.executor = _make_executor()

    def _parse(self, args_str):
        return self.executor._parse_tool_args("create_directory", args_str)

    def test_positional(self):
        result = self._parse('"scratch/myapp"')
        assert result == {"path": "scratch/myapp"}

    def test_keyword(self):
        result = self._parse('path="scratch/myapp"')
        assert result == {"path": "scratch/myapp"}

    def test_bare_path(self):
        result = self._parse('scratch/myapp')
        assert result == {"path": "scratch/myapp"}


class TestGetFileTreeParser:
    def setup_method(self):
        self.executor = _make_executor()

    def _parse(self, arg_str):
        return self.executor._parse_tool_args("get_file_tree", arg_str)

    def test_positional_path(self):
        result = self._parse('"/tmp/repo"')
        assert result == {"path": "/tmp/repo"}


# ===================================================================
# Safety: unsafe function names and required arg validation
# ===================================================================

class TestSafety:
    def setup_method(self):
        self.executor = _make_executor()

    def test_reject_unsafe_tool(self):
        """Unknown tool names must be rejected."""
        result = self.executor._parse_tool_args("os_system", '"rm -rf /"')
        assert result is None

    def test_reject_eval(self):
        result = self.executor._parse_tool_args("eval", '"malicious_code()"')
        assert result is None

    def test_reject_exec(self):
        result = self.executor._parse_tool_args("exec", '"import os"')
        assert result is None

    def test_accept_known_tools(self):
        """All known safe tools should be accepted."""
        for tool in Executor.SAFE_TOOLS:
            result = self.executor._parse_tool_args(tool, "")
            assert result == {}, f"{tool} should accept empty args"

    def test_missing_required_command(self):
        """run_command without command arg must return None."""
        result = self.executor._parse_tool_args("run_command", '{"cwd":"scratch"}')
        assert result is None

    def test_missing_required_path(self):
        """write_file without path must return None."""
        result = self.executor._parse_tool_args("write_file", '{"content":"hello"}')
        assert result is None

    def test_empty_command_rejected(self):
        """run_command({"command":"","cwd":"."}) should be rejected."""
        result = self.executor._parse_tool_args("run_command", '{"command":"","cwd":"."}')
        assert result is None


# ===================================================================
# Error handling
# ===================================================================

class TestParseErrorHandling:
    def setup_method(self):
        self.executor = _make_executor()

    def test_empty_args_returns_empty_dict(self):
        result = self.executor._parse_tool_args("run_command", "")
        assert result == {}

    @pytest.mark.anyio
    async def test_parse_failure_streams_error(self):
        """execute_step should return error content when parser returns None."""
        workspace = MagicMock()
        result = await self.executor.execute_step('dangerous_hack("rm -rf /")', workspace)
        assert "tool_calls" not in result
        assert "Error" in result.get("content", "")


# ===================================================================
# End-to-end execute_step dispatch
# ===================================================================

class TestEndToEndExecuteStep:
    def setup_method(self):
        self.executor = _make_executor()

    @pytest.mark.anyio
    async def test_run_command_mixed_args(self):
        workspace = MagicMock()
        result = await self.executor.execute_step('run_command("pip install -r requirements.txt", cwd="scratch/eval_backend")', workspace)
        assert "tool_calls" in result
        tc = result["tool_calls"][0]
        args = json.loads(tc["function"]["arguments"])
        assert args["command"] == "pip install -r requirements.txt"
        assert args["cwd"] == "scratch/eval_backend"

    @pytest.mark.anyio
    async def test_run_command_json_dict(self):
        workspace = MagicMock()
        result = await self.executor.execute_step('run_command({"command":"pytest -v","cwd":"scratch/app"})', workspace)
        assert "tool_calls" in result
        tc = result["tool_calls"][0]
        args = json.loads(tc["function"]["arguments"])
        assert args["command"] == "pytest -v"
        assert args["cwd"] == "scratch/app"

    @pytest.mark.anyio
    async def test_create_directory_keyword(self):
        workspace = MagicMock()
        result = await self.executor.execute_step('create_directory(path="scratch/myapp")', workspace)
        assert "tool_calls" in result
        tc = result["tool_calls"][0]
        args = json.loads(tc["function"]["arguments"])
        assert args["path"] == "scratch/myapp"

    @pytest.mark.anyio
    async def test_write_file_json_dict(self):
        workspace = MagicMock()
        result = await self.executor.execute_step('write_file({"path":"test.py","content":"print(1)"})', workspace)
        assert "tool_calls" in result
        tc = result["tool_calls"][0]
        args = json.loads(tc["function"]["arguments"])
        assert args["path"] == "test.py"
        assert args["content"] == "print(1)"


# ===================================================================
# Command normalization (pip/pytest/python → venv executable)
# ===================================================================

class TestCommandNormalization:
    """Test that Python-related commands are normalized to use the venv interpreter."""

    def setup_method(self):
        import sys
        self.py = sys.executable
        from app.tools import ToolRegistry
        self.norm = ToolRegistry._normalize_python_command

    def test_pip_install(self):
        result = self.norm("pip install fastapi")
        assert result == f"{self.py} -m pip install fastapi"

    def test_pip3_install(self):
        result = self.norm("pip3 install requests")
        assert result == f"{self.py} -m pip install requests"

    def test_pip_install_requirements(self):
        result = self.norm("pip install -r requirements.txt")
        assert result == f"{self.py} -m pip install -r requirements.txt"

    def test_pytest(self):
        result = self.norm("pytest")
        assert result == f"PYTHONPATH=. {self.py} -m pytest -q"

    def test_pytest_with_args(self):
        result = self.norm("pytest test_main.py -v")
        assert result == f"PYTHONPATH=. {self.py} -m pytest test_main.py -v"

    def test_python_m_pytest(self):
        result = self.norm("python -m pytest")
        assert result == f"PYTHONPATH=. {self.py} -m pytest -q"

    def test_python3_m_pytest(self):
        result = self.norm("python3 -m pytest")
        assert result == f"PYTHONPATH=. {self.py} -m pytest -q"

    def test_npm_unchanged(self):
        """Non-Python commands must NOT be rewritten."""
        assert self.norm("npm run build") == "npm run build"

    def test_echo_unchanged(self):
        assert self.norm("echo hello") == "echo hello"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
