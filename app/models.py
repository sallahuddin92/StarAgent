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
    model_config = {
        "extra": "allow"
    }
    conversation_id: str
    project_id: str = "default"
    project_summary: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    style_preferences: List[str] = Field(default_factory=list)
    archive_turns: List[Dict[str, str]] = Field(default_factory=list)
    turn_count: int = 0
    # Agent-path continuation state (canonical in SQLite; legacy JSON sidecar is fallback/import only).
    pending_approval: Optional[Dict[str, Any]] = None
    pending_plan: Optional[List[str]] = None
    pending_history: Optional[List[Dict[str, Any]]] = None
    pending_goal: Optional[str] = None
    
    # StarAgent v3 explicit system-level research trigger flag
    force_research_flag: bool = False
    last_api_error: Optional[str] = None


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


# ============================================================================
# Phase 4: Iterative Task Engine + Document Research Mode
# ============================================================================

TaskStatus = Literal["pending", "running", "paused", "completed", "failed", "partial"]


class TaskCreateRequest(BaseModel):
    project_id: str = "default"
    conversation_id: str = "default"
    task_type: str = "agent"  # agent | research
    user_goal: str
    definition_of_done: Optional[str] = None
    max_steps: int = 25
    max_retries: int = 2
    run_now: bool = True


class TaskContinueRequest(BaseModel):
    # Continue the run for a bounded amount of work.
    max_step_advances: int = 3
    max_duration_s: float = 20.0


class TaskActionRequest(BaseModel):
    action: Literal["continue", "approve", "reject"] = "continue"
    # Optional for reject.
    reason: Optional[str] = None
    max_step_advances: int = 3
    max_duration_s: float = 20.0


class ResearchRunRequest(BaseModel):
    project_id: str = "default"
    conversation_id: str = "default"
    path: str
    files: Optional[List[str]] = None
    question: Optional[str] = None
    mode: Literal["summary", "research", "comparison"] = "research"
    max_steps: int = 60
    max_retries: int = 1
    run_now: bool = True


class RepoAuditRunRequest(BaseModel):
    project_id: str = "default"
    conversation_id: str = "default"
    path: str
    question: Optional[str] = None
    max_steps: int = 25
    max_retries: int = 1
    run_now: bool = True


class IssueTriageRunRequest(BaseModel):
    project_id: str = "default"
    conversation_id: str = "default"
    path: str
    issue: str
    files: Optional[List[str]] = None
    logs: Optional[List[str]] = None
    max_steps: int = 25
    max_retries: int = 1
    run_now: bool = True


class WritingRunRequest(BaseModel):
    project_id: str = "default"
    conversation_id: str = "default"
    path: str
    goal: str
    files: Optional[List[str]] = None
    max_steps: int = 25
    max_retries: int = 1
    run_now: bool = True


# ============================================================================
# Preset Workflows (thin wrappers over existing profiles/task engine)
# ============================================================================


class PresetRunRequest(BaseModel):
    project_id: str = "default"
    conversation_id: str = "default"
    # Common inputs used by presets (only some fields apply depending on preset).
    path: Optional[str] = None
    question: Optional[str] = None
    issue: Optional[str] = None
    goal: Optional[str] = None
    files: Optional[List[str]] = None
    logs: Optional[List[str]] = None
    mode: Optional[Literal["summary", "research", "comparison"]] = None
    output_path: Optional[str] = None

    max_steps: Optional[int] = None
    max_retries: Optional[int] = None
    run_now: bool = True
