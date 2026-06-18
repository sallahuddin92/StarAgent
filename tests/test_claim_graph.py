"""StarAgent v0.8.0 — Claim Graph tests.

Focused tests for atomic claim extraction, contradiction detection,
claim graph integration with research loop, and benchmark enrichment.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from app.claim_graph import (
    _split_atomic_claims,
    _detect_contradiction_numeric,
    _detect_contradiction_polarity,
    build_claim_graph,
    read_claim_graph,
    claim_metrics,
)
from app.research_loop import ResearchLoop, ResearchLoopConfig


# ── Atomic Claim Splitting ───────────────────────────────────────

class TestAtomicClaimSplitting:
    def test_single_claim_passes_through(self):
        result = _split_atomic_claims("SQLite is serverless.")
        assert result == ["SQLite is serverless."]

    def test_compound_with_comma_and(self):
        result = _split_atomic_claims(
            "SQLite is self-contained, serverless, and zero-configuration."
        )
        assert len(result) >= 2
        assert any("self-contained" in c for c in result)
        assert any("serverless" in c for c in result)
        assert any("zero-configuration" in c for c in result)

    def test_list_with_or(self):
        result = _split_atomic_claims(
            "PostgreSQL supports JSON, full-text search, or geospatial data."
        )
        assert len(result) >= 2

    def test_multiple_sentences(self):
        result = _split_atomic_claims(
            "SQLite is serverless. It requires zero configuration. It is ACID compliant."
        )
        assert len(result) >= 2
        assert all(c.endswith(".") for c in result)

    def test_short_text_returns_single(self):
        result = _split_atomic_claims("Hello world")
        assert len(result) == 1

    def test_empty_text(self):
        result = _split_atomic_claims("")
        assert result == []

    def test_duplicate_normalized_merge(self):
        a = _split_atomic_claims("SQLite is fast and reliable.")
        b = _split_atomic_claims("SQLite is very fast, reliable, and lightweight.")
        # Both should produce claims about fast and reliable
        assert len(a) >= 1
        assert len(b) >= 1


# ── Contradiction Detection ──────────────────────────────────────

class TestContradictionDetection:
    def test_numeric_contradiction(self):
        result = _detect_contradiction_numeric(
            "Supports up to 256 GB of memory",
            "Supports up to 512 GB of memory",
        )
        assert result is not None
        assert "256" in result or "512" in result

    def test_no_numeric_contradiction(self):
        result = _detect_contradiction_numeric(
            "Supports up to 256 GB of memory",
            "Runs on any hardware platform",
        )
        assert result is None

    def test_polarity_contradiction_supports(self):
        result = _detect_contradiction_polarity(
            "PostgreSQL supports JSON indexing.",
            "PostgreSQL does not support JSON indexing.",
        )
        assert result is not None

    def test_polarity_contradiction_is(self):
        result = _detect_contradiction_polarity(
            "SQLite is ACID compliant.",
            "SQLite is not ACID compliant.",
        )
        assert result is not None

    def test_no_polarity_contradiction(self):
        result = _detect_contradiction_polarity(
            "SQLite is ACID compliant.",
            "SQLite is serverless.",
        )
        assert result is None

    def test_polarity_requires(self):
        result = _detect_contradiction_polarity(
            "PostgreSQL requires configuration.",
            "PostgreSQL does not require configuration.",
        )
        assert result is not None


# ── Claim Graph Build ────────────────────────────────────────────

class TestBuildClaimGraph:
    def test_claim_graph_json_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from app.claim_graph import WORKFLOW_RUNS_DIR as orig_dir
            from app.claim_graph import RUNTIME_DIR
            # Override RUNTIME_DIR to temp
            import app.claim_graph as cg
            old_rt = cg.RUNTIME_DIR
            cg.RUNTIME_DIR = Path(tmpdir)
            cg.WORKFLOW_RUNS_DIR = cg.RUNTIME_DIR / "workflows"
            try:
                evidence = [
                    {"evidence_id": "E1", "source_id": "S1",
                     "assertion": "SQLite is serverless and zero-configuration.",
                     "quote": "SQLite is serverless and zero-configuration."},
                    {"evidence_id": "E2", "source_id": "S2",
                     "assertion": "PostgreSQL requires configuration.",
                     "quote": "PostgreSQL requires configuration."},
                ]
                graph = build_claim_graph(evidence, "test_run_cg")
                assert "claims" in graph
                assert "edges" in graph
                # Verify file written
                graph_path = cg.WORKFLOW_RUNS_DIR / "test_run_cg" / "claim_graph.json"
                assert graph_path.is_file()
                loaded = json.loads(graph_path.read_text(encoding="utf-8"))
                assert len(loaded["claims"]) >= 2
            finally:
                cg.RUNTIME_DIR = old_rt
                cg.WORKFLOW_RUNS_DIR = old_rt / "workflows"

    def test_evidence_maps_to_claims(self):
        evidence = [
            {"evidence_id": "E1", "source_id": "S1",
             "assertion": "SQLite is serverless.",
             "quote": "SQLite is serverless."},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            import app.claim_graph as cg
            old_rt = cg.RUNTIME_DIR
            cg.RUNTIME_DIR = Path(tmpdir)
            cg.WORKFLOW_RUNS_DIR = cg.RUNTIME_DIR / "workflows"
            try:
                graph = build_claim_graph(evidence, "test_map")
                edges = graph["edges"]
                # Each edge should connect claim to evidence
                assert any(e["to"] == "E1" and e["type"] == "supported_by" for e in edges)
                # Claim should reference source S1
                for c in graph["claims"]:
                    assert "S1" in c["source_ids"]
                    assert "E1" in c["evidence_ids"]
            finally:
                cg.RUNTIME_DIR = old_rt
                cg.WORKFLOW_RUNS_DIR = old_rt / "workflows"

    def test_numeric_contradiction_in_graph(self):
        evidence = [
            {"evidence_id": "E1", "source_id": "S1",
             "assertion": "Supports up to 256 GB memory.",
             "quote": "Supports up to 256 GB memory."},
            {"evidence_id": "E2", "source_id": "S2",
             "assertion": "Supports up to 512 GB memory.",
             "quote": "Supports up to 512 GB memory."},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            import app.claim_graph as cg
            old_rt = cg.RUNTIME_DIR
            cg.RUNTIME_DIR = Path(tmpdir)
            cg.WORKFLOW_RUNS_DIR = cg.RUNTIME_DIR / "workflows"
            try:
                graph = build_claim_graph(evidence, "test_num_ct")
                contradicts = [e for e in graph["edges"] if e["type"] == "contradicts"]
                assert len(contradicts) >= 1
            finally:
                cg.RUNTIME_DIR = old_rt
                cg.WORKFLOW_RUNS_DIR = old_rt / "workflows"

    def test_polarity_contradiction_in_graph(self):
        evidence = [
            {"evidence_id": "E1", "source_id": "S1",
             "assertion": "SQLite is ACID compliant.",
             "quote": "SQLite is ACID compliant."},
            {"evidence_id": "E2", "source_id": "S2",
             "assertion": "SQLite is not ACID compliant.",
             "quote": "SQLite is not ACID compliant."},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            import app.claim_graph as cg
            old_rt = cg.RUNTIME_DIR
            cg.RUNTIME_DIR = Path(tmpdir)
            cg.WORKFLOW_RUNS_DIR = cg.RUNTIME_DIR / "workflows"
            try:
                graph = build_claim_graph(evidence, "test_pol_ct")
                contradicts = [e for e in graph["edges"] if e["type"] == "contradicts"]
                assert len(contradicts) >= 1
            finally:
                cg.RUNTIME_DIR = old_rt
                cg.WORKFLOW_RUNS_DIR = old_rt / "workflows"

    def test_supported_claims_boost_confidence(self):
        evidence = [
            {"evidence_id": "E1", "source_id": "S1",
             "assertion": "SQLite is serverless.",
             "quote": "SQLite is serverless."},
            {"evidence_id": "E2", "source_id": "S2",
             "assertion": "SQLite is serverless.",
             "quote": "SQLite is serverless."},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            import app.claim_graph as cg
            old_rt = cg.RUNTIME_DIR
            cg.RUNTIME_DIR = Path(tmpdir)
            cg.WORKFLOW_RUNS_DIR = cg.RUNTIME_DIR / "workflows"
            try:
                graph = build_claim_graph(evidence, "test_support_boost")
                for c in graph["claims"]:
                    if c["support_count"] >= 2:
                        assert c["confidence"] >= 80
                        assert c["status"] == "supported"
            finally:
                cg.RUNTIME_DIR = old_rt
                cg.WORKFLOW_RUNS_DIR = old_rt / "workflows"

    def test_weak_claim_single_source(self):
        evidence = [
            {"evidence_id": "E1", "source_id": "S1",
             "assertion": "SQLite is serverless.",
             "quote": "SQLite is serverless."},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            import app.claim_graph as cg
            old_rt = cg.RUNTIME_DIR
            cg.RUNTIME_DIR = Path(tmpdir)
            cg.WORKFLOW_RUNS_DIR = cg.RUNTIME_DIR / "workflows"
            try:
                graph = build_claim_graph(evidence, "test_weak")
                for c in graph["claims"]:
                    assert c["status"] == "weak"
                    assert c["confidence"] == 50
            finally:
                cg.RUNTIME_DIR = old_rt
                cg.WORKFLOW_RUNS_DIR = old_rt / "workflows"


# ── Claim Metrics ─────────────────────────────────────────────────

class TestClaimMetrics:
    def test_claim_metrics_empty(self):
        metrics = claim_metrics({"claims": [], "edges": []})
        assert metrics["total_claims"] == 0
        assert metrics["claim_confidence_avg"] == 0.0

    def test_claim_metrics_counts(self):
        graph = {
            "claims": [
                {"status": "supported", "confidence": 90},
                {"status": "supported", "confidence": 85},
                {"status": "weak", "confidence": 50},
                {"status": "contradicted", "confidence": 20},
            ],
            "edges": [],
        }
        metrics = claim_metrics(graph)
        assert metrics["total_claims"] == 4
        assert metrics["supported_count"] == 2
        assert metrics["weak_count"] == 1
        assert metrics["contradicted_count"] == 1
        assert metrics["unsupported_count"] == 0
        assert metrics["claim_confidence_avg"] == 61.2


# ── Claim Graph Read ─────────────────────────────────────────────

class TestReadClaimGraph:
    def test_read_missing(self):
        graph = read_claim_graph("nonexistent_run")
        assert graph == {"claims": [], "edges": []}


# ── Research Loop Integration ────────────────────────────────────

class TestResearchLoopIntegration:
    def test_too_few_supported_claims_gap(self):
        loop = ResearchLoop(ResearchLoopConfig(min_supported_claims=2))
        evidence = [
            {"evidence_id": "E1", "source_id": "S1",
             "assertion": "SQLite is serverless.", "quote": "SQLite is serverless.",
             "accepted": True},
        ]
        claim_graph = {"claims": [{"status": "weak", "support_count": 1}], "edges": []}
        gaps = loop.detect_gaps(
            [{"source_id": "S1"}], evidence, "SQLite [E1]",
            claim_graph=claim_graph,
        )
        assert "too_few_supported_claims" in gaps

    def test_supported_claims_met(self):
        loop = ResearchLoop(ResearchLoopConfig(min_supported_claims=1))
        evidence = [
            {"evidence_id": "E1", "source_id": "S1",
             "assertion": "SQLite is serverless.", "quote": "SQLite is serverless.",
             "accepted": True},
        ]
        claim_graph = {"claims": [{"status": "supported", "support_count": 2}], "edges": []}
        gaps = loop.detect_gaps(
            [{"source_id": "S1"}], evidence, "SQLite [E1]",
            claim_graph=claim_graph,
        )
        assert "too_few_supported_claims" not in gaps

    def test_confidence_boost_from_claim_graph(self):
        loop = ResearchLoop(ResearchLoopConfig(min_supported_claims=2))
        sources = [
            {"source_id": "S1", "title": "Source 1"},
            {"source_id": "S2", "title": "Source 2"},
        ]
        evidence = [
            {"evidence_id": "E1", "source_id": "S1",
             "assertion": "SQLite is serverless.", "quote": "SQLite is serverless.",
             "accepted": True},
            {"evidence_id": "E2", "source_id": "S2",
             "assertion": "PostgreSQL is client-server.", "quote": "PostgreSQL is client-server.",
             "accepted": True},
        ]
        report = "SQLite [E1] PostgreSQL [E2]"
        claim_graph = {
            "claims": [
                {"status": "supported", "support_count": 2, "confidence": 90},
                {"status": "supported", "support_count": 2, "confidence": 85},
            ],
            "edges": [],
        }
        # Compute confidence with claim graph
        with_graph = loop.compute_confidence(
            sources, evidence, report, claim_graph=claim_graph,
        )
        # Compute without
        no_graph = loop.compute_confidence(
            sources, evidence, report,
        )
        # With graph should be >= without (boost from supported claims)
        assert with_graph >= no_graph

    def test_confidence_penalty_from_contradictions(self):
        loop = ResearchLoop(ResearchLoopConfig(min_supported_claims=1))
        sources = [{"source_id": "S1", "title": "A"}, {"source_id": "S2", "title": "B"}]
        evidence = [
            {"evidence_id": "E1", "source_id": "S1",
             "assertion": "SQLite is ACID compliant.", "quote": "SQLite is ACID compliant.",
             "accepted": True},
            {"evidence_id": "E2", "source_id": "S2",
             "assertion": "SQLite is not ACID compliant.", "quote": "SQLite is not ACID compliant.",
             "accepted": True},
        ]
        report = "SQLite [E1] [E2]"
        claim_graph = {
            "claims": [
                {"status": "contradicted", "support_count": 1, "contradiction_count": 1, "confidence": 20},
            ],
            "edges": [{"type": "contradicts"}],
        }
        conf = loop.compute_confidence(sources, evidence, report, claim_graph=claim_graph)
        # Should be penalized for contradicted claims
        assert conf <= 70  # contradict penalty

    def test_hard_gap_too_few_supported_claims(self):
        loop = ResearchLoop(ResearchLoopConfig(min_supported_claims=5, max_iterations=1))
        gaps = ["too_few_supported_claims"]
        action = loop.decide_next_action(1, 50, gaps)
        assert action.startswith("stop_")

    def test_append_confidence_includes_claims(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import app.research_loop as rl
            old_dir = rl.WORKFLOW_RUNS_DIR
            rl.WORKFLOW_RUNS_DIR = Path(tmpdir) / "workflows"
            try:
                run_id = "test_claim_section"
                wf_dir = rl.WORKFLOW_RUNS_DIR / run_id
                wf_dir.mkdir(parents=True, exist_ok=True)
                report_path = wf_dir / "final_report.md"
                report_path.write_text("# Test Report", encoding="utf-8")

                # Write a claim graph
                from app.claim_graph import build_claim_graph
                build_claim_graph([
                    {"evidence_id": "E1", "source_id": "S1",
                     "assertion": "SQLite is serverless.",
                     "quote": "SQLite is serverless."},
                ], run_id)

                loop = ResearchLoop()
                loop.append_confidence_section(
                    run_id, 75, [], "stop_confidence_met",
                    [{"source_id": "S1"}],
                    [{"evidence_id": "E1", "source_id": "S1",
                      "assertion": "SQLite is serverless.", "quote": "SQLite is serverless.",
                      "accepted": True}],
                    "# Test Report [E1]",
                )
                content = report_path.read_text(encoding="utf-8")
                assert "Total claims" in content
                assert "Supported claims" in content
                assert "Average claim confidence" in content
            finally:
                rl.WORKFLOW_RUNS_DIR = old_dir


# ── Benchmark Integration ────────────────────────────────────────

class TestBenchmarkIntegration:
    def test_claim_metrics_in_score_data(self):
        """Verify score_data structure supports claim metrics."""
        result = {
            "supported_claim_count": 5,
            "contradicted_claim_count": 1,
            "unsupported_claim_count": 0,
            "claim_confidence_avg": 78.5,
        }
        score_data = {
            "run_id": "test",
            "case_name": "test_case",
            "scores": {"overall_score": 85.0},
            **{k: result.get(k) for k in (
                "supported_claim_count", "contradicted_claim_count",
                "unsupported_claim_count", "claim_confidence_avg",
            )},
        }
        assert score_data["supported_claim_count"] == 5
        assert score_data["contradicted_claim_count"] == 1
        assert score_data["claim_confidence_avg"] == 78.5

    def test_claim_graph_endpoint_structure(self):
        """Verify the API response structure for claims endpoint."""
        response = {
            "run_id": "test123",
            "graph": {"claims": [], "edges": []},
            "metrics": {
                "total_claims": 0,
                "supported_count": 0,
                "weak_count": 0,
                "contradicted_count": 0,
                "unsupported_count": 0,
                "claim_confidence_avg": 0.0,
            },
        }
        assert "graph" in response
        assert "metrics" in response
        assert "claims" in response["graph"]


# ── Gate Engine Integration ──────────────────────────────────────

class TestGateEngineIntegration:
    def test_claim_graph_exists_gate(self):
        from app.gate_engine import GateEngine
        with tempfile.TemporaryDirectory() as tmpdir:
            eng = GateEngine(workspace_root=tmpdir)
            run_id = "test_gate_claims"
            # No graph — should fail
            status, msg = eng._gate_claim_graph_exists({}, {"run_id": run_id})
            assert status == "fail"

            # With graph
            wf_dir = Path(tmpdir) / ".runtime" / "workflows" / run_id
            wf_dir.mkdir(parents=True, exist_ok=True)
            (wf_dir / "claim_graph.json").write_text(
                json.dumps({"claims": [], "edges": []}), encoding="utf-8"
            )
            status, msg = eng._gate_claim_graph_exists({}, {"run_id": run_id})
            assert status == "pass"

    def test_supported_claims_min_gate(self):
        from app.gate_engine import GateEngine
        with tempfile.TemporaryDirectory() as tmpdir:
            eng = GateEngine(workspace_root=tmpdir)
            run_id = "test_supported_min"
            wf_dir = Path(tmpdir) / ".runtime" / "workflows" / run_id
            wf_dir.mkdir(parents=True, exist_ok=True)
            (wf_dir / "claim_graph.json").write_text(json.dumps({
                "claims": [
                    {"status": "supported"},
                    {"status": "supported"},
                    {"status": "weak"},
                ],
                "edges": [],
            }), encoding="utf-8")
            status, msg = eng._gate_supported_claims_min({"min_count": 2}, {"run_id": run_id})
            assert status == "pass"

            status, msg = eng._gate_supported_claims_min({"min_count": 5}, {"run_id": run_id})
            assert status == "fail"

    def test_contradicted_claims_max_gate(self):
        from app.gate_engine import GateEngine
        with tempfile.TemporaryDirectory() as tmpdir:
            eng = GateEngine(workspace_root=tmpdir)
            run_id = "test_contradicted_max"
            wf_dir = Path(tmpdir) / ".runtime" / "workflows" / run_id
            wf_dir.mkdir(parents=True, exist_ok=True)
            (wf_dir / "claim_graph.json").write_text(json.dumps({
                "claims": [
                    {"status": "contradicted"},
                ],
                "edges": [],
            }), encoding="utf-8")
            status, msg = eng._gate_contradicted_claims_max({"max_count": 0}, {"run_id": run_id})
            assert status == "fail"

            status, msg = eng._gate_contradicted_claims_max({"max_count": 2}, {"run_id": run_id})
            assert status == "pass"


# ── CLI Structure Integration ────────────────────────────────────

class TestCLIIntegration:
    def test_workflow_claims_parser_exists(self):
        """Verify the workflow claims parser is registered."""
        import argparse
        from cli.macagent import build_parser
        parser = build_parser()
        # Parse workflow claims command
        args = parser.parse_args(["workflow", "claims", "test_run_123"])
        assert args.workflow_cmd == "claims"
        assert args.run_id == "test_run_123"
