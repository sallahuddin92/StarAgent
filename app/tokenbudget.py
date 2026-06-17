"""
Token budget management and cost tracking.
Enforces token limits per conversation and trims message windows.
"""

import logging
import os
from typing import List, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Token counting with tiktoken
try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False
    logger.warning("tiktoken not available, using word-count estimation")


@dataclass
class TokenBudget:
    """Token budget tracking for a conversation."""
    conversation_id: str
    max_prompt_tokens: int
    max_completion_tokens: int
    current_prompt_tokens: int = 0
    current_completion_tokens: int = 0
    last_reset: datetime = None
    reset_window_hours: int = 24  # Reset budget daily
    
    def __post_init__(self):
        if self.last_reset is None:
            self.last_reset = datetime.utcnow()
    
    @property
    def total_tokens(self) -> int:
        """Total tokens used in current window."""
        return self.current_prompt_tokens + self.current_completion_tokens
    
    @property
    def remaining_tokens(self) -> int:
        """Remaining tokens in budget."""
        used = self.total_tokens
        available = self.max_prompt_tokens + self.max_completion_tokens
        return max(0, available - used)
    
    @property
    def budget_exhausted(self) -> bool:
        """Check if budget is exhausted."""
        return self.remaining_tokens <= 0
    
    @property
    def should_reset(self) -> bool:
        """Check if budget window should reset."""
        elapsed = (datetime.utcnow() - self.last_reset).total_seconds()
        window_seconds = self.reset_window_hours * 3600
        return elapsed > window_seconds
    
    def reset(self):
        """Reset token counters."""
        self.current_prompt_tokens = 0
        self.current_completion_tokens = 0
        self.last_reset = datetime.utcnow()
        logger.info(f"Reset budget for conversation {self.conversation_id}")


class TokenCounter:
    """Utility for token counting with fallback to word estimation."""
    
    def __init__(self, use_tiktoken: bool = True, encoding: str = "cl100k_base"):
        self.use_tiktoken = use_tiktoken and HAS_TIKTOKEN
        self.encoding_name = encoding
        self.encoder = None
        
        if self.use_tiktoken:
            try:
                self.encoder = tiktoken.get_encoding(encoding)
                logger.info(f"Initialized tiktoken with {encoding} encoding")
            except Exception as e:
                logger.warning(f"Failed to load tiktoken: {e}, using word estimation")
                self.use_tiktoken = False
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        if not text:
            return 0
        
        if self.use_tiktoken and self.encoder:
            try:
                tokens = self.encoder.encode(text)
                return len(tokens)
            except Exception as e:
                logger.warning(f"Token counting failed: {e}")
        
        # Fallback: estimate tokens from word count
        # Rough approximation: 1 token ≈ 4 characters or 0.75 words
        words = len(text.split())
        return max(1, int(words * 1.33))  # Slightly conservative estimate
    
    def count_messages_tokens(self, messages: List[Dict]) -> int:
        """Count tokens for a list of chat messages."""
        total = 0
        for msg in messages:
            # Add overhead for message structure (role, etc.)
            overhead = 4
            content = msg.get("content", "")
            if isinstance(content, str):
                total += overhead + self.count_tokens(content)
            elif isinstance(content, list):
                # For content blocks (e.g., image+text)
                for block in content:
                    if isinstance(block, dict):
                        if "text" in block:
                            total += self.count_tokens(block["text"])
                        # Estimate tokens for image blocks
                        total += overhead
        
        return total
    
    def estimate_completion_tokens(self, message_length: int) -> int:
        """Estimate completion tokens based on input length."""
        # Conservative estimate: completion ~1.5x input length
        return max(128, int(message_length * 1.5))


class TokenBudgetManager:
    """Manages token budgets across conversations."""
    
    def __init__(
        self,
        default_prompt_budget: int = 1000000,
        default_completion_budget: int = 1000000
    ):
        self.default_prompt_budget = default_prompt_budget
        self.default_completion_budget = default_completion_budget
        self.budgets: Dict[str, TokenBudget] = {}
        self.counter = TokenCounter()
    
    def get_budget(self, conversation_id: str) -> TokenBudget:
        """Get or create token budget for conversation."""
        if conversation_id not in self.budgets:
            self.budgets[conversation_id] = TokenBudget(
                conversation_id=conversation_id,
                max_prompt_tokens=self.default_prompt_budget,
                max_completion_tokens=self.default_completion_budget
            )
        
        budget = self.budgets[conversation_id]
        if budget.should_reset:
            budget.reset()
        
        return budget
    
    def record_prompt_tokens(self, conversation_id: str, tokens: int) -> None:
        """Record prompt tokens used."""
        budget = self.get_budget(conversation_id)
        budget.current_prompt_tokens += tokens
        logger.debug(f"Recorded {tokens} prompt tokens for {conversation_id}, remaining: {budget.remaining_tokens}")
    
    def record_completion_tokens(self, conversation_id: str, tokens: int) -> None:
        """Record completion tokens used."""
        budget = self.get_budget(conversation_id)
        budget.current_completion_tokens += tokens
        logger.debug(f"Recorded {tokens} completion tokens for {conversation_id}, remaining: {budget.remaining_tokens}")
    
    def trim_messages_to_budget(
        self,
        messages: List[Dict],
        conversation_id: str,
        max_new_tokens: int = 1500,
        keep_system: bool = True
    ) -> Tuple[List[Dict], int]:
        """
        Trim messages to fit within token budget.
        Always preserves system messages if keep_system=True.
        
        Returns:
            (trimmed_messages, total_tokens)
        """
        budget = self.get_budget(conversation_id)
        
        # Separate system and regular messages
        system_messages = [m for m in messages if m.get("role") == "system"]
        other_messages = [m for m in messages if m.get("role") != "system"]
        
        # Reserve tokens for completion
        reserved_completion = max_new_tokens
        available_for_context = budget.remaining_tokens - reserved_completion
        
        if available_for_context <= 0:
            logger.warning(f"Budget exhausted for {conversation_id}")
            # Return only system messages
            return system_messages if keep_system else [], self.counter.count_messages_tokens(system_messages)
        
        # Include system messages
        trimmed = system_messages if keep_system else []
        current_tokens = self.counter.count_messages_tokens(trimmed)
        
        # Add messages from most recent backwards until budget exceeded
        for msg in reversed(other_messages):
            msg_tokens = self.counter.count_tokens(str(msg.get("content", ""))) + 4
            
            if current_tokens + msg_tokens <= available_for_context:
                trimmed.insert(len(system_messages), msg)
                current_tokens += msg_tokens
            else:
                logger.debug(
                    f"Trimmed messages for {conversation_id}: "
                    f"kept {len(trimmed)} messages, {current_tokens} tokens"
                )
                break
        
        # If no messages fit, return system only
        if not trimmed or (keep_system and len(trimmed) == len(system_messages)):
            return system_messages if keep_system else [], current_tokens
        
        return trimmed, current_tokens
    
    def get_budget_status(self, conversation_id: str) -> Dict:
        """Get detailed budget status for a conversation."""
        budget = self.get_budget(conversation_id)
        return {
            "conversation_id": conversation_id,
            "total_tokens_used": budget.total_tokens,
            "prompt_tokens_used": budget.current_prompt_tokens,
            "completion_tokens_used": budget.current_completion_tokens,
            "max_prompt_tokens": budget.max_prompt_tokens,
            "max_completion_tokens": budget.max_completion_tokens,
            "remaining_tokens": budget.remaining_tokens,
            "budget_exhausted": budget.budget_exhausted,
            "last_reset": budget.last_reset.isoformat(),
            "reset_in_hours": budget.reset_window_hours - int((datetime.utcnow() - budget.last_reset).total_seconds() / 3600)
        }


# Global budget manager instance
_budget_manager: TokenBudgetManager = None


def get_budget_manager() -> TokenBudgetManager:
    """Get or initialize the global budget manager."""
    global _budget_manager
    if _budget_manager is None:
        default_prompt = int(os.getenv("DEFAULT_PROMPT_TOKENS", "1000000"))
        default_completion = int(os.getenv("DEFAULT_COMPLETION_TOKENS", "1000000"))
        _budget_manager = TokenBudgetManager(default_prompt, default_completion)
    return _budget_manager


def init_budget_manager(
    default_prompt_budget: int = 2000,
    default_completion_budget: int = 2000
) -> TokenBudgetManager:
    """Initialize budget manager with custom limits."""
    global _budget_manager
    _budget_manager = TokenBudgetManager(default_prompt_budget, default_completion_budget)
    return _budget_manager
