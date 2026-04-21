import logging
import re
from typing import List, Dict, Any, Tuple
from .models import ChatCompletionRequest, MemoryState

logger = logging.getLogger(__name__)

FAST_PATH = "fast"
AGENT_PATH = "agent"

AGENT_KEYWORDS = [
    "inspect", "build", "debug", "implement", "analyze", 
    "create", "search", "modify", "fix", "verify", "run"
]

_OPENWEBUI_HELPER_PATTERNS = [
    # Common Open WebUI helper tasks
    r"###\s*task\s*:\s*suggest\s+\d+\s*-\s*\d+\s+relevant\s+follow[-\s]?up\s+questions",
    r"###\s*task\s*:\s*suggest\s+.*follow[-\s]?up\s+questions",
    r"###\s*task\s*:\s*generate\s+a\s+concise\s+title",
    r"###\s*task\s*:\s*generate\s+.*title",
    r"###\s*task\s*:\s*generate\s+\d+\s*-\s*\d+\s+.*tags",
    r"###\s*task\s*:\s*generate\s+.*tags",
    # More generic helper/meta instructions that Open WebUI uses for titles/tags/followups.
    r"\bgenerate\s+(a\s+)?(concise\s+)?title\b",
    r"\bsuggest\s+(\d+\s*-\s*\d+\s+)?follow[-\s]?up\s+questions\b",
    r"\bgenerate\s+(\d+\s*-\s*\d+\s+)?(broad\s+)?tags\b",
]


def _is_openwebui_helper_prompt(text: str) -> bool:
    """
    Open WebUI sends background helper requests (title/tags/follow-ups) that may include
    prior chat history containing agent-keywords like "create file" or "yes".
    Those must never enter the agent path.
    """
    if not text:
        return False
    lower = text.strip().lower()

    # Strong signal: Open WebUI task wrapper.
    if "### task:" in lower and any(k in lower for k in ("title", "tag", "tags", "follow-up", "follow up")):
        return True

    for pat in _OPENWEBUI_HELPER_PATTERNS:
        if re.search(pat, lower):
            # Limit generic patterns to metadata-ish prompts to avoid surprising routing.
            if any(k in lower for k in ("title", "tag", "tags", "follow-up", "follow up", "questions")):
                return True
    return False


def determine_execution_route(
    request: ChatCompletionRequest, 
    memory: MemoryState
) -> str:
    """Determine if a request should go to the fast path or agent path."""
    
    # Normalize last message content (OpenAI-compatible requests may pass list content).
    latest_msg = request.messages[-1].content if request.messages else ""
    if isinstance(latest_msg, list):
        latest_msg = " ".join([str(m) for m in latest_msg])
    msg_lower = str(latest_msg).strip().lower()

    # 1) Open WebUI helper/meta prompt detection: ALWAYS fast path.
    # This prevents embedded historical text from triggering agent tools/approvals.
    if _is_openwebui_helper_prompt(str(latest_msg)):
        logger.info("Detected Open WebUI helper/meta prompt; forcing FAST_PATH (agent bypass).")
        return FAST_PATH

    # 1.5) Optional explicit routing override (used by first-party CLI/MCP adapters).
    # This is a no-op for Open WebUI (metadata is absent/empty) and preserves default behavior.
    meta = getattr(request, "metadata", None) or {}
    force_route = str(meta.get("force_route") or "").strip().lower()
    if force_route in {"fast", "agent"}:
        logger.info(f"Forced route via metadata.force_route={force_route!r}")
        return FAST_PATH if force_route == "fast" else AGENT_PATH
    if meta.get("force_agent") is True:
        logger.info("Forced route via metadata.force_agent=True")
        return AGENT_PATH
    if meta.get("force_fast") is True:
        logger.info("Forced route via metadata.force_fast=True")
        return FAST_PATH

    # 2) If an agent run is awaiting approval or has a pending plan, route explicit resume tokens.
    if (getattr(memory, "pending_approval", None) or getattr(memory, "pending_plan", None)) and msg_lower in {
        "yes", "y", "approve", "approved", "ok", "continue", "c", "resume",
    }:
        logger.info(f"Routing to AGENT_PATH due to pending agent state + resume/approval token: {msg_lower!r}")
        return AGENT_PATH
        
    # Keyword check
    if any(kw in msg_lower for kw in AGENT_KEYWORDS):
        logger.info(f"Routing to AGENT_PATH due to keywords in: {msg_lower[:50]}...")
        return AGENT_PATH
        
    return FAST_PATH
