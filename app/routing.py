import logging
from typing import List, Dict, Any, Tuple
from .models import ChatCompletionRequest, MemoryState

logger = logging.getLogger(__name__)

FAST_PATH = "fast"
AGENT_PATH = "agent"

AGENT_KEYWORDS = [
    "inspect", "build", "debug", "implement", "analyze", 
    "create", "search", "modify", "fix", "verify", "run"
]

def determine_execution_route(
    request: ChatCompletionRequest, 
    memory: MemoryState
) -> str:
    """Determine if a request should go to the fast path or agent path."""
    
    # If an agent run is awaiting approval or has a pending plan, route follow-ups to agent path.
    latest_msg = request.messages[-1].content if request.messages else ""
    if isinstance(latest_msg, list):
        latest_msg = " ".join([str(m) for m in latest_msg])
    msg_lower = str(latest_msg).strip().lower()
    if (getattr(memory, "pending_approval", None) or getattr(memory, "pending_plan", None)) and msg_lower in {
        "yes", "y", "approve", "approved", "ok", "continue", "c", "resume",
    }:
        logger.info("Routing to AGENT_PATH due to pending agent state + resume/approval token.")
        return AGENT_PATH
        
    # Keyword check
    msg_lower = str(latest_msg).lower()
    if any(kw in msg_lower for kw in AGENT_KEYWORDS):
        logger.info(f"Routing to AGENT_PATH due to keywords in: {msg_lower[:50]}...")
        return AGENT_PATH
        
    return FAST_PATH
