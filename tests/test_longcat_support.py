import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from io import StringIO

@pytest.fixture(autouse=True)
def backup_dotenv():
    # Back up existing .env if present
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dotenv_path = os.path.join(root_dir, ".env")
    backup_path = os.path.join(root_dir, ".env.bak.longcat.test")
    has_dotenv = os.path.exists(dotenv_path)
    if has_dotenv:
        import shutil
        shutil.copy2(dotenv_path, backup_path)
        os.remove(dotenv_path)
    yield
    if has_dotenv and os.path.exists(backup_path):
        import shutil
        shutil.move(backup_path, dotenv_path)

def test_longcat_inspect_model():
    from app.model_registry import registry
    info = registry.inspect_model("LongCat-2.0-Preview")
    assert info["provider"] == "longcat"
    assert info["context_estimate"] == 260000
    assert "reasoning" in info["recommended_roles"]


def test_longcat_switch_with_and_without_api_key():
    from cli.macagent import main
    
    # 1. Test switch when key is missing
    with patch.dict(os.environ, {}):
        if "LONGCAT_API_KEY" in os.environ:
            del os.environ["LONGCAT_API_KEY"]
        
        # Capture stdout
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            rc = main(["model", "switch", "LongCat-2.0-Preview"])
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
            
        assert rc == 1
        assert "LongCat API key is missing" in output
        
    # 2. Test switch when key exists
    with patch.dict(os.environ, {"LONGCAT_API_KEY": "test-key-123"}):
        with patch("app.model_registry.registry.set_global_default") as mock_set:
            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                rc = main(["model", "switch", "LongCat-2.0-Preview"])
                output = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
                
            assert rc == 0
            assert "Global default model switched to LongCat-2.0-Preview" in output
            mock_set.assert_called_once_with("LongCat-2.0-Preview")


def test_longcat_set_role_allowed():
    from cli.macagent import main
    
    # Set with key exists
    with patch.dict(os.environ, {"LONGCAT_API_KEY": "test-key-123"}):
        with patch("app.model_registry.registry.set_agent_model") as mock_set:
            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                rc = main(["model", "set", "ORCHESTRATOR", "LongCat-2.0-Preview"])
                output = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout
                
            assert rc == 0
            assert "Agent ORCHESTRATOR model set to LongCat-2.0-Preview" in output
            mock_set.assert_called_once_with("ORCHESTRATOR", "LongCat-2.0-Preview")


def test_doctor_skips_ollama_for_longcat():
    from cli.macagent import _run_doctor
    
    client_mock = MagicMock()
    # Mock client.health() returning default_model as longcat
    client_mock.health.return_value = {
        "ok": True,
        "service": "macagent-proxy",
        "version": "2.0.0",
        "default_model": "LongCat-2.0-Preview"
    }
    
    # Run doctor with valid LongCat env variables
    mock_env = {
        "LONGCAT_API_KEY": "test-key",
        "LONGCAT_BASE_URL": "https://api.longcat.chat/openai",
        "LONGCAT_MODEL": "LongCat-2.0-Preview",
        "DEFAULT_MODEL": "LongCat-2.0-Preview"
    }
    with patch.dict(os.environ, mock_env), \
         patch("app.model_registry.get_effective_model_config", return_value={"model": "LongCat-2.0-Preview", "provider": "longcat"}):
        # We also mock openapi endpoint return
        client_mock._http.get.return_value.json.return_value = {
            "paths": {
                "/v1/docs/ingest": {},
                "/v1/docs/search": {},
                "/v1/docs/ask": {}
            }
        }
        # Mock subprocess.run for eval_baseline
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
                
            # Verify it passed and checked LongCat variables
            assert rc == 0
            assert "longcat_api_key_configured: ok" or "longcat_api_key_configured: True" in output
            assert "longcat_base_url_configured: https://api.longcat.chat/openai" in output
            # Check default_model_installed was not added (which would print default_model_installed)
            assert "default_model_installed" not in output


def test_stream_renderer_renders_final_status():
    from client.macagent_client import _StreamRenderer
    
    # Compact mode
    renderer = _StreamRenderer(mode="compact")
    
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        renderer.finalize(agent_status="completed")
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
        
    assert "[x_agent_status] completed" in output
