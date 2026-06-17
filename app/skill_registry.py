"""
StarAgent Skill Registry — SQLite persistence for ingested skills.

Module-level functions used by skill_router.py, skill_library.py, and CLI.
"""

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("STARAGENT_SKILL_DB_PATH", "./data/skills.db")
_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn, DB_PATH
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS skills (
        id TEXT PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        domain TEXT NOT NULL,
        description TEXT,
        source_repo TEXT,
        source_path TEXT,
        skill_md_content TEXT,
        tags TEXT,
        license TEXT DEFAULT 'unknown',
        created_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_skills_domain ON skills(domain);
    CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name);

    CREATE TABLE IF NOT EXISTS skill_chunks (
        id TEXT PRIMARY KEY,
        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        heading TEXT,
        content TEXT NOT NULL,
        embedding TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_chunks_skill ON skill_chunks(skill_id);

    CREATE TABLE IF NOT EXISTS skill_tools (
        id TEXT PRIMARY KEY,
        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT,
        script_path TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_tools_skill ON skill_tools(skill_id);

    CREATE TABLE IF NOT EXISTS skill_tags (
        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
        tag TEXT NOT NULL,
        PRIMARY KEY (skill_id, tag)
    );

    CREATE TABLE IF NOT EXISTS skill_usage_log (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        skill_name TEXT NOT NULL,
        relevance_score REAL,
        reason TEXT,
        created_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_usage_task ON skill_usage_log(task_id);
    """)
    conn.commit()
    logger.info("Skill registry tables initialized")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def upsert_skill(
    name: str,
    domain: str,
    description: str,
    source_repo: str,
    source_path: str,
    skill_md_content: str,
    tags: List[str] = None,
    license_info: str = "unknown",
):
    """Insert or update a skill. Returns (skill_id, was_update)."""
    conn = _get_conn()
    row = conn.execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()
    was_update = row is not None
    if row:
        skill_id = row["id"]
        conn.execute("""
            UPDATE skills SET domain=?, description=?, source_repo=?, source_path=?,
                   skill_md_content=?, tags=?, license=?
            WHERE id=?
        """, (domain, description, source_repo, source_path, skill_md_content,
              json.dumps(tags or []), license_info, skill_id))
        # Clear old chunks/tags for re-ingestion
        conn.execute("DELETE FROM skill_chunks WHERE skill_id = ?", (skill_id,))
        conn.execute("DELETE FROM skill_tags WHERE skill_id = ?", (skill_id,))
        conn.execute("DELETE FROM skill_tools WHERE skill_id = ?", (skill_id,))
    else:
        skill_id = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO skills (id, name, domain, description, source_repo, source_path,
                               skill_md_content, tags, license, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (skill_id, name, domain, description, source_repo, source_path,
              skill_md_content, json.dumps(tags or []), license_info,
              datetime.utcnow().isoformat()))
    
    # Insert tags
    for tag in (tags or []):
        try:
            conn.execute("INSERT OR IGNORE INTO skill_tags (skill_id, tag) VALUES (?, ?)",
                         (skill_id, tag.lower()))
        except Exception:
            pass

    conn.commit()
    return skill_id, was_update


def add_chunk(skill_id: str, heading: str, content: str) -> str:
    conn = _get_conn()
    chunk_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO skill_chunks (id, skill_id, heading, content) VALUES (?, ?, ?, ?)",
        (chunk_id, skill_id, heading, content)
    )
    conn.commit()
    return chunk_id


def add_tool(skill_id: str, name: str, description: str = "", script_path: str = "") -> str:
    conn = _get_conn()
    tool_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO skill_tools (id, skill_id, name, description, script_path) VALUES (?, ?, ?, ?, ?)",
        (tool_id, skill_id, name, description, script_path)
    )
    conn.commit()
    return tool_id


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def list_skills(domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all skills, optionally filtered by domain."""
    conn = _get_conn()
    if domain:
        rows = conn.execute(
            "SELECT * FROM skills WHERE lower(domain) = ? ORDER BY domain, name",
            (domain.lower(),)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM skills ORDER BY domain, name").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_skill(name: str) -> Optional[Dict[str, Any]]:
    """Get full skill details by name."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM skills WHERE name = ?", (name,)).fetchone()
    if not row:
        return None
    skill = _row_to_dict(row)
    
    # Fetch chunks
    chunks = conn.execute(
        "SELECT heading, content FROM skill_chunks WHERE skill_id = ?", (skill["id"],)
    ).fetchall()
    skill["chunks"] = [{"heading": c["heading"], "content": c["content"]} for c in chunks]
    
    # Fetch tools
    tools = conn.execute(
        "SELECT name, description, script_path FROM skill_tools WHERE skill_id = ?", (skill["id"],)
    ).fetchall()
    skill["tools"] = [{"name": t["name"], "description": t["description"], "script_path": t["script_path"]} for t in tools]
    
    return skill


def search_skills(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Search skills by keyword matching against name, description, tags, and content."""
    conn = _get_conn()
    query_lower = query.lower()
    keywords = [w.strip() for w in query_lower.split() if len(w.strip()) >= 2]
    
    if not keywords:
        return []
    
    # Fetch all skills and score them
    rows = conn.execute("SELECT * FROM skills").fetchall()
    scored = []
    
    for row in rows:
        skill = _row_to_dict(row)
        score = 0.0
        
        name_lower = skill["name"].lower()
        desc_lower = (skill.get("description") or "").lower()
        tags_str = " ".join(json.loads(skill.get("tags") or "[]")).lower()
        content_lower = (skill.get("skill_md_content") or "")[:2000].lower()
        
        for kw in keywords:
            if kw in name_lower:
                score += 10.0
            if kw in desc_lower:
                score += 5.0
            if kw in tags_str:
                score += 3.0
            if kw in content_lower:
                score += 1.0
        
        if score > 0:
            skill["score"] = score
            scored.append(skill)
    
    # Sort by score descending
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:limit]


def get_skill_count() -> int:
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) as cnt FROM skills").fetchone()
    return row["cnt"] if row else 0


def get_domain_counts() -> Dict[str, int]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT domain, COUNT(*) as cnt FROM skills GROUP BY domain ORDER BY cnt DESC"
    ).fetchall()
    return {r["domain"]: r["cnt"] for r in rows}


def log_skill_usage(
    task_id: str,
    skill_id: str,
    skill_name: str,
    relevance_score: float = 0,
    reason: str = "",
):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO skill_usage_log (id, task_id, skill_id, skill_name, relevance_score, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), task_id, skill_id, skill_name, relevance_score, reason,
         datetime.utcnow().isoformat())
    )
    conn.commit()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)
