import logging
import json
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from .database import get_db, WebSource, WebCache, SearchLog

logger = logging.getLogger(__name__)

class SourceStore:
    """Manages storage and retrieval of web sources and search logs."""
    
    def __init__(self, vector_store=None):
        self.db = get_db()
        self.cache_ttl_hours = int(os.getenv("STARAGENT_WEB_CACHE_TTL_HOURS", "24"))
        self.vector_store = vector_store

    async def save_source(self, project_id: str, url: str, title: str, content: str, metadata: Dict[str, Any] = None) -> int:
        """Save extracted source to database, sync with FTS index AND vector store."""
        session = self.db.get_session()
        try:
            source = WebSource(
                url=url,
                project_id=project_id,
                title=title,
                content=content,
                word_count=len(content.split()),
                metadata_json=json.dumps(metadata or {})
            )
            session.add(source)
            session.flush() # Get the auto-generated ID
            source_id = source.id
            
            # 1. Sync to FTS5
            try:
                session.execute(
                    text("INSERT INTO web_sources_fts(rowid, content) VALUES (:id, :content)"),
                    {"id": source_id, "content": content}
                )
                session.commit()
            except Exception as e:
                logger.warning(f"FTS5 sync failed: {e}")
            
            # 2. Sync to Vector Store (Semantic)
            if self.vector_store:
                try:
                    await self.vector_store.add_document(
                        doc_id=f"src_{source_id}",
                        text=content,
                        metadata={
                            "source_id": source_id,
                            "url": url,
                            "title": title,
                            "project_id": project_id
                        }
                    )
                except Exception as e:
                    logger.warning(f"Vectorization failed for {source_id}: {e}")
                
            return source_id
        finally:
            session.close()

    def get_cached_html(self, url: str) -> Optional[str]:
        """Retrieve HTML from cache if not expired."""
        session = self.db.get_session()
        try:
            cache = session.query(WebCache).filter_by(url=url).first()
            if cache and (not cache.expires_at or cache.expires_at > datetime.utcnow()):
                return cache.raw_html
            return None
        finally:
            session.close()

    def set_cached_html(self, url: str, html: str, status_code: int = 200, content_type: str = "text/html"):
        """Save HTML to cache with expiration."""
        session = self.db.get_session()
        try:
            expires_at = datetime.utcnow() + timedelta(hours=self.cache_ttl_hours)
            cache = WebCache(
                url=url,
                raw_html=html,
                status_code=status_code,
                content_type=content_type,
                expires_at=expires_at
            )
            session.merge(cache) # Overwrite if exists
            session.commit()
        finally:
            session.close()

    def search_sources(self, project_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search local sources using FTS5."""
        session = self.db.get_session()
        try:
            query_sql = text("""
            SELECT s.id, s.url, s.title, s.content, bm25(web_sources_fts) as rank
            FROM web_sources s
            JOIN web_sources_fts f ON s.id = f.rowid
            WHERE s.project_id = :project_id AND web_sources_fts MATCH :query
            ORDER BY rank
            LIMIT :limit
            """)
            # Sanitize FTS5 query
            import re
            clean_query = re.sub(r'[^\w\s]', '', query).strip()
            if not clean_query: clean_query = query
            
            results = session.execute(query_sql, {"project_id": project_id, "query": clean_query, "limit": limit}).fetchall()
            return [
                {
                    "id": r.id,
                    "url": r.url,
                    "title": r.title,
                    "content": r.content,
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"FTS5 search failed: {e}")
            # Fallback to LIKE
            sources = session.query(WebSource).filter(
                WebSource.project_id == project_id,
                WebSource.content.like(f"%{query}%")
            ).limit(limit).all()
            return [{"id": s.id, "url": s.url, "title": s.title, "content": s.content} for s in sources]
        finally:
            session.close()

    async def semantic_search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search local sources using vector embeddings (Semantic)."""
        if not self.vector_store:
            return []
            
        return await self.vector_store.search(query, limit=limit)

    def log_search(self, project_id: str, query: str, engine: str, results: List[Dict[str, Any]]):
        """Audit log for searches."""
        session = self.db.get_session()
        try:
            log = SearchLog(
                id=str(uuid.uuid4()),
                query=query,
                project_id=project_id,
                engine=engine,
                results_json=json.dumps(results)
            )
            session.add(log)
            session.commit()
        finally:
            session.close()

import os
