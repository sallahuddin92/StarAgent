import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from cli import macagent as cli_mod


class _WaitClient:
    def __init__(self, summary=None, artifacts=None, preview=None):
        self._summary = summary or {}
        self._artifacts = artifacts or {}
        self._preview = preview or {}

    def task_status(self, task_id):
        return {"task_id": task_id, "task": {"status": "completed"}}

    def task_summary(self, task_id):
        return self._summary

    def task_artifacts(self, task_id):
        return self._artifacts

    def task_artifact_preview(self, task_id, artifact_name):
        return self._preview


class CLITaskResultOutputTests(unittest.TestCase):
    def test_wait_print_primary_uses_primary_report(self):
        client = _WaitClient(summary={"primary_report": "AUDIT REPORT BODY"})
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_mod._wait_for_task(client, "task-1", print_primary=True, as_json=False)
        self.assertEqual(rc, 0)
        self.assertIn("AUDIT REPORT BODY", out.getvalue())

    def test_wait_print_primary_missing_report_shows_clear_error(self):
        client = _WaitClient(summary={}, artifacts={"primary_artifact": {"name": "audit_report.md", "exists": False}})
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_mod._wait_for_task(client, "task-2", print_primary=True, as_json=False)
        self.assertEqual(rc, 1)
        self.assertIn("Task completed but no primary report was stored.", out.getvalue())

    @patch("cli.macagent._ensure_server_running")
    @patch("cli.macagent.MacAgentClient")
    def test_task_logs_handles_string_logs(self, MockClient, _mock_ensure):
        inst = MockClient.return_value
        inst.task_logs.return_value = {"task_id": "task-3", "logs": "line one\nline two"}
        inst.close.return_value = None

        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_mod.main(["task", "logs", "task-3"])
        self.assertEqual(rc, 0)
        self.assertIn("line one", out.getvalue())

    @patch("cli.macagent._ensure_server_running")
    @patch("cli.macagent.MacAgentClient")
    def test_task_summary_shows_real_values_not_none(self, MockClient, _mock_ensure):
        inst = MockClient.return_value
        inst.task_summary.return_value = {
            "task_id": "task-4",
            "task": {
                "task_id": "task-4",
                "status": "completed",
                "final_verdict": "completed",
                "retry_count": 0,
                "final_summary": "Summary body",
            },
            "progress": {"counts": {"completed": 5, "total": 5}, "percent_complete": 100.0},
            "time": {"age_s": 2.0},
            "primary_artifact": {"name": "audit_report.md", "exists": True},
        }
        inst.close.return_value = None

        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_mod.main(["task", "summary", "task-4"])
        self.assertEqual(rc, 0)
        txt = out.getvalue()
        self.assertNotIn("None", txt)
        self.assertIn("task-4", txt)
        self.assertIn("completed", txt)


if __name__ == "__main__":
    unittest.main()

