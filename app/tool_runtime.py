import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class ToolPermissionError(Exception):
    """Raised when a tool is called in a stage where it is not permitted."""
    pass

def verify_tool_permission(
    stage_config: Dict[str, Any], 
    tool_name: str,
    mcp_server: Optional[str] = None,
    registry: Optional[Any] = None
) -> bool:
    """
    Check if a tool (local or MCP) is allowed in the current stage.
    
    Rules:
    - Block write/destructive actions during inspection/planning stages.
    - Check tool-specific stage allowlist if defined.
    - If allowed_tools is defined, the tool must be listed (or match wildcard).
    - If blocked_tools is defined, the tool must NOT be listed.
    - If mcp_server is provided, checks if that server is allowed.
    """
    stage_name = stage_config.get("name", "").lower()
    allowed = stage_config.get("allowed_tools")
    blocked = stage_config.get("blocked_tools") or []

    # Clean inputs
    t_name = str(tool_name).strip()

    # 1. Block write/destructive tools in inspection/planning stages (inspect, analyze, plan)
    is_inspect_or_plan_stage = stage_name in {"inspect", "analyze", "plan"}
    tool_rw = "read"

    # Look up in the passed registry first
    if registry and hasattr(registry, "tools") and t_name in registry.tools:
        tool_entry = registry.tools[t_name]
        metadata = tool_entry.get("metadata", {})
        tool_rw = metadata.get("read_write_destructive", "read")
        
        # Check tool-specific stage allowlist
        stage_allowlist = metadata.get("stage_allowlist")
        if stage_allowlist is not None:
            check_stage = stage_name[7:] if stage_name.startswith("repair_") else stage_name
            if check_stage not in stage_allowlist and stage_name not in stage_allowlist:
                allowed_list = stage_config.get("allowed_tools") or []
                if t_name not in allowed_list:
                    logger.warning(f"Tool '{t_name}' has stage allowlist {stage_allowlist} which does not include '{stage_name}'.")
                    return False
    else:
        # Fallback to local import of BUILTIN_TOOL_METADATA to check
        try:
            from .tools import BUILTIN_TOOL_METADATA
            meta = BUILTIN_TOOL_METADATA.get(t_name, {})
            tool_rw = meta.get("read_write_destructive", "read")
            stage_allowlist = meta.get("stage_allowlist")
            if stage_allowlist is not None:
                check_stage = stage_name[7:] if stage_name.startswith("repair_") else stage_name
                if check_stage not in stage_allowlist and stage_name not in stage_allowlist:
                    allowed_list = stage_config.get("allowed_tools") or []
                    if t_name not in allowed_list:
                        logger.warning(f"Tool '{t_name}' has stage allowlist {stage_allowlist} which does not include '{stage_name}'.")
                        return False
        except Exception:
            # Fallback heuristics
            if t_name in {"write_file", "patch", "git", "shell", "run_command", "install_dependency", "create_directory"}:
                tool_rw = "write"
                if t_name in {"run_command", "shell"}:
                    tool_rw = "destructive"

    if is_inspect_or_plan_stage and tool_rw in {"write", "destructive"}:
        logger.warning(f"Tool '{t_name}' ({tool_rw}) is blocked during '{stage_name}' stage.")
        return False
    
    # 2. Check blocked list first
    if t_name in blocked:
        logger.warning(f"Tool '{t_name}' is explicitly blocked in this stage.")
        return False
        
    if mcp_server and mcp_server in blocked:
        logger.warning(f"MCP Server '{mcp_server}' is explicitly blocked in this stage.")
        return False

    # 3. Check allowed list
    if allowed is not None:
        # Wildcard allows everything unless blocked
        if "*" in allowed:
            return True
            
        # Check explicit tool name
        if t_name in allowed:
            return True
            
        # Check MCP server wildcard (e.g., "mcp:Filesystem:*")
        if mcp_server:
            mcp_pattern = f"mcp:{mcp_server}:*"
            mcp_tool_pattern = f"mcp:{mcp_server}:{t_name}"
            if mcp_pattern in allowed or mcp_tool_pattern in allowed or mcp_server in allowed:
                return True
                
        # If not explicitly allowed, reject
        logger.warning(f"Tool '{t_name}' (server={mcp_server}) is not in the allowed list of this stage: {allowed}")
        return False

    # If no allowed list is specified, default to allowing everything (not blocked)
    return True
