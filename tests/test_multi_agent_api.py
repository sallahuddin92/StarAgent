import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
import json
import os
import shutil
from pathlib import Path

from app.main import app
from app.database import DatabaseManager

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "temp_memory.db"
    manager = DatabaseManager(str(db_file))
    from app.main import workflow_engine
    with patch("app.main.db", manager), \
         patch.object(workflow_engine, "db", manager), \
         patch("app.database._db", manager):
        yield manager

def test_multi_agent_run_simple_task_fast_path(temp_db):
    client = TestClient(app)
    headers = {"Authorization": "Bearer local-dev-key"}
    payload = {
        "task": "Write a Python script that prints hello",
        "project_id": "test-fast-path",
        "conversation_id": "test-conv-123",
        "stream": False
    }

    response = client.post("/v1/multi-agent/run", json=payload, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert "Simple task executed and verified successfully" in data["message"]
    assert len(data["artifacts"]) > 0
    
    # Verify the fast path scratch file was created and contains "hello"
    artifact_path = Path(data["artifacts"][0])
    assert artifact_path.exists()
    content = artifact_path.read_text(encoding="utf-8")
    assert "hello" in content.lower()
    
    # Cleanup scratch directory
    if artifact_path.parent.exists():
        shutil.rmtree(artifact_path.parent)


def test_multi_agent_run_stream_mode(temp_db):
    client = TestClient(app)
    headers = {"Authorization": "Bearer local-dev-key"}
    payload = {
        "task": "Write a Python script that prints hello",
        "project_id": "test-fast-path-stream",
        "conversation_id": "test-conv-456",
        "stream": True
    }

    response = client.post("/v1/multi-agent/run", json=payload, headers=headers)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    
    # Read stream chunks
    chunks = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                continue
            chunks.append(json.loads(data_str))
            
    # Check that it ends with completed status in the choices/finish event
    assert len(chunks) > 0
    finish_chunk = next((c for c in chunks if "x_agent_status" in c), None)
    assert finish_chunk is not None
    assert finish_chunk["x_agent_status"] == "completed"


def test_multi_agent_run_internal_exception_non_stream(temp_db):
    client = TestClient(app)
    headers = {"Authorization": "Bearer local-dev-key"}
    payload = {
        "task": "Write a Python script that prints hello",
        "project_id": "test-error",
        "conversation_id": "test-conv-789",
        "stream": False
    }

    # Force an exception during db.create_task_run
    with patch.object(temp_db, "create_task_run", side_effect=ValueError("Forced DB Error")):
        response = client.post("/v1/multi-agent/run", json=payload, headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "Forced DB Error" in data["message"]


def test_multi_agent_run_internal_exception_stream(temp_db):
    client = TestClient(app)
    headers = {"Authorization": "Bearer local-dev-key"}
    payload = {
        "task": "Write a Python script that prints hello",
        "project_id": "test-error-stream",
        "conversation_id": "test-conv-012",
        "stream": True
    }

    # Force an exception during db.create_task_run
    with patch.object(temp_db, "create_task_run", side_effect=ValueError("Forced DB Error")):
        response = client.post("/v1/multi-agent/run", json=payload, headers=headers)
        assert response.status_code == 200
        
        chunks = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    continue
                chunks.append(json.loads(data_str))
                
        # Check that it has an event with status "failed" and error message
        assert len(chunks) > 0
        error_chunk = next((c for c in chunks if c.get("x_agent_status") == "failed"), None)
        assert error_chunk is not None
        assert "Forced DB Error" in error_chunk["choices"][0]["delta"]["content"]
