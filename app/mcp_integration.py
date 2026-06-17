import logging
from typing import Dict, Any, List, Optional
from .tool_runtime import verify_tool_permission

logger = logging.getLogger(__name__)

class MCPPermissionManager:
    """
    Manages stage-based permissions for MCP servers and tools.
    """
    def __init__(self):
        self.stage_mcp_mappings = {
            "inspect": {
                "allowed_servers": ["filesystem", "git", "search", "docs"],
                "allowed_tools": ["read_file", "list_files", "search_files", "grep", "file_tree"]
            },
            "analyze": {
                "allowed_servers": ["filesystem", "git", "search"],
                "allowed_tools": ["read_file", "list_files", "search_files", "grep"]
            },
            "plan": {
                "allowed_servers": ["docs"],
                "allowed_tools": ["read_file", "grep"]
            },
            "execute": {
                "allowed_servers": ["filesystem", "git", "docker"],
                "allowed_tools": ["write_file", "patch", "git", "shell", "docker", "run_command"]
            },
            "verify": {
                "allowed_servers": ["pytest", "coverage", "lint"],
                "allowed_tools": ["pytest", "npm", "coverage", "lint", "run_command"]
            },
            "finalize": {
                "allowed_servers": [],
                "allowed_tools": []
            }
        }

    def check_permission(
        self, 
        stage_name: str, 
        tool_name: str, 
        mcp_server: Optional[str] = None
    ) -> bool:
        """
        Validates if the MCP client or agent tool is permitted during the current stage.
        """
        stage = str(stage_name).lower()
        if stage not in self.stage_mcp_mappings:
            return True # If unknown stage, default to open/permissive or fallback

        mapping = self.stage_mcp_mappings[stage]
        
        # Enforce server-level validation if provided
        if mcp_server:
            srv = str(mcp_server).lower()
            if srv not in mapping["allowed_servers"]:
                logger.warning(f"MCP server '{mcp_server}' is not allowed in stage '{stage}'")
                return False

        # Enforce tool-level validation
        t_name = str(tool_name).strip()
        if mapping["allowed_tools"] and t_name not in mapping["allowed_tools"]:
            logger.warning(f"MCP tool '{tool_name}' is not allowed in stage '{stage}'")
            return False

        return True

mcp_permission_manager = MCPPermissionManager()
