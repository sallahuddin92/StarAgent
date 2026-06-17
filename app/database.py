"""
SQLite-based conversation memory storage with semantic search support.
Handles conversation state, memory items with embeddings, and project isolation.
"""

import json
import os
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, Float, ForeignKey, Index, ForeignKeyConstraint
from sqlalchemy.orm import declarative_base, Session, relationship

logger = logging.getLogger(__name__)

Base = declarative_base()


class Conversation(Base):
    """Represents a single conversation within a project."""
    __tablename__ = "conversations"
    
    id = Column(String, primary_key=True)  # conversation_id
    project_id = Column(String, primary_key=True)  # Composite PK with id
    user = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # JSON-serialized memory state
    project_summary = Column(Text, default="[]")  # List[str]
    decisions = Column(Text, default="[]")
    constraints = Column(Text, default="[]")
    issues = Column(Text, default="[]")
    style_preferences = Column(Text, default="[]")

    # Agent-path pending resume state (durable; JSON-serialized where structured)
    pending_approval = Column(Text, nullable=True)  # Dict[str, Any] as JSON
    pending_plan = Column(Text, nullable=True)  # List[str] as JSON
    pending_history = Column(Text, nullable=True)  # List[Dict[str, Any]] as JSON
    pending_goal = Column(Text, nullable=True)  # str
    force_research_flag = Column(Integer, default=0)  # bool as 0/1

    # Tracking
    turn_count = Column(Integer, default=0)
    last_compaction = Column(DateTime, nullable=True)
    
    # Relationships
    memory_items = relationship("MemoryItem", back_populates="conversation", cascade="all, delete-orphan")
    archive_turns = relationship("ArchiveTurn", back_populates="conversation", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_project_id', 'project_id'),
    )


class MemoryItem(Base):
    """Individual memory item with embedding for semantic search."""
    __tablename__ = "memory_items"
    
    id = Column(String, primary_key=True)
    conversation_id = Column(String, nullable=False, index=True)
    project_id = Column(String, nullable=False, index=True)
    category = Column(String, nullable=False)  # "summary", "decision", "constraint", "issue", "style"
    content = Column(Text, nullable=False)
    embedding = Column(Text, nullable=True)  # JSON-serialized list of floats
    created_at = Column(DateTime, default=datetime.utcnow)
    relevance_score = Column(Float, default=1.0)
    
    # Relationships
    conversation = relationship(
        "Conversation",
        back_populates="memory_items",
        foreign_keys=[conversation_id, project_id]
    )
    
    __table_args__ = (
        ForeignKeyConstraint(['conversation_id', 'project_id'], ['conversations.id', 'conversations.project_id']),
        Index('idx_memory_items_conv_id', 'conversation_id', 'project_id'),
        Index('idx_memory_items_category', 'category'),
    )


class ArchiveTurn(Base):
    """Historical conversation turns for context window management."""
    __tablename__ = "archive_turns"
    
    id = Column(String, primary_key=True)
    conversation_id = Column(String, nullable=False, index=True)
    project_id = Column(String, nullable=False, index=True)
    turn_number = Column(Integer, nullable=False)
    user_message = Column(Text, nullable=False)
    assistant_message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    conversation = relationship(
        "Conversation",
        back_populates="archive_turns",
        foreign_keys=[conversation_id, project_id]
    )
    
    __table_args__ = (
        ForeignKeyConstraint(['conversation_id', 'project_id'], ['conversations.id', 'conversations.project_id']),
        Index('idx_archive_turns_conv_id', 'conversation_id', 'project_id'),
        Index('idx_archive_turns_turn_num', 'conversation_id', 'project_id', 'turn_number'),
    )


class TaskRun(Base):
    """
    Durable iterative task execution state.

    Generic enough to represent both iterative agent tasks and document research runs.
    """
    __tablename__ = "task_runs"

    task_id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    conversation_id = Column(String, nullable=False, index=True)
    task_type = Column(String, nullable=False, index=True)  # e.g. "agent", "research"
    user_goal = Column(Text, nullable=False)
    definition_of_done = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending, running, paused, completed, failed, partial
    current_step_index = Column(Integer, default=0)
    max_steps = Column(Integer, default=25)
    max_retries = Column(Integer, default=2)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    final_summary = Column(Text, nullable=True)
    final_verdict = Column(String, nullable=True)
    artifacts_json = Column(Text, default="{}")  # JSON dict with artifact paths/metadata

    steps = relationship("TaskStep", back_populates="task_run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_task_runs_scope", "project_id", "conversation_id", "created_at"),
        Index("idx_task_runs_status", "status"),
    )


class TaskStep(Base):
    """A single step within a task run."""
    __tablename__ = "task_steps"

    step_id = Column(String, primary_key=True)
    task_id = Column(String, ForeignKey("task_runs.task_id"), nullable=False, index=True)
    step_index = Column(Integer, nullable=False, index=True)
    step_type = Column(String, nullable=False, default="generic")  # e.g. "tool", "research_file", "synthesis"
    instruction = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending, running, paused, completed, failed
    attempt_count = Column(Integer, default=0)
    output_summary = Column(Text, nullable=True)
    verifier_result = Column(Text, nullable=True)  # JSON or text summary of verification outcome
    artifact_path = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    task_run = relationship("TaskRun", back_populates="steps")

    __table_args__ = (
        Index("idx_task_steps_task_index", "task_id", "step_index"),
        Index("idx_task_steps_status", "task_id", "status"),
    )


class WebSource(Base):
    """Extracted text and metadata from a web search result."""
    __tablename__ = "web_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String, nullable=False, index=True)
    project_id = Column(String, nullable=False, index=True)
    title = Column(String)
    content = Column(Text, nullable=False)
    summary = Column(Text)
    word_count = Column(Integer)
    embedding = Column(Text)  # JSON-serialized vector
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_web_sources_project", "project_id", "url"),
    )


class WebCache(Base):
    """Cache for raw HTML content."""
    __tablename__ = "web_cache"

    url = Column(String, primary_key=True)
    raw_html = Column(Text)
    status_code = Column(Integer)
    content_type = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)


class SearchLog(Base):
    """Audit log for all web searches."""
    __tablename__ = "search_logs"

    id = Column(String, primary_key=True)
    query = Column(String, nullable=False)
    project_id = Column(String, nullable=False, index=True)
    engine = Column(String)
    results_json = Column(Text)  # List of {title, url, snippet}
    created_at = Column(DateTime, default=datetime.utcnow)


# =========================================================================
# Local Code Documentation Knowledge Base Models
# =========================================================================

class DocsSource(Base):
    """A source of documentation (e.g., a local file, a package, or an official web URL)."""
    __tablename__ = "docs_sources"
    
    source_id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    source_type = Column(String, nullable=False) # devdocs | local_docs | package_source | official_web | project_docs
    package_name = Column(String, nullable=True)
    version = Column(String, nullable=True)
    title = Column(String, nullable=False)
    path_or_url = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_docs_sources_project", "project_id", "source_type"),
    )


class DocsChunk(Base):
    """A chunk of documentation text, broken down for searchability."""
    __tablename__ = "docs_chunks"
    
    chunk_id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    source_id = Column(String, ForeignKey("docs_sources.source_id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False, default=0)
    source_path = Column(String, nullable=True)
    page_ref = Column(String, nullable=True)
    section_ref = Column(String, nullable=True)
    heading = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    code_examples = Column(Text, nullable=True) # Extracted code blocks
    content_hash = Column(String, nullable=False, index=True) # To avoid duplicate ingestion
    embedding = Column(Text, nullable=True) # JSON-serialized vector (optional)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    source = relationship("DocsSource", backref="chunks")


class DocsSearchLog(Base):
    """Audit log for offline docs searches."""
    __tablename__ = "docs_search_logs"
    
    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    query = Column(String, nullable=False)
    is_error_lookup = Column(Integer, default=0) # 1 if triggered by verification failure
    error_message = Column(Text, nullable=True)
    results_json = Column(Text) # Resulting chunk IDs and titles
    created_at = Column(DateTime, default=datetime.utcnow)


from functools import wraps

def self_healing_retry(func):
    """Decorator to retry DatabaseManager operations on OperationalError (missing tables/columns or unlinked files)."""
    import asyncio
    from sqlalchemy.exc import OperationalError

    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(self, *args, **kwargs):
            try:
                return await func(self, *args, **kwargs)
            except OperationalError as e:
                err_str = str(e).lower()
                if "no such table" in err_str or "no such column" in err_str or "disk i/o error" in err_str:
                    logger.warning(f"[DATABASE_RETRY] Table/column missing or disk I/O error, attempting self-healing: {e}")
                    try:
                        self.engine.dispose()
                        # Re-create tables
                        Base.metadata.create_all(self.engine)
                        self._ensure_pending_columns()
                    except Exception as re_err:
                        logger.error(f"[DATABASE_RETRY] Failed to recreate tables: {re_err}")
                    return await func(self, *args, **kwargs)
                raise
        return async_wrapper
    else:
        @wraps(func)
        def sync_wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except OperationalError as e:
                err_str = str(e).lower()
                if "no such table" in err_str or "no such column" in err_str or "disk i/o error" in err_str:
                    logger.warning(f"[DATABASE_RETRY] Table/column missing or disk I/O error, attempting self-healing: {e}")
                    try:
                        self.engine.dispose()
                        # Re-create tables
                        Base.metadata.create_all(self.engine)
                        self._ensure_pending_columns()
                    except Exception as re_err:
                        logger.error(f"[DATABASE_RETRY] Failed to recreate tables: {re_err}")
                    return func(self, *args, **kwargs)
                raise
        return sync_wrapper


class DatabaseManager:
    """Manages SQLite database operations for conversation memory."""
    
    def __init__(self, db_path: str = "./data/memory.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        # SQLite configuration with retry and timeout handling
        from sqlalchemy import event
        from sqlalchemy.pool import QueuePool
        from sqlalchemy.orm import sessionmaker
        import time
        
        # Use QueuePool instead of StaticPool for better connection management
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={
                "check_same_thread": False,
                "timeout": 15.0  # 15 second timeout for locks
            },
            poolclass=QueuePool,
            pool_size=1,  # SQLite works best with single connection
            max_overflow=5,  # Allow up to 5 overflow connections
            pool_pre_ping=True,
            pool_recycle=1800,  # Recycle every 30 minutes
            echo=False
        )
        
        # Enable WAL mode for better concurrency support
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            try:
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
                cursor.execute("PRAGMA busy_timeout=15000")  # 15 second timeout
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
            except Exception as e:
                logger.warning(f"Failed to set SQLite pragmas: {e}")
        
        # Create session maker with expire_on_commit=False to avoid DetachedInstanceError
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,  # Keep objects valid after commit
            autoflush=False,  # More explicit control
            autocommit=False
        )
        
        # Create tables
        Base.metadata.create_all(self.engine)
        self._ensure_pending_columns()
        logger.info(f"Database initialized at {db_path} with QueuePool and WAL mode")

    def _ensure_pending_columns(self) -> None:
        """
        Minimal in-place migration to add pending_* columns to the existing
        conversations table (we don't use Alembic in this repo).

        Important: SQLAlchemy ORM will select these columns once declared, so
        we must ensure they exist before any queries occur.
        """
        try:
            with self.engine.connect() as conn:
                rows = conn.exec_driver_sql("PRAGMA table_info(conversations)").fetchall()
                cols = {r[1] for r in rows}  # (cid, name, type, notnull, dflt_value, pk)

                alters = []
                if "pending_approval" not in cols:
                    alters.append("ALTER TABLE conversations ADD COLUMN pending_approval TEXT")
                if "pending_plan" not in cols:
                    alters.append("ALTER TABLE conversations ADD COLUMN pending_plan TEXT")
                if "pending_history" not in cols:
                    alters.append("ALTER TABLE conversations ADD COLUMN pending_history TEXT")
                if "pending_goal" not in cols:
                    alters.append("ALTER TABLE conversations ADD COLUMN pending_goal TEXT")
                if "force_research_flag" not in cols:
                    alters.append("ALTER TABLE conversations ADD COLUMN force_research_flag INTEGER DEFAULT 0")

                for stmt in alters:
                    conn.exec_driver_sql(stmt)

                # Docs chunk metadata migration for project-aware RAG.
                try:
                    docs_rows = conn.exec_driver_sql("PRAGMA table_info(docs_chunks)").fetchall()
                    docs_cols = {r[1] for r in docs_rows}
                    docs_alters = []
                    if "project_id" not in docs_cols:
                        docs_alters.append("ALTER TABLE docs_chunks ADD COLUMN project_id TEXT")
                    if "chunk_index" not in docs_cols:
                        docs_alters.append("ALTER TABLE docs_chunks ADD COLUMN chunk_index INTEGER DEFAULT 0")
                    if "source_path" not in docs_cols:
                        docs_alters.append("ALTER TABLE docs_chunks ADD COLUMN source_path TEXT")
                    if "page_ref" not in docs_cols:
                        docs_alters.append("ALTER TABLE docs_chunks ADD COLUMN page_ref TEXT")
                    if "section_ref" not in docs_cols:
                        docs_alters.append("ALTER TABLE docs_chunks ADD COLUMN section_ref TEXT")
                    if "metadata_json" not in docs_cols:
                        docs_alters.append("ALTER TABLE docs_chunks ADD COLUMN metadata_json TEXT")
                    for stmt in docs_alters:
                        conn.exec_driver_sql(stmt)

                    # Backfill project_id for older rows when available through source join.
                    conn.exec_driver_sql(
                        """
                        UPDATE docs_chunks
                        SET project_id = (
                            SELECT s.project_id
                            FROM docs_sources s
                            WHERE s.source_id = docs_chunks.source_id
                        )
                        WHERE project_id IS NULL OR project_id = ''
                        """
                    )
                except Exception as de:
                    logger.warning(f"Docs chunk schema migration skipped/failed: {de}")
                
                # Ensure FTS5 Virtual Table for content search if available
                try:
                    conn.exec_driver_sql("CREATE VIRTUAL TABLE IF NOT EXISTS web_sources_fts USING fts5(content, content='web_sources', content_rowid='id')")
                    conn.exec_driver_sql("CREATE VIRTUAL TABLE IF NOT EXISTS docs_chunks_fts USING fts5(chunk_id UNINDEXED, heading, content, code_examples)")
                    # Simple trigger-based sync can be complex in SQLite migrations; we will handle it in source_store.py and docs_store.py
                except Exception as fe:
                    logger.warning(f"FTS5 initialization failed (unsupported sqlite?): {fe}")

                if alters:
                    conn.commit()
                    logger.info(f"Added pending_* columns to conversations: {len(alters)}")
        except Exception as e:
            logger.warning(f"Pending-state schema migration skipped/failed: {e}")
    
    def get_session(self):
        """Get a new database session."""
        return self.SessionLocal()
    
    @self_healing_retry
    def get_or_create_conversation(
        self,
        conversation_id: str,
        project_id: str,
        user: Optional[str] = None
    ) -> Conversation:
        """Get existing conversation or create new one."""
        session = self.get_session()
        try:
            conv = session.query(Conversation).filter_by(
                id=conversation_id,
                project_id=project_id
            ).first()
            
            if not conv:
                conv = Conversation(
                    id=conversation_id,
                    project_id=project_id,
                    user=user
                )
                session.add(conv)
                session.commit()
                logger.info(f"Created conversation {conversation_id} in project {project_id}")
                # Force load all attributes before session closes (expire_on_commit=False will keep them)
                _ = conv.id
            
            return conv
        finally:
            session.close()
    
    @self_healing_retry
    def get_conversation(self, conversation_id: str, project_id: str) -> Optional[Conversation]:
        """Retrieve conversation by ID and project."""
        session = self.get_session()
        try:
            conv = session.query(Conversation).filter_by(
                id=conversation_id,
                project_id=project_id
            ).first()
            if conv:
                # Force load all attributes before session closes
                _ = conv.id
            return conv
        finally:
            session.close()
    
    @self_healing_retry
    def save_memory_state(
        self,
        conversation_id: str,
        project_id: str,
        state: Dict[str, Any]
    ) -> None:
        """Save memory state (project_summary, decisions, etc.), including pending agent resume state."""
        session = self.get_session()
        try:
            conv = self.get_or_create_conversation(conversation_id, project_id)
            
            conv.project_summary = json.dumps(state.get("project_summary", [])[:20])
            conv.decisions = json.dumps(state.get("decisions", [])[:20])
            conv.constraints = json.dumps(state.get("constraints", [])[:20])
            conv.issues = json.dumps(state.get("issues", [])[:20])
            conv.style_preferences = json.dumps(state.get("style_preferences", [])[:20])
            conv.turn_count = state.get("turn_count", 0)
            conv.updated_at = datetime.utcnow()

            # Durable pending state (canonical)
            pending_approval = state.get("pending_approval")
            pending_plan = state.get("pending_plan")
            pending_history = state.get("pending_history")
            pending_goal = state.get("pending_goal")

            conv.pending_approval = json.dumps(pending_approval) if pending_approval is not None else None
            conv.pending_plan = json.dumps(pending_plan) if pending_plan is not None else None
            conv.pending_history = json.dumps(pending_history) if pending_history is not None else None
            conv.pending_goal = pending_goal if pending_goal is not None else None
            conv.force_research_flag = 1 if state.get("force_research_flag", False) else 0
            
            session.add(conv)
            session.commit()
        finally:
            session.close()
    
    @self_healing_retry
    def get_memory_state(self, conversation_id: str, project_id: str) -> Dict[str, Any]:
        """Retrieve memory state for a conversation."""
        session = self.get_session()
        try:
            conv = self.get_conversation(conversation_id, project_id)
            if not conv:
                return {
                    "project_summary": [],
                    "decisions": [],
                    "constraints": [],
                    "issues": [],
                    "style_preferences": [],
                    "turn_count": 0,
                    "pending_approval": None,
                    "pending_plan": None,
                    "pending_history": None,
                    "pending_goal": None,
                    "force_research_flag": False,
                }
            
            # Fetch recent archive turns separately
            turns = session.query(ArchiveTurn).filter_by(
                conversation_id=conversation_id,
                project_id=project_id
            ).order_by(ArchiveTurn.turn_number.asc()).limit(30).all()
            
            archive_turns = []
            for t in turns:
                archive_turns.append({
                    "user": t.user_message,
                    "assistant": t.assistant_message
                })

            return {
                "project_summary": json.loads(conv.project_summary or "[]"),
                "decisions": json.loads(conv.decisions or "[]"),
                "constraints": json.loads(conv.constraints or "[]"),
                "issues": json.loads(conv.issues or "[]"),
                "style_preferences": json.loads(conv.style_preferences or "[]"),
                "archive_turns": archive_turns,
                "turn_count": conv.turn_count,
                "pending_approval": json.loads(conv.pending_approval) if getattr(conv, "pending_approval", None) else None,
                "pending_plan": json.loads(conv.pending_plan) if getattr(conv, "pending_plan", None) else None,
                "pending_history": json.loads(conv.pending_history) if getattr(conv, "pending_history", None) else None,
                "pending_goal": getattr(conv, "pending_goal", None),
                "force_research_flag": bool(getattr(conv, "force_research_flag", 0)),
            }
        finally:
            session.close()

    # =========================================================================
    # Task Runs / Steps (Iterative Task Engine)
    # =========================================================================

    @self_healing_retry
    def create_task_run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Create and persist a new TaskRun."""
        import uuid
        session = self.get_session()
        try:
            task_id = task.get("task_id") or str(uuid.uuid4())
            tr = TaskRun(
                task_id=task_id,
                project_id=task["project_id"],
                conversation_id=task["conversation_id"],
                task_type=task.get("task_type", "agent"),
                user_goal=task["user_goal"],
                definition_of_done=task.get("definition_of_done"),
                status=task.get("status", "pending"),
                current_step_index=int(task.get("current_step_index", 0)),
                max_steps=int(task.get("max_steps", 25)),
                max_retries=int(task.get("max_retries", 2)),
                retry_count=int(task.get("retry_count", 0)),
                final_summary=task.get("final_summary"),
                final_verdict=task.get("final_verdict"),
                artifacts_json=json.dumps(task.get("artifacts_json") or {}),
            )
            session.add(tr)
            session.commit()
            out = self.get_task_run(task_id)
            return out or {}
        finally:
            session.close()

    @self_healing_retry
    def get_task_run(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a task run and normalize to a dict."""
        session = self.get_session()
        try:
            tr = session.query(TaskRun).filter_by(task_id=task_id).first()
            if not tr:
                return None
            _ = tr.task_id
            return {
                "task_id": tr.task_id,
                "project_id": tr.project_id,
                "conversation_id": tr.conversation_id,
                "task_type": tr.task_type,
                "user_goal": tr.user_goal,
                "definition_of_done": tr.definition_of_done,
                "status": tr.status,
                "current_step_index": tr.current_step_index,
                "max_steps": tr.max_steps,
                "max_retries": tr.max_retries,
                "retry_count": tr.retry_count,
                "created_at": tr.created_at.isoformat() if tr.created_at else None,
                "updated_at": tr.updated_at.isoformat() if tr.updated_at else None,
                "final_summary": tr.final_summary,
                "final_verdict": tr.final_verdict,
                "artifacts_json": json.loads(tr.artifacts_json or "{}"),
            }
        finally:
            session.close()

    @self_healing_retry
    def update_task_run(self, task_id: str, patch: Dict[str, Any]) -> None:
        """Update a task run with a partial patch."""
        session = self.get_session()
        try:
            tr = session.query(TaskRun).filter_by(task_id=task_id).first()
            if not tr:
                return
            for k, v in patch.items():
                if k == "artifacts_json" and isinstance(v, (dict, list)):
                    setattr(tr, k, json.dumps(v))
                elif hasattr(tr, k):
                    setattr(tr, k, v)
            tr.updated_at = datetime.utcnow()
            session.add(tr)
            session.commit()
        finally:
            session.close()

    @self_healing_retry
    def list_task_steps(self, task_id: str) -> List[Dict[str, Any]]:
        """List steps for a task in index order."""
        session = self.get_session()
        try:
            steps = (
                session.query(TaskStep)
                .filter_by(task_id=task_id)
                .order_by(TaskStep.step_index.asc())
                .all()
            )
            out: List[Dict[str, Any]] = []
            for s in steps:
                out.append(
                    {
                        "step_id": s.step_id,
                        "task_id": s.task_id,
                        "step_index": s.step_index,
                        "step_type": s.step_type,
                        "instruction": s.instruction,
                        "status": s.status,
                        "attempt_count": s.attempt_count,
                        "output_summary": s.output_summary,
                        "verifier_result": s.verifier_result,
                        "artifact_path": s.artifact_path,
                        "created_at": s.created_at.isoformat() if s.created_at else None,
                        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                    }
                )
            return out
        finally:
            session.close()

    @self_healing_retry
    def create_task_steps(self, task_id: str, steps: List[Dict[str, Any]]) -> None:
        """Bulk insert steps for a task."""
        import uuid
        session = self.get_session()
        try:
            for st in steps:
                session.add(
                    TaskStep(
                        step_id=st.get("step_id") or str(uuid.uuid4()),
                        task_id=task_id,
                        step_index=int(st["step_index"]),
                        step_type=st.get("step_type", "generic"),
                        instruction=st["instruction"],
                        status=st.get("status", "pending"),
                        attempt_count=int(st.get("attempt_count", 0)),
                        output_summary=st.get("output_summary"),
                        verifier_result=st.get("verifier_result"),
                        artifact_path=st.get("artifact_path"),
                    )
                )
            session.commit()
        finally:
            session.close()

    @self_healing_retry
    def update_task_step(self, step_id: str, patch: Dict[str, Any]) -> None:
        """Update a task step with a partial patch."""
        session = self.get_session()
        try:
            st = session.query(TaskStep).filter_by(step_id=step_id).first()
            if not st:
                return
            for k, v in patch.items():
                if hasattr(st, k):
                    setattr(st, k, v)
            st.updated_at = datetime.utcnow()
            session.add(st)
            session.commit()
        finally:
            session.close()

    @self_healing_retry
    def list_task_runs(
        self,
        *,
        project_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "updated_desc",
    ) -> List[Dict[str, Any]]:
        """
        List task runs with optional scoping filters.

        This is used for operator observability (CLI/API) and must remain
        lightweight (no joins to steps).
        """
        session = self.get_session()
        try:
            q = session.query(TaskRun)
            if project_id:
                q = q.filter(TaskRun.project_id == project_id)
            if conversation_id:
                q = q.filter(TaskRun.conversation_id == conversation_id)
            if status:
                q = q.filter(TaskRun.status == status)

            if order == "created_asc":
                q = q.order_by(TaskRun.created_at.asc())
            elif order == "created_desc":
                q = q.order_by(TaskRun.created_at.desc())
            elif order == "updated_asc":
                q = q.order_by(TaskRun.updated_at.asc())
            else:
                q = q.order_by(TaskRun.updated_at.desc())

            q = q.offset(max(0, int(offset or 0))).limit(max(1, min(int(limit or 50), 500)))

            out: List[Dict[str, Any]] = []
            for tr in q.all():
                out.append(
                    {
                        "task_id": tr.task_id,
                        "project_id": tr.project_id,
                        "conversation_id": tr.conversation_id,
                        "task_type": tr.task_type,
                        "user_goal": tr.user_goal,
                        "definition_of_done": tr.definition_of_done,
                        "status": tr.status,
                        "current_step_index": tr.current_step_index,
                        "max_steps": tr.max_steps,
                        "max_retries": tr.max_retries,
                        "retry_count": tr.retry_count,
                        "created_at": tr.created_at.isoformat() if tr.created_at else None,
                        "updated_at": tr.updated_at.isoformat() if tr.updated_at else None,
                        "final_summary": tr.final_summary,
                        "final_verdict": tr.final_verdict,
                        "artifacts_json": json.loads(tr.artifacts_json or "{}"),
                    }
                )
            return out
        finally:
            session.close()
    
    @self_healing_retry
    def add_memory_item(
        self,
        conversation_id: str,
        project_id: str,
        category: str,
        content: str,
        embedding: Optional[List[float]] = None,
        *args,
        **kwargs
    ) -> MemoryItem:
        """Add a memory item with optional embedding."""
        import uuid
        session = self.get_session()
        try:
            conv = self.get_or_create_conversation(conversation_id, project_id)
            
            item = MemoryItem(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                project_id=project_id,
                category=category,
                content=content,
                embedding=json.dumps(embedding) if embedding else None
            )
            session.add(item)
            session.commit()
            return item
        finally:
            session.close()
    
    @self_healing_retry
    def get_memory_items(
        self,
        conversation_id: Optional[str],
        project_id: str,
        category: Optional[str] = None,
        include_global: bool = False
    ) -> List[MemoryItem]:
        """
        Retrieve memory items for a conversation or project.
        
        Args:
            conversation_id: Specific conversation to target.
            project_id: Project scope.
            category: Filter by category (e.g. 'decision').
            include_global: If True and conversation_id is provided, also includes 
                            project-level items that aren't tied to a specific chat.
        """
        session = self.get_session()
        try:
            if conversation_id:
                if include_global:
                    # Fetch items for this conversation OR items in this project with NO conversation_id (Global)
                    from sqlalchemy import or_
                    query = session.query(MemoryItem).filter(
                        MemoryItem.project_id == project_id
                    ).filter(
                        or_(
                            MemoryItem.conversation_id == conversation_id,
                            MemoryItem.conversation_id == "global", # Explicitly marked global
                            MemoryItem.conversation_id == ""        # Or empty
                        )
                    )
                else:
                    query = session.query(MemoryItem).filter_by(conversation_id=conversation_id, project_id=project_id)
            else:
                query = session.query(MemoryItem).filter_by(project_id=project_id)
                
            if category:
                query = query.filter_by(category=category)
            return query.all()
        finally:
            session.close()

    
    @self_healing_retry
    def add_archive_turn(
        self,
        conversation_id: str,
        project_id: str,
        user_message: str,
        assistant_message: str
    ) -> ArchiveTurn:
        """Add a user-assistant turn to the archive."""
        import uuid
        session = self.get_session()
        try:
            conv = session.query(Conversation).filter_by(
                id=conversation_id,
                project_id=project_id
            ).first()
            
            if not conv:
                conv = Conversation(
                    id=conversation_id,
                    project_id=project_id
                )
                session.add(conv)
                session.commit()
            
            # Count turns in this session to get next turn number
            turn_count = session.query(ArchiveTurn).filter_by(
                conversation_id=conversation_id
            ).count()
            
            turn = ArchiveTurn(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                project_id=project_id,
                turn_number=turn_count,
                user_message=user_message[:4000],
                assistant_message=assistant_message[:4000]
            )
            session.add(turn)
            session.commit()
            return turn
        finally:
            session.close()
    
    @self_healing_retry
    def get_archive_turns(
        self,
        conversation_id: str,
        project_id: str,
        limit: int = 30
    ) -> List[ArchiveTurn]:
        """Retrieve recent archive turns for context window."""
        session = self.get_session()
        try:
            return session.query(ArchiveTurn)\
                .filter_by(conversation_id=conversation_id, project_id=project_id)\
                .order_by(ArchiveTurn.turn_number.desc())\
                .limit(limit)\
                .all()
        finally:
            session.close()
    
    def migrate_from_json(self, json_memory_dir: str = "./data/memory") -> None:
        """Migrate existing JSON-based memory to SQLite."""
        if not os.path.exists(json_memory_dir):
            logger.info(f"No JSON memory directory found at {json_memory_dir}")
            return
        
        logger.info(f"Starting migration from {json_memory_dir}")
        migrated = 0
        
        for json_file in Path(json_memory_dir).glob("*.json"):
            try:
                with open(json_file, 'r') as f:
                    data = json.load(f)
                
                conv_id = data.get("conversation_id", json_file.stem)
                project_id = data.get("project_id", "default")
                
                # Restore memory state
                self.save_memory_state(conv_id, project_id, {
                    "project_summary": data.get("project_summary", []),
                    "decisions": data.get("decisions", []),
                    "constraints": data.get("constraints", []),
                    "issues": data.get("issues", []),
                    "style_preferences": data.get("style_preferences", []),
                    "turn_count": data.get("turn_count", 0),
                    # Pending agent resume state (if any)
                    "pending_approval": data.get("pending_approval"),
                    "pending_plan": data.get("pending_plan"),
                    "pending_history": data.get("pending_history"),
                    "pending_goal": data.get("pending_goal"),
                })
                
                # Restore archive turns
                for turn in data.get("archive_turns", []):
                    self.add_archive_turn(
                        conv_id,
                        project_id,
                        turn.get("user", ""),
                        turn.get("assistant", "")
                    )
                
                migrated += 1
                logger.info(f"Migrated {conv_id} to SQLite")
            except Exception as e:
                logger.error(f"Failed to migrate {json_file}: {e}")
        
        logger.info(f"Migration complete: {migrated} conversations migrated")


# Global database instance
_db: Optional[DatabaseManager] = None


def get_db() -> DatabaseManager:
    """Get or initialize the global database instance."""
    global _db
    if _db is None:
        db_path = os.getenv("DATABASE_PATH", "./data/memory.db")
        _db = DatabaseManager(db_path)
        # Run migration from JSON if enabled
        if os.getenv("MIGRATE_FROM_JSON", "true").lower() == "true":
            _db.migrate_from_json()
    return _db


def init_db(db_path: str = "./data/memory.db") -> DatabaseManager:
    """Initialize database with custom path."""
    global _db
    _db = DatabaseManager(db_path)
    if os.getenv("MIGRATE_FROM_JSON", "true").lower() == "true":
        _db.migrate_from_json()
    return _db
