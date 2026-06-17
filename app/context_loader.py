import os
import logging
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from .tokenbudget import TokenCounter
from .model_profiles import get_active_profile
from .model_registry import registry

logger = logging.getLogger(__name__)

class ContextLoader:
    """
    Implements layered context loading:
    Workflow Context -> Stage Context -> Project Context -> Task Context -> Docs Context.
    Ensures context stays within the model's token budget.
    """
    def __init__(self, token_counter: Optional[TokenCounter] = None):
        self.counter = token_counter or TokenCounter()

    def load_layered_context(
        self,
        workflow_dir: Path,
        stage_name: str,
        stage_purpose: str,
        project_id: str,
        user_goal: str,
        docs_context: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> str:
        """
        Loads all context layers, budgets tokens, and merges them.
        """
        # 1. Resolve model profile token limit
        model = model_id or registry.global_default
        provider = registry.infer_provider(model)
        profile = get_active_profile(provider, model)
        limit = profile.context_limit if profile else 8192
        
        # Override for groq to avoid 413 Payload Too Large / TPM limits
        if provider == "groq":
            limit = 4096
            reserved_overhead = 1500
        else:
            reserved_overhead = 2000
            
        budget = max(1000, limit - reserved_overhead)

        # 2. Load layers
        from .model_registry import is_compact_prompts_enabled

        # Layer 1: Workflow Context
        workflow_ctx = ""
        if not is_compact_prompts_enabled(model_id):
            context_md_path = workflow_dir / "CONTEXT.md"
            if context_md_path.exists():
                try:
                    workflow_ctx = context_md_path.read_text(encoding="utf-8")
                except Exception as e:
                    logger.error(f"Failed to read CONTEXT.md: {e}")

        # Layer 2: Stage Context
        stage_ctx = f"Stage: {stage_name.upper()}\nPurpose: {stage_purpose}"

        # Layer 3: Project Context
        project_ctx = f"Project Scope: {project_id}\nWorkspace Path: {os.path.abspath('.')}"

        # Layer 4: Task Context
        task_ctx = f"Target Goal:\n{user_goal}"

        # Layer 5: Docs Context
        docs_ctx = ""
        if not is_compact_prompts_enabled(model_id):
            docs_ctx = docs_context or ""

        # 3. Budgeting (trim layers from bottom to top if budget exceeded)
        # We assign priorities:
        # P1 (Highest): Workflow Context & Stage Context
        # P2: Project Context
        # P3: Task Context
        # P4 (Lowest): Docs Context
        
        layers = [
            {"name": "Workflow", "content": workflow_ctx, "priority": 1},
            {"name": "Stage", "content": stage_ctx, "priority": 1},
            {"name": "Project", "content": project_ctx, "priority": 2},
            {"name": "Task", "content": task_ctx, "priority": 3},
            {"name": "Docs", "content": docs_ctx, "priority": 4},
        ]

        # Count tokens per layer
        for l in layers:
            l["tokens"] = self.counter.count_tokens(l["content"])

        total_tokens = sum(l["tokens"] for l in layers)

        if total_tokens > budget:
            logger.info(f"Layered context token count ({total_tokens}) exceeds budget ({budget}). Budgeting layers...")
            # Trim from lowest priority (highest priority number)
            # For simplicity, we trim Docs Context first, then Task, etc.
            excess = total_tokens - budget
            
            # Trim Docs Context (priority 4)
            if layers[4]["tokens"] > 0:
                trimmed_tokens = max(0, layers[4]["tokens"] - excess)
                excess -= (layers[4]["tokens"] - trimmed_tokens)
                if trimmed_tokens == 0:
                    layers[4]["content"] = ""
                else:
                    # Estimate truncation
                    char_limit = int(trimmed_tokens * 3.5)
                    layers[4]["content"] = layers[4]["content"][:char_limit] + "\n... (truncated due to token budget)"
                layers[4]["tokens"] = trimmed_tokens

            # If still excess, trim Task Context (priority 3)
            if excess > 0 and layers[3]["tokens"] > 0:
                trimmed_tokens = max(100, layers[3]["tokens"] - excess)
                excess -= (layers[3]["tokens"] - trimmed_tokens)
                char_limit = int(trimmed_tokens * 3.5)
                layers[3]["content"] = layers[3]["content"][:char_limit] + "\n... (truncated due to token budget)"
                layers[3]["tokens"] = trimmed_tokens

            # If still excess, trim Project Context (priority 2)
            if excess > 0 and layers[2]["tokens"] > 0:
                trimmed_tokens = max(100, layers[2]["tokens"] - excess)
                excess -= (layers[2]["tokens"] - trimmed_tokens)
                char_limit = int(trimmed_tokens * 3.5)
                layers[2]["content"] = layers[2]["content"][:char_limit] + "\n... (truncated due to token budget)"
                layers[2]["tokens"] = trimmed_tokens

        # Merge layers
        merged = []
        for l in layers:
            if l["content"]:
                merged.append(f"=== {l['name'].upper()} CONTEXT ===\n{l['content']}\n")

        return "\n".join(merged)

context_loader = ContextLoader()
