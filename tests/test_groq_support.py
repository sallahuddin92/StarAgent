import os
import sys
import unittest
import pytest
from unittest.mock import MagicMock, patch
from io import StringIO

from app.model_registry import registry
from app.eval_result_parser import detect_agent_status

class TestGroqSupport(unittest.TestCase):
    def setUp(self):
        # Back up existing .env to .env.bak.class if present, and remove .env
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.dotenv_path = os.path.join(root_dir, ".env")
        self.backup_path_class = os.path.join(root_dir, ".env.bak.class")
        self.has_dotenv_class = os.path.exists(self.dotenv_path)
        if self.has_dotenv_class:
            import shutil
            shutil.copy2(self.dotenv_path, self.backup_path_class)
            os.remove(self.dotenv_path)

    def tearDown(self):
        # Restore .env from .env.bak.class if it was present
        if self.has_dotenv_class and os.path.exists(self.backup_path_class):
            import shutil
            shutil.move(self.backup_path_class, self.dotenv_path)

    def test_groq_provider_inference(self):
        # 1. Registered model always infers groq (to allow CLI missing key checks)
        provider = registry.infer_provider("llama-3.1-8b-instant")
        self.assertEqual(provider, "groq")

        # 2. Unregistered model containing llama-3.1-8b-instant infers groq when key exists
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            provider = registry.infer_provider("unregistered-llama-3.1-8b-instant")
            self.assertEqual(provider, "groq")

        # 3. Unregistered model containing llama-3.1-8b-instant infers ollama when key is missing
        with patch.dict(os.environ, {}):
            if "GROQ_API_KEY" in os.environ:
                del os.environ["GROQ_API_KEY"]
            provider = registry.infer_provider("unregistered-llama-3.1-8b-instant")
            self.assertEqual(provider, "ollama")

    def test_groq_inspect_model(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
            info = registry.inspect_model("llama-3.1-8b-instant")
            self.assertEqual(info["provider"], "groq")
            self.assertEqual(info["installed"], "remote")
            self.assertEqual(info["context_estimate"], 128000)
            self.assertIn("planning", info["recommended_roles"])
            self.assertIn("routing", info["recommended_roles"])

    def test_groq_switch_with_and_without_api_key(self):
        from cli.macagent import main

        # 1. Switch fails when key is missing
        with patch.dict(os.environ, {}):
            if "GROQ_API_KEY" in os.environ:
                del os.environ["GROQ_API_KEY"]
            
            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                rc = main(["model", "switch", "llama-3.1-8b-instant"])
                output = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
            
            self.assertEqual(rc, 1)
            self.assertIn("Groq API key is missing", output)

        # 2. Switch succeeds when key exists
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key-123"}):
            with patch("app.model_registry.registry.set_global_default") as mock_set:
                old_stdout = sys.stdout
                sys.stdout = StringIO()
                try:
                    rc = main(["model", "switch", "llama-3.1-8b-instant"])
                    output = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout
                
                self.assertEqual(rc, 0)
                self.assertIn("Global default model switched to llama-3.1-8b-instant", output)
                mock_set.assert_called_once_with("llama-3.1-8b-instant")

    def test_groq_set_role_allowed(self):
        from cli.macagent import main

        # Set succeeds when key exists
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key-123"}):
            with patch("app.model_registry.registry.set_agent_model") as mock_set:
                old_stdout = sys.stdout
                sys.stdout = StringIO()
                try:
                    rc = main(["model", "set", "ORCHESTRATOR", "llama-3.1-8b-instant"])
                    output = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout
                
                self.assertEqual(rc, 0)
                self.assertIn("Agent ORCHESTRATOR model set to llama-3.1-8b-instant", output)
                mock_set.assert_called_once_with("ORCHESTRATOR", "llama-3.1-8b-instant")

    def test_doctor_skips_ollama_for_groq(self):
        from cli.macagent import _run_doctor

        client_mock = MagicMock()
        client_mock.health.return_value = {
            "ok": True,
            "service": "macagent-proxy",
            "version": "2.0.0",
            "default_model": "llama-3.1-8b-instant"
        }

        mock_env = {
            "GROQ_API_KEY": "test-key",
            "GROQ_BASE_URL": "https://api.groq.com/openai/v1",
            "GROQ_MODEL": "llama-3.1-8b-instant",
            "DEFAULT_MODEL": "llama-3.1-8b-instant"
        }
        with patch.dict(os.environ, mock_env), \
             patch("app.model_registry.get_effective_model_config", return_value={"model": "llama-3.1-8b-instant", "provider": "groq"}):
            
            client_mock._http.get.return_value.json.return_value = {
                "paths": {
                    "/v1/docs/ingest": {},
                    "/v1/docs/search": {},
                    "/v1/docs/ask": {}
                }
            }
            with patch("subprocess.run") as mock_sub:
                mock_sub.return_value.returncode = 0
                mock_sub.return_value.stdout = "PASSED"
                mock_sub.return_value.stderr = ""
                
                old_stdout = sys.stdout
                sys.stdout = StringIO()
                try:
                    rc = _run_doctor(client_mock, {"project_id": "test"})
                    output = sys.stdout.getvalue()
                finally:
                    sys.stdout = old_stdout
                
                self.assertEqual(rc, 0)
                self.assertTrue(any(line in output for line in ["groq_api_key_configured: ok", "groq_api_key_configured: True", "groq_api_key_configured: configured"]))
                self.assertIn("groq_base_url_configured: https://api.groq.com/openai/v1", output)
                self.assertNotIn("default_model_installed", output)

    def test_universal_llm_client_builds_correct_groq_request(self):
        from app.llm_client import UniversalLLMClient
        import httpx
        import asyncio

        mock_http = MagicMock(spec=httpx.AsyncClient)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {"content": "Hello from Groq Mock"}
            }]
        }
        mock_http.post.return_value = mock_response

        client = UniversalLLMClient(mock_http)
        
        mock_env = {
            "GROQ_API_KEY": "groq-key-abc",
            "GROQ_BASE_URL": "https://api.groq.com/openai/v1",
        }
        with patch.dict(os.environ, mock_env):
            async def run_chat():
                return await client.chat(
                    [{"role": "user", "content": "hi"}],
                    model="llama-3.1-8b-instant"
                )
            res = asyncio.run(run_chat())
            self.assertEqual(res["message"]["content"], "Hello from Groq Mock")
            
            # Verify request shape, auth, and timeout
            mock_http.post.assert_called_once()
            args, kwargs = mock_http.post.call_args
            self.assertEqual(args[0], "https://api.groq.com/openai/v1/chat/completions")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer groq-key-abc")
            self.assertEqual(kwargs["json"]["model"], "llama-3.1-8b-instant")
            self.assertEqual(kwargs["timeout"], 900.0)

    def test_eval_parser_handles_groq_compact_output(self):
        # 1. Custom status extraction test
        self.assertEqual(detect_agent_status("[x_agent_status] completed"), "completed")
        self.assertEqual(detect_agent_status("[x_agent_status] failed"), "failed")
        self.assertEqual(detect_agent_status("[x_agent_status] paused"), "paused")
        self.assertEqual(detect_agent_status("[x_agent_status] partial"), "partial")
        
        # 2. Precedence test
        out = """
        [x_agent_status] completed
        [x_agent_status] failed
        """
        self.assertEqual(detect_agent_status(out), "failed")

    def test_start_script_reload_disabled_by_default(self):
        script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "start_macagent.sh")
        with open(script_path, "r") as f:
            content = f.read()
        
        # Reload must be disabled unless STARAGENT_RELOAD=1
        self.assertIn('RELOAD_ARG=""', content)
        self.assertIn('if [[ "${STARAGENT_RELOAD:-0}" == "1" ]]; then', content)
        self.assertIn('RELOAD_ARG="--reload"', content)

    def test_cli_dotenv_loading_scenarios(self):
        from cli.macagent import main, load_dotenv_if_present
        import shutil

        # 1. Back up existing .env if present
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dotenv_path = os.path.join(root_dir, ".env")
        backup_path = os.path.join(root_dir, ".env.bak.test")
        
        has_dotenv = os.path.exists(dotenv_path)
        if has_dotenv:
            shutil.copy2(dotenv_path, backup_path)

        try:
            # Clear GROQ_API_KEY from environment first
            with patch.dict(os.environ, {}, clear=False):
                if "GROQ_API_KEY" in os.environ:
                    del os.environ["GROQ_API_KEY"]

                # Write a test .env with GROQ_API_KEY and other required keys for doctor
                with open(dotenv_path, "w") as f:
                    f.write("GROQ_API_KEY=env-file-key-456\n")
                    f.write("GROQ_BASE_URL=https://api.groq.com/openai/v1\n")
                    f.write("GROQ_MODEL=llama-3.1-8b-instant\n")
                    f.write("DEFAULT_MODEL=llama-3.1-8b-instant\n")

                # Call load_dotenv_if_present
                load_dotenv_if_present()
                
                # Check that GROQ_API_KEY is loaded
                self.assertEqual(os.getenv("GROQ_API_KEY"), "env-file-key-456")

                # Test 1: model set Groq succeeds
                with patch("app.model_registry.registry.set_agent_model") as mock_set:
                    old_stdout = sys.stdout
                    sys.stdout = StringIO()
                    try:
                        rc = main(["model", "set", "ORCHESTRATOR", "llama-3.1-8b-instant"])
                        output = sys.stdout.getvalue()
                    finally:
                        sys.stdout = old_stdout
                    self.assertEqual(rc, 0)
                    self.assertIn("Agent ORCHESTRATOR model set to llama-3.1-8b-instant", output)

                # Test 2: doctor sees GROQ_API_KEY from .env
                # We can call load_dotenv_if_present, and run the doctor on a mock client
                from cli.macagent import _run_doctor
                client_mock = MagicMock()
                client_mock.health.return_value = {"ok": True, "service": "macagent-proxy", "version": "2.0.0", "default_model": "llama-3.1-8b-instant"}
                client_mock._http.get.return_value.json.return_value = {"paths": {"/v1/docs/ingest": {}, "/v1/docs/search": {}, "/v1/docs/ask": {}}}
                with patch("subprocess.run") as mock_sub, \
                     patch("app.model_registry.get_effective_model_config", return_value={"model": "llama-3.1-8b-instant", "provider": "groq"}):
                    mock_sub.return_value.returncode = 0
                    mock_sub.return_value.stdout = "PASSED"
                    mock_sub.return_value.stderr = ""
                    old_stdout = sys.stdout
                    sys.stdout = StringIO()
                    try:
                        rc = _run_doctor(client_mock, {"project_id": "test"})
                        output = sys.stdout.getvalue()
                    finally:
                        sys.stdout = old_stdout
                    self.assertEqual(rc, 0)
                    self.assertTrue(any(line in output for line in ["groq_api_key_configured: ok", "groq_api_key_configured: True", "groq_api_key_configured: configured"]))

            # Test 3: exported env overrides .env
            with patch.dict(os.environ, {"GROQ_API_KEY": "shell-key-789"}, clear=False):
                # Write a test .env with different key
                with open(dotenv_path, "w") as f:
                    f.write("GROQ_API_KEY=env-file-key-456\n")
                
                load_dotenv_if_present()
                # Check that shell key overrides .env key
                self.assertEqual(os.getenv("GROQ_API_KEY"), "shell-key-789")

        finally:
            # Clean up
            if os.path.exists(dotenv_path):
                os.remove(dotenv_path)
            if has_dotenv and os.path.exists(backup_path):
                shutil.move(backup_path, dotenv_path)

    @patch("asyncio.sleep", return_value=None)
    def test_groq_429_rate_limit_retry(self, mock_sleep):
        from app.llm_client import GroqProvider
        import httpx
        import asyncio
        
        mock_http = MagicMock(spec=httpx.AsyncClient)
        # First response is 429
        r_429 = MagicMock(spec=httpx.Response)
        r_429.status_code = 429
        r_429.headers = {"Retry-After": "5"}
        r_429.text = "Rate limit reached. Please try again in 5.84s."
        
        # Second response is 200
        r_200 = MagicMock(spec=httpx.Response)
        r_200.status_code = 200
        r_200.json.return_value = {
            "choices": [{"message": {"content": "Retry success"}}]
        }
        
        mock_http.post.side_effect = [r_429, r_200]
        
        provider = GroqProvider(api_key="test-key", default_model="llama-3.1-8b-instant", http_client=mock_http)
        async def run_chat():
            return await provider.chat([{"role": "user", "content": "hi"}])
        
        res = asyncio.run(run_chat())
        self.assertEqual(res["message"]["content"], "Retry success")
        self.assertEqual(mock_sleep.call_count, 1)
        args, kwargs = mock_sleep.call_args
        # Should wait for 5s parsed from header + jitter (0.1 to 1.0)
        self.assertTrue(5.0 <= args[0] <= 6.0)

    def test_invalid_path_blocked_by_guard(self):
        from app.tool_executor import ToolExecutor
        import asyncio
        
        registry_mock = MagicMock()
        registry_mock.tools = {"list_files": {}}
        executor = ToolExecutor(registry_mock)
        
        # 1. Test "the" blocked
        tc_the = {"id": "call_1", "function": {"name": "list_files", "arguments": '{"path": "the"}'}}
        res = asyncio.run(executor.execute_tool_call(tc_the))
        self.assertIn("[GUARD] Blocked invalid or generic path", res["content"])
        
        # 2. Test "Workspace" blocked
        tc_ws = {"id": "call_2", "function": {"name": "list_files", "arguments": '{"path": "Workspace"}'}}
        res = asyncio.run(executor.execute_tool_call(tc_ws))
        self.assertIn("[GUARD] Blocked invalid or generic path", res["content"])
        
        # 3. Test relative path outside workspace root blocked
        tc_outside = {"id": "call_3", "function": {"name": "list_files", "arguments": '{"path": "../../../etc/passwd"}'}}
        res = asyncio.run(executor.execute_tool_call(tc_outside))
        self.assertIn("[GUARD] Blocked invalid or generic path", res["content"])

    def test_simple_script_fast_path(self):
        from app.main import workflow_engine
        from pathlib import Path
        import shutil
        import asyncio
        
        task_id = "test_fast_path_task"
        tr = {
            "task_id": task_id,
            "project_id": "default",
            "user_goal": "Write a Python script that prints hello",
            "artifacts_json": {"workflow_name": "feature_build"}
        }
        
        scratch_dir = Path.cwd() / "scratch" / task_id
        if scratch_dir.exists():
            shutil.rmtree(scratch_dir)
            
        with patch("app.main.db.get_task_run", return_value=tr), \
             patch("app.main.db.list_task_steps", return_value=[]), \
             patch("app.main.db.create_task_steps"), \
             patch("app.main.db.update_task_run") as mock_update_run, \
             patch("app.main.db.update_task_step") as mock_update_step:
                 
            async def run_wf():
                return await workflow_engine.execute_workflow(task_id)
            
            res = asyncio.run(run_wf())
            
            self.assertTrue(scratch_dir.exists())
            self.assertTrue((scratch_dir / "main.py").exists())
            content = (scratch_dir / "main.py").read_text(encoding="utf-8")
            self.assertIn("hello", content.lower())
            
            mock_update_run.assert_any_call(task_id, {
                "status": "completed",
                "final_verdict": "completed",
                "final_summary": "Simple task executed and verified successfully (Fast Path)."
            })
            
            shutil.rmtree(scratch_dir)

    def test_streaming_error_handling_emits_failed_status(self):
        from app.main import _stream_multi_agent
        import asyncio
        import json
        
        with patch("app.main.workflow_engine.execute_workflow", side_effect=ValueError("Test crash")), \
             patch("app.main.db.create_task_run"), \
             patch("app.main.db.update_task_run"):
            
            async def read_stream():
                chunks = []
                async for chunk in _stream_multi_agent("print hello", "default", "default"):
                    chunks.append(chunk)
                return chunks
            
            chunks = asyncio.run(read_stream())
            err_found = False
            for c in chunks:
                if c.startswith("data: "):
                    data_str = c[6:].strip()
                    if data_str == "[DONE]":
                        continue
                    try:
                        data = json.loads(data_str)
                        if data.get("x_agent_status") == "failed":
                            err_found = True
                    except Exception:
                        pass
            self.assertTrue(err_found)

    def test_flat_json_tool_call_parsing(self):
        from app.executor import Executor, ToolExecutor
        import asyncio
        import json
        
        registry_mock = MagicMock()
        registry_mock.tools = {"write_file": {}}
        tool_executor = ToolExecutor(registry_mock)
        executor = Executor(
            llm_client=MagicMock(),
            tool_executor=tool_executor,
            approval_policy=MagicMock(),
            reflection_layer=MagicMock()
        )
        
        # Test parsing of a flat tool call structure
        flat_call_str = """
        {
          "tool": "write_file",
          "path": "scratch/eval_backend/entry_points.md",
          "content": "# Entry Points"
        }
        """
        
        step_res = asyncio.run(executor.execute_step(flat_call_str, None))
        tool_calls = step_res.get("tool_calls")
        self.assertIsNotNone(tool_calls)
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0]["function"]["name"], "write_file")
        
        args = json.loads(tool_calls[0]["function"]["arguments"])
        self.assertEqual(args["path"], "scratch/eval_backend/entry_points.md")
        self.assertEqual(args["content"], "# Entry Points")
        self.assertNotIn("tool", args)


if __name__ == "__main__":
    unittest.main()
