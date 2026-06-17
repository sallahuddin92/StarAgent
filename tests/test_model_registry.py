import pytest
import os
import json
from pathlib import Path
from app.model_registry import ModelRegistry, ModelInfo

def test_model_registry_initialization():
    registry = ModelRegistry()
    assert len(registry.models) > 0
    assert "gemma4:e2b" in registry.models

def test_model_registry_config_loading(tmp_path, monkeypatch):
    config_file = tmp_path / ".staragent" / "models.json"
    config_file.parent.mkdir()
    config_data = {
        "default": "custom-model",
        "agents": {
            "BACKEND_AGENT": "backend-model"
        },
        "fallbacks": {
            "custom-model": ["fallback-1", "fallback-2"]
        }
    }
    with open(config_file, "w") as f:
        json.dump(config_data, f)
        
    registry = ModelRegistry()
    # Mock get_config_path to return our tmp_path
    monkeypatch.setattr(registry, "get_config_path", lambda: config_file)
    registry.load_config()
    
    assert registry.global_default == "custom-model"
    assert registry.get_agent_model("BACKEND_AGENT") == "backend-model"
    assert registry.get_fallback_chain("custom-model") == ["fallback-1", "fallback-2"]

def test_model_registry_set_and_save(tmp_path, monkeypatch):
    config_file = tmp_path / ".staragent" / "models.json"
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_config_path", lambda: config_file)
    
    registry.set_global_default("new-default")
    registry.set_agent_model("FRONTEND_AGENT", "frontend-model")
    
    assert os.path.exists(config_file)
    with open(config_file, "r") as f:
        data = json.load(f)
        
    assert data["default"] == "new-default"
    assert data["agents"]["FRONTEND_AGENT"] == "frontend-model"


def test_model_registry_refresh_writes_cache(tmp_path, monkeypatch):
    config_file = tmp_path / ".staragent" / "models.json"
    registry = ModelRegistry()
    monkeypatch.setattr(registry, "get_config_path", lambda: config_file)
    monkeypatch.setattr(
        registry,
        "_fetch_ollama_tags",
        lambda: {
            "models": [
                {"name": "llama3.1:8b", "size": 123, "modified_at": "2026-01-01T00:00:00Z"},
                {"name": "qwen2.5-coder:14b", "size": 456, "modified_at": "2026-01-02T00:00:00Z"},
            ]
        },
    )

    cache = registry.refresh_ollama_cache()
    assert isinstance(cache.get("models"), list)
    assert registry.is_local_ollama_model_installed("llama3.1:8b")
    assert registry.is_local_ollama_model_installed("qwen2.5-coder:14b")

    with open(config_file, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert "ollama_cache" in saved
    assert len(saved["ollama_cache"].get("models") or []) == 2
