from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


@dataclass
class MacAgentConfig:
    base_url: str
    api_key: str
    default_model: str = "gemma4:e2b"
    default_project_id: str = "default"
    default_conversation_id: str = "default"
    timeout_s: float = 180.0

    @staticmethod
    def from_env() -> "MacAgentConfig":
        base = os.getenv("MACAGENT_BASE_URL") or os.getenv("MACAGENT_API_BASE_URL") or "http://127.0.0.1:8095/v1"
        api_key = os.getenv("MACAGENT_API_KEY") or os.getenv("PROXY_API_KEY") or "local-dev-key"
        model = os.getenv("MACAGENT_DEFAULT_MODEL") or os.getenv("DEFAULT_MODEL") or "gemma4:e2b"
        proj = os.getenv("MACAGENT_DEFAULT_PROJECT") or "default"
        conv = os.getenv("MACAGENT_DEFAULT_CONVERSATION") or "default"
        return MacAgentConfig(base_url=base, api_key=api_key, default_model=model, default_project_id=proj, default_conversation_id=conv)


@dataclass
class MacAgentResult:
    message: str
    agent_status: Optional[str]
    raw: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"message": self.message, "agent_status": self.agent_status, "raw": self.raw}


def _normalize_v1_base_url(base_url: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    if base_url.endswith("/v1"):
        return base_url
    # Accept either http://host:port or http://host:port/v1
    return base_url + "/v1"


def _root_from_v1(v1_url: str) -> str:
    v1_url = _normalize_v1_base_url(v1_url)
    return v1_url[: -len("/v1")]


class MacAgentClient:
    """
    Thin adapter over the existing FastAPI runtime.
    This should not duplicate agent logic; it only calls the API and normalizes responses.
    """

    def __init__(self, config: Optional[MacAgentConfig] = None, *, http: Optional[httpx.Client] = None):
        self.config = config or MacAgentConfig.from_env()
        self.v1_base_url = _normalize_v1_base_url(self.config.base_url)
        self.root_base_url = _root_from_v1(self.v1_base_url)
        self._http = http or httpx.Client(timeout=self.config.timeout_s)

    def close(self) -> None:
        self._http.close()

    def health(self) -> Dict[str, Any]:
        r = self._http.get(f"{self.root_base_url}/health")
        r.raise_for_status()
        return r.json()

    def models(self) -> Dict[str, Any]:
        r = self._http.get(f"{self.v1_base_url}/models")
        r.raise_for_status()
        return r.json()

    def chat(
        self,
        prompt: str,
        *,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        force_route: Optional[str] = None,
        stream: bool = False,
        debug_raw: bool = False,
    ) -> MacAgentResult:
        project_id = project_id or self.config.default_project_id
        conversation_id = conversation_id or self.config.default_conversation_id
        model = model or self.config.default_model

        metadata: Dict[str, Any] = {}
        if force_route:
            metadata["force_route"] = force_route

        payload = {
            "model": model,
            "stream": stream,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "metadata": metadata,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        msg = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        agent_status = data.get("x_agent_status")
        if not debug_raw:
            # Remove very large raw fields from common outputs.
            pass
        return MacAgentResult(message=msg, agent_status=agent_status, raw=data)

    def ask(self, prompt: str, **kwargs: Any) -> MacAgentResult:
        # Force fast path so accidental keyword matches do not trigger agent/tool flow.
        return self.chat(prompt, force_route="fast", **kwargs)

    def agent(self, prompt: str, **kwargs: Any) -> MacAgentResult:
        return self.chat(prompt, force_route="agent", **kwargs)

    def approve(self, *, project_id: Optional[str] = None, conversation_id: Optional[str] = None, model: Optional[str] = None) -> MacAgentResult:
        return self.chat("yes", project_id=project_id, conversation_id=conversation_id, model=model)

    def reject(self, *, project_id: Optional[str] = None, conversation_id: Optional[str] = None, model: Optional[str] = None) -> MacAgentResult:
        return self.chat("no", project_id=project_id, conversation_id=conversation_id, model=model)

    def continue_task(self, *, project_id: Optional[str] = None, conversation_id: Optional[str] = None, model: Optional[str] = None) -> MacAgentResult:
        return self.chat("continue", project_id=project_id, conversation_id=conversation_id, model=model)

    def rollback(self, *, project_id: Optional[str] = None, conversation_id: Optional[str] = None, model: Optional[str] = None) -> MacAgentResult:
        # Best-effort: if runtime supports rollback tools, agent path will execute them; otherwise it will respond safely.
        return self.agent("Rollback the last action if possible.", project_id=project_id, conversation_id=conversation_id, model=model)

    def smoke_test_compact(self) -> Dict[str, Any]:
        """
        Compact smoke test that mirrors the runtime-validated flows, returning machine-friendly results.
        This intentionally writes only within sandbox_test/.
        """
        ts = int(time.time())
        project = f"cli-smoke"
        conv = f"cli-smoke-{ts}"
        results = []

        def record(name: str, ok: bool, detail: str = "") -> None:
            results.append({"phase": name, "ok": ok, "detail": detail})

        try:
            h = self.health()
            record("health", bool(h.get("ok") is True), json.dumps(h)[:200])
        except Exception as e:
            record("health", False, str(e))
            return {"ok": False, "results": results}

        try:
            out = self.ask("Reply with exactly MACAGENT_OK", project_id=project, conversation_id=conv).message.strip()
            record("fast_path", "MACAGENT_OK" in out, out[:200])
        except Exception as e:
            record("fast_path", False, str(e))

        try:
            self.ask("Use FastAPI as the backend framework. Reply OK only.", project_id=project, conversation_id=conv)
            recall = self.ask("What backend framework did we decide to use? Answer one word only.", project_id=project, conversation_id=conv).message.strip()
            record("memory", "FastAPI" in recall, recall[:200])
        except Exception as e:
            record("memory", False, str(e))

        try:
            agent_out = self.agent("Inspect the app folder and identify the main API entry file.", project_id=project, conversation_id=conv)
            record("agent_inspect", "app/main.py" in agent_out.message and "Task finished or iteration limit reached." not in agent_out.message, agent_out.message[:200])
        except Exception as e:
            record("agent_inspect", False, str(e))

        # Approval write flow
        path = f"sandbox_test/cli_smoke_{ts}.txt"
        content = f"CLI_SMOKE_{ts}"
        try:
            first = self.agent(f"Create a file at {path} with exact content: {content}", project_id=project, conversation_id=conv)
            record("approval_prompt", first.agent_status == "approval_required" and "awaiting_approval" in first.message, first.message[:200])
            second = self.approve(project_id=project, conversation_id=conv)
            record("approval_resume", path in second.message, second.message[:200])
        except Exception as e:
            record("approval_flow", False, str(e))

        ok = all(r["ok"] for r in results)
        return {"ok": ok, "results": results}

