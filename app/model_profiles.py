import os
import re
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

@dataclass
class ModelProfile:
    name: str
    provider: str
    model_name_pattern: str
    context_limit: int
    preferred_tool_protocol: str  # "native" or "json"
    small_model_mode: bool
    supports_multimodal: bool
    supports_native_tool_calling: bool
    requires_strict_blueprint: bool
    max_skill_injection_tokens: int
    max_doc_injection_tokens: int

# Default Profiles
GENERIC_SMALL_LOCAL = ModelProfile(
    name="generic_small_local",
    provider="ollama",
    model_name_pattern=r".*",
    context_limit=8192,
    preferred_tool_protocol="json",
    small_model_mode=True,
    supports_multimodal=False,
    supports_native_tool_calling=False,
    requires_strict_blueprint=True,
    max_skill_injection_tokens=2000,
    max_doc_injection_tokens=2000
)

GENERIC_API_CAPABLE = ModelProfile(
    name="generic_api_capable",
    provider="openai|anthropic|gemini|longcat|groq",
    model_name_pattern=r".*",
    context_limit=128000,
    preferred_tool_protocol="native",
    small_model_mode=False,
    supports_multimodal=True,
    supports_native_tool_calling=True,
    requires_strict_blueprint=False,
    max_skill_injection_tokens=8000,
    max_doc_injection_tokens=8000
)

GEMMA4_E2B_BASELINE = ModelProfile(
    name="gemma4_e2b_baseline",
    provider="ollama",
    model_name_pattern=r"gemma4:e2b",
    context_limit=8192,
    preferred_tool_protocol="json",
    small_model_mode=True,
    supports_multimodal=False,
    supports_native_tool_calling=False,
    requires_strict_blueprint=True,
    max_skill_injection_tokens=1500,
    max_doc_injection_tokens=1500
)

GEMMA4_12B_MLX = ModelProfile(
    name="gemma4_12b_mlx",
    provider="ollama",
    model_name_pattern=r"gemma4:12b-mlx",
    context_limit=8192,
    preferred_tool_protocol="json",
    small_model_mode=True,
    supports_multimodal=False,
    supports_native_tool_calling=False,
    requires_strict_blueprint=True,
    max_skill_injection_tokens=2000,
    max_doc_injection_tokens=2000
)

PROFILES = [
    GEMMA4_12B_MLX,
    GEMMA4_E2B_BASELINE,
    GENERIC_API_CAPABLE,
    GENERIC_SMALL_LOCAL
]

def get_active_profile(provider: str, model_name: str) -> ModelProfile:
    """
    Finds the first matching profile based on provider and model name.
    Returns GENERIC_SMALL_LOCAL if no match found.
    """
    for profile in PROFILES:
        # Check provider match
        if re.search(profile.provider, provider, re.IGNORECASE):
            # Check model name pattern match
            if re.search(profile.model_name_pattern, model_name, re.IGNORECASE):
                return profile
    
    return GENERIC_SMALL_LOCAL

def detect_and_log_profile():
    from .model_registry import get_effective_model_config
    
    eff = get_effective_model_config()
    model = eff["model"]
    provider = eff["provider"]
    
    # Fallback to env variables if registry.global_default is default gemma4:12b-mlx or gemma4:e2b but a remote provider is forced via env
    env_provider = os.getenv("STARAGENT_LLM_PROVIDER")
    if env_provider and env_provider.lower() != "ollama" and model in ("gemma4:12b-mlx", "gemma4:e2b"):
        provider = env_provider.lower()
        if provider == "openai":
            model = os.getenv("OPENAI_MODEL", "gpt-4o")
        elif provider == "anthropic":
            model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20240620")
        elif provider == "gemini":
            model = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")
        elif provider == "longcat":
            model = os.getenv("LONGCAT_MODEL", "LongCat-Flash-Thinking-2601")
        elif provider == "groq":
            model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    profile = get_active_profile(provider, model)
    print(f"[MODEL_PROFILE] active={profile.name}")
    logger.info(f"Loaded model profile: {profile.name} (provider={provider}, model={model})")
    return profile
