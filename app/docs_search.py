from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .docs_store import DocsStore
from .docs_verifier import DocsEvidenceVerifier, NO_EVIDENCE_ANSWER


class DocsSearcher:
    """Retrieve, verify, and format project-scoped docs evidence."""

    def __init__(self, docs_store: DocsStore):
        self.store = docs_store
        self.verifier = DocsEvidenceVerifier()

    def search_structured(
        self,
        project_id: str,
        query: str,
        package_name: Optional[str] = None,
        max_results: int = 5,
        is_error_lookup: bool = False,
        error_message: str = "",
    ) -> List[Dict[str, Any]]:
        results = self.store.search(
            project_id=project_id,
            query=query,
            package_name=package_name,
            max_results=max_results,
        )

        # Verifier gate: docs search must return evidence-bearing entries only.
        verified: List[Dict[str, Any]] = []
        for r in results:
            check = self.verifier.verify_search_results([r])
            if check.ok:
                verified.append(r)

        self.store.log_search(project_id, query, is_error_lookup, error_message, verified)
        return verified

    def search(
        self,
        project_id: str,
        query: str,
        package_name: Optional[str] = None,
        max_results: int = 3,
        is_error_lookup: bool = False,
        error_message: str = "",
    ) -> str:
        results = self.search_structured(
            project_id=project_id,
            query=query,
            package_name=package_name,
            max_results=max_results,
            is_error_lookup=is_error_lookup,
            error_message=error_message,
        )
        if not results:
            return f"No local documentation evidence found for query: '{query}'"

        out = [f"## Local Documentation Evidence for '{query}'\n"]
        for i, r in enumerate(results, start=1):
            citation = self._citation_label(r)
            out.append(f"### Evidence {i}: {r.get('title')} ({r.get('source_type')})")
            out.append(f"**Citation**: {citation}")
            if r.get("heading"):
                out.append(f"**Section**: {r['heading']}")
            if r.get("page_ref"):
                out.append(f"**Page**: {r['page_ref']}")
            out.append(f"```text\n{(r.get('content') or '')[:1800]}\n```")
            if r.get("code_examples"):
                out.append(f"**Code Examples**:\n```\n{(r.get('code_examples') or '')[:1000]}\n```")
            out.append("---\n")
        return "\n".join(out)

    def ask(
        self,
        project_id: str,
        question: str,
        package_name: Optional[str] = None,
        max_results: int = 5,
    ) -> Dict[str, Any]:
        evidence = self.search_structured(
            project_id=project_id,
            query=question,
            package_name=package_name,
            max_results=max_results,
            is_error_lookup=False,
            error_message="",
        )

        if not evidence:
            payload = {
                "status": "no_evidence",
                "question": question,
                "answer": NO_EVIDENCE_ANSWER,
                "citations": [],
                "evidence": [],
            }
            return payload

        # Verifier gate: reject weak lexical overlap to avoid unsupported answers.
        overlap_stats = [self._keyword_overlap(question, e.get("content") or "") for e in evidence]
        best_overlap = max((ov for ov, _ in overlap_stats), default=0)
        best_ratio = max(((ov / qn) if qn else 0.0 for ov, qn in overlap_stats), default=0.0)
        if best_overlap < 1 or best_ratio < 0.35:
            return {
                "status": "no_evidence",
                "question": question,
                "answer": NO_EVIDENCE_ANSWER,
                "citations": [],
                "evidence": [],
                "reason": "low_overlap",
            }

        citations = [self._citation_obj(r) for r in evidence]
        highlights = []
        for r in evidence[:3]:
            content = (r.get("content") or "").strip().replace("\n", " ")
            snippet = content[:260] + ("..." if len(content) > 260 else "")
            highlights.append(f"- {snippet} [{self._citation_label(r)}]")

        answer = "\n".join(
            [
                "Evidence-backed answer:",
                "I found supporting snippets in the project documentation:",
                *highlights,
                "Use the cited chunks as the authoritative reference for implementation.",
            ]
        )

        payload = {
            "status": "ok",
            "question": question,
            "answer": answer,
            "citations": citations,
            "evidence": evidence,
        }

        verdict = self.verifier.verify_answer(payload)
        if not verdict.ok:
            return {
                "status": "no_evidence",
                "question": question,
                "answer": NO_EVIDENCE_ANSWER,
                "citations": [],
                "evidence": [],
                "reason": verdict.reason,
            }

        return payload

    def search_for_error(self, project_id: str, error_message: str) -> str:
        match = re.search(r"No module named '([^']+)'", error_message)
        if match:
            pkg = match.group(1)
            return self.search(project_id, f"install {pkg} usage", package_name=pkg, is_error_lookup=True, error_message=error_message)

        match = re.search(r"AttributeError: '([^']+)' object has no attribute '([^']+)'", error_message)
        if match:
            obj = match.group(1)
            attr = match.group(2)
            return self.search(project_id, f"{obj} {attr}", is_error_lookup=True, error_message=error_message)

        lines = [line.strip() for line in error_message.split("\n") if line.strip()]
        last_line = lines[-1] if lines else error_message
        clean_query = re.sub(r"[^a-zA-Z0-9\s]", " ", last_line)
        clean_query = " ".join(clean_query.split()[:10])
        return self.search(project_id, clean_query, is_error_lookup=True, error_message=error_message)

    @staticmethod
    def _citation_obj(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "chunk_id": item.get("chunk_id"),
            "chunk_index": item.get("chunk_index"),
            "source_path": item.get("source_path"),
            "path_or_url": item.get("path_or_url"),
            "title": item.get("title"),
            "section_ref": item.get("section_ref") or item.get("heading"),
            "page_ref": item.get("page_ref"),
        }

    @classmethod
    def _citation_label(cls, item: Dict[str, Any]) -> str:
        c = cls._citation_obj(item)
        src = c.get("source_path") or c.get("path_or_url") or "unknown-source"
        chunk = c.get("chunk_id") or "unknown-chunk"
        return f"{src}#chunk={chunk}"

    @staticmethod
    def _keyword_overlap(a: str, b: str) -> tuple[int, int]:
        stop = {
            "the", "a", "an", "is", "are", "was", "were", "how", "what", "with", "this",
            "that", "for", "from", "into", "should", "would", "could", "to", "of", "and", "or",
        }
        at = {w.lower() for w in re.findall(r"[A-Za-z0-9_]+", a) if len(w) > 2 and w.lower() not in stop}
        bt = {w.lower() for w in re.findall(r"[A-Za-z0-9_]+", b) if len(w) > 2 and w.lower() not in stop}
        return len(at & bt), len(at)
