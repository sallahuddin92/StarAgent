import io
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from cli import macagent as cli_mod


class _FakeRes:
    def __init__(self, message, agent_status=None):
        self.message = message
        self.agent_status = agent_status
        self.raw = {"x_agent_status": agent_status}

    def to_dict(self):
        return {"message": self.message, "agent_status": self.agent_status, "raw": self.raw}


class TestCLIAgent(unittest.TestCase):
    @patch("cli.macagent.MacAgentClient")
    def test_cli_agent_forced(self, MockClient):
        inst = MockClient.return_value
        inst.agent.return_value = _FakeRes("Main API entry file: `app/main.py`", agent_status="completed")
        inst.close.return_value = None

        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_mod.main(["--project", "p", "--conversation", "c", "agent", "inspect"])
        self.assertEqual(rc, 0)
        self.assertIn("app/main.py", out.getvalue())
        self.assertIn("x_agent_status", err.getvalue())


if __name__ == "__main__":
    unittest.main()

