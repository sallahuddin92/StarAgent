from __future__ import annotations

import json
import os
import re
import logging
import uuid
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from .models import MemoryState
from .database import get_db, DatabaseManager

logger = logging.getLogger(__name__)

STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "have", "your", "about",
    "will", "into", "just", "like", "need", "want", "what", "when", "then", "they",
    "were", "them", "than", "into", "using", "use", "can", "does", "dont", "not",
    "yang", "dan", "untuk", "dengan", "ini", "itu", "saya", "kita", "boleh", "nak",
    "apa", "jadi", "dalam", "pada", "akan", "macam", "kalau", "lebih", "kurang"
}


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    return value[:80] or "default"


class MemoryStore:
    """
    Hybrid memory store supporting both SQLite (primary) and JSON (legacy).
    Uses semantic retrieval via embeddings with fallback to heuristic matching.
    """
    def __init__(
        self,
        base_dir: str,
        max_archive_turns: int = 200,
        max_retrieved_items: int = 6,
        use_sqlite: bool = True,
        db_manager: Optional[DatabaseManager] = None
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_archive_turns = max_archive_turns
        self.max_retrieved_items = max_retrieved_items
        self.use_sqlite = use_sqlite
        self.db = db_manager or (get_db() if use_sqlite else None)

    def _path(self, conversation_id: str, project_id: str = "default") -> Path:
        """Get JSON file path for legacy storage."""
        return self.base_dir / f"{slugify(project_id)}-{slugify(conversation_id)}.json"

    def load(self, conversation_id: str, project_id: str = "default") -> MemoryState:
        """Load memory state from SQLite or JSON."""
        if self.use_sqlite and self.db:
            return self._load_from_db(conversation_id, project_id)
        else:
            return self._load_from_json(conversation_id, project_id)
    
    def _load_from_db(self, conversation_id: str, project_id: str = "default") -> MemoryState:
        """Load from SQLite database."""
        state_dict = self.db.get_memory_state(conversation_id, project_id)
        state = MemoryState(
            conversation_id=conversation_id,
            project_id=project_id,
            **state_dict
        )
        # Merge agent continuation state from legacy JSON (SQLite stores only core memory fields).
        # This is intentionally narrow to avoid changing fast-path memory behavior.
        try:
            legacy_path = self._path(conversation_id, project_id)
            if legacy_path.exists():
                legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
                for k in ("pending_approval", "pending_plan", "pending_history", "pending_goal"):
                    if legacy.get(k) is not None:
                        setattr(state, k, legacy.get(k))
        except Exception as e:
            logger.warning(f"Failed to merge legacy agent state for {conversation_id}: {e}")
        return state
    
    def _load_from_json(self, conversation_id: str, project_id: str = "default") -> MemoryState:
        """Load from legacy JSON file."""
        path = self._path(conversation_id, project_id)
        if not path.exists():
            state = MemoryState(conversation_id=conversation_id, project_id=project_id)
            self.save(state)
            return state
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return MemoryState(**data)
        except Exception as e:
            logger.error(f"Failed to load JSON memory from {path}: {e}")
            return MemoryState(conversation_id=conversation_id, project_id=project_id)

    def save(self, state: MemoryState) -> None:
        """Save memory state to SQLite and optionally JSON."""
        if self.use_sqlite and self.db:
            self._save_to_db(state)
        
        # Also save to JSON for backward compatibility
        self._save_to_json(state)

    def _save_to_db(self, state: MemoryState) -> None:
        """Save to SQLite database."""
        try:
            self.db.save_memory_state(
                state.conversation_id,
                state.project_id,
                state.model_dump()
            )
        except Exception as e:
            logger.error(f"Failed to save to database: {e}")

    def _save_to_json(self, state: MemoryState) -> None:
        """Save to JSON file (legacy support)."""
        try:
            self._path(state.conversation_id, state.project_id).write_text(
                json.dumps(state.model_dump(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to save to JSON: {e}")

    def append_turn(
        self,
        state: MemoryState,
        user_text: str,
        assistant_text: str
    ) -> MemoryState:
        """Append user-assistant turn and update memory state."""
        state.archive_turns.append({
            "user": user_text[:4000],
            "assistant": assistant_text[:4000]
        })
        state.archive_turns = state.archive_turns[-self.max_archive_turns:]
        state.turn_count = len(state.archive_turns)
        
        # Update memory via heuristics
        self._heuristic_update(state, user_text, assistant_text)
        
        # Persist changes
        self.save(state)
        
        # Also add to SQLite archive_turns table
        if self.use_sqlite and self.db:
            self.db.add_archive_turn(
                state.conversation_id,
                state.project_id,
                user_text[:4000],
                assistant_text[:4000]
            )
        
        return state

    def retrieve_relevant(
        self,
        state: MemoryState,
        query: str,
        retrieved_items: Optional[List] = None
    ) -> List[str]:
        """
        Retrieve relevant context items.
        Uses semantic search results if provided, falls back to heuristic matching.
        """
        if retrieved_items:
            # Format semantic search results
            results = []
            for item in retrieved_items[:self.max_retrieved_items]:
                category = item.category if hasattr(item, 'category') else item.get('category', '')
                content = item.content if hasattr(item, 'content') else item.get('content', '')
                score = item.score if hasattr(item, 'score') else item.get('score', 0)
                results.append(f"{category.upper()}: {content}")
            return results
        
        # Fallback to heuristic matching
        return self._heuristic_retrieve(state, query)

    def _heuristic_retrieve(self, state: MemoryState, query: str) -> List[str]:
        """Heuristic-based retrieval (fallback when semantic search unavailable)."""
        q_terms = self._terms(query)
        scored: List[tuple[int, str]] = []

        pool = []
        for item in state.project_summary:
            pool.append(f"PROJECT SUMMARY: {item}")
        for item in state.decisions:
            pool.append(f"DECISION: {item}")
        for item in state.constraints:
            pool.append(f"CONSTRAINT: {item}")
        for item in state.issues:
            pool.append(f"ISSUE: {item}")
        for item in state.style_preferences:
            pool.append(f"STYLE: {item}")
        for turn in state.archive_turns[-30:]:
            pool.append(f"PAST USER: {turn['user']}")
            pool.append(f"PAST ASSISTANT: {turn['assistant']}")

        for item in pool:
            terms = self._terms(item)
            overlap = len(q_terms.intersection(terms))
            if overlap > 0:
                scored.append((overlap, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        seen = set()
        results: List[str] = []
        for _, item in scored:
            if item in seen:
                continue
            results.append(item)
            seen.add(item)
            if len(results) >= self.max_retrieved_items:
                break
        return results

    def _terms(self, text: str) -> set[str]:
        """Extract keywords from text."""
        words = re.findall(r"[a-zA-Z0-9_/-]+", text.lower())
        return {w for w in words if len(w) > 2 and w not in STOPWORDS}

    def _heuristic_update(self, state: MemoryState, user_text: str, assistant_text: str) -> None:
        """Update memory state based on heuristic patterns."""
        text = f"{user_text}\n{assistant_text}".strip()
        lower = text.lower()

        remember_patterns = [
            r"remember this decision[:\-]?\s*(.+)",
            r"decision[:\-]?\s*(.+)",
            r"constraint[:\-]?\s*(.+)",
            r"issue[:\-]?\s*(.+)",
            r"style[:\-]?\s*(.+)",
        ]

        for pattern in remember_patterns:
            for match in re.findall(pattern, lower):
                cleaned = match.strip().strip(".")
                if "decision" in pattern:
                    self._push_unique(state.decisions, cleaned)
                elif "constraint" in pattern:
                    self._push_unique(state.constraints, cleaned)
                elif "issue" in pattern:
                    self._push_unique(state.issues, cleaned)
                elif "style" in pattern:
                    self._push_unique(state.style_preferences, cleaned)
                else:
                    self._push_unique(state.project_summary, cleaned)

        explicit_markers = [
            ("offline", state.constraints),
            ("small model", state.constraints),
            ("gemma4:e2b", state.project_summary),
            ("open webui", state.project_summary),
            ("ollama", state.project_summary),
            ("antigravity", state.style_preferences),
            ("prompt", state.style_preferences),
        ]

        for marker, target in explicit_markers:
            if marker in lower:
                self._push_unique(target, marker)

        if user_text:
            concise = user_text.strip().replace("\n", " ")[:220]
            self._push_unique(state.project_summary, concise)

        state.project_summary = state.project_summary[-20:]
        state.decisions = state.decisions[-20:]
        state.constraints = state.constraints[-20:]
        state.issues = state.issues[-20:]
        state.style_preferences = state.style_preferences[-20:]

    def _push_unique(self, items: List[str], value: str) -> None:
        value = value.strip()
        if not value:
            return
        normalized = value.lower()
        existing = {x.lower() for x in items}
        if normalized not in existing:
            items.append(value)
