import asyncio
import json
import pytest
from unittest.mock import MagicMock
from app.multi_agent import BaseAgent, AgentRole, SubTask, TaskGraph
from app.model_profiles import GENERIC_SMALL_LOCAL, GENERIC_API_CAPABLE

@pytest.mark.anyio
async def test_base_agent_plan_steps_no_name_error():
    # Setup mocks
    executor = MagicMock()
    tool_executor = MagicMock()
    tool_executor.registry.get_tool_descriptions.return_value = "- test_tool: A test tool"
    
    llm_client = MagicMock()
    llm_client.provider = "ollama"
    llm_client.model_name = "gemma4:e2b"
    
    async def mock_text(*args, **kwargs):
        return '{"tool": "write_file", "args": {"path": "test.py", "content": "print(1)"}}'
    llm_client.text = mock_text
    
    trace = MagicMock()
    queue = asyncio.Queue()
    
    # Test with small model profile
    agent = BaseAgent(AgentRole.BACKEND, executor, tool_executor, llm_client, queue, trace)
    agent.profile = GENERIC_SMALL_LOCAL
    
    subtask = SubTask(
        id="test_st",
        role=AgentRole.BACKEND,
        description="test description",
        requirements={"project_root": "scratch/test"}
    )
    
    # This should not raise NameError: tool_descriptions
    steps = await agent._plan_steps(subtask)
    
    assert len(steps) > 0
    # Confirm it used the tool_descriptions in the prompt (implicitly by not crashing)
    
    # Test with capable model profile
    agent.profile = GENERIC_API_CAPABLE
    async def mock_text_native(*args, **kwargs):
        return 'write_file("test.py", "print(1)")'
    agent.llm_client.text = mock_text_native
    
    steps = await agent._plan_steps(subtask)
    assert len(steps) > 0

if __name__ == "__main__":
    # For manual run if needed
    import sys
    asyncio.run(test_base_agent_plan_steps_no_name_error())
