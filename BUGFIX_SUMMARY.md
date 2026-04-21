# Root Cause Analysis & Fixes

## Problems Found & Fixed

### 1. **CRITICAL: SQLite Schema Bug - Composite Primary Key Missing**
**Issue**: The `conversations` table had a PRIMARY KEY of only `(id)` (conversation_id), but the application supports multiple projects with conversations of the same name. This caused `UNIQUE constraint failed` errors when attempting to create conversations with the same ID in different projects.

**Root Cause**: When a conversation ID existed in project_a, trying to create the same conversation ID in project_b failed because SQLite saw it as a duplicate.

**Fix**: Changed the PRIMARY KEY from:
```python
id = Column(String, primary_key=True)  # WRONG
project_id = Column(String, nullable=False, index=True)
```

To:
```python
id = Column(String, primary_key=True)  # Composite key
project_id = Column(String, primary_key=True)  # Composite key
```

Also updated `MemoryItem` and `ArchiveTurn` tables to include `project_id` in their composite foreign keys:
```python
__table_args__ = (
    ForeignKeyConstraint(['conversation_id', 'project_id'], 
                        ['conversations.id', 'conversations.project_id']),
    ...
)
```

---

### 2. **SQLAlchemy Session Lifecycle Issue**
**Issue**: `DetachedInstanceError` when accessing attributes on Conversation objects after the session closed.

**Root Cause**: SQLAlchemy's default behavior (`expire_on_commit=True`) expires all ORM objects after commit, making them "detached" from the session. Accessing attributes later requires the session to still be open.

**Fix**: Added `expire_on_commit=False` to the SessionLocal maker:
```python
self.SessionLocal = sessionmaker(
    bind=self.engine,
    expire_on_commit=False,  # Keep objects valid after commit
    autoflush=False,
    autocommit=False
)
```

---

### 3. **MemoryStore Constructor Missing `base_dir` Parameter**
**Issue**: Tests called `MemoryStore(db_manager=db)` but the constructor required `base_dir` as the first positional argument.

**Fix**: Updated test calls to include the required parameter:
```python
store = MemoryStore(base_dir="./data/memory", db_manager=db)
```

---

### 4. **TokenCounter Method Name Wrong**
**Issue**: Tests called `counter.count()` but the method was named `count_tokens()`.

**Fix**: Updated test to use correct method name:
```python
tokens = counter.count_tokens("This is a test message")
```

---

### 5. **TokenBudgetManager Method Names Wrong**
**Issue**: Tests called `mgr.update_usage()` but the actual methods were `record_prompt_tokens()` and `record_completion_tokens()`.

**Fix**: Updated test to use correct method names:
```python
mgr.record_prompt_tokens(conv_id1, 50)
mgr.record_completion_tokens(conv_id1, 25)
```

---

### 6. **EmbeddingModel.embed() is Async**
**Issue**: Test called `embedder.embed()` expecting a synchronous return, but the method is async and returns a coroutine.

**Fix**: Used the synchronous batch method instead:
```python
embs = embedder.embed_batch(["Test document"])
emb = embs[0]
```

---

### 7. **ChatMessage is Pydantic Model, Not Dictionary**
**Issue**: Test accessed `request1.messages[0]["content"]` but `messages` is a list of `ChatMessage` objects (Pydantic models), not dicts.

**Fix**: Updated to use attribute access:
```python
request1.messages[0].content
```

---

### 8. **SemanticRetriever Constructor Parameters**
**Issue**: Test passed `db_manager=DatabaseManager(...)` but the constructor doesn't accept that parameter.

**Fix**: Updated to use only `embedding_model`:
```python
retriever = SemanticRetriever(embedding_model=embedder)
```

---

## Test Results

### All Tests Now PASS ✅

**Component Tests (test_app_components.py)**:
- ✅ Database Module - Schema Creation and Migration: PASS
- ✅ Memory Module - Load and Save: PASS
- ✅ Token Budget Module - Tracking: PASS
- ✅ Retrieval Module - Embeddings and Search: PASS
- ✅ Complete Request Flow - Simulated Chat Request: PASS

**Concurrency Tests (test_fastapi_concurrent.py)**:
- ✅ Sequential Requests (Baseline): PASS
- ✅ Concurrent Requests (Threads): PASS (5 concurrent threads)
- ✅ Rapid Sequential Requests to Same Conversation: PASS

---

## What Was NOT the Issue

❌ **NOT SQLite itself** - SQLite with WAL mode and proper timeouts handles concurrency fine
❌ **NOT FastAPI** - The framework works correctly when the database layer is fixed
❌ **NOT database locks** - No locks needed, just proper schema design

---

## Key Takeaway

The **CRITICAL BUG** was the incomplete PRIMARY KEY. In a multi-tenant/multi-project system, you MUST include all identifying columns in the primary key. The fix was straightforward once identified through systematic component testing and concurrent request simulation.

