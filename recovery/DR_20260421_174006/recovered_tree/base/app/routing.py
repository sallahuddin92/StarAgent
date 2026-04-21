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
    
    # Force agent path if we have an active plan or continuation state
    if memory.archive_turns and len(memory.archive_turns) > 0:
        # Heuristic: if the last turn was part of an agent flow
        pass
        
    latest_msg = request.messages[-1].content if request.messages else ""
    if isinstance(latest_msg, list):
        latest_msg = " ".join([str(m) for m in latest_msg])
    
    # Keyword check
    msg_lower = str(latest_msg).lower()
    if any(kw in msg_lower for kw in AGENT_KEYWORDS):
        logger.info(f"Routing to AGENT_PATH due to keywords in: {msg_lower[:50]}...")
        return AGENT_PATH
        
    return FAST_PATH
