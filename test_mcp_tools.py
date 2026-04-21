import json
import unittest

from mcp.server import MacAgentMCPServer


class _FakeClient:
    def __init__(self):
        self.v1_base_url = "http://127.0.0.1:8095/v1"

    def ask(self, prompt, **kwargs):
        return type("R", (), {"message": f"ask:{prompt}", "agent_status": None, "raw": {"ok": True}})()

    def agent(self, prompt, **kwargs):
        return type("R", (), {"message": f"agent:{prompt}", "agent_status": "completed", "raw": {"x_agent_status": "completed"}})()

    def approve(self, **kwargs):
        return type("R", (), {"message": "approved", "agent_status": "completed", "raw": {}})()

    def reject(self, **kwargs):
        return type("R", (), {"message": "rejected", "agent_status": "completed", "raw": {}})()

    def continue_task(self, **kwargs):
        return type("R", (), {"message": "continued", "agent_status": "completed", "raw": {}})()

    def rollback(self, **kwargs):
        return type("R", (), {"message": "rollback", "agent_status": "completed", "raw": {}})()

    def health(self):
        return {"ok": True}

    def models(self):
        return {"object": "list", "data": [{"id": "gemma4:e2b", "object": "model", "owned_by": "macagent"}]}

    def smoke_test_compact(self):
        return {"ok": True, "results": []}

    def close(self):
        pass


class TestMCPTools(unittest.TestCase):
    def test_tools_list_contains_required(self):
        server = MacAgentMCPServer(_FakeClient())
        tools = {t["name"] for t in server.tools()}
        required = {
            "macagent_ask",
            "macagent_agent",
            "macagent_approve",
            "macagent_reject",
            "macagent_continue",
            "macagent_status",
            "macagent_rollback",
            "macagent_smoke_test",
        }
        self.assertTrue(required.issubset(tools))

    def test_tools_call_shape(self):
        server = MacAgentMCPServer(_FakeClient())
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "macagent_ask", "arguments": {"prompt": "hi"}}}
        resp = server.handle(req)
        self.assertEqual(resp["id"], 1)
        text = resp["result"]["content"][0]["text"]
        payload = json.loads(text)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["message"], "ask:hi")


if __name__ == "__main__":
    unittest.main()

