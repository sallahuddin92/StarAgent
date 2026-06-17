import os
import json
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from .model_registry import registry, ModelInfo

logger = logging.getLogger(__name__)

DEFAULT_ROUTING = {
    "inspect": "gemma4:12b-mlx",
    "analyze": "LongCat-Flash-Chat",
    "plan": "gemma4:12b-mlx",
    "execute": "gemma4:e4b",
    "verify": "gemma4:12b-mlx",
    "finalize": "gemma4:12b-mlx"
}

# Map stages to standard capability lists
STAGE_CAPABILITIES = {
    "inspect": ["local_fast", "summarization", "reasoning"],
    "analyze": ["long_context", "reasoning"],
    "plan": ["planning", "reasoning"],
    "execute": ["coding", "reasoning"],
    "verify": ["verification", "local_fast"],
    "finalize": ["summarization", "local_fast"]
}

def _get_config_path() -> Path:
    d = os.getenv("STARAGENT_CLI_STATE_DIR") or os.getenv("MACAGENT_CLI_STATE_DIR")
    if d:
        base = Path(d)
    else:
        base = Path.home() / ".staragent"
    base.mkdir(parents=True, exist_ok=True)
    return base / "model_routing.json"

def init_model_routing():
    """Ensure model_routing.json exists with defaults."""
    path = _get_config_path()
    if not path.exists():
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_ROUTING, f, indent=2)
            logger.info(f"Initialized default model routing at {path}")
        except Exception as e:
            logger.error(f"Failed to write default model routing: {e}")

def load_model_routing() -> Dict[str, str]:
    init_model_routing()
    path = _get_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                # Ensure all stages are present in lowercase
                return {k.lower(): v for k, v in data.items()}
    except Exception as e:
        logger.error(f"Failed to load model routing from {path}: {e}")
    return DEFAULT_ROUTING

def resolve_capabilities(
    capabilities: List[str], 
    privacy_mode: bool = False, 
    latency_preference: str = "low"
) -> str:
    """
    Resolve requested capabilities to the best available model.
    Matches capabilities against configured API keys and local Ollama models.
    """
    available_models: List[ModelInfo] = []
    
    # Check what providers are configured
    openai_ok = bool(os.getenv("OPENAI_API_KEY"))
    anthropic_ok = bool(os.getenv("ANTHROPIC_API_KEY"))
    gemini_ok = bool(os.getenv("GEMINI_API_KEY"))
    longcat_ok = bool(os.getenv("LONGCAT_API_KEY") and os.getenv("LONGCAT_MODEL"))
    groq_ok = bool(os.getenv("GROQ_API_KEY"))
    
    for model_id, info in registry.models.items():
        provider = info.provider
        
        # Respect STARAGENT_LLM_PROVIDER if set and not "all"
        env_provider = (os.getenv("STARAGENT_LLM_PROVIDER") or "").strip().lower()
        if env_provider and env_provider != "all" and provider != env_provider:
            continue

        # Privacy mode forces local Ollama
        if privacy_mode and provider != "ollama":
            continue

        # Only consider installed Ollama models
        if provider == "ollama":
            if not registry.is_local_ollama_model_installed(model_id):
                continue
            
        # Check API key presence for commercial providers
        if provider == "openai" and not openai_ok:
            continue
        if provider == "anthropic" and not anthropic_ok:
            continue
        if provider == "gemini" and not gemini_ok:
            continue
        if provider == "longcat":
            if not longcat_ok:
                continue
            env_model = os.getenv("LONGCAT_MODEL")
            if env_model and model_id != env_model:
                continue
        if provider == "groq" and not groq_ok:
            continue
            
        available_models.append(info)
        
    if not available_models:
        return registry.global_default

    scored_models = []
    for info in available_models:
        score = 0
        
        for cap in capabilities:
            cap_lower = cap.lower()
            
            # Direct matches on strengths
            if cap_lower in [s.lower() for s in info.strengths]:
                score += 3
                
            # Boolean capability flags mapping
            if cap_lower == "long_context" and info.supports_long_context:
                score += 5
            if cap_lower == "reasoning" and info.supports_reasoning:
                score += 5
            if cap_lower == "local_fast" and info.provider == "ollama":
                score += 4
            if cap_lower == "coding" and "coding" in info.strengths:
                score += 3
            if cap_lower == "planning" and "planning" in info.strengths:
                score += 3

        # Only apply tie-breakers if we matched some capabilities
        if score > 0:
            # Latency preference modifier
            if latency_preference == "low" and info.latency_class == "low":
                score += 1
            elif latency_preference == "medium" and info.latency_class == "medium":
                score += 1

            # Tie breaker: cost class
            if info.cost_class == "low":
                score += 0.5
            elif info.cost_class == "medium":
                score += 0.2

        scored_models.append((score, info))

    # Sort descending by score
    scored_models.sort(key=lambda x: x[0], reverse=True)
    if scored_models and scored_models[0][0] > 0:
        return scored_models[0][1].id
        
    return registry.global_default

def get_stage_model(stage_name: str) -> str:
    """Resolve model ID for a specific stage, with fallbacks."""
    routing = load_model_routing()
    key = str(stage_name).strip().lower()
    
    groq_ok = bool(os.getenv("GROQ_API_KEY"))
    longcat_ok = bool(os.getenv("LONGCAT_API_KEY") and os.getenv("LONGCAT_MODEL"))
    
    # Check special capability routing overrides:
    if key == "analyze":
        if longcat_ok:
            return os.getenv("LONGCAT_MODEL") or "LongCat-Flash-Chat"
        if groq_ok:
            return "llama-3.1-8b-instant"
    elif key in ("deep_research", "deep-research"):
        if longcat_ok:
            return os.getenv("LONGCAT_MODEL") or "LongCat-Flash-Chat"
        if groq_ok:
            return "llama-3.1-8b-instant"
    elif key in ("inspect", "plan", "execute", "verify", "finalize"):
        if groq_ok:
            return "llama-3.1-8b-instant"

    # If the user has explicitly routed this stage to a configured model, respect that
    model_id = routing.get(key) or DEFAULT_ROUTING.get(key)
    
    # Verify if configured model is available
    if model_id:
        # If it's one of the old defaults, and groq is configured, bypass and use groq
        is_default_local = model_id in ("gemma4:12b-mlx", "gemma4:e4b", "gemma4:e2b", "LongCat-Flash-Chat")
        if is_default_local and groq_ok:
            return "llama-3.1-8b-instant"

        provider = registry.infer_provider(model_id)
        is_available = True
        
        if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
            is_available = False
        elif provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
            is_available = False
        elif provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
            is_available = False
        elif provider == "longcat" and not longcat_ok:
            is_available = False
        elif provider == "groq" and not groq_ok:
            is_available = False
            
        if is_available:
            return model_id

    # Fall back to resolving capabilities
    caps = STAGE_CAPABILITIES.get(key, ["local_fast"])
    return resolve_capabilities(caps)
