import logging
import asyncio
from typing import List, Dict, Any, Optional
from .planner import Planner
from .executor import Executor
from .models import MemoryState
from .workspace_state import WorkspaceTracker

logger = logging.getLogger(__name__)

class AgentLoop:
    """The central orchestrator that runs the plan-execute loop."""
    
    def __init__(
        self, 
        planner: Planner, 
        executor: Executor,
        max_iterations: int = 5
    ):
        self.planner = planner
        self.executor = executor
        self.max_iterations = max_iterations

    async def run(
        self, 
        user_input: str, 
        memory: MemoryState, 
        workspace: WorkspaceTracker
    ) -> Dict[str, Any]:
        """Run the multi-step loop until goal completion or limit reached."""
        logger.info(f"Starting Agent Loop for input: {user_input}")
        
        # 1. Planning phase
        plan = await self.planner.create_plan(user_input, memory, workspace)
        logger.info(f"Generated plan with {len(plan)} steps.")
        
        # 2. Iteration loop
        history = []
        for i in range(self.max_iterations):
            if not plan:
                logger.info("Plan finished early.")
                break
                
            current_step = plan.pop(0)
            logger.info(f"Iteration {i+1}: Executing {current_step}")
            
            # Execute step
            result = await self.executor.execute_step(current_step, workspace)
            
            # Handle tool calls
            tool_calls = result.get("tool_calls", [])
            if tool_calls:
                # Check for approval
                for tc in tool_calls:
                    if self.executor.approval_policy.is_approval_required(tc):
                        logger.info("Approval required. Pausing execution.")
                        return {
                            "status": "approval_required",
                            "tool_call": tc,
                            "plan": plan,
                            "history": history
                        }
                    
                    # Execute tool (simulated result for now)
                    tool_result = await self.executor.tool_executor.execute_tool_call(tc)
                    history.append(tool_result)
                    
                    # Process result
                    feedback = await self.executor.handle_tool_response(tc, tool_result["content"])
                    history.append({"role": "assistant", "content": feedback})
            else:
                # Regular assistant content
                content = result.get("content", "")
                history.append({"role": "assistant", "content": content})
                
        # 3. Final summary
        return {
            "status": "completed",
            "message": "Task finished or iteration limit reached.",
            "history": history
        }
