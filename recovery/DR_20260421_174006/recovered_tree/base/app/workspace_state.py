import logging
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class WorkspaceTracker(BaseModel):
    """
    Tracks grounded targets, focus reason, and task state across agent turns.
    Prevents continuation drift by anchoring the agent to a specific target.
    """
    focus_target: Optional[str] = None
    focus_reason: Optional[str] = None
    completed_evidence: List[str] = Field(default_factory=list)
    remaining_task: Optional[str] = None
    allowed_scope: List[str] = Field(default_factory=list)
    scope_locked: bool = False
    
    def set_continuation_state(
        self, 
        focus_target: str, 
        focus_reason: str, 
        remaining_task: str, 
        scope_locked: bool = True
    ):
        """Anchor the workspace to a specific target for the next turn."""
        self.focus_target = focus_target
        self.focus_reason = focus_reason
        self.remaining_task = remaining_task
        self.scope_locked = scope_locked
        logger.info(f"Workspace context anchored to: {focus_target} (reason: {focus_reason})")

    def clear(self):
        """Clear all anchors."""
        self.focus_target = None
        self.focus_reason = None
        self.remaining_task = None
        self.scope_locked = False
        self.completed_evidence = []
        self.allowed_scope = []

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()
