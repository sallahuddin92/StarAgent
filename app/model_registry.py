import json
import os
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

@dataclass
class ModelInfo:
    id: str
    provider: str
    context_limit: int = 8192
    strengths: List[str] = field(default_factory=list)
    cost_class: str = "low"
    latency_class: str = "low"
    supports_tools: bool = False
    supports_json: bool = True
    supports_long_context: bool = False
    supports_reasoning: bool = False

class ModelRegistry:
    def __init__(self):
        self.models: Dict[str, ModelInfo] = {}
        self._global_default: Optional[str] = None
        self.agent_routing: Dict[str, str] = {}
        self.fallbacks: Dict[str, List[str]] = {}
        self.ollama_cache: Dict[str, Any] = {"updated_at": None, "models": []}
        self._load_builtins()
        self.load_config()

    @property
    def global_default(self) -> str:
        if self._global_default:
            return self._global_default
        
        env_val = os.environ.get("DEFAULT_MODEL")
        if env_val:
            return env_val
            
        dotenv_val = get_dotenv_default_model()
        if dotenv_val:
            return dotenv_val
            
        return "gemma4:12b-mlx"

    @global_default.setter
    def global_default(self, value: str):
        self._global_default = value

    def _load_builtins(self):
        # Register some well-known models
        self.register(ModelInfo(
            id="gemma4:e2b",
            provider="ollama",
            strengths=["coding", "small_tasks"],
            supports_json=True
        ))
        self.register(ModelInfo(
            id="gemma4:12b-mlx",
            provider="ollama",
            context_limit=8192,
            strengths=["planning", "coding", "testing", "local_fast"],
            supports_json=True
        ))
        self.register(ModelInfo(
            id="qwen2.5-coder:14b",
            provider="ollama",
            context_limit=32768,
            strengths=["coding", "reasoning"],
            cost_class="medium",
            latency_class="medium"
        ))
        self.register(ModelInfo(
            id="deepseek-coder:latest",
            provider="ollama",
            context_limit=32768,
            strengths=["coding", "verification"]
        ))
        self.register(ModelInfo(
            id="gpt-4o",
            provider="openai",
            context_limit=128000,
            strengths=["general", "complex_reasoning"],
            supports_tools=True,
            cost_class="high"
        ))
        self.register(ModelInfo(
            id="claude-3-5-sonnet",
            provider="anthropic",
            context_limit=200000,
            strengths=["coding", "long_context"],
            supports_tools=True,
            supports_long_context=True,
            cost_class="high"
        ))
        self.register(ModelInfo(
            id="LongCat-Flash-Thinking-2601",
            provider="longcat",
            context_limit=260000,
            strengths=["reasoning", "long_context"],
            supports_json=True,
            supports_long_context=True,
            supports_reasoning=True,
            cost_class="medium",
            latency_class="medium",
        ))
        self.register(ModelInfo(
            id="LongCat-Flash-Chat",
            provider="longcat",
            context_limit=260000,
            strengths=["general", "long_context"],
            supports_json=True,
            supports_long_context=True,
            cost_class="low",
            latency_class="low",
        ))
        self.register(ModelInfo(
            id="LongCat-2.0-Preview",
            provider="longcat",
            context_limit=260000,
            strengths=["reasoning", "long_context", "coding"],
            supports_json=True,
            supports_long_context=True,
            supports_reasoning=True,
            cost_class="high",
            latency_class="medium",
        ))
        self.register(ModelInfo(
            id="gemini-1.5-pro",
            provider="gemini",
            context_limit=1000000,
            strengths=["long_context", "analysis"],
            supports_json=True,
            supports_long_context=True,
            supports_reasoning=True,
            cost_class="high",
            latency_class="medium",
        ))
        self.register(ModelInfo(
            id="llama-3.1-8b-instant",
            provider="groq",
            context_limit=int(os.getenv("GROQ_CONTEXT_LIMIT") or 128000),
            strengths=["planning", "routing", "tool_calling", "summarization", "local_fast_alternative", "repair_loop"],
            supports_json=True,
            supports_tools=True,
            cost_class="low",
            latency_class="low",
        ))

    def register(self, model_info: ModelInfo):
        self.models[model_info.id] = model_info

    def get_config_path(self) -> Path:
        # Check project root first, then user home
        project_config = Path.cwd() / ".staragent" / "models.json"
        if project_config.exists():
            return project_config
        return Path.home() / ".staragent" / "models.json"

    def load_config(self):
        config_path = self.get_config_path()
        if not config_path.exists():
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            if "default" in data:
                self.global_default = data["default"]
            
            if "agents" in data and isinstance(data["agents"], dict):
                self.agent_routing.update(data["agents"])
                
            if "fallbacks" in data and isinstance(data["fallbacks"], dict):
                for k, v in data["fallbacks"].items():
                    if isinstance(v, list):
                        self.fallbacks[k] = v
            if "ollama_cache" in data and isinstance(data["ollama_cache"], dict):
                cache = data["ollama_cache"]
                models = cache.get("models")
                updated = cache.get("updated_at")
                if isinstance(models, list):
                    self.ollama_cache = {"updated_at": updated, "models": models}
        except Exception as e:
            logger.error(f"Failed to load model config {config_path}: {e}")

    def save_config(self):
        config_path = self.get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "default": self.global_default,
            "agents": self.agent_routing,
            "fallbacks": self.fallbacks,
            "ollama_cache": self.ollama_cache,
        }
        
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save model config {config_path}: {e}")

    def get_agent_model(self, agent_role: str) -> str:
        """Get the assigned model for a given agent role, falling back to global default."""
        # Convert enum value if passed
        role_str = getattr(agent_role, "value", agent_role)
        return self.agent_routing.get(role_str, self.global_default)

    def set_agent_model(self, agent_role: str, model_id: str):
        role_str = getattr(agent_role, "value", agent_role)
        self.agent_routing[role_str] = model_id
        self.save_config()

    def set_global_default(self, model_id: str):
        self.global_default = model_id
        self.save_config()

    def get_fallback_chain(self, model_id: str) -> List[str]:
        return self.fallbacks.get(model_id, [])

    def list_models(self) -> List[ModelInfo]:
        return list(self.models.values())

    @staticmethod
    def _ollama_base_url() -> str:
        return (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")

    def _fetch_ollama_tags(self, *, timeout_s: float = 10.0) -> Dict[str, Any]:
        url = f"{self._ollama_base_url()}/api/tags"
        with httpx.Client(timeout=timeout_s) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.json()

    def refresh_ollama_cache(self) -> Dict[str, Any]:
        payload = self._fetch_ollama_tags()
        raw_models = payload.get("models")
        models: List[Dict[str, Any]] = []
        if isinstance(raw_models, list):
            for m in raw_models:
                if not isinstance(m, dict):
                    continue
                name = m.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                models.append(
                    {
                        "name": name.strip(),
                        "size": m.get("size"),
                        "modified_at": m.get("modified_at"),
                        "digest": m.get("digest"),
                        "details": m.get("details") if isinstance(m.get("details"), dict) else None,
                    }
                )
        models.sort(key=lambda x: str(x.get("name") or ""))
        self.ollama_cache = {"updated_at": int(time.time()), "models": models}
        self.save_config()
        return self.ollama_cache

    def get_ollama_cache(self) -> Dict[str, Any]:
        return self.ollama_cache or {"updated_at": None, "models": []}

    def list_local_ollama_models(self, *, refresh: bool = False) -> List[Dict[str, Any]]:
        if refresh:
            self.refresh_ollama_cache()
        cache = self.get_ollama_cache()
        models = cache.get("models")
        if not isinstance(models, list):
            return []
        return list(models)

    @staticmethod
    def normalize_model_name(model_id: str) -> str:
        name = (model_id or "").strip().lower()
        if ":" not in name:
            name = f"{name}:latest"
        return name

    def is_local_ollama_model_installed(self, model_id: str, *, refresh: bool = False) -> bool:
        models = self.list_local_ollama_models(refresh=refresh)
        target = self.normalize_model_name(model_id)
        for m in models:
            m_name = self.normalize_model_name(m.get("name") or "")
            if m_name == target:
                return True
        return False

    def infer_provider(self, model_id: str) -> str:
        mi = self.models.get(model_id)
        if mi:
            return mi.provider
        mid = (model_id or "").strip().lower()
        if "llama-3.1-8b-instant" in mid and os.getenv("GROQ_API_KEY"):
            return "groq"
        if mid.startswith("gpt-"):
            return "openai"
        if mid.startswith("claude-"):
            return "anthropic"
        if mid.startswith("gemini-"):
            return "gemini"
        if "longcat" in mid:
            return "longcat"
        if mid.startswith("openai-compatible/"):
            return "openai_compatible"
        return "ollama"

    def get_model_info(self, model_id: str) -> Optional[ModelInfo]:
        return self.models.get(model_id)

    def get_registry_suggestions(self, *, include_providers: Optional[List[str]] = None) -> List[ModelInfo]:
        allowed = set(include_providers or [])
        out = []
        for m in self.list_models():
            if allowed and m.provider not in allowed:
                continue
            out.append(m)
        out.sort(key=lambda x: x.id)
        return out

    def inspect_model(self, model_id: str, *, refresh_local: bool = False) -> Dict[str, Any]:
        provider = self.infer_provider(model_id)
        local_models = self.list_local_ollama_models(refresh=refresh_local) if provider == "ollama" else []
        local_entry = None
        target = self.normalize_model_name(model_id)
        for m in local_models:
            if self.normalize_model_name(m.get("name") or "") == target:
                local_entry = m
                break
        mi = self.get_model_info(model_id)
        context_estimate = mi.context_limit if mi else None
        recommended_roles = mi.strengths if mi else []
        return {
            "model_id": model_id,
            "provider": provider,
            "installed": bool(local_entry) if provider == "ollama" else ("remote" if provider in ("openai", "anthropic", "gemini", "longcat", "groq") else None),
            "context_estimate": context_estimate,
            "size": local_entry.get("size") if local_entry else None,
            "modified_at": local_entry.get("modified_at") if local_entry else None,
            "recommended_roles": recommended_roles,
        }

# Global registry instance
registry = ModelRegistry()


def get_dotenv_default_model() -> Optional[str]:
    try:
        dotenv_path = Path.cwd() / ".env"
        if dotenv_path.exists():
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEFAULT_MODEL="):
                        val = line.split("=", 1)[1].strip()
                        if val.startswith(('"', "'")) and val.endswith(val[0]):
                            val = val[1:-1]
                        return val
    except Exception:
        pass
    return None


def get_effective_model_config() -> Dict[str, Any]:
    model = registry.global_default
    return {
        "model": model,
        "provider": registry.infer_provider(model)
    }


def is_compact_prompts_enabled(model_id: Optional[str] = None) -> bool:
    val = os.getenv("STARAGENT_COMPACT_PROMPTS")
    if val is not None:
        return val.strip() == "1" or val.strip().lower() == "true"
    
    effective_model = model_id or registry.global_default
    provider = registry.infer_provider(effective_model)
    if provider == "groq":
        return True
    return False

