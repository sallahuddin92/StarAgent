import pytest
from unittest.mock import MagicMock, AsyncMock
from app.executor import Executor

@pytest.fixture
def anyio_backend():
    return 'asyncio'

from app.llm_client import LLMClient
from app.blueprint import generate_blueprint, extract_explicit_path

def test_extract_explicit_path():
    assert extract_explicit_path("Create FastAPI app in scratch/eval_backend") == "scratch/eval_backend"
    assert extract_explicit_path("Build app at scratch/eval_calculator:") == "scratch/eval_calculator"
    assert extract_explicit_path("Create app under `scratch/my_app`") == "scratch/my_app"
    assert extract_explicit_path("Folder: \"scratch/test-folder\", do X") == "scratch/test-folder"
    assert extract_explicit_path("No explicit path here") is None
    assert extract_explicit_path("Unsafe path in ../secret") is None

@pytest.mark.anyio
async def test_generate_blueprint_issue_tracker():
    mock_llm = MagicMock(spec=LLMClient)
    mock_llm.text = AsyncMock(return_value='''
    {
      "project_root": "scratch/eval_issue_tracker",
      "structure": ["backend/main.py", "frontend/src/App.js"],
      "required_semantics": ["POST /projects", "GET /projects"],
      "required_commands": ["pytest", "npm run build"],
      "task_type": "fullstack"
    }
    ''')
    
    blueprint = await generate_blueprint("Create an issue tracker in scratch/eval_issue_tracker", mock_llm)
    
    assert blueprint["project_root"] == "scratch/eval_issue_tracker"
    assert "scratch/eval_issue_tracker/backend/main.py" in blueprint["structure"]
    assert "POST /projects" in blueprint["required_semantics"]
    assert "pytest" in blueprint["required_commands"]
    assert blueprint["task_type"] == "fullstack"

@pytest.mark.anyio
async def test_generate_blueprint_simple_script():
    mock_llm = MagicMock(spec=LLMClient)
    mock_llm.text = AsyncMock(return_value='''
    {
      "project_root": "scratch/hello_script",
      "structure": ["hello.py"],
      "required_semantics": ["prints hello"],
      "required_commands": ["python3 hello.py"],
      "task_type": "script"
    }
    ''')
    
    blueprint = await generate_blueprint("Write a script that prints hello", mock_llm)
    
    assert blueprint["project_root"] == "scratch/hello_script"
    assert "scratch/hello_script/hello.py" in blueprint["structure"]
    assert blueprint["task_type"] == "script"

@pytest.mark.anyio
async def test_generate_blueprint_fallback():
    mock_llm = MagicMock(spec=LLMClient)
    mock_llm.text = AsyncMock(side_effect=Exception("LLM Fail"))
    
    blueprint = await generate_blueprint("Some random task", mock_llm)
    
    assert "project_root" in blueprint
    assert "structure" in blueprint
    assert blueprint["task_type"] == "script"
