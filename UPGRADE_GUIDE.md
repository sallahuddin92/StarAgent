# MacAgent Proxy v2.0 – Upgrade Guide

## Summary of Changes

Your MacAgent Proxy has been upgraded from a basic JSON memory starter to a production-ready system with enterprise features. This guide documents all new features and how to use them.

## What's New

### 1. **Streaming Responses** (SSE)
- Send `"stream": true` in chat request to get real-time streaming responses
- Responses are Server-Sent Events (SSE) format compatible with OpenAI clients
- First chunk includes memory metadata; subsequent chunks contain token deltas

**Example:**
```bash
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma4:e2b",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
```

**Response (SSE):**
```
data: {"id":"chatcmpl-...", "object":"chat.completion.chunk", "choices":[{"delta":{"content":"Hello"}}], ...}
data: {"id":"chatcmpl-...", "object":"chat.completion.chunk", "choices":[{"delta":{"content":" there"}}], ...}
data: [DONE]
```

### 2. **SQLite + Embeddings Storage**
- **Old:** JSON files in `./data/memory/`
- **New:** SQLite database in `./data/memory.db` with auto-migration

**Benefits:**
- Structured queries via SQL
- Embedding vectors stored alongside memory items
- Scalable to thousands of conversations
- Automatic JSON → SQLite migration on startup

**Database Schema:**
```
conversations
├── id (conversation_id)
├── project_id
├── created_at, updated_at
├── project_summary, decisions, constraints, issues, style_preferences (JSON)
└── turn_count

memory_items
├── id
├── conversation_id (FK)
├── category ("summary", "decision", etc.)
├── content
├── embedding (vector as JSON)
└── relevance_score

archive_turns
├── id
├── conversation_id (FK)
├── turn_number
├── user_message, assistant_message
└── created_at
```

### 3. **Semantic Search via Embeddings**
- **Old:** Keyword-based heuristic matching
- **New:** Embedding-based cosine similarity search (fallback to heuristic if unavailable)

**How it works:**
1. Query → embed via `sentence-transformers` (all-MiniLM-L6-v2)
2. Compute cosine similarity vs. all memory items
3. Return top-K items by score
4. Automatically falls back to keyword matching if embeddings unavailable

**Configuration:**
- `USE_SEMANTIC_SEARCH=true` – Enable embeddings (default: true)
- Embeddings generated on-the-fly; cached in `memory_items.embedding` for later queries
- ~100MB one-time download for sentence-transformers model

### 4. **Token Budgeting & Cost Control**
- **Per-conversation token tracking** with 24-hour reset window
- **Configurable limits** for prompt + completion tokens
- **Automatic message trimming** when approaching budget

**Default budgets (configurable):**
```
DEFAULT_PROMPT_TOKENS=2000       # System + context + recent messages
DEFAULT_COMPLETION_TOKENS=2000   # Model response
Reset daily (24 hours)
```

**Budget Status Endpoint:**
```bash
curl http://localhost:8081/v1/budget/conv-123 \
  -H "Authorization: Bearer local-dev-key"
```

**Response:**
```json
{
  "conversation_id": "conv-123",
  "total_tokens_used": 1200,
  "prompt_tokens_used": 800,
  "completion_tokens_used": 400,
  "max_prompt_tokens": 2000,
  "max_completion_tokens": 2000,
  "remaining_tokens": 2800,
  "budget_exhausted": false,
  "last_reset": "2024-01-20T10:15:00",
  "reset_in_hours": 18
}
```

When exhausted, API returns:
```json
HTTP 429 Too Many Requests
{
  "error": {
    "message": "Token budget exhausted for this conversation",
    "type": "token_budget_exceeded",
    "budget_status": {...}
  }
}
```

### 5. **Per-Project Memory Isolation**
- **Old:** All conversations shared global namespace
- **New:** Separate memory per `project_id`

**Usage:**
```json
{
  "model": "gemma4:e2b",
  "messages": [...],
  "project_id": "my-project",
  "conversation_id": "conv-123"
}
```

**Benefits:**
- Multiple projects without context bleeding
- Project-specific custom prompts (future)
- Isolated memory budgets per project
- Better organization for multi-tenant setups

**Project Management Endpoints:**
```bash
# Create project
POST /v1/projects
{
  "project_id": "my-project",
  "name": "My Project",
  "description": "...",
  "custom_system_prompt": "...",  # Optional override
  "token_budget_prompt": 2500,    # Project-specific budget
  "token_budget_completion": 3000
}

# Get project
GET /v1/projects/my-project
```

### 6. **Intelligent Memory Compaction**
- **Old:** Manual compaction (not implemented)
- **New:** Automatic LLM-based compaction + manual trigger

**How it works:**
1. Every N turns (default: 100), compaction is triggered
2. Last 50 turns are sent to LLM with current memory state
3. LLM extracts structured insights:
   - New decisions made
   - Constraints discovered
   - Issues/blockers
   - Style preferences
4. Results merged into memory state (deduped, truncated)
5. Old turns archived, new state persists

**Manual Trigger:**
```bash
POST /v1/memory/compact
{
  "conversation_id": "conv-123",
  "project_id": "my-project",
  "force": true  # Bypass interval check
}

Response:
{
  "conversation_id": "conv-123",
  "items_compacted": 50,
  "items_created": 5,
  "summary": "...",
  "compacted_at": "2024-01-20T10:15:00"
}
```

**Configuration:**
- `COMPACTION_INTERVAL=100` – Compact every N turns (default: 100)
- Compaction happens asynchronously (non-blocking)
- Template: `templates/memory_compactor_prompt.txt`

### 7. **Production Hardening**

#### Request Validation
- Conversation ID: alphanumeric + hyphens, max 256 chars
- Message length: max 16,000 chars per message
- API key: Bearer token validation

#### Retry Logic (with exponential backoff)
- 3 retry attempts for Ollama calls
- Backoff: 2s → 4s → 8s → 16s
- Uses `tenacity` library
- Automatic on network errors

#### Logging & Monitoring
- Structured logging with conversation context
- Request/response metadata logged
- Error stacktraces with full context
- Performance metrics tracking (avg latency, token counts)

**Example Log:**
```
[my-project:conv-123] Non-streaming request
[my-project:conv-123] Tokens: prompt=245, completion=156 | Retrieved: 4 items
```

#### Health Checks
- Docker health check: `GET /health` every 30s
- Returns service status + feature flags
- Auto-restart on failure

### 8. **Enhanced Response Metadata** (x_memory)

All chat completion responses now include extended metadata:

```json
{
  ...
  "x_memory": {
    "conversation_id": "conv-123",
    "project_id": "my-project",
    "retrieved_items": 4,              # Memory items injected
    "project_summary_items": 8,        # Items in memory state
    "turn_count": 12,                  # Total turns archived
    "budget_status": {                 # Token budget snapshot
      "total_tokens_used": 1200,
      "remaining_tokens": 2800,
      "budget_exhausted": false,
      ...
    }
  }
}
```

## Migration Guide

### From v1.0 to v2.0

**No breaking changes!** Your existing JSON memory files are automatically migrated to SQLite on first startup.

**What happens on startup:**
1. Database check: If `./data/memory.db` doesn't exist, create it
2. Migration: If `MIGRATE_FROM_JSON=true`, scan `./data/memory/` and import all JSON files
3. Both stored in parallel for one deployment cycle for safety
4. After verification, you can delete JSON files manually

**To enable migration:**
```bash
# In .env
MIGRATE_FROM_JSON=true
```

**To disable migration (if migrated already):**
```bash
# In .env
MIGRATE_FROM_JSON=false
```

### API Changes

| Feature | v1.0 | v2.0 | Change |
|---------|------|------|--------|
| `/v1/chat/completions` | ✓ | ✓ | Added `stream`, `project_id` fields |
| Streaming | ✗ | ✓ | New SSE support |
| Memory storage | JSON | SQLite | Auto-migrated |
| Semantic search | ✗ | ✓ | New retrieval method |
| Token counting | Disabled | ✓ | Now returns accurate counts |
| Token limits | ✗ | ✓ | New budget enforcement |
| Per-project | ✗ | ✓ | New `project_id` parameter |
| Memory compaction | ✗ | ✓ | New LLM-based compaction |

**Backward Compatibility:**
- Old requests without `project_id` default to `"default"` project
- Old requests with `stream: true` fail gracefully (get non-streamed response)
- JSON memory auto-imported on startup

## New Environment Variables

```bash
# Storage
DATABASE_PATH=./data/memory.db
MIGRATE_FROM_JSON=true

# Features
USE_SEMANTIC_SEARCH=true
USE_STREAMING=true
COMPACTION_INTERVAL=100

# Token budgets
DEFAULT_PROMPT_TOKENS=2000
DEFAULT_COMPLETION_TOKENS=2000

# Logging
LOG_LEVEL=INFO
```

See `.env.example` for full list with descriptions.

## Deployment Checklist

- [ ] Update `requirements.txt` dependencies (`pip install -r requirements.txt`)
- [ ] Review and update `.env` based on `.env.example`
- [ ] Start proxy: `docker-compose up -d` or `uvicorn app.main:app --port 8081`
- [ ] Verify health: `curl http://localhost:8081/health`
- [ ] Monitor logs for migration: `docker-compose logs -f`
- [ ] Test streaming: `POST /v1/chat/completions` with `"stream": true`
- [ ] Test token budgeting: `GET /v1/budget/test-conv`
- [ ] Verify SQLite created: `ls -la data/memory.db`

## Common Tasks

### Enable Semantic Search
```bash
# .env
USE_SEMANTIC_SEARCH=true
# Requires: pip install sentence-transformers
```

### Adjust Token Budgets
```bash
# .env
DEFAULT_PROMPT_TOKENS=3000       # Increase for longer context
DEFAULT_COMPLETION_TOKENS=2500   # Increase for longer responses
```

### Disable Streaming (v1.0 behavior)
```bash
# .env
USE_STREAMING=false
```

### Manual Memory Compaction
```bash
curl -X POST http://localhost:8081/v1/memory/compact \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv-123",
    "project_id": "my-project",
    "force": true
  }'
```

### Export Memory State
```bash
# SQLite query
sqlite3 data/memory.db \
  "SELECT id, project_id, project_summary FROM conversations WHERE id='conv-123';"
```

## Troubleshooting

### Q: Embeddings download is slow
**A:** First load downloads ~100MB sentence-transformers model. Subsequent loads use cache. Disable with `USE_SEMANTIC_SEARCH=false` to use heuristic fallback.

### Q: Token counts are now being reported, why?
**A:** v2.0 implements proper token counting with `tiktoken`. Estimates are more accurate than before. Budget enforcement requires this.

### Q: Can I use the old JSON files?
**A:** Yes! Set `MIGRATE_FROM_JSON=true` to auto-import on startup. Both formats work in parallel.

### Q: SQLite database is locked
**A:** Ensure only one proxy process writes to `memory.db`. For multiple instances, use PostgreSQL (future feature).

### Q: How do I reset token budgets manually?
**A:** Currently reset automatically after 24 hours. Manual reset support coming in v2.1.

## Performance Notes

- **Embeddings:** ~50-100ms per query (cached afterwards)
- **Semantic search:** ~10-20ms (cosine similarity)
- **Heuristic fallback:** ~5ms
- **Token counting:** ~5-10ms
- **SQLite queries:** <1ms (indexed)
- **Memory compaction:** 10-30s (LLM call, async, non-blocking)

## Next Steps

1. **Deploy** using the quick start guide in README_v2.md
2. **Configure** memory settings in `.env`
3. **Monitor** logs for any migration issues
4. **Test** new features (streaming, semantic search, token budgeting)
5. **Integrate** with Open WebUI using the proxy URL

## Support

For issues or questions:
1. Check logs: `docker-compose logs -f macagent-proxy`
2. Verify health: `curl http://localhost:8081/health`
3. Test Ollama connection: `curl http://127.0.0.1:11434/api/tags`
4. Review configuration in `.env` and `docker-compose.yml`

---

**Version:** 2.0.0  
**Date:** 2024-01-20  
**Status:** Production-Ready
