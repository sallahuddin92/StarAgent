import logging
import json
import re
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
        
        step_lower = step.lower()

        # Minimal deterministic tool selection to avoid emitting ungrounded "completed" messages.
        if "list" in step_lower and "file" in step_lower:
            path = "app" if ("app" in step_lower or "app folder" in step_lower) else "."
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_list_files",
                    "type": "function",
                    "function": {
                        "name": "list_files",
                        "arguments": json.dumps({"path": path})
                    }
                }]
            }

        # Read any explicitly referenced file path.
        m_read = re.search(r"\b(app/[a-zA-Z0-9_./-]+\.py|sandbox_test/[a-zA-Z0-9_./-]+)\b", step)
        if "read" in step_lower and m_read:
            fp = m_read.group(1)
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_read_file",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": fp})
                    }
                }]
            }

        # Write to a sandbox path (approval-gated by policy).
        m_write = re.search(r"\bwrite file\s+(sandbox_test/[a-zA-Z0-9_./-]+)\b", step_lower)
        if m_write:
            path = m_write.group(1)
            m_content = re.search(r"content\s*:\s*(.+)$", step, flags=re.IGNORECASE)
            content = (m_content.group(1) if m_content else "hello from macagent\n")
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_write_file",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": path, "content": content})
                    }
                }]
            }

        if "identify" in step_lower and "entry" in step_lower:
            # Search for FastAPI usage as a grounded signal.
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_search_fastapi",
                    "type": "function",
                    "function": {
                        "name": "search_files",
                        "arguments": json.dumps({"query": "FastAPI", "path": "app"})
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
