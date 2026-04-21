import io
import json
import unittest

from mcp.transport_stdio import StdioTransport
from mcp.server import MacAgentMCPServer


class _FakeClient:
    def __init__(self):
        self.v1_base_url = "http://127.0.0.1:8095/v1"

    def close(self):
        pass


class TestMCPTransportHandshake(unittest.TestCase):
    def _frame(self, payload: dict) -> bytes:
        body = json.dumps(payload).encode("utf-8")
        return f"Content-Length: {len(body)}\r\nContent-Type: application/vscode-jsonrpc; charset=utf-8\r\n\r\n".encode(
            "utf-8"
        ) + body

    def test_transport_skips_blank_lines(self):
        stdin = io.BytesIO(b"\r\n\r\n" + self._frame({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}))
        stdout = io.BytesIO()
        t = StdioTransport(stdin=stdin, stdout=stdout)
        msg = t.read()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.payload["method"], "ping")

    def test_initialize_response_has_protocol_version(self):
        server = MacAgentMCPServer(_FakeClient())
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
        resp = server.handle(req)
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("capabilities", resp["result"])

    def test_notifications_do_not_respond(self):
        server = MacAgentMCPServer(_FakeClient())
        note = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        resp = server.handle(note)
        self.assertIsNone(resp)


if __name__ == "__main__":
    unittest.main()

