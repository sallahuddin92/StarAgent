from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    """Unified interface for all LLM providers."""

    @property
    @abstractmethod
    def provider(self) -> str:
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        pass

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Send a chat completion request."""
        pass

    @abstractmethod
    async def text(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> str:
        """Send a chat completion request and return only the text content."""
        pass

    @abstractmethod
    async def generate_embeddings(self, text: str, model: Optional[str] = None) -> List[float]:
        """Generate vector embeddings for a given text."""
        pass


class OllamaProvider(LLMClient):
    """Ollama local model provider."""

    def __init__(self, base_url: str, default_model: str, http_client: httpx.AsyncClient):
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.default_model = default_model
        self.http = http_client

    @property
    def provider(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self.default_model

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": model or self.default_model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
        }
        if isinstance(num_predict, int) and num_predict > 0:
            payload["options"] = {"num_predict": int(num_predict)}
        
        r = await self.http.post(url, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama error {r.status_code}: {r.text[:300]}")
        return r.json()

    async def text(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> str:
        data = await self.chat(messages, model=model, temperature=temperature, num_predict=num_predict, **kwargs)
        return str(data.get("message", {}).get("content") or "")

    async def generate_embeddings(self, text: str, model: Optional[str] = None) -> List[float]:
        url = f"{self.base_url}/api/embeddings"
        payload = {"model": model or self.default_model, "prompt": text}
        r = await self.http.post(url, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama embeddings error {r.status_code}: {r.text[:300]}")
        return r.json().get("embedding", [])


class OpenAIProvider(LLMClient):
    """OpenAI cloud provider."""

    def __init__(self, api_key: str, default_model: str, http_client: httpx.AsyncClient, base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model or "gpt-4o"
        self.http = http_client

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self.default_model

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("OpenAI API key is missing.")
            
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature,
        }
        if num_predict:
            payload["max_tokens"] = num_predict

        timeout_val = kwargs.pop("timeout", None)
        if timeout_val is None:
            provider_timeout_env = f"{self.provider.upper()}_TIMEOUT"
            timeout_str = os.getenv(provider_timeout_env) or os.getenv("STARAGENT_LLM_TIMEOUT")
            try:
                timeout_val = float(timeout_str) if timeout_str else 900.0
            except ValueError:
                timeout_val = 900.0

        max_attempts = int(os.getenv("GROQ_MAX_RETRIES") or os.getenv("STARAGENT_LLM_MAX_RETRIES") or 3)
        attempt = 0
        while True:
            attempt += 1
            r = await self.http.post(url, headers=headers, json=payload, timeout=timeout_val)
            if r.status_code == 429:
                if attempt >= max_attempts:
                    raise RuntimeError(f"{self.provider.capitalize()} error 429: Rate limit exceeded after {max_attempts} attempts. {r.text[:300]}")
                
                # Parse retry-after
                wait_s = None
                for k, v in r.headers.items():
                    if k.lower() == "retry-after":
                        try:
                            wait_s = float(v)
                            break
                        except ValueError:
                            pass
                
                # Check response body if header not found or invalid
                if wait_s is None:
                    import re
                    m = re.search(r"(?:try again in|retry after|wait)\s*([0-9.]+)\s*(?:s|second)", r.text, re.IGNORECASE)
                    if m:
                        try:
                            wait_s = float(m.group(1))
                        except ValueError:
                            pass
                
                if wait_s is None:
                    import random
                    wait_s = (2 ** attempt) + random.uniform(0.1, 1.0)
                else:
                    import random
                    wait_s += random.uniform(0.1, 1.0)
                
                logger.warning(f"[LLM_RETRY] provider={self.provider} reason=429 wait={wait_s:.2f}")
                print(f"[LLM_RETRY] provider={self.provider} reason=429 wait={wait_s:.2f}", flush=True)
                import asyncio
                await asyncio.sleep(wait_s)
                continue
                
            if r.status_code >= 400:
                raise RuntimeError(f"{self.provider.capitalize()} error {r.status_code}: {r.text[:300]}")
            
            data = r.json()
            break
        
        # Adapt OpenAI response to StarAgent internal format (Ollama-like)
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        return {"message": {"content": message.get("content", "")}}

    async def text(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> str:
        data = await self.chat(messages, model=model, temperature=temperature, num_predict=num_predict, **kwargs)
        return data["message"]["content"]

    async def generate_embeddings(self, text: str, model: Optional[str] = None) -> List[float]:
        url = f"{self.base_url}/embeddings"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"model": "text-embedding-3-small", "input": text}
        r = await self.http.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"OpenAI embeddings error {r.status_code}: {r.text[:300]}")
        return r.json().get("data", [{}])[0].get("embedding", [])


class AnthropicProvider(LLMClient):
    """Anthropic cloud provider."""

    def __init__(self, api_key: str, default_model: str, http_client: httpx.AsyncClient):
        self.api_key = api_key
        self.default_model = default_model or "claude-3-5-sonnet-20240620"
        self.http = http_client

    @property
    def provider(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self.default_model

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("Anthropic API key is missing.")
            
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        
        # Convert messages to Anthropic format (separate system prompt)
        system_prompt = ""
        user_messages = []
        for m in messages:
            if m["role"] == "system":
                system_prompt = m["content"]
            else:
                user_messages.append(m)
                
        payload = {
            "model": model or self.default_model,
            "messages": user_messages,
            "max_tokens": num_predict or 4096,
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt

        r = await self.http.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Anthropic error {r.status_code}: {r.text[:300]}")
        data = r.json()
        
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
                
        return {"message": {"content": content}}

    async def text(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> str:
        data = await self.chat(messages, model=model, temperature=temperature, num_predict=num_predict, **kwargs)
        return data["message"]["content"]

    async def generate_embeddings(self, text: str, model: Optional[str] = None) -> List[float]:
        raise NotImplementedError("Anthropic does not natively support embeddings via this API.")


class GeminiProvider(LLMClient):
    """Google Gemini cloud provider."""

    def __init__(self, api_key: str, default_model: str, http_client: httpx.AsyncClient):
        self.api_key = api_key
        self.default_model = default_model or "gemini-1.5-pro"
        self.http = http_client

    @property
    def provider(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self.default_model

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("Gemini API key is missing.")
            
        m = model or self.default_model
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={self.api_key}"
        
        # Convert messages to Gemini format
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            # system prompt as a user message for simplicity if not supported natively in this endpoint
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })
            
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": num_predict or 4096,
            }
        }

        r = await self.http.post(url, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:300]}")
        data = r.json()
        
        content = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return {"message": {"content": content}}

    async def text(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> str:
        data = await self.chat(messages, model=model, temperature=temperature, num_predict=num_predict, **kwargs)
        return data["message"]["content"]

    async def generate_embeddings(self, text: str, model: Optional[str] = None) -> List[float]:
        m = model or "text-embedding-004"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:embedContent?key={self.api_key}"
        payload = {
            "model": f"models/{m}",
            "content": {"parts": [{"text": text}]}
        }
        r = await self.http.post(url, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini embeddings error {r.status_code}: {r.text[:300]}")
        return r.json().get("embedding", {}).get("values", [])


class LongCatProvider(OpenAIProvider):
    """LongCat provider (OpenAI-compatible)."""

    def __init__(self, api_key: str, default_model: str, http_client: httpx.AsyncClient):
        super().__init__(
            api_key=api_key,
            default_model=default_model or "LongCat-Flash-Thinking-2601",
            http_client=http_client,
            base_url="https://api.longcat.chat/openai/v1"
        )

    @property
    def provider(self) -> str:
        return "longcat"

    @property
    def model_name(self) -> str:
        return self.default_model

    async def generate_embeddings(self, text: str, model: Optional[str] = None) -> List[float]:
        raise NotImplementedError("LongCat does not support embeddings via this API.")


class GroqProvider(OpenAIProvider):
    """Groq provider (OpenAI-compatible)."""

    def __init__(self, api_key: str, default_model: str, http_client: httpx.AsyncClient):
        super().__init__(
            api_key=api_key,
            default_model=default_model or "llama-3.1-8b-instant",
            http_client=http_client,
            base_url=os.getenv("GROQ_BASE_URL") or "https://api.groq.com/openai/v1"
        )

    @property
    def provider(self) -> str:
        return "groq"

    @property
    def model_name(self) -> str:
        return self.default_model

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("Groq API key is missing (GROQ_API_KEY).")
        if num_predict is None:
            num_predict = 1024
        return await super().chat(messages, model=model, temperature=temperature, num_predict=num_predict, **kwargs)

    async def generate_embeddings(self, text: str, model: Optional[str] = None) -> List[float]:
        raise NotImplementedError("Groq does not support embeddings via this API.")


class UniversalLLMClient(LLMClient):
    """
    A client that routes requests to the appropriate provider based on the model name.
    """

    def __init__(self, http_client: httpx.AsyncClient):
        self.http = http_client
        self._providers: Dict[str, LLMClient] = {}

    def _get_provider(self, model: str) -> LLMClient:
        provider_name = self.infer_provider(model)
        if provider_name not in self._providers:
            if provider_name == "openai":
                self._providers[provider_name] = OpenAIProvider(
                    api_key=os.getenv("OPENAI_API_KEY"),
                    default_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                    http_client=self.http
                )
            elif provider_name == "anthropic":
                self._providers[provider_name] = AnthropicProvider(
                    api_key=os.getenv("ANTHROPIC_API_KEY"),
                    default_model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20240620"),
                    http_client=self.http
                )
            elif provider_name == "gemini":
                self._providers[provider_name] = GeminiProvider(
                    api_key=os.getenv("GEMINI_API_KEY"),
                    default_model=os.getenv("GEMINI_MODEL", "gemini-1.5-pro"),
                    http_client=self.http
                )
            elif provider_name == "longcat":
                self._providers[provider_name] = LongCatProvider(
                    api_key=os.getenv("LONGCAT_API_KEY"),
                    default_model=os.getenv("LONGCAT_MODEL", "LongCat-Flash-Thinking-2601"),
                    http_client=self.http
                )
            elif provider_name == "groq":
                self._providers[provider_name] = GroqProvider(
                    api_key=os.getenv("GROQ_API_KEY"),
                    default_model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
                    http_client=self.http
                )
            elif provider_name == "openai_compatible":
                self._providers[provider_name] = OpenAIProvider(
                    api_key=os.getenv("OPENAI_COMPATIBLE_API_KEY"),
                    default_model=os.getenv("OPENAI_COMPATIBLE_MODEL"),
                    base_url=os.getenv("OPENAI_COMPATIBLE_BASE_URL"),
                    http_client=self.http
                )
            else:
                self._providers[provider_name] = OllamaProvider(
                    base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                    default_model=os.getenv("DEFAULT_MODEL", "gemma4:12b-mlx"),
                    http_client=self.http
                )
        return self._providers[provider_name]

    def infer_provider(self, model: str) -> str:
        if not model:
            return os.getenv("STARAGENT_LLM_PROVIDER", "ollama").lower()
        if model.startswith("gpt-"): return "openai"
        if model.startswith("claude-"): return "anthropic"
        if model.startswith("gemini-"): return "gemini"
        if "longcat" in model.lower(): return "longcat"
        if "llama-3.1-8b-instant" in model.lower() and os.getenv("GROQ_API_KEY"): return "groq"
        return "ollama"

    @property
    def provider(self) -> str:
        return "universal"

    @property
    def model_name(self) -> str:
        return "dynamic"

    async def chat(self, messages: List[Dict[str, str]], *, model: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        p = self._get_provider(model)
        return await p.chat(messages, model=model, **kwargs)

    async def text(self, messages: List[Dict[str, str]], *, model: Optional[str] = None, **kwargs) -> str:
        p = self._get_provider(model)
        return await p.text(messages, model=model, **kwargs)

    async def generate_embeddings(self, text: str, model: Optional[str] = None) -> List[float]:
        p = self._get_provider(model)
        return await p.generate_embeddings(text, model=model)


def get_llm_client(http_client: httpx.AsyncClient) -> LLMClient:
    """Factory to create the universal LLM client."""
    return UniversalLLMClient(http_client)


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


# For backward compatibility (legacy name)
OllamaChatClient = OllamaProvider
