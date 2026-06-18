"""v0.6.3 Benchmark engine tests — discovery, scoring, regression, CLI."""

import os
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.benchmark_engine import (
    BenchmarkEngine,
    BenchmarkError,
    SCORE_WEIGHTS,
    REGRESSION_THRESHOLD,
    BENCHMARKS_DIR,
    BENCHMARK_RUNS_DIR,
)


# ── Fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def sample_expected():
    return {
        "required_claims": ["SQLite is serverless", "PostgreSQL uses client-server"],
        "forbidden_claims": ["SQLite handles terabytes"],
        "required_citations": True,
        "min_sources": 2,
        "min_evidence": 2,
        "required_sections": ["Summary", "Key Findings", "Evidence Table", "Limitations", "Citations", "Source List"],
    }


@pytest.fixture
def sample_evidence():
    return [
        {"evidence_id": "E1", "source_id": "S1", "quote": "SQLite is serverless", "assertion": "SQLite is serverless", "accepted": True},
        {"evidence_id": "E2", "source_id": "S2", "quote": "PostgreSQL uses client-server", "assertion": "PostgreSQL uses client-server", "accepted": True},
        {"evidence_id": "E3", "source_id": "S1", "quote": "SQLite is zero-config", "assertion": "SQLite is zero-config", "accepted": False},
    ]


@pytest.fixture
def sample_sources():
    return [
        {"source_id": "S1", "title": "SQLite Overview", "url": "file:///sources/sqlite.md"},
        {"source_id": "S2", "title": "PostgreSQL Overview", "url": "file:///sources/pg.md"},
    ]


@pytest.fixture
def sample_good_report():
    return """# Deep Research: Test Question

## Summary
A summary of findings.

## Key Findings
- SQLite is serverless [E1].
- PostgreSQL uses client-server architecture [E2].

## Evidence Table
| E1 | S1 | quote | assertion |

## Limitations
None.

## Citations
- [E1]: direct quote from S1

## Source List
- [S1]: SQLite Overview
"""


# ── Discovery Tests ────────────────────────────────────────────────

class TestBenchmarkDiscovery:
    def test_benchmark_discovery(self):
        """BenchmarkEngine.list_cases() finds local_vs_cloud and sqlite_vs_postgres."""
        cases = BenchmarkEngine.list_cases()
        assert "local_vs_cloud" in cases
        assert "sqlite_vs_postgres" in cases
        assert len(cases) >= 2

    def test_load_case_returns_expected_structure(self):
        """load_case returns question, sources, expected, gold_report."""
        case = BenchmarkEngine.load_case("local_vs_cloud")
        assert "question" in case
        assert "sources" in case
        assert len(case["sources"]) >= 2
        assert "expected" in case
        assert "required_claims" in case["expected"]
        assert "required_sections" in case["expected"]


# ── Expected.json Validation ───────────────────────────────────────

class TestExpectedValidation:
    def test_load_case_validates_expected_json(self):
        """Valid expected.json loads without error."""
        case = BenchmarkEngine.load_case("local_vs_cloud")
        expected = case["expected"]
        assert isinstance(expected["required_claims"], list)
        assert isinstance(expected["forbidden_claims"], list)
        assert isinstance(expected["required_sections"], list)

    def test_invalid_expected_missing_key_raises(self):
        """Missing required key raises BenchmarkError."""
        with pytest.raises(BenchmarkError, match="required_claims"):
            BenchmarkEngine._validate_expected({"min_sources": 2})

    def test_expected_sections_normalized_lowercase(self):
        """required_sections are lowercased during validation."""
        expected = BenchmarkEngine._validate_expected({
            "required_claims": ["c1"],
            "required_citations": True,
            "min_sources": 1,
            "min_evidence": 1,
            "required_sections": ["Summary", "Key Findings"],
        })
        assert all(s == s.lower() for s in expected["required_sections"])


# ── Scoring: Required Claims ───────────────────────────────────────

class TestScoringRequiredClaims:
    def test_score_required_claims_pass(self, sample_expected, sample_evidence, sample_sources, sample_good_report):
        """Report containing all required_claims gets high completeness."""
        scores = BenchmarkEngine.score_report(sample_good_report, sample_evidence, sample_sources, sample_expected)
        assert scores["completeness"] >= 50.0  # at least half the claims found
        assert scores["overall_score"] >= 0

    def test_score_required_claims_missing(self, sample_expected, sample_evidence, sample_sources):
        """Report missing a required_claim gets lower completeness."""
        bad_report = "# Deep Research\n\nNothing about the required claims.\n"
        scores = BenchmarkEngine.score_report(bad_report, sample_evidence, sample_sources, sample_expected)
        assert scores["completeness"] == 0.0

    def test_score_forbidden_claims_penalty(self, sample_expected, sample_evidence, sample_sources):
        """Report containing a forbidden_claim gets penalized completeness."""
        bad_report = "# Deep Research\n\nSQLite handles terabytes of data efficiently [E1].\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        scores = BenchmarkEngine.score_report(bad_report, sample_evidence, sample_sources, sample_expected)
        # completeness should be penalized: 0 / 2 required = 0, minus 20 for forbidden = 0 (clamped)
        assert scores["completeness"] == 0.0


# ── Scoring: Citations ─────────────────────────────────────────────

class TestScoringCitations:
    def test_score_citation_accuracy_all_valid(self, sample_expected, sample_sources):
        """All citations are valid evidence IDs."""
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "quote": "q1", "assertion": "a1", "accepted": True},
            {"evidence_id": "E2", "source_id": "S2", "quote": "q2", "assertion": "a2", "accepted": True},
        ]
        report = "# Deep Research\nClaim [E1] and [E2].\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        scores = BenchmarkEngine.score_report(report, evidence, sample_sources, sample_expected)
        assert scores["citation_accuracy"] == 100.0
        assert scores["hallucination_risk"] == 0.0

    def test_score_citation_accuracy_with_fakes(self, sample_expected, sample_sources):
        """Fake citations reduce accuracy."""
        evidence = [{"evidence_id": "E1", "source_id": "S1", "quote": "q1", "assertion": "a1", "accepted": True}]
        report = "# Deep Research\nClaim [E1] and [E999].\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        scores = BenchmarkEngine.score_report(report, evidence, sample_sources, sample_expected)
        assert scores["citation_accuracy"] == 50.0  # 1 valid / 2 total
        assert scores["unsupported_claims"] == 50.0
        assert scores["hallucination_risk"] == 50.0

    def test_no_citations_scores_zero(self, sample_expected, sample_evidence, sample_sources):
        """No citations in report yields 0 for citation-based metrics."""
        report = "# Deep Research\nNo citations here.\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        scores = BenchmarkEngine.score_report(report, sample_evidence, sample_sources, sample_expected)
        assert scores["citation_accuracy"] == 0.0
        assert scores["unsupported_claims"] == 0.0


# ── Scoring: Evidence Coverage ─────────────────────────────────────

class TestScoringEvidenceCoverage:
    def test_all_evidence_cited_is_100(self, sample_expected, sample_sources):
        """All accepted evidence cited yields 100 coverage."""
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "quote": "q1", "assertion": "a1", "accepted": True},
            {"evidence_id": "E2", "source_id": "S2", "quote": "q2", "assertion": "a2", "accepted": True},
        ]
        report = "# Deep Research\nClaim [E1] and [E2].\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        scores = BenchmarkEngine.score_report(report, evidence, sample_sources, sample_expected)
        assert scores["evidence_coverage"] == 100.0

    def test_no_evidence_cited_is_0(self, sample_expected, sample_sources):
        """No accepted evidence cited yields 0 coverage."""
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "quote": "q1", "assertion": "a1", "accepted": True},
        ]
        report = "# Deep Research\nNo evidence cited.\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        scores = BenchmarkEngine.score_report(report, evidence, sample_sources, sample_expected)
        assert scores["evidence_coverage"] == 0.0

    def test_all_rejected_evidence_vacuous_truth(self, sample_expected, sample_sources):
        """When all evidence is rejected, coverage is 100 (vacuously true)."""
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "quote": "q1", "assertion": "a1", "accepted": False},
        ]
        report = "# Deep Research\nNo evidence.\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        scores = BenchmarkEngine.score_report(report, evidence, sample_sources, sample_expected)
        assert scores["evidence_coverage"] == 100.0


# ── Scoring: Report Structure ──────────────────────────────────────

class TestScoringReportStructure:
    def test_all_sections_present_100(self, sample_expected, sample_evidence, sample_sources, sample_good_report):
        """All required sections present yields 100."""
        scores = BenchmarkEngine.score_report(sample_good_report, sample_evidence, sample_sources, sample_expected)
        assert scores["report_structure"] == 100.0

    def test_missing_sections_lower_score(self, sample_expected, sample_evidence, sample_sources):
        """Missing required sections reduce score."""
        report = "# Deep Research\n## Summary\n## Key Findings\n"
        scores = BenchmarkEngine.score_report(report, sample_evidence, sample_sources, sample_expected)
        # 2 of 6 sections found = 33.3
        assert scores["report_structure"] < 50.0


# ── Scoring: Source Relevance ──────────────────────────────────────

class TestScoringSourceRelevance:
    def test_sources_not_cited_penalizes(self, sample_expected, sample_evidence, sample_sources):
        """Sources not cited in report reduce source_relevance."""
        report = "# Deep Research\nNo sources cited.\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        scores = BenchmarkEngine.score_report(report, sample_evidence, sample_sources, sample_expected)
        assert scores["source_relevance"] == 0.0

    def test_all_sources_cited_full_score(self, sample_expected, sample_evidence, sample_sources):
        """All sources cited yields 100."""
        report = "# Deep Research\nSource [S1] and [S2].\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        scores = BenchmarkEngine.score_report(report, sample_evidence, sample_sources, sample_expected)
        assert scores["source_relevance"] == 100.0


# ── Scoring: Missing Evidence ──────────────────────────────────────

class TestScoringMissingEvidence:
    def test_missing_evidence_items_lowers_score(self, sample_expected, sample_sources):
        """Fewer evidence items than min_evidence doesn't directly lower score (coverage does)."""
        evidence = [
            {"evidence_id": "E1", "source_id": "S1", "quote": "q1", "assertion": "a1", "accepted": True},
        ]
        report = "# Deep Research\nClaim [E1].\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        scores = BenchmarkEngine.score_report(report, evidence, sample_sources, sample_expected)
        # min_evidence is not a direct score factor — coverage and claims handle quality
        assert scores["evidence_coverage"] == 100.0
        assert scores["overall_score"] > 0


# ── Regression Detection ───────────────────────────────────────────

class TestRegressionDetection:
    def test_compare_detects_regression(self, tmp_path):
        """compare_runs flags regression when overall_score drops > threshold."""
        # Create two mock benchmark runs with score.json
        run_a = tmp_path / "run_a"
        run_b = tmp_path / "run_b"
        run_a.mkdir(parents=True, exist_ok=True)
        run_b.mkdir(parents=True, exist_ok=True)

        high_score = {
            "run_id": "run_a", "case_name": "test",
            "scores": {"overall_score": 90.0, "citation_accuracy": 100.0, "evidence_coverage": 100.0,
                       "unsupported_claims": 100.0, "hallucination_risk": 0.0, "source_relevance": 100.0,
                       "report_structure": 100.0, "completeness": 100.0},
        }
        low_score = {
            "run_id": "run_b", "case_name": "test",
            "scores": {"overall_score": 80.0, "citation_accuracy": 80.0, "evidence_coverage": 80.0,
                       "unsupported_claims": 80.0, "hallucination_risk": 20.0, "source_relevance": 80.0,
                       "report_structure": 80.0, "completeness": 80.0},
        }
        (run_a / "score.json").write_text(json.dumps(high_score), encoding="utf-8")
        (run_b / "score.json").write_text(json.dumps(low_score), encoding="utf-8")

        # Patch BENCHMARK_RUNS_DIR to tmp_path
        original = BENCHMARK_RUNS_DIR
        import app.benchmark_engine as bm
        bm.BENCHMARK_RUNS_DIR = tmp_path
        try:
            comparison = BenchmarkEngine.compare_runs("run_a", "run_b")
            # 10-point drop > 5 threshold, so regression should be True
            assert comparison["regression"] is True
            assert len(comparison["regression_details"]) > 0
        finally:
            bm.BENCHMARK_RUNS_DIR = original

    def test_compare_no_regression_for_small_drop(self, tmp_path):
        """Small drops under threshold are not flagged."""
        run_a = tmp_path / "run_a2"
        run_b = tmp_path / "run_b2"
        run_a.mkdir(parents=True, exist_ok=True)
        run_b.mkdir(parents=True, exist_ok=True)

        high = {
            "run_id": "run_a2", "case_name": "test",
            "scores": {"overall_score": 90.0, "citation_accuracy": 100.0, "evidence_coverage": 100.0,
                       "unsupported_claims": 100.0, "hallucination_risk": 0.0, "source_relevance": 100.0,
                       "report_structure": 100.0, "completeness": 100.0},
        }
        slight_lower = {
            "run_id": "run_b2", "case_name": "test",
            "scores": {"overall_score": 87.0, "citation_accuracy": 95.0, "evidence_coverage": 95.0,
                       "unsupported_claims": 95.0, "hallucination_risk": 5.0, "source_relevance": 95.0,
                       "report_structure": 95.0, "completeness": 95.0},
        }
        (run_a / "score.json").write_text(json.dumps(high), encoding="utf-8")
        (run_b / "score.json").write_text(json.dumps(slight_lower), encoding="utf-8")

        import app.benchmark_engine as bm
        original = bm.BENCHMARK_RUNS_DIR
        bm.BENCHMARK_RUNS_DIR = tmp_path
        try:
            comparison = BenchmarkEngine.compare_runs("run_a2", "run_b2")
            # 3-point drop is under 5 threshold
            assert comparison["regression"] is False
        finally:
            bm.BENCHMARK_RUNS_DIR = original


# ── Output File Checks ─────────────────────────────────────────────

class TestBenchmarkOutput:
    def test_score_report_returns_all_metrics(self, sample_expected, sample_evidence, sample_sources, sample_good_report):
        """score_report returns all 8 metrics."""
        scores = BenchmarkEngine.score_report(sample_good_report, sample_evidence, sample_sources, sample_expected)
        expected_metrics = {"citation_accuracy", "evidence_coverage", "unsupported_claims", "hallucination_risk", "source_relevance", "report_structure", "completeness", "overall_score"}
        assert expected_metrics.issubset(scores.keys())

    def test_all_scores_in_zero_to_hundred_range(self, sample_expected, sample_evidence, sample_sources, sample_good_report):
        """All individual metric scores are in 0-100 range."""
        scores = BenchmarkEngine.score_report(sample_good_report, sample_evidence, sample_sources, sample_expected)
        for metric in ["citation_accuracy", "evidence_coverage", "unsupported_claims", "hallucination_risk", "source_relevance", "report_structure", "completeness"]:
            assert 0 <= scores[metric] <= 100, f"{metric} = {scores[metric]} not in [0, 100]"
        # overall_score also 0-100
        assert 0 <= scores["overall_score"] <= 100


# ── Helper tests ───────────────────────────────────────────────────

class TestHelpers:
    def test_extract_eids(self):
        from app.benchmark_engine import BenchmarkEngine
        result = BenchmarkEngine._extract_eids("Text [E1] and [E2] and [E10].")
        assert result == ["E1", "E2", "E10"]

    def test_extract_sids(self):
        from app.benchmark_engine import BenchmarkEngine
        result = BenchmarkEngine._extract_sids("Source [S1] and [S2a].")
        assert result == ["S1"]

    def test_section_present(self):
        from app.benchmark_engine import BenchmarkEngine
        report = "# Deep Research\n## Summary\nContent.\n## Key Findings\nMore.\n"
        assert BenchmarkEngine._section_present(report.lower(), "summary")
        assert BenchmarkEngine._section_present(report.lower(), "key findings")
        assert not BenchmarkEngine._section_present(report.lower(), "missing section")

    def test_default_expected_keys_filled(self):
        """Optional keys get defaults when missing."""
        expected = BenchmarkEngine._validate_expected({
            "required_claims": ["c1"],
            "required_citations": True,
            "min_sources": 1,
            "min_evidence": 1,
            "required_sections": ["Summary"],
        })
        assert "forbidden_claims" in expected
        assert expected["forbidden_claims"] == []


# ── Determinism ────────────────────────────────────────────────────

class TestDeterminism:
    def test_scoring_is_deterministic(self, sample_expected, sample_evidence, sample_sources, sample_good_report):
        """Same inputs always produce same scores."""
        scores1 = BenchmarkEngine.score_report(sample_good_report, sample_evidence, sample_sources, sample_expected)
        scores2 = BenchmarkEngine.score_report(sample_good_report, sample_evidence, sample_sources, sample_expected)
        scores3 = BenchmarkEngine.score_report(sample_good_report, sample_evidence, sample_sources, sample_expected)
        assert scores1 == scores2 == scores3


# ── Regression tests for v0.6.3 — JSON parse errors ───────────────

class TestBenchmarkErrorMessages:
    def test_empty_expected_json_raises_clear_error(self, tmp_path):
        """An empty expected.json should produce a clear error message."""
        case_dir = tmp_path / "test_case"
        case_dir.mkdir(parents=True)
        (case_dir / "expected.json").write_text("", encoding="utf-8")
        (case_dir / "question.md").write_text("Test question?", encoding="utf-8")
        (case_dir / "sources").mkdir()

        with pytest.raises(BenchmarkError, match="empty"):
            BenchmarkEngine._load_json_safely(case_dir / "expected.json", "expected.json")

    def test_invalid_json_expected_shows_snippet(self, tmp_path):
        """Invalid JSON in expected.json shows first 200 chars in error."""
        case_dir = tmp_path / "test_case2"
        case_dir.mkdir(parents=True)
        bad_json = "{bad json here"
        (case_dir / "expected.json").write_text(bad_json, encoding="utf-8")
        (case_dir / "question.md").write_text("Test?", encoding="utf-8")
        (case_dir / "sources").mkdir()

        with pytest.raises(BenchmarkError, match="bad json"):
            BenchmarkEngine._load_json_safely(case_dir / "expected.json", "expected.json")

    def test_save_run_outputs_writes_valid_files(self, tmp_path):
        """save_run_outputs copies workflow files and score_run can read them."""
        wf_dir = tmp_path / ".runtime" / "workflows" / "test_run"
        wf_dir.mkdir(parents=True)

        # Write mock workflow output
        report = "# Deep Research: Test\n## Summary\n## Key Findings\n## Evidence Table\n## Limitations\n## Citations\n## Source List\n"
        (wf_dir / "final_report.md").write_text(report, encoding="utf-8")
        evidence = [{"evidence_id": "E1", "source_id": "S1", "quote": "q", "assertion": "a", "accepted": True}]
        (wf_dir / "evidence_items.json").write_text(json.dumps(evidence), encoding="utf-8")
        sources = [{"source_id": "S1", "title": "Test", "url": "file:///test"}]
        (wf_dir / "sources.json").write_text(json.dumps(sources), encoding="utf-8")

        import app.benchmark_engine as bm
        original_bench = bm.BENCHMARK_RUNS_DIR
        original_wf = bm.WORKFLOW_RUNS_DIR
        bm.WORKFLOW_RUNS_DIR = tmp_path / ".runtime" / "workflows"
        bm.BENCHMARK_RUNS_DIR = tmp_path / ".runtime" / "benchmarks"
        expected = {
            "required_claims": ["test claim"],
            "forbidden_claims": [],
            "required_citations": True,
            "min_sources": 1,
            "min_evidence": 1,
            "required_sections": ["Summary", "Key Findings"],
        }
        try:
            copied = bm.BenchmarkEngine.save_run_outputs("test_run", wf_dir)
            assert "generated_report.md" in copied
            assert "sources.json" in copied
            assert "evidence_items.json" in copied

            # Write benchmark_result.json so score_run can read it
            bench_dir = bm.BENCHMARK_RUNS_DIR / "test_run"
            result_data = {"run_id": "test_run", "case_name": "test", "expected": expected, "question": "test", "source_count": 1, "workflow_result": {}, "timestamp": 0}
            (bench_dir / "benchmark_result.json").write_text(json.dumps(result_data), encoding="utf-8")

            scores = bm.BenchmarkEngine.score_run("test_run")
            assert "overall_score" in scores
            assert "citation_accuracy" in scores
            assert 0 <= scores["overall_score"] <= 100
        finally:
            bm.BENCHMARK_RUNS_DIR = original_bench
            bm.WORKFLOW_RUNS_DIR = original_wf
