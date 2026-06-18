from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx


@dataclass
class MacAgentConfig:
    base_url: str
    api_key: str
    default_model: str = "gemma4:12b-mlx"
    default_project_id: str = "default"
    default_conversation_id: str = "default"
    timeout_s: float = 300.0

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
            or "gemma4:12b-mlx"
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


class _StreamRenderer:
    """Render SSE content in full/compact/quiet modes."""

    def __init__(self, mode: str = "full"):
        self.mode = (mode or "full").lower()
        self._line_buf = ""
        self._pending_step: Dict[str, Tuple[str, str]] = {}
        self._seen_docs = False
        self._quiet_ticks = 0
        self._in_verify_block = False

    @staticmethod
    def _parse_role_line(line: str) -> Tuple[Optional[str], str]:
        m = re.match(r"^\[([A-Z_]+)\]\s*(.*)$", (line or "").strip())
        if not m:
            return None, line
        return m.group(1), m.group(2)

    @staticmethod
    def _strip_quotes(text: str) -> str:
        t = (text or "").strip()
        if len(t) >= 2 and t[0] in {"'", '"'} and t[-1] == t[0]:
            return t[1:-1]
        return t

    @staticmethod
    def _summarize_step(step: str) -> Tuple[str, str]:
        s = (step or "").strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                data = json.loads(s)
                tool = str(data.get("tool") or "step")
                args = data.get("args") or {}
                if tool == "write_file":
                    return tool, str(args.get("path") or "file")
                if tool == "run_command":
                    return tool, str(args.get("command") or "command")
                if tool in {"create_directory", "list_files", "read_file"}:
                    return tool, str(args.get("path") or "path")
                return tool, str(args)[:80]
            except Exception:
                pass
        m = re.match(r"([a-zA-Z_][a-zA-Z0-9_]*)\((.*)\)$", s)
        if not m:
            return "step", s[:80]
        tool = m.group(1)
        args = m.group(2)
        if tool == "write_file":
            am = re.match(r'\s*(".*?"|\'.*?\')\s*,', args, re.S)
            path = _StreamRenderer._strip_quotes(am.group(1)) if am else "file"
            return tool, path
        if tool == "run_command":
            am = re.match(r'\s*(".*?"|\'.*?\')', args, re.S)
            cmd = _StreamRenderer._strip_quotes(am.group(1)) if am else "command"
            return tool, cmd
        if tool in {"create_directory", "list_files", "read_file"}:
            am = re.match(r'\s*(".*?"|\'.*?\')', args, re.S)
            target = _StreamRenderer._strip_quotes(am.group(1)) if am else "path"
            return tool, target
        return tool, args[:80]

    def _print_compact_line(self, role: str, msg: str) -> None:
        import sys
        sys.stdout.write(f"[{role}] {msg}\n")
        sys.stdout.flush()

    def _print_quiet_progress(self) -> None:
        import sys
        self._quiet_ticks += 1
        if self._quiet_ticks % 3 == 0:
            sys.stdout.write(".")
            sys.stdout.flush()

    def _render_line_compact(self, line: str) -> None:
        raw = (line or "").rstrip()
        if raw.lstrip().startswith("- ") and self._in_verify_block:
            self._print_compact_line("VERIFY", raw.strip())
            return

        role, msg = self._parse_role_line(line)
        if not role:
            return
        plain = (msg or "").strip()
        if not plain:
            return

        if "Received task:" in plain:
            self._print_compact_line("ORCHESTRATOR", "planning task...")
            return
        if plain.startswith("[MODEL] "):
            self._print_compact_line("ROUTER", plain.replace("[MODEL] ", "").strip())
            return
        if plain.startswith("[ROUTER] "):
            self._print_compact_line("ROUTER", plain.replace("[ROUTER] ", "").strip())
            return
        if plain.startswith("[MODEL_ROUTER] "):
            self._print_compact_line("ROUTER", plain.replace("[MODEL_ROUTER] ", "").strip())
            return
        # Handle [GUARD], [GUARD:INSPECT], [GUARD:SYNTHESIZE], etc.
        guard_m = re.match(r'\[GUARD(?::(\w+))?\]\s*(.*)', plain)
        if guard_m:
            phase = guard_m.group(1)
            body = guard_m.group(2).strip()
            label = f"GUARD:{phase}" if phase else "GUARD"
            self._print_compact_line(label, body)
            return
        if plain.startswith("[PATH_GUARD] "):
            self._print_compact_line("GUARD", plain.replace("[PATH_GUARD] ", "").strip())
            return
        if plain.startswith("[DOCS_EVIDENCE] query="):
            self._seen_docs = True
            q = plain.split("query=", 1)[1].strip()
            self._print_compact_line("DOCS", f"project-doc query: {q[:120]}")
            return
        if plain.startswith("[ERROR] insufficient project documentation evidence"):
            self._print_compact_line("DOCS", "insufficient project documentation evidence ❌")
            return
        if plain.startswith("Dispatching to ["):
            rm = re.search(r"Dispatching to \[([A-Z_]+)\]", plain)
            if rm:
                self._print_compact_line("ORCHESTRATOR", f"dispatch -> {rm.group(1)}")
            return
        if plain.startswith("[STEP] "):
            step = plain[len("[STEP] "):].strip()
            tool, target = self._summarize_step(step)
            self._pending_step[role] = (tool, target)
            return
        if plain.startswith("[RESULT] "):
            result = plain[len("[RESULT] "):]
            tool, target = self._pending_step.pop(role, ("result", ""))
            success = ("[Success]" in result) or ("Successfully" in result)
            failure = ("[Failed]" in result) or ("Error" in result) or ("failed" in result.lower())
            mark = "✅" if success and not failure else ("❌" if failure else "•")
            label = f"{tool} {target}".strip()
            self._print_compact_line(role, f"{label} {mark}")
            return
        if plain.startswith("Verifier Gate:"):
            self._in_verify_block = True
            if "PASS" in plain:
                self._print_compact_line("VERIFY", plain.replace("Verifier Gate:", "").strip() + " ✅")
            else:
                self._print_compact_line("VERIFY", "failed ❌")
            return
        if plain.startswith("[REPORT] "):
            body = plain.replace("[REPORT] ", "").strip()
            if body.startswith("==="):
                self._print_compact_line("REPORT", body)
            elif body.startswith("  -"):
                self._print_compact_line("REPORT", body.strip())
            else:
                self._print_compact_line("REPORT", body)
            return
        if plain.startswith("Failures:") or plain.startswith("# Multi-Agent Execution Report"):
            return
        if plain.startswith("- Required") or plain.startswith("- missing") or plain.startswith("- command"):
            self._print_compact_line("VERIFY", plain)
            return

    def _render_line_quiet(self, line: str) -> None:
        role, msg = self._parse_role_line(line)
        if not role:
            return
        plain = (msg or "").strip()
        if not plain:
            return
        if "Received task:" in plain:
            self._print_compact_line("TASK", "started")
            return
        if plain.startswith("Dispatching to ["):
            rm = re.search(r"Dispatching to \[([A-Z_]+)\]", plain)
            if rm:
                self._print_compact_line("TASK", f"in progress ({rm.group(1)})")
            return
        self._print_quiet_progress()

    def feed(self, content: str) -> None:
        import sys
        if self.mode == "full":
            sys.stdout.write(content)
            sys.stdout.flush()
            return

        self._line_buf += content
        while "\n" in self._line_buf:
            line, self._line_buf = self._line_buf.split("\n", 1)
            if self.mode == "compact":
                self._render_line_compact(line)
            elif self.mode == "quiet":
                self._render_line_quiet(line)

    def finalize(self, *, trace_id: Optional[str] = None, agent_status: Optional[str] = None) -> None:
        import sys
        if self.mode == "quiet" and self._quiet_ticks > 0:
            sys.stdout.write("\n")
            sys.stdout.flush()
        if self.mode == "compact" and not self._seen_docs:
            self._print_compact_line("DOCS", "no project-doc requirement detected")
        if agent_status and self.mode in {"compact", "quiet"}:
            self._print_compact_line("x_agent_status", agent_status)
        if trace_id and self.mode in {"compact", "quiet"}:
            self._print_compact_line("TRACE", f"Full trace: ./scripts/staragent trace {trace_id}")


def _normalize_v1_base_url(base_url: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    if base_url.endswith("/v1"):
        return base_url
    # Accept either http://host:port or http://host:port/v1
    return base_url + "/v1"


def _root_from_v1(v1_url: str) -> str:
    v1_url = _normalize_v1_base_url(v1_url)
    return v1_url[: -len("/v1")]


def _resolve_client_path(path: Optional[str]) -> Optional[str]:
    """
    Resolve a user-provided path on the client side.

    Important: the server cannot safely interpret relative paths because its CWD
    may differ from the CLI/MCP client CWD. Always send absolute paths when possible.
    """
    if path is None:
        return None
    p = os.path.expanduser(str(path))
    if not os.path.isabs(p):
        p = os.path.abspath(p)
    return p


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

    def health(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        r = self._http.get(f"{self.root_base_url}/health", timeout=timeout)
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
        path = _resolve_client_path(path)
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
        path = _resolve_client_path(path)
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

    def web_search(self, query: str, max_results: int = 5, project_id: Optional[str] = None) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/search", params={"q": query, "max_results": max_results, "project_id": project_id}, headers=headers)
        r.raise_for_status()
        return r.json()

    def web_fetch(self, url: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.post(f"{self.v1_base_url}/web/fetch", params={"url": url}, headers=headers)
        r.raise_for_status()
        return r.json()

    def web_extract(self, url: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.post(f"{self.v1_base_url}/web/extract", params={"url": url, "project_id": project_id}, headers=headers)
        r.raise_for_status()
        return r.json()

    def web_research(self, query: str, max_results: int = 5, max_sources: int = 3, project_id: Optional[str] = None) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.post(f"{self.v1_base_url}/web/research", params={"query": query, "max_results": max_results, "max_sources": max_sources, "project_id": project_id}, headers=headers)
        r.raise_for_status()
        return r.json()

    def search_sources(self, query: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/sources/search", params={"q": query, "project_id": project_id}, headers=headers)
        r.raise_for_status()
        return r.json()

    def semantic_search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/sources/semantic_search", params={"q": query, "limit": limit}, headers=headers)
        r.raise_for_status()
        return r.json()

    def index_file(self, path: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.post(f"{self.v1_base_url}/documents/index_file", params={"path": path, "project_id": project_id}, headers=headers)
        r.raise_for_status()
        return r.json()

    def index_folder(self, path: str, project_id: Optional[str] = None) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.post(f"{self.v1_base_url}/documents/index_folder", params={"path": path, "project_id": project_id}, headers=headers)
        r.raise_for_status()
        return r.json()

    def docs_ingest(self, path: str, source_type: str = "project_docs", project_id: Optional[str] = None) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        payload = {"path": path, "source_type": source_type, "project_id": project_id}
        r = self._http.post(f"{self.v1_base_url}/docs/ingest", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def docs_ingest_package(self, package_name: str, manager: str = "pip", project_id: Optional[str] = None) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        payload = {"package_name": package_name, "manager": manager, "project_id": project_id}
        r = self._http.post(f"{self.v1_base_url}/docs/ingest-package", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def docs_search(self, query: str, package_name: Optional[str] = None, max_results: int = 5, project_id: Optional[str] = None) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        payload: Dict[str, Any] = {"query": query, "max_results": max_results, "project_id": project_id}
        if package_name:
            payload["package_name"] = package_name
        r = self._http.post(f"{self.v1_base_url}/docs/search", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()

    def docs_ask(
        self,
        question: str,
        package_name: Optional[str] = None,
        max_results: int = 5,
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        project_id = project_id or self.config.default_project_id
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        payload: Dict[str, Any] = {"question": question, "max_results": max_results, "project_id": project_id}
        if package_name:
            payload["package_name"] = package_name
        r = self._http.post(f"{self.v1_base_url}/docs/ask", json=payload, headers=headers)
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
        path = _resolve_client_path(path) or path
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
        path = _resolve_client_path(path) or path
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
        path = _resolve_client_path(path) or path
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
        path = _resolve_client_path(path) or path
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
        stream_mode: str = "full",
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
        
        if stream:
            final_content = ""
            agent_status: Optional[str] = None
            raw_data = {}
            trace_id = None
            renderer = _StreamRenderer(stream_mode)
            try:
                with self._http.stream("POST", f"{self.v1_base_url}/chat/completions", json=payload, headers=headers) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[len("data: "):]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            if "x_agent_status" in chunk:
                                agent_status = chunk["x_agent_status"]
                            if "x_trace_id" in chunk:
                                trace_id = chunk["x_trace_id"]
                            raw_data = chunk  # Keep the last chunk's meta

                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            if "content" in delta:
                                content = delta["content"]
                                final_content += content
                                renderer.feed(content)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                agent_status = "failed"
            renderer.finalize(trace_id=trace_id, agent_status=agent_status)
            return MacAgentResult(message=final_content, agent_status=agent_status, raw=raw_data)
        else:
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
        # Allow the server to determine the route (agent vs fast) based on the prompt.
        return self.chat(prompt, **kwargs)

    def agent(self, prompt: str, **kwargs: Any) -> MacAgentResult:
        return self.chat(prompt, force_route="agent", **kwargs)

    def multi_agent(
        self,
        task: str,
        *,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        stream: bool = False,
        stream_mode: str = "full",
    ) -> MacAgentResult:
        """Run a multi-agent orchestration task."""
        project_id = project_id or self.config.default_project_id
        conversation_id = conversation_id or self.config.default_conversation_id

        payload = {
            "task": task,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "stream": stream,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}

        if stream:
            final_content = ""
            agent_status: Optional[str] = None
            raw_data = {}
            trace_id = None
            renderer = _StreamRenderer(stream_mode)
            try:
                with self._http.stream("POST", f"{self.v1_base_url}/multi-agent/run", json=payload, headers=headers) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[len("data: "):]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            if "x_agent_status" in chunk:
                                agent_status = chunk["x_agent_status"]
                            if "x_trace_id" in chunk:
                                trace_id = chunk["x_trace_id"]
                            raw_data = chunk

                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            if "content" in delta:
                                content = delta["content"]
                                final_content += content
                                renderer.feed(content)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                agent_status = "failed"
            renderer.finalize(trace_id=trace_id, agent_status=agent_status)
            return MacAgentResult(message=final_content, agent_status=agent_status, raw=raw_data)
        else:
            r = self._http.post(f"{self.v1_base_url}/multi-agent/run", json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            return MacAgentResult(message=data.get("message", ""), agent_status=data.get("status"), raw=data)

    def approve(self, *, project_id: Optional[str] = None, conversation_id: Optional[str] = None, model: Optional[str] = None) -> MacAgentResult:
        return self.chat("yes", project_id=project_id, conversation_id=conversation_id, model=model)

    def reject(self, *, project_id: Optional[str] = None, conversation_id: Optional[str] = None, model: Optional[str] = None) -> MacAgentResult:
        return self.chat("no", project_id=project_id, conversation_id=conversation_id, model=model)

    def continue_task(self, *, project_id: Optional[str] = None, conversation_id: Optional[str] = None, model: Optional[str] = None) -> MacAgentResult:
        return self.chat("continue", project_id=project_id, conversation_id=conversation_id, model=model)

    def rollback(self, *, project_id: Optional[str] = None, conversation_id: Optional[str] = None, model: Optional[str] = None) -> MacAgentResult:
        # Best-effort: if runtime supports rollback tools, agent path will execute them; otherwise it will respond safely.
        return self.agent("Rollback the last action if possible.", project_id=project_id, conversation_id=conversation_id, model=model)

    def get_file_tree(self, path: str, max_depth: int = 3) -> Dict[str, Any]:
        params = {"path": _resolve_client_path(path), "max_depth": max_depth}
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/files/tree", headers=headers, params=params)
        r.raise_for_status()
        return r.json()

    def read_memory(self, query: str, limit: int = 10, project_id: Optional[str] = None, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        params = {
            "query": query, 
            "limit": limit,
            "project_id": project_id or self.config.default_project_id,
            "conversation_id": conversation_id or self.config.default_conversation_id
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/memory/search", headers=headers, params=params)
        r.raise_for_status()
        return r.json()

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

    def workflows_list(self) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows", headers=headers)
        return r.json()

    def workflow_inspect(self, name: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{name}", headers=headers)
        return r.json()

    def workflow_graph(self, name: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{name}/graph", headers=headers)
        return r.json()

    def workflow_run(
        self,
        name: str,
        project_id: str,
        conversation_id: str,
        goal: str,
        definition_of_done: Optional[str] = None,
        mode: str = "live",
        urls: Optional[List[str]] = None,
        docs: bool = False
    ) -> Dict[str, Any]:
        payload = {
            "name": name,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "goal": goal,
            "definition_of_done": definition_of_done,
            "mode": mode,
            "urls": urls or [],
            "docs": docs
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/workflows/run", json=payload, headers=headers)
        return r.json()

    def workflow_create(self, name: str, description: str = "") -> Dict[str, Any]:
        payload = {"name": name, "description": description}
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/workflows/create", json=payload, headers=headers)
        return r.json()

    def workflow_resume(self, task_id: str, stage: Optional[str] = None) -> Dict[str, Any]:
        payload = {"stage": stage}
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/workflows/{task_id}/resume", json=payload, headers=headers)
        return r.json()

    def workflow_checkpoints(self, task_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{task_id}/checkpoints", headers=headers)
        return r.json()

    def workflow_runs(self) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/runs", headers=headers)
        return r.json()

    def workflow_run_status(self, run_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{run_id}/status", headers=headers)
        return r.json()

    def workflow_run_trace(self, run_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{run_id}/trace", headers=headers)
        return r.json()

    def workflow_run_state(self, run_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{run_id}/state", headers=headers)
        return r.json()

    def workflow_run_gates(self, run_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{run_id}/gates", headers=headers)
        return r.json()

    def workflow_run_approve(self, run_id: str, stage: Optional[str] = None) -> Dict[str, Any]:
        payload = {"stage": stage}
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/workflows/{run_id}/approve", json=payload, headers=headers)
        return r.json()

    def workflow_run_reject(self, run_id: str, stage: Optional[str] = None) -> Dict[str, Any]:
        payload = {"stage": stage}
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/workflows/{run_id}/reject", json=payload, headers=headers)
        return r.json()

    def workflow_run_report(self, run_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{run_id}/report", headers=headers)
        return r.json()

    def workflow_explain(self, workflow_name: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{workflow_name}/explain", headers=headers)
        return r.json()

    def workflow_run_sources(self, run_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{run_id}/sources", headers=headers)
        return r.json()

    def workflow_run_evidence(self, run_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{run_id}/evidence", headers=headers)
        return r.json()

    # ── v0.6.1 Runtime Hardening ──────────────────────────────────────

    def workflow_cleanup(self, older_than: str = "7d", dry_run: bool = False) -> Dict[str, Any]:
        """Clean up workflow runtime directories older than the given duration
        (e.g. ``7d``, ``30d``, ``24h``). Returns count of cleaned runs.
        When dry_run is True, returns candidate directories without deleting."""
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.post(
            f"{self.v1_base_url}/workflows/cleanup",
            json={"older_than": older_than, "dry_run": dry_run},
            headers=headers,
        )
        return r.json()

    def workflow_doctor(self, run_id: str) -> Dict[str, Any]:
        """Diagnose a workflow run and return health information about
        its state files, stages, and any detected anomalies."""
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{run_id}/doctor", headers=headers)
        return r.json()

    def workflow_replay(self, run_id: str) -> Dict[str, Any]:
        """Replay the trace of a completed workflow run for diagnostics.
        Returns structured event log with timing information."""
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/workflows/{run_id}/replay", headers=headers)
        return r.json()

    # =========================================================================
    # Benchmark Suite
    # =========================================================================

    def benchmark_list(self) -> Dict[str, Any]:
        """List available benchmark cases."""
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/benchmarks", headers=headers)
        return r.json()

    def benchmark_run(self, case_name: Optional[str] = None) -> Dict[str, Any]:
        """Run a benchmark case (or all cases if None)."""
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/benchmarks/run", json={"case_name": case_name}, headers=headers)
        return r.json()

    def benchmark_score(self, run_id: str) -> Dict[str, Any]:
        """Get scores for a completed benchmark run."""
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/benchmarks/{run_id}/score", headers=headers)
        return r.json()

    def benchmark_history(self) -> Dict[str, Any]:
        """List all completed benchmark runs."""
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        r = self._http.get(f"{self.v1_base_url}/benchmarks/history", headers=headers)
        return r.json()

    def benchmark_compare(self, run_id_a: str, run_id_b: str) -> Dict[str, Any]:
        """Compare two benchmark runs and detect regression."""
        headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
        r = self._http.post(f"{self.v1_base_url}/benchmarks/compare", json={"run_id_a": run_id_a, "run_id_b": run_id_b}, headers=headers)
        return r.json()
