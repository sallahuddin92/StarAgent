import os
import shutil
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Mock EmbeddingModel to prevent slow download/initialization during test imports
patcher = patch("app.retrieval.EmbeddingModel")
patcher.start()

# Mock DocsEmbeddingProvider to prevent slow download/initialization in DocsStore
patcher2 = patch("app.docs_embeddings.DocsEmbeddingProvider")
patcher2.start()

from app.workflow_engine import WorkflowEngine, init_workflows, WORKFLOWS_ROOT
from app.stage_engine import StageEngine
from app.model_router import get_stage_model, load_model_routing
from app.tool_runtime import verify_tool_permission
from app.context_loader import context_loader
from app.skill_packs import get_pack_skills, build_pack_injection
from app.checkpoint import save_stage_checkpoint, load_stage_checkpoint, list_task_checkpoints
from app.workflow_trace import WorkflowTraceLogger, render_workflow_trace_tree

@pytest.fixture(autouse=True)
def setup_teardown_workflows():
    # Setup - clean state
    init_workflows()
    runtime_wf = Path(".runtime") / "workflows"
    runtime_tasks = Path(".runtime") / "tasks"
    if runtime_wf.exists():
        shutil.rmtree(runtime_wf)
    if runtime_tasks.exists():
        shutil.rmtree(runtime_tasks)
    yield
    # Teardown
    custom_wf = WORKFLOWS_ROOT / "test_custom"
    if custom_wf.exists():
        shutil.rmtree(custom_wf)
    if runtime_wf.exists():
        shutil.rmtree(runtime_wf)
    if runtime_tasks.exists():
        shutil.rmtree(runtime_tasks)

def test_workflow_initialization():
    """Verify that all 9 default workflows are initialized at startup."""
    assert WORKFLOWS_ROOT.exists()
    required_workflows = [
        "repo_audit", "existing_repo_fix", "feature_build",
        "docs_sdk", "research", "issue_triage", "release",
        "bug_fix", "refactor"
    ]
    for wf in required_workflows:
        wf_dir = WORKFLOWS_ROOT / wf
        assert wf_dir.exists()
        assert (wf_dir / "workflow.yaml").exists()
        assert (wf_dir / "CONTEXT.md").exists()
        assert (wf_dir / "01_inspect").exists()
        assert (wf_dir / "06_finalize").exists()

def test_model_routing(monkeypatch, tmp_path):
    """Verify stage-based model routing."""
    monkeypatch.setenv("STARAGENT_CLI_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LONGCAT_API_KEY", "dummy-key")
    monkeypatch.setenv("LONGCAT_MODEL", "dummy-model")
    
    routing = load_model_routing()
    assert routing.get("inspect") == "gemma4:12b-mlx"
    assert routing.get("analyze") == "LongCat-Flash-Chat"
    
    # Test helper method
    assert get_stage_model("inspect") == "gemma4:12b-mlx"
    assert get_stage_model("analyze") == "LongCat-Flash-Chat"

def test_tool_runtime_permissions():
    """Verify that allowed and blocked tool profiles are strictly enforced."""
    stage_config = {
        "name": "inspect",
        "allowed_tools": ["read_file", "list_files", "search_files", "grep"],
        "blocked_tools": ["write_file", "patch"]
    }
    
    # Allowed
    assert verify_tool_permission(stage_config, "read_file") is True
    assert verify_tool_permission(stage_config, "grep") is True
    
    # Blocked
    assert verify_tool_permission(stage_config, "write_file") is False
    assert verify_tool_permission(stage_config, "patch") is False
    
    # Not in allowed list
    assert verify_tool_permission(stage_config, "run_command") is False

def test_context_loader_layered_and_budgeting():
    """Verify layered context construction and budget trimming."""
    workflow_dir = WORKFLOWS_ROOT / "repo_audit"
    
    # Large docs context to trigger budgeting/truncation
    huge_docs = "doc content " * 5000
    
    context = context_loader.load_layered_context(
        workflow_dir=workflow_dir,
        stage_name="inspect",
        stage_purpose="Code inspection",
        project_id="test_proj",
        user_goal="Audit the code",
        docs_context=huge_docs,
        model_id="gemma4:e2b"
    )
    
    assert "=== WORKFLOW CONTEXT ===" in context
    assert "=== STAGE CONTEXT ===" in context
    assert "=== PROJECT CONTEXT ===" in context
    assert "=== TASK CONTEXT ===" in context
    assert "=== DOCS CONTEXT ===" in context
    # Truncation marker should be present because of budgeting limit
    assert "truncated due to token budget" in context

def test_skill_pack_injection():
    """Verify resolution and compilation of skill packs."""
    skills = get_pack_skills("repo_audit")
    assert "architecture-review" in skills
    assert "security-review" in skills
    
    injection = build_pack_injection("repo_audit")
    assert "## Skill Pack Guidance: repo_audit" in injection
    assert "architecture-review" in injection

def test_checkpoints():
    """Verify checkpoint serialization and retrieval."""
    task_id = "test_task_123"
    trace_data = {"test_step": "value"}
    report = "# Report for test inspect stage"
    
    cp_dir = save_stage_checkpoint(
        task_id=task_id,
        workflow_name="repo_audit",
        stage_name="inspect",
        stage_index=0,
        status="completed",
        variables={"my_var": "val"},
        files_produced=[],
        trace_data=trace_data,
        report_content=report
    )
    
    assert cp_dir.exists()
    assert (cp_dir / "checkpoint.json").exists()
    assert (cp_dir / "trace.json").exists()
    assert (cp_dir / "report.md").exists()
    
    # Load
    loaded = load_stage_checkpoint(task_id, "inspect")
    assert loaded is not None
    assert loaded["workflow_name"] == "repo_audit"
    assert loaded["status"] == "completed"
    assert loaded["variables"]["my_var"] == "val"
    
    # List
    all_cp = list_task_checkpoints(task_id)
    assert len(all_cp) == 1
    assert all_cp[0]["stage_name"] == "inspect"

def test_workflow_graph():
    """Verify the ascii representation of the workflow graph."""
    db_mock = MagicMock()
    se_mock = MagicMock()
    engine = WorkflowEngine(db=db_mock, stage_engine=se_mock)
    
    graph = engine.get_workflow_graph("repo_audit")
    assert "Workflow Graph: repo_audit" in graph
    assert "Stage: inspect" in graph
    assert "Stage: analyze" in graph

def test_custom_workflow_creation():
    """Verify creation of custom workflows."""
    db_mock = MagicMock()
    se_mock = MagicMock()
    engine = WorkflowEngine(db=db_mock, stage_engine=se_mock)
    
    wf = engine.create_custom_workflow("test_custom", "Custom description")
    assert wf["name"] == "test_custom"
    
    custom_dir = WORKFLOWS_ROOT / "test_custom"
    assert custom_dir.exists()
    assert (custom_dir / "workflow.yaml").exists()
    assert (custom_dir / "CONTEXT.md").exists()

@pytest.mark.anyio
async def test_multi_agent_routes_to_workflow():
    from app.main import multi_agent_run
    from fastapi import Request
    
    request_mock = MagicMock(spec=Request)
    async def mock_json():
        return {
            "task": "Build a python script that prints hello world",
            "project_id": "test_proj",
            "conversation_id": "test_conv",
            "stream": False
        }
    request_mock.json = mock_json
    
    with patch("app.main.workflow_engine.execute_workflow", new_callable=AsyncMock) as mock_exec, \
         patch("app.main.workflow_engine.inspect_workflow") as mock_inspect, \
         patch("app.main._validate_api_key"):
         
        mock_inspect.return_value = {"stages": [{"name": "inspect"}]}
        mock_exec.return_value = {"status": "completed"}
        
        await multi_agent_run(request_mock)
        
        mock_exec.assert_called_once()
        called_task_id = mock_exec.call_args[0][0]
        assert len(called_task_id) == 8

@pytest.mark.anyio
async def test_stream_multi_agent_routes_to_workflow():
    from app.main import _stream_multi_agent
    
    with patch("app.main.workflow_engine.execute_workflow", new_callable=AsyncMock) as mock_exec, \
         patch("app.main.workflow_engine.inspect_workflow") as mock_inspect:
         
        mock_inspect.return_value = {"stages": [{"name": "inspect"}]}
        mock_exec.return_value = {"status": "completed"}
        
        gen = _stream_multi_agent("test task", "test_proj", "test_conv")
        
        events = []
        async for event in gen:
            events.append(event)
            
        mock_exec.assert_called_once()

@pytest.mark.anyio
async def test_real_checkpoint_resume():
    db_mock = MagicMock()
    se_mock = MagicMock()
    
    task_id = "res_123"
    db_mock.get_task_run.return_value = {
        "task_id": task_id,
        "project_id": "test_proj",
        "user_goal": "test goal",
        "artifacts_json": {
            "workflow_name": "repo_audit",
            "current_stage_index": 0,
            "variables": {"project_id": "test_proj"}
        }
    }
    
    engine = WorkflowEngine(db=db_mock, stage_engine=se_mock)
    
    with patch("app.workflow_engine.list_task_checkpoints") as mock_list_cp:
        mock_list_cp.return_value = [
            {"stage_name": "inspect", "status": "completed"}
        ]
        
        se_mock.execute_stage = AsyncMock(return_value=("completed", {}))
        
        await engine.execute_workflow(task_id)
        
        # Check that inspect was skipped and the remaining 5 stages were executed
        assert se_mock.execute_stage.call_count == 5
        first_call_args = se_mock.execute_stage.call_args_list[0][1]
        assert first_call_args["stage_config"]["name"] == "analyze"
        assert first_call_args["stage_index"] == 1

def test_stage_centric_trace_keys(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr("app.trace_logger.TRACE_DIR", str(tmp_path))
    
    logger = WorkflowTraceLogger("trace_task_123")
    logger.log_workflow_start("repo_audit", "test goal")
    logger.log_stage_start("inspect", 0, "gemma4:e2b", ["read_file"], ["write_file"])
    logger.log_model_routing("inspect", "gemma4:e2b")
    logger.log_tool_sandbox_check("read_file", True)
    logger.log_stage_verifier("file_exists", True, "entry_points.md exists")
    logger.log_checkpoint_saved("inspect", "/path/to/checkpoint")
    logger.log_workflow_end("completed", "all done")
    
    trace_file = tmp_path / "trace_task_123.jsonl"
    assert trace_file.exists()
    
    lines = trace_file.read_text().splitlines()
    events = [json.loads(line) for line in lines]
    
    event_types = {e["event_type"] for e in events}
    roles = {e["role"] for e in events}
    
    assert "workflow_start" in event_types
    assert "stage_start" in event_types
    assert "model_routing" in event_types
    assert "sandbox_check" in event_types
    assert "stage_verifier" in event_types
    assert "checkpoint_saved" in event_types
    assert "workflow_end" in event_types
    
    assert "WORKFLOW_ENGINE" in roles
    assert "STAGE_ENGINE" in roles
    assert "MODEL_ROUTER" in roles
    assert "TOOL_RUNTIME" in roles
    assert "STAGE_VERIFIER" in roles
    assert "CHECKPOINT_MANAGER" in roles

# --- v0.5.0 Native Regression Tests ---

def test_capability_routing(monkeypatch):
    """Verify selection of configured API/local models via resolve_capabilities."""
    from app.model_router import resolve_capabilities
    
    # 1. Privacy mode (local only)
    model_id = resolve_capabilities(["reasoning", "coding"], privacy_mode=True)
    assert model_id == "gemma4:e2b" or "qwen" in model_id # ollama provider only
    
    # Isolate from environment to ensure deterministic result
    monkeypatch.delenv("LONGCAT_API_KEY", raising=False)
    monkeypatch.delenv("LONGCAT_MODEL", raising=False)
    
    # 2. Configure mock keys and request reasoning/long context
    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    model_id = resolve_capabilities(["long_context", "reasoning"], privacy_mode=False)
    assert model_id == "gemini-1.5-pro"

    # 3. Configure LongCat
    monkeypatch.setenv("LONGCAT_API_KEY", "mock-key")
    monkeypatch.setenv("LONGCAT_MODEL", "LongCat-Flash-Thinking-2601")
    model_id = resolve_capabilities(["planning", "reasoning"], privacy_mode=False)
    assert model_id == "LongCat-Flash-Thinking-2601"

def test_fallback_model_routing(monkeypatch):
    """Verify fallback chain when preferred models are missing."""
    from app.model_router import resolve_capabilities
    
    # Ensure no API keys are present
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LONGCAT_API_KEY", raising=False)
    
    model_id = resolve_capabilities(["unknown_capability"])
    # Should fall back to local default
    assert model_id == "gemma4:12b-mlx"

def test_tool_registry_stage_allowlist():
    """Verify that write/destructive actions are blocked during planning/inspect stages."""
    from app.tool_runtime import verify_tool_permission
    
    # Inspect stage config
    inspect_config = {
        "name": "inspect",
        "allowed_tools": ["read_file", "write_file", "run_command"] # Even if explicitly allowed in config
    }
    
    # Check permissions (should block write/destructive tool anyway)
    assert verify_tool_permission(inspect_config, "read_file") is True
    assert verify_tool_permission(inspect_config, "write_file") is False
    assert verify_tool_permission(inspect_config, "run_command") is False
    
    # Execute stage config (should allow write/destructive tool)
    exec_config = {
        "name": "execute",
        "allowed_tools": ["read_file", "write_file", "run_command"]
    }
    assert verify_tool_permission(exec_config, "write_file") is True
    assert verify_tool_permission(exec_config, "run_command") is True

def test_gate_engine_pass_fail(tmp_path):
    """Verify gate matching logic in GateEngine."""
    from app.gate_engine import GateEngine
    
    engine = GateEngine(workspace_root=str(tmp_path))
    
    # Setup test file
    test_file = tmp_path / "test.md"
    test_file.write_text("Hello StarAgent v0.5.0", encoding="utf-8")
    
    # 1. file_exists gate
    res = engine.evaluate_gates([
        {"type": "file_exists", "arguments": {"paths": ["test.md"]}}
    ], {})
    assert res["success"] is True
    assert res["status"] == "pass"
    
    res = engine.evaluate_gates([
        {"type": "file_exists", "arguments": {"paths": ["nonexistent.md"]}}
    ], {})
    assert res["success"] is False
    assert res["status"] == "fail"
    
    # 2. output_contains gate
    res = engine.evaluate_gates([
        {"type": "output_contains", "arguments": {"substring": "StarAgent"}}
    ], {"stage_output": "Welcome to StarAgent platform."})
    assert res["success"] is True
    
    # 3. semantic_regex gate
    res = engine.evaluate_gates([
        {"type": "semantic_regex", "arguments": {"pattern": r"v0\.[0-9]\.[0-9]"}}
    ], {"stage_output": "Welcome to StarAgent v0.5.0."})
    assert res["success"] is True

def test_workflow_state_persistence(tmp_path, monkeypatch):
    """Verify that execution state is persisted to the state folder."""
    import json
    from app.workflow_engine import save_workflow_run_state, get_workflow_runtime_dir
    
    # Set temp folder for runtime
    monkeypatch.setattr("app.workflow_engine.Path", lambda *args: tmp_path / Path(*args) if args[0] == ".runtime" else Path(*args))
    
    run_id = "run_999"
    stages = [{"name": "inspect"}, {"name": "analyze"}]
    stage_statuses = {"inspect": "completed", "analyze": "running"}
    variables = {"project_id": "test_proj", "my_var": "hello"}
    
    save_workflow_run_state(
        run_id=run_id,
        workflow_name="repo_audit",
        current_stage_idx=1,
        variables=variables,
        stages=stages,
        stage_statuses=stage_statuses
    )
    
    # Check workflow_state.json
    wf_state_file = tmp_path / ".runtime" / "workflows" / run_id / "workflow_state.json"
    assert wf_state_file.exists()
    wf_state = json.loads(wf_state_file.read_text())
    assert wf_state["run_id"] == run_id
    assert wf_state["current_stage_index"] == 1
    assert wf_state["variables"]["my_var"] == "hello"
    
    # Check stage_state.json
    s_state_file = tmp_path / ".runtime" / "workflows" / run_id / "stage_state.json"
    assert s_state_file.exists()
    s_state = json.loads(s_state_file.read_text())
    assert len(s_state) == 2
    assert s_state[0]["stage_name"] == "inspect"
    assert s_state[0]["status"] == "completed"
    assert s_state[1]["status"] == "running"

@pytest.mark.anyio
async def test_human_approval_pause_and_resume():
    """Verify that approval pause/resume endpoints function correctly."""
    db_mock = MagicMock()
    se_mock = MagicMock()
    
    engine = WorkflowEngine(db=db_mock, stage_engine=se_mock)
    
    task_id = "approval_123"
    task_run_data = {
        "task_id": task_id,
        "project_id": "test_proj",
        "user_goal": "test goal",
        "status": "pending",
        "artifacts_json": {
            "workflow_name": "repo_audit",
            "current_stage_index": 2, # Stage: plan (approval required)
            "variables": {"project_id": "test_proj"}
        }
    }
    
    db_mock.get_task_run.side_effect = lambda tid: task_run_data
    def update_run(tid, patch):
        task_run_data.update(patch)
    db_mock.update_task_run.side_effect = update_run
    
    # Mock stage engine to pause
    se_mock.execute_stage = AsyncMock(return_value=("paused", {"project_id": "test_proj"}))
    
    # Run
    res = await engine.execute_workflow(task_id)
    # Status should be paused
    assert res.get("status") == "paused"
    
    # Mock approval
    engine.approve_stage(task_id, "plan")
    
    # Resume should execute normally
    se_mock.execute_stage = AsyncMock(return_value=("completed", {"project_id": "test_proj", "approved_plan": True}))
    res2 = await engine.resume_workflow(task_id, force_stage_name="plan")
    
    assert se_mock.execute_stage.call_count == 4
    first_call_kwargs = se_mock.execute_stage.call_args_list[0][1]
    assert first_call_kwargs["stage_config"]["name"] == "plan"

@pytest.mark.anyio
async def test_repair_stage_loop():
    """Verify that a verification failure triggers a repair stage."""
    db_mock = MagicMock()
    se_mock = MagicMock()
    
    engine = WorkflowEngine(db=db_mock, stage_engine=se_mock)
    
    task_id = "repair_123"
    db_mock.get_task_run.return_value = {
        "task_id": task_id,
        "project_id": "test_proj",
        "user_goal": "test goal",
        "status": "running",
        "artifacts_json": {
            "workflow_name": "repo_audit",
            "current_stage_index": 0,
            "variables": {"project_id": "test_proj"}
        }
    }
    
    # Side effect function to handle multiple stages dynamically
    async def mock_execute(*args, **kwargs):
        stage_name = kwargs.get("stage_config", {}).get("name", "")
        if stage_name == "inspect":
            return "failed", {}
        elif stage_name == "repair_inspect":
            return "completed", {}
        return "completed", {}
        
    se_mock.execute_stage = AsyncMock(side_effect=mock_execute)
    
    await engine.execute_workflow(task_id, max_step_advances=1)
    
    # execute_stage should be called twice (stage execution + repair execution)
    assert se_mock.execute_stage.call_count == 2
    
    # First call is inspect stage
    assert se_mock.execute_stage.call_args_list[0][1]["stage_config"]["name"] == "inspect"
    # Second call is repair_inspect dynamic stage
    assert se_mock.execute_stage.call_args_list[1][1]["stage_config"]["name"] == "repair_inspect"

