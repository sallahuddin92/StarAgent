from __future__ import annotations

import json
import logging
import math
import uuid
import hashlib
from typing import Dict, Any, List, Optional

from sqlalchemy import text

from .database import DatabaseManager, get_db, DocsSource, DocsChunk, DocsSearchLog
from .docs_embeddings import DocsEmbeddingProvider

logger = logging.getLogger(__name__)


class DocsStore:
    """
    Project-scoped documentation storage and retrieval.

    Supports:
    - source/chunk persistence in SQLite
    - metadata-aware chunk storage (path/page/section/chunk index)
    - FTS keyword search fallback
    - optional local embeddings with vector reranking
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        embedding_provider: Optional[DocsEmbeddingProvider] = None,
    ):
        self.db = db_manager or get_db()
        self.embedding_provider = embedding_provider or DocsEmbeddingProvider()

    def _compute_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def add_source(
        self,
        project_id: str,
        source_type: str,
        title: str,
        path_or_url: str,
        package_name: Optional[str] = None,
        version: Optional[str] = None,
    ) -> str:
        """Add a documentation source, returning its UUID."""
        session = self.db.get_session()
        try:
            existing = (
                session.query(DocsSource)
                .filter_by(project_id=project_id, path_or_url=path_or_url)
                .first()
            )
            if existing:
                return existing.source_id

            source_id = str(uuid.uuid4())
            session.add(
                DocsSource(
                    source_id=source_id,
                    project_id=project_id,
                    source_type=source_type,
                    package_name=package_name,
                    version=version,
                    title=title,
                    path_or_url=path_or_url,
                )
            )
            session.commit()
            return source_id
        except Exception as exc:
            session.rollback()
            logger.error("Failed to add DocsSource: %s", exc)
            raise
        finally:
            session.close()

    def _resolve_project_id_from_source(self, source_id: str) -> str:
        session = self.db.get_session()
        try:
            src = session.query(DocsSource).filter_by(source_id=source_id).first()
            return src.project_id if src else "default"
        finally:
            session.close()

    def add_chunk(
        self,
        source_id: str,
        content: str,
        heading: Optional[str] = None,
        code_examples: Optional[str] = None,
        *,
        chunk_index: int = 0,
        source_path: Optional[str] = None,
        page_ref: Optional[str] = None,
        section_ref: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Add a chunk and keep FTS index in sync. Returns False if duplicate."""
        content_hash = self._compute_hash(content)
        session = self.db.get_session()
        try:
            existing = (
                session.query(DocsChunk)
                .filter_by(source_id=source_id, content_hash=content_hash)
                .first()
            )
            if existing:
                return False

            project_id = self._resolve_project_id_from_source(source_id)
            embedding = self.embedding_provider.embed_text(content)

            chunk_id = str(uuid.uuid4())
            session.add(
                DocsChunk(
                    chunk_id=chunk_id,
                    project_id=project_id,
                    source_id=source_id,
                    chunk_index=int(chunk_index),
                    source_path=source_path,
                    page_ref=page_ref,
                    section_ref=section_ref,
                    heading=heading,
                    content=content,
                    code_examples=code_examples,
                    content_hash=content_hash,
                    embedding=json.dumps(embedding) if embedding else None,
                    metadata_json=json.dumps(metadata or {}),
                )
            )

            try:
                session.execute(
                    text(
                        "INSERT INTO docs_chunks_fts (chunk_id, heading, content, code_examples) "
                        "VALUES (:cid, :hdg, :cnt, :ce)"
                    ),
                    {
                        "cid": chunk_id,
                        "hdg": heading or "",
                        "cnt": content,
                        "ce": code_examples or "",
                    },
                )
            except Exception as fts_err:
                logger.warning("Failed to sync docs_chunks_fts: %s", fts_err)

            session.commit()
            return True
        except Exception as exc:
            session.rollback()
            logger.error("Failed to add DocsChunk: %s", exc)
            raise
        finally:
            session.close()

    @staticmethod
    def _cosine_similarity(a: Optional[List[float]], b: Optional[List[float]]) -> Optional[float]:
        if not a or not b or len(a) != len(b):
            return None
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return None
        return dot / (na * nb)

    def _fts_candidates(
        self,
        project_id: str,
        query: str,
        package_name: Optional[str] = None,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        session = self.db.get_session()
        try:
            sanitized = query.replace('"', "").replace("'", "").strip()
            if not sanitized:
                return []

            fts_query = " OR ".join(sanitized.split())
            sql = """
                SELECT
                    c.chunk_id,
                    c.chunk_index,
                    c.source_path,
                    c.page_ref,
                    c.section_ref,
                    c.heading,
                    c.content,
                    c.code_examples,
                    c.embedding,
                    c.metadata_json,
                    s.title,
                    s.path_or_url,
                    s.source_type,
                    s.package_name,
                    bm25(docs_chunks_fts) AS fts_score
                FROM docs_chunks_fts f
                JOIN docs_chunks c ON f.chunk_id = c.chunk_id
                JOIN docs_sources s ON c.source_id = s.source_id
                WHERE docs_chunks_fts MATCH :query
                  AND s.project_id = :pid
            """
            params: Dict[str, Any] = {"query": fts_query, "pid": project_id, "limit": max_results * 4}
            if package_name:
                sql += " AND s.package_name = :pkg"
                params["pkg"] = package_name

            sql += " ORDER BY fts_score LIMIT :limit"
            rows = session.execute(text(sql), params).fetchall()

            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "chunk_id": r[0],
                        "chunk_index": int(r[1] or 0),
                        "source_path": r[2],
                        "page_ref": r[3],
                        "section_ref": r[4],
                        "heading": r[5],
                        "content": r[6],
                        "code_examples": r[7],
                        "embedding": r[8],
                        "metadata_json": r[9],
                        "title": r[10],
                        "path_or_url": r[11],
                        "source_type": r[12],
                        "package_name": r[13],
                        "fts_score": float(r[14]) if r[14] is not None else None,
                    }
                )
            return out
        except Exception as exc:
            logger.error("FTS candidate search failed: %s", exc)
            return self._like_candidates(session, project_id, query, package_name=package_name, max_results=max_results * 4)
        finally:
            session.close()

    def _like_candidates(
        self,
        session,
        project_id: str,
        query: str,
        package_name: Optional[str] = None,
        max_results: int = 20,
    ) -> List[Dict[str, Any]]:
        """Fallback search for environments without SQLite FTS5 support."""
        terms = [t.strip() for t in query.split() if t.strip()]
        if not terms:
            return []

        clauses = []
        params: Dict[str, Any] = {"pid": project_id, "limit": max_results}
        for i, t in enumerate(terms[:8]):
            key = f"q{i}"
            clauses.append(f"(c.content LIKE :{key} OR c.heading LIKE :{key} OR c.code_examples LIKE :{key})")
            params[key] = f"%{t}%"

        sql = f"""
            SELECT
                c.chunk_id,
                c.chunk_index,
                c.source_path,
                c.page_ref,
                c.section_ref,
                c.heading,
                c.content,
                c.code_examples,
                c.embedding,
                c.metadata_json,
                s.title,
                s.path_or_url,
                s.source_type,
                s.package_name
            FROM docs_chunks c
            JOIN docs_sources s ON c.source_id = s.source_id
            WHERE s.project_id = :pid
              AND ({' OR '.join(clauses)})
        """
        if package_name:
            sql += " AND s.package_name = :pkg"
            params["pkg"] = package_name
        sql += " ORDER BY c.created_at DESC LIMIT :limit"

        rows = session.execute(text(sql), params).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "chunk_id": r[0],
                    "chunk_index": int(r[1] or 0),
                    "source_path": r[2],
                    "page_ref": r[3],
                    "section_ref": r[4],
                    "heading": r[5],
                    "content": r[6],
                    "code_examples": r[7],
                    "embedding": r[8],
                    "metadata_json": r[9],
                    "title": r[10],
                    "path_or_url": r[11],
                    "source_type": r[12],
                    "package_name": r[13],
                    "fts_score": None,
                }
            )
        return out

    def search(
        self,
        project_id: str,
        query: str,
        package_name: Optional[str] = None,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """Hybrid docs search: FTS + embedding rerank (if available)."""
        candidates = self._fts_candidates(project_id, query, package_name=package_name, max_results=max_results)
        if not candidates:
            return []

        query_embedding = self.embedding_provider.embed_text(query)
        enriched: List[Dict[str, Any]] = []

        # Convert FTS score (lower bm25 is better) to positive relevance score.
        fts_values = [abs(float(c["fts_score"])) for c in candidates if c.get("fts_score") is not None]
        max_fts = max(fts_values) if fts_values else 1.0

        for c in candidates:
            emb = DocsEmbeddingProvider.decode_embedding(c.get("embedding"))
            cos = self._cosine_similarity(query_embedding, emb) if query_embedding else None
            vector_score = ((cos + 1.0) / 2.0) if cos is not None else None

            fts_score_raw = abs(float(c.get("fts_score") or 0.0))
            # smaller bm25 => better, normalize into [0,1]
            fts_relevance = 1.0 - min(fts_score_raw / (max_fts or 1.0), 1.0)

            if vector_score is None:
                combined = fts_relevance
            else:
                combined = (0.55 * vector_score) + (0.45 * fts_relevance)

            metadata = {}
            raw_metadata = c.get("metadata_json")
            if isinstance(raw_metadata, str) and raw_metadata:
                try:
                    metadata = json.loads(raw_metadata)
                except Exception:
                    metadata = {}

            enriched.append(
                {
                    "chunk_id": c["chunk_id"],
                    "chunk_index": c.get("chunk_index", 0),
                    "source_path": c.get("source_path"),
                    "page_ref": c.get("page_ref"),
                    "section_ref": c.get("section_ref"),
                    "heading": c.get("heading"),
                    "content": c.get("content"),
                    "code_examples": c.get("code_examples"),
                    "title": c.get("title"),
                    "path_or_url": c.get("path_or_url"),
                    "source_type": c.get("source_type"),
                    "package_name": c.get("package_name"),
                    "metadata": metadata,
                    "scores": {
                        "fts": round(fts_relevance, 6),
                        "vector": round(vector_score, 6) if vector_score is not None else None,
                        "combined": round(combined, 6),
                    },
                }
            )

        enriched.sort(key=lambda x: x["scores"]["combined"], reverse=True)
        return enriched[: max(1, int(max_results))]

    def log_search(
        self,
        project_id: str,
        query: str,
        is_error_lookup: bool,
        error_message: str,
        results: List[Dict[str, Any]],
    ) -> None:
        session = self.db.get_session()
        try:
            results_summary = [
                {
                    "chunk_id": r.get("chunk_id"),
                    "title": r.get("title"),
                    "source_path": r.get("source_path") or r.get("path_or_url"),
                }
                for r in results
            ]
            session.add(
                DocsSearchLog(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    query=query,
                    is_error_lookup=1 if is_error_lookup else 0,
                    error_message=error_message,
                    results_json=json.dumps(results_summary),
                )
            )
            session.commit()
        except Exception as exc:
            logger.warning("Failed to log docs search: %s", exc)
        finally:
            session.close()
