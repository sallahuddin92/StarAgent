import json
import unittest

import httpx

from client.macagent_client import MacAgentClient, MacAgentConfig


class TestMacAgentClientLayer(unittest.TestCase):
    def test_models_parsing(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/v1/models"):
                return httpx.Response(
                    200,
                    json={
                        "object": "list",
                        "data": [{"id": "gemma4:e2b", "object": "model", "owned_by": "macagent"}],
                    },
                )
            if request.url.path.endswith("/health"):
                return httpx.Response(200, json={"ok": True})
            if request.url.path.endswith("/v1/chat/completions"):
                body = json.loads(request.content.decode("utf-8"))
                # Ensure auth header is present.
                assert request.headers.get("authorization", "").startswith("Bearer ")
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": f"echo:{body['messages'][0]['content']}"}}],
                        "x_agent_status": None,
                    },
                )
            return httpx.Response(404, json={"error": "not found"})

        transport = httpx.MockTransport(handler)
        http = httpx.Client(transport=transport)
        cfg = MacAgentConfig(base_url="http://127.0.0.1:8095/v1", api_key="k", default_model="gemma4:e2b")
        client = MacAgentClient(cfg, http=http)
        self.assertTrue(client.health()["ok"])
        models = client.models()
        self.assertEqual(models["data"][0]["id"], "gemma4:e2b")
        out = client.ask("hi").message
        self.assertEqual(out, "echo:hi")


if __name__ == "__main__":
    unittest.main()

