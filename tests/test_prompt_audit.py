"""Tests for v0.8.4 Prompt Audit Layer."""

from typing import Dict, List, Any

from app.models import MemoryState, PromptAudit
from app.prompting import (
    build_system_prompt,
    build_system_prompt_with_audit,
)
from app.tokenbudget import TokenCounter


# ---------------------------------------------------------------------------
# Helpers (mirror test_token_efficiency.py)
# ---------------------------------------------------------------------------

def _make_memory(
    summary: List[str] = None,
    decisions: List[str] = None,
    constraints: List[str] = None,
    issues: List[str] = None,
    style: List[str] = None,
    enriched: bool = False,
    stale_decisions: List[str] = None,
    superseded_summary: List[str] = None,
    rejected: List[str] = None,
) -> MemoryState:
    """Build a MemoryState with optional authority-enriched items."""
    if enriched:
        def _wrap(items: List[str], status: str = "active") -> List[Dict[str, Any]]:
            return [{"text": t, "status": status} for t in (items or [])]
    else:
        def _wrap(items: List[str], status: str = "active") -> List[str]:
            return list(items or [])

    ms = MemoryState(
        conversation_id="test",
        project_id="test",
        project_summary=_wrap(summary or []),
        decisions=_wrap(decisions or []),
        constraints=_wrap(constraints or []),
        issues=_wrap(issues or []),
        style_preferences=_wrap(style or []),
    )

    if enriched:
        if stale_decisions:
            for d in stale_decisions:
                ms.decisions.append({"text": d, "status": "stale"})
        if superseded_summary:
            for s in superseded_summary:
                ms.project_summary.append({"text": s, "status": "superseded"})
        if rejected:
            for r in rejected:
                ms.project_summary.append({"text": r, "status": "rejected"})
    return ms


def _token_counter() -> TokenCounter:
    return TokenCounter(use_tiktoken=False)


# ---------------------------------------------------------------------------
# TestAuditMetadata — audit contains all expected fields
# ---------------------------------------------------------------------------

class TestAuditMetadata:
    def test_audit_has_all_fields(self):
        """Audit object contains all required metadata fields."""
        memory = _make_memory(summary=["Test"], decisions=["A"])
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=[], mode="compact"
        )
        assert isinstance(audit, PromptAudit)
        assert audit.mode == "compact"
        assert isinstance(audit.estimated_tokens_total, int)
        assert isinstance(audit.sections_kept, list)
        assert isinstance(audit.sections_dropped, list)
        assert isinstance(audit.section_tokens, dict)

    def test_audit_reports_token_counts(self):
        """Audit estimated_tokens_total should be > 0 for a non-empty prompt."""
        memory = _make_memory(
            summary=["Project summary with enough text to have tokens"],
            decisions=["Important decision about architecture"],
        )
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=["[ACTIVE] RETRIEVED: some context"],
            mode="compact", token_counter=_token_counter(),
        )
        assert audit.estimated_tokens_total > 0

    def test_audit_mode_reflects_input(self):
        """Audit mode matches the mode parameter passed in."""
        memory = _make_memory(summary=["Test"])
        for mode in ("normal", "compact"):
            _prompt, audit = build_system_prompt_with_audit(
                memory, retrieved_items=[], mode=mode,
            )
            assert audit.mode == mode


# ---------------------------------------------------------------------------
# TestAuditDroppedSections — dropped sections correctly reported
# ---------------------------------------------------------------------------

class TestAuditDroppedSections:
    def test_no_drops_with_high_budget(self):
        """High token budget means no sections dropped."""
        memory = _make_memory(decisions=["A"], summary=["B"])
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=["item"],
            mode="compact", max_memory_tokens=4096,
        )
        assert audit.sections_dropped == []

    def test_drops_retrieved_at_low_budget(self):
        """Low token budget drops retrieved context."""
        memory = _make_memory(
            decisions=["A" * 200],
            summary=["B" * 200],
            constraints=["C" * 200],
            style=["D" * 200],
            issues=["E" * 200],
        )
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=["R" * 200],
            mode="compact", max_memory_tokens=128,
        )
        assert "retrieved" in audit.sections_dropped or len(audit.sections_kept) <= 2

    def test_drops_historical_at_low_budget(self):
        """Dropped reason is set when sections are trimmed."""
        memory = _make_memory(
            decisions=["Active decision", "Another one"],
            enriched=True,
            stale_decisions=["Old stale"],
        )
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=["Some context"],
            mode="compact", max_memory_tokens=128,
            include_historical=True,
        )
        if "historical" in audit.sections_dropped:
            assert audit.dropped_reason == "token_budget_exceeded"

    def test_dropped_reason_none_when_no_drops(self):
        """dropped_reason is None when no sections are dropped."""
        memory = _make_memory(decisions=["A"])
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=[], mode="compact",
            max_memory_tokens=4096,
        )
        assert audit.dropped_reason is None


# ---------------------------------------------------------------------------
# TestAuditCounts — item counts correct
# ---------------------------------------------------------------------------

class TestAuditCounts:
    def test_audit_rejected_excluded_count(self):
        """Rejected items are counted in rejected_excluded_count."""
        memory = _make_memory(
            summary=["Good summary"],
            enriched=True,
            rejected=["Bad idea", "Won't implement"],
        )
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=[], mode="compact",
        )
        assert audit.rejected_excluded_count >= 2

    def test_audit_active_memory_count(self):
        """Active memory count reflects all active items across categories."""
        memory = _make_memory(
            summary=["S1", "S2"],
            decisions=["D1", "D2", "D3"],
            constraints=["C1"],
            style=["St1"],
            issues=[],
        )
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=[], mode="compact",
        )
        assert audit.active_memory_count == 7  # 2 + 3 + 1 + 1 + 0

    def test_audit_conflicts_count(self):
        """Conflict count reflects number of conflicts passed."""
        memory = _make_memory(decisions=["A", "B"])
        conflicts = [
            {"category": "decisions", "items": ["A", "B"], "similarity": 0.5},
        ]
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=[], conflicts=conflicts, mode="compact",
        )
        assert audit.conflicts_count == 1

    def test_audit_no_conflicts_when_none(self):
        """Conflicts count is 0 when no conflicts passed."""
        memory = _make_memory(decisions=["A"])
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=[], mode="compact",
        )
        assert audit.conflicts_count == 0

    def test_audit_retrieved_count(self):
        """Retrieved count matches number of retrieved items."""
        memory = _make_memory(decisions=["A"])
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=["item1", "item2", "item3"], mode="compact",
        )
        assert audit.retrieved_count == 3

    def test_audit_max_memory_tokens(self):
        """max_memory_tokens reflects the budget passed in."""
        memory = _make_memory(decisions=["A"])
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=[], mode="compact", max_memory_tokens=512,
        )
        assert audit.max_memory_tokens == 512


# ---------------------------------------------------------------------------
# TestAuditSectionTokens — per-section token breakdown
# ---------------------------------------------------------------------------

class TestAuditSectionTokens:
    def test_section_tokens_has_keys(self):
        """Section tokens dict contains keys for present sections."""
        memory = _make_memory(decisions=["Active decision"])
        _prompt, audit = build_system_prompt_with_audit(
            memory, retrieved_items=["retrieved context"], mode="compact",
        )
        assert "active_memory" in audit.section_tokens
        assert "retrieved" in audit.section_tokens


# ---------------------------------------------------------------------------
# TestAuditBackwardCompat — normal API unchanged
# ---------------------------------------------------------------------------

class TestAuditBackwardCompat:
    def test_build_system_prompt_returns_str(self):
        """build_system_prompt still returns a plain string."""
        memory = _make_memory(summary=["Test"])
        result = build_system_prompt(memory, retrieved_items=[])
        assert isinstance(result, str)

    def test_normal_chat_api_no_audit(self):
        """build_system_prompt_with_audit returns tuple with str + PromptAudit."""
        memory = _make_memory(summary=["Test"])
        result = build_system_prompt_with_audit(memory, retrieved_items=[])
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], PromptAudit)

    def test_compact_mode_audit_shows_smaller_tokens(self):
        """Compact mode audit shows meaningfully smaller than normal."""
        memory = _make_memory(
            summary=["Item " * 20],
            decisions=["Decision " * 20],
            constraints=["Constraint " * 20],
            style=["Style " * 20],
            issues=["Issue " * 20],
        )
        retrieved = ["Context " * 10] * 5
        counter = _token_counter()

        _normal_prompt, normal_audit = build_system_prompt_with_audit(
            memory, retrieved_items=retrieved, mode="normal",
            token_counter=counter,
        )
        _compact_prompt, compact_audit = build_system_prompt_with_audit(
            memory, retrieved_items=retrieved, mode="compact",
            max_memory_tokens=2048, token_counter=counter,
        )
        assert compact_audit.estimated_tokens_total < normal_audit.estimated_tokens_total
