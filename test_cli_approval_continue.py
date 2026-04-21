import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from cli import macagent as cli_mod


class _FakeRes:
    def __init__(self, message, agent_status=None):
        self.message = message
        self.agent_status = agent_status
        self.raw = {}

    def to_dict(self):
        return {"message": self.message, "agent_status": self.agent_status, "raw": self.raw}


class TestCLIApprovalContinue(unittest.TestCase):
    @patch("cli.macagent.MacAgentClient")
    def test_cli_approve_continue(self, MockClient):
        inst = MockClient.return_value
        inst.approve.return_value = _FakeRes("Wrote `sandbox_test/x.txt`.", agent_status="completed")
        inst.continue_task.return_value = _FakeRes("continued", agent_status="completed")
        inst.close.return_value = None

        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_mod.main(["--project", "p", "--conversation", "c", "approve"])
        self.assertEqual(rc, 0)
        self.assertIn("Wrote", out.getvalue())

        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_mod.main(["--project", "p", "--conversation", "c", "continue"])
        self.assertEqual(rc, 0)
        self.assertIn("continued", out.getvalue())


if __name__ == "__main__":
    unittest.main()

