import io
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from cli import macagent as cli_mod


class _FakeRes:
    def __init__(self, message, agent_status=None):
        self.message = message
        self.agent_status = agent_status
        self.raw = {}

    def to_dict(self):
        return {"message": self.message, "agent_status": self.agent_status, "raw": self.raw}


class TestCLIBasic(unittest.TestCase):
    @patch("cli.macagent.MacAgentClient")
    def test_cli_ask_basic(self, MockClient):
        inst = MockClient.return_value
        inst.ask.return_value = _FakeRes("OK")
        inst.close.return_value = None

        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_mod.main(["--project", "p", "--conversation", "c", "ask", "hi"])
        self.assertEqual(rc, 0)
        self.assertIn("OK", out.getvalue())
        inst.ask.assert_called_once()


if __name__ == "__main__":
    unittest.main()

