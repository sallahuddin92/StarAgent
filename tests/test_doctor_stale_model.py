import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from io import StringIO
from pathlib import Path
import json

from app.model_registry import get_effective_model_config, registry
from app.eval_result_parser import detect_agent_status
from cli.macagent import _run_doctor


class TestDoctorStaleModel(unittest.TestCase):
    def setUp(self):
        # Reset registry global default state
        registry.global_default = None
        registry._global_default = None

    def tearDown(self):
        # Cleanup environment variables
        if "DEFAULT_MODEL" in os.environ:
            del os.environ["DEFAULT_MODEL"]

    def test_get_effective_model_config_priority(self):
        # 1. Fallback default
        with patch("app.model_registry.get_dotenv_default_model", return_value=None):
            cfg = get_effective_model_config()
            self.assertEqual(cfg["model"], "gemma4:12b-mlx")

        # 2. .env file priority (mock get_dotenv_default_model)
        with patch("app.model_registry.get_dotenv_default_model", return_value="gemma4:12b-mlx"):
            cfg = get_effective_model_config()
            self.assertEqual(cfg["model"], "gemma4:12b-mlx")

        # 3. Environment variable priority
        with patch.dict(os.environ, {"DEFAULT_MODEL": "qwen2.5-coder:14b"}):
            with patch("app.model_registry.get_dotenv_default_model", return_value="gemma4:12b-mlx"):
                cfg = get_effective_model_config()
                self.assertEqual(cfg["model"], "qwen2.5-coder:14b")

        # 4. models.json (registry setter representing config file) priority
        registry.global_default = "claude-3-5-sonnet"
        with patch.dict(os.environ, {"DEFAULT_MODEL": "qwen2.5-coder:14b"}):
            with patch("app.model_registry.get_dotenv_default_model", return_value="gemma4:12b-mlx"):
                cfg = get_effective_model_config()
                self.assertEqual(cfg["model"], "claude-3-5-sonnet")

    def test_detect_agent_status_errors(self):
        # Test standard parsing
        self.assertEqual(detect_agent_status("[x_agent_status] completed"), "completed")
        self.assertEqual(detect_agent_status("[x_agent_status] failed"), "failed")

        # Test error fallback parsing
        self.assertEqual(detect_agent_status("[WORKFLOW] [ERROR] local model not found"), "failed")
        self.assertEqual(detect_agent_status("Some stdout\n[ERROR] database lock timeout\nSome stderr"), "failed")
        self.assertEqual(detect_agent_status("Traceback (most recent call last):\n  File \"app.py\", line 12\nIndexError: list index out of range"), "failed")

    def test_doctor_evaluates_active_model_without_stale_warnings(self):
        # Mock client health
        client_mock = MagicMock()
        client_mock.health.return_value = {
            "ok": True,
            "service": "macagent-proxy",
            "version": "2.0.0",
            "default_model": "gemma4:12b-mlx"
        }
        client_mock.root_base_url = "http://127.0.0.1:8095"
        client_mock.v1_base_url = "http://127.0.0.1:8095/v1"

        # Mock openapi routes get request
        client_mock._http.get.return_value.json.return_value = {
            "paths": {
                "/v1/docs/ingest": {},
                "/v1/docs/search": {},
                "/v1/docs/ask": {}
            }
        }

        # Configure registry to have gemma4:12b-mlx installed
        with patch.object(registry, "list_local_ollama_models", return_value=[{"name": "gemma4:12b-mlx"}]), \
             patch.object(registry, "is_local_ollama_model_installed", side_effect=lambda m, refresh=False: m == "gemma4:12b-mlx"), \
             patch.dict(os.environ, {"DEFAULT_MODEL": "gemma4:12b-mlx"}), \
             patch("subprocess.run") as mock_sub:

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
            # Should have checked active profile for gemma4:12b-mlx
            self.assertIn("active_model_profile", output)
            self.assertIn("model=gemma4:12b-mlx", output)
            # Default model installed should be checked for gemma4:12b-mlx
            self.assertIn("default_model_installed: gemma4:12b-mlx", output)
            # Should not warn about gemma4:e2b
            self.assertNotIn("gemma4:e2b", output)

    def test_main_default_model_fallback_without_env(self):
        # Verify app.main's get_default_model resolves to gemma4:12b-mlx when env is absent
        test_db = str(Path.cwd() / "data" / "test_memory_pytest.db")
        with patch.dict(os.environ, {"DATABASE_PATH": test_db}, clear=True):
            with patch("app.model_registry.get_dotenv_default_model", return_value=None), \
                 patch("dotenv.load_dotenv", return_value=None):
                from app.main import get_default_model
                self.assertEqual(get_default_model(), "gemma4:12b-mlx")


if __name__ == "__main__":
    unittest.main()
