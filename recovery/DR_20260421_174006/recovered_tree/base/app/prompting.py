from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from .models import MemoryState

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"


def load_template(name: str) -> str:
    path = TEMPLATES_DIR / name
    return path.read_text(encoding="utf-8")


def build_system_prompt(
    memory: MemoryState,
    retrieved_items: List[str],
    custom_prompt: Optional[str] = None
) -> str:
    """Build system prompt with memory context and retrieved items."""
    base = custom_prompt or load_template("system_prompt.txt")

    lines = [base.strip(), "", "[WORKING MEMORY]"]

    def section(title: str, items: List[str]) -> None:
        lines.append(f"{title}:")
        if items:
            for item in items[-10:]:
                lines.append(f"- {item}")
        else:
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

    lines.append("")
    lines.append("[RULES]")
    lines.append("- Prefer continuity with prior project decisions.")
    lines.append("- Do not rely on raw long chat history.")
    lines.append("- Use the memory sections above as the source of continuity.")
    lines.append("- Keep outputs copy-paste ready when the task is prompt-writing or implementation planning.")
    lines.append("- If context is insufficient, say clearly what is missing.")

    return "\n".join(lines)


class MemoryCompactor:
    """LLM-based memory compaction and summarization."""
    
    def __init__(self, ollama_client = None, default_model: str = "gemma4:e2b"):
        """
        Initialize compactor.
        
        Args:
            ollama_client: Async HTTP client for Ollama (injected from main.py)
            default_model: Model to use for compaction
        """
        self.ollama_client = ollama_client
        self.default_model = default_model
    
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
        # Merge with existing items (preserve old + add new insights)
        new_state = MemoryState(
            conversation_id=memory.conversation_id,
            project_id=memory.project_id,
            project_summary=memory.project_summary + compacted.get("project_summary", []),
            decisions=memory.decisions + compacted.get("decisions", []),
            constraints=memory.constraints + compacted.get("constraints", []),
            issues=memory.issues + compacted.get("issues", []),
            style_preferences=memory.style_preferences + compacted.get("style_preferences", []),
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

