from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: Dict[str, Any]

class ToolMessage(BaseModel):
    role: str = "tool"
    tool_call_id: str
    content: str
