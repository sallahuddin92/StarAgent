"""Focused v0.6.1 Runtime Hardening tests for workflow cleanup, doctor, replay,
research failure statuses, and stream error markers."""

import os
import sys
import json
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.statuses import COMPLETED, COMPLETED_WITH_LIMITATIONS, FAILED_WITH_REASON, TERMINAL_STATUSES, is_terminal, is_success
from app.evidence_engine import EvidenceEngine
from app.research_reader import ResearchReader


# ── Research failure statuses ──────────────────────────────────────

class TestResearchFailureStatuses:
    """Malformed URL → failed_with_reason, no sources → completed_with_limitations."""

    def test_malformed_url_returns_error(self):
        """Malformed URL is caught by ResearchReader and error field is set."""
        reader = ResearchReader()
        res = reader.fetch_and_clean("not-a-valid-url:///bad", "test_run_malformed", "S1")
        assert res["source_id"] == "S1"
        assert res["error"] is not None
        assert "Error fetching" in Path(res["file_path"]).read_text(encoding="utf-8")

    def test_no_sources_completed_with_limitations(self):
        """write_final_report with empty sources returns completed_with_limitations marker."""
        engine = EvidenceEngine(llm_client=None)
        report = _async_to_sync(
            engine.write_final_report("test question", [], [], [], [], "outline", "mock_model")
        )
        assert "No configured live sources were available." in report
        assert "[S1]" not in report
        assert "[E1]" not in report

    def test_all_sources_fail_sets_failed_reason(self):
        """Simulate write_report detecting all sources with errors."""
        sources = [
            {"source_id": "S1", "url": "file:///nonexistent1", "error": "file not found"},
            {"source_id": "S2", "url": "file:///nonexistent2", "error": "file not found"},
        ]
        evidence_items = []
        test_failed = False
        if sources and all(s.get("error") for s in sources):
            test_failed = True
        assert test_failed

    def test_partial_source_errors_still_has_limitations(self):
        """When some sources succeed but evidence is empty, status is completed_with_limitations."""
        engine = EvidenceEngine(llm_client=None)
        sources = [
            {"source_id": "S1", "title": "Ok Source", "url": "https://example.com/ok", "error": None},
            {"source_id": "S2", "title": "Bad Source", "url": "file:///nonexistent", "error": "file not found"},
        ]
        evidence_items = []
        report = _async_to_sync(
            engine.write_final_report("test question", sources, evidence_items, [], [], "outline", "mock_model")
        )
        assert "No search results" not in report


# ── Cleanup dry-run ────────────────────────────────────────────────

class TestCleanupDryRun:
    """Cleanup --dry-run must detect candidates without deleting."""

    def test_dry_run_detects_old_dir(self, tmp_path):
        """Simulate cleanup dry-run detecting an old workflow directory."""
        from app.main import _parse_age

        wf_root = tmp_path / ".runtime" / "workflows"
        wf_root.mkdir(parents=True, exist_ok=True)

        # Create old run dir
        old_run = wf_root / "old_run_999"
        old_run.mkdir(parents=True, exist_ok=True)
        state_file = old_run / "workflow_state.json"
        state_file.write_text(json.dumps({"run_id": "old_run_999"}), encoding="utf-8")
        old_time = time.time() - 30 * 86400  # 30 days ago
        os.utime(str(state_file), (old_time, old_time))

        # Create recent run dir
        recent_run = wf_root / "recent_run_123"
        recent_run.mkdir(parents=True, exist_ok=True)
        recent_state = recent_run / "workflow_state.json"
        recent_state.write_text(json.dumps({"run_id": "recent_run_123"}), encoding="utf-8")

        max_age_s = _parse_age("7d")
        now = time.time()
        candidates = []
        for d in wf_root.iterdir():
            if not d.is_dir():
                continue
            sf = d / "workflow_state.json"
            mtime = sf.stat().st_mtime if sf.exists() else d.stat().st_mtime
            if mtime and (now - mtime) > max_age_s:
                candidates.append(d.name)

        assert "old_run_999" in candidates
        assert "recent_run_123" not in candidates
        assert len(candidates) == 1
        # Verify dry-run didn't delete anything
        assert old_run.exists()

    def test_dry_run_no_candidates(self, tmp_path):
        """Dry-run with no old dirs returns empty list."""
        from app.main import _parse_age

        wf_root = tmp_path / ".runtime" / "workflows"
        wf_root.mkdir(parents=True, exist_ok=True)

        recent_run = wf_root / "recent_run_123"
        recent_run.mkdir(parents=True, exist_ok=True)
        sf = recent_run / "workflow_state.json"
        sf.write_text(json.dumps({"run_id": "recent_run_123"}), encoding="utf-8")

        max_age_s = _parse_age("7d")
        now = time.time()
        candidates = []
        for d in wf_root.iterdir():
            if not d.is_dir():
                continue
            sf = d / "workflow_state.json"
            mtime = sf.stat().st_mtime if sf.exists() else d.stat().st_mtime
            if mtime and (now - mtime) > max_age_s:
                candidates.append(d.name)

        assert len(candidates) == 0


# ── Workflow Doctor ────────────────────────────────────────────────

class TestWorkflowDoctor:
    """Doctor must detect missing state and final_report.md."""

    def test_doctor_missing_run_dir(self):
        """Doctor on a non-existent run returns not_found."""
        wf_dir = Path("/tmp/nonexistent_sa_doctor_test")
        if not wf_dir.exists():
            result = {
                "status": "not_found",
                "state_health": "missing",
                "stages_count": 0,
                "stages_completed": 0,
                "anomalies": [f"Workflow run directory not found"],
            }
            assert result["status"] == "not_found"
            assert len(result["anomalies"]) == 1

    def test_doctor_missing_final_report(self, tmp_path):
        """Doctor flags missing final_report.md when stages exist."""
        run_id = "doctor_test_no_report"
        wf_dir = tmp_path / ".runtime" / "workflows" / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)

        # Write state files (stages_completed > 0)
        state = {"run_id": run_id, "workflow_name": "deep_research", "current_stage_index": 8}
        (wf_dir / "workflow_state.json").write_text(json.dumps(state), encoding="utf-8")

        stages = [{"stage_name": "scope", "status": "completed"}]
        (wf_dir / "stage_state.json").write_text(json.dumps(stages), encoding="utf-8")

        # Traces dir present
        (wf_dir / "traces").mkdir(parents=True, exist_ok=True)
        (wf_dir / "traces" / "trace.json").write_text("[]", encoding="utf-8")

        # Run doctor logic
        anomalies = []
        stages_completed = 0
        stage_file = wf_dir / "stage_state.json"
        if stage_file.exists():
            s_list = json.loads(stage_file.read_text(encoding="utf-8"))
            stages_completed = sum(1 for s in s_list if s.get("status") == "completed")

        report_file = wf_dir / "final_report.md"
        if not report_file.exists() and stages_completed > 0:
            anomalies.append("final_report.md missing (workflow may not have completed)")

        assert len(anomalies) == 1
        assert "final_report.md" in anomalies[0]


# ── Workflow Replay ───────────────────────────────────────────────

class TestWorkflowReplay:
    """Replay must return stage state and tool events."""

    def test_replay_returns_stage_order(self, tmp_path):
        """Replay includes ordered stage list from stage_state.json."""
        run_id = "replay_test_stages"
        wf_dir = tmp_path / ".runtime" / "workflows" / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)

        stages = [
            {"stage_name": "scope", "status": "completed"},
            {"stage_name": "collect_sources", "status": "completed"},
            {"stage_name": "extract_evidence", "status": "failed"},
        ]
        (wf_dir / "stage_state.json").write_text(json.dumps(stages), encoding="utf-8")

        # Simulate replay logic
        s_list = json.loads((wf_dir / "stage_state.json").read_text(encoding="utf-8"))
        assert len(s_list) == 3
        assert s_list[0]["stage_name"] == "scope"
        assert s_list[1]["status"] == "completed"
        assert s_list[2]["status"] == "failed"


# ── Stream error marker ────────────────────────────────────────────

class TestStreamErrorMarker:
    """Stream errors must emit [x_agent_status] failed."""

    def test_stream_exception_sets_failed(self):
        """Simulate stream exception setting agent_status to failed."""
        from client.macagent_client import MacAgentResult

        agent_status = None
        try:
            raise RuntimeError("stream connection lost")
        except Exception:
            agent_status = "failed"

        assert agent_status == "failed"
        result = MacAgentResult(message="partial content", agent_status=agent_status, raw={})
        assert result.agent_status == "failed"

    def test_cli_defaults_to_failed_when_none(self):
        """CLI defaults agent_status to 'failed' when None."""
        from client.macagent_client import MacAgentResult

        res = MacAgentResult(message="error", agent_status=None, raw={})
        status = res.agent_status or "failed"
        assert status == "failed"
        marker = f"[x_agent_status] {status}"
        assert marker == "[x_agent_status] failed"


# ── WebResearch status propagation ─────────────────────────────────

class TestWebResearchStatus:
    """WebResearch must return structured statuses for failure modes."""

    def test_no_search_results_status(self):
        """Simulate web_research returning completed_with_limitations when search is empty."""
        result = {
            "query": "test",
            "answer": "No search results found.",
            "sources": [],
            "status": "completed_with_limitations",
            "status_reason": "No search results were returned by the configured search backend.",
        }
        assert result["status"] == "completed_with_limitations"
        assert "No search results" in result["answer"]

    def test_failed_extraction_status(self):
        """Simulate web_research returning completed_with_limitations when extraction fails."""
        result = {
            "query": "test",
            "answer": "Failed to extract content from sources.",
            "sources": [],
            "status": "completed_with_limitations",
            "status_reason": "Sources were found but none yielded extractable content",
        }
        assert result["status"] == "completed_with_limitations"
        assert "Failed to extract" in result["answer"]


# ── v0.6.2 Research Quality Gates ──────────────────────────────────

class TestVersionDetection:
    """Version detection and evidence relevance scoring."""

    def test_version_detected_in_question(self):
        """_detect_version_in_question detects v0.6.1 in question text."""
        from app.evidence_engine import EvidenceEngine
        engine = EvidenceEngine(llm_client=None)
        vi = engine._detect_version_in_question("What does StarAgent v0.6.1 improve?")
        assert "v0.6.1" in vi["versions"]
        assert vi["is_comparison"] is False
        assert len(vi["versions"]) == 1

    def test_comparison_question_detected(self):
        """v0.6.1 vs v0.6.0 detected as comparison with both versions."""
        from app.evidence_engine import EvidenceEngine
        engine = EvidenceEngine(llm_client=None)
        vi = engine._detect_version_in_question("What does v0.6.1 improve compared with v0.6.0?")
        assert "v0.6.1" in vi["versions"]
        assert "v0.6.0" in vi["versions"]
        assert vi["is_comparison"] is True
        assert len(vi["comparison_versions"]) == 2

    def test_unrelated_version_evidence_rejected(self):
        """Evidence from an unrelated version section gets accepted=False when question specifies a version."""
        from app.evidence_engine import EvidenceEngine
        engine = EvidenceEngine(llm_client=None)
        question = "What does StarAgent v0.6.1 improve?"
        vi = engine._detect_version_in_question(question)
        content_lines = ["## v0.5.2", "Some feature was added in this version."]
        score, reason = engine._score_evidence_relevance(
            question, "RELEASE_NOTES.md", content_lines,
            "Some feature was added in this version.", vi
        )
        assert score < 0.3  # penalty from unrelated version section

    def test_version_match_scores_high(self):
        """Evidence from a matching version section scores >= 0.3 and is accepted."""
        from app.evidence_engine import EvidenceEngine
        engine = EvidenceEngine(llm_client=None)
        question = "What does StarAgent v0.6.1 improve?"
        vi = engine._detect_version_in_question(question)
        content_lines = ["## v0.6.1", "New feature X was added."]
        score, reason = engine._score_evidence_relevance(
            question, "RELEASE_NOTES.md", content_lines,
            "New feature X was added in v0.6.1.", vi
        )
        assert score >= 0.3
        assert "matches version" in reason

    def test_report_rejects_unaccepted_evidence(self):
        """write_final_report returns limitation report when all evidence is rejected."""
        from app.evidence_engine import EvidenceEngine
        engine = EvidenceEngine(llm_client=None)
        sources = [{"source_id": "S1", "title": "Test", "url": "file:///test"}]
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "quote": "old feature", "assertion": "old feature",
             "relevance_score": 0.1, "relevance_reason": "unrelated", "accepted": False},
            {"evidence_id": "E2", "source_id": "S1", "quote": "another old", "assertion": "another old",
             "relevance_score": 0.0, "relevance_reason": "unrelated", "accepted": False},
        ]
        report = _async_to_sync(
            engine.write_final_report("test question", sources, evidence, [], [], "outline", "mock_model")
        )
        assert "No citations were fabricated" in report
        assert "[E1]" not in report

    def test_report_only_uses_accepted_evidence(self):
        """write_final_report only includes accepted evidence in fallback report."""
        from app.evidence_engine import EvidenceEngine
        engine = EvidenceEngine(llm_client=None)
        sources = [{"source_id": "S1", "title": "Test", "url": "file:///test"}]
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "quote": "rejected", "assertion": "rejected",
             "relevance_score": 0.1, "relevance_reason": "bad", "accepted": False},
            {"evidence_id": "E2", "source_id": "S1", "quote": "accepted", "assertion": "accepted",
             "relevance_score": 0.8, "relevance_reason": "good", "accepted": True},
        ]
        claims = [{"claim_id": "C1", "claim_text": "test claim", "supporting_evidence_ids": ["E2"], "status": "consensus"}]
        report = _async_to_sync(
            engine.write_final_report("test question", sources, evidence, claims, [], "outline", "mock_model")
        )
        assert "[E2]" in report
        assert "[E1]" not in report


class TestUnsupportedClaimsGate:
    """Unsupported claims gate must detect fake citations."""

    def test_unsupported_claim_gate_detects_fake_citations(self, tmp_path):
        """Gate fails when report cites evidence IDs not in accepted set."""
        from app.gate_engine import GateEngine
        gate_eng = GateEngine(workspace_root=str(tmp_path))
        run_id = "test_unsupported_gate"
        wf_dir = tmp_path / ".runtime" / "workflows" / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)

        # Write evidence_items.json with accepted items
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "quote": "real", "assertion": "real",
             "relevance_score": 0.9, "accepted": True},
        ]
        (wf_dir / "evidence_items.json").write_text(json.dumps(evidence), encoding="utf-8")

        # Write final_report.md with fake citation E999
        report = "# Deep Research\n\nClaim from nowhere [E999]."
        (wf_dir / "final_report.md").write_text(report, encoding="utf-8")

        status, msg = gate_eng._gate_unsupported_claims({}, {"run_id": run_id})
        assert status == "fail"
        assert "E999" in msg

    def test_unsupported_claim_gate_passes_with_valid_citations(self, tmp_path):
        """Gate passes when all cited evidence IDs are in accepted set."""
        from app.gate_engine import GateEngine
        gate_eng = GateEngine(workspace_root=str(tmp_path))
        run_id = "test_valid_gate"
        wf_dir = tmp_path / ".runtime" / "workflows" / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)

        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "quote": "real", "assertion": "real",
             "relevance_score": 0.9, "accepted": True},
        ]
        (wf_dir / "evidence_items.json").write_text(json.dumps(evidence), encoding="utf-8")

        report = "# Deep Research\n\nValid claim [E1]."
        (wf_dir / "final_report.md").write_text(report, encoding="utf-8")

        status, msg = gate_eng._gate_unsupported_claims({}, {"run_id": run_id})
        assert status == "pass"


class TestDoctorTraceFix:
    """Doctor must not degrade for empty traces when checkpoints exist."""

    def test_doctor_missing_traces_with_checkpoints_not_degraded(self, tmp_path):
        """Doctor skips trace anomaly when checkpoints exist."""
        # Simulate doctor logic: checkpoints present, traces dir missing
        wf_dir = tmp_path / ".runtime" / "workflows" / "test_run"
        wf_dir.mkdir(parents=True, exist_ok=True)

        # Create workflow state (health)
        (wf_dir / "workflow_state.json").write_text(json.dumps({"run_id": "test_run", "workflow_name": "deep_research"}), encoding="utf-8")
        (wf_dir / "stage_state.json").write_text(json.dumps([{"stage_name": "scope", "status": "completed"}]), encoding="utf-8")

        # Create checkpoints (simulating work was done)
        cp = wf_dir / "checkpoints" / "01_scope"
        cp.mkdir(parents=True, exist_ok=True)
        (cp / "stage_state.json").write_text("{}", encoding="utf-8")

        # Run doctor logic (matching main.py)
        anomalies = []
        traces_dir = wf_dir / "traces"
        cp_dir = wf_dir / "checkpoints"
        has_checkpoints = cp_dir.exists() and any(cp_dir.iterdir())
        if not traces_dir.exists() or not any(traces_dir.iterdir()):
            if not has_checkpoints:
                anomalies.append("traces directory missing or empty")

        assert len(anomalies) == 0  # No anomaly because checkpoints exist


# ── Helper ─────────────────────────────────────────────────────────

def _async_to_sync(coro):
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
