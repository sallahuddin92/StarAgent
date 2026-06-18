"""StarAgent v0.7.0 — Autonomous Research Loop.

Wraps the deep_research workflow in an iterative loop that scores confidence,
detects gaps, and re-runs until quality thresholds are met.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.benchmark_engine import BenchmarkEngine
from app.claim_graph import build_claim_graph, read_claim_graph, claim_metrics


RUNTIME_DIR = Path(os.getenv("STARAGENT_RUNTIME_DIR", ".runtime"))
WORKFLOW_RUNS_DIR = RUNTIME_DIR / "workflows"


@dataclass
class ResearchLoopConfig:
    """Configuration for the autonomous research loop."""

    max_iterations: int = 3
    min_sources: int = 3
    min_evidence: int = 5
    min_confidence: int = 75
    stop_on_no_new_evidence: bool = True
    min_supported_claims: int = 3


class ResearchLoop:
    """Orchestrates iterative deep research with confidence scoring and gap detection."""

    def __init__(self, config: Optional[ResearchLoopConfig] = None):
        self.config = config or ResearchLoopConfig()

    # ── Source Classification ───────────────────────────────────────

    @staticmethod
    def _classify_sources(sources: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
        """Split sources into usable and failed, return (usable, failed, error_messages)."""
        usable: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        errors: List[str] = []
        for s in sources:
            err = s.get("error")
            if err:
                failed.append(s)
                errors.append(f"{s.get('source_id', '?')}: {err}")
            else:
                usable.append(s)
        return usable, failed, errors

    # ── Confidence Scoring ──────────────────────────────────────────

    def compute_confidence(
        self,
        sources: List[Dict[str, Any]],
        evidence_items: List[Dict[str, Any]],
        report: str,
        contradictions: Optional[List[Dict[str, Any]]] = None,
        expected: Optional[Dict[str, Any]] = None,
        claim_graph: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Deterministic confidence score 0-100 based on evidence quality.

        When all sources failed or no usable evidence exists, returns <= 20.
        Claim graph metrics boost or penalize confidence.
        """
        cfg = self.config
        accepted = [e for e in evidence_items if e.get("accepted", True)]
        cited_eids = self._extract_eids(report)
        usable_sources, _, _ = self._classify_sources(sources)
        usable_count = len(usable_sources)
        accepted_count = len(accepted)

        # If no usable sources or no evidence at all, cap at 20
        if usable_count == 0 or accepted_count == 0:
            return 0

        # 1. Source count — only count usable sources (0-25)
        source_score = min(25.0, 25.0 * usable_count / max(cfg.min_sources, 1))

        # 2. Evidence count (0-25)
        evidence_score = min(25.0, 25.0 * accepted_count / max(cfg.min_evidence, 1))

        # 3. Citation coverage (0-20)
        if accepted:
            cited_count = sum(1 for e in accepted if e.get("evidence_id", "") in cited_eids)
            citation_coverage = cited_count / len(accepted)
        else:
            citation_coverage = 0.0
        citation_score = 20.0 * citation_coverage

        # 4. Source diversity (0-10)
        source_ids_in_evidence = {e.get("source_id", "") for e in accepted if e.get("source_id")}
        if usable_count > 0:
            diversity_ratio = len(source_ids_in_evidence) / usable_count
        else:
            diversity_ratio = 0.0
        diversity_score = 10.0 * diversity_ratio

        # 5. Contradiction status (0-10)
        if contradictions and any(c.get("status") == "unresolved" for c in contradictions):
            contradiction_score = 0.0
        else:
            contradiction_score = 10.0

        # 6. Claim coverage (0-10)
        required_claims = (expected or {}).get("required_claims", [])
        if required_claims:
            report_lower = report.lower()
            found = sum(1 for c in required_claims if c.lower() in report_lower)
            claim_score = 10.0 * found / len(required_claims)
        else:
            claim_score = 10.0

        total = int(
            source_score
            + evidence_score
            + citation_score
            + diversity_score
            + contradiction_score
            + claim_score
        )

        # 7. Claim graph boost/penalty (±15)
        if claim_graph:
            metrics = claim_metrics(claim_graph)
            if metrics["total_claims"] >= cfg.min_supported_claims:
                # Boost: good claim coverage
                total += min(15, int(15 * metrics["supported_count"] / max(cfg.min_supported_claims, 1)))
            if metrics["contradicted_count"] > 0:
                # Penalty for contradictions
                total -= min(15, 5 * metrics["contradicted_count"])
            # Ensure at least some claims are supported
            if metrics["supported_count"] < 1 and metrics["total_claims"] > 0:
                total = min(total, 40)

        # Apply caps based on evidence/source quality
        if accepted_count == 0:
            total = min(total, 10)
        if accepted_count < cfg.min_evidence:
            total = min(total, 60)
        if usable_count < cfg.min_sources:
            total = min(total, 60)
        # Only one unique source in evidence
        if len({e.get("source_id", "") for e in accepted}) <= 1:
            total = min(total, 65)
        # Unsupported claims in report
        valid_eids = {e.get("evidence_id", "") for e in evidence_items}
        if any(eid not in valid_eids for eid in cited_eids):
            total = min(total, 50)

        return max(0, min(100, total))

    # ── Gap Detection ───────────────────────────────────────────────

    def detect_gaps(
        self,
        sources: List[Dict[str, Any]],
        evidence_items: List[Dict[str, Any]],
        report: str,
        contradictions: Optional[List[Dict[str, Any]]] = None,
        expected: Optional[Dict[str, Any]] = None,
        claim_graph: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Detect research quality gaps."""
        cfg = self.config
        gaps: List[str] = []
        accepted = [e for e in evidence_items if e.get("accepted", True)]
        valid_eids = {e.get("evidence_id", "") for e in evidence_items}

        # Classify sources to detect failures
        usable_sources, failed_sources, _ = self._classify_sources(sources)

        # All sources failed
        if len(sources) > 0 and len(usable_sources) == 0:
            gaps.append("all_sources_failed")

        # Too few usable sources
        if len(usable_sources) < cfg.min_sources:
            gaps.append("too_few_sources")

        # Too few accepted evidence items
        if len(accepted) < cfg.min_evidence:
            gaps.append("too_few_evidence")

        # Missing required claims
        required_claims = (expected or {}).get("required_claims", [])
        if required_claims:
            report_lower = report.lower()
            missing = [c for c in required_claims if c.lower() not in report_lower]
            if missing:
                gaps.append("missing_required_claims")

        # Unsupported claims (fake [E#] citations)
        cited_eids = self._extract_eids(report)
        fake_eids = [eid for eid in cited_eids if eid not in valid_eids]
        if fake_eids:
            gaps.append("unsupported_claims")

        # Low source diversity
        source_ids_in_evidence = {e.get("source_id", "") for e in accepted if e.get("source_id")}
        if len(source_ids_in_evidence) <= 1 and len(usable_sources) > 1:
            gaps.append("low_source_diversity")

        # Unresolved contradictions
        if contradictions:
            unresolved = [c for c in contradictions if c.get("status") == "unresolved"]
            if unresolved:
                gaps.append("contradictions_unresolved")

        # Low citation coverage
        if accepted:
            cited_count = sum(1 for e in accepted if e.get("evidence_id", "") in cited_eids)
            if cited_count / len(accepted) < 0.5:
                gaps.append("low_citation_coverage")

        # Too few supported claims from claim graph
        if claim_graph:
            metrics = claim_metrics(claim_graph)
            if metrics["supported_count"] < cfg.min_supported_claims and metrics["total_claims"] > 0:
                gaps.append("too_few_supported_claims")

        return gaps

    # ── Decision Logic ──────────────────────────────────────────────

    HARD_GAPS = frozenset({
        "too_few_sources",
        "too_few_evidence",
        "all_sources_failed",
        "unsupported_claims",
        "contradictions_unresolved",
        "too_few_supported_claims",
    })

    def decide_next_action(
        self,
        iteration: int,
        confidence: int,
        gaps: List[str],
        prev_evidence_count: Optional[int] = None,
        current_evidence_count: int = 0,
    ) -> str:
        """Decide whether to continue or stop and why.

        Hard gaps (too_few_sources, too_few_evidence, all_sources_failed,
        unsupported_claims, contradictions_unresolved) take priority over
        confidence threshold — stop_confidence_met is blocked when any exist.
        """
        cfg = self.config
        has_hard_gaps = bool(self.HARD_GAPS & set(gaps))

        # Hard-gap logic: confidence threshold cannot override hard gaps
        if has_hard_gaps:
            if iteration < cfg.max_iterations:
                can_improve = (
                    "too_few_evidence" not in gaps
                    or prev_evidence_count is None
                    or not cfg.stop_on_no_new_evidence
                    or current_evidence_count > prev_evidence_count
                )
                if can_improve:
                    return "continue"

            if iteration >= cfg.max_iterations:
                return "stop_max_iterations"
            if prev_evidence_count is not None and current_evidence_count <= prev_evidence_count:
                return "stop_no_new_evidence"
            return "stop_max_iterations"

        # No hard gaps — normal confidence check
        if confidence >= cfg.min_confidence:
            return "stop_confidence_met"

        # Stop if no new evidence gained
        if cfg.stop_on_no_new_evidence and prev_evidence_count is not None:
            if current_evidence_count <= prev_evidence_count:
                return "stop_no_new_evidence"

        # Stop if max iterations reached
        if iteration >= cfg.max_iterations:
            return "stop_max_iterations"

        return "continue"

    # ── State Persistence ───────────────────────────────────────────

    @staticmethod
    def _extract_eids(text: str) -> List[str]:
        """Extract all [E#] citation markers from text."""
        return re.findall(r"\[(E\d+)\]", text)

    @staticmethod
    def _extract_source_ids(text: str) -> List[str]:
        """Extract all [S#] source markers from text."""
        return re.findall(r"\[(S\d+)\]", text)

    @staticmethod
    def _evidence_count(evidence_items: List[Dict[str, Any]]) -> int:
        return len([e for e in evidence_items if e.get("accepted", True)])

    def write_loop_state(
        self,
        run_id: str,
        iterations: List[Dict[str, Any]],
        final_decision: str,
        final_confidence: int,
    ) -> Path:
        """Write research_loop.json to the workflow run directory."""
        wf_dir = WORKFLOW_RUNS_DIR / run_id
        wf_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "run_id": run_id,
            "iterations": iterations,
            "final_decision": final_decision,
            "final_confidence": final_confidence,
        }
        path = wf_dir / "research_loop.json"
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def read_loop_state(run_id: str) -> Dict[str, Any]:
        """Read research_loop.json from the workflow run directory."""
        path = WORKFLOW_RUNS_DIR / run_id / "research_loop.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _citation_coverage_pct(evidence_items: List[Dict[str, Any]], report: str) -> float:
        """Compute percentage of accepted evidence items cited in the report."""
        accepted = [e for e in evidence_items if e.get("accepted", True)]
        if not accepted:
            return 0.0
        cited_eids = set(re.findall(r"\[(E\d+)\]", report))
        cited = sum(1 for e in accepted if e.get("evidence_id", "") in cited_eids)
        return round(100.0 * cited / len(accepted), 1)

    @staticmethod
    def _source_diversity_desc(sources: List[Dict[str, Any]], evidence_items: List[Dict[str, Any]]) -> str:
        """Describe source diversity."""
        source_ids = {e.get("source_id", "") for e in evidence_items if e.get("source_id")}
        return f"{len(source_ids)} unique domains"

    def append_confidence_section(
        self,
        run_id: str,
        confidence: int,
        gaps: List[str],
        stop_reason: str,
        sources: List[Dict[str, Any]],
        evidence_items: List[Dict[str, Any]],
        report: str,
    ) -> None:
        """Append Research Confidence section to final_report.md."""
        wf_dir = WORKFLOW_RUNS_DIR / run_id
        report_path = wf_dir / "final_report.md"
        if not report_path.is_file():
            return

        coverage = self._citation_coverage_pct(evidence_items, report)
        diversity = self._source_diversity_desc(sources, evidence_items)
        gap_str = ", ".join(gaps) if gaps else "None"
        usable_sources, failed_sources, _ = self._classify_sources(sources)
        stop_labels = {
            "stop_confidence_met": "Confidence threshold met",
            "stop_max_iterations": "Maximum iterations reached",
            "stop_no_new_evidence": "No new evidence from iteration",
            "stop_no_provider": "No provider or sources available",
        }

        # Read claim graph metrics if available
        claim_section = ""
        cg = read_claim_graph(run_id)
        if cg.get("claims"):
            metrics = claim_metrics(cg)
            claim_section = (
                f"- Total claims: {metrics['total_claims']}\n"
                f"- Supported claims: {metrics['supported_count']}\n"
                f"- Weak claims: {metrics['weak_count']}\n"
                f"- Contradicted claims: {metrics['contradicted_count']}\n"
                f"- Unsupported claims: {metrics['unsupported_count']}\n"
                f"- Average claim confidence: {metrics['claim_confidence_avg']}\n"
            )

        section = (
            f"\n\n## Research Confidence\n"
            f"- Overall confidence: {confidence}/100\n"
            f"- Evidence coverage: {coverage}%\n"
            f"- Source diversity: {diversity}\n"
            f"- Usable sources: {len(usable_sources)}\n"
            f"- Failed sources: {len(failed_sources)}\n"
            f"{claim_section}"
            f"- Remaining gaps: {gap_str}\n"
            f"- Stop reason: {stop_labels.get(stop_reason, stop_reason)}\n"
        )

        with open(str(report_path), "a", encoding="utf-8") as f:
            f.write(section)

    # ── Main Loop ───────────────────────────────────────────────────

    async def run(
        self,
        question: str,
        urls: List[str],
        mode: str,
        db: Any,
        workflow_engine: Any,
        provider: Optional[str] = None,
        docs: bool = False,
    ) -> Dict[str, Any]:
        """Run the autonomous research loop.

        Each iteration creates a fresh task run and executes the deep_research
        workflow. After each iteration, confidence is scored and gaps detected.
        The loop continues until a stop condition is met.
        """
        base_run_id = str(uuid.uuid4())[:8]
        iterations: List[Dict[str, Any]] = []
        prev_evidence_count: Optional[int] = None
        final_decision = "continue"
        final_confidence = 0
        final_report_content = ""

        for iteration in range(1, self.config.max_iterations + 1):
            task_id = f"{base_run_id}_iter_{iteration}" if iteration > 1 else base_run_id
            run_id = task_id

            # Build workflow variables
            variables: Dict[str, Any] = {
                "project_id": "research",
                "docs_context": "",
                "mode": mode,
                "urls": urls,
                "docs": docs,
                "question": question,
                "iteration": iteration,
                "max_iterations": self.config.max_iterations,
            }

            # Create task run
            art: Dict[str, Any] = {
                "workflow_name": "deep_research",
                "current_stage_index": 0,
                "variables": variables,
            }
            db.create_task_run({
                "task_id": task_id,
                "project_id": "research",
                "conversation_id": f"research-auto-{base_run_id}",
                "task_type": "workflow",
                "user_goal": question,
                "definition_of_done": "Deep research report generated and verified.",
                "max_steps": 9,
                "max_retries": 1,
                "artifacts_json": art,
            })

            # Execute workflow with auto-approve loop
            wf_result: Optional[Dict[str, Any]] = None
            try:
                wf_result = await workflow_engine.execute_workflow(task_id)
                for _ in range(10):
                    status = None
                    if isinstance(wf_result, dict):
                        status = wf_result.get("status") or wf_result.get("final_verdict")
                    if status in ("completed", "failed", "cancelled"):
                        break
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

            # Read outputs
            wf_dir = WORKFLOW_RUNS_DIR / run_id
            sources: List[Dict[str, Any]] = []
            evidence_items: List[Dict[str, Any]] = []
            report = ""
            contradictions: List[Dict[str, Any]] = []

            sources_file = wf_dir / "sources.json"
            if sources_file.is_file():
                try:
                    sources = json.loads(sources_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, Exception):
                    sources = []

            evidence_file = wf_dir / "evidence_items.json"
            if evidence_file.is_file():
                try:
                    evidence_items = json.loads(evidence_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, Exception):
                    evidence_items = []

            report_file = wf_dir / "final_report.md"
            if report_file.is_file():
                report = report_file.read_text(encoding="utf-8")

            contradictions_file = wf_dir / "contradictions.json"
            if contradictions_file.is_file():
                try:
                    contradictions = json.loads(contradictions_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, Exception):
                    contradictions = []

            # Build claim graph from evidence
            claim_graph = build_claim_graph(evidence_items, base_run_id)

            # Compute confidence
            usable_sources, failed_sources, source_errors = self._classify_sources(sources)
            confidence = self.compute_confidence(sources, evidence_items, report, contradictions, claim_graph=claim_graph)
            gaps = self.detect_gaps(sources, evidence_items, report, contradictions, claim_graph=claim_graph)

            current_evidence_count = self._evidence_count(evidence_items)

            # Decide next action
            action = self.decide_next_action(
                iteration, confidence, gaps,
                prev_evidence_count=prev_evidence_count,
                current_evidence_count=current_evidence_count,
            )

            iter_record = {
                "iteration": iteration,
                "task_id": task_id,
                "sources_count": len(sources),
                "failed_sources_count": len(failed_sources),
                "usable_sources_count": len(usable_sources),
                "evidence_count": current_evidence_count,
                "confidence": confidence,
                "gaps": gaps,
                "action": action,
            }
            if source_errors:
                iter_record["source_errors"] = source_errors
            iterations.append(iter_record)
            prev_evidence_count = current_evidence_count

            # Write cumulative loop state
            self.write_loop_state(base_run_id, iterations, action, confidence)

            # Check for stop conditions
            if action != "continue":
                final_decision = action
                final_confidence = confidence
                final_report_content = report
                break

            # Check for no-provider safety: if no sources after iteration, stop
            if not sources:
                final_decision = "stop_no_provider"
                final_confidence = 0
                break

        # After loop: write final state with actual final decision
        self.write_loop_state(base_run_id, iterations, final_decision, final_confidence)

        # Append confidence section to final report
        if final_report_content:
            self.append_confidence_section(
                base_run_id, final_confidence,
                iterations[-1].get("gaps", []) if iterations else [],
                final_decision,
                sources if iterations else [],
                evidence_items if iterations else [],
                final_report_content,
            )

        # Try to read report from the last successful iteration
        last_report = ""
        for it in reversed(iterations):
            it_dir = WORKFLOW_RUNS_DIR / it["task_id"]
            rp = it_dir / "final_report.md"
            if rp.is_file():
                try:
                    last_report = rp.read_text(encoding="utf-8")
                except Exception:
                    pass
                break

        return {
            "run_id": base_run_id,
            "iterations": iterations,
            "iteration_count": len(iterations),
            "final_confidence": final_confidence,
            "stop_reason": final_decision,
            "final_report": last_report,
        }
