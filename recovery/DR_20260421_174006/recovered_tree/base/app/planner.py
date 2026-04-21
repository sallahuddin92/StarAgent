import logging
import json
from typing import List, Dict, Any, Optional
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
        
        # Heuristic for demo purposes when LLM call is simulated:
        if "inspect" in user_input.lower():
            return [
                "List files in the app directory",
                "Read app/main.py to identify the entrypoint",
                "Summarize the findings"
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
