# Legacy Conversation ID Cleanup Plan

## Context
The previous iteration of the proxy used raw message content or sequential strings (e.g., "Hello", "Sequential test 1") as `conversation_id`. The new system uses **stable UUIDs** resolved via fingerprinting.

## Strategy: Safely Ignore
Legacy IDs do not need to be migrated because:
1.  **Identity Conflict is Low**: Fingerprinting-based resolution is unlikely to match a legacy string unless the user re-types that exact string as their first message.
2.  **Schema Compatibility**: The database schema supports both string-based and UUID-based IDs without modification.

## Cleanup Options

### Option A: Automatic Dry Run (Recommended)
Users can run `scripts/cleanup_legacy_ids.py` to identify non-UUID IDs in their database. These can be safely ignored or manually deleted.

### Option B: Formal Purge
To reclaim space and clean up the UI/DB, run the following SQL:
```sql
DELETE FROM conversations WHERE conversation_id NOT GLOB 'conv-[0-9a-f]*';
DELETE FROM archive_turns WHERE conversation_id NOT GLOB 'conv-[0-9a-f]*';
DELETE FROM memory_items WHERE conversation_id NOT GLOB 'conv-[0-9a-f]*';
```

## Recommendation
We recommend **Option A** (Ignore). The legacy data serves as a useful diagnostic "shadow history" and does not interfere with the production stability of the new identity model.
