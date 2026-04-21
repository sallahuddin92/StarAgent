from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Any


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    user: Optional[str] = None
    conversation_id: Optional[str] = None
    project_id: Optional[str] = "default"  # Per-project isolation
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class MemoryState(BaseModel):
    conversation_id: str
    project_id: str = "default"
    project_summary: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    style_preferences: List[str] = Field(default_factory=list)
    archive_turns: List[Dict[str, str]] = Field(default_factory=list)
    turn_count: int = 0
    # Agent-path continuation state (persisted via legacy JSON; DB stores core memory only).
    pending_approval: Optional[Dict[str, Any]] = None
    pending_plan: Optional[List[str]] = None
    pending_history: Optional[List[Dict[str, Any]]] = None
    pending_goal: Optional[str] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict[str, Any]]
    usage: Dict[str, int]
    x_memory: Optional[Dict[str, Any]] = None


class ChatCompletionStreamResponse(BaseModel):
    """SSE stream event for streaming responses."""
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[Dict[str, Any]]
    x_memory: Optional[Dict[str, Any]] = None  # Only in first chunk


class MemoryCompactionRequest(BaseModel):
    """Request to compact conversation memory."""
    conversation_id: str
    project_id: str = "default"
    force: bool = False  # Force compaction even if not due


class ProjectInfo(BaseModel):
    """Project information and settings."""
    project_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    conversation_count: int = 0
    custom_system_prompt: Optional[str] = None
    custom_compaction_prompt: Optional[str] = None
    embedding_model: str = "all-MiniLM-L6-v2"
    token_budget_prompt: int = 2000
    token_budget_completion: int = 2000


class RetrievedMemoryItem(BaseModel):
    """Memory item retrieved for context injection."""
    category: str
    content: str
    relevance_score: float


class MemoryCompactionResponse(BaseModel):
    """Response from memory compaction operation."""
    conversation_id: str
    project_id: str
    compacted_at: datetime
    items_compacted: int
    items_created: int
    summary: str
