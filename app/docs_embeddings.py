from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

import importlib.util
import threading

try:
    _HAS_SENTENCE_TRANSFORMERS = importlib.util.find_spec("sentence_transformers") is not None
except Exception:
    _HAS_SENTENCE_TRANSFORMERS = False

_DOCS_EMBEDDING_MODELS: Dict[str, Any] = {}
_DOCS_LOCK = threading.Lock()

def _get_docs_sentence_transformer(model_name: str) -> Optional[object]:
    global _DOCS_EMBEDDING_MODELS
    if model_name in _DOCS_EMBEDDING_MODELS:
        return _DOCS_EMBEDDING_MODELS[model_name]
    
    with _DOCS_LOCK:
        if model_name in _DOCS_EMBEDDING_MODELS:
            return _DOCS_EMBEDDING_MODELS[model_name]
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Initializing sentence-transformers docs model: %s...", model_name)
            model = SentenceTransformer(model_name)
            logger.info("Loaded sentence-transformers docs model: %s", model_name)
            _DOCS_EMBEDDING_MODELS[model_name] = model
        except Exception as e:
            logger.error(f"Failed to load embedding model {model_name}: {e}")
            _DOCS_EMBEDDING_MODELS[model_name] = None
    return _DOCS_EMBEDDING_MODELS[model_name]


class DocsEmbeddingProvider:
    """
    Pluggable embedding provider for docs RAG.

    Defaults to a local sentence-transformers model. If embeddings are
    unavailable, callers can fallback to keyword/FTS retrieval.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        ollama_base_url: Optional[str] = None,
    ) -> None:
        self.provider = (provider or os.getenv("STARAGENT_DOCS_EMBED_PROVIDER") or "local").strip().lower()
        self.model_name = model_name or os.getenv("STARAGENT_DOCS_EMBED_MODEL") or "all-MiniLM-L6-v2"
        self.ollama_base_url = (ollama_base_url or os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
        self._model_override = None

        if self.provider == "local":
            if not _HAS_SENTENCE_TRANSFORMERS:
                logger.warning("Docs embeddings disabled: sentence-transformers unavailable")
                self.provider = "disabled"
            else:
                logger.info("Docs embedding provider ready (lazy-load): local (%s)", self.model_name)
        elif self.provider in {"none", "disabled", "keyword"}:
            self.provider = "disabled"
        elif self.provider == "ollama":
            logger.info("Docs embedding provider ready: ollama")
        else:
            logger.warning("Unknown docs embedding provider '%s'; disabling embeddings", self.provider)
            self.provider = "disabled"

    @property
    def _model(self) -> Optional[Any]:
        if self._model_override is not None:
            return self._model_override
        if self.provider == "local":
            return _get_docs_sentence_transformer(self.model_name)
        return None

    @_model.setter
    def _model(self, value: Optional[Any]) -> None:
        self._model_override = value

    @property
    def enabled(self) -> bool:
        return self.provider in {"local", "ollama"}

    def provider_info(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_name if self.provider == "local" else ("nomic-embed-text" if self.provider == "ollama" else None),
            "enabled": self.enabled,
        }

    def embed_text(self, text: str) -> Optional[List[float]]:
        text = (text or "").strip()
        if not text or not self.enabled:
            return None

        if self.provider == "local":
            try:
                emb = self._model.encode(text, convert_to_numpy=True)  # type: ignore[union-attr]
                return emb.tolist()
            except Exception as exc:  # pragma: no cover - model runtime dependent
                logger.warning("Local docs embedding failed: %s", exc)
                return None

        if self.provider == "ollama":
            try:
                import httpx

                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(
                        f"{self.ollama_base_url}/api/embeddings",
                        json={"model": "nomic-embed-text", "prompt": text},
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    emb = payload.get("embedding")
                    if isinstance(emb, list) and emb:
                        return [float(x) for x in emb]
            except Exception as exc:  # pragma: no cover - network/runtime dependent
                logger.warning("Ollama docs embedding failed: %s", exc)
        return None

    @staticmethod
    def decode_embedding(raw: Any) -> Optional[List[float]]:
        if raw is None:
            return None
        if isinstance(raw, list):
            try:
                return [float(x) for x in raw]
            except Exception:
                return None
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, list):
                    return [float(x) for x in decoded]
            except Exception:
                return None
        return None
