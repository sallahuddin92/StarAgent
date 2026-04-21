import logging
from typing import Dict, Any, List
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

class ToolExecutor:
    """Executes tool calls and returns results."""
    
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        
    async def execute_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single tool call from the LLM."""
        call_id = tool_call.get("id")
        func_info = tool_call.get("function", {})
        func_name = func_info.get("name")
        import json
        try:
            args = json.loads(func_info.get("arguments", "{}"))
        except:
            args = {}
            
        logger.info(f"Executing tool: {func_name} with args: {args}")
        
        if func_name not in self.registry.tools:
            result = f"Error: Tool '{func_name}' not found."
        else:
            handler = self.registry.tools[func_name]["handler"]
            try:
                # Most tools are synchronous for now; wrap in thread if needed
                # But here we just call them
                result = handler(**args)
            except Exception as e:
                logger.error(f"Error executing tool {func_name}: {e}")
                result = f"Error: {str(e)}"
                
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": str(result)
        }
