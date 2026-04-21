# Root Cause Analysis: Memory Continuity Issues

## Problem Description
The MacAgent Proxy fails to maintain conversation continuity across multiple turns. This results in each prompt being treated as a new conversation, which breaks memory retrieval and prevents the model from answering follow-up questions about facts shared earlier in the same thread.

## Root Cause: Brittle `conversation_id` Derivation
The primary issue lies in the fallback logic for identifying a conversation when a `conversation_id` is not explicitly provided by the client (a common scenario with standard OpenAI-compatible interfaces like Open WebUI).

### Evidence from `app/main.py`
In `_chat_completions_non_streaming` and `_stream_chat_completions`, the `conversation_id` is assigned as follows:

```python
conversation_id = request.conversation_id or request.user or _derive_conversation_id(request)
```

The `_derive_conversation_id` function (lines 551-558) is implemented as:

```python
def _derive_conversation_id(request: ChatCompletionRequest) -> str:
    """Derive conversation ID from request content."""
    seed_parts = []
    if request.messages:
        for message in request.messages[-3:]:
            seed_parts.append(_content_to_text(message.content)[:80])
    seed = "|".join(seed_parts).strip() or "default"
    return seed[:80]
```

### Why It Breaks Continuity
1. **Sliding Window**: The function uses the last 3 messages to create a "seed" for the ID.
2. **Dynamic ID**: As a conversation progresses, the "last 3 messages" change with every new turn.
3. **Partitioned Memory**: Because the ID changes every turn, the proxy creates a fresh `MemoryState` in the database for each prompt.
4. **Retrieval Failure**: Semantic retrieval and heuristic memory loading only look at items associated with the *current* `conversation_id`. Since Turn 2 has a different ID than Turn 1, it cannot "see" the memory stored in Turn 1.

## Impact
- **Broken Multi-turn**: The agent "forgets" everything from the previous turn because it's technically in a "new" conversation.
- **Database Bloat**: The `conversations` table is filled with hundreds of single-turn entries.
- **Useless Memory**: Facts explicitly told to the agent ("Remember this...") are inaccessible in the next turn.

## Resolution Requirement
We must move away from content-based ID derivation for active threads and implement a stable identity model that supports:
- Client-assisted continuity (explicit IDs).
- Proxy-generated stable IDs for new threads.
- Message-history-based lookup for "dumb" clients that provide history but no ID.
