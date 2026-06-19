from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from .models import MemoryState, PromptAudit
from .tokenbudget import TokenCounter

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"


def load_template(name: str) -> str:
    path = TEMPLATES_DIR / name
    return path.read_text(encoding="utf-8")


def _count_prompt_tokens(text: str, token_counter: Optional[TokenCounter] = None) -> int:
    """Count tokens in prompt text using TokenCounter or fallback estimation."""
    if token_counter is not None:
        return token_counter.count_tokens(text)
    # Fallback: approximate character-based estimate
    return len(text) // 4


def _format_compact_memory(memory: MemoryState) -> str:
    """Render working memory as compact single-line-per-category format.

    Instead of verbose bullet lists, each category is rendered as a
    single line with comma-separated items:

        summary: item1, item2, item3
        decisions: item1, item2

    Only includes active/unknown status items. Truncates each item to 100 chars.
    """
    lines: List[str] = []
    for field, label in (
        ("project_summary", "summary"),
        ("decisions", "decisions"),
        ("constraints", "constraints"),
        ("issues", "issues"),
        ("style_preferences", "style"),
    ):
        items = getattr(memory, field, [])
        active = []
        for item in items[-20:]:
            if isinstance(item, dict):
                status = item.get("status", "unknown")
                text = item.get("text", "")
                if status in ("active", "unknown") and text:
                    active.append(text.strip().rstrip(".,;!?")[:100])
            elif isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    active.append(stripped.rstrip(".,;!?")[:100])
        if active:
            lines.append(f"{label}: {', '.join(active)}")
    if not lines:
        lines.append("(empty)")
    return "\n".join(lines)


def _format_normal_memory(memory: MemoryState) -> List[str]:
    """Render working memory as bullet-point format (current behavior).

    Returns list of lines to be joined by the caller.
    """
    lines: List[str] = ["[ACTIVE MEMORY]"]

    def section(title: str, items: List) -> None:
        """Render a working memory section, showing only active items."""
        lines.append(f"{title}:")
        shown = 0
        for item in items[-20:]:
            if isinstance(item, dict):
                status = item.get("status", "unknown")
                text = item.get("text", "")
                if status in ("active", "unknown"):
                    lines.append(f"- {text}")
                    shown += 1
            elif isinstance(item, str):
                lines.append(f"- {item}")
                shown += 1
            if shown >= 10:
                break
        if shown == 0:
            lines.append("- none")

    section("PROJECT SUMMARY", memory.project_summary)
    section("DECISIONS", memory.decisions)
    section("CONSTRAINTS", memory.constraints)
    section("ISSUES", memory.issues)
    section("STYLE PREFERENCES", memory.style_preferences)
    return lines


def _trim_by_token_budget(
    sections: Dict[str, str],
    max_tokens: int,
    token_counter: Optional[TokenCounter] = None,
) -> str:
    """Trim sections to fit within token budget, preserving priority order.

    Sections dict keys are priority-ordered (highest first). Lowest-priority
    sections are dropped first. Remaining sections are joined with newlines.
    """
    result_parts: List[str] = []
    running_tokens = 0

    for section_name, section_text in sections.items():
        section_tokens = _count_prompt_tokens(section_text, token_counter)
        if running_tokens + section_tokens <= max_tokens:
            result_parts.append(section_text)
            running_tokens += section_tokens
        elif not result_parts:
            # Even the highest-priority section doesn't fit — truncate it
            max_chars = max_tokens * 4
            truncated = section_text[:max_chars].rsplit("\n", 1)[0] if "\n" in section_text[:max_chars] else section_text[:max_chars]
            result_parts.append(truncated + "\n…")
            break
        else:
            # This section doesn't fit — stop adding
            break

    return "\n".join(result_parts)


def _trim_by_token_budget_with_audit(
    sections: Dict[str, str],
    max_tokens: int,
    token_counter: Optional[TokenCounter] = None,
) -> Tuple[str, List[str]]:
    """Trim sections to fit within token budget, returning kept text and dropped keys.

    Returns:
        Tuple of (joined_kept_sections, list_of_dropped_section_keys).
    """
    result_parts: List[str] = []
    dropped: List[str] = []
    running_tokens = 0

    for section_name, section_text in sections.items():
        section_tokens = _count_prompt_tokens(section_text, token_counter)
        if running_tokens + section_tokens <= max_tokens:
            result_parts.append(section_text)
            running_tokens += section_tokens
        elif not result_parts:
            # Even the highest-priority section doesn't fit — truncate it
            max_chars = max_tokens * 4
            truncated = section_text[:max_chars].rsplit("\n", 1)[0] if "\n" in section_text[:max_chars] else section_text[:max_chars]
            result_parts.append(truncated + "\n…")
            break
        else:
            dropped.append(section_name)
            break

    # Collect any remaining sections that were never reached
    all_keys = list(sections.keys())
    started_dropping = False
    for key in all_keys:
        if key not in dropped and key not in [k for k in all_keys if any(k.startswith(kk) for kk in dropped)]:
            # Simple check: if we've started dropping, everything after is dropped
            pass
    # Actually simpler: once we hit a section that doesn't fit, everything after is dropped
    for key in all_keys[len(result_parts):]:
        if key not in dropped:
            dropped.append(key)

    return "\n".join(result_parts), dropped


def _build_normal_prompt_with_audit(
    base: str,
    memory: MemoryState,
    retrieved_items: List[str],
    conflicts: Optional[List[Dict[str, Any]]],
    include_historical: bool,
) -> Tuple[str, PromptAudit]:
    """Build system prompt in normal (bullet) mode with audit tracking."""
    lines: List[str] = [base, "", "[ACTIVE MEMORY]"]
    active_total = 0

    def section(title: str, items: List) -> None:
        nonlocal active_total
        lines.append(f"{title}:")
        shown = 0
        for item in items[-20:]:
            if isinstance(item, dict):
                status = item.get("status", "unknown")
                text = item.get("text", "")
                if status in ("active", "unknown"):
                    lines.append(f"- {text}")
                    shown += 1
                    active_total += 1
            elif isinstance(item, str):
                lines.append(f"- {item}")
                shown += 1
                active_total += 1
            if shown >= 10:
                break
        if shown == 0:
            lines.append("- none")

    section("PROJECT SUMMARY", memory.project_summary)
    section("DECISIONS", memory.decisions)
    section("CONSTRAINTS", memory.constraints)
    section("ISSUES", memory.issues)
    section("STYLE PREFERENCES", memory.style_preferences)

    lines.append("")
    lines.append("[RETRIEVED PRIOR CONTEXT]")
    if retrieved_items:
        for item in retrieved_items:
            lines.append(f"- {item}")
    else:
        lines.append("- none")

    conflicts_count = 0
    if conflicts:
        conflicts_count = len(conflicts)
        lines.append("")
        lines.append("[UNRESOLVED CONFLICTS]")
        lines.append("The following active memories conflict with each other:")
        for conflict in conflicts:
            cat = conflict.get("category", "unknown")
            items = conflict.get("items", [])
            sim = conflict.get("similarity", 0)
            lines.append(f"  [{cat}] (similarity: {sim})")
            for item_text in items:
                lines.append(f"  - \"{item_text[:120]}\"")
        lines.append("")
        lines.append("Ask the user to resolve these conflicts before continuing.")

    # Count rejected before building historical
    rejected_excluded = 0
    for field_name in ("project_summary", "decisions", "constraints", "issues", "style_preferences"):
        for item in getattr(memory, field_name, []):
            if isinstance(item, dict) and item.get("status") == "rejected":
                rejected_excluded += 1

    # HISTORICAL / SUPERSEDED MEMORY
    historical_items_list = []
    for field_name in ("project_summary", "decisions", "constraints", "issues", "style_preferences"):
        items = getattr(memory, field_name, [])
        for item in items:
            if isinstance(item, dict):
                status = item.get("status", "unknown")
                text = item.get("text", "")
                if status in ("superseded", "stale") and text:
                    historical_items_list.append((status, field_name.upper(), text))
    if include_historical and historical_items_list:
        lines.append("")
        lines.append("[HISTORICAL / SUPERSEDED MEMORY]")
        lines.append("The following items have been superseded or marked as stale:")
        for status, field, text in historical_items_list[:10]:
            lines.append(f"- [{status.upper()}] {field}: {text}")

    lines.append("")
    lines.append("[RULES]")
    lines.append("- ALWAYS respond in English unless the user explicitly requests another language.")
    lines.append("- Prefer continuity with prior project decisions.")
    lines.append("- Do not rely on raw long chat history.")
    lines.append("- Use the ACTIVE MEMORY sections above as the source of continuity.")
    lines.append("- Keep outputs copy-paste ready when the task is prompt-writing or implementation planning.")
    lines.append("- If context is insufficient, say clearly what is missing.")
    lines.append("- If [UNRESOLVED CONFLICTS] exists, ask the user to resolve before proceeding.")

    prompt_text = "\n".join(lines)

    audit = PromptAudit(
        mode="normal",
        estimated_tokens_total=_count_prompt_tokens(prompt_text),
        sections_kept=["active_memory", "retrieved", "conflicts", "historical", "rules"],
        sections_dropped=[],
        conflicts_count=conflicts_count,
        active_memory_count=active_total,
        retrieved_count=len(retrieved_items),
        historical_count=len(historical_items_list) if include_historical else 0,
        rejected_excluded_count=rejected_excluded,
    )

    return prompt_text, audit


# ============================================================================
# Internal: shared entry point used by both build_system_prompt (str-only)
# and build_system_prompt_with_audit (str + PromptAudit).
# ============================================================================


def _build_system_prompt_internal(
    memory: MemoryState,
    retrieved_items: List[str],
    custom_prompt: Optional[str] = None,
    conflicts: Optional[List[Dict[str, Any]]] = None,
    *,
    mode: str = "normal",
    max_memory_tokens: Optional[int] = None,
    include_historical: bool = False,
    token_counter: Optional[TokenCounter] = None,
) -> Tuple[str, PromptAudit]:
    """Build system prompt with audit tracking.

    Returns (prompt_text, audit) tuple.
    """
    base = custom_prompt or load_template("system_prompt.txt")
    base = base.strip()

    # Count rejected items from memory
    rejected_excluded = 0
    for field_name in ("project_summary", "decisions", "constraints", "issues", "style_preferences"):
        for item in getattr(memory, field_name, []):
            if isinstance(item, dict) and item.get("status") == "rejected":
                rejected_excluded += 1

    if mode == "compact":
        return _build_compact_prompt_with_audit(
            base=base,
            memory=memory,
            retrieved_items=retrieved_items,
            conflicts=conflicts,
            max_memory_tokens=max_memory_tokens or 1024,
            include_historical=include_historical,
            token_counter=token_counter,
            rejected_excluded=rejected_excluded,
        )

    return _build_normal_prompt_with_audit(
        base=base,
        memory=memory,
        retrieved_items=retrieved_items,
        conflicts=conflicts,
        include_historical=include_historical,
    )


def build_system_prompt(
    memory: MemoryState,
    retrieved_items: List[str],
    custom_prompt: Optional[str] = None,
    conflicts: Optional[List[Dict[str, Any]]] = None,
    *,
    mode: str = "normal",
    max_memory_tokens: Optional[int] = None,
    include_historical: bool = False,
    token_counter: Optional[TokenCounter] = None,
) -> str:
    """Build system prompt with authority-aware memory context.

    Wraps _build_system_prompt_internal and returns only the prompt string.
    See build_system_prompt_with_audit for audit metadata.
    """
    prompt_text, _audit = _build_system_prompt_internal(
        memory=memory,
        retrieved_items=retrieved_items,
        custom_prompt=custom_prompt,
        conflicts=conflicts,
        mode=mode,
        max_memory_tokens=max_memory_tokens,
        include_historical=include_historical,
        token_counter=token_counter,
    )
    return prompt_text


def build_system_prompt_with_audit(
    memory: MemoryState,
    retrieved_items: List[str],
    custom_prompt: Optional[str] = None,
    conflicts: Optional[List[Dict[str, Any]]] = None,
    *,
    mode: str = "normal",
    max_memory_tokens: Optional[int] = None,
    include_historical: bool = False,
    token_counter: Optional[TokenCounter] = None,
) -> Tuple[str, PromptAudit]:
    """Build system prompt with audit metadata (v0.8.4).

    Same parameters as build_system_prompt, but returns (prompt_text, audit)
    where audit is a PromptAudit instance with section breakdowns.
    """
    return _build_system_prompt_internal(
        memory=memory,
        retrieved_items=retrieved_items,
        custom_prompt=custom_prompt,
        conflicts=conflicts,
        mode=mode,
        max_memory_tokens=max_memory_tokens,
        include_historical=include_historical,
        token_counter=token_counter,
    )


def _build_compact_prompt_with_audit(
    base: str,
    memory: MemoryState,
    retrieved_items: List[str],
    conflicts: Optional[List[Dict[str, Any]]],
    max_memory_tokens: int,
    include_historical: bool,
    token_counter: Optional[TokenCounter],
    rejected_excluded: int = 0,
) -> Tuple[str, PromptAudit]:
    """Build system prompt in compact mode with audit tracking.

    Priority order (highest first):
    1. [UNRESOLVED CONFLICTS]
    2. [ACTIVE MEMORY] (compact single-line format)
    3. [RETRIEVED PRIOR CONTEXT]
    4. [HISTORICAL / SUPERSEDED MEMORY]

    Returns (prompt_text, audit).
    """
    sections: Dict[str, str] = {}
    all_section_keys: List[str] = []
    active_memory_count = 0
    conflicts_count = 0
    historical_count = 0

    # Count active items
    for field_name in ("project_summary", "decisions", "constraints", "issues", "style_preferences"):
        for item in getattr(memory, field_name, []):
            if isinstance(item, dict):
                status = item.get("status", "unknown")
                if status in ("active", "unknown"):
                    active_memory_count += 1
            elif isinstance(item, str):
                active_memory_count += 1

    # 1. UNRESOLVED CONFLICTS (highest priority)
    if conflicts:
        conflicts_count = len(conflicts)
        conflict_lines = [
            "[UNRESOLVED CONFLICTS]",
            "The following active memories conflict with each other:",
        ]
        for conflict in conflicts:
            cat = conflict.get("category", "unknown")
            items = conflict.get("items", [])
            sim = conflict.get("similarity", 0)
            conflict_lines.append(f"  [{cat}] (similarity: {sim})")
            for item_text in items:
                conflict_lines.append(f"  - \"{item_text[:120]}\"")
        conflict_lines.append("")
        conflict_lines.append("Ask the user to resolve these conflicts before continuing.")
        sections["conflicts"] = "\n".join(conflict_lines)
        all_section_keys.append("conflicts")

    # 2. ACTIVE MEMORY (compact single-line format)
    active_json = _format_compact_memory(memory)
    sections["active_memory"] = f"[ACTIVE MEMORY]\n{active_json}"
    all_section_keys.append("active_memory")

    # 3. RETRIEVED PRIOR CONTEXT
    retrieved_section = "[RETRIEVED PRIOR CONTEXT]\n"
    if retrieved_items:
        retrieved_section += "\n".join(f"- {item}" for item in retrieved_items)
    else:
        retrieved_section += "- none"
    sections["retrieved"] = retrieved_section
    all_section_keys.append("retrieved")

    # 4. HISTORICAL / SUPERSEDED MEMORY (lowest priority — only if requested)
    historical_items_list = []
    if include_historical:
        for field_name in ("project_summary", "decisions", "constraints", "issues", "style_preferences"):
            items = getattr(memory, field_name, [])
            for item in items:
                if isinstance(item, dict):
                    status = item.get("status", "unknown")
                    text = item.get("text", "")
                    if status in ("superseded", "stale") and text:
                        historical_items_list.append((status, field_name.upper(), text))
        historical_count = len(historical_items_list)
        if historical_items_list:
            hist_lines = [
                "[HISTORICAL / SUPERSEDED MEMORY]",
                "The following items have been superseded or marked as stale:",
            ]
            for status, field, text in historical_items_list[:10]:
                hist_lines.append(f"- [{status.upper()}] {field}: {text}")
            sections["historical"] = "\n".join(hist_lines)
            all_section_keys.append("historical")

    # Build rules section (always appended after trimmed block, not subject to budget)
    rules = "\n".join([
        "[RULES]",
        "- ALWAYS respond in English unless the user explicitly requests another language.",
        "- Prefer continuity with prior project decisions.",
        "- Do not rely on raw long chat history.",
        "- Use the ACTIVE MEMORY sections above as the source of continuity.",
        "- Keep outputs copy-paste ready when the task is prompt-writing or implementation planning.",
        "- If context is insufficient, say clearly what is missing.",
        "- If [UNRESOLVED CONFLICTS] exists, ask the user to resolve before proceeding.",
    ])

    # Trim memory+context sections to budget
    trimmed, dropped_keys = _trim_by_token_budget_with_audit(
        sections, max_memory_tokens, token_counter
    )

    # Build section token breakdown
    section_tokens: Dict[str, int] = {}
    for key in all_section_keys:
        if key in sections:
            section_tokens[key] = _count_prompt_tokens(sections[key], token_counter)

    kept_keys = [k for k in all_section_keys if k in sections and k not in dropped_keys]
    dropped_keys_final = [k for k in all_section_keys if k in dropped_keys]

    # Assemble final prompt: base + trimmed memory block + rules
    if trimmed:
        prompt_text = f"{base}\n\n{trimmed}\n\n{rules}"
    else:
        prompt_text = f"{base}\n\n{rules}"

    audit = PromptAudit(
        mode="compact",
        estimated_tokens_total=_count_prompt_tokens(prompt_text, token_counter),
        max_memory_tokens=max_memory_tokens,
        sections_kept=kept_keys,
        sections_dropped=dropped_keys_final,
        dropped_reason="token_budget_exceeded" if dropped_keys_final else None,
        conflicts_count=conflicts_count,
        active_memory_count=active_memory_count,
        retrieved_count=len(retrieved_items),
        historical_count=historical_count,
        rejected_excluded_count=rejected_excluded,
        section_tokens=section_tokens,
    )

    return prompt_text, audit


class MemoryCompactor:
    """LLM-based memory compaction and summarization."""
    
    def __init__(self, ollama_client = None, default_model: Optional[str] = None):
        """
        Initialize compactor.
        
        Args:
            ollama_client: Async HTTP client for Ollama (injected from main.py)
            default_model: Model to use for compaction
        """
        self.ollama_client = ollama_client
        self._default_model = default_model

    @property
    def default_model(self) -> str:
        if self._default_model:
            return self._default_model
        from .model_registry import get_effective_model_config
        return get_effective_model_config()["model"]
    
    async def should_compact(self, memory: MemoryState, force: bool = False) -> bool:
        """
        Determine if memory should be compacted.
        
        Triggers on:
        - Force flag set
        - Turn count exceeds threshold (100 turns)
        - Last compaction > 48 hours ago
        """
        if force:
            return True
        
        if memory.turn_count < 100:
            return False
        
        # Would need to track last_compaction in database
        return True
    
    async def compact(
        self,
        memory: MemoryState,
        db_manager = None
    ) -> Dict[str, Any]:
        """
        Compact conversation memory using LLM.
        
        Creates structured summary from archive_turns while preserving key decisions.
        Updates memory state with compacted results.
        
        Returns:
            {
                "items_compacted": int,
                "items_created": int,
                "summary": str,
                "new_memory_state": MemoryState
            }
        """
        if not memory.archive_turns:
            return {
                "items_compacted": 0,
                "items_created": 0,
                "summary": "No turns to compact",
                "new_memory_state": memory
            }
        
        # Prepare conversation history for compaction
        turns_text = "\n".join([
            f"User: {turn['user']}\nAssistant: {turn['assistant']}\n"
            for turn in memory.archive_turns[-50:]  # Last 50 turns
        ])
        
        compaction_prompt = self._build_compaction_prompt(
            turns_text,
            memory
        )
        
        try:
            # Call Ollama to generate compacted summary
            summary = await self._call_ollama_compact(compaction_prompt)
            
            # Parse structured output
            compacted = self._parse_compaction_output(summary)
            
            # Update memory state
            updated_state = self._apply_compaction(memory, compacted)
            updated_state.turn_count = len(updated_state.archive_turns)
            
            logger.info(
                f"Compacted memory for {memory.conversation_id}: "
                f"{len(memory.archive_turns)} → {len(updated_state.archive_turns)} turns"
            )
            
            return {
                "items_compacted": len(memory.archive_turns),
                "items_created": len(updated_state.archive_turns),
                "summary": summary[:500],  # Truncate for response
                "new_memory_state": updated_state
            }
        except Exception as e:
            logger.error(f"Compaction failed for {memory.conversation_id}: {e}")
            return {
                "items_compacted": 0,
                "items_created": 0,
                "summary": f"Compaction failed: {str(e)}",
                "new_memory_state": memory
            }
    
    def _build_compaction_prompt(self, turns_text: str, memory: MemoryState) -> str:
        """Build prompt for LLM-based compaction."""
        custom = load_template("memory_compactor_prompt.txt") if TEMPLATES_DIR.exists() else ""
        
        if not custom:
            custom = """You are a memory compaction assistant. Analyze conversation turns and:
1. Identify key decisions made (store in 'decisions')
2. Extract important constraints discovered (store in 'constraints')
3. Note blockers or issues (store in 'issues')
4. Extract code/style preferences (store in 'style_preferences')
5. Summarize project context (store in 'project_summary')

Return valid JSON with these keys. Keep descriptions under 100 words each."""
        
        return f"""{custom}

CURRENT MEMORY STATE:
- Project Summary: {memory.project_summary}
- Decisions: {memory.decisions}
- Constraints: {memory.constraints}
- Issues: {memory.issues}
- Style Preferences: {memory.style_preferences}

CONVERSATION TO ANALYZE:
{turns_text}

Return JSON object with keys: decisions, constraints, issues, style_preferences, project_summary"""
    
    async def _call_ollama_compact(self, prompt: str) -> str:
        """Call Ollama to generate compaction summary."""
        if not self.ollama_client:
            logger.warning("No Ollama client available for compaction")
            return ""
        
        try:
            response = await self.ollama_client.post(
                "http://127.0.0.1:11434/api/chat",
                json={
                    "model": self.default_model,
                    "messages": [
                        {"role": "system", "content": "You are a memory compaction expert."},
                        {"role": "user", "content": prompt}
                    ],
                    "stream": False,
                    "temperature": 0.3
                },
                timeout=120.0
            )
            
            if response.status_code == 200:
                data = response.json()
                message = data.get("message", {})
                return message.get("content", "")
        except Exception as e:
            logger.error(f"Ollama compaction call failed: {e}")
        
        return ""
    
    def _parse_compaction_output(self, text: str) -> Dict[str, List[str]]:
        """Parse LLM output into structured memory items."""
        try:
            # Try to extract JSON from response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = text[start:end]
                data = json.loads(json_str)
                return {
                    "decisions": data.get("decisions", []),
                    "constraints": data.get("constraints", []),
                    "issues": data.get("issues", []),
                    "style_preferences": data.get("style_preferences", []),
                    "project_summary": data.get("project_summary", [])
                }
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse compaction JSON: {e}")
        
        # Fallback: return empty structure
        return {
            "decisions": [],
            "constraints": [],
            "issues": [],
            "style_preferences": [],
            "project_summary": []
        }
    
    def _apply_compaction(
        self,
        memory: MemoryState,
        compacted: Dict[str, List[str]]
    ) -> MemoryState:
        """Apply compacted results to memory state."""
        # Collect existing items — handle both enriched dicts and plain strings
        def collect(items: List) -> List[str]:
            """Extract text from enriched or plain items."""
            result = []
            for item in items:
                if isinstance(item, dict):
                    result.append(item.get("text", str(item)))
                else:
                    result.append(str(item))
            return result

        # Merge with existing items (preserve old + add new insights)
        new_state = MemoryState(
            conversation_id=memory.conversation_id,
            project_id=memory.project_id,
            project_summary=collect(memory.project_summary) + compacted.get("project_summary", []),
            decisions=collect(memory.decisions) + compacted.get("decisions", []),
            constraints=collect(memory.constraints) + compacted.get("constraints", []),
            issues=collect(memory.issues) + compacted.get("issues", []),
            style_preferences=collect(memory.style_preferences) + compacted.get("style_preferences", []),
            archive_turns=memory.archive_turns[-10:],  # Keep only recent turns
            turn_count=memory.turn_count
        )

        # Deduplicate and limit
        new_state.project_summary = list(set(new_state.project_summary))[:20]
        new_state.decisions = list(set(new_state.decisions))[:20]
        new_state.constraints = list(set(new_state.constraints))[:20]
        new_state.issues = list(set(new_state.issues))[:20]
        new_state.style_preferences = list(set(new_state.style_preferences))[:20]

        return new_state

