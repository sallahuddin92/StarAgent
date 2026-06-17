from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.database import DatabaseManager
from app.docs_ingest import DocsIngester
from app.docs_search import DocsSearcher
from app.docs_store import DocsStore
from app.multi_agent import OrchestratorAgent, SubTask, AgentRole
from app.blueprint import generate_blueprint
from app.tokenbudget import TokenCounter


class DummyLLM:
    provider = "ollama"
    model_name = "gemma4:e2b"

    async def text(self, *args, **kwargs):
        return "write_file(\"scratch/x/main.py\", \"print('ok')\")"


class DummyToolExecutor:
    async def execute_tool_call(self, tool_call):
        return {"content": "[Failed] Output:\ninstall failed"}


class EchoToolExecutor:
    async def execute_tool_call(self, tool_call):
        return {"content": "ok"}


class DummyStepExecutor:
    async def execute_step(self, step, workspace):
        return {
            "tool_calls": [
                {
                    "id": "call_rc",
                    "type": "function",
                    "function": {
                        "name": "run_command",
                        "arguments": '{"command":"echo noop","cwd":"."}',
                    },
                }
            ]
        }


def _build_docs_stack(tmp_path):
    db = DatabaseManager(str(tmp_path / "docs_grounded_agent.db"))
    store = DocsStore(db_manager=db)
    ingester = DocsIngester(store)
    searcher = DocsSearcher(store)
    return store, ingester, searcher


def _write_weathersdk_docs(docs_dir: Path) -> None:
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "weathersdk.md").write_text(
        """
# WeatherSDK Quickstart

```python
from weather_sdk import WeatherClient
client = WeatherClient(api_key="abc")
forecast = client.current(city="Kuala Lumpur")
print(forecast)
```
""".strip(),
        encoding="utf-8",
    )


@pytest.mark.anyio
async def test_docs_context_injection_extracts_weathersdk_terms(tmp_path):
    docs_dir = tmp_path / "docs"
    _write_weathersdk_docs(docs_dir)

    _, ingester, searcher = _build_docs_stack(tmp_path)
    res = ingester.ingest_path("sdk-test", str(docs_dir), source_type="project_docs")
    assert res["status"] == "success"

    orch = OrchestratorAgent(
        executor=None,
        tool_executor=None,
        llm_client=DummyLLM(),
        project_id="sdk-test",
    )
    orch.docs_searcher = searcher

    task = "Create a Python weather app using WeatherSDK from project documentation. Save to scratch/sdk_test/app."
    docs_ctx = await orch._prepare_docs_context(task)

    assert docs_ctx["required"] is True
    assert docs_ctx["evidence"], "Expected docs evidence"
    assert "WeatherSDK" in docs_ctx["query"] or "weather" in docs_ctx["query"].lower()

    block = docs_ctx["block"]
    assert "WeatherClient(api_key=\"abc\")" in block
    assert "client.current(city=\"Kuala Lumpur\")" in block

    terms = docs_ctx["required_terms"]
    assert any("WeatherClient(api_key=\"abc\")" in t for t in terms)
    assert any("client.current(city=\"Kuala Lumpur\")" in t for t in terms)


@pytest.mark.anyio
async def test_docs_required_task_fails_fast_when_evidence_missing(tmp_path):
    _, _, searcher = _build_docs_stack(tmp_path)

    orch = OrchestratorAgent(
        executor=None,
        tool_executor=None,
        llm_client=DummyLLM(),
        project_id="sdk-test-empty",
    )
    orch.docs_searcher = searcher

    task = "Create a Python weather app using WeatherSDK from project documentation. Save to scratch/sdk_test/app."
    out = await orch.run(task)

    assert out["status"] == "failed"
    assert out["message"] == "insufficient project documentation evidence"
    assert out["subtask_count"] == 0


@pytest.mark.anyio
async def test_missing_sdk_dependency_triggers_doc_search(tmp_path):
    docs_dir = tmp_path / "docs"
    _write_weathersdk_docs(docs_dir)
    _, ingester, searcher = _build_docs_stack(tmp_path)
    ingester.ingest_path("sdk-test", str(docs_dir), source_type="project_docs")

    class TrackingSearcher:
        def __init__(self, base):
            self.base = base
            self.queries = []

        def search_structured(self, project_id, query, max_results=3, **kwargs):
            self.queries.append(query)
            return self.base.search_structured(project_id=project_id, query=query, max_results=max_results, **kwargs)

    orch = OrchestratorAgent(
        executor=None,
        tool_executor=DummyToolExecutor(),
        llm_client=DummyLLM(),
        project_id="sdk-test",
    )
    tracker = TrackingSearcher(searcher)
    orch.docs_searcher = tracker

    subtask = SubTask(
        id="run",
        role=AgentRole.TESTING,
        description="Run app",
        status="failed",
        result="ModuleNotFoundError: No module named 'weather_sdk'",
        tool_outputs=[],
    )
    docs_ctx = await orch._prepare_docs_context(
        "Create a demo weather app using WeatherSDK from project documentation."
    )
    await orch._attempt_docs_grounded_dependency_resolution(
        user_input="Create a demo weather app using WeatherSDK from project documentation.",
        blueprint={"project_root": str(tmp_path / "demo_app")},
        testing_subtask=subtask,
        docs_ctx=docs_ctx,
    )

    assert any("install setup import" in q for q in tracker.queries), tracker.queries


@pytest.mark.anyio
async def test_fake_sdk_demo_creates_stub_and_runs(tmp_path):
    docs_dir = tmp_path / "docs"
    _write_weathersdk_docs(docs_dir)
    _, ingester, searcher = _build_docs_stack(tmp_path)
    ingester.ingest_path("sdk-test", str(docs_dir), source_type="project_docs")

    app_dir = tmp_path / "sdk_demo_app"
    app_dir.mkdir(parents=True, exist_ok=True)
    main_py = app_dir / "main.py"
    main_py.write_text(
        """
from weather_sdk import WeatherClient

client = WeatherClient(api_key="abc")
print(client.current(city="Kuala Lumpur"))
""".strip()
        + "\n",
        encoding="utf-8",
    )

    orch = OrchestratorAgent(
        executor=None,
        tool_executor=DummyToolExecutor(),
        llm_client=DummyLLM(),
        project_id="sdk-test",
    )
    orch.docs_searcher = searcher

    subtask = SubTask(
        id="run",
        role=AgentRole.TESTING,
        description="Run app",
        status="failed",
        result="ModuleNotFoundError: No module named 'weather_sdk'",
        tool_outputs=[],
        artifacts=[str(main_py)],
    )
    docs_ctx = await orch._prepare_docs_context(
        "Create a demo weather app using WeatherSDK from project documentation."
    )
    out = await orch._attempt_docs_grounded_dependency_resolution(
        user_input="Create a demo weather app using WeatherSDK from project documentation.",
        blueprint={"project_root": str(app_dir)},
        testing_subtask=subtask,
        docs_ctx=docs_ctx,
    )

    assert out["resolved"] is True
    stub = app_dir / "weather_sdk.py"
    assert stub.exists()
    stub_text = stub.read_text(encoding="utf-8")
    assert "class WeatherClient" in stub_text
    assert "def current" in stub_text

    run = subprocess.run(["python3", "main.py"], cwd=app_dir, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    output = run.stdout + run.stderr
    assert "Kuala Lumpur" in output
    assert "31" in output
    assert "Cloudy" in output


@pytest.mark.anyio
async def test_production_task_without_install_docs_fails_safely(tmp_path):
    docs_dir = tmp_path / "docs"
    _write_weathersdk_docs(docs_dir)
    _, ingester, searcher = _build_docs_stack(tmp_path)
    ingester.ingest_path("sdk-test", str(docs_dir), source_type="project_docs")

    app_dir = tmp_path / "prod_app"
    app_dir.mkdir(parents=True, exist_ok=True)

    orch = OrchestratorAgent(
        executor=None,
        tool_executor=DummyToolExecutor(),
        llm_client=DummyLLM(),
        project_id="sdk-test",
    )
    orch.docs_searcher = searcher

    subtask = SubTask(
        id="run",
        role=AgentRole.TESTING,
        description="Run app",
        status="failed",
        result="ModuleNotFoundError: No module named 'weather_sdk'",
        tool_outputs=[],
    )
    docs_ctx = await orch._prepare_docs_context(
        "Build production weather service using WeatherSDK from project documentation."
    )
    out = await orch._attempt_docs_grounded_dependency_resolution(
        user_input="Build production weather service using WeatherSDK from project documentation.",
        blueprint={"project_root": str(app_dir)},
        testing_subtask=subtask,
        docs_ctx=docs_ctx,
    )

    assert out["fatal"] is True
    assert out["message"] == "missing dependency and no installation evidence found"


@pytest.mark.anyio
async def test_docs_intent_detects_using_project_documentation_phrase():
    task = "Create a script using the project documentation for API usage."
    assert OrchestratorAgent._needs_project_docs(task) is True


@pytest.mark.anyio
async def test_docs_intent_does_not_trigger_for_generic_fastapi_task():
    task = "Create a FastAPI calculator API with pytest tests in scratch/eval_backend."
    assert OrchestratorAgent._needs_project_docs(task) is False


@pytest.mark.anyio
async def test_longcat_blueprint_contains_required_semantics():
    llm = DummyLLM()
    task = (
        "Create a Python script in scratch/longcat_client_test that calls LongCat "
        "OpenAI-compatible chat completions using the project documentation."
    )
    blueprint = await generate_blueprint(task, llm)
    assert blueprint["project_root"] == "scratch/longcat_client_test"
    assert "scratch/longcat_client_test/main.py" in blueprint["structure"]
    assert "LONGCAT_API_KEY" in blueprint["required_semantics"]
    assert "chat/completions" in blueprint["required_semantics"]
    assert "python3 main.py" in blueprint["required_commands"]


@pytest.mark.anyio
async def test_docs_tokens_nonzero_when_evidence_injected(tmp_path):
    docs_dir = tmp_path / "docs"
    _write_weathersdk_docs(docs_dir)
    _, ingester, searcher = _build_docs_stack(tmp_path)
    ingester.ingest_path("sdk-test", str(docs_dir), source_type="project_docs")

    orch = OrchestratorAgent(
        executor=None,
        tool_executor=None,
        llm_client=DummyLLM(),
        project_id="sdk-test",
    )
    orch.docs_searcher = searcher

    docs_ctx = await orch._prepare_docs_context(
        "Create a weather demo using the project documentation and WeatherSDK."
    )
    assert docs_ctx["required"] is True
    assert docs_ctx["evidence"]
    tokens = TokenCounter().count_tokens(docs_ctx.get("block", ""))
    assert tokens > 0


@pytest.mark.anyio
async def test_backend_impl_task_fails_without_required_write(tmp_path):
    from app.multi_agent import BaseAgent

    root = tmp_path / "impl_app"
    root.mkdir(parents=True, exist_ok=True)
    required_path = str(root / "main.py")
    subtask = SubTask(
        id="write",
        role=AgentRole.BACKEND,
        description="Implement Python app in main.py",
        requirements={"project_root": str(root), "path": required_path},
        blueprint={"project_root": str(root), "structure": [required_path]},
        tool_steps=['run_command("echo noop")'],
    )

    agent = BaseAgent(
        role=AgentRole.BACKEND,
        executor=DummyStepExecutor(),
        tool_executor=EchoToolExecutor(),
        llm_client=DummyLLM(),
    )
    out = await agent.run(subtask)
    assert out.status == "failed"
    assert "required file not written" in out.result
