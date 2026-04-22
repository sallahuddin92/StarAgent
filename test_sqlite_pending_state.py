import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import DatabaseManager
from app.memory import MemoryStore, slugify


class TestSQLitePendingState(unittest.TestCase):
    def test_db_roundtrip_pending_fields(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "memory.db")
            db = DatabaseManager(db_path)

            conv = "c1"
            proj = "p1"
            pending = {
                "pending_approval": {"tool_call": {"name": "write_file"}},
                "pending_plan": ["step1", "step2"],
                "pending_history": [{"role": "tool", "content": "x"}],
                "pending_goal": "do thing",
            }
            db.save_memory_state(conv, proj, {**pending, "project_summary": ["a"], "turn_count": 1})
            out = db.get_memory_state(conv, proj)
            self.assertEqual(out["pending_goal"], "do thing")
            self.assertEqual(out["pending_plan"], ["step1", "step2"])
            self.assertEqual(out["pending_history"][0]["role"], "tool")
            self.assertEqual(out["pending_approval"]["tool_call"]["name"], "write_file")

    def test_ensure_columns_migrates_old_schema(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "memory.db")

            # Create legacy conversations table without pending_* columns.
            con = sqlite3.connect(db_path)
            con.execute(
                """
                CREATE TABLE conversations (
                    id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    user TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    project_summary TEXT,
                    decisions TEXT,
                    constraints TEXT,
                    issues TEXT,
                    style_preferences TEXT,
                    turn_count INTEGER,
                    last_compaction TEXT,
                    PRIMARY KEY (id, project_id)
                )
                """
            )
            con.commit()
            con.close()

            # Instantiating DatabaseManager should ALTER TABLE to add pending_* columns.
            db = DatabaseManager(db_path)
            out = db.get_memory_state("c1", "p1")
            self.assertIn("pending_approval", out)

    def test_memory_store_imports_legacy_json_pending_into_sqlite(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "memory.db")
            mem_dir = str(Path(td) / "memory")
            os.makedirs(mem_dir, exist_ok=True)

            db = DatabaseManager(db_path)
            store = MemoryStore(mem_dir, use_sqlite=True, db_manager=db)

            conv = "conv-1"
            proj = "proj-1"

            legacy = {
                "conversation_id": conv,
                "project_id": proj,
                "project_summary": [],
                "decisions": [],
                "constraints": [],
                "issues": [],
                "style_preferences": [],
                "archive_turns": [],
                "turn_count": 0,
                "pending_goal": "legacy goal",
                "pending_plan": ["legacy step"],
                "pending_history": [{"x": 1}],
                "pending_approval": {"tool_call": {"name": "write_file"}},
            }
            legacy_path = Path(mem_dir) / f"{slugify(proj)}-{slugify(conv)}.json"
            legacy_path.write_text(json.dumps(legacy), encoding="utf-8")

            state = store.load(conv, proj)
            self.assertEqual(state.pending_goal, "legacy goal")

            # Delete legacy JSON; state should still load from SQLite.
            legacy_path.unlink()
            state2 = store.load(conv, proj)
            self.assertEqual(state2.pending_plan, ["legacy step"])
            self.assertEqual(state2.pending_approval["tool_call"]["name"], "write_file")


if __name__ == "__main__":
    unittest.main()

