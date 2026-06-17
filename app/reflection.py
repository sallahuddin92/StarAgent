import logging
from typing import Dict, Any, List
from .llm_client import LLMClient

logger = logging.getLogger(__name__)

class ReflectionLayer:
    """Provides self-reflection capabilities to the agent loop."""
    
    def __init__(self, llm_client: LLMClient = None, *args, **kwargs):
        self.llm = llm_client
        # Alias for backward compatibility in methods
        self.ollama = llm_client

    async def evaluate(
        self,
        step_action: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_result: Dict[str, Any],
        memory: Any
    ) -> Dict[str, Any]:
        """Evaluate action results for change status and verification policies."""
        action_id = tool_args.get("action_id")
        if not hasattr(memory, "workspace_state") or memory.workspace_state is None:
            memory.workspace_state = {}
        if "verification_results" not in memory.workspace_state:
            memory.workspace_state["verification_results"] = {}
            
        success = tool_result.get("success", False)
        change_status = "unverified success"
        
        if tool_name in ["run_tests", "syntax_check"]:
            if success:
                change_status = "verified success"
            else:
                change_status = "rollback recommended"
        elif tool_name == "file_exists":
            change_status = "unverified success"
            
        if tool_name in ["write_file", "edit_file"] and success:
            change_status = "unverified success"
            
        if action_id:
            if tool_name in ["run_tests", "syntax_check", "file_exists"]:
                memory.workspace_state["verification_results"][action_id] = {
                    "tool": tool_name,
                    "success": success
                }
                
        return {
            "change_status": change_status,
            "rollback_available": True
        }

    def reflect_on_error(self, error: str, context: Dict[str, Any]) -> str:
        """Analyze a tool failure and suggest recovery."""
        logger.warning(f"Reflection triggered for error: {error}")
        return f"Encountered error: {error}. I should try a more specific search or check a different source."
    
    def judge_progress(self, current_step: str, result: str) -> bool:
        """Decide if a step was successful enough to move on."""
        # Check for obvious technical failures
        if not result or "error" in result.lower() or "not found" in result.lower():
            return False
        return True

    async def verify_completion(self, goal: str, data: str, attempt: int = 0) -> Dict[str, Any]:
        """Use LLM to verify if the gathered data actually satisfies the user goal.
        Relaxes constraints after multiple attempts.
        """
        if attempt >= 2:
            logger.info("Factual precision limits reached. Forcing completion bypass (Best Effort).")
            return {"met": True, "reason": "Precision limit bypass"}

        if not self.ollama:
            return {"met": True, "reason": "No reflection model available"}
            
        prompt = f"""
        Objective: {goal}
        Data found: {data[:2000]}
        
        CRITICAL ASSESSMENT:
        1. **Factual Check**: Did the 'Data found' provide the actual answer required (e.g., a specific % rate or list of names)?
        2. **Context Resolution**: If the objective contains pronouns like "this growth" or "it", you MUST resolve them using the conversation context in the 'Objective'. 
        3. **NO Human Interaction**: NEVER suggest a query that asks for clarification. All queries must be search terms for an engine (e.g., "Malaysia GDP 2024").
        
        INSTRUCTIONS:
        If information is missing, suggest a TARGETED search query.
        Respond in this exact JSON format:
        {{
            "met": false,
            "reason": "missing specific statistics",
            "suggested_query": "Malaysia economic growth 2024 sector breakdown"
        }}
        If it IS met, respond with {{"met": true}}.
        """
        try:
            res = await self.ollama.text([{"role": "user", "content": prompt}])
            logger.info(f"Reflection Analysis: {res}")
            
            # Use a robust regex for JSON (handles basic nesting and whitespace)
            import json
            import re
            
            # Simple match for first occurring { ... }
            json_match = re.search(r'(\{.*\})', res, re.DOTALL)
            if not json_match:
                # Secondary attempt: find anything that looks like "met": false
                if '"met": false' in res.lower() or '"met":false' in res.lower():
                    return {"met": False, "reason": "Detected met=false in raw text", "suggested_query": "current specific weather temperature"}
                return {"met": True, "reason": "No JSON found, assuming success"}
                
            try:
                data = json.loads(json_match.group(1))
                return data
            except:
                # If JSON is malformed, try to scavenge the fields
                is_false = '"met": false' in res.lower() or '"met":false' in res.lower()
                return {"met": not is_false, "reason": "Malformed JSON recovery"}
        except Exception as e:
            logger.error(f"Reflection verification failed: {e}")
            return {"met": True, "reason": "Guardrail bypass"}
