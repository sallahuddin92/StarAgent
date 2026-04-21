#!/usr/bin/env python3
"""
Test FastAPI concurrent request handling with SQLite.
This will try to reproduce the database lock issues.
"""

import asyncio
import sys
import os
from pathlib import Path
import time
import threading

# Setup path
sys.path.insert(0, "/Users/sallahuddin/Desktop/macagent_proxy_starter")
os.chdir("/Users/sallahuddin/Desktop/macagent_proxy_starter")

# Clean slate
db_path = Path("./data/concurrent_test.db")
if db_path.exists():
    db_path.unlink()

print("\n" + "="*70)
print("FASTAPI CONCURRENT REQUEST TEST")
print("="*70)

from app.database import DatabaseManager
from app.memory import MemoryStore
from app.tokenbudget import TokenBudgetManager
from app.models import ChatCompletionRequest

# Simulate what happens during FastAPI request handling
def simulate_request(request_id: int, conversation_id: str, db_manager: DatabaseManager, store: MemoryStore, budget_mgr: TokenBudgetManager):
    """Simulate a single FastAPI request that uses database."""
    try:
        print(f"\n[Request {request_id}] Starting conversation={conversation_id}")
        
        # 1. Get or create conversation (database read/write)
        conv = db_manager.get_or_create_conversation(conversation_id, f"proj_{request_id}")
        print(f"[Request {request_id}] ✓ Got conversation: {conv.id}")
        
        # 2. Load memory (database read)
        memory = store.load(conversation_id, f"proj_{request_id}")
        print(f"[Request {request_id}] ✓ Loaded memory, turn_count={memory.turn_count}")
        
        # 3. Simulate processing (sleep like the LLM would)
        time.sleep(0.5)
        
        # 4. Update memory (database write)
        memory.project_summary.append(f"Request {request_id} completed")
        store.save(memory)
        print(f"[Request {request_id}] ✓ Saved memory")
        
        # 5. Update budget (in-memory, not DB)
        budget_mgr.record_prompt_tokens(conversation_id, 50)
        budget_mgr.record_completion_tokens(conversation_id, 25)
        print(f"[Request {request_id}] ✓ Updated budget")
        
        return True
        
    except Exception as e:
        print(f"[Request {request_id}] ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

# Test 1: Sequential requests (baseline)
print("\n[TEST 1] Sequential Requests (Baseline)")
print("-" * 70)

db = DatabaseManager(str(db_path))
store = MemoryStore(base_dir="./data/memory", db_manager=db)
budget_mgr = TokenBudgetManager()

results = []
for i in range(3):
    success = simulate_request(i, f"conv_{i}", db, store, budget_mgr)
    results.append(success)
    
if all(results):
    print("✅ SEQUENTIAL TEST: PASS")
else:
    print("❌ SEQUENTIAL TEST: FAIL")

# Test 2: Concurrent requests (threading)
print("\n[TEST 2] Concurrent Requests (Threads)")
print("-" * 70)

db2 = DatabaseManager(str(db_path))
store2 = MemoryStore(base_dir="./data/memory2", db_manager=db2)
budget_mgr2 = TokenBudgetManager()

results = []
threads = []

def thread_wrapper(request_id):
    success = simulate_request(request_id, f"conv_t{request_id}", db2, store2, budget_mgr2)
    results.append(success)

# Launch 5 threads concurrently
for i in range(5):
    t = threading.Thread(target=thread_wrapper, args=(i,))
    threads.append(t)
    t.start()

# Wait for all threads
for t in threads:
    t.join()

if all(results):
    print("\n✅ CONCURRENT TEST: PASS")
else:
    print("\n❌ CONCURRENT TEST: FAIL")

# Test 3: Rapid sequential requests to same conversation
print("\n[TEST 3] Rapid Sequential Requests to Same Conversation")
print("-" * 70)

db3 = DatabaseManager(str(db_path))
store3 = MemoryStore(base_dir="./data/memory3", db_manager=db3)
budget_mgr3 = TokenBudgetManager()

results = []
conv_id = "shared_conv"

for i in range(5):
    success = simulate_request(i, conv_id, db3, store3, budget_mgr3)
    results.append(success)

if all(results):
    print("✅ RAPID SEQUENTIAL TEST: PASS")
else:
    print("❌ RAPID SEQUENTIAL TEST: FAIL")

print("\n" + "="*70)
print("CONCLUSION")
print("="*70)
print("If all tests PASS: SQLite is handling concurrency correctly")
print("If any test FAILS: Database locks or connection issues detected")
