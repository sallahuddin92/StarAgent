"""v0.7.0 Autonomous Research Loop tests — confidence, gaps, decisions, state."""

import os
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.research_loop import ResearchLoop, ResearchLoopConfig, WORKFLOW_RUNS_DIR


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def loop():
    return ResearchLoop(ResearchLoopConfig(
        max_iterations=3,
        min_sources=3,
        min_evidence=5,
        min_confidence=75,
    ))


@pytest.fixture
def sample_sources():
    return [
        {"source_id": "S1", "title": "Source One"},
        {"source_id": "S2", "title": "Source Two"},
        {"source_id": "S3", "title": "Source Three"},
    ]


@pytest.fixture
def sample_evidence():
    return [
        {"evidence_id": "E1", "source_id": "S1", "accepted": True},
        {"evidence_id": "E2", "source_id": "S1", "accepted": True},
        {"evidence_id": "E3", "source_id": "S2", "accepted": True},
        {"evidence_id": "E4", "source_id": "S2", "accepted": True},
        {"evidence_id": "E5", "source_id": "S3", "accepted": True},
    ]


@pytest.fixture
def sample_good_report():
    return (
        "# Deep Research: Test\n"
        "## Summary\n"
        "Some findings [E1].\n"
        "More findings [E2].\n"
        "## Key Findings\n"
        "- Claim one [E3].\n"
        "- Claim two [E4].\n"
        "- Claim three [E5].\n"
    )


@pytest.fixture
def sample_contradictions():
    return [
        {"contradiction_id": "C1", "status": "resolved", "description": "Minor conflict resolved."},
    ]


# ── Confidence Scoring Tests ───────────────────────────────────────

class TestConfidenceScoring:
    """Confidence score increases with more evidence, meets thresholds."""

    def test_confidence_increases_with_more_evidence(self, loop):
        sources = [{"source_id": "S1"}] * 3
        report = "## Summary\nline [E1]\n## Key Findings\nline [E2]\n"

        # Low evidence
        low = loop.compute_confidence(sources, [
            {"evidence_id": "E1", "source_id": "S1", "accepted": True},
        ], report)
        # High evidence
        high = loop.compute_confidence(sources, [
            {"evidence_id": "E1", "source_id": "S1", "accepted": True},
            {"evidence_id": "E2", "source_id": "S2", "accepted": True},
            {"evidence_id": "E3", "source_id": "S3", "accepted": True},
            {"evidence_id": "E4", "source_id": "S1", "accepted": True},
            {"evidence_id": "E5", "source_id": "S2", "accepted": True},
        ], report)
        assert high >= low
        assert 0 <= low <= 100
        assert 0 <= high <= 100

    def test_confidence_min_sources_met(self, loop):
        sources = [{"source_id": f"S{i}"} for i in range(1, 6)]  # 5 sources
        evidence = [{"evidence_id": f"E{i}", "source_id": "S1", "accepted": True} for i in range(1, 6)]
        report = "## Summary\n" + " ".join(f"[E{i}]" for i in range(1, 6))

        score = loop.compute_confidence(sources, evidence, report)
        # With 5 sources (>=3), 5 evidence (>=5), full citation coverage, all from 1 source
        # base: 92, but capped at 65 (single source in evidence)
        assert score == 65

    def test_confidence_zero_with_no_data(self, loop):
        score = loop.compute_confidence([], [], "")
        # No usable sources → cap at 0
        assert score == 0

    def test_confidence_with_expected_claims(self, loop):
        sources = [{"source_id": "S1"}] * 5
        evidence = [{"evidence_id": f"E{i}", "source_id": "S1", "accepted": True} for i in range(1, 6)]
        report = "## Summary\nSQLite is serverless [E1]. PostgreSQL uses client-server [E2]."
        expected = {"required_claims": ["SQLite is serverless", "PostgreSQL uses client-server"]}

        score = loop.compute_confidence(sources, evidence, report, expected=expected)
        assert 0 <= score <= 100

    def test_confidence_with_unresolved_contradictions(self, loop):
        sources = [{"source_id": "S1"}] * 5
        evidence = [{"evidence_id": f"E{i}", "source_id": "S1", "accepted": True} for i in range(1, 6)]
        report = "## Summary\n" + " ".join(f"[E{i}]" for i in range(1, 6))
        contradictions = [{"contradiction_id": "C1", "status": "unresolved", "description": "Conflict."}]

        score = loop.compute_confidence(sources, evidence, report, contradictions=contradictions)
        # Contradiction penalty: 10 -> 0
        assert score <= 90

    def test_confidence_all_sources_failed(self, loop):
        """When all sources have errors, confidence should be low (<= 20)."""
        sources = [
            {"source_id": "S1", "error": "file not found"},
            {"source_id": "S2", "error": "connection refused"},
            {"source_id": "S3", "error": "timeout"},
        ]
        score = loop.compute_confidence(sources, [], "")
        assert score <= 20

    def test_confidence_no_evidence(self, loop):
        """When no evidence items exist, confidence should be low (<= 20)."""
        sources = [{"source_id": "S1"}, {"source_id": "S2"}, {"source_id": "S3"}]
        score = loop.compute_confidence(sources, [], "")
        assert score <= 20

    def test_confidence_mixed_sources(self, loop):
        """Some failed sources should not penalize usable ones."""
        sources = [
            {"source_id": "S1", "title": "Good source"},
            {"source_id": "S2", "error": "not found"},
            {"source_id": "S3", "error": "timeout"},
        ]
        evidence = [{"evidence_id": "E1", "source_id": "S1", "accepted": True}]
        report = "## Summary\n[E1]\n"
        score = loop.compute_confidence(sources, evidence, report)
        assert score > 20  # should be well above the failure cap
        assert score <= 100

    def test_confidence_capped_when_evidence_below_min(self, loop):
        """When evidence_count < min_evidence, confidence <= 60."""
        sources = [{"source_id": "S1"}, {"source_id": "S2"}, {"source_id": "S3"}]
        evidence = [{"evidence_id": "E1", "source_id": "S1", "accepted": True},
                    {"evidence_id": "E2", "source_id": "S1", "accepted": True}]
        report = "## Summary\n[E1] [E2]\n"
        score = loop.compute_confidence(sources, evidence, report)
        assert score <= 60

    def test_confidence_capped_single_source(self, loop):
        """When all evidence is from one source, confidence <= 65."""
        sources = [{"source_id": "S1"}, {"source_id": "S2"}, {"source_id": "S3"},
                   {"source_id": "S4"}, {"source_id": "S5"}]
        evidence = [{"evidence_id": f"E{i}", "source_id": "S1", "accepted": True} for i in range(1, 6)]
        report = "## Summary\n" + " ".join(f"[E{i}]" for i in range(1, 6))
        score = loop.compute_confidence(sources, evidence, report)
        assert score <= 65

    def test_low_citation_coverage_caps_confidence(self, loop):
        """Evidence coverage < 50% caps confidence at 70."""
        sources = [{"source_id": "S1"}, {"source_id": "S2"}, {"source_id": "S3"}]
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "accepted": True},
            {"evidence_id": "E2", "source_id": "S2", "accepted": True},
            {"evidence_id": "E3", "source_id": "S3", "accepted": True},
            {"evidence_id": "E4", "source_id": "S1", "accepted": True},
            {"evidence_id": "E5", "source_id": "S2", "accepted": True},
        ]
        # Only 2/5 evidence items cited = 40% coverage
        report = "## Summary [E1] [E2]"
        score = loop.compute_confidence(sources, evidence, report)
        assert score <= 70
        assert score >= 0

    def test_coverage_60_pct_caps_at_85(self, loop):
        """Evidence coverage between 50-75% caps confidence at 85."""
        sources = [{"source_id": "S1"}, {"source_id": "S2"}, {"source_id": "S3"}]
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "accepted": True},
            {"evidence_id": "E2", "source_id": "S2", "accepted": True},
            {"evidence_id": "E3", "source_id": "S3", "accepted": True},
            {"evidence_id": "E4", "source_id": "S1", "accepted": True},
            {"evidence_id": "E5", "source_id": "S2", "accepted": True},
        ]
        # 3/5 evidence items cited = 60% coverage, < 75% triggers 85 cap
        report = "## Summary [E1] [E2] [E3]"
        score = loop.compute_confidence(sources, evidence, report)
        # 60% < 75% so 85 cap applies
        assert score <= 85

    def test_full_coverage_no_citation_cap(self, loop):
        """Evidence coverage >= 75% does not trigger coverage cap."""
        sources = [{"source_id": "S1"}, {"source_id": "S2"}, {"source_id": "S3"},
                   {"source_id": "S4"}, {"source_id": "S5"}]
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "accepted": True},
            {"evidence_id": "E2", "source_id": "S2", "accepted": True},
            {"evidence_id": "E3", "source_id": "S3", "accepted": True},
            {"evidence_id": "E4", "source_id": "S4", "accepted": True},
            {"evidence_id": "E5", "source_id": "S5", "accepted": True},
        ]
        # 5/5 cited = 100% coverage, >= 75% so no coverage cap
        report = "## Summary [E1] [E2] [E3] [E4] [E5]"
        score = loop.compute_confidence(sources, evidence, report)
        # 5 sources, 5 evidence, all cited — base score is high
        # No coverage cap, no evidence cap (5 >= 5)
        assert score > 85  # above 85 cap threshold means no coverage cap applied


# ── Gap Detection Tests ────────────────────────────────────────────

class TestGapDetection:
    """Gap detection identifies all specified gap types."""

    def test_too_few_sources(self, loop):
        gaps = loop.detect_gaps(
            [{"source_id": "S1"}],  # 1 source < min 3
            [{"evidence_id": "E1", "source_id": "S1", "accepted": True}],
            "## Summary\n[E1]\n",
        )
        assert "too_few_sources" in gaps

    def test_too_few_evidence(self, loop):
        sources = [{"source_id": "S1"}] * 3
        gaps = loop.detect_gaps(
            sources,
            [{"evidence_id": "E1", "source_id": "S1", "accepted": True}],  # 1 < min 5
            "## Summary\n[E1]\n",
        )
        assert "too_few_evidence" in gaps

    def test_missing_required_claims(self, loop):
        sources = [{"source_id": "S1"}] * 3
        evidence = [{"evidence_id": f"E{i}", "source_id": "S1", "accepted": True} for i in range(1, 6)]
        report = "## Summary\nNothing about the required claims.\n"
        expected = {"required_claims": ["SQLite is serverless", "PostgreSQL uses client-server"]}

        gaps = loop.detect_gaps(sources, evidence, report, expected=expected)
        assert "missing_required_claims" in gaps

    def test_unsupported_claims(self, loop):
        sources = [{"source_id": "S1"}] * 3
        evidence = [{"evidence_id": "E1", "source_id": "S1", "accepted": True}]
        report = "## Summary\n[E1] and [E99]\n"  # E99 doesn't exist

        gaps = loop.detect_gaps(sources, evidence, report)
        assert "unsupported_claims" in gaps

    def test_low_source_diversity(self, loop):
        sources = [{"source_id": "S1"}, {"source_id": "S2"}, {"source_id": "S3"}]
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "accepted": True},
            {"evidence_id": "E2", "source_id": "S1", "accepted": True},
            {"evidence_id": "E3", "source_id": "S1", "accepted": True},
        ]
        report = "## Summary\n[E1] [E2] [E3]\n"

        gaps = loop.detect_gaps(sources, evidence, report)
        assert "low_source_diversity" in gaps

    def test_contradictions_unresolved(self, loop):
        sources = [{"source_id": "S1"}] * 3
        evidence = [{"evidence_id": f"E{i}", "source_id": "S1", "accepted": True} for i in range(1, 6)]
        report = "## Summary\n" + " ".join(f"[E{i}]" for i in range(1, 6))
        contradictions = [{"contradiction_id": "C1", "status": "unresolved"}]

        gaps = loop.detect_gaps(sources, evidence, report, contradictions=contradictions)
        assert "contradictions_unresolved" in gaps

    def test_resolved_contradictions_no_gap(self, loop):
        sources = [{"source_id": "S1"}] * 3
        evidence = [{"evidence_id": f"E{i}", "source_id": "S1", "accepted": True} for i in range(1, 6)]
        report = "## Summary\n" + " ".join(f"[E{i}]" for i in range(1, 6))
        contradictions = [{"contradiction_id": "C1", "status": "resolved"}]

        gaps = loop.detect_gaps(sources, evidence, report, contradictions=contradictions)
        assert "contradictions_unresolved" not in gaps

    def test_low_citation_coverage(self, loop):
        sources = [{"source_id": "S1"}] * 3
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "accepted": True},
            {"evidence_id": "E2", "source_id": "S1", "accepted": True},
            {"evidence_id": "E3", "source_id": "S2", "accepted": True},
            {"evidence_id": "E4", "source_id": "S2", "accepted": True},
            {"evidence_id": "E5", "source_id": "S3", "accepted": True},
        ]
        report = "## Summary\n[E1]\n"  # only 1/5 cited

        gaps = loop.detect_gaps(sources, evidence, report)
        assert "low_citation_coverage" in gaps

    def test_no_gaps_with_good_data(self, loop, sample_sources, sample_evidence, sample_good_report):
        gaps = loop.detect_gaps(sample_sources, sample_evidence, sample_good_report)
        assert gaps == []

    def test_all_sources_failed_gap(self, loop):
        """When all sources have errors, detect all_sources_failed and too_few_sources."""
        sources = [
            {"source_id": "S1", "error": "not found"},
            {"source_id": "S2", "error": "timeout"},
        ]
        gaps = loop.detect_gaps(sources, [], "")
        assert "all_sources_failed" in gaps
        assert "too_few_sources" in gaps

    def test_mixed_sources_no_all_failed_gap(self, loop):
        """When some sources succeed, all_sources_failed should not appear."""
        sources = [
            {"source_id": "S1", "title": "Good source"},
            {"source_id": "S2", "error": "not found"},
        ]
        evidence = [{"evidence_id": "E1", "source_id": "S1", "accepted": True}]
        report = "## Summary\n[E1]\n"
        gaps = loop.detect_gaps(sources, evidence, report)
        assert "all_sources_failed" not in gaps


# ── Decision Logic Tests ───────────────────────────────────────────

class TestDecideNextAction:
    """Decision logic for stop/continue."""

    def test_stop_confidence_met(self, loop):
        action = loop.decide_next_action(1, 80, [], prev_evidence_count=0, current_evidence_count=5)
        assert action == "stop_confidence_met"

    def test_no_new_evidence_stops(self, loop):
        action = loop.decide_next_action(2, 50, ["too_few_evidence"],
                                          prev_evidence_count=5, current_evidence_count=5)
        assert action == "stop_no_new_evidence"

    def test_max_iterations_stops(self, loop):
        action = loop.decide_next_action(3, 50, ["too_few_sources"],
                                          prev_evidence_count=0, current_evidence_count=2)
        assert action == "stop_max_iterations"

    def test_continue_when_improving(self, loop):
        action = loop.decide_next_action(1, 50, ["too_few_evidence"],
                                          prev_evidence_count=2, current_evidence_count=5)
        assert action == "continue"

    def test_continue_with_new_evidence(self, loop):
        action = loop.decide_next_action(2, 60, ["too_few_sources"],
                                          prev_evidence_count=2, current_evidence_count=4)
        assert action == "continue"

    def test_hard_gap_blocks_confidence_met(self, loop):
        """Hard gap with high confidence should not return stop_confidence_met."""
        action = loop.decide_next_action(1, 80, ["too_few_evidence"],
                                          prev_evidence_count=1, current_evidence_count=2)
        assert action == "continue"  # improving, so continue

    def test_hard_gap_no_improvement_stops(self, loop):
        """Hard gap with no evidence improvement should stop."""
        action = loop.decide_next_action(2, 60, ["too_few_evidence"],
                                          prev_evidence_count=3, current_evidence_count=3)
        assert action == "stop_no_new_evidence"

    def test_hard_gap_max_iterations(self, loop):
        """Hard gap at max iteration should stop_max_iterations."""
        action = loop.decide_next_action(3, 60, ["too_few_evidence"],
                                          prev_evidence_count=1, current_evidence_count=2)
        assert action == "stop_max_iterations"

    def test_hard_gap_unsupported_claims_blocks(self, loop):
        """unsupported_claims hard gap blocks stop_confidence_met."""
        action = loop.decide_next_action(1, 95, ["unsupported_claims"],
                                          prev_evidence_count=3, current_evidence_count=3)
        assert action != "stop_confidence_met"

    def test_all_sources_failed_blocks_confidence_met(self, loop):
        """all_sources_failed hard gap blocks stop_confidence_met."""
        action = loop.decide_next_action(1, 80, ["all_sources_failed"],
                                          prev_evidence_count=0, current_evidence_count=0)
        assert action != "stop_confidence_met"

    def test_scenario_evidence_1_not_confidence_met(self, loop):
        """With evidence=1, min_evidence=3, min_confidence=75, must not stop_confidence_met."""
        gaps = ["too_few_evidence", "low_source_diversity"]
        action = loop.decide_next_action(1, 76, gaps,
                                          prev_evidence_count=0, current_evidence_count=1)
        assert action == "continue"  # hard gap exists, evidence is improving

    def test_stop_on_no_new_evidence_disabled(self, loop):
        loop.config.stop_on_no_new_evidence = False
        action = loop.decide_next_action(1, 50, ["too_few_evidence"],
                                          prev_evidence_count=5, current_evidence_count=5)
        assert action == "continue"  # would continue instead of stopping

    def test_low_citation_coverage_blocks_stop_confidence_met(self, loop):
        """low_citation_coverage hard gap blocks stop_confidence_met even at high confidence."""
        action = loop.decide_next_action(1, 85, ["low_citation_coverage"],
                                          prev_evidence_count=0, current_evidence_count=5)
        assert action != "stop_confidence_met"
        assert action == "continue"  # evidence improving

    def test_no_low_citation_gap_allows_stop_confidence_met(self, loop):
        """Without low_citation_coverage gap, stop_confidence_met is possible at 80% coverage."""
        action = loop.decide_next_action(1, 80, [],
                                          prev_evidence_count=0, current_evidence_count=5)
        assert action == "stop_confidence_met"

    def test_manual_low_coverage_scenario_not_stop(self, loop):
        """Replicates the bug scenario: 33% coverage, confidence 100, low_citation_coverage gap must block."""
        gaps = ["low_citation_coverage"]
        action = loop.decide_next_action(1, 100, gaps,
                                          prev_evidence_count=0, current_evidence_count=3)
        assert action != "stop_confidence_met"
        assert action == "continue"  # evidence is improving from 0 to 3


# ── Loop State Tests ───────────────────────────────────────────────

class TestLoopState:
    """research_loop.json write/read and confidence section append."""

    def test_write_and_read_loop_state(self, tmp_path, loop):
        import app.research_loop as rl
        original = rl.WORKFLOW_RUNS_DIR
        rl.WORKFLOW_RUNS_DIR = tmp_path / ".runtime" / "workflows"

        iterations = [
            {"iteration": 1, "sources_count": 2, "evidence_count": 3, "confidence": 62,
             "gaps": ["too_few_sources"], "action": "continue"},
        ]
        try:
            path = loop.write_loop_state("test_run", iterations, "stop_confidence_met", 85)
            assert path.is_file()

            state = ResearchLoop.read_loop_state("test_run")
            assert state["run_id"] == "test_run"
            assert len(state["iterations"]) == 1
            assert state["final_decision"] == "stop_confidence_met"
            assert state["final_confidence"] == 85
            assert state["iterations"][0]["confidence"] == 62
        finally:
            rl.WORKFLOW_RUNS_DIR = original

    def test_read_loop_state_missing(self):
        state = ResearchLoop.read_loop_state("nonexistent_run")
        assert state == {}

    def test_write_loop_state_cumulative(self, tmp_path, loop):
        import app.research_loop as rl
        original = rl.WORKFLOW_RUNS_DIR
        rl.WORKFLOW_RUNS_DIR = tmp_path / ".runtime" / "workflows"

        try:
            it1 = [{"iteration": 1, "confidence": 50, "gaps": [], "action": "continue"}]
            loop.write_loop_state("cumulative", it1, "continue", 50)

            it2 = [{"iteration": 1, "confidence": 50, "gaps": [], "action": "continue"},
                   {"iteration": 2, "confidence": 80, "gaps": [], "action": "stop_confidence_met"}]
            loop.write_loop_state("cumulative", it2, "stop_confidence_met", 80)

            state = ResearchLoop.read_loop_state("cumulative")
            assert len(state["iterations"]) == 2
            assert state["final_decision"] == "stop_confidence_met"
            assert state["final_confidence"] == 80
        finally:
            rl.WORKFLOW_RUNS_DIR = original

    def test_append_confidence_section(self, tmp_path, loop):
        import app.research_loop as rl
        original = rl.WORKFLOW_RUNS_DIR
        rl.WORKFLOW_RUNS_DIR = tmp_path / ".runtime" / "workflows"

        wf_dir = tmp_path / ".runtime" / "workflows" / "test_run"
        wf_dir.mkdir(parents=True)
        report_path = wf_dir / "final_report.md"
        report_path.write_text("# Deep Research: Test\n## Summary\nSome content [E1].\n", encoding="utf-8")

        sources = [{"source_id": "S1", "title": "S1"}]
        evidence = [{"evidence_id": "E1", "source_id": "S1", "accepted": True}]
        report = report_path.read_text(encoding="utf-8")

        try:
            loop.append_confidence_section("test_run", 85, ["too_few_sources"],
                                           "stop_confidence_met", sources, evidence, report)
            content = report_path.read_text(encoding="utf-8")
            assert "## Research Confidence" in content
            assert "85/100" in content
            assert "Stop reason: Confidence threshold met" in content
            assert "too_few_sources" in content
            assert "Usable sources: 1" in content
            assert "Failed sources: 0" in content
        finally:
            rl.WORKFLOW_RUNS_DIR = original

    def test_append_confidence_section_includes_cap_reason(self, tmp_path, loop):
        """When citation coverage < 50%, the confidence section includes cap reason."""
        import app.research_loop as rl
        original = rl.WORKFLOW_RUNS_DIR
        rl.WORKFLOW_RUNS_DIR = tmp_path / ".runtime" / "workflows"

        wf_dir = tmp_path / ".runtime" / "workflows" / "cap_reason_run"
        wf_dir.mkdir(parents=True)
        report_path = wf_dir / "final_report.md"
        report_path.write_text("# Deep Research: Test\n## Summary\nSome content [E1].\n", encoding="utf-8")

        sources = [{"source_id": "S1", "title": "S1"}, {"source_id": "S2", "title": "S2"}]
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "accepted": True},
            {"evidence_id": "E2", "source_id": "S2", "accepted": True},
            {"evidence_id": "E3", "source_id": "S1", "accepted": True},
        ]
        report = report_path.read_text(encoding="utf-8")

        try:
            loop.append_confidence_section("cap_reason_run", 60, ["low_citation_coverage"],
                                           "stop_no_new_evidence", sources, evidence, report)
            content = report_path.read_text(encoding="utf-8")
            # 1/3 cited = 33.3% → < 50% → cap reason should mention below 50%
            assert "Confidence cap reason" in content
            assert "below 50%" in content
        finally:
            rl.WORKFLOW_RUNS_DIR = original


# ── Safety Tests ───────────────────────────────────────────────────

class TestLoopSafety:
    """No fake sources, no provider stop."""

    def test_detect_unsupported_claims_no_fakes(self, loop):
        """When report cites only valid evidence, no unsupported_claims gap."""
        sources = [{"source_id": "S1"}] * 3
        evidence = [{"evidence_id": "E1", "source_id": "S1", "accepted": True}]
        report = "## Summary\n[E1]\n"

        gaps = loop.detect_gaps(sources, evidence, report)
        assert "unsupported_claims" not in gaps

    def test_detect_fake_citations(self, loop):
        """Fake E99 is detected as unsupported_claims."""
        sources = [{"source_id": "S1"}] * 3
        evidence = [{"evidence_id": "E1", "source_id": "S1", "accepted": True}]
        report = "## Summary\n[E1] and [E999]\n"

        gaps = loop.detect_gaps(sources, evidence, report)
        assert "unsupported_claims" in gaps

    def test_no_provider_handling(self, loop):
        """With no sources, too_few_sources gap is detected."""
        gaps = loop.detect_gaps([], [], "")
        assert "too_few_sources" in gaps

    def test_empty_evidence_no_crash(self, loop):
        """All methods handle empty evidence gracefully."""
        score = loop.compute_confidence([], [], "")
        assert 0 <= score <= 100

        gaps = loop.detect_gaps([], [], "")
        assert isinstance(gaps, list)


# ── Extraction Helpers Tests ───────────────────────────────────────

class TestExtractionHelpers:

    def test_extract_eids(self, loop):
        assert loop._extract_eids("[E1] and [E2] and text") == ["E1", "E2"]
        assert loop._extract_eids("no citations") == []

    def test_evidence_count(self, loop):
        items = [
            {"evidence_id": "E1", "accepted": True},
            {"evidence_id": "E2", "accepted": False},
            {"evidence_id": "E3", "accepted": True},
        ]
        assert loop._evidence_count(items) == 2

    def test_citation_coverage_pct(self, loop):
        evidence = [
            {"evidence_id": "E1", "accepted": True},
            {"evidence_id": "E2", "accepted": True},
        ]
        report = "## Summary\n[E1]\n"
        pct = loop._citation_coverage_pct(evidence, report)
        assert pct == 50.0

    def test_classify_sources_all_usable(self, loop):
        sources = [
            {"source_id": "S1", "title": "A"},
            {"source_id": "S2", "title": "B"},
        ]
        usable, failed, errors = loop._classify_sources(sources)
        assert len(usable) == 2
        assert len(failed) == 0
        assert errors == []

    def test_classify_sources_all_failed(self, loop):
        sources = [
            {"source_id": "S1", "error": "not found"},
            {"source_id": "S2", "error": "timeout"},
        ]
        usable, failed, errors = loop._classify_sources(sources)
        assert len(usable) == 0
        assert len(failed) == 2
        assert "S1: not found" in errors
        assert "S2: timeout" in errors

    def test_classify_sources_mixed(self, loop):
        sources = [
            {"source_id": "S1", "title": "Good"},
            {"source_id": "S2", "error": "failed"},
        ]
        usable, failed, errors = loop._classify_sources(sources)
        assert len(usable) == 1
        assert len(failed) == 1
        assert usable[0]["source_id"] == "S1"
        assert failed[0]["source_id"] == "S2"


# ── Benchmark Integration Tests ────────────────────────────────────

class TestBenchmarkIntegration:

    def test_loop_state_includes_metadata(self, tmp_path, loop):
        """research_loop.json contains expected schema fields."""
        import app.research_loop as rl
        original = rl.WORKFLOW_RUNS_DIR
        rl.WORKFLOW_RUNS_DIR = tmp_path / ".runtime" / "workflows"

        iterations = [
            {"iteration": 1, "sources_count": 2, "evidence_count": 3, "confidence": 60,
             "gaps": ["too_few_sources", "too_few_evidence"], "action": "continue"},
            {"iteration": 2, "sources_count": 4, "evidence_count": 6, "confidence": 80,
             "gaps": [], "action": "stop_confidence_met"},
        ]
        try:
            loop.write_loop_state("bench_run", iterations, "stop_confidence_met", 80)
            state = ResearchLoop.read_loop_state("bench_run")

            assert "run_id" in state
            assert "iterations" in state
            assert "final_decision" in state
            assert "final_confidence" in state
            assert len(state["iterations"]) == 2

            # Schema for each iteration
            for it in state["iterations"]:
                assert "iteration" in it
                assert "sources_count" in it
                assert "evidence_count" in it
                assert "confidence" in it
                assert "gaps" in it
                assert "action" in it

            # Benchmark-relevant fields
            assert state["final_decision"] == "stop_confidence_met"
            assert state["final_confidence"] == 80
            assert len(state["iterations"]) == 2  # iteration_count
        finally:
            rl.WORKFLOW_RUNS_DIR = original

    def test_source_errors_in_loop_state(self, tmp_path, loop):
        """research_loop.json iteration records contain source error info."""
        import app.research_loop as rl
        original = rl.WORKFLOW_RUNS_DIR
        rl.WORKFLOW_RUNS_DIR = tmp_path / ".runtime" / "workflows"

        iterations = [{
            "iteration": 1,
            "sources_count": 2,
            "failed_sources_count": 2,
            "usable_sources_count": 0,
            "evidence_count": 0,
            "confidence": 0,
            "gaps": ["all_sources_failed", "too_few_sources", "too_few_evidence"],
            "action": "stop_no_provider",
            "source_errors": ["S1: not found", "S2: timeout"],
        }]
        try:
            loop.write_loop_state("src_err_run", iterations, "stop_no_provider", 0)
            state = ResearchLoop.read_loop_state("src_err_run")
            it = state["iterations"][0]
            assert it["failed_sources_count"] == 2
            assert it["usable_sources_count"] == 0
            assert "source_errors" in it
            assert len(it["source_errors"]) == 2
            assert "S1: not found" in it["source_errors"]
        finally:
            rl.WORKFLOW_RUNS_DIR = original

    def test_loop_state_stop_reasons(self, tmp_path, loop):
        """All stop reasons are valid enum values."""
        import app.research_loop as rl
        original = rl.WORKFLOW_RUNS_DIR
        rl.WORKFLOW_RUNS_DIR = tmp_path / ".runtime" / "workflows"

        valid_reasons = {
            "stop_confidence_met",
            "stop_max_iterations",
            "stop_no_new_evidence",
            "stop_no_provider",
        }
        try:
            for reason in valid_reasons:
                loop.write_loop_state(f"run_{reason}", [], reason, 0)
                state = ResearchLoop.read_loop_state(f"run_{reason}")
                assert state["final_decision"] in valid_reasons
        finally:
            rl.WORKFLOW_RUNS_DIR = original
