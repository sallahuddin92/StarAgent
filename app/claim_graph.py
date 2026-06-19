"""StarAgent v0.8.0 — Claim Graph Engine.

Extracts atomic claims from evidence, detects contradictions,
and builds a claim graph for confidence-aware research reporting.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RUNTIME_DIR = Path(__file__).resolve().parent.parent / ".runtime"
WORKFLOW_RUNS_DIR = RUNTIME_DIR / "workflows"


def _split_atomic_claims(text: str) -> List[str]:
    """Split a compound statement into atomic claims.

    Examples:
      "SQLite is self-contained, serverless, and zero-configuration."
      -> ["SQLite is self-contained.", "SQLite is serverless.", "SQLite requires zero configuration."]
    """
    text = text.strip().rstrip(".")
    if not text:
        return []

    # Strategy 1: list-like patterns with commas + "and"/"or"
    # e.g. "X is A, B, and C" -> ["X is A.", "X is B.", "X is C."]
    parts = re.split(r"\s*,\s*(?:\s*(?:and|or)\s+)?(?=\S)", text)

    if len(parts) >= 2:
        # Check if first part has a subject + verb structure to replicate
        first = parts[0].strip()
        # Get the subject+verb prefix (e.g., "SQLite is", "PostgreSQL requires")
        prefix_match = re.match(
            r"^([A-Za-z][A-Za-z0-9\s]*(?:is|are|was|were|has|have|had|does|do|can|could|will|would|shall|should|may|might|must|requires|require|supports|support|uses|use|provides|provide|offers|offer|enables|enable|allows|allow|runs|run))(?:\s+.*)",
            first,
            re.IGNORECASE,
        )
        if prefix_match:
            prefix = prefix_match.group(1).strip()
            claims = []
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                # If part doesn't start with the subject, prepend the prefix
                if not re.match(
                    r"^[A-Za-z][A-Za-z0-9]*(?:\s|$)", p, re.IGNORECASE
                ):
                    # p is just the predicate — wrap to "prefix p"
                    claim = f"{prefix} {p}".strip()
                else:
                    claim = p
                claim = claim.rstrip(".") + "."
                claims.append(claim)
            if claims:
                return claims

    # Strategy 2: multiple sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) >= 2:
        return [s.strip().rstrip(".") + "." for s in sentences if s.strip()]

    # Fallback: return as single claim
    return [text + "."]


DETERMINISTIC_CONTRADICTION_PATTERNS = [
    # Polarity flip: "supports X" vs "does not support X"
    (r"\bdoes\s+not\s+(support|require|use|allow|enable|have|include)\b",
     r"\b(?:supports|requires|uses|allows|enables|has|includes?)\b"),
    # "is not X" vs "is X"
    (r"\bis\s+not\s+", r"\bis\b(?!\s+not\s+)"),
    # Numeric: "X MB" vs "Y MB" for different X,Y
    (r"\b(\d+\.?\d*)\s*(MB|GB|KB|ms|seconds|minutes|GHz|MHz)\b",
     r"\b(\d+\.?\d*)\s*(MB|GB|KB|ms|seconds|minutes|GHz|MHz)\b"),
]


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation for comparison."""
    return re.sub(r"[^a-z0-9\s]", "", text.lower()).strip()


def _detect_contradiction_numeric(a_text: str, b_text: str) -> Optional[str]:
    """Detect numeric contradictions e.g. '256GB' vs '512GB'."""
    nums_a = set(re.findall(r"(\d+\.?\d*)\s*(MB|GB|KB|ms|s|GHz|MHz)", a_text, re.IGNORECASE))
    nums_b = set(re.findall(r"(\d+\.?\d*)\s*(MB|GB|KB|ms|s|GHz|MHz)", b_text, re.IGNORECASE))
    if nums_a and nums_b and nums_a != nums_b:
        return f"numeric conflict: {nums_a} vs {nums_b}"
    return None


def _detect_contradiction_polarity(a_text: str, b_text: str) -> Optional[str]:
    """Detect polarity contradictions like 'supports X' vs 'does not support X'."""
    for neg_pat, pos_pat in DETERMINISTIC_CONTRADICTION_PATTERNS:
        a_neg = bool(re.search(neg_pat, a_text, re.IGNORECASE))
        b_neg = bool(re.search(neg_pat, b_text, re.IGNORECASE))
        a_pos = bool(re.search(pos_pat, a_text, re.IGNORECASE))
        b_pos = bool(re.search(pos_pat, b_text, re.IGNORECASE))
        if (a_neg and b_pos) or (b_neg and a_pos):
            return f"polarity conflict"
    return None


def build_claim_graph(
    evidence_items: List[Dict[str, Any]],
    run_id: str,
) -> Dict[str, Any]:
    """Build a claim graph from evidence items.

    Steps:
    1. Split each evidence assertion into atomic claims.
    2. Deduplicate claims (normalized text match).
    3. Detect contradictions between claims from different sources.
    4. Build graph with claims, edges, and status.
    5. Write to .runtime/workflows/<run_id>/claim_graph.json
    """
    run_dir = WORKFLOW_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    claims: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    claim_counter = 1

    # Collect accepted evidence IDs for classification
    accepted_eids = {ev.get("evidence_id", "")
                     for ev in evidence_items
                     if ev.get("accepted", True) and ev.get("relevance_score", 0.3) >= 0.3}

    # Step 1 & 2: Extract atomic claims from each evidence item
    for ev in evidence_items:
        eid = ev.get("evidence_id", "")
        sid = ev.get("source_id", "")
        assertion = ev.get("assertion", "")
        quote = ev.get("quote", "")
        is_accepted = eid in accepted_eids

        atomic = _split_atomic_claims(assertion)
        if not atomic:
            atomic = _split_atomic_claims(quote)

        for claim_text in atomic:
            norm = _normalize(claim_text)
            if not norm or len(norm) < 5:
                continue

            # Find existing or create new
            existing = None
            for cid, cdata in claims.items():
                if _normalize(cdata["text"]) == norm:
                    existing = cid
                    break

            if existing:
                cid = existing
                claims[cid]["source_ids"].append(sid)
                claims[cid]["evidence_ids"].append(eid)
                claims[cid]["source_ids"] = list(set(claims[cid]["source_ids"]))
                claims[cid]["evidence_ids"] = list(set(claims[cid]["evidence_ids"]))
                claims[cid]["support_count"] += 1
                # Track if any backing evidence is accepted
                if is_accepted:
                    claims[cid]["_has_accepted"] = True
            else:
                cid = f"C{claim_counter}"
                claim_counter += 1
                claims[cid] = {
                    "claim_id": cid,
                    "text": claim_text,
                    "source_ids": [sid],
                    "evidence_ids": [eid],
                    "support_count": 1,
                    "contradiction_count": 0,
                    "confidence": 85,  # high base for evidence-backed
                    "_has_accepted": is_accepted,
                }

            edges.append({"from": cid, "to": eid, "type": "supported_by"})

    # Step 3: Detect contradictions between claims from different sources
    claim_list = list(claims.keys())
    for i in range(len(claim_list)):
        for j in range(i + 1, len(claim_list)):
            ca = claims[claim_list[i]]
            cb = claims[claim_list[j]]
            # Skip if same source — no self-contradiction
            if set(ca["source_ids"]) & set(cb["source_ids"]):
                if _normalize(ca["text"]) == _normalize(cb["text"]):
                    continue

            contradiction = _detect_contradiction_numeric(ca["text"], cb["text"])
            if not contradiction:
                contradiction = _detect_contradiction_polarity(ca["text"], cb["text"])

            if contradiction:
                ca["contradiction_count"] += 1
                cb["contradiction_count"] += 1
                edges.append({
                    "from": ca["claim_id"],
                    "to": cb["claim_id"],
                    "type": "contradicts",
                })

    # Final classification: supported/weak/contradicted/unsupported
    for cid, cdata in claims.items():
        has_accepted = cdata.pop("_has_accepted", False)
        has_evidence = bool(cdata["evidence_ids"])

        if cdata["contradiction_count"] > 0:
            cdata["status"] = "contradicted"
            cdata["confidence"] = max(10, 85 - 25 * cdata["contradiction_count"])
        elif has_accepted:
            # Backed by accepted evidence — supported
            boost = min(15, 5 * (len(cdata["evidence_ids"]) - 1))
            cdata["confidence"] = min(100, 75 + boost)
            cdata["status"] = "supported"
        elif has_evidence:
            # Has evidence but none accepted — weak
            cdata["confidence"] = 30
            cdata["status"] = "weak"
        else:
            # No evidence at all — unsupported
            cdata["confidence"] = 5
            cdata["status"] = "unsupported"

    graph = {
        "claims": list(claims.values()),
        "edges": edges,
    }

    # Write to disk
    graph_path = run_dir / "claim_graph.json"
    graph_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")

    return graph


def read_claim_graph(run_id: str) -> Dict[str, Any]:
    """Read claim_graph.json from a workflow run directory."""
    path = WORKFLOW_RUNS_DIR / run_id / "claim_graph.json"
    if not path.is_file():
        return {"claims": [], "edges": []}
    return json.loads(path.read_text(encoding="utf-8"))


def claim_metrics(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Compute aggregate metrics from a claim graph."""
    claims = graph.get("claims", [])
    total = len(claims)
    if total == 0:
        return {
            "total_claims": 0,
            "supported_count": 0,
            "weak_count": 0,
            "contradicted_count": 0,
            "unsupported_count": 0,
            "claim_confidence_avg": 0.0,
        }

    supported = sum(1 for c in claims if c.get("status") == "supported")
    weak = sum(1 for c in claims if c.get("status") == "weak")
    contradicted = sum(1 for c in claims if c.get("status") == "contradicted")
    unsupported = sum(1 for c in claims if c.get("status") == "unsupported")
    avg_conf = sum(c.get("confidence", 0) for c in claims) / total

    return {
        "total_claims": total,
        "supported_count": supported,
        "weak_count": weak,
        "contradicted_count": contradicted,
        "unsupported_count": unsupported,
        "claim_confidence_avg": round(avg_conf, 1),
    }
