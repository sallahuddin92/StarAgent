import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from cli import macagent as cli_mod
from app.model_registry import ModelInfo


class CLILiveModelTests(unittest.TestCase):
    @patch("cli.macagent.MacAgentClient")
    def test_model_list_uses_live_local_models(self, MockClient):
        inst = MockClient.return_value
        inst.close.return_value = None

        from app.model_registry import registry

        with patch.object(registry, "list_local_ollama_models", return_value=[{"name": "llama3.1:8b"}]), patch.object(
            registry,
            "get_registry_suggestions",
            return_value=[
                ModelInfo(id="qwen2.5-coder:14b", provider="ollama"),
                ModelInfo(id="gpt-4o", provider="openai"),
            ],
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = cli_mod.main(["model", "list"])
        self.assertEqual(rc, 0)
        txt = out.getvalue()
        self.assertIn("LOCAL OLLAMA MODELS:", txt)
        self.assertIn("- llama3.1:8b", txt)
        self.assertIn("CONFIGURED REMOTE MODELS:", txt)
        self.assertIn("REGISTRY SUGGESTIONS:", txt)

    @patch("cli.macagent.MacAgentClient")
    def test_model_switch_rejects_missing_local_model(self, MockClient):
        inst = MockClient.return_value
        inst.close.return_value = None

        from app.model_registry import registry

        with patch.object(registry, "infer_provider", return_value="ollama"), patch.object(
            registry, "is_local_ollama_model_installed", return_value=False
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = cli_mod.main(["model", "switch", "qwen2.5-coder:14b"])
        self.assertEqual(rc, 1)
        txt = out.getvalue()
        self.assertIn("Model qwen2.5-coder:14b is not installed locally.", txt)
        self.assertIn("ollama pull qwen2.5-coder:14b", txt)

    @patch("cli.macagent.MacAgentClient")
    def test_model_set_allows_longcat_for_backend_agent_when_configured(self, MockClient):
        inst = MockClient.return_value
        inst.close.return_value = None

        from app.model_registry import registry

        env = {
            "LONGCAT_API_KEY": "k",
            "LONGCAT_MODEL": "LongCat-Flash-Chat",
            "LONGCAT_BASE_URL": "https://api.longcat.chat/openai",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            registry, "infer_provider", return_value="longcat"
        ), patch.object(registry, "set_agent_model") as set_model:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = cli_mod.main(["model", "set", "BACKEND_AGENT", "LongCat-Flash-Chat"])
        self.assertEqual(rc, 0)
        set_model.assert_called_once_with("BACKEND_AGENT", "LongCat-Flash-Chat")

    @patch("cli.macagent.MacAgentClient")
    def test_model_set_allows_longcat_for_non_backend_agent(self, MockClient):
        inst = MockClient.return_value
        inst.close.return_value = None
    
        from app.model_registry import registry
    
        env = {
            "LONGCAT_API_KEY": "k",
            "LONGCAT_MODEL": "LongCat-Flash-Chat",
            "LONGCAT_BASE_URL": "https://api.longcat.chat/openai",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            registry, "infer_provider", return_value="longcat"
        ), patch("app.model_registry.registry.set_agent_model") as mock_set:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = cli_mod.main(["model", "set", "FRONTEND_AGENT", "LongCat-Flash-Chat"])
        self.assertEqual(rc, 0)
        self.assertIn("Agent FRONTEND_AGENT model set to LongCat-Flash-Chat", out.getvalue())
        mock_set.assert_called_once_with("FRONTEND_AGENT", "LongCat-Flash-Chat")

    def test_doctor_warns_missing_configured_ollama_model(self):
        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"paths": {"/v1/docs/ingest": {}, "/v1/docs/search": {}, "/v1/docs/ask": {}}}

        class _HTTP:
            def get(self, url, *args, **kwargs):
                return _Resp()

        fake_client = Mock()
        fake_client.health.return_value = {"ok": True, "service": "staragent", "version": "x", "default_model": "missing:model"}
        fake_client._http = _HTTP()
        fake_client.root_base_url = "http://127.0.0.1:8095"
        fake_client.v1_base_url = "http://127.0.0.1:8095/v1"
        fake_client.config.default_model = "missing:model"

        from app.model_registry import registry

        with patch("cli.macagent.subprocess.run") as mock_run, patch.object(
            registry, "list_local_ollama_models", return_value=[{"name": "llama3.1:8b"}]
        ), patch.object(registry, "is_local_ollama_model_installed", side_effect=lambda m, refresh=False: m == "llama3.1:8b"), patch.object(
            registry, "agent_routing", {"BACKEND_AGENT": "missing:model"}
        ), patch("app.model_registry.get_effective_model_config", return_value={"model": "missing:model", "provider": "ollama"}):
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok"
            mock_run.return_value.stderr = ""
            out = io.StringIO()
            with redirect_stdout(out):
                rc = cli_mod._run_doctor(fake_client, {"project_id": "p"}, as_json=False)
        self.assertEqual(rc, 1)
        txt = out.getvalue()
        self.assertIn("configured_model_missing_warning", txt)
        self.assertIn("WARN: missing local model(s): missing:model", txt)


if __name__ == "__main__":
    unittest.main()
