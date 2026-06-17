import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import sys
import types

from fastapi.testclient import TestClient

# app.main imports SearchBackend -> duckduckgo_search at import-time.
if "duckduckgo_search" not in sys.modules:
    fake_mod = types.ModuleType("duckduckgo_search")
    fake_mod.DDGS = object
    sys.modules["duckduckgo_search"] = fake_mod
if "wikipedia" not in sys.modules:
    fake_wiki = types.ModuleType("wikipedia")
    fake_wiki.search = lambda *args, **kwargs: []
    fake_wiki.page = lambda *args, **kwargs: None
    sys.modules["wikipedia"] = fake_wiki
if "trafilatura" not in sys.modules:
    fake_traf = types.ModuleType("trafilatura")
    fake_traf.extract = lambda *args, **kwargs: ""
    sys.modules["trafilatura"] = fake_traf
if "readability" not in sys.modules:
    fake_read = types.ModuleType("readability")

    class _Doc:
        def __init__(self, html):
            self._html = html

        def short_title(self):
            return "Untitled"

        def summary(self):
            return self._html

    fake_read.Document = _Doc
    sys.modules["readability"] = fake_read
if "chromadb" not in sys.modules:
    fake_chroma = types.ModuleType("chromadb")

    class _Collection:
        def add(self, **kwargs):
            return None

        def query(self, **kwargs):
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def get_or_create_collection(self, **kwargs):
            return _Collection()

    fake_chroma.PersistentClient = _Client
    sys.modules["chromadb"] = fake_chroma
if "chromadb.config" not in sys.modules:
    fake_chroma_cfg = types.ModuleType("chromadb.config")
    fake_chroma_cfg.Settings = object
    sys.modules["chromadb.config"] = fake_chroma_cfg

from app import main as app_main


class TaskResultRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_main.app)
        self.auth = {"Authorization": f"Bearer {app_main.PROXY_API_KEY}"}

    def test_repo_audit_stores_primary_report_and_inspect_works(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app").mkdir(parents=True, exist_ok=True)
            (root / "app" / "main.py").write_text(
                "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health():\n    return {'ok': True}\n",
                encoding="utf-8",
            )

            mock_risk_notes = (
                "## Risks\n- Missing tests around task retrieval.\n\n"
                "## Unknowns\n- Production traffic volume.\n\n"
                "## Recommended Next Checks\n- Add endpoint tests.\n"
            )

            with patch.object(app_main.task_engine.repo_audit.llm, "text", new=AsyncMock(return_value=mock_risk_notes)):
                run_res = self.client.post(
                    "/v1/repo_audit/run",
                    headers=self.auth,
                    json={
                        "project_id": "task-result-test",
                        "conversation_id": "conv-a",
                        "path": str(root),
                        "question": "Read-only audit",
                        "max_steps": 10,
                        "run_now": True,
                    },
                )

            self.assertEqual(run_res.status_code, 200, run_res.text)
            payload = run_res.json()
            task_id = payload.get("task_id") or (payload.get("task") or {}).get("task_id")
            self.assertTrue(task_id)
            self.assertIsInstance(payload.get("primary_report"), str)
            self.assertTrue(payload.get("primary_report"))

            inspect_res = self.client.get(f"/v1/tasks/{task_id}/inspect", headers=self.auth)
            self.assertEqual(inspect_res.status_code, 200, inspect_res.text)
            inspect = inspect_res.json()
            self.assertEqual(inspect.get("task_id"), task_id)
            self.assertIn(inspect.get("task", {}).get("status"), {"completed", "partial", "running"})
            self.assertIn("primary_report", inspect)
            self.assertIn("summary", inspect)

            summary_res = self.client.get(f"/v1/tasks/{task_id}/summary", headers=self.auth)
            self.assertEqual(summary_res.status_code, 200, summary_res.text)
            summary = summary_res.json()
            self.assertEqual(summary.get("task_id"), task_id)
            self.assertTrue(summary.get("task", {}).get("task_id"))
            self.assertTrue(summary.get("task", {}).get("status"))
            self.assertIn("primary_report", summary)

            logs_res = self.client.get(f"/v1/tasks/{task_id}/logs", headers=self.auth)
            self.assertEqual(logs_res.status_code, 200, logs_res.text)
            logs = logs_res.json()
            self.assertIsInstance(logs.get("logs"), list)

            tr = app_main.db.get_task_run(task_id)
            self.assertIsNotNone(tr)
            result = (tr.get("artifacts_json") or {}).get("task_result")
            self.assertIsInstance(result, dict)
            for key in ("task_id", "status", "primary_report", "summary", "logs", "events", "artifacts"):
                self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main()
