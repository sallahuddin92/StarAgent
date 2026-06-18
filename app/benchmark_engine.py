"""StarAgent v0.6.3 — Deterministic research benchmark engine.

Scores deep research output against expected.json using only
rule-based checks (regex, counts, file presence). No LLM calls in scoring.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
RUNTIME_DIR = Path(os.getenv("STARAGENT_RUNTIME_DIR", ".runtime"))
BENCHMARK_RUNS_DIR = RUNTIME_DIR / "benchmarks"
WORKFLOW_RUNS_DIR = RUNTIME_DIR / "workflows"

SCORE_WEIGHTS = {
    "citation_accuracy": 0.25,
    "evidence_coverage": 0.25,
    "report_structure": 0.20,
    "completeness": 0.20,
    "source_relevance": 0.10,
    # unsupported_claims and hallucination_risk are derived from citation_accuracy
}

REGRESSION_THRESHOLD = 5.0

REQUIRED_EXPECTED_KEYS = {
    "required_claims": list,
    "required_sections": list,
    "min_sources": (int, float),
    "min_evidence": (int, float),
    "required_citations": bool,
}

OPTIONAL_EXPECTED_KEYS = {
    "forbidden_claims": list,
}


class BenchmarkError(Exception):
    """Raised for benchmark configuration or runtime errors."""


class BenchmarkEngine:
    """Loads benchmark cases, scores research output, detects regressions."""

    @staticmethod
    def _benchmarks_root() -> Path:
        return BENCHMARKS_DIR

    # ── Case Discovery ──────────────────────────────────────────────

    @classmethod
    def list_cases(cls) -> List[str]:
        """Return sorted list of valid benchmark case names."""
        root = cls._benchmarks_root()
        if not root.is_dir():
            return []
        cases = []
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and (entry / "expected.json").is_file():
                cases.append(entry.name)
        return cases

    @classmethod
    def case_path(cls, case_name: str) -> Path:
        root = cls._benchmarks_root()
        cp = root / case_name
        if not cp.is_dir():
            raise BenchmarkError(f"Benchmark case '{case_name}' not found at {cp}")
        return cp

    # ── Case Loading ───────────────────────────────────────────────

    @classmethod
    def load_case(cls, case_name: str) -> Dict[str, Any]:
        """Load a benchmark case, returning parsed question, sources, expected, gold."""
        cp = cls.case_path(case_name)

        question_file = cp / "question.md"
        if not question_file.is_file():
            raise BenchmarkError(f"Missing question.md for case '{case_name}'")
        question = question_file.read_text(encoding="utf-8").strip()

        expected_file = cp / "expected.json"
        if not expected_file.is_file():
            raise BenchmarkError(f"Missing expected.json for case '{case_name}' at {expected_file}")
        expected = cls._validate_expected(cls._load_json_safely(expected_file, f"expected.json for '{case_name}'"))

        sources_dir = cp / "sources"
        sources = []
        if sources_dir.is_dir():
            for src_file in sorted(sources_dir.iterdir()):
                if src_file.suffix in (".md", ".txt", ".html", ".json"):
                    sources.append({
                        "filename": src_file.name,
                        "path": str(src_file.resolve()),
                        "content": src_file.read_text(encoding="utf-8"),
                    })

        gold_file = cp / "gold_report.md"
        gold = gold_file.read_text(encoding="utf-8") if gold_file.is_file() else ""

        return {
            "case_name": case_name,
            "question": question,
            "sources": sources,
            "expected": expected,
            "gold_report": gold,
        }

    @staticmethod
    def _validate_expected(expected: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and normalize expected.json content."""
        errors = []
        for key, expected_type in REQUIRED_EXPECTED_KEYS.items():
            if key not in expected:
                errors.append(f"Missing required key: {key}")
            elif not isinstance(expected[key], expected_type):
                errors.append(f"Key '{key}' should be {expected_type.__name__}, got {type(expected[key]).__name__}")
        if errors:
            raise BenchmarkError("; ".join(errors))

        # Set defaults for optional keys
        for key, default_type in OPTIONAL_EXPECTED_KEYS.items():
            if key not in expected:
                expected[key] = [] if default_type is list else default_type()

        # Normalize required_sections to lowercase for matching
        expected.setdefault("required_citations", True)
        expected["required_sections"] = [s.lower() for s in expected.get("required_sections", [])]
        return expected

    # ── Deterministic Scoring ──────────────────────────────────────

    @classmethod
    def score_report(
        cls,
        report: str,
        evidence_items: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
        expected: Dict[str, Any],
    ) -> Dict[str, float]:
        """Score a generated report against expected claims and structure.

        All scores are 0-100. No LLM calls. Pure deterministic computation.
        """
        # Extract citation IDs from report
        cited_evidence = cls._extract_eids(report)
        cited_sources = cls._extract_sids(report)

        # Total evidence citations in report
        total_citations = len(cited_evidence)

        # 1. Citation Accuracy: valid [E#] / total [E#]
        valid_eids = {e["evidence_id"] for e in evidence_items}
        if total_citations > 0:
            valid_count = sum(1 for eid in cited_evidence if eid in valid_eids)
            citation_accuracy = (valid_count / total_citations) * 100.0
        else:
            citation_accuracy = 0.0

        # 2. Unsupported Claims: inverse of fake citation ratio
        fake_count = total_citations - (valid_count if total_citations > 0 else 0)
        if total_citations > 0:
            unsupported_claims = (1.0 - fake_count / total_citations) * 100.0
        else:
            unsupported_claims = 0.0

        # 3. Hallucination Risk: inverse of citation accuracy
        hallucination_risk = 100.0 - citation_accuracy

        # 4. Evidence Coverage: what fraction of accepted evidence is cited
        accepted = [e for e in evidence_items if e.get("accepted", True)]
        if accepted:
            cited_accepted = sum(1 for e in accepted if e["evidence_id"] in cited_evidence)
            evidence_coverage = (cited_accepted / len(accepted)) * 100.0
        else:
            evidence_coverage = 100.0  # vacuously true

        # 5. Source Relevance: fraction of sources cited via [S#]
        if sources:
            cited_source_count = sum(1 for s in sources if s.get("source_id", "") in cited_sources)
            source_relevance = (cited_source_count / len(sources)) * 100.0
        else:
            source_relevance = 100.0

        # 6. Report Structure: required markdown sections present
        report_lower = report.lower()
        required_sections = expected.get("required_sections", [])
        if required_sections:
            found_sections = sum(1 for sec in required_sections if cls._section_present(report_lower, sec.lower()))
            report_structure = (found_sections / len(required_sections)) * 100.0
        else:
            report_structure = 100.0

        # 7. Completeness: required claims present, forbidden claims penalize
        required_claims = expected.get("required_claims", [])
        forbidden_claims = expected.get("forbidden_claims", [])
        if required_claims:
            found_claims = sum(1 for c in required_claims if c.lower() in report_lower)
            completeness = (found_claims / len(required_claims)) * 100.0
        else:
            completeness = 100.0

        # Penalize forbidden claims
        for fc in forbidden_claims:
            if fc.lower() in report_lower:
                completeness = max(0.0, completeness - 20.0)

        # 8. Overall Score: weighted combination
        overall_score = (
            SCORE_WEIGHTS["citation_accuracy"] * citation_accuracy
            + SCORE_WEIGHTS["evidence_coverage"] * evidence_coverage
            + SCORE_WEIGHTS["report_structure"] * report_structure
            + SCORE_WEIGHTS["completeness"] * completeness
            + SCORE_WEIGHTS["source_relevance"] * source_relevance
        )

        scores = {
            "citation_accuracy": round(citation_accuracy, 1),
            "evidence_coverage": round(evidence_coverage, 1),
            "unsupported_claims": round(unsupported_claims, 1),
            "hallucination_risk": round(hallucination_risk, 1),
            "source_relevance": round(source_relevance, 1),
            "report_structure": round(report_structure, 1),
            "completeness": round(completeness, 1),
            "overall_score": round(overall_score, 1),
        }
        return scores

    @staticmethod
    def _extract_eids(text: str) -> List[str]:
        """Extract all [E#] citation markers from text."""
        return re.findall(r'\[(E\d+)\]', text)

    @staticmethod
    def _extract_sids(text: str) -> List[str]:
        """Extract all [S#] source markers from text."""
        return re.findall(r'\[(S\d+)\]', text)

    @staticmethod
    def _section_present(report_lower: str, section_name: str) -> bool:
        """Check if a markdown heading for the section exists in the report."""
        # Match ## Section Name or # Section Name
        pattern = r'^#{1,4}\s+' + re.escape(section_name) + r'\s*$'
        return bool(re.search(pattern, report_lower, re.MULTILINE))

    # ── Run Management ─────────────────────────────────────────────

    @classmethod
    def save_run_outputs(cls, run_id: str, wf_dir: Path) -> None:
        """Copy workflow output files to .runtime/benchmarks/<run_id>/."""
        bench_dir = BENCHMARK_RUNS_DIR / run_id
        bench_dir.mkdir(parents=True, exist_ok=True)

        file_map = {
            "final_report.md": "generated_report.md",
            "sources.json": "sources.json",
            "evidence_items.json": "evidence_items.json",
        }
        copied = []
        for src_name, dst_name in file_map.items():
            src = wf_dir / src_name
            dst = bench_dir / dst_name
            if src.is_file():
                shutil.copy2(str(src), str(dst))
                copied.append(dst_name)
            else:
                # Write an empty indicator
                dst.write_text("", encoding="utf-8")
        return copied

    @classmethod
    def _load_json_safely(cls, path: Path, label: str = "") -> Any:
        """Load a JSON file with a descriptive error on failure."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise BenchmarkError(f"{label} file not found: {path}")
        except Exception as e:
            raise BenchmarkError(f"Cannot read {label} file {path}: {e}")
        if not raw.strip():
            raise BenchmarkError(f"{label} file is empty: {path}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            snippet = raw[:200]
            raise BenchmarkError(
                f"Invalid JSON in {label} file {path}: {e}\n"
                f"First 200 chars: {snippet!r}"
            )

    @classmethod
    def score_run(cls, run_id: str) -> Dict[str, float]:
        """Score a completed benchmark run from stored output files."""
        bench_dir = BENCHMARK_RUNS_DIR / run_id
        if not bench_dir.is_dir():
            raise BenchmarkError(f"Benchmark run '{run_id}' not found at {bench_dir}")

        report_file = bench_dir / "generated_report.md"
        evidence_file = bench_dir / "evidence_items.json"
        sources_file = bench_dir / "sources.json"
        result_file = bench_dir / "benchmark_result.json"

        if not report_file.is_file():
            raise BenchmarkError(f"Missing generated_report.md for run '{run_id}'")
        if not evidence_file.is_file():
            raise BenchmarkError(f"Missing evidence_items.json for run '{run_id}'")
        if not sources_file.is_file():
            raise BenchmarkError(f"Missing sources.json for run '{run_id}'")
        if not result_file.is_file():
            raise BenchmarkError(f"Missing benchmark_result.json for run '{run_id}'")

        report = report_file.read_text(encoding="utf-8")
        evidence_items = cls._load_json_safely(evidence_file, "evidence_items")
        sources = cls._load_json_safely(sources_file, "sources")
        result = cls._load_json_safely(result_file, "benchmark_result")
        expected = result.get("expected", {})

        scores = cls.score_report(report, evidence_items, sources, expected)

        # Write score.json
        score_file = bench_dir / "score.json"
        score_data = {
            "run_id": run_id,
            "case_name": result.get("case_name", ""),
            "timestamp": time.time(),
            "scores": scores,
            "weights": SCORE_WEIGHTS,
            "regression_threshold": REGRESSION_THRESHOLD,
        }
        score_file.write_text(json.dumps(score_data, indent=2), encoding="utf-8")

        return scores

    @classmethod
    def compare_runs(cls, run_id_a: str, run_id_b: str) -> Dict[str, Any]:
        """Compare two benchmark runs and detect regression."""
        score_a = cls._load_score(run_id_a)
        score_b = cls._load_score(run_id_b)

        scores_a = score_a.get("scores", {})
        scores_b = score_b.get("scores", {})

        deltas = {}
        regression = False
        regression_details = []

        all_metrics = set(scores_a.keys()) | set(scores_b.keys())
        for metric in sorted(all_metrics):
            va = scores_a.get(metric, 0)
            vb = scores_b.get(metric, 0)
            delta = round(vb - va, 1)
            deltas[metric] = {
                "run_a": va,
                "run_b": vb,
                "delta": delta,
            }
            if metric == "overall_score" and delta < -REGRESSION_THRESHOLD:
                regression = True
                regression_details.append(
                    f"overall_score dropped {abs(delta):.1f} points (threshold: {REGRESSION_THRESHOLD})"
                )

        return {
            "run_id_a": run_id_a,
            "run_id_b": run_id_b,
            "case_name_a": score_a.get("case_name", ""),
            "case_name_b": score_b.get("case_name", ""),
            "regression": regression,
            "regression_details": regression_details,
            "threshold": REGRESSION_THRESHOLD,
            "deltas": deltas,
        }

    @classmethod
    def _load_score(cls, run_id: str) -> Dict[str, Any]:
        """Load score.json for a benchmark run."""
        bench_dir = BENCHMARK_RUNS_DIR / run_id
        score_file = bench_dir / "score.json"
        if not score_file.is_file():
            raise BenchmarkError(f"No score.json found for benchmark run '{run_id}'")
        return json.loads(score_file.read_text(encoding="utf-8"))

    @classmethod
    def history(cls) -> List[Dict[str, Any]]:
        """List all completed benchmark runs with scores."""
        if not BENCHMARK_RUNS_DIR.is_dir():
            return []
        runs = []
        for entry in sorted(BENCHMARK_RUNS_DIR.iterdir()):
            if not entry.is_dir():
                continue
            score_file = entry / "score.json"
            if score_file.is_file():
                data = json.loads(score_file.read_text(encoding="utf-8"))
                runs.append({
                    "run_id": data.get("run_id", entry.name),
                    "case_name": data.get("case_name", ""),
                    "timestamp": data.get("timestamp", 0),
                    "overall_score": data.get("scores", {}).get("overall_score"),
                })
        return sorted(runs, key=lambda r: r["timestamp"], reverse=True)
