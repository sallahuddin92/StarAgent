import os
import json
import unittest
import asyncio
import tempfile
from pathlib import Path

import httpx

from app.database import DatabaseManager
from app.memory import MemoryStore
from app.planner import Planner
from app.executor import Executor
from app.tools import ToolRegistry
from app.tool_executor import ToolExecutor
from app.approval import ApprovalPolicy
from app.reflection import ReflectionLayer
from app.workspace_state import WorkspaceTracker
from app.task_engine import TaskEngine
from app.research_mode import ResearchPipeline


class _FakeLLM:
    async def text(self, messages, *, model=None, temperature=0.2):
        # Heuristic: if asked for JSON, return strict JSON.
        sys = (messages[0].get("content") or "").lower()
        if "return only valid json" in sys:
            return json.dumps(
                {
                    "summary": "chunk summary",
                    "key_points": ["kp1", "kp2"],
                    "open_questions": ["oq1"],
                }
            )
        # Otherwise return a concise markdown bullet list.
        return "- point 1\n- point 2\n"


class TaskEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)

    def _make_engine(self, tmp: Path, *, with_research: bool = False) -> TaskEngine:
        db = DatabaseManager(str(tmp / "memory.db"))
        store = MemoryStore(str(tmp / "memory"), use_sqlite=True, db_manager=db)
        http = httpx.AsyncClient()
        planner = Planner("http://127.0.0.1:11434", "gemma4:e2b", http)
        registry = ToolRegistry()
        executor = Executor(
            "http://127.0.0.1:11434",
            "gemma4:e2b",
            http,
            ToolExecutor(registry),
            ApprovalPolicy(),
            ReflectionLayer(),
        )
        workspace = WorkspaceTracker()
        research = ResearchPipeline(_FakeLLM()) if with_research else None
        return TaskEngine(db=db, store=store, planner=planner, executor=executor, workspace=workspace, approval_policy=ApprovalPolicy(), research=research)

    async def test_task_creation_and_completion(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            os.chdir(tmp)

            engine = self._make_engine(tmp)
            tr = engine.create_task(project_id="p1", conversation_id="c1", task_type="agent", user_goal="Inspect the app folder and identify the main API entry file.", max_steps=10)

            # Create a minimal repo-like structure for tool grounding.
            (tmp / "app").mkdir()
            (tmp / "app" / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")

            out = await engine.run(tr["task_id"], max_step_advances=5, max_duration_s=10.0)
            self.assertIn(out.task["status"], {"completed", "partial"})
            steps = out.steps
            self.assertGreaterEqual(len(steps), 2)

            # Should have evidence in tool outputs and/or summaries.
            joined = "\n".join([str(s.get("output_summary") or "") for s in steps])
            self.assertTrue("main.py" in joined or "FastAPI" in joined or out.task.get("final_summary"))

    async def test_retry_logic_and_failure(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            os.chdir(tmp)

            engine = self._make_engine(tmp)
            # Force the planner to emit explicit read steps by using "inspect" and multiple file references.
            (tmp / "app").mkdir()
            (tmp / "app" / "a.py").write_text("print('a')\n", encoding="utf-8")
            (tmp / "app" / "b.py").write_text("print('b')\n", encoding="utf-8")
            (tmp / "app" / "c.py").write_text("print('c')\n", encoding="utf-8")
            # Intentionally do NOT create app/does_not_exist.py
            goal = "Inspect these files: app/a.py app/b.py app/c.py app/does_not_exist.py"
            tr = engine.create_task(project_id="p1", conversation_id="c1", task_type="agent", user_goal=goal, max_steps=10, max_retries=1)

            out1 = await engine.run(tr["task_id"], max_step_advances=1, max_duration_s=5.0)
            self.assertIn(out1.task["status"], {"partial", "failed", "running"})
            # Keep continuing until it fails.
            for _ in range(10):
                out1 = await engine.run(tr["task_id"], max_step_advances=2, max_duration_s=5.0)
                if out1.task["status"] == "failed":
                    break
            self.assertEqual(out1.task["status"], "failed")

    async def test_research_mode_artifacts_created(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            os.chdir(tmp)

            # Create sample docs
            docs = tmp / "docs"
            docs.mkdir()
            (docs / "a.md").write_text("# A\nhello\n", encoding="utf-8")
            (docs / "b.md").write_text("# B\nworld\n", encoding="utf-8")

            engine = self._make_engine(tmp, with_research=True)
            tr = engine.create_task(
                project_id="p1",
                conversation_id="c1",
                task_type="research",
                user_goal="Research the docs folder",
                max_steps=25,
                artifacts_json={"root_path": str(docs), "question": "What is in these docs?", "mode": "summary"},
            )

            # Run until completion (bounded loops).
            status = None
            for _ in range(20):
                out = await engine.run(tr["task_id"], max_step_advances=5, max_duration_s=10.0)
                status = out.task["status"]
                if status == "completed":
                    break
            self.assertEqual(status, "completed")

            task_dir = tmp / ".runtime" / "tasks" / tr["task_id"]
            self.assertTrue((task_dir / "file_index.json").exists())
            self.assertTrue((task_dir / "chunk_summaries.json").exists())
            self.assertTrue((task_dir / "file_summaries.md").exists())
            self.assertTrue((task_dir / "research_brief.md").exists())
            self.assertTrue((task_dir / "final_report.md").exists())


if __name__ == "__main__":
    unittest.main()
