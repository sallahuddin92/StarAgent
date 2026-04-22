from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class OllamaChatClient:
    """
    Minimal Ollama chat adapter used by Phase 4 research mode.

    We keep this intentionally small and explicit so small local models can succeed:
    one request at a time, short prompts, strict output schemas.
    """

    def __init__(self, base_url: str, chat_path: str, default_model: str, http_client: httpx.AsyncClient):
        self.base_url = (base_url or "").rstrip("/")
        self.chat_path = chat_path or "/api/chat"
        self.default_model = default_model
        self.http = http_client

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{self.chat_path}"
        payload = {
            "model": model or self.default_model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        r = await self.http.post(url, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama error {r.status_code}: {r.text[:300]}")
        data = r.json()
        if not isinstance(data, dict) or "message" not in data:
            raise RuntimeError("Unexpected Ollama response shape")
        return data

    async def text(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        data = await self.chat(messages, model=model, temperature=temperature)
        msg = data.get("message") or {}
        content = msg.get("content")
        return str(content or "")


def extract_json_object(text: str) -> Dict[str, Any]:
    """
    Small-model-friendly JSON repair: extract the outermost {...} block.
    Returns {} if parsing fails.
    """
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        logger.warning("Failed to parse JSON from model output (truncated): %r", text[:200])
        return {}

