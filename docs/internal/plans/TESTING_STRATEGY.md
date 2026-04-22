# Testing Strategy: MacAgent Proxy

Testing a memory-augmented LLM proxy requires going beyond unit tests. We focus on **Identity**, **Persistence**, and **Semantic Quality**.

---

## 1. Automated Validation Suite
We have built specialized test scripts to verify the core engine:

### A. Identity & Collision Test
**Command**: `python3 scripts/test_identity_hardening.py`
*   **What it does**: Simulates multiple threads starting with the same generic greeting (e.g., "Hi").
*   **Success Criteria**: Each thread is assigned a unique UUID and does not "leak" context to the other.

### B. Restart & Persistence Test
**Command**: `python3 scripts/test_restart_persistence.py`
*   **What it does**: Starts a conversation, kills the proxy process, restarts it, and sends another message.
*   **Success Criteria**: The proxy successfully "reattaches" to the existing SQLite conversation record using the first-message fingerprint.

### C. End-to-End Semantic Recall (The "Golden Test")
**Command**: `python3 scripts/final_validation_pass.py`
*   **What it does**: 
    1. Stores specific facts (e.g., Architecture type).
    2. Forces a background compaction cycle.
    3. Trims active message history to effectively "forget" the facts.
    4. Asks a fuzzy query about those facts.
*   **Success Criteria**: The AI correctly recalls the facts from the **Semantic Vector Store**.

---

## 2. Manual Verification (Open WebUI)
To test the "real world" experience:

1.  **Connect Open WebUI**: Set the API URL to `http://localhost:8081/v1`.
2.  **Turn On "Show Metadata"**: Most UIs will show the `x_memory` metadata in the first chunk. Check for `retrieved_items` count.
3.  **The "24-Hour" Test**: 
    - Tell the agent something unique (e.g., "The project code name is NEBULA").
    - Chat about unrelated things for 20+ turns.
    - Ask: "By the way, what was the project code name?"
    - **Success**: If it answers correctly, the Compaction + Retrieval loop is working.

---

## 3. Database Inspection (Observability)
For deep debugging, you can query the state directly:

```bash
# View all learned insights for a project
sqlite3 data/memory.db "SELECT category, content FROM memory_items;"

# Check current turn counts
sqlite3 data/memory.db "SELECT conversation_id, turn_count FROM conversations;"
```

---

## 4. Quality Benchmarks (Future)
For professional production, we would add:
*   **Retrieval Hit Rate**: Testing 100 queries against 100 documents to ensure the Top-1 result is relevant.
*   **Compaction Compression Ratio**: Monitoring how many tokens are saved while maintaining summary quality.
