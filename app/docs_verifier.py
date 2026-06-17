from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

NO_EVIDENCE_ANSWER = "No answer: insufficient project documentation evidence for this query."


@dataclass
class DocsVerificationResult:
    ok: bool
    reason: str


class DocsEvidenceVerifier:
    """Verifier gates for docs search and docs answer outputs."""

    def verify_search_results(self, results: List[Dict[str, Any]]) -> DocsVerificationResult:
        if not isinstance(results, list) or not results:
            return DocsVerificationResult(ok=False, reason="no_results")

        for item in results:
            if not item.get("chunk_id"):
                return DocsVerificationResult(ok=False, reason="missing_chunk_id")
            if not (item.get("source_path") or item.get("path_or_url")):
                return DocsVerificationResult(ok=False, reason="missing_source_path")
            if not (item.get("content") or "").strip():
                return DocsVerificationResult(ok=False, reason="empty_content")

        return DocsVerificationResult(ok=True, reason="ok")

    def verify_answer(self, payload: Dict[str, Any]) -> DocsVerificationResult:
        if payload.get("status") == "no_evidence":
            # Explicit no-evidence response is valid and enforced.
            return DocsVerificationResult(ok=True, reason="no_evidence_enforced")

        citations = payload.get("citations") or []
        answer = (payload.get("answer") or "").strip()

        if not answer:
            return DocsVerificationResult(ok=False, reason="empty_answer")
        if not citations:
            return DocsVerificationResult(ok=False, reason="missing_citations")
        for c in citations:
            if not c.get("chunk_id"):
                return DocsVerificationResult(ok=False, reason="citation_missing_chunk_id")
            if not (c.get("source_path") or c.get("path_or_url")):
                return DocsVerificationResult(ok=False, reason="citation_missing_source")

        return DocsVerificationResult(ok=True, reason="ok")
