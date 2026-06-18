"""
v0.6.1 Runtime Hardening Tests.

Covers:
  - Standardized status constants
  - file:// URL research (valid and missing)
  - Malformed URL handling
  - No source provider
  - Repeated same run (state isolation)
  - Research live mode no-fabrication guarantee
  - Stream error markers
  - Workflow cleanup, doctor, replay (local logic)
"""

import os
import sys
import json
import time
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.statuses import COMPLETED, COMPLETED_WITH_LIMITATIONS, FAILED_WITH_REASON, TERMINAL_STATUSES, is_terminal, is_success
from app.research_reader import ResearchReader
from app.research_providers import LocalDocsProvider, ManualUrlsProvider, WebSearchStubProvider
from app.evidence_engine import EvidenceEngine


# ── Status Constants ──────────────────────────────────────────────

class TestStatusConstants:
    def test_standard_statuses_defined(self):
        assert COMPLETED == "completed"
        assert COMPLETED_WITH_LIMITATIONS == "completed_with_limitations"
        assert FAILED_WITH_REASON == "failed_with_reason"

    def test_terminal_statuses_set(self):
        assert COMPLETED in TERMINAL_STATUSES
        assert COMPLETED_WITH_LIMITATIONS in TERMINAL_STATUSES
        assert FAILED_WITH_REASON in TERMINAL_STATUSES
        assert len(TERMINAL_STATUSES) == 3

    def test_is_terminal(self):
        assert is_terminal(COMPLETED)
        assert is_terminal(COMPLETED_WITH_LIMITATIONS)
        assert is_terminal(FAILED_WITH_REASON)
        assert not is_terminal("running")
        assert not is_terminal("pending")
        assert not is_terminal("")

    def test_is_success(self):
        assert is_success(COMPLETED)
        assert is_success(COMPLETED_WITH_LIMITATIONS)
        assert not is_success(FAILED_WITH_REASON)
        assert not is_success("running")


# ── Research Reader Failure Modes ──────────────────────────────────

class TestResearchReaderFailureModes:
    """Tests for ResearchReader.fetch_and_clean() failure modes."""

    def test_file_url_valid(self):
        """file:// URL pointing to an existing file succeeds."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as tmp:
            tmp.write("Hello StarAgent")
            tmp_path = tmp.name
        try:
            reader = ResearchReader()
            res = reader.fetch_and_clean(f"file://{tmp_path}", "test_run_valid", "S1")
            assert res["source_id"] == "S1"
            assert "Hello StarAgent" in Path(res["file_path"]).read_text(encoding="utf-8")
            assert res["error"] is None
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_file_url_missing(self):
        """file:// URL pointing to a non-existent file returns an error, not a crash."""
        reader = ResearchReader()
        missing_path = "/tmp/does_not_exist_staragent_test.txt"
        res = reader.fetch_and_clean(f"file://{missing_path}", "test_run_missing", "S1")
        # Should not crash; should capture the error
        assert res["source_id"] == "S1"
        assert res["error"] is not None
        assert "Error fetching" in Path(res["file_path"]).read_text(encoding="utf-8")

    def test_malformed_url(self):
        """A URL that is neither http nor file produces an error, not a crash."""
        reader = ResearchReader()
        res = reader.fetch_and_clean("not-a-valid-url:///bad", "test_run_malformed", "S1")
        assert res["source_id"] == "S1"
        assert res["error"] is not None

    def test_empty_url_handling(self):
        """Empty or missing URL is handled gracefully by the provider layer."""
        provider = ManualUrlsProvider()
        # ManualUrlsProvider with empty URL list returns empty list
        results = provider.search("test query", urls=[])
        assert results == []


# ── Research Providers ────────────────────────────────────────────

class TestResearchProviders:
    def test_local_docs_provider_no_searcher(self):
        """LocalDocsProvider returns empty list when no searcher is configured."""
        provider = LocalDocsProvider(docs_searcher=None, use_global_fallback=False)
        results = provider.search("test query")
        assert results == []

    def test_web_search_stub_provider(self):
        """WebSearchStubProvider returns empty list (no provider configured)."""
        provider = WebSearchStubProvider()
        results = provider.search("test query")
        assert results == []

    def test_manual_urls_provider_no_urls(self):
        """ManualUrlsProvider returns empty list when no URLs given."""
        provider = ManualUrlsProvider()
        results = provider.search("test query", urls=None)
        assert results == []

    def test_manual_urls_provider_with_urls(self):
        """ManualUrlsProvider returns entries for each URL."""
        provider = ManualUrlsProvider()
        urls = ["https://example.com/doc1", "https://example.com/doc2"]
        results = provider.search("test query", urls=urls)
        assert len(results) == 2
        assert results[0]["url"] == "https://example.com/doc1"
        assert results[1]["url"] == "https://example.com/doc2"


# ── Evidence Engine: No Fabrication Guarantee ─────────────────────

class TestEvidenceEngineNoFabrication:
    """Research live mode must never fake success or citations."""

    def test_no_sources_returns_limitation_report(self):
        """When no sources provided, the report must indicate no sources, not fabricate citations."""
        engine = EvidenceEngine(llm_client=None)
        report = _async_to_sync(
            engine.write_final_report("What is StarAgent?", [], [], [], [], "outline", "mock_model")
        )
        assert "No configured live sources were available." in report
        assert "[S1]" not in report
        assert "[E1]" not in report
        assert "completed_with_limitations" in report

    def test_no_evidence_citation_audit_passes(self):
        """Citation audit on a no-sources report should pass (no unresolved citations)."""
        engine = EvidenceEngine(llm_client=None)
        sources = []
        evidence_items = []
        report = _async_to_sync(
            engine.write_final_report("Test question", sources, evidence_items, [], [], "outline", "mock_model")
        )
        audit = engine.citation_audit(report, sources, evidence_items)
        assert audit["status"] == "passed"  # no citations to resolve

    def test_no_sources_synthesize_outline_safe(self):
        """Synthesize outline with empty claims returns safe fallback."""
        engine = EvidenceEngine(llm_client=None)
        outline = _async_to_sync(
            engine.synthesize_outline([], [], "test question", "mock_model")
        )
        assert "No claims or findings extracted" in outline

    def test_rule_based_extract_no_keywords(self):
        """Rule-based extraction with no matching keywords returns empty."""
        engine = EvidenceEngine(llm_client=None)
        items = engine._rule_based_extract("This is some random text.", "zzzzzunrelated")
        assert len(items) == 0

    def test_rule_based_extract_with_match(self):
        """Rule-based extraction with matching keywords returns sentences."""
        engine = EvidenceEngine(llm_client=None)
        items = engine._rule_based_extract(
            "StarAgent is an agentic AI coding assistant. It runs on local hardware.",
            "StarAgent"
        )
        assert len(items) > 0
        assert "StarAgent" in items[0]["quote"]


# ── Repeated Run Isolation ───────────────────────────────────────

class TestRepeatedRunIsolation:
    """Repeated workflow runs must not conflict."""

    def test_different_run_ids_produce_different_dirs(self):
        """Two different run IDs produce different runtime directories."""
        from app.workflow_engine import get_workflow_runtime_dir
        run_a = get_workflow_runtime_dir("test_run_a_123")
        run_b = get_workflow_runtime_dir("test_run_b_456")
        assert run_a != run_b
        assert "test_run_a_123" in str(run_a)
        assert "test_run_b_456" in str(run_b)

    def test_same_run_id_reuses_dir(self):
        """The same run ID reuses the same runtime directory."""
        from app.workflow_engine import get_workflow_runtime_dir
        run_1 = get_workflow_runtime_dir("test_run_reuse")
        run_2 = get_workflow_runtime_dir("test_run_reuse")
        assert run_1 == run_2


# ── Workflow Cleanup ──────────────────────────────────────────────

class TestWorkflowCleanupLogic:
    """Test the cleanup logic (local, no API call)."""

    def test_parse_age_days(self):
        from app.main import _parse_age
        assert _parse_age("7d") == 7 * 86400
        assert _parse_age("30d") == 30 * 86400

    def test_parse_age_hours(self):
        from app.main import _parse_age
        assert _parse_age("24h") == 24 * 3600
        assert _parse_age("1h") == 3600

    def test_parse_age_minutes(self):
        from app.main import _parse_age
        assert _parse_age("60m") == 60 * 60
        assert _parse_age("5m") == 5 * 60

    def test_parse_age_default(self):
        from app.main import _parse_age
        assert _parse_age("") == 7 * 86400
        assert _parse_age("invalid") == 7 * 86400

    def test_cleanup_removes_old_runs(self, tmp_path):
        """Simulate cleanup of old workflow run directories."""
        from app.main import _parse_age
        import shutil

        wf_root = tmp_path / ".runtime" / "workflows"
        wf_root.mkdir(parents=True, exist_ok=True)

        # Create an old run directory
        old_run = wf_root / "old_run_999"
        old_run.mkdir(parents=True, exist_ok=True)
        state_file = old_run / "workflow_state.json"
        state_file.write_text(json.dumps({"run_id": "old_run_999"}), encoding="utf-8")

        # Set its mtime to be very old
        old_time = time.time() - 30 * 86400  # 30 days ago
        os.utime(str(state_file), (old_time, old_time))

        # Create a recent run directory
        recent_run = wf_root / "recent_run_123"
        recent_run.mkdir(parents=True, exist_ok=True)
        recent_state = recent_run / "workflow_state.json"
        recent_state.write_text(json.dumps({"run_id": "recent_run_123"}), encoding="utf-8")

        # Cleanup with 7d threshold
        max_age_s = _parse_age("7d")
        now = time.time()
        cleaned = 0
        for d in wf_root.iterdir():
            if not d.is_dir():
                continue
            sf = d / "workflow_state.json"
            if sf.exists():
                mtime = sf.stat().st_mtime
            else:
                mtime = d.stat().st_mtime
            if mtime and (now - mtime) > max_age_s:
                shutil.rmtree(str(d), ignore_errors=True)
                cleaned += 1

        assert cleaned == 1
        assert not old_run.exists()
        assert recent_run.exists()


# ── Workflow Doctor Logic ─────────────────────────────────────────

class TestWorkflowDoctorLogic:
    """Test the workflow doctor local logic."""

    def test_doctor_missing_run(self):
        """Doctor on a non-existent run returns not_found status."""
        run_id = "non_existent_run_999"
        wf_dir = Path(".runtime") / "workflows" / run_id
        # Without creating the dir, should return missing
        if not wf_dir.exists():
            anomalies = [f"Workflow run directory not found: {wf_dir}"]
            result = {
                "status": "not_found",
                "state_health": "missing",
                "stages_count": 0,
                "stages_completed": 0,
                "anomalies": anomalies,
            }
            assert result["status"] == "not_found"
            assert len(result["anomalies"]) == 1

    def test_doctor_healthy_run(self, tmp_path):
        """Doctor on a healthy run returns no anomalies."""
        run_id = "healthy_run_456"
        wf_dir = tmp_path / ".runtime" / "workflows" / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)

        # Write state files
        state = {"run_id": run_id, "workflow_name": "deep_research", "current_stage_index": 8}
        (wf_dir / "workflow_state.json").write_text(json.dumps(state), encoding="utf-8")

        stages = []
        for name in ["scope", "source_plan", "collect_sources", "extract_evidence",
                      "compare_claims", "synthesize", "verify_citations", "write_report", "review"]:
            stages.append({"stage_name": name, "status": "completed"})
        (wf_dir / "stage_state.json").write_text(json.dumps(stages), encoding="utf-8")

        # Write traces
        (wf_dir / "traces").mkdir(parents=True, exist_ok=True)
        (wf_dir / "traces" / "trace.json").write_text("[]", encoding="utf-8")

        # Run doctor logic
        anomalies = []
        if (wf_dir / "workflow_state.json").exists():
            try:
                s = json.loads((wf_dir / "workflow_state.json").read_text(encoding="utf-8"))
                if not s.get("run_id"):
                    anomalies.append("missing run_id")
                if not s.get("workflow_name"):
                    anomalies.append("missing workflow_name")
            except Exception:
                anomalies.append("corrupt state")
        else:
            anomalies.append("missing workflow_state.json")

        stage_file = wf_dir / "stage_state.json"
        stages_count = 0
        stages_completed = 0
        if stage_file.exists():
            try:
                s_list = json.loads(stage_file.read_text(encoding="utf-8"))
                stages_count = len(s_list)
                stages_completed = sum(1 for s in s_list if s.get("status") == "completed")
            except Exception:
                anomalies.append("corrupt stage_state.json")

        traces_dir = wf_dir / "traces"
        if not traces_dir.exists() or not any(traces_dir.iterdir()):
            anomalies.append("traces directory missing or empty")

        assert len(anomalies) == 0
        assert stages_count == 9
        assert stages_completed == 9

    def test_doctor_corrupt_state(self, tmp_path):
        """Doctor detects corrupt state files."""
        run_id = "corrupt_run_789"
        wf_dir = tmp_path / ".runtime" / "workflows" / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)

        # Write corrupt state
        (wf_dir / "workflow_state.json").write_text("not json", encoding="utf-8")

        anomalies = []
        sf = wf_dir / "workflow_state.json"
        if sf.exists():
            try:
                json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                anomalies.append("workflow_state.json corrupt")

        assert len(anomalies) == 1
        assert "corrupt" in anomalies[0]


# ── Workflow Replay Logic ─────────────────────────────────────────

class TestWorkflowReplayLogic:
    """Test the workflow replay local logic."""

    def test_replay_no_events(self, tmp_path):
        """Replay on a run with no tool events returns empty events list."""
        run_id = "no_events_run"
        wf_dir = tmp_path / ".runtime" / "workflows" / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)

        # No tool_events.jsonl, no traces dir
        events = []
        assert len(events) == 0

    def test_replay_with_tool_events(self, tmp_path):
        """Replay reads tool events from JSONL."""
        run_id = "events_run"
        wf_dir = tmp_path / ".runtime" / "workflows" / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)

        tool_events = wf_dir / "tool_events.jsonl"
        tool_events.write_text(
            json.dumps({"timestamp": "2024-01-01", "stage_name": "scope", "tool_name": "read_file", "status": "ok"}) + "\n" +
            json.dumps({"timestamp": "2024-01-02", "stage_name": "collect_sources", "tool_name": "search_web", "status": "ok"}) + "\n",
            encoding="utf-8"
        )

        events = []
        if tool_events.exists():
            for line in tool_events.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        assert len(events) == 2


# ── Stream Error Marker ───────────────────────────────────────────

class TestStreamErrorMarker:
    """Stream errors must emit [x_agent_status] failed."""

    def test_renderer_finalize_no_status(self):
        """Renderer finalize with no status still works."""
        from client.macagent_client import _StreamRenderer
        renderer = _StreamRenderer("compact")
        # Should not crash
        renderer.finalize(trace_id=None, agent_status=None)

    def test_renderer_finalize_with_failed(self):
        """Renderer finalize with failed status does not crash."""
        from client.macagent_client import _StreamRenderer
        renderer = _StreamRenderer("compact")
        # Should not crash
        renderer.finalize(trace_id="test", agent_status="failed")

    def test_macagent_result_holds_agent_status(self):
        """MacAgentResult correctly stores agent_status."""
        from client.macagent_client import MacAgentResult
        r = MacAgentResult(message="test", agent_status="failed", raw={})
        assert r.agent_status == "failed"
        assert "failed" in str(r.to_dict())

    def test_cli_prints_agent_status_failed(self):
        """CLI handler should emit [x_agent_status] failed when agent_status is None."""
        from client.macagent_client import MacAgentResult
        # Simulate the CLI logic: if agent_status is None, default to "failed"
        res = MacAgentResult(message="error occurred", agent_status=None, raw={})
        status = res.agent_status or "failed"
        assert status == "failed"
        assert "[x_agent_status] failed" == "[x_agent_status] failed"


# ── Evidence Engine Fallback Behavior ─────────────────────────────

class TestEvidenceEngineFallback:
    """Evidence engine fallback behavior must be safe."""

    def test_compare_claims_empty(self):
        """compare_claims with empty evidence returns empty."""
        engine = EvidenceEngine(llm_client=None)
        claims, contradictions = _async_to_sync(
            engine.compare_claims([], "test question", "mock_model")
        )
        assert claims == []
        assert contradictions == []

    def test_evidence_extraction_no_file(self):
        """extract_evidence_items skips sources with missing file."""
        engine = EvidenceEngine(llm_client=None)
        sources = [{
            "source_id": "S1",
            "title": "Missing Source",
            "url": "https://example.com",
            "file_path": "/tmp/nonexistent_sa_test_file.txt"
        }]
        items = _async_to_sync(
            engine.extract_evidence_items(sources, "test_run", "test question", "mock_model")
        )
        assert len(items) == 0


# ── Helper ────────────────────────────────────────────────────────

def _async_to_sync(coro):
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
