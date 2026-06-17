import pytest
import os
from app.eval_harness import EvalVerifier
from app.blueprint import generate_blueprint
from unittest.mock import MagicMock

def test_eval_truthfulness_fail_precedence():
    # Mock output that contains 'completed' in subtask logs but final failure
    output = """
[ORCHESTRATOR] subtask 1 completed
[ORCHESTRATOR] subtask 2 completed
[ORCHESTRATOR] [x_agent_status] failed
    """
    
    # We want to ensure our grep-based eval scripts (or logic) correctly identifies this as failure.
    # The bash script fix: grep -q "\[x_agent_status\] completed"
    import subprocess
    
    # Simulate the bash check
    def check_pass(text):
        if "[x_agent_status] completed" in text:
            return "PASS"
        return "FAIL"
        
    assert check_pass(output) == "FAIL"
    assert check_pass("[ORCHESTRATOR] [x_agent_status] completed") == "PASS"

@pytest.mark.anyio
async def test_simple_script_blueprint_no_pytest():
    llm_client = MagicMock()
    
    async def mock_text(*args, **kwargs):
        return """
{
  "project_root": "scratch/hello_script",
  "structure": ["scratch/hello_script/main.py"],
  "required_semantics": [],
  "required_commands": ["python3 main.py"],
  "required_output_keywords": ["hello"],
  "task_type": "script"
}
"""
    llm_client.text = mock_text
    blueprint = await generate_blueprint("Write a python script that prints hello", llm_client)
    
    assert "pytest" not in blueprint["required_commands"]
    assert "test_main.py" not in blueprint["structure"]
    assert "python3 main.py" in blueprint["required_commands"]

def test_simple_script_verifier_stdout():
    verifier = EvalVerifier()
    
    # Mock tool outputs containing script execution success
    tool_outputs = [
        "Running: python3 main.py [Success]\nOutput: hello world\n"
    ]
    
    result = verifier.verify_task(
        required_commands_ran=["python3 main.py"],
        required_output_keywords=["hello"],
        tool_outputs=tool_outputs
    )
    
    assert result.passed
    assert result.checks["output_contains:hello"] is True

def test_simple_script_no_semantic_grep_if_not_in_blueprint():
    # If the blueprint doesn't have required_semantics, none should be checked
    verifier = EvalVerifier()
    
    result = verifier.verify_task(
        required_semantics=None,
        tool_outputs=["Success"]
    )
    
    assert result.passed
    # No semantic checks should be in the result
    assert not any(k.startswith("semantic:") for k in result.checks)
