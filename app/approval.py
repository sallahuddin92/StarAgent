import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class ApprovalPolicy:
    """Manages safety gates for risky tool actions."""
    
    def __init__(self):
        import os
        self.auto_approve = os.getenv("AUTO_APPROVE", "false").lower() == "true"
        self.risky_tools = ["write_file", "delete_file", "run_command"]
        logger.info(f"ApprovalPolicy initialized. AUTO_APPROVE: {self.auto_approve}")



        
    def is_approval_required(self, tool_call: Dict[str, Any]) -> bool:
        """Check if a tool call requires human-in-the-loop approval."""
        if self.auto_approve:
            return False
            
        func_name = tool_call.get("function", {}).get("name")
        return func_name in self.risky_tools

    
    def format_approval_request(self, tool_call: Dict[str, Any]) -> str:
        """Format a clear request for the operator to approve."""
        func_name = tool_call.get("function", {}).get("name")
        args = tool_call.get("function", {}).get("arguments", "{}")
        return f"Requesting approval to execute risky tool: {func_name}\nArguments: {args}"
