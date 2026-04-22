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

                for stmt in alters:
                    conn.exec_driver_sql(stmt)
                if alters:
                    conn.commit()
                    logger.info(f"Added pending_* columns to conversations: {len(alters)}")
        except Exception as e:
            logger.warning(f"Pending-state schema migration skipped/failed: {e}")
    
    def get_session(self):
        """Get a new database session."""
        return self.SessionLocal()
    
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
            
            session.add(conv)
            session.commit()
        finally:
            session.close()
    
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
                }
            
            return {
                "project_summary": json.loads(conv.project_summary or "[]"),
                "decisions": json.loads(conv.decisions or "[]"),
                "constraints": json.loads(conv.constraints or "[]"),
                "issues": json.loads(conv.issues or "[]"),
                "style_preferences": json.loads(conv.style_preferences or "[]"),
                "turn_count": conv.turn_count,
                "pending_approval": json.loads(conv.pending_approval) if getattr(conv, "pending_approval", None) else None,
                "pending_plan": json.loads(conv.pending_plan) if getattr(conv, "pending_plan", None) else None,
                "pending_history": json.loads(conv.pending_history) if getattr(conv, "pending_history", None) else None,
                "pending_goal": getattr(conv, "pending_goal", None),
            }
        finally:
            session.close()
    
    def add_memory_item(
        self,
        conversation_id: str,
        project_id: str,
        category: str,
        content: str,
        embedding: Optional[List[float]] = None
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
    
    def get_memory_items(
        self,
        conversation_id: str,
        project_id: str,
        category: Optional[str] = None
    ) -> List[MemoryItem]:
        """Retrieve memory items for a conversation, optionally filtered by category."""
        session = self.get_session()
        try:
            query = session.query(MemoryItem).filter_by(conversation_id=conversation_id, project_id=project_id)
            if category:
                query = query.filter_by(category=category)
            return query.all()
        finally:
            session.close()
    
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
