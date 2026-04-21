"""
Production-ready Local Memory Proxy for Open WebUI + Ollama.
Features: Streaming responses, SQLite/embeddings, token budgeting, 
per-project memory, intelligent compaction, production hardening.
"""

from __future__ import annotations

import json
import os
import time
import uuid
import logging
import asyncio
from typing import Any, Dict, List, Optional, AsyncGenerator, Union
from datetime import datetime

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from tenacity import retry, stop_after_attempt, wait_exponential

from .memory import MemoryStore
from .models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamResponse,
    MemoryCompactionRequest,
    ProjectInfo,
)
from .prompting import build_system_prompt, MemoryCompactor
from .database import get_db, DatabaseManager
from .retrieval import SemanticRetriever, EmbeddingModel, retrieve_context
from .tokenbudget import get_budget_manager, TokenBudgetManager, TokenCounter
from .agent import AgentLoop
from .planner import Planner
from .executor import Executor
from .tools import ToolRegistry
from .tool_executor import ToolExecutor
from .approval import ApprovalPolicy
from .reflection import ReflectionLayer
from .workspace_state import WorkspaceTracker
from .routing import determine_execution_route, AGENT_PATH

# Configuration
load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_CHAT_PATH = os.getenv("OLLAMA_CHAT_PATH", "/api/chat")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemma4:e2b")
MEMORY_DIR = os.getenv("MEMORY_DIR", "./data/memory")
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/memory.db")
PROXY_API_KEY = os.getenv("PROXY_API_KEY", "local-dev-key")
MAX_ARCHIVE_TURNS = int(os.getenv("MAX_ARCHIVE_TURNS", "200"))
MAX_RETRIEVED_ITEMS = int(os.getenv("MAX_RETRIEVED_ITEMS", "6"))
ENABLE_MEMORY_UPDATE = os.getenv("ENABLE_MEMORY_UPDATE", "true").lower() == "true"
USE_SEMANTIC_SEARCH = os.getenv("USE_SEMANTIC_SEARCH", "true").lower() == "true"
USE_STREAMING = os.getenv("USE_STREAMING", "true").lower() == "true"
COMPACTION_INTERVAL = int(os.getenv("COMPACTION_INTERVAL", "100"))  # Turns
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Logging setup
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="MacAgent Proxy",
    version="2.0.0",
    description="Production-ready local memory proxy for Open WebUI + Ollama"
)

# Initialize components
store = MemoryStore(
    MEMORY_DIR,
    MAX_ARCHIVE_TURNS,
    MAX_RETRIEVED_ITEMS,
    use_sqlite=True,
    db_manager=None  # Will use global instance from get_db()
)

db: DatabaseManager = get_db()
budget_manager: TokenBudgetManager = get_budget_manager()
embedding_model: EmbeddingModel = EmbeddingModel(use_ollama=False) if USE_SEMANTIC_SEARCH else None
compactor: MemoryCompactor = MemoryCompactor(default_model=DEFAULT_MODEL)
token_counter: TokenCounter = TokenCounter()

# V5 Global Components
planner: Optional[Planner] = None
executor: Optional[Executor] = None
agent_loop: Optional[AgentLoop] = None
workspace_tracker: WorkspaceTracker = WorkspaceTracker()
tool_registry: ToolRegistry = ToolRegistry()
tool_executor: ToolExecutor = ToolExecutor(tool_registry)
approval_policy: ApprovalPolicy = ApprovalPolicy()
reflection_layer: ReflectionLayer = ReflectionLayer()
http_client: Optional[httpx.AsyncClient] = None

# Projects storage (in-memory for now; can be extended to DB)
_projects: Dict[str, ProjectInfo] = {}


@app.on_event("startup")
async def startup():
    """Initialize on startup."""
    logger.info(f"Starting MacAgent Proxy v2.0.0")
    logger.info(f"Ollama: {OLLAMA_BASE_URL}")
    logger.info(f"Database: {DATABASE_PATH}")
    logger.info(f"Semantic Search: {USE_SEMANTIC_SEARCH}")
    logger.info(f"Streaming: {USE_STREAMING}")
    
    global http_client, planner, executor, agent_loop
    http_client = httpx.AsyncClient(timeout=180.0)
    planner = Planner(OLLAMA_BASE_URL, DEFAULT_MODEL, http_client)
    executor = Executor(
        OLLAMA_BASE_URL, DEFAULT_MODEL, http_client, 
        tool_executor, approval_policy, reflection_layer
    )
    agent_loop = AgentLoop(planner, executor)
    logger.info("V5 Agent Runtime initialized.")


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    if http_client:
        await http_client.aclose()
        logger.info("HTTP client closed.")


# ============================================================================
# Health & Info Endpoints
# ============================================================================

@app.get("/health")
async def health() -> Dict[str, Any]:
    """Health check endpoint."""
    return {
        "ok": True,
        "service": "macagent-proxy",
        "version": "2.0.0",
        "ollama_base_url": OLLAMA_BASE_URL,
        "default_model": DEFAULT_MODEL,
        "features": {
            "streaming": USE_STREAMING,
            "semantic_search": USE_SEMANTIC_SEARCH,
            "memory_compaction": True,
            "token_budgeting": True,
            "per_project_memory": True
        }
    }


@app.get("/v1/models")
async def list_models() -> Dict[str, Any]:
    """List available models (OpenAI-compatible)."""
    # Open WebUI expects an OpenAI-like models list response.
    model_name = os.getenv("DEFAULT_MODEL") or DEFAULT_MODEL or "gemma4:e2b"
    return {
        "object": "list",
        "data": [
            {
                "id": model_name,
                "object": "model",
                "owned_by": "macagent",
            }
        ],
    }


# ============================================================================
# Core Chat Completion Endpoints
# ============================================================================

@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: Optional[str] = Header(default=None),
) -> Union[StreamingResponse, JSONResponse]:
    """
    OpenAI-compatible chat completions endpoint.
    Supports both streaming and non-streaming responses.
    """
    _validate_api_key(authorization)
    
    # V5 Routing
    project_id = request.project_id or "default"
    conversation_id = request.conversation_id or request.user or _derive_conversation_id(request)
    memory = store.load(conversation_id, project_id)
    
    route = determine_execution_route(request, memory)
    
    if route == AGENT_PATH:
        logger.info(f"[{project_id}:{conversation_id}] Routing to AGENT_PATH")
        user_text = _content_to_text(request.messages[-1].content)
        result = await agent_loop.run(user_text, memory, workspace_tracker)
        status = result.get("status")
        agent_payload: Dict[str, Any] = {
            "status": status,
            "plan_remaining": result.get("plan_remaining"),
            "tool_call": result.get("tool_call"),
        }
        # Persist agent continuation state for approval/continue flows (legacy JSON merge handles reload).
        try:
            if status == "approval_required":
                memory.pending_goal = memory.pending_goal or result.get("goal") or user_text
                memory.pending_approval = {
                    "tool_call": result.get("tool_call"),
                    "plan_remaining": result.get("plan_remaining") or result.get("plan") or [],
                    "history": result.get("history") or [],
                    "goal": memory.pending_goal,
                }
                memory.pending_plan = None
                memory.pending_history = None
                store.save(memory)
            elif status == "partial":
                memory.pending_goal = memory.pending_goal or user_text
                memory.pending_plan = result.get("plan_remaining") or []
                memory.pending_history = result.get("history") or []
                memory.pending_approval = None
                store.save(memory)
            else:
                # Completed or unknown: clear pending state.
                if getattr(memory, "pending_approval", None) or getattr(memory, "pending_plan", None):
                    memory.pending_approval = None
                    memory.pending_plan = None
                    memory.pending_history = None
                    memory.pending_goal = None
                    store.save(memory)
        except Exception as e:
            logger.warning(f"Failed to persist agent continuation state: {e}")
        # Ensure we never return the internal agent fallback string as the only content.
        message = result.get("message") or ""
        if message.strip() == "Task finished or iteration limit reached.":
            message = ""
        if status == "approval_required":
            # Return an explicit approval payload (client-safe) in addition to x_agent_status.
            if not message:
                message = json.dumps(
                    {
                        "status": "awaiting_approval",
                        "proposed_action": result.get("tool_call"),
                    },
                    indent=2,
                )
        if not message:
            # Provide a structured partial output rather than a bare internal status.
            message = json.dumps(
                {
                    "status": status or "unknown",
                    "note": "Agent did not produce a user-facing summary. See x_agent_payload.",
                    "history_excerpt": (result.get("history") or [])[-6:],
                    "plan_remaining": result.get("plan_remaining"),
                },
                indent=2,
            )
        return JSONResponse(content={
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model or DEFAULT_MODEL,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": message},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "x_agent_status": status,
            "x_agent_payload": agent_payload,
        })

    # Use streaming if requested and enabled
    if request.stream and USE_STREAMING:
        return StreamingResponse(
            _stream_chat_completions(request),
            media_type="text/event-stream"
        )
    else:
        return await _chat_completions_non_streaming(request)


async def _chat_completions_non_streaming(
    request: ChatCompletionRequest
) -> JSONResponse:
    """Handle non-streaming chat completion."""
    project_id = request.project_id or "default"
    conversation_id = request.conversation_id or request.user or _derive_conversation_id(request)
    
    logger.info(f"[{project_id}:{conversation_id}] Non-streaming request")
    
    # Load memory and retrieve context
    memory = store.load(conversation_id, project_id)
    user_messages = [m for m in request.messages if m.role == "user"]
    latest_user_text = _content_to_text(user_messages[-1].content if user_messages else "")
    
    # Semantic retrieval
    retrieved_items = []
    if USE_SEMANTIC_SEARCH:
        db_items = db.get_memory_items(conversation_id, project_id)
        retrieved_items = await retrieve_context(
            latest_user_text,
            db_items,
            top_k=MAX_RETRIEVED_ITEMS,
            embedding_model=embedding_model
        )
    
    retrieved_text = store.retrieve_relevant(memory, latest_user_text, retrieved_items)
    
    # Build system prompt
    system_prompt = build_system_prompt(memory, retrieved_text)
    
    # Prepare messages for Ollama
    ollama_messages = [{"role": "system", "content": system_prompt}]
    recent_messages = request.messages[-6:]
    for msg in recent_messages:
        ollama_messages.append({"role": msg.role, "content": _content_to_text(msg.content)})
    
    # Token budgeting
    prompt_tokens = token_counter.count_messages_tokens(ollama_messages)
    budget_manager.record_prompt_tokens(conversation_id, prompt_tokens)
    
    # Trim if necessary
    budget = budget_manager.get_budget(conversation_id)
    if budget.budget_exhausted:
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "message": "Token budget exhausted for this conversation",
                    "type": "token_budget_exceeded",
                    "budget_status": budget_manager.get_budget_status(conversation_id)
                }
            }
        )
    
    ollama_messages, trimmed_tokens = budget_manager.trim_messages_to_budget(
        ollama_messages,
        conversation_id,
        max_new_tokens=request.max_tokens or 1500
    )
    
    # Call Ollama
    try:
        ollama_response = await _call_ollama({
            "model": request.model or DEFAULT_MODEL,
            "messages": ollama_messages,
            "stream": False,
            "temperature": request.temperature or 0.2,
        })
    except Exception as e:
        logger.error(f"Ollama error: {e}")
        raise HTTPException(status_code=502, detail=f"Ollama error: {str(e)}")
    
    assistant_text = ollama_response.get("message", {}).get("content", "")
    completion_tokens = token_counter.count_tokens(assistant_text)
    budget_manager.record_completion_tokens(conversation_id, completion_tokens)
    
    # Update memory
    if ENABLE_MEMORY_UPDATE:
        memory = store.append_turn(memory, latest_user_text, assistant_text)
        
        # Check if compaction needed
        if memory.turn_count % COMPACTION_INTERVAL == 0 and memory.turn_count > 0:
            asyncio.create_task(_compact_memory_async(conversation_id, project_id, memory))
    
    # Response
    response = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model or DEFAULT_MODEL,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": assistant_text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "x_memory": {
            "conversation_id": conversation_id,
            "project_id": project_id,
            "retrieved_items": len(retrieved_text),
            "project_summary_items": len(memory.project_summary),
            "turn_count": memory.turn_count,
            "budget_status": budget_manager.get_budget_status(conversation_id)
        },
    }
    
    logger.info(
        f"[{project_id}:{conversation_id}] "
        f"Tokens: prompt={prompt_tokens}, completion={completion_tokens} | "
        f"Retrieved: {len(retrieved_text)} items"
    )
    
    return JSONResponse(content=response)


async def _stream_chat_completions(request: ChatCompletionRequest) -> AsyncGenerator[str, None]:
    """Stream chat completions via SSE."""
    project_id = request.project_id or "default"
    conversation_id = request.conversation_id or request.user or _derive_conversation_id(request)
    
    logger.info(f"[{project_id}:{conversation_id}] Streaming request")
    
    # Load memory and retrieve context
    memory = store.load(conversation_id, project_id)
    user_messages = [m for m in request.messages if m.role == "user"]
    latest_user_text = _content_to_text(user_messages[-1].content if user_messages else "")
    
    # Semantic retrieval
    retrieved_items = []
    if USE_SEMANTIC_SEARCH:
        db_items = db.get_memory_items(conversation_id, project_id)
        retrieved_items = await retrieve_context(
            latest_user_text,
            db_items,
            top_k=MAX_RETRIEVED_ITEMS,
            embedding_model=embedding_model
        )
    
    retrieved_text = store.retrieve_relevant(memory, latest_user_text, retrieved_items)
    
    # Build system prompt
    system_prompt = build_system_prompt(memory, retrieved_text)
    
    # Prepare messages
    ollama_messages = [{"role": "system", "content": system_prompt}]
    recent_messages = request.messages[-6:]
    for msg in recent_messages:
        ollama_messages.append({"role": msg.role, "content": _content_to_text(msg.content)})
    
    # Token tracking
    prompt_tokens = token_counter.count_messages_tokens(ollama_messages)
    budget_manager.record_prompt_tokens(conversation_id, prompt_tokens)
    
    # Stream from Ollama
    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    full_response = ""
    completion_tokens = 0
    
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_BASE_URL.rstrip('/')}{OLLAMA_CHAT_PATH}",
                json={
                    "model": request.model or DEFAULT_MODEL,
                    "messages": ollama_messages,
                    "stream": True,
                    "temperature": request.temperature or 0.2,
                },
            ) as response:
                if response.status_code >= 400:
                    yield f"data: {json.dumps({'error': 'Ollama error'})}\n\n"
                    return
                
                # First chunk includes metadata
                first_chunk = True
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    
                    try:
                        data = json.loads(line)
                        message = data.get("message", {})
                        content = message.get("content", "")
                        if content:
                            full_response += content
                            
                            # Create SSE event
                            event = {
                                "id": request_id,
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": request.model or DEFAULT_MODEL,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": content},
                                    "finish_reason": None,
                                }],
                            }
                            
                            # Add memory metadata to first chunk
                            if first_chunk:
                                event["x_memory"] = {
                                    "conversation_id": conversation_id,
                                    "project_id": project_id,
                                    "retrieved_items": len(retrieved_text),
                                    "project_summary_items": len(memory.project_summary),
                                }
                                first_chunk = False
                            
                            yield f"data: {json.dumps(event)}\n\n"
                    except json.JSONDecodeError:
                        continue
        
        # Final chunk (stop)
        completion_tokens = token_counter.count_tokens(full_response)
        budget_manager.record_completion_tokens(conversation_id, completion_tokens)
        
        final_event = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": request.model or DEFAULT_MODEL,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        }
        yield f"data: {json.dumps(final_event)}\n\n"
        yield "data: [DONE]\n\n"
        
        # Update memory
        if ENABLE_MEMORY_UPDATE:
            memory = store.append_turn(memory, latest_user_text, full_response)
            if memory.turn_count % COMPACTION_INTERVAL == 0 and memory.turn_count > 0:
                asyncio.create_task(_compact_memory_async(conversation_id, project_id, memory))
        
        logger.info(
            f"[{project_id}:{conversation_id}] Streamed: "
            f"prompt={prompt_tokens}, completion={completion_tokens}"
        )
        
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


# ============================================================================
# Project Management Endpoints
# ============================================================================

@app.post("/v1/projects")
async def create_project(
    project_info: ProjectInfo,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Create a new project with custom settings."""
    _validate_api_key(authorization)
    
    if project_info.project_id in _projects:
        raise HTTPException(status_code=400, detail="Project already exists")
    
    _projects[project_info.project_id] = project_info
    logger.info(f"Created project: {project_info.project_id}")
    
    return JSONResponse(
        status_code=201,
        content=project_info.model_dump()
    )


@app.get("/v1/projects/{project_id}")
async def get_project(
    project_id: str,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Get project info."""
    _validate_api_key(authorization)
    
    if project_id not in _projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return _projects[project_id].model_dump()


# ============================================================================
# Memory Compaction Endpoint
# ============================================================================

@app.post("/v1/memory/compact")
async def compact_memory(
    request: MemoryCompactionRequest,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Trigger memory compaction for a conversation."""
    _validate_api_key(authorization)
    
    memory = store.load(request.conversation_id, request.project_id)
    result = await compactor.compact(memory, db)
    
    # Save compacted memory
    store.save(result["new_memory_state"])
    
    return JSONResponse(content={
        "conversation_id": request.conversation_id,
        "project_id": request.project_id,
        "items_compacted": result["items_compacted"],
        "items_created": result["items_created"],
        "summary": result["summary"],
        "compacted_at": datetime.utcnow().isoformat(),
    })


# ============================================================================
# Budget Status Endpoint
# ============================================================================

@app.get("/v1/budget/{conversation_id}")
async def get_budget(
    conversation_id: str,
    project_id: str = "default",
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Get token budget status for conversation."""
    _validate_api_key(authorization)
    return budget_manager.get_budget_status(conversation_id)


# ============================================================================
# Utility Functions
# ============================================================================

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def _call_ollama(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Call Ollama chat API with retry logic."""
    url = f"{OLLAMA_BASE_URL.rstrip('/')}{OLLAMA_CHAT_PATH}"
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code >= 400:
                raise Exception(f"Ollama error {response.status_code}: {response.text}")
            
            # Handle empty response
            if not response.text:
                raise Exception("Ollama returned empty response")
            
            return response.json()
    except Exception as e:
        logger.error(f"Ollama call failed: {e}")
        raise


async def _compact_memory_async(conversation_id: str, project_id: str, memory):
    """Async task for memory compaction."""
    try:
        await compactor.compact(memory, db)
        logger.info(f"Auto-compacted memory for {project_id}:{conversation_id}")
    except Exception as e:
        logger.error(f"Auto-compaction failed: {e}")


def _validate_api_key(authorization: Optional[str]) -> None:
    """Validate API key from Authorization header."""
    if not PROXY_API_KEY:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    expected = f"Bearer {PROXY_API_KEY}"
    if authorization.strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _content_to_text(content: Any) -> str:
    """Convert message content to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or json.dumps(item, ensure_ascii=False)
                parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _derive_conversation_id(request: ChatCompletionRequest) -> str:
    """Derive conversation ID from request content."""
    seed_parts = []
    if request.messages:
        for message in request.messages[-3:]:
            seed_parts.append(_content_to_text(message.content)[:80])
    seed = "|".join(seed_parts).strip() or "default"
    return seed[:80]
