# Implementation Plan - Memory Continuity & Stable Identity

This plan addresses the issue where `conversation_id` changes every turn, breaking multi-turn memory retrieval. We will introduce a stable identity model that persists the same ID across a single thread even when the client does not provide one.

## User Review Required

> [!IMPORTANT]
> The proxy will now use a deterministic hash of the **first message** in a conversation as the fallback `conversation_id` if none is provided. This ensures stability as long as the conversation history is passed by the client.

> [!NOTE]
> If a client does provide a `conversation_id` or `user` ID, it will take precedence.

## Proposed Changes

### Database Layer

#### [MODIFY] [database.py](file:///Users/sallahuddin/Desktop/macagent_proxy_starter/app/database.py)
- Add an index on `user_message` (or a hash of it) if necessary, but start with a simple query of `ArchiveTurn` to find existing conversations by content.
- Update `get_or_create_conversation` to be more robust.

### Application Logic

#### [MODIFY] [main.py](file:///Users/sallahuddin/Desktop/macagent_proxy_starter/app/main.py)
- Replace `_derive_conversation_id` with a more stable implementation.
- Introduce `_resolve_conversation_id` which:
    1. Checks `request.conversation_id`.
    2. Checks `request.user`.
    3. Searches the database for matching history (using the first message as a key).
    4. Falls back to a deterministic hash of the first message.
- Update both streaming and non-streaming endpoints to use this new resolution logic.
- Ensure `x_memory` in the response always contains the resolved `conversation_id`.

### Verification

#### [NEW] [MEMORY_RECALL_VERIFICATION.md](file:///Users/sallahuddin/Desktop/macagent_proxy_starter/MEMORY_RECALL_VERIFICATION.md)
- Document the verification steps and results.

#### [NEW] [test_memory_continuity.py](file:///Users/sallahuddin/Desktop/macagent_proxy_starter/scripts/test_memory_continuity.py)
- A specialized test script to prove:
    - Multi-turn stability.
    - Same-project recall.
    - Project isolation.
    - Retrieval count > 0.

## Verification Plan

### Automated Tests
1. Run `scripts/test_memory_continuity.py`.
2. This script will:
    - Send Turn 1 to `project-a` with a secret.
    - Send Turn 2 to `project-a` asking for the secret (verify same `conversation_id` is returned).
    - Verify Turn 2 response contains the secret and `retrieved_items > 0`.
    - Send Turn 1 to `project-b` asking for the same secret (verify it's NOT found).

### Manual Verification
- Inspect the SQLite database using `sqlite3` to confirm only one `conversation_id` was created for the multi-turn session in `project-a`.
