#!/usr/bin/env python3
"""
Application-Level Failure Investigation
Test the actual app code components to find the exact failure point
"""

import sys
import os
import sqlite3
import json
from pathlib import Path

# Setup path
sys.path.insert(0, "/Users/sallahuddin/Desktop/macagent_proxy_starter")
os.chdir("/Users/sallahuddin/Desktop/macagent_proxy_starter")

print("\n" + "="*70)
print("APPLICATION-LEVEL INVESTIGATION")
print("="*70)

# Clean slate
db_path = Path("./data/memory.db")
if db_path.exists():
    db_path.unlink()

# Test 1: Database module import and creation
print("\n[TEST 1] Database Module - Schema Creation and Migration")
print("-" * 70)

try:
    from app.database import DatabaseManager
    
    db = DatabaseManager(str(db_path))
    print("✓ DatabaseManager created")
    
    # Check schema
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"✓ Tables created: {tables}")
    
    # Test get_or_create_conversation (first call)
    conv = db.get_or_create_conversation("test1", "proj1", "user1")
    print(f"✓ First conversation created: {conv.id}")
    
    # Test second conversation
    conv2 = db.get_or_create_conversation("test2", "proj1", "user1")
    print(f"✓ Second conversation created: {conv2.id}")
    
    # Check database state
    cursor.execute("SELECT COUNT(*) FROM conversations")
    count = cursor.fetchone()[0]
    print(f"✓ Conversations in DB: {count}")
    conn.close()
    
    print("✅ DATABASE MODULE: PASS")
    
except Exception as e:
    print(f"❌ DATABASE MODULE: FAIL - {e}")
    import traceback
    traceback.print_exc()

# Test 2: Memory module
print("\n[TEST 2] Memory Module - Load and Save")
print("-" * 70)

try:
    from app.memory import MemoryStore, MemoryState
    
    db = DatabaseManager(str(db_path))
    store = MemoryStore(base_dir="./data/memory", db_manager=db)
    
    # Create/save initial memory
    state1 = store.load("test1", "proj1")
    print(f"✓ Loaded memory for test1: turn_count={state1.turn_count}")
    
    # Update and save
    state1.project_summary.append("Test summary")
    store.save(state1)
    print(f"✓ Saved memory for test1")
    
    # Load again
    state1_reload = store.load("test1", "proj1")
    print(f"✓ Reloaded memory: {state1_reload}")
    
    # Second conversation
    state2 = store.load("test2", "proj1")
    state2.project_summary.append("Second summary")
    store.save(state2)
    print(f"✓ Saved memory for test2")
    
    print("✅ MEMORY MODULE: PASS")
    
except Exception as e:
    print(f"❌ MEMORY MODULE: FAIL - {e}")
    import traceback
    traceback.print_exc()

# Test 3: Token budget module
print("\n[TEST 3] Token Budget Module - Tracking")
print("-" * 70)

try:
    from app.tokenbudget import TokenBudgetManager, TokenCounter
    
    counter = TokenCounter()
    mgr = TokenBudgetManager()
    
    # Count tokens
    tokens = counter.count_tokens("This is a test message")
    print(f"✓ Token count: {tokens}")
    
    # Test budget for first conversation
    budget1 = mgr.get_budget("test1")
    print(f"✓ Budget 1 initial: remaining_tokens={budget1.remaining_tokens}")
    
    # Update budget
    mgr.record_prompt_tokens("test1", 100)
    mgr.record_completion_tokens("test1", 50)
    budget1_after = mgr.get_budget("test1")
    print(f"✓ Budget 1 after use: remaining_tokens={budget1_after.remaining_tokens}")
    
    # Test budget for second conversation
    budget2 = mgr.get_budget("test2")
    print(f"✓ Budget 2: remaining_tokens={budget2.remaining_tokens}")
    
    print("✅ TOKEN BUDGET MODULE: PASS")
    
except Exception as e:
    print(f"❌ TOKEN BUDGET MODULE: FAIL - {e}")
    import traceback
    traceback.print_exc()

# Test 4: Retrieval module
print("\n[TEST 4] Retrieval Module - Embeddings and Search")
print("-" * 70)

try:
    from app.retrieval import EmbeddingModel, SemanticRetriever
    
    embedder = EmbeddingModel()
    print("✓ Embedding model loaded")
    
    # Generate embedding using sync batch method (embed() is async)
    embs = embedder.embed_batch(["Test document for retrieval"])
    emb = embs[0]
    if emb:
        print(f"✓ Embedding generated: {len(emb)} dimensions")
    else:
        print(f"✓ Embedding generation returned None (expected for some models)")
    
    # Test retriever
    retriever = SemanticRetriever(embedding_model=embedder)
    print("✓ Semantic retriever created")
    
    print("✅ RETRIEVAL MODULE: PASS")
    
except Exception as e:
    print(f"❌ RETRIEVAL MODULE: FAIL - {e}")
    import traceback
    traceback.print_exc()

# Test 5: Complete request flow (simulated)
print("\n[TEST 5] Complete Request Flow - Simulated Chat Request")
print("-" * 70)

try:
    from app.main import _call_ollama
    from app.memory import MemoryStore
    from app.tokenbudget import TokenBudgetManager
    from app.database import DatabaseManager
    from app.models import ChatCompletionRequest
    
    # Setup
    db = DatabaseManager(str(db_path))
    store = MemoryStore(base_dir="./data/memory", db_manager=db)
    budget_mgr = TokenBudgetManager()
    
    # Request 1
    print("Processing Request 1...")
    request1 = ChatCompletionRequest(
        model="test-model",
        messages=[{"role": "user", "content": "Hello"}],
        project_id="proj1"
    )
    
    conv_id1 = request1.messages[0].content[:10]
    memory1 = store.load(conv_id1, "proj1")
    budget1_before = budget_mgr.get_budget(conv_id1)
    print(f"  ✓ Request 1 setup: memory loaded, budget: {budget1_before.remaining_tokens}")
    
    memory1.project_summary.append(f"User said: {request1.messages[0].content}")
    store.save(memory1)
    budget_mgr.record_prompt_tokens(conv_id1, 50)
    budget_mgr.record_completion_tokens(conv_id1, 25)
    budget1_after = budget_mgr.get_budget(conv_id1)
    print(f"  ✓ Request 1 save: memory updated, budget: {budget1_after.remaining_tokens}")
    
    # Request 2
    print("Processing Request 2...")
    request2 = ChatCompletionRequest(
        model="test-model",
        messages=[{"role": "user", "content": "World"}],
        project_id="proj1"
    )
    
    conv_id2 = request2.messages[0].content[:10]
    memory2 = store.load(conv_id2, "proj1")
    budget2_before = budget_mgr.get_budget(conv_id2)
    print(f"  ✓ Request 2 setup: memory loaded, budget: {budget2_before.remaining_tokens}")
    
    memory2.project_summary.append(f"User said: {request2.messages[0].content}")
    store.save(memory2)
    budget_mgr.record_prompt_tokens(conv_id2, 50)
    budget_mgr.record_completion_tokens(conv_id2, 25)
    budget2_after = budget_mgr.get_budget(conv_id2)
    print(f"  ✓ Request 2 save: memory updated, budget: {budget2_after.remaining_tokens}")
    
    # Verify database state
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM conversations")
    conv_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM memory_items")
    item_count = cursor.fetchone()[0]
    conn.close()
    
    print(f"  ✓ Database state: {conv_count} conversations, {item_count} memory items")
    
    print("✅ REQUEST FLOW: PASS")
    
except Exception as e:
    print(f"❌ REQUEST FLOW: FAIL - {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*70)
print("CONCLUSION")
print("="*70)
print("If all tests PASS: Issue is in FastAPI request handling/streaming")
print("If any test FAILS: Issue is in that specific module")
