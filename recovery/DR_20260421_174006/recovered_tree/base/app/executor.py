import logging
import json
from typing import Dict, Any, List, Optional
import httpx
from .tool_executor import ToolExecutor
from .approval import ApprovalPolicy
from .reflection import ReflectionLayer
from .workspace_state import WorkspaceTracker

logger = logging.getLogger(__name__)

class Executor:
    """Translates plan steps into actions and executes tools."""
    
    def __init__(
        self, 
        ollama_url: str, 
        model: str, 
        http_client: httpx.AsyncClient,
        tool_executor: ToolExecutor,
        approval_policy: ApprovalPolicy,
        reflection_layer: ReflectionLayer
    ):
        self.ollama_url = ollama_url
        self.model = model
        self.http_client = http_client
        self.tool_executor = tool_executor
        self.approval_policy = approval_policy
        self.reflection_layer = reflection_layer

    async def execute_step(
        self, 
        step: str, 
        workspace: WorkspaceTracker
    ) -> Dict[str, Any]:
        """Process one step and decide whether to call a tool or just reason."""
        logger.info(f"Executing step: {step}")
        
        # Continuation logic: if step involves the focus_target, prioritize it
        if workspace.focus_target and workspace.focus_target not in step and not workspace.scope_locked:
            logger.info(f"Step '{step}' does not mention focus target '{workspace.focus_target}'.")
        
        # (Decision logic here: tool call vs text)
        # For reconstruction, we simulate a tool selection:
        if "list" in step.lower():
            # Construct a tool call
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "list_files",
                        "arguments": json.dumps({"path": "."})
                    }
                }]
            }
        
        return {
            "role": "assistant",
            "content": f"Verified step: {step}. Progressing to next target."
        }

    async def handle_tool_response(self, tool_call: Dict[str, Any], result: str) -> str:
        """Process the outcome of a tool call."""
        # Use reflection to judge if we succeeded
        success = self.reflection_layer.judge_progress("", result)
        if success:
            return f"Step complete. Result: {result[:200]}..."
        else:
            return self.reflection_layer.reflect_on_error(result, {})
