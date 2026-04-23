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
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncGenerator, Union
from datetime import datetime
import base64

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from tenacity import retry, stop_after_attempt, wait_exponential

from .version import __version__
from .memory import MemoryStore
from .models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamResponse,
    MemoryCompactionRequest,
    ProjectInfo,
    TaskCreateRequest,
    TaskActionRequest,
    ResearchRunRequest,
    RepoAuditRunRequest,
    IssueTriageRunRequest,
    WritingRunRequest,
    PresetRunRequest,
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
from .task_engine import TaskEngine
from .research_mode import ResearchPipeline
from .llm_client import OllamaChatClient
from .repo_audit import RepoAuditPipeline
from .issue_triage import IssueTriagePipeline
from .writing_profile import WritingPipeline
from .presets import PRESETS, PACKS, list_presets, list_preset_packs, default_release_review_output_path
from .dashboard_ui import render_dashboard_html
from .artifact_registry import task_conventions
from .task_metadata import (
    task_meta as build_task_meta,
    time_meta as _tm_time_meta,
    primary_artifact as _tm_primary_artifact,
    dataset_meta as _tm_dataset_meta,
)

# Configuration
load_dotenv()

def _truthy_env(name: str, default: str = "false") -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


STARAGENT_BRAND_API = _truthy_env("STARAGENT_BRAND_API", "false")
API_SERVICE_NAME = "staragent-proxy" if STARAGENT_BRAND_API else "macagent-proxy"
API_OWNED_BY = "staragent" if STARAGENT_BRAND_API else "macagent"

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
    title="StarAgent Proxy" if STARAGENT_BRAND_API else "MacAgent Proxy",
    version=__version__,
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
research_pipeline: Optional[ResearchPipeline] = None
repo_audit_pipeline: Optional[RepoAuditPipeline] = None
issue_triage_pipeline: Optional[IssueTriagePipeline] = None
writing_pipeline: Optional[WritingPipeline] = None
task_engine: Optional[TaskEngine] = None

# Projects storage (in-memory for now; can be extended to DB)
_projects: Dict[str, ProjectInfo] = {}


@app.on_event("startup")
async def startup():
    """Initialize on startup."""
    logger.info(f"Starting {'StarAgent' if STARAGENT_BRAND_API else 'MacAgent'} Proxy v{__version__}")
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

    # Phase 4: Task engine + research pipeline (additive; does not affect /v1/chat behavior).
    global research_pipeline, repo_audit_pipeline, issue_triage_pipeline, writing_pipeline, task_engine
    llm = OllamaChatClient(OLLAMA_BASE_URL, OLLAMA_CHAT_PATH, DEFAULT_MODEL, http_client)
    research_pipeline = ResearchPipeline(llm)
    repo_audit_pipeline = RepoAuditPipeline(llm)
    issue_triage_pipeline = IssueTriagePipeline(llm)
    writing_pipeline = WritingPipeline(llm)
    task_engine = TaskEngine(
        db=db,
        store=store,
        planner=planner,
        executor=executor,
        workspace=workspace_tracker,
        approval_policy=approval_policy,
        research=research_pipeline,
        repo_audit=repo_audit_pipeline,
        issue_triage=issue_triage_pipeline,
        writing=writing_pipeline,
    )
    logger.info("Phase 4 Task Engine initialized.")


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
        "service": API_SERVICE_NAME,
        "version": __version__,
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
                "owned_by": API_OWNED_BY,
            }
        ],
    }


# ============================================================================
# Lightweight Dashboard (local UI)
# ============================================================================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(render_dashboard_html())


@app.get("/dashboard/", response_class=HTMLResponse)
async def dashboard_slash() -> HTMLResponse:
    return HTMLResponse(render_dashboard_html())


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
                # Completed or unknown: always clear pending state and persist.
                # This prevents "stuck" approvals/continuations if the agent loop cleared
                # memory.pending_* internally before we reached this branch.
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
# Phase 4: Iterative Task Engine + Document Research Mode
# ============================================================================

def _task_progress(tr: Dict[str, Any], steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    completed = [s for s in steps if s.get("status") == "completed"]
    failed = [s for s in steps if s.get("status") == "failed"]
    paused = [s for s in steps if s.get("status") == "paused"]
    running = [s for s in steps if s.get("status") == "running"]
    pending = [s for s in steps if s.get("status") == "pending"]

    # Determine "current" step: prefer the index pointer, else first non-completed.
    cur = None
    idx = int(tr.get("current_step_index") or 0)
    for s in steps:
        if int(s.get("step_index") or 0) == idx:
            cur = s
            break
    if cur is None:
        for s in steps:
            if s.get("status") != "completed":
                cur = s
                break
    pct = 0.0
    if steps:
        pct = round(len(completed) / max(1, len(steps)) * 100.0, 1)
    return {
        "counts": {
            "total": len(steps),
            "completed": len(completed),
            "pending": len(pending),
            "running": len(running),
            "paused": len(paused),
            "failed": len(failed),
        },
        "percent_complete": pct,
        "current_step": cur,
        "last_completed_step": completed[-1] if completed else None,
    }

def _primary_artifact_name(task_type: Optional[str]) -> Optional[str]:
    # Compatibility shim: use centralized artifact conventions.
    conv = task_conventions(task_type, artifacts_json=None)
    return conv.primary


def _parse_iso_dt(s: Any) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        st = str(s)
        # stored as "...Z" sometimes; datetime.fromisoformat doesn't accept "Z"
        if st.endswith("Z"):
            st = st[:-1] + "+00:00"
        return datetime.fromisoformat(st)
    except Exception:
        return None


def _task_time_meta(tr: Dict[str, Any]) -> Dict[str, Any]:
    # Compatibility shim: use centralized task metadata builder.
    return _tm_time_meta(tr)

def _task_primary_artifact(tr: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Compatibility shim: use centralized task metadata builder.
    return _tm_primary_artifact(tr)


def _safe_task_artifact_path(task_id: str, artifact_name: str) -> Path:
    tr = db.get_task_run(task_id)
    if not tr:
        raise HTTPException(status_code=404, detail="Task not found")
    aj = tr.get("artifacts_json") or {}
    artifact_dir = Path(str(aj.get("artifact_dir") or (Path(".runtime") / "tasks" / task_id))).resolve()
    if not artifact_dir.exists() or not artifact_dir.is_dir():
        raise HTTPException(status_code=404, detail="Artifact directory not found")
    p = (artifact_dir / artifact_name).resolve()
    # Prevent path traversal
    if artifact_dir not in p.parents and artifact_dir != p:
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return p


@app.post("/v1/tasks")
async def create_task(
    req: TaskCreateRequest,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if not task_engine:
        raise HTTPException(status_code=503, detail="Task engine not initialized")

    tr = task_engine.create_task(
        project_id=req.project_id,
        conversation_id=req.conversation_id,
        task_type=req.task_type,
        user_goal=req.user_goal,
        definition_of_done=req.definition_of_done,
        max_steps=req.max_steps,
        max_retries=req.max_retries,
        artifacts_json={},
    )
    # Record artifact dir for clients.
    artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
    db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
    tr = db.get_task_run(tr["task_id"]) or tr

    if req.run_now:
        out = await task_engine.run(tr["task_id"])
        return JSONResponse(content=out.to_dict())
    return JSONResponse(status_code=201, content={"task": tr, "steps": []})

@app.get("/v1/tasks")
async def list_tasks(
    project_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    runs = db.list_task_runs(
        project_id=project_id,
        conversation_id=conversation_id,
        status=status,
        limit=limit,
        offset=offset,
        order="updated_desc",
    )
    # Additive metadata for clients (kept light; no file reads here).
    enriched: List[Dict[str, Any]] = []
    for tr in runs:
        try:
            aj = tr.get("artifacts_json") or {}
        except Exception:
            aj = {}
        conv = task_conventions(tr.get("task_type"), artifacts_json=aj)
        labels = {
            "pack_name": aj.get("pack_name") if isinstance(aj.get("pack_name"), str) else None,
            "preset_name": (aj.get("preset") if isinstance(aj.get("preset"), str) else None)
            or (aj.get("pack_preset") if isinstance(aj.get("pack_preset"), str) else None),
        }
        pending = aj.get("pending_approval") if isinstance(aj, dict) else None
        enriched.append(
            {
                **tr,
                "primary_artifact_name": conv.primary,
                "labels": labels,
                "approval_required": bool(isinstance(pending, dict) and pending.get("tool_call")),
            }
        )
    return JSONResponse(content={"tasks": enriched, "limit": limit, "offset": offset})


@app.get("/v1/tasks/{task_id}")
async def get_task_status(
    task_id: str,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if not task_engine:
        raise HTTPException(status_code=503, detail="Task engine not initialized")
    out = task_engine.get_task(task_id)
    if not out:
        raise HTTPException(status_code=404, detail="Task not found")
    return JSONResponse(content=out.to_dict())

@app.get("/v1/tasks/{task_id}/inspect")
async def inspect_task(
    task_id: str,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    tr = db.get_task_run(task_id)
    if not tr:
        raise HTTPException(status_code=404, detail="Task not found")
    steps = db.list_task_steps(task_id)
    meta = build_task_meta(tr, steps)
    prog = meta.get("progress") or _task_progress(tr, steps)
    return JSONResponse(
        content={
            "task": tr,
            "progress": prog,
            "time": _task_time_meta(tr),
            "primary_artifact": _task_primary_artifact(tr),
            "dataset_meta": _tm_dataset_meta(tr),
            "task_meta": meta,
            "steps": steps,
        }
    )


@app.get("/v1/tasks/{task_id}/summary")
async def task_summary(
    task_id: str,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    tr = db.get_task_run(task_id)
    if not tr:
        raise HTTPException(status_code=404, detail="Task not found")
    steps = db.list_task_steps(task_id)
    meta = build_task_meta(tr, steps)
    prog = meta.get("progress") or _task_progress(tr, steps)

    final = tr.get("final_summary")
    if not final and prog.get("last_completed_step"):
        final = (prog["last_completed_step"].get("output_summary") or "")[:1200]
    out = {
        "task": {
            "task_id": tr.get("task_id"),
            "project_id": tr.get("project_id"),
            "conversation_id": tr.get("conversation_id"),
            "task_type": tr.get("task_type"),
            "status": tr.get("status"),
            "current_step_index": tr.get("current_step_index"),
            "retry_count": tr.get("retry_count"),
            "final_verdict": tr.get("final_verdict"),
            "final_summary": final,
            "artifacts_json": tr.get("artifacts_json") or {},
            "created_at": tr.get("created_at"),
            "updated_at": tr.get("updated_at"),
        },
        "progress": prog,
        "time": _task_time_meta(tr),
        "primary_artifact": _task_primary_artifact(tr),
        "dataset_meta": _tm_dataset_meta(tr),
        "task_meta": meta,
    }
    return JSONResponse(content=out)


@app.get("/v1/tasks/{task_id}/logs")
async def task_logs(
    task_id: str,
    tail_steps: int = 50,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    tr = db.get_task_run(task_id)
    if not tr:
        raise HTTPException(status_code=404, detail="Task not found")
    steps = db.list_task_steps(task_id)
    tail_steps = max(1, min(int(tail_steps or 50), 300))
    steps = steps[-tail_steps:]
    logs = []
    for s in steps:
        logs.append(
            {
                "step_index": s.get("step_index"),
                "step_type": s.get("step_type"),
                "status": s.get("status"),
                "attempt_count": s.get("attempt_count"),
                "instruction": (s.get("instruction") or "")[:400],
                "output_summary": s.get("output_summary"),
                "verifier_result": s.get("verifier_result"),
                "artifact_path": s.get("artifact_path"),
                "updated_at": s.get("updated_at"),
            }
        )
    return JSONResponse(content={"task_id": task_id, "status": tr.get("status"), "logs": logs})


@app.get("/v1/tasks/{task_id}/artifacts")
async def get_task_artifacts(
    task_id: str,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    tr = db.get_task_run(task_id)
    if not tr:
        raise HTTPException(status_code=404, detail="Task not found")
    aj = tr.get("artifacts_json") or {}
    artifact_dir = str(aj.get("artifact_dir") or (Path(".runtime") / "tasks" / task_id))
    d = Path(artifact_dir)
    conv = task_conventions(tr.get("task_type"), artifacts_json=aj)
    primary_name = conv.primary
    files: List[Dict[str, Any]] = []
    if d.exists() and d.is_dir():
        for p in sorted(d.glob("*")):
            if not p.is_file():
                continue
            try:
                st = p.stat()
                ext = p.suffix.lower()
                ftype = "json" if ext == ".json" else ("markdown" if ext in {".md", ".markdown"} else ("text" if ext in {".txt", ".log", ".rst"} else "other"))
                files.append(
                    {
                        "name": p.name,
                        "type": ftype,
                        "size_bytes": int(st.st_size),
                        "mtime": int(st.st_mtime),
                        "is_primary": bool(primary_name and p.name == primary_name),
                        "preview_url": f"/v1/tasks/{task_id}/artifacts/{p.name}",
                    }
                )
            except Exception:
                files.append({"name": p.name, "is_primary": bool(primary_name and p.name == primary_name)})

    primary = _task_primary_artifact(tr)
    return JSONResponse(
        content={
            "task_id": task_id,
            "artifact_dir": artifact_dir,
            "primary_artifact": primary,
            "conventions": {"primary_name": conv.primary, "important": conv.important},
            "files": files,
        }
    )


@app.get("/v1/tasks/{task_id}/artifacts/{artifact_name}")
async def preview_task_artifact(
    task_id: str,
    artifact_name: str,
    format: str = "text",  # text|json
    max_bytes: int = 50_000,
    tail_lines: int = 200,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    p = _safe_task_artifact_path(task_id, artifact_name)

    # Important: do not treat 0 as "unset" for these query params.
    max_bytes = max(512, min(int(max_bytes), 500_000))
    tail_lines = max(0, min(int(tail_lines), 4000))
    raw = p.read_bytes()
    truncated = False
    if len(raw) > max_bytes:
        raw = raw[-max_bytes:]
        truncated = True

    ext = p.suffix.lower()
    content_text = ""
    try:
        content_text = raw.decode("utf-8")
    except Exception:
        content_text = raw.decode("latin-1", errors="replace")

    is_json = (format == "json") or (ext == ".json")
    if is_json:
        try:
            obj = json.loads(content_text)
            content_text = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            # Leave as text if not valid JSON
            pass

    # Tail after optional JSON pretty-printing (tailing raw JSON first often breaks parsing).
    if tail_lines > 0:
        lines = content_text.splitlines()
        if len(lines) > tail_lines:
            content_text = "\n".join(lines[-tail_lines:])
            truncated = True

    return JSONResponse(
        content={
            "task_id": task_id,
            "artifact_name": artifact_name,
            "artifact_path": str(p),
            "format": format,
            "truncated": truncated,
            "content": content_text,
        }
    )


@app.post("/v1/tasks/{task_id}/continue")
async def continue_task_run(
    task_id: str,
    req: TaskActionRequest,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if not task_engine:
        raise HTTPException(status_code=503, detail="Task engine not initialized")

    if req.action == "approve":
        out = await task_engine.approve(task_id)
        return JSONResponse(content=out.to_dict())
    if req.action == "reject":
        out = await task_engine.reject(task_id, reason=req.reason or "rejected")
        return JSONResponse(content=out.to_dict())

    out = await task_engine.run(task_id, max_step_advances=req.max_step_advances, max_duration_s=req.max_duration_s)
    return JSONResponse(content=out.to_dict())


@app.post("/v1/research/run")
async def research_run(
    req: ResearchRunRequest,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if not task_engine:
        raise HTTPException(status_code=503, detail="Task engine not initialized")

    tr = task_engine.create_task(
        project_id=req.project_id,
        conversation_id=req.conversation_id,
        task_type="research",
        user_goal=req.question or f"Research folder: {req.path}",
        definition_of_done="Artifacts written: file_index.json, chunk_summaries.json, file_summaries.md, research_brief.md, open_questions.md, final_report.md",
        max_steps=req.max_steps,
        max_retries=req.max_retries,
        artifacts_json={
            "root_path": req.path,
            "files": req.files,
            "question": req.question,
            "mode": req.mode,
        },
    )
    # Record artifact dir for clients.
    artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
    db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
    tr = db.get_task_run(tr["task_id"]) or tr
    if req.run_now:
        # Keep launch responsive: do minimal safe work and return quickly with a task_id.
        out = await task_engine.run(tr["task_id"], max_step_advances=1, max_duration_s=10.0)
        return JSONResponse(content=out.to_dict())
    return JSONResponse(status_code=201, content={"task": tr, "steps": []})


@app.post("/v1/repo_audit/run")
async def repo_audit_run(
    req: RepoAuditRunRequest,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if not task_engine:
        raise HTTPException(status_code=503, detail="Task engine not initialized")

    tr = task_engine.create_task(
        project_id=req.project_id,
        conversation_id=req.conversation_id,
        task_type="repo_audit",
        user_goal=req.question or f"Repo audit: {req.path}",
        definition_of_done="Artifacts written: file_index.json, entry_points.md, architecture_map.md, risk_notes.md, open_questions.md, audit_report.md",
        max_steps=req.max_steps,
        max_retries=req.max_retries,
        artifacts_json={"root_path": req.path, "question": req.question},
    )
    artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
    db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
    tr = db.get_task_run(tr["task_id"]) or tr
    if req.run_now:
        out = await task_engine.run(tr["task_id"], max_step_advances=3, max_duration_s=25.0)
        return JSONResponse(content=out.to_dict())
    return JSONResponse(status_code=201, content={"task": tr, "steps": []})


@app.post("/v1/issue_triage/run")
async def issue_triage_run(
    req: IssueTriageRunRequest,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if not task_engine:
        raise HTTPException(status_code=503, detail="Task engine not initialized")

    tr = task_engine.create_task(
        project_id=req.project_id,
        conversation_id=req.conversation_id,
        task_type="issue_triage",
        user_goal=req.issue,
        definition_of_done="Artifacts written: issue_summary.md, evidence_table.json, likely_causes.md, reproduction_steps.md, next_actions.md",
        max_steps=req.max_steps,
        max_retries=req.max_retries,
        artifacts_json={
            "root_path": req.path,
            "issue": req.issue,
            "files": req.files,
            "logs": req.logs,
        },
    )
    artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
    db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
    tr = db.get_task_run(tr["task_id"]) or tr
    if req.run_now:
        out = await task_engine.run(tr["task_id"], max_step_advances=3, max_duration_s=25.0)
        return JSONResponse(content=out.to_dict())
    return JSONResponse(status_code=201, content={"task": tr, "steps": []})


@app.post("/v1/write/run")
async def writing_run(
    req: WritingRunRequest,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if not task_engine:
        raise HTTPException(status_code=503, detail="Task engine not initialized")

    tr = task_engine.create_task(
        project_id=req.project_id,
        conversation_id=req.conversation_id,
        task_type="writing",
        user_goal=req.goal,
        definition_of_done="Artifacts written: source_index.json, outline.md, draft.md, final_output.md",
        max_steps=req.max_steps,
        max_retries=req.max_retries,
        artifacts_json={"root_path": req.path, "goal": req.goal, "files": req.files},
    )
    artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
    db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
    tr = db.get_task_run(tr["task_id"]) or tr
    if req.run_now:
        out = await task_engine.run(tr["task_id"], max_step_advances=3, max_duration_s=25.0)
        return JSONResponse(content=out.to_dict())
    return JSONResponse(status_code=201, content={"task": tr, "steps": []})


# ============================================================================
# Preset Workflows (operator-friendly entrypoints)
# ============================================================================


@app.get("/v1/presets")
async def presets_list(
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    # Additive response: existing clients rely on {"presets":[...]}.
    return JSONResponse(content={"presets": list_presets(), "packs": list_preset_packs()})


@app.get("/v1/presets/packs")
async def preset_packs_list(
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    return JSONResponse(content={"packs": list_preset_packs()})


async def _run_preset_internal(preset_name: str, req: PresetRunRequest) -> Dict[str, Any]:
    """
    Internal helper used by both /v1/presets/{preset}/run and preset packs.
    Returns a JSON-serializable dict.
    """
    if not task_engine:
        raise HTTPException(status_code=503, detail="Task engine not initialized")

    name = (preset_name or "").strip()
    spec = PRESETS.get(name)
    if not spec:
        raise HTTPException(status_code=404, detail="Unknown preset")

    max_steps = int(req.max_steps or spec.default_max_steps)
    max_retries = int(req.max_retries or spec.default_max_retries)

    if name in ("quick_repo_audit", "deep_repo_audit"):
        path = _require(req, "path")
        question = req.question
        if name == "deep_repo_audit" and question:
            question = f"{question}\n\n(Deep audit requested: emphasize architecture, risks, and unknowns.)"
        tr = task_engine.create_task(
            project_id=req.project_id,
            conversation_id=req.conversation_id,
            task_type="repo_audit",
            user_goal=question or f"Repo audit: {path}",
            definition_of_done="Artifacts written: file_index.json, entry_points.md, architecture_map.md, risk_notes.md, open_questions.md, audit_report.md",
            max_steps=max_steps,
            max_retries=max_retries,
            artifacts_json={"root_path": path, "question": question},
        )
        artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
        db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
        tr = db.get_task_run(tr["task_id"]) or tr
        if req.run_now:
            out = await task_engine.run(tr["task_id"], max_step_advances=3, max_duration_s=25.0)
            return {"preset": spec.__dict__, **out.to_dict()}
        return {"preset": spec.__dict__, "task": tr, "steps": []}

    if name == "bug_triage":
        path = _require(req, "path")
        issue = _require(req, "issue")
        tr = task_engine.create_task(
            project_id=req.project_id,
            conversation_id=req.conversation_id,
            task_type="issue_triage",
            user_goal=issue,
            definition_of_done="Artifacts written: issue_summary.md, evidence_table.json, likely_causes.md, reproduction_steps.md, next_actions.md",
            max_steps=max_steps,
            max_retries=max_retries,
            artifacts_json={"root_path": path, "issue": issue, "files": req.files, "logs": req.logs},
        )
        artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
        db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
        tr = db.get_task_run(tr["task_id"]) or tr
        if req.run_now:
            out = await task_engine.run(tr["task_id"], max_step_advances=3, max_duration_s=25.0)
            return {"preset": spec.__dict__, **out.to_dict()}
        return {"preset": spec.__dict__, "task": tr, "steps": []}

    if name == "docs_research":
        path = _require(req, "path")
        mode = req.mode or "research"
        question = req.question
        tr = task_engine.create_task(
            project_id=req.project_id,
            conversation_id=req.conversation_id,
            task_type="research",
            user_goal=question or f"Research folder: {path}",
            definition_of_done="Artifacts written: file_index.json, chunk_summaries.json, file_summaries.md, research_brief.md, open_questions.md, final_report.md",
            max_steps=max_steps,
            max_retries=max_retries,
            artifacts_json={"preset": name, "root_path": path, "files": req.files, "question": question, "mode": mode},
        )
        artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
        db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
        tr = db.get_task_run(tr["task_id"]) or tr
        if req.run_now:
            # Keep preset launch responsive; heavy work should happen via bounded /continue calls.
            out = await task_engine.run(tr["task_id"], max_step_advances=1, max_duration_s=10.0)
            return {"preset": spec.__dict__, **out.to_dict()}
        return {"preset": spec.__dict__, "task": tr, "steps": []}

    if name in ("dataset_profile", "dataset_theme_report"):
        path = _require(req, "path")
        # Question is optional; keep a stable operator-friendly default.
        question = req.question or (spec.default_question or "Analyze this JSON dataset in bounded batches.")
        mode = req.mode or "research"
        tr = task_engine.create_task(
            project_id=req.project_id,
            conversation_id=req.conversation_id,
            task_type="research",
            user_goal=question,
            definition_of_done=(
                "Artifacts written (dataset mode): dataset_profile.json, dataset_facts.json, sample_records.json, "
                "batch_summaries.json, dataset_brief.md, themes.json, themes.md, dataset_theme_report.md, final_report.md, open_questions.md"
            ),
            max_steps=max_steps,
            max_retries=max_retries,
            artifacts_json={"preset": name, "root_path": path, "files": req.files, "question": question, "mode": mode},
        )
        artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
        db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
        tr = db.get_task_run(tr["task_id"]) or tr
        if req.run_now:
            # Keep launch responsive; dataset pipelines often need multiple continues.
            out = await task_engine.run(tr["task_id"], max_step_advances=1, max_duration_s=10.0)
            return {"preset": spec.__dict__, **out.to_dict()}
        return {"preset": spec.__dict__, "task": tr, "steps": []}

    if name == "structured_memo":
        path = _require(req, "path")
        goal = req.goal or (spec.default_goal or "Write a short structured memo with clear section headings and bullet points.")
        tr = task_engine.create_task(
            project_id=req.project_id,
            conversation_id=req.conversation_id,
            task_type="writing",
            user_goal=goal,
            definition_of_done="Artifacts written: source_index.json, outline.md, draft.md, final_output.md",
            max_steps=max_steps,
            max_retries=max_retries,
            artifacts_json={"root_path": path, "goal": goal, "files": req.files},
        )
        artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
        db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
        tr = db.get_task_run(tr["task_id"]) or tr
        if req.run_now:
            out = await task_engine.run(tr["task_id"], max_step_advances=3, max_duration_s=25.0)
            return {"preset": spec.__dict__, **out.to_dict()}
        return {"preset": spec.__dict__, "task": tr, "steps": []}

    if name == "release_review":
        path = req.path or "."
        out_path = req.output_path or default_release_review_output_path()
        question = req.goal or (
            "Perform a release readiness review of this repository.\n"
            f"- Scope: {path}\n"
            "- Identify entry points and request flow.\n"
            "- Summarize operational risks and unknowns.\n"
            "- Provide a minimal validation checklist (tests/lint) where applicable.\n"
            "Keep the report grounded in the repo contents and operator-friendly."
        )
        tr = task_engine.create_task(
            project_id=req.project_id,
            conversation_id=req.conversation_id,
            task_type="repo_audit",
            user_goal=question,
            definition_of_done=f"Repo audit completed and export queued to {out_path} (approval-gated write).",
            max_steps=max_steps,
            max_retries=max_retries,
            artifacts_json={"preset": "release_review", "root_path": path, "question": question, "output_path": out_path},
        )
        artifact_dir = str(Path(".runtime") / "tasks" / tr["task_id"])
        db.update_task_run(tr["task_id"], {"artifacts_json": {**(tr.get("artifacts_json") or {}), "artifact_dir": artifact_dir}})
        tr = db.get_task_run(tr["task_id"]) or tr
        if req.run_now:
            out = await task_engine.run(tr["task_id"], max_step_advances=6, max_duration_s=50.0)
            return {"preset": spec.__dict__, **out.to_dict()}
        return {"preset": spec.__dict__, "task": tr, "steps": []}

    raise HTTPException(status_code=500, detail="Preset handler missing")


def _require(req: PresetRunRequest, field: str) -> str:
    v = getattr(req, field, None)
    if not v or not str(v).strip():
        raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
    return str(v)


@app.post("/v1/presets/{preset_name}/run")
async def presets_run(
    preset_name: str,
    req: PresetRunRequest,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    out = await _run_preset_internal(preset_name, req)
    # If the caller requested creation-only, keep 201 for consistency.
    if not req.run_now:
        return JSONResponse(status_code=201, content=out)
    return JSONResponse(content=out)


@app.post("/v1/presets/packs/{pack_name}/run")
async def preset_packs_run(
    pack_name: str,
    req: PresetRunRequest,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if not task_engine:
        raise HTTPException(status_code=503, detail="Task engine not initialized")

    name = (pack_name or "").strip()
    pack = PACKS.get(name)
    if not pack:
        raise HTTPException(status_code=404, detail="Unknown preset pack")

    root_path = req.path or "."
    runs: List[Dict[str, Any]] = []
    prev_artifact_dir: Optional[str] = None

    # Bounded pack orchestration: for each preset in the pack, attempt to run
    # enough for primary artifacts to exist, but do not allow unbounded looping.
    for preset in pack.presets:
        derived = req.model_copy(deep=True)
        if preset in ("structured_memo",) and prev_artifact_dir:
            derived.path = prev_artifact_dir
        else:
            derived.path = root_path

        if preset == "structured_memo" and not (derived.goal or "").strip():
            derived.goal = (
                "Write a short structured memo with clear section headings and bullet points.\n"
                "Ground the memo strictly in the provided sources/artifacts.\n"
                "- Do not mention code paths or components that are not explicitly present in the sources.\n"
                "- If information is missing, say 'Unknown' rather than guessing.\n"
                "- Do not include TO/FROM/DATE/SUBJECT headers or bracket placeholders."
            )

        # For bug triage, prefer req.issue if present; otherwise surface a clear error.
        if preset == "bug_triage":
            derived.issue = derived.issue or req.issue

        out = await _run_preset_internal(preset, derived)
        runs.append(out)

        task = out.get("task") or {}
        task_id = task.get("task_id")
        # Tag tasks created via pack runs so dashboards/clients can filter by pack
        # without any new persistence surface. This is additive metadata only.
        if task_id:
            tr_live = db.get_task_run(task_id)
            if tr_live:
                aj = tr_live.get("artifacts_json") or {}
                if aj.get("pack_name") != pack.name:
                    aj = {**aj, "pack_name": pack.name, "pack_preset": preset}
                    db.update_task_run(task_id, {"artifacts_json": aj})
                    # Keep response in sync for clients that rely on the immediate payload.
                    tr_live = db.get_task_run(task_id) or tr_live
                    task = tr_live
                    runs[-1]["task"] = tr_live

        artifacts = (task.get("artifacts_json") or {})
        prev_artifact_dir = artifacts.get("artifact_dir") or prev_artifact_dir

        # Stop early on approval/continuation signals so operator can proceed safely.
        if out.get("action_required"):
            return JSONResponse(
                content={
                    "pack": pack.__dict__,
                    "runs": runs,
                    "action_required": out.get("action_required"),
                    "note": "pack_paused_for_action",
                }
            )

        # If we need additional work before the next preset (e.g., memo wants the audit report),
        # do a couple bounded continues.
        if preset in ("quick_repo_audit", "deep_repo_audit", "docs_research") and task.get("status") == "partial":
            task_id = task.get("task_id")
            if task_id:
                for _ in range(2):
                    cont = await task_engine.run(task_id, max_step_advances=6, max_duration_s=50.0)
                    out2 = {"preset": out.get("preset"), **cont.to_dict()}
                    runs[-1] = out2
                    task = out2.get("task") or task
                    if (task.get("status") or "") in ("completed", "paused", "failed"):
                        break

    return JSONResponse(content={"pack": pack.__dict__, "runs": runs})


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
