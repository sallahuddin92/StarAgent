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
        # StarAgent is the user-facing product name. For compatibility, we still
        # accept the legacy MACAGENT_* variables.
        base = (
            os.getenv("STARAGENT_BASE_URL")
            or os.getenv("STARAGENT_API_BASE_URL")
            or os.getenv("MACAGENT_BASE_URL")
            or os.getenv("MACAGENT_API_BASE_URL")
            or "http://127.0.0.1:8095/v1"
        )
        api_key = (
            os.getenv("STARAGENT_API_KEY")
            or os.getenv("MACAGENT_API_KEY")
            or os.getenv("PROXY_API_KEY")
            or "local-dev-key"
        )
        model = (
            os.getenv("STARAGENT_DEFAULT_MODEL")
            or os.getenv("MACAGENT_DEFAULT_MODEL")
            or os.getenv("DEFAULT_MODEL")
            or "gemma4:e2b"
        )
        proj = os.getenv("STARAGENT_DEFAULT_PROJECT") or os.getenv("MACAGENT_DEFAULT_PROJECT") or "default"
        conv = os.getenv("STARAGENT_DEFAULT_CONVERSATION") or os.getenv("MACAGENT_DEFAULT_CONVERSATION") or "default"
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

    # =========================================================================
    # Preset Workflows
    # =========================================================================

    def presets_list(self) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/presets", headers=headers)
        r.raise_for_status()
        return r.json()

    def preset_packs_list(self) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/presets/packs", headers=headers)
        r.raise_for_status()
        return r.json()

    def preset_run(
        self,
        preset_name: str,
        *,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        path: Optional[str] = None,
        question: Optional[str] = None,
        issue: Optional[str] = None,
        goal: Optional[str] = None,
        files: Optional[list[str]] = None,
        logs: Optional[list[str]] = None,
        mode: Optional[str] = None,
        output_path: Optional[str] = None,
        max_steps: Optional[int] = None,
        max_retries: Optional[int] = None,
        run_now: bool = True,
    ) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        conversation_id = conversation_id or self.config.default_conversation_id
        payload: Dict[str, Any] = {
            "project_id": project_id,
            "conversation_id": conversation_id,
            "path": path,
            "question": question,
            "issue": issue,
            "goal": goal,
            "files": files,
            "logs": logs,
            "mode": mode,
            "output_path": output_path,
            "max_steps": max_steps,
            "max_retries": max_retries,
            "run_now": run_now,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/presets/{preset_name}/run", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def preset_pack_run(
        self,
        pack_name: str,
        *,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        path: Optional[str] = None,
        question: Optional[str] = None,
        issue: Optional[str] = None,
        goal: Optional[str] = None,
        files: Optional[list[str]] = None,
        logs: Optional[list[str]] = None,
        mode: Optional[str] = None,
        output_path: Optional[str] = None,
        max_steps: Optional[int] = None,
        max_retries: Optional[int] = None,
        run_now: bool = True,
    ) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        conversation_id = conversation_id or self.config.default_conversation_id
        payload: Dict[str, Any] = {
            "project_id": project_id,
            "conversation_id": conversation_id,
            "path": path,
            "question": question,
            "issue": issue,
            "goal": goal,
            "files": files,
            "logs": logs,
            "mode": mode,
            "output_path": output_path,
            "max_steps": max_steps,
            "max_retries": max_retries,
            "run_now": run_now,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/presets/packs/{pack_name}/run", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    # =========================================================================
    # Phase 4: Tasks / Research Mode
    # =========================================================================

    def task_create(
        self,
        *,
        user_goal: str,
        task_type: str = "agent",
        definition_of_done: Optional[str] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        max_steps: int = 25,
        max_retries: int = 2,
        run_now: bool = True,
    ) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        conversation_id = conversation_id or self.config.default_conversation_id
        payload = {
            "project_id": project_id,
            "conversation_id": conversation_id,
            "task_type": task_type,
            "user_goal": user_goal,
            "definition_of_done": definition_of_done,
            "max_steps": max_steps,
            "max_retries": max_retries,
            "run_now": run_now,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/tasks", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def task_status(self, task_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.get(f"{self.v1_base_url}/tasks/{task_id}", headers=headers)
        r.raise_for_status()
        return r.json()

    def task_list(
        self,
        *,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if project_id:
            params["project_id"] = project_id
        if conversation_id:
            params["conversation_id"] = conversation_id
        if status:
            params["status"] = status
        r = self._http.get(f"{self.v1_base_url}/tasks", headers=headers, params=params)
        r.raise_for_status()
        return r.json()

    def task_inspect(self, task_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/tasks/{task_id}/inspect", headers=headers)
        r.raise_for_status()
        return r.json()

    def task_summary(self, task_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/tasks/{task_id}/summary", headers=headers)
        r.raise_for_status()
        return r.json()

    def task_logs(self, task_id: str, *, tail_steps: int = 50) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/tasks/{task_id}/logs", headers=headers, params={"tail_steps": tail_steps})
        r.raise_for_status()
        return r.json()

    def task_artifacts(self, task_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.get(f"{self.v1_base_url}/tasks/{task_id}/artifacts", headers=headers)
        r.raise_for_status()
        return r.json()

    def task_artifact_preview(
        self,
        task_id: str,
        artifact_name: str,
        *,
        format: str = "text",
        max_bytes: int = 50_000,
        tail_lines: int = 200,
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(
            f"{self.v1_base_url}/tasks/{task_id}/artifacts/{artifact_name}",
            headers=headers,
            params={"format": format, "max_bytes": max_bytes, "tail_lines": tail_lines},
        )
        r.raise_for_status()
        return r.json()

    def task_action(
        self,
        task_id: str,
        *,
        action: str = "continue",
        reason: Optional[str] = None,
        max_step_advances: int = 3,
        max_duration_s: float = 20.0,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "action": action,
            "max_step_advances": max_step_advances,
            "max_duration_s": max_duration_s,
        }
        if reason:
            payload["reason"] = reason
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/tasks/{task_id}/continue", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def research_run(
        self,
        *,
        path: str,
        files: Optional[list[str]] = None,
        question: Optional[str] = None,
        mode: str = "research",
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        max_steps: int = 60,
        max_retries: int = 1,
        run_now: bool = True,
    ) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        conversation_id = conversation_id or self.config.default_conversation_id
        payload = {
            "project_id": project_id,
            "conversation_id": conversation_id,
            "path": path,
            "files": files,
            "question": question,
            "mode": mode,
            "max_steps": max_steps,
            "max_retries": max_retries,
            "run_now": run_now,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/research/run", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def repo_audit_run(
        self,
        *,
        path: str,
        question: Optional[str] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        max_steps: int = 25,
        max_retries: int = 1,
        run_now: bool = True,
    ) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        conversation_id = conversation_id or self.config.default_conversation_id
        payload = {
            "project_id": project_id,
            "conversation_id": conversation_id,
            "path": path,
            "question": question,
            "max_steps": max_steps,
            "max_retries": max_retries,
            "run_now": run_now,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/repo_audit/run", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def issue_triage_run(
        self,
        *,
        path: str,
        issue: str,
        files: Optional[list[str]] = None,
        logs: Optional[list[str]] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        max_steps: int = 25,
        max_retries: int = 1,
        run_now: bool = True,
    ) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        conversation_id = conversation_id or self.config.default_conversation_id
        payload = {
            "project_id": project_id,
            "conversation_id": conversation_id,
            "path": path,
            "issue": issue,
            "files": files,
            "logs": logs,
            "max_steps": max_steps,
            "max_retries": max_retries,
            "run_now": run_now,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/issue_triage/run", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def write_run(
        self,
        *,
        path: str,
        goal: str,
        files: Optional[list[str]] = None,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        max_steps: int = 25,
        max_retries: int = 1,
        run_now: bool = True,
    ) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        conversation_id = conversation_id or self.config.default_conversation_id
        payload = {
            "project_id": project_id,
            "conversation_id": conversation_id,
            "path": path,
            "goal": goal,
            "files": files,
            "max_steps": max_steps,
            "max_retries": max_retries,
            "run_now": run_now,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/write/run", json=payload, headers=headers)
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
            out = self.ask("Reply with exactly STARAGENT_OK", project_id=project, conversation_id=conv).message.strip()
            record("fast_path", "STARAGENT_OK" in out, out[:200])
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
