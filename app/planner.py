import logging
import json
from typing import List, Dict, Any, Optional
import re
import httpx
from .models import MemoryState
from .workspace_state import WorkspaceTracker

logger = logging.getLogger(__name__)

class Planner:
    """Creates and revises multi-step execution plans."""
    
    def __init__(self, ollama_url: str, model: str, http_client: httpx.AsyncClient):
        self.ollama_url = ollama_url
        self.model = model
        self.http_client = http_client

    async def create_plan(
        self, 
        user_input: str, 
        memory: MemoryState, 
        workspace: WorkspaceTracker
    ) -> List[str]:
        """Generate a structured plan based on input and memory."""
        logger.info(f"Generating plan for: {user_input}")
        
        # In a real scenario, this calls the LLM with a system prompt.
        # For reconstruction, we simulate the logic:
        
        system_prompt = "You are a senior AI planner. Create a step-by-step plan."
        if workspace.focus_target:
            system_prompt += f"\nCONTINUATION MODE: Focus exclusively on {workspace.focus_target}."
        
        # (Call Ollama here)
        # For now, we return a basic 3-step placeholder or a heuristic plan
        # but the actual V5 planner parses a structured list.
        
        # Heuristic planning (recovered runtime): keep deterministic and tool-friendly.
        if "inspect" in user_input.lower():
            # If user asks to read many specific files, expand steps so the agent can be resumed (partial completion).
            files = re.findall(r"\bapp/[a-zA-Z0-9_./-]+\.py\b", user_input)
            files = list(dict.fromkeys(files))  # stable de-dupe
            if len(files) >= 4:
                steps: List[str] = ["List files in the app directory"]
                for fp in files:
                    steps.append(f"Read {fp} to gather grounded evidence")
                steps.append("Summarize the findings")
                return steps
            return [
                "List files in the app directory",
                "Read app/main.py to identify the entrypoint",
                "Summarize the findings"
            ]

        # Heuristic for approval-gated writes in a safe sandbox folder.
        lower = (user_input or "").lower()
        if any(w in lower for w in ("create file", "write file", "create a file", "modify file", "update file", "edit file")):
            # Expect a path like sandbox_test/<name> and optional "content:" marker.
            m_path = re.search(r"(sandbox_test/[a-zA-Z0-9_./-]+)", user_input)
            path = m_path.group(1) if m_path else "sandbox_test/agent_write.txt"
            m_content = re.search(r"content\s*:\s*(.+)$", user_input, flags=re.IGNORECASE)
            content = (m_content.group(1).strip() if m_content else "hello from macagent\n")
            return [
                f"Write file {path} with content: {content}",
                f"Read file {path} to confirm it was written",
                "Summarize the result",
            ]
        
        return ["Analyze the request", "Perform the task", "Confirm completion"]

    def parse_plan(self, llm_output: str) -> List[str]:
        """Extract steps from LLM text."""
        # Logic to parse numbered lists or JSON from LLM
        steps = []
        for line in llm_output.split("\n"):
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith("-")):
                steps.append(line.lstrip("0123456789.- ").strip())
        return steps or ["No clear steps identified"]
