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
from .llm_client import get_llm_client, LLMClient
from .tools import ToolRegistry
from .tool_executor import ToolExecutor
from .approval import ApprovalPolicy
from .workspace_state import WorkspaceTracker
from .reflection import ReflectionLayer
from .routing import determine_execution_route, AGENT_PATH, FAST_PATH
from .multi_agent import OrchestratorAgent
from .search_backend import SearchBackend
from .web_fetcher import WebFetcher
from .content_extractor import ContentExtractor
from .source_store import SourceStore
from .vector_store import VectorStore
from .document_processor import DocumentProcessor
from .web_research import WebResearcher
from .model_profiles import detect_and_log_profile
from .task_engine import TaskEngine
from .research_mode import ResearchPipeline
from .repo_audit import RepoAuditPipeline
from .issue_triage import IssueTriagePipeline
from .writing_profile import WritingPipeline
from .task_metadata import task_meta
from .artifact_registry import task_conventions
from .presets import PRESETS, PACKS, list_presets as list_presets_specs, list_preset_packs as list_preset_packs_specs

# Configuration
load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_CHAT_PATH = os.getenv("OLLAMA_CHAT_PATH", "/api/chat")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemma4:12b-mlx")
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

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logger.info("MacAgent Proxy v2.0.0 starting up...")
    detect_and_log_profile()
    logger.info(f"Ollama: {OLLAMA_BASE_URL}")
    yield
    # Shutdown logic
    logger.info("StarAgent Proxy shutting down...")
    await _http_client.aclose()

# Initialize FastAPI
app = FastAPI(
    title="MacAgent Proxy",
    version="2.0.0",
    description="Production-ready local memory proxy for Open WebUI + Ollama",
    lifespan=lifespan
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
if embedding_model:
    store.set_embedding_model(embedding_model)

compactor: MemoryCompactor = MemoryCompactor(default_model=None)
token_counter: TokenCounter = TokenCounter()


def get_default_model() -> str:
    from .model_registry import get_effective_model_config
    return get_effective_model_config()["model"]


# Projects storage (in-memory for now; can be extended to DB)
_projects: Dict[str, ProjectInfo] = {}

# Initialize Agent System
_http_client = httpx.AsyncClient(timeout=900.0)
llm_client = get_llm_client(_http_client)
# Keep ollama_client for backward compatibility in variable names
ollama_client = llm_client 

planner = Planner(llm_client=llm_client)

# Initialize research components
vector_store = VectorStore(embedding_model)
source_store = SourceStore(vector_store=vector_store)
document_processor = DocumentProcessor(vector_store)
search_backend = SearchBackend()
web_fetcher = WebFetcher()
content_extractor = ContentExtractor()
web_researcher = WebResearcher(llm_client, source_store=source_store)

tool_registry = ToolRegistry(
    search_backend=search_backend, 
    web_researcher=web_researcher, 
    web_extractor=content_extractor,
    source_store=source_store,
    document_processor=document_processor
)
tool_executor = ToolExecutor(registry=tool_registry)
approval_policy = ApprovalPolicy()
reflection_layer = ReflectionLayer(llm_client=llm_client)
executor = Executor(
    llm_client=llm_client, 
    tool_executor=tool_executor, 
    approval_policy=approval_policy,
    reflection_layer=reflection_layer
)
agent_loop = AgentLoop(planner=planner, executor=executor)
task_engine = TaskEngine(
    db=db,
    store=store,
    planner=planner,
    executor=executor,
    workspace=WorkspaceTracker(),
    approval_policy=approval_policy,
    research=ResearchPipeline(llm_client),
    repo_audit=RepoAuditPipeline(llm_client),
    issue_triage=IssueTriagePipeline(llm_client),
    writing=WritingPipeline(llm_client),
)

from .stage_engine import StageEngine
from .workflow_engine import WorkflowEngine
from .statuses import COMPLETED
stage_engine = StageEngine(llm=llm_client, executor=executor, db=db)
workflow_engine = WorkflowEngine(db=db, stage_engine=stage_engine)




# ============================================================================
# Health & Info Endpoints
# ============================================================================

@app.get("/v1/search")
async def api_web_search(q: str, max_results: int = 5, project_id: str = "default"):
    results = await search_backend.search(q, max_results=max_results)
    source_store.log_search(project_id, q, search_backend.backend_type, results)
    return {"results": results}

@app.post("/v1/web/fetch")
async def api_web_fetch(url: str):
    status, content_type, final_url, length = await web_fetcher.fetch(url)
    return {
        "status": status,
        "content_type": content_type,
        "final_url": final_url,
        "length": length
    }

@app.post("/v1/web/extract")
async def api_web_extract(url: str, project_id: str = "default"):
    html = source_store.get_cached_html(url)
    if not html:
        html = await web_fetcher.fetch_full(url)
        if html:
            source_store.set_cached_html(url, html)
    
    if not html:
        raise HTTPException(status_code=404, detail="Failed to fetch content")
        
    extracted = content_extractor.extract(html, url)
    await source_store.save_source(project_id, url, extracted["title"], extracted["text"])
    return extracted

@app.post("/v1/web/research")
async def api_web_research(query: str, max_results: int = 5, max_sources: int = 3, project_id: str = "default"):
    result = await web_researcher.perform_research(
        project_id=project_id,
        query=query,
        max_results=max_results,
        max_sources=max_sources
    )
    return result

@app.get("/v1/sources/search")
async def api_sources_search(q: str, project_id: str = "default"):
    results = source_store.search_sources(project_id, q)
    return {"results": results}

@app.get("/v1/sources/semantic_search")
async def api_sources_semantic_search(q: str, limit: int = 5):
    results = await source_store.semantic_search(q, limit=limit)
    return {"results": results}

@app.post("/v1/documents/index_file")
async def api_index_file(path: str, project_id: str = "default"):
    success = await document_processor.index_file(path, project_id)
    return {"success": success, "path": path}

@app.post("/v1/documents/index_folder")
async def api_index_folder(path: str, project_id: str = "default"):
    result = await document_processor.index_folder(path, project_id)
    return result

# ============================================================================
# Local Code Documentation Knowledge Base Endpoints
# ============================================================================

@app.post("/v1/docs/ingest")
async def api_docs_ingest(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    body = await request.json()
    path = body.get("path")
    project_id = body.get("project_id", "default")
    source_type = body.get("source_type", "project_docs")
    if not path:
        raise HTTPException(status_code=400, detail="Missing path")
    
    if hasattr(tool_registry, "docs_ingester"):
        res = tool_registry.docs_ingester.ingest_folder(project_id, path, source_type)
        return JSONResponse(content=res)
    else:
        raise HTTPException(status_code=500, detail="Docs ingester not initialized")

@app.post("/v1/docs/ingest-package")
async def api_docs_ingest_package(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    body = await request.json()
    package_name = body.get("package_name")
    project_id = body.get("project_id", "default")
    manager = body.get("manager", "pip")
    if not package_name:
        raise HTTPException(status_code=400, detail="Missing package_name")
        
    if hasattr(tool_registry, "docs_ingester"):
        res = tool_registry.docs_ingester.ingest_package(project_id, package_name, manager)
        return JSONResponse(content=res)
    else:
        raise HTTPException(status_code=500, detail="Docs ingester not initialized")

@app.post("/v1/docs/search")
async def api_docs_search_post(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if not hasattr(tool_registry, "docs_searcher"):
        raise HTTPException(status_code=500, detail="Docs searcher not initialized")

    body = await request.json()
    query = body.get("query") or body.get("q")
    project_id = body.get("project_id", "default")
    package_name = body.get("package_name")
    max_results = int(body.get("max_results", 5))

    if not query:
        raise HTTPException(status_code=400, detail="Missing query")

    results = tool_registry.docs_searcher.search_structured(
        project_id=project_id,
        query=query,
        package_name=package_name,
        max_results=max_results,
    )
    return JSONResponse(content={"results": results, "evidence_count": len(results)})


@app.get("/v1/docs/search")
async def api_docs_search_get(
    q: str,
    package_name: Optional[str] = None,
    project_id: str = "default",
    max_results: int = 5,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Backward-compatible search endpoint; prefer POST /v1/docs/search."""
    _validate_api_key(authorization)
    if not hasattr(tool_registry, "docs_searcher"):
        raise HTTPException(status_code=500, detail="Docs searcher not initialized")

    results = tool_registry.docs_searcher.search_structured(
        project_id=project_id,
        query=q,
        package_name=package_name,
        max_results=max_results,
    )
    return JSONResponse(content={"results": results, "evidence_count": len(results)})


@app.post("/v1/docs/ask")
async def api_docs_ask(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if not hasattr(tool_registry, "docs_searcher"):
        raise HTTPException(status_code=500, detail="Docs searcher not initialized")

    body = await request.json()
    question = body.get("question")
    project_id = body.get("project_id", "default")
    package_name = body.get("package_name")
    max_results = int(body.get("max_results", 5))

    if not question:
        raise HTTPException(status_code=400, detail="Missing question")

    payload = tool_registry.docs_searcher.ask(
        project_id=project_id,
        question=question,
        package_name=package_name,
        max_results=max_results,
    )
    return JSONResponse(content=payload)

@app.get("/health")
async def health() -> Dict[str, Any]:
    """Health check endpoint."""
    from .model_registry import registry
    return {
        "ok": True,
        "service": "macagent-proxy",
        "version": "2.0.0",
        "ollama_base_url": OLLAMA_BASE_URL,
        "default_model": registry.global_default,
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
    """List available models."""
    return {
        "object": "list",
        "data": [
            {
                "id": get_default_model(),
                "object": "model",
                "created": int(time.time()),
                "owned_by": "local",
            }
        ],
    }


# ============================================================================
# Trace & Eval Dashboard Endpoints
# ============================================================================

@app.get("/v1/traces")
async def get_traces():
    """List all available trace IDs."""
    from .trace_logger import list_traces
    traces = list_traces()
    return {"traces": traces, "count": len(traces)}

@app.get("/v1/traces/{task_id}")
async def get_trace(task_id: str):
    """Get structured trace events for a task."""
    from .trace_logger import load_trace
    events = load_trace(task_id)
    if not events:
        raise HTTPException(status_code=404, detail=f"No trace found for {task_id}")
    # Build dashboard-friendly summary
    roles = list(set(e.get("role", "") for e in events))
    files_created = [e.get("output_preview", "")[:80] for e in events if e.get("tool_name") == "write_file" and e.get("event_type") == "result"]
    commands_run = [e.get("output_preview", "")[:120] for e in events if e.get("tool_name") == "run_command" and e.get("event_type") == "result"]
    verifier_events = [e for e in events if e.get("event_type") == "verifier"]
    return {
        "task_id": task_id,
        "total_events": len(events),
        "roles": roles,
        "files_created": files_created,
        "commands_run": commands_run,
        "verifier_result": verifier_events[-1] if verifier_events else None,
        "timeline": events,
    }


# ============================================================================
# Multi-Agent Orchestration Endpoint
# ============================================================================

@app.post("/v1/multi-agent/run")
async def multi_agent_run(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _validate_api_key(authorization)
    body = await request.json()
    task = body.get("task", "")
    project_id = body.get("project_id", "default")
    conversation_id = body.get("conversation_id", "default")
    stream = body.get("stream", False)

    if not task:
        raise HTTPException(status_code=400, detail="Missing 'task' field")

    if stream:
        return StreamingResponse(
            _stream_multi_agent(task, project_id, conversation_id),
            media_type="text/event-stream"
        )
    else:
        try:
            workflow_name = "feature_build"
            wf = workflow_engine.inspect_workflow(workflow_name)
            if not wf:
                raise HTTPException(status_code=404, detail=f"Workflow '{workflow_name}' not found.")
                
            task_id = str(uuid.uuid4())[:8]
            art = {
                "workflow_name": workflow_name,
                "current_stage_index": 0,
                "variables": {
                    "project_id": project_id,
                    "docs_context": ""
                }
            }
            
            db.create_task_run({
                "task_id": task_id,
                "project_id": project_id,
                "conversation_id": conversation_id,
                "task_type": "workflow",
                "user_goal": task,
                "definition_of_done": "Complete feature_build workflow",
                "max_steps": len(wf.get("stages") or []),
                "max_retries": 1,
                "artifacts_json": art
            })
            
            await workflow_engine.execute_workflow(task_id)
            
            from .checkpoint import list_task_checkpoints
            checkpoints = list_task_checkpoints(task_id)
            all_artifacts = []
            for cp in checkpoints:
                all_artifacts.extend(cp.get("files_produced") or [])
                
            final_tr = db.get_task_run(task_id) or {}
            final_status = final_tr.get("status", "completed")
            final_summary = final_tr.get("final_summary", "")
            trace_file_path = os.path.join(".runtime", "traces", f"{task_id}.jsonl")
            
            return JSONResponse(content={
                "status": final_status,
                "message": final_summary,
                "artifacts": all_artifacts,
                "subtask_count": len(wf.get("stages") or []),
                "task_id": task_id,
                "trace_file": trace_file_path,
            })
        except Exception as e:
            logger.error(f"Error in multi-agent run: {e}", exc_info=True)
            return JSONResponse(
                status_code=200,
                content={
                    "status": "failed",
                    "message": f"Internal server error: {str(e)}",
                    "artifacts": [],
                    "subtask_count": 0,
                    "task_id": "failed",
                    "trace_file": "",
                }
            )


async def _stream_multi_agent(task: str, project_id: str, conversation_id: str):
    """Stream multi-agent orchestration events via SSE."""
    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    try:
        queue = asyncio.Queue()
        workflow_name = "feature_build"
        wf = workflow_engine.inspect_workflow(workflow_name)
        if not wf:
            err_event = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "choices": [{"delta": {"content": f"[WORKFLOW] [ERROR] Workflow '{workflow_name}' not found.\n"}}]
            }
            yield f"data: {json.dumps(err_event)}\n\n"
            return
            
        task_id = str(uuid.uuid4())[:8]
        art = {
            "workflow_name": workflow_name,
            "current_stage_index": 0,
            "variables": {
                "project_id": project_id,
                "docs_context": ""
            }
        }
        
        db.create_task_run({
            "task_id": task_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "task_type": "workflow",
            "user_goal": task,
            "definition_of_done": "Complete feature_build workflow",
            "max_steps": len(wf.get("stages") or []),
            "max_retries": 1,
            "artifacts_json": art
        })

        async def run_workflow_task():
            try:
                result = await workflow_engine.execute_workflow(task_id, progress_queue=queue)
                await queue.put({"result": result})
            except Exception as e:
                logger.error(f"Workflow execution failed: {e}", exc_info=True)
                await queue.put({"error": str(e)})

        asyncio.create_task(run_workflow_task())

        while True:
            item = await queue.get()
            if isinstance(item, str):
                event = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": item}}]
                }
                yield f"data: {json.dumps(event)}\n\n"
            elif isinstance(item, dict):
                if "error" in item:
                    err_event = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"delta": {"content": f"[WORKFLOW] [ERROR] {item['error']}\n"}}],
                        "x_agent_status": "failed",
                    }
                    yield f"data: {json.dumps(err_event)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
                elif "result" in item:
                    task_result = item["result"]
                    status = task_result.get("status", "completed")
                    
                    from .checkpoint import list_task_checkpoints
                    checkpoints = list_task_checkpoints(task_id)
                    all_artifacts = []
                    for cp in checkpoints:
                        all_artifacts.extend(cp.get("files_produced") or [])
                    
                    finish_event = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                        "x_agent_status": status,
                        "x_trace_id": task_id,
                    }
                    yield f"data: {json.dumps(finish_event)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
    except Exception as e:
        logger.error(f"Error in streaming multi-agent run: {e}", exc_info=True)
        err_event = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "choices": [{"delta": {"content": f"[STREAM_ERROR] {str(e)}\n"}}],
            "x_agent_status": "failed",
        }
        yield f"data: {json.dumps(err_event)}\n\n"
        yield "data: [DONE]\n\n"


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
    
    # Derive identity and context for routing
    project_id = request.project_id or "default"
    conversation_id = request.conversation_id or request.user or _derive_conversation_id(request)
    memory = store.load(conversation_id, project_id)
    
    user_messages = [m for m in request.messages if m.role == "user"]
    latest_user_text = _content_to_text(user_messages[-1].content if user_messages else "")

    # Execute agent path if routed
    route = await determine_execution_route(request, memory, ollama_client=ollama_client)
    if route == AGENT_PATH:
        logger.info(f"[{project_id}:{conversation_id}] Routing to AGENT_PATH: {latest_user_text[:50]}...")
        
        if request.stream and USE_STREAMING:
            return StreamingResponse(
                _stream_agent_run(request, memory, project_id, conversation_id, latest_user_text),
                media_type="text/event-stream"
            )
        
        # FIX: Ensure memory knows about the CURRENT turn before the agent runs!
        # This prevents the 'One-Turn Lag' in history-aware planning.
        virtual_memory = memory  # For safety, we work on a context-enriched view
        
        # Clear stale approval state if this is a fresh research request, not a 'yes/ok'
        if latest_user_text.lower() not in {"yes", "y", "approve", "approved", "ok", "continue", "c", "resume"}:
            memory.pending_approval = None
            memory.pending_plan = None
            memory.pending_goal = latest_user_text
            
        try:
            agent_result = await agent_loop.run(
                latest_user_text, 
                memory, 
                WorkspaceTracker()
            )
        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")
        
        # Persist agent state in memory for continue/approve turns
        status = agent_result.get("status")
        assistant_text = agent_result.get("message", "")
        
        # Record the turn in memory for persistence!
        if ENABLE_MEMORY_UPDATE:
            memory = await store.append_turn(memory, latest_user_text, assistant_text)
            # Save immediately to DB
            store.save(memory)

        if status == "approval_required":
            memory.pending_approval = {
                "tool_call": agent_result.get("tool_call"),
                "plan_remaining": agent_result.get("plan_remaining"),
                "history": agent_result.get("history"),
                "goal": agent_result.get("goal")
            }
        elif status == "partial":
            memory.pending_plan = agent_result.get("plan_remaining")
            memory.pending_history = agent_result.get("history")
            memory.pending_goal = latest_user_text
        else:
            memory.pending_approval = None
            memory.pending_plan = None
            
        store.save(memory)
        
        # Agent results are currently returned as non-streaming completions.
        response = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model or get_default_model(),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": agent_result.get("message", "")},
                "finish_reason": "stop" if status == "completed" else "length",
            }],
            "x_agent_status": status,
            "x_memory": {
                "conversation_id": conversation_id,
                "project_id": project_id,
                "agent_status": status
            }
        }
        return JSONResponse(content=response)

    # Use streaming if requested and enabled
    if request.stream and USE_STREAMING:
        return StreamingResponse(
            _stream_chat_completions(request),
            media_type="text/event-stream"
        )
    else:
        res = await _chat_completions_non_streaming(request, memory, project_id, conversation_id)
        if ENABLE_MEMORY_UPDATE:
            # Re-fetch assistant text from response body to update memory
            try:
                data = json.loads(res.body)
                assistant_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                await store.append_turn(memory, latest_user_text, assistant_text)
                store.save(memory)
                logger.info(f"[{project_id}:{conversation_id}] Persisted fast-path turn to memory.")
            except Exception as e:
                logger.error(f"Failed to record memory: {e}")
        return res


async def _stream_agent_run(request: ChatCompletionRequest, memory: MemoryState, project_id: str, conversation_id: str, latest_user_text: str):
    """Stream agent execution logs via SSE."""
    queue = asyncio.Queue()
    
    if latest_user_text.lower() not in {"yes", "y", "approve", "approved", "ok", "continue", "c", "resume"}:
        memory.pending_approval = None
        memory.pending_plan = None
        memory.pending_goal = latest_user_text

    async def run_agent():
        try:
            res = await agent_loop.run(latest_user_text, memory, WorkspaceTracker(), stream_queue=queue)
            await queue.put({"result": res})
        except Exception as e:
            logger.error(f"Agent execution failed: {e}", exc_info=True)
            await queue.put({"error": str(e)})
            
    asyncio.create_task(run_agent())
    
    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    
    try:
        while True:
            item = await queue.get()
            if isinstance(item, str):
                event = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"content": item}}]
                }
                yield f"data: {json.dumps(event)}\n\n"
            elif isinstance(item, dict):
                if "error" in item:
                    err_event = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"delta": {"content": f"[AGENT_ERROR] {item['error']}\n"}}],
                        "x_agent_status": "failed",
                    }
                    yield f"data: {json.dumps(err_event)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
                elif "result" in item:
                    agent_result = item["result"]
                    status = agent_result.get("status")
                    assistant_text = agent_result.get("message", "")
                    
                    if ENABLE_MEMORY_UPDATE:
                        await store.append_turn(memory, latest_user_text, assistant_text)
                    
                    if status == "approval_required":
                        memory.pending_approval = {
                            "tool_call": agent_result.get("tool_call"),
                            "plan_remaining": agent_result.get("plan_remaining"),
                            "history": agent_result.get("history"),
                            "goal": agent_result.get("goal")
                        }
                    elif status == "partial":
                        memory.pending_plan = agent_result.get("plan_remaining")
                        memory.pending_history = agent_result.get("history")
                        memory.pending_goal = latest_user_text
                    else:
                        memory.pending_approval = None
                        memory.pending_plan = None
                    store.save(memory)
                    
                    # Yield final summary
                    if assistant_text:
                        event = {
                            "id": request_id,
                            "object": "chat.completion.chunk",
                            "choices": [{"delta": {"content": "\n\n=== Final Report ===\n\n" + assistant_text}}]
                        }
                        yield f"data: {json.dumps(event)}\n\n"
                    
                    # Yield finish
                    finish_event = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                        "x_agent_status": status
                    }
                    yield f"data: {json.dumps(finish_event)}\n\n"
                    yield "data: [DONE]\n\n"
                    break
    except Exception as e:
        logger.error(f"Error in streaming agent run: {e}", exc_info=True)
        err_event = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "choices": [{"delta": {"content": f"[STREAM_ERROR] {str(e)}\n"}}],
            "x_agent_status": "failed",
        }
        yield f"data: {json.dumps(err_event)}\n\n"
        yield "data: [DONE]\n\n"

async def _chat_completions_non_streaming(
    request: ChatCompletionRequest,
    memory: Optional[MemoryState] = None,
    project_id: str = "default",
    conversation_id: str = "default"
) -> JSONResponse:
    """Non-streaming completion shim."""
    if memory is None:
        memory = store.load(conversation_id, project_id)
    
    logger.info(f"[{project_id}:{conversation_id}] Non-streaming request")
    
    # Load memory and retrieve context
    user_messages = [m for m in request.messages if m.role == "user"]
    latest_user_text = _content_to_text(user_messages[-1].content if user_messages else "")
    
    # Semantic retrieval
    retrieved_items = []
    # Dual-Layer Semantic Retrieval: 
    # 1. Local items (this chat only)
    # 2. Global items (Decisions/Constraints/Style across the whole project)
    retrieved_items = []
    if USE_SEMANTIC_SEARCH:
        # Fetch current conversation's full context
        local_items = db.get_memory_items(conversation_id, project_id)
        
        # Fetch project-wide knowledge (Categories that should be shared)
        global_items = []
        for cat in ["decision", "constraint", "style_preferences"]:
            global_items.extend(db.get_memory_items(None, project_id, category=cat))
        
        all_sources = local_items + global_items
        
        retrieved_items = await retrieve_context(
            latest_user_text,
            all_sources,
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
            "model": request.model or get_default_model(),
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
        memory = await store.append_turn(memory, latest_user_text, assistant_text)
        
        # Check if compaction needed
        if memory.turn_count % COMPACTION_INTERVAL == 0 and memory.turn_count > 0:
            asyncio.create_task(_compact_memory_async(conversation_id, project_id, memory))
    
    # Response
    response = {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model or get_default_model(),
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
    
    # Dual-Layer Semantic Retrieval
    retrieved_items = []
    if USE_SEMANTIC_SEARCH:
        local_items = db.get_memory_items(conversation_id, project_id)
        global_items = []
        for cat in ["decision", "constraint", "style_preferences"]:
            global_items.extend(db.get_memory_items(None, project_id, category=cat))
        
        all_sources = local_items + global_items
        
        retrieved_items = await retrieve_context(
            latest_user_text,
            all_sources,
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
                    "model": request.model or get_default_model(),
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
                                "model": request.model or get_default_model(),
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
            "model": request.model or get_default_model(),
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
            memory = await store.append_turn(memory, latest_user_text, full_response)
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
# Task & Research Implementation (MCP Compatibility)
# ============================================================================

def _new_task_id() -> str:
    return f"task-{uuid.uuid4().hex[:8]}"


def _task_or_404(task_id: str) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    tr = db.get_task_run(task_id)
    if not tr:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    steps = db.list_task_steps(task_id)
    return tr, steps


def _task_artifact_dir(task_run: Dict[str, Any]) -> Path:
    task_id = str(task_run.get("task_id") or "")
    aj = task_run.get("artifacts_json") or {}
    return Path(str(aj.get("artifact_dir") or (Path(".runtime") / "tasks" / task_id))).resolve()


def _read_text(path: Path, *, max_bytes: int = 2_000_000) -> Optional[str]:
    try:
        if not path.exists() or not path.is_file():
            return None
        if int(path.stat().st_size) > max_bytes:
            return None
        return path.read_text(encoding="utf-8")
    except Exception:
        try:
            return path.read_text(errors="ignore")
        except Exception:
            return None


def _artifact_listing(task_run: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(task_run.get("task_id") or "")
    task_dir = _task_artifact_dir(task_run)
    conv = task_conventions(task_run.get("task_type"), artifacts_json=(task_run.get("artifacts_json") or {}))
    primary_name = conv.primary
    primary_path = (task_dir / primary_name).resolve() if primary_name else None
    primary_exists = bool(primary_path and primary_path.exists() and primary_path.is_file())

    files: List[Dict[str, Any]] = []
    if task_dir.exists():
        for p in sorted(task_dir.glob("*")):
            if not p.is_file():
                continue
            suffix = p.suffix.lower().lstrip(".")
            ftype = suffix if suffix else "bin"
            try:
                size = int(p.stat().st_size)
            except Exception:
                size = None
            files.append(
                {
                    "name": p.name,
                    "path": str(p),
                    "type": ftype,
                    "size_bytes": size,
                    "is_primary": bool(primary_name and p.name == primary_name),
                }
            )

    out: Dict[str, Any] = {
        "task_id": task_id,
        "artifact_dir": str(task_dir),
        "files": files,
    }
    if primary_name:
        out["primary_artifact"] = {
            "name": primary_name,
            "exists": primary_exists,
            "path": str(primary_path) if primary_path else None,
            "preview_url": f"/v1/tasks/{task_id}/artifacts/{primary_name}" if primary_exists else None,
        }
    return out


def _task_payload(task_id: str) -> Dict[str, Any]:
    tr, steps = _task_or_404(task_id)
    meta = task_meta(tr, steps)
    aj = tr.get("artifacts_json") or {}
    snapshot = aj.get("task_result") if isinstance(aj.get("task_result"), dict) else None
    artifacts = _artifact_listing(tr)
    primary = artifacts.get("primary_artifact") or {}
    primary_report = snapshot.get("primary_report") if snapshot else None
    if primary_report is None and primary.get("exists") and primary.get("path"):
        primary_report = _read_text(Path(str(primary.get("path"))))

    logs = snapshot.get("logs") if snapshot and isinstance(snapshot.get("logs"), list) else steps
    events = snapshot.get("events") if snapshot and isinstance(snapshot.get("events"), list) else steps
    summary = tr.get("final_summary") or (snapshot.get("summary") if snapshot else None)

    return {
        "task_id": task_id,
        "task": tr,
        "progress": meta.get("progress"),
        "time": meta.get("time"),
        "task_meta": meta,
        "primary_artifact": artifacts.get("primary_artifact"),
        "primary_report": primary_report,
        "summary": summary,
        "logs": logs,
        "events": events,
        "artifacts": artifacts.get("files"),
    }


def _normalize_task_input(task_type: str, body: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    tt = (task_type or "agent").strip().lower()
    artifacts_json = dict(body.get("artifacts_json") or {})
    if body.get("path") and not artifacts_json.get("root_path"):
        artifacts_json["root_path"] = body.get("path")
    for k in ("files", "logs", "question", "mode", "issue", "goal", "output_path"):
        if body.get(k) is not None:
            artifacts_json[k] = body.get(k)

    goal = str(body.get("user_goal") or body.get("goal") or "").strip()
    if not goal:
        if tt == "repo_audit":
            goal = f"Audit repository at {artifacts_json.get('root_path') or '.'}"
            if artifacts_json.get("question"):
                goal = f"{goal}: {artifacts_json.get('question')}"
        elif tt == "research":
            goal = f"Research {artifacts_json.get('root_path') or '.'}"
            if artifacts_json.get("question"):
                goal = f"{goal}: {artifacts_json.get('question')}"
        elif tt == "issue_triage":
            goal = f"Issue triage: {artifacts_json.get('issue') or ''}".strip()
        elif tt == "writing":
            goal = artifacts_json.get("goal") or "Generate final written output"
        else:
            goal = "Task run"
    return goal, artifacts_json


async def _create_profile_task(body: Dict[str, Any], *, task_type: str, user_goal: Optional[str] = None) -> Dict[str, Any]:
    project_id = str(body.get("project_id") or "default")
    conversation_id = str(body.get("conversation_id") or "default")
    max_retries = int(body.get("max_retries") or 1)
    run_now = bool(body.get("run_now", True))

    goal, artifacts_json = _normalize_task_input(task_type, body)
    if user_goal:
        goal = user_goal

    # Map task type to workflow name
    workflow_map = {
        "repo_audit": "repo_audit",
        "research": "research",
        "issue_triage": "issue_triage",
        "writing": "refactor",
        "agent": "existing_repo_fix"
    }
    wf_name = workflow_map.get(task_type, "existing_repo_fix")
    wf = workflow_engine.inspect_workflow(wf_name)
    stages = wf.get("stages") if wf else []
    max_steps = len(stages) if stages else 25

    task_id = _new_task_id()
    
    # Store initial state in task run
    art = {
        **(artifacts_json or {}),
        "workflow_name": wf_name,
        "current_stage_index": 0,
        "variables": {
            "project_id": project_id,
            "docs_context": ""
        }
    }

    tr = db.create_task_run({
        "task_id": task_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "task_type": task_type,
        "user_goal": goal,
        "definition_of_done": body.get("definition_of_done"),
        "max_steps": max_steps,
        "max_retries": max_retries,
        "artifacts_json": art,
    })
    if not run_now:
        return {"task_id": task_id, "task": tr}

    result = await workflow_engine.execute_workflow(task_id)
    out = _task_payload(task_id)
    if result.get("final_verdict") == "approval_required":
        out["action_required"] = {"type": "approval", "note": "Stage approval required"}
    return out



@app.post("/v1/tasks")
async def create_task(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    body = await request.json()
    task_type = str(body.get("task_type") or body.get("type") or "agent").strip().lower()
    out = await _create_profile_task(body, task_type=task_type)
    return JSONResponse(content=out)

@app.post("/v1/research/run")
async def research_run(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    body = await request.json()
    out = await _create_profile_task(body, task_type="research")
    return JSONResponse(content=out)

@app.post("/v1/repo_audit/run")
async def repo_audit_run(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    body = await request.json()
    out = await _create_profile_task(body, task_type="repo_audit")
    return JSONResponse(content=out)

@app.post("/v1/issue_triage/run")
async def issue_triage_run(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    body = await request.json()
    out = await _create_profile_task(body, task_type="issue_triage")
    return JSONResponse(content=out)

@app.post("/v1/write/run")
async def write_run(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    body = await request.json()
    out = await _create_profile_task(body, task_type="writing")
    return JSONResponse(content=out)


@app.get("/v1/tasks")
async def get_tasks(
    project_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _validate_api_key(authorization)
    runs = db.list_task_runs(
        project_id=project_id,
        conversation_id=conversation_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    for tr in runs:
        conv = task_conventions(tr.get("task_type"), artifacts_json=(tr.get("artifacts_json") or {}))
        tr["primary_artifact_name"] = conv.primary
    return {"tasks": runs, "count": len(runs), "limit": limit, "offset": offset}

@app.get("/v1/tasks/{task_id}")
async def get_task_status(task_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _validate_api_key(authorization)
    out = _task_payload(task_id)
    return {
        "task_id": task_id,
        "task": out.get("task"),
        "progress": out.get("progress"),
        "time": out.get("time"),
        "task_meta": out.get("task_meta"),
    }


@app.get("/v1/tasks/{task_id}/inspect")
async def get_task_inspect(task_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _validate_api_key(authorization)
    return _task_payload(task_id)

@app.get("/v1/presets")
async def list_presets(authorization: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    _validate_api_key(authorization)
    return list_presets_specs()

@app.post("/v1/presets/{name}/run")
async def run_preset(
    name: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if name not in PRESETS:
        raise HTTPException(status_code=404, detail=f"Unknown preset: {name}")
    body = await request.json()
    spec = PRESETS[name]
    body = dict(body)
    body.setdefault("max_steps", spec.default_max_steps)
    body.setdefault("max_retries", spec.default_max_retries)
    body.setdefault("run_now", True)
    if spec.task_type == "repo_audit" and not body.get("question") and spec.default_question:
        body["question"] = spec.default_question
    if spec.task_type == "issue_triage" and not body.get("issue") and spec.default_issue:
        body["issue"] = spec.default_issue
    if spec.task_type == "writing" and not body.get("goal") and spec.default_goal:
        body["goal"] = spec.default_goal

    out = await _create_profile_task(body, task_type=spec.task_type)
    task = out.get("task") or {}
    aj = dict(task.get("artifacts_json") or {})
    aj["preset"] = name
    if task.get("task_id"):
        db.update_task_run(task.get("task_id"), {"artifacts_json": aj})
        out = _task_payload(str(task.get("task_id")))
    return JSONResponse(content={"preset": {"name": name}, **out})

@app.post("/v1/presets/packs/{name}/run")
async def run_preset_pack(
    name: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _validate_api_key(authorization)
    if name not in PACKS:
        raise HTTPException(status_code=404, detail=f"Unknown preset pack: {name}")
    body = await request.json()
    pack = PACKS[name]
    run_now = bool(body.get("run_now", True))
    runs: List[Dict[str, Any]] = []
    for preset_name in pack.presets:
        spec = PRESETS[preset_name]
        pb = dict(body)
        pb.setdefault("max_steps", spec.default_max_steps)
        pb.setdefault("max_retries", spec.default_max_retries)
        pb["run_now"] = run_now
        out = await _create_profile_task(pb, task_type=spec.task_type)
        task = out.get("task") or {}
        tid = str(task.get("task_id") or out.get("task_id") or "")
        if tid:
            tr = db.get_task_run(tid) or {}
            aj = dict(tr.get("artifacts_json") or {})
            aj["preset"] = preset_name
            aj["pack_name"] = name
            db.update_task_run(tid, {"artifacts_json": aj})
        runs.append({"preset": {"name": preset_name}, "task": task or {"task_id": out.get("task_id")}})
    return JSONResponse(
        content={
            "pack": {"name": pack.name, "description": pack.description},
            "runs": runs,
        }
    )

@app.get("/v1/tasks/{task_id}/logs")
async def get_task_logs(
    task_id: str,
    tail_steps: int = 50,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _validate_api_key(authorization)
    out = _task_payload(task_id)
    logs = out.get("logs")
    if isinstance(logs, list):
        logs_list = logs
    elif isinstance(logs, str):
        logs_list = [logs]
    else:
        logs_list = []
    tail = max(0, int(tail_steps))
    if tail > 0 and len(logs_list) > tail:
        logs_list = logs_list[-tail:]
    return {"task_id": task_id, "logs": logs_list}

@app.get("/v1/tasks/{task_id}/artifacts")
async def get_task_artifacts(task_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _validate_api_key(authorization)
    tr, _steps = _task_or_404(task_id)
    return _artifact_listing(tr)


@app.get("/v1/tasks/{task_id}/artifacts/{artifact_name}")
async def get_task_artifact(
    task_id: str,
    artifact_name: str,
    format: str = "text",
    max_bytes: int = 50_000,
    tail_lines: int = 200,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _validate_api_key(authorization)
    tr, _steps = _task_or_404(task_id)
    task_dir = _task_artifact_dir(tr)
    path = (task_dir / artifact_name).resolve()
    if not str(path).startswith(str(task_dir)):
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_name}")

    raw = path.read_bytes()
    truncated = False
    if max_bytes > 0 and len(raw) > max_bytes:
        raw = raw[-max_bytes:]
        truncated = True

    if format == "json":
        try:
            obj = json.loads(raw.decode("utf-8", errors="ignore"))
            content = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            content = raw.decode("utf-8", errors="ignore")
    else:
        content = raw.decode("utf-8", errors="ignore")
        if tail_lines and tail_lines > 0:
            lines = content.splitlines()
            if len(lines) > tail_lines:
                content = "\n".join(lines[-tail_lines:])
                truncated = True
    return {"task_id": task_id, "artifact_name": artifact_name, "format": format, "content": content, "truncated": truncated}

@app.get("/v1/tasks/{task_id}/summary")
async def get_task_summary(task_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _validate_api_key(authorization)
    out = _task_payload(task_id)
    return {
        "task_id": task_id,
        "task": out.get("task"),
        "progress": out.get("progress"),
        "time": out.get("time"),
        "task_meta": out.get("task_meta"),
        "primary_artifact": out.get("primary_artifact"),
        "primary_report": out.get("primary_report"),
        "summary": out.get("summary"),
    }

@app.post("/v1/tasks/{task_id}/continue")
async def task_continue(
    task_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _validate_api_key(authorization)
    body = await request.json()
    action = str(body.get("action") or "continue").strip().lower()

    tr, _steps = _task_or_404(task_id)
    aj = tr.get("artifacts_json") or {}
    is_workflow = bool(aj.get("workflow_name") or tr.get("task_type") == "workflow")

    if is_workflow:
        if action in {"continue", "approve"}:
            result = await workflow_engine.resume_workflow(task_id)
            out = _task_payload(task_id)
            if result.get("final_verdict") == "approval_required":
                out["action_required"] = {"type": "approval", "note": "Stage approval required"}
            return out
        if action == "reject":
            reason = str(body.get("reason") or "rejected")
            db.update_task_run(
                task_id,
                {
                    "status": "failed",
                    "final_verdict": "rejected",
                    "final_summary": f"Workflow stage rejected: {reason}",
                },
            )
            return _task_payload(task_id)

    if action == "continue":
        result = await task_engine.run(
            task_id,
            max_step_advances=int(body.get("max_step_advances") or 3),
            max_duration_s=float(body.get("max_duration_s") or 20.0),
        )
        out = _task_payload(task_id)
        if result.action_required:
            out["action_required"] = result.action_required
        return out
    if action == "approve":
        result = await task_engine.approve(task_id)
        out = _task_payload(task_id)
        if result.action_required:
            out["action_required"] = result.action_required
        return out
    if action == "reject":
        reason = str(body.get("reason") or "rejected")
        aj_dict = dict(aj)
        pending = aj_dict.pop("pending_approval", None)
        if isinstance(pending, dict) and pending.get("step_id"):
            db.update_task_step(str(pending.get("step_id")), {"status": "failed", "output_summary": f"approval_rejected: {reason}"})
        db.update_task_run(
            task_id,
            {
                "status": "failed",
                "final_verdict": "rejected",
                "final_summary": f"Task rejected: {reason}",
                "artifacts_json": aj_dict,
            },
        )
        return _task_payload(task_id)

    raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")

@app.get("/v1/presets/packs")
async def list_preset_packs(authorization: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    _validate_api_key(authorization)
    return list_preset_packs_specs()

@app.get("/v1/files/tree")
async def get_file_tree(path: str, max_depth: int = 3) -> Dict[str, Any]:
    tree = tool_registry.get_file_tree(path, max_depth)
    return {"path": path, "tree": tree}

@app.get("/v1/memory/search")
async def search_memory(
    query: str, 
    limit: int = 10,
    project_id: str = "default",
    conversation_id: str = "default"
) -> Dict[str, Any]:
    db_items = db.get_memory_items(conversation_id, project_id)
    retrieved = await retrieve_context(query, db_items, top_k=limit, embedding_model=embedding_model)
    return {"query": query, "results": retrieved}

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


# ============================================================================
# Workflow Engine Endpoints
# ============================================================================

@app.get("/v1/workflows")
async def get_workflows(authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    return {"workflows": workflow_engine.list_workflows()}

@app.get("/v1/workflows/{name}")
async def inspect_workflow(name: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    wf = workflow_engine.inspect_workflow(name)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{name}' not found.")
    return wf

@app.get("/v1/workflows/{name}/graph")
async def get_workflow_graph(name: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    graph = workflow_engine.get_workflow_graph(name)
    return {"graph": graph}

@app.post("/v1/workflows/run")
async def run_workflow(request: Request, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    body = await request.json()
    workflow_name = body.get("name")
    project_id = body.get("project_id", "default")
    conversation_id = body.get("conversation_id", "default")
    goal = body.get("goal", "Execute workflow")
    
    # Verify workflow exists
    wf = workflow_engine.inspect_workflow(workflow_name)
    if not wf:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_name}' not found.")
        
    task_id = str(uuid.uuid4())[:8] # Short unique ID
    
    # Store initial state in task run
    art = {
        "workflow_name": workflow_name,
        "current_stage_index": 0,
        "variables": {
            "project_id": project_id,
            "docs_context": "",
            "mode": body.get("mode", "test"),  # Default to test for safety/backward compatibility
            "urls": body.get("urls", []),
            "docs": body.get("docs", False),
            "question": goal
        }
    }
    
    tr = db.create_task_run({
        "task_id": task_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "task_type": "workflow",
        "user_goal": goal,
        "definition_of_done": body.get("definition_of_done"),
        "max_steps": len(wf.get("stages") or []),
        "max_retries": 1,
        "artifacts_json": art
    })
    
    result = await workflow_engine.execute_workflow(task_id)
    return JSONResponse(content={"task_id": task_id, "task": result})

@app.post("/v1/workflows/create")
async def create_custom_workflow(request: Request, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    body = await request.json()
    name = body.get("name")
    description = body.get("description", "")
    try:
        wf = workflow_engine.create_custom_workflow(name, description)
        return {"ok": True, "workflow": wf}
    except FileExistsError as fe:
        raise HTTPException(status_code=400, detail=str(fe))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/workflows/{task_id}/resume")
async def resume_workflow_endpoint(task_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    body = await request.json()
    stage = body.get("stage")
    try:
        result = await workflow_engine.resume_workflow(task_id, force_stage_name=stage)
        return JSONResponse(content={"task_id": task_id, "task": result})
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task run '{task_id}' not found.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/workflows/{task_id}/checkpoints")
async def get_checkpoints(task_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    return {"checkpoints": list_task_checkpoints(task_id)}

@app.get("/v1/workflows/runs")
async def get_workflow_runs(authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    return {"runs": workflow_engine.list_workflow_runs()}

@app.get("/v1/workflows/{run_id}/status")
async def get_workflow_run_status(run_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    tr = db.get_task_run(run_id)
    if not tr:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
        
    wf_dir = Path(".runtime") / "workflows" / run_id
    stages = []
    if (wf_dir / "stage_state.json").exists():
        try:
            stages = json.loads((wf_dir / "stage_state.json").read_text(encoding="utf-8"))
        except Exception:
            pass
            
    art = tr.get("artifacts_json") or {}
    return {
        "run_id": run_id,
        "workflow_name": art.get("workflow_name"),
        "status": tr.get("status"),
        "current_stage_index": art.get("current_stage_index", 0),
        "stages": stages
    }

@app.get("/v1/workflows/{run_id}/trace")
async def get_workflow_run_trace(run_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    wf_dir = Path(".runtime") / "workflows" / run_id
    events = []
    events_file = wf_dir / "tool_events.jsonl"
    if events_file.exists():
        try:
            with open(events_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        events.append(json.loads(line))
        except Exception:
            pass
    return {"events": events}

@app.get("/v1/workflows/{run_id}/state")
async def get_workflow_run_state_endpoint(run_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    wf_dir = Path(".runtime") / "workflows" / run_id
    
    variables = {}
    if (wf_dir / "workflow_state.json").exists():
        try:
            wf_state = json.loads((wf_dir / "workflow_state.json").read_text(encoding="utf-8"))
            variables = wf_state.get("variables", {})
        except Exception:
            pass
            
    contexts = {}
    if (wf_dir / "context_snapshot.json").exists():
        try:
            contexts = json.loads((wf_dir / "context_snapshot.json").read_text(encoding="utf-8"))
        except Exception:
            pass
            
    models = {}
    if (wf_dir / "model_selection.json").exists():
        try:
            models = json.loads((wf_dir / "model_selection.json").read_text(encoding="utf-8"))
        except Exception:
            pass
            
    return {
        "run_id": run_id,
        "variables": variables,
        "context_snapshots": contexts,
        "model_selections": models
    }

@app.get("/v1/workflows/{run_id}/report")
async def get_workflow_report(run_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    wf_dir = Path(".runtime") / "workflows" / run_id
    report_file = wf_dir / "final_report.md"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail=f"Final report for run '{run_id}' not found.")
    try:
        report_content = report_file.read_text(encoding="utf-8")
        return {"report": report_content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read final report: {e}")

@app.get("/v1/workflows/{run_id}/sources")
async def get_workflow_sources(run_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    wf_dir = Path(".runtime") / "workflows" / run_id
    sources_file = wf_dir / "sources.json"
    if not sources_file.exists():
        raise HTTPException(status_code=404, detail=f"Sources for run '{run_id}' not found.")
    try:
        sources_content = json.loads(sources_file.read_text(encoding="utf-8"))
        return {"sources": sources_content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read sources: {e}")

@app.get("/v1/workflows/{run_id}/evidence")
async def get_workflow_evidence(run_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    wf_dir = Path(".runtime") / "workflows" / run_id
    evidence_file = wf_dir / "evidence_items.json"
    if not evidence_file.exists():
        raise HTTPException(status_code=404, detail=f"Evidence for run '{run_id}' not found.")
    try:
        evidence_content = json.loads(evidence_file.read_text(encoding="utf-8"))
        return {"evidence": evidence_content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read evidence: {e}")

@app.get("/v1/workflows/{run_id}/gates")
async def get_workflow_run_gates(run_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    wf_dir = Path(".runtime") / "workflows" / run_id
    gate_results = {}
    if (wf_dir / "gate_results.json").exists():
        try:
            gate_results = json.loads((wf_dir / "gate_results.json").read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"gate_results": gate_results}

@app.post("/v1/workflows/{run_id}/approve")
async def approve_workflow_run(run_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    body = await request.json()
    stage = body.get("stage")
    if not stage:
        tr = db.get_task_run(run_id)
        if tr:
            art = tr.get("artifacts_json") or {}
            wf_name = art.get("workflow_name")
            wf = workflow_engine.inspect_workflow(wf_name)
            if wf:
                stages = wf.get("stages") or []
                idx = art.get("current_stage_index", 0)
                if idx < len(stages):
                    stage = stages[idx]["name"]
    if not stage:
        raise HTTPException(status_code=400, detail="Stage name must be specified or inferred.")
        
    res = workflow_engine.approve_stage(run_id, stage)
    return res

@app.post("/v1/workflows/{run_id}/reject")
async def reject_workflow_run(run_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    body = await request.json()
    stage = body.get("stage")
    if not stage:
        tr = db.get_task_run(run_id)
        if tr:
            art = tr.get("artifacts_json") or {}
            wf_name = art.get("workflow_name")
            wf = workflow_engine.inspect_workflow(wf_name)
            if wf:
                stages = wf.get("stages") or []
                idx = art.get("current_stage_index", 0)
                if idx < len(stages):
                    stage = stages[idx]["name"]
    if not stage:
        raise HTTPException(status_code=400, detail="Stage name must be specified or inferred.")
        
    res = workflow_engine.reject_stage(run_id, stage)
    return res

@app.get("/v1/workflows/{workflow_name}/explain")
async def explain_workflow_endpoint(workflow_name: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    explanation = workflow_engine.explain_workflow(workflow_name)
    return {"explanation": explanation}


# ── v0.6.1 Runtime Hardening ──────────────────────────────────────


def _parse_age(older_than: str) -> int:
    """Parse an age string like ``7d``, ``30d``, ``24h`` into seconds."""
    older_than = (older_than or "7d").strip().lower()
    if older_than.endswith("d"):
        val = older_than[:-1]
        return int(val) * 86400 if val.isdigit() else 7 * 86400
    if older_than.endswith("h"):
        val = older_than[:-1]
        return int(val) * 3600 if val.isdigit() else 7 * 86400
    if older_than.endswith("m"):
        val = older_than[:-1]
        return int(val) * 60 if val.isdigit() else 7 * 86400
    if older_than.isdigit():
        return int(older_than)
    return 7 * 86400  # default 7 days


@app.post("/v1/workflows/cleanup")
async def cleanup_workflow_runs(request: Request, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    body = await request.json()
    older_than = body.get("older_than", "7d")
    dry_run = body.get("dry_run", False)
    max_age_s = _parse_age(older_than)
    now = time.time()

    wf_root = Path(".runtime") / "workflows"
    if not wf_root.exists():
        return {"cleaned_count": 0, "dry_run": dry_run, "candidates": []}

    cleaned = 0
    candidates = []
    for d in wf_root.iterdir():
        if not d.is_dir():
            continue
        # Skip active run (has workflow_state.json with matching current process)
        state_file = d / "workflow_state.json"
        if state_file.exists():
            try:
                mtime = state_file.stat().st_mtime
            except Exception:
                mtime = 0
        else:
            try:
                mtime = d.stat().st_mtime
            except Exception:
                mtime = 0
        if mtime and (now - mtime) > max_age_s:
            candidates.append(d.name)
            if not dry_run:
                import shutil
                try:
                    shutil.rmtree(str(d), ignore_errors=True)
                    logger.info(f"Cleaned up stale workflow run: {d.name}")
                    cleaned += 1
                except Exception as e:
                    logger.warning(f"Failed to clean up {d.name}: {e}")

    result = {"cleaned_count": cleaned, "older_than": older_than, "dry_run": dry_run}
    if dry_run:
        result["candidates"] = candidates
    return result


@app.get("/v1/workflows/{run_id}/doctor")
async def doctor_workflow_run(run_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    wf_dir = Path(".runtime") / "workflows" / run_id
    if not wf_dir.exists():
        return {
            "status": "not_found",
            "state_health": "missing",
            "stages_count": 0,
            "stages_completed": 0,
            "anomalies": [f"Workflow run directory not found: {wf_dir}"],
        }

    anomalies: List[str] = []
    state_health = "ok"
    stages_completed = 0
    stages_count = 0

    # Check state file
    state_file = wf_dir / "workflow_state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if not state.get("run_id"):
                anomalies.append("workflow_state.json missing 'run_id' field")
            if not state.get("workflow_name"):
                anomalies.append("workflow_state.json missing 'workflow_name' field")
        except Exception as e:
            anomalies.append(f"workflow_state.json corrupt: {e}")
            state_health = "corrupt"
    else:
        anomalies.append("workflow_state.json missing")
        state_health = "incomplete"

    # Check stage state
    stage_file = wf_dir / "stage_state.json"
    if stage_file.exists():
        try:
            s_list = json.loads(stage_file.read_text(encoding="utf-8"))
            stages_count = len(s_list)
            stages_completed = sum(1 for s in s_list if s.get("status") == COMPLETED)
        except Exception as e:
            anomalies.append(f"stage_state.json corrupt: {e}")
    else:
        anomalies.append("stage_state.json missing")

    # Check for orphaned checkpoints
    cp_dir = wf_dir / "checkpoints"
    if cp_dir.exists():
        orphaned = [f.name for f in sorted(cp_dir.iterdir()) if f.is_dir() and not (f / "stage_state.json").exists()]
        if orphaned:
            anomalies.append(f"orphaned checkpoint directories: {len(orphaned)}")

    # Check trace dir — traces are written to global .runtime/traces/<run_id>.jsonl,
    # so an empty local traces/ dir is expected when checkpoints/stages exist.
    traces_dir = wf_dir / "traces"
    cp_dir = wf_dir / "checkpoints"
    has_checkpoints = cp_dir.exists() and any(cp_dir.iterdir())
    if not traces_dir.exists() or not any(traces_dir.iterdir()):
        if not has_checkpoints:
            anomalies.append("traces directory missing or empty")

    # Check final report output (deep research workflows)
    report_file = wf_dir / "final_report.md"
    if not report_file.exists():
        # Only flag as anomaly if there are completed stages (work was started)
        if stages_completed > 0:
            anomalies.append("final_report.md missing (workflow may not have completed)")

    return {
        "status": "ok" if not anomalies else "degraded",
        "state_health": state_health,
        "stages_count": stages_count,
        "stages_completed": stages_completed,
        "anomalies": anomalies,
    }


@app.get("/v1/workflows/{run_id}/replay")
async def replay_workflow_run(run_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    wf_dir = Path(".runtime") / "workflows" / run_id
    events: List[Dict[str, Any]] = []

    # Collect tool events from JSONL
    tool_events_file = wf_dir / "tool_events.jsonl"
    if tool_events_file.exists():
        try:
            for line in tool_events_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    ev = json.loads(line)
                    events.append(ev)
        except Exception as e:
            pass

    # If no tool events, try traces directory
    if not events:
        traces_dir = wf_dir / "traces"
        if traces_dir.exists():
            for f in sorted(traces_dir.iterdir()):
                if f.suffix in (".json", ".jsonl"):
                    try:
                        ev = json.loads(f.read_text(encoding="utf-8"))
                        if isinstance(ev, list):
                            events.extend(ev)
                        else:
                            events.append(ev)
                    except Exception:
                        pass

    # Check stage_state for context and ordered stage list
    stage_file = wf_dir / "stage_state.json"
    total_stages = 0
    stages = []
    if stage_file.exists():
        try:
            s_list = json.loads(stage_file.read_text(encoding="utf-8"))
            total_stages = len(s_list)
            stages = s_list
        except Exception:
            pass

    return {
        "run_id": run_id,
        "total_events": len(events),
        "total_stages": total_stages,
        "stages": stages,
        "events": events[:500],  # cap to avoid oversized response
    }


# =============================================================================
# v0.6.3 — Research Benchmark Endpoints
# =============================================================================

@app.get("/v1/benchmarks")
async def list_benchmarks(authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    from app.benchmark_engine import BenchmarkEngine
    cases = BenchmarkEngine.list_cases()
    return {"cases": cases}


@app.post("/v1/benchmarks/run")
async def run_benchmark(request: Request, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    body = await request.json()
    case_name = body.get("case_name")
    from app.benchmark_engine import BenchmarkEngine

    if case_name:
        try:
            case = BenchmarkEngine.load_case(case_name)
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": f"Cannot load case '{case_name}': {e}"})
        run_id = str(uuid.uuid4())[:8]
        return await _run_benchmark_case_async(run_id, case, case_name)
    else:
        # Run all cases
        try:
            cases = BenchmarkEngine.list_cases()
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"Cannot list benchmark cases: {e}"})
        results = []
        for cn in cases:
            try:
                case = BenchmarkEngine.load_case(cn)
                run_id = str(uuid.uuid4())[:8]
                result = await _run_benchmark_case_async(run_id, case, cn)
                results.append(result)
            except Exception as e:
                results.append({"case_name": cn, "error": str(e)})
        return {"results": results}


async def _run_benchmark_case_async(run_id: str, case: dict, case_name: str) -> dict:
    """Run a single benchmark case with proper async handling."""
    import json
    import shutil
    from pathlib import Path
    from app.benchmark_engine import BenchmarkEngine, BENCHMARK_RUNS_DIR, WORKFLOW_RUNS_DIR

    question = case["question"]
    sources = case["sources"]
    expected = case.get("expected", {})
    urls = [f"file://{s['path']}" for s in sources]

    # Create a task run using the workflow engine
    task_id = run_id
    art = {
        "workflow_name": "deep_research",
        "current_stage_index": 0,
        "variables": {
            "project_id": "benchmark",
            "docs_context": "",
            "mode": "live",
            "urls": urls,
            "docs": False,
            "question": question,
        },
    }
    db.create_task_run({
        "task_id": task_id,
        "project_id": "benchmark",
        "conversation_id": f"benchmark-{run_id}",
        "task_type": "workflow",
        "user_goal": question,
        "definition_of_done": "Deep research report generated and verified.",
        "max_steps": 9,
        "max_retries": 1,
        "artifacts_json": art,
    })

    # Execute workflow with auto-approve loop
    wf_result = None
    try:
        wf_result = await workflow_engine.execute_workflow(task_id)
        # Auto-approve and resume if paused
        for _ in range(10):
            status = None
            if isinstance(wf_result, dict):
                status = wf_result.get("status") or wf_result.get("final_verdict")
            if status in ("completed", "failed", "cancelled"):
                break
            # Check task DB record for current stage
            task_record = db.get_task_run(task_id)
            if task_record:
                art_data = task_record.get("artifacts_json") or {}
                wf_name = art_data.get("workflow_name", "deep_research")
                wf = workflow_engine.inspect_workflow(wf_name)
                stages = wf.get("stages") if wf else []
                idx = art_data.get("current_stage_index", 0)
                if idx < len(stages):
                    stage_name = stages[idx]["name"]
                    workflow_engine.approve_stage(task_id, stage_name)
                    wf_result = await workflow_engine.resume_workflow(task_id)
                    continue
            break
    except Exception as wf_err:
        wf_result = {"error": str(wf_err), "status": "failed"}

    # Copy output files to .runtime/benchmarks/<run_id>/
    wf_dir = WORKFLOW_RUNS_DIR / run_id
    bench_dir = BENCHMARK_RUNS_DIR / run_id
    bench_dir.mkdir(parents=True, exist_ok=True)

    file_map = {
        "final_report.md": "generated_report.md",
        "sources.json": "sources.json",
        "evidence_items.json": "evidence_items.json",
    }
    for src_name, dst_name in file_map.items():
        src = wf_dir / src_name
        dst = bench_dir / dst_name
        if src.is_file():
            try:
                shutil.copy2(str(src), str(dst))
            except Exception as copy_err:
                dst.write_text("", encoding="utf-8")
        else:
            dst.write_text("", encoding="utf-8")

    # Write benchmark result metadata
    result_data = {
        "run_id": run_id,
        "case_name": case_name,
        "question": question,
        "source_count": len(sources),
        "expected": expected,
        "workflow_result": wf_result if isinstance(wf_result, dict) else {"raw": str(wf_result)},
        "timestamp": time.time(),
    }
    try:
        (bench_dir / "benchmark_result.json").write_text(json.dumps(result_data, indent=2), encoding="utf-8")
    except Exception as meta_err:
        return {"run_id": run_id, "case_name": case_name, "error": f"Failed to write benchmark_result.json: {meta_err}"}

    # Score the run
    try:
        scores = BenchmarkEngine.score_run(run_id)
    except Exception as score_err:
        scores = {"error": str(score_err)}

    return {"run_id": run_id, "case_name": case_name, "scores": scores}


@app.get("/v1/benchmarks/{run_id}/score")
async def get_benchmark_score(run_id: str, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    from app.benchmark_engine import BenchmarkEngine
    try:
        scores = BenchmarkEngine.score_run(run_id)
        return {"run_id": run_id, "scores": scores}
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/v1/benchmarks/history")
async def benchmark_history(authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    from app.benchmark_engine import BenchmarkEngine
    runs = BenchmarkEngine.history()
    return {"runs": runs}


@app.post("/v1/benchmarks/compare")
async def compare_benchmarks(request: Request, authorization: Optional[str] = Header(default=None)):
    _validate_api_key(authorization)
    body = await request.json()
    run_id_a = body.get("run_id_a")
    run_id_b = body.get("run_id_b")
    if not run_id_a or not run_id_b:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Both run_id_a and run_id_b required")
    from app.benchmark_engine import BenchmarkEngine
    comparison = BenchmarkEngine.compare_runs(run_id_a, run_id_b)
    return comparison
