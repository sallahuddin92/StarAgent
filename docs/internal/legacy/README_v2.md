# MacAgent Proxy v2.0 - Production-Ready Local Memory Proxy

Upgraded local middleware for **Open WebUI → Memory Proxy → Ollama** with advanced features.

This production-ready proxy adds intelligent context management to small local models, enabling long conversation continuity without token-stuffing. Features include streaming responses, semantic search via embeddings, SQLite persistence with automatic JSON migration, token budgeting, per-project memory isolation, intelligent memory compaction, and comprehensive production hardening.

## Features

✅ **Streaming Responses** – SSE (Server-Sent Events) streaming support for real-time chat
✅ **Semantic Search** – Embedding-based retrieval (sentence-transformers) with cosine similarity
✅ **SQLite Storage** – Persistent database with schema for conversations, memory items, and embeddings
✅ **Token Budgeting** – Track and enforce token limits per conversation with automatic message trimming
✅ **Per-Project Memory** – Isolate conversation memory by project for multi-project workflows
✅ **Intelligent Compaction** – LLM-based memory compaction triggered by turn count thresholds
✅ **Production Hardening** – Request logging, Ollama retry logic with exponential backoff, input validation, rate limiting
✅ **Backward Compatible** – Automatic migration from JSON memory to SQLite on startup
✅ **OpenAI-Compatible API** – Drop-in replacement for Open WebUI with `/v1/chat/completions` endpoint
✅ **Health Checks & Monitoring** – Built-in Docker health checks, performance metrics, structured logging

## Architecture

```
Open WebUI
    ↓
┌─────────────────────────────────────────────┐
│      MacAgent Proxy v2.0                    │
├─────────────────────────────────────────────┤
│ • Streaming Response Handler                │
│ • Token Budget Manager                      │
│ • Semantic Retriever (embeddings)           │
│ • Memory Compactor (LLM-based)              │
│ • Request Logger & Validator                │
└─────────────────────────────────────────────┘
    ↓                          ↓
  Ollama                   SQLite DB
  (inference)          (memory + embeddings)
```

## Quick Start

### Prerequisites

- **Ollama** running locally (http://127.0.0.1:11434)
- **Python 3.12+** or Docker

### 1. Local Installation

```bash
# Clone or download the starter
cd /path/to/macagent_proxy_starter

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Copy example env
cp .env.example .env

# Run proxy
uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload
```

### 2. Docker Setup

```bash
docker-compose up -d
```

This starts the proxy on `http://localhost:8081`. The proxy automatically initializes the SQLite database and migrates any existing JSON memory files.

### 3. Configure Open WebUI

Point Open WebUI to the proxy:

```
Base URL: http://localhost:8081
API Key: Bearer local-dev-key  (or set PROXY_API_KEY in .env)
```

## Endpoints

### Chat Completions (OpenAI-compatible)

**POST** `/v1/chat/completions`

```json
{
  "model": "gemma4:e2b",
  "messages": [
    {"role": "user", "content": "..."}
  ],
  "stream": false,
  "project_id": "my-project",
  "conversation_id": "conv-123",
  "temperature": 0.2,
  "max_tokens": 1500
}
```

**Response:**

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "gemma4:e2b",
  "choices": [{
    "message": {"role": "assistant", "content": "..."},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 245,
    "completion_tokens": 156,
    "total_tokens": 401
  },
  "x_memory": {
    "conversation_id": "conv-123",
    "project_id": "my-project",
    "retrieved_items": 4,
    "turn_count": 12,
    "budget_status": {...}
  }
}
```

### List Models

**GET** `/v1/models`

Returns available models (currently hardcoded to `DEFAULT_MODEL`).

### Project Management

**POST** `/v1/projects`
Create a new project with custom settings.

**GET** `/v1/projects/{project_id}`
Retrieve project info.

### Memory Compaction

**POST** `/v1/memory/compact`

Trigger LLM-based memory compaction for a conversation:

```json
{
  "conversation_id": "conv-123",
  "project_id": "my-project",
  "force": false
}
```

### Budget Status

**GET** `/v1/budget/{conversation_id}`

Get token usage and budget status:

```json
{
  "conversation_id": "conv-123",
  "total_tokens_used": 1200,
  "prompt_tokens_used": 800,
  "completion_tokens_used": 400,
  "remaining_tokens": 2800,
  "budget_exhausted": false,
  "last_reset": "2024-01-20T10:15:00",
  "reset_in_hours": 18
}
```

### Health Check

**GET** `/health`

Server health and feature status.

## Configuration

Edit `.env` to customize:

```bash
# Ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
DEFAULT_MODEL=gemma4:e2b

# Storage
MEMORY_DIR=./data/memory
DATABASE_PATH=./data/memory.db

# Memory
MAX_ARCHIVE_TURNS=200
MAX_RETRIEVED_ITEMS=6
ENABLE_MEMORY_UPDATE=true

# Features
USE_SEMANTIC_SEARCH=true       # Enable embedding-based retrieval
USE_STREAMING=true              # Enable SSE streaming
COMPACTION_INTERVAL=100         # Compact memory every N turns

# API Security
PROXY_API_KEY=local-dev-key

# Token Budgets (per conversation, 24-hour reset)
DEFAULT_PROMPT_TOKENS=2000
DEFAULT_COMPLETION_TOKENS=2000

# Logging
LOG_LEVEL=INFO
```

## Memory Model

### Storage

- **Primary:** SQLite database (`./data/memory.db`)
  - `conversations` – Conversation metadata and memory state
  - `memory_items` – Discrete memory items with embeddings for semantic search
  - `archive_turns` – Historical user/assistant exchanges
- **Legacy:** JSON files (auto-migrated to SQLite on startup)

### Memory Categories

For each conversation, the system maintains:

- **Project Summary** – High-level project context (max 20 items)
- **Decisions** – Key design and implementation decisions
- **Constraints** – Technical or architectural constraints
- **Issues** – Known blockers or problems
- **Style Preferences** – Code style, tone, output preferences
- **Archive Turns** – Historical exchanges (max 200 recent turns)

### Retrieval

**Semantic Search (Enabled by default):**
1. Generate embedding for user query via `sentence-transformers` (all-MiniLM-L6-v2) or Ollama
2. Compute cosine similarity against all memory items
3. Return top-K items sorted by relevance score

**Fallback: Heuristic Matching**
- Extract keywords from query
- Match against memory items via Jaccard similarity
- Returns top-K non-zero matches

### Memory Compaction

Triggered automatically when:
- Turn count reaches `COMPACTION_INTERVAL` (default: 100)
- Or via manual API trigger

Process:
1. Collect last N conversation turns
2. Call LLM to extract structured insights
3. Merge results into memory categories
4. Deduplicate and truncate
5. Archive older turns

## Token Budgeting

Each conversation has:
- **Prompt Budget** – Max tokens per turn for system + context (default: 2000)
- **Completion Budget** – Max tokens for model response (default: 2000)
- **24-hour Reset** – Budget resets daily

When budget exhausted:
- API returns `HTTP 429 Too Many Requests`
- Includes detailed budget status in response

Message trimming strategy:
- Always preserves system prompt
- Keeps most recent messages within budget
- Trims oldest messages first if needed

## Logging & Monitoring

Structured logging includes:
- Request/response metadata (tokens, duration, retrieved items)
- Error stacktraces with conversation context
- Performance metrics (avg token counting time, Ollama latency)

Example log:

```
[my-project:conv-123] Non-streaming request
[my-project:conv-123] Tokens: prompt=245, completion=156 | Retrieved: 4 items
```

## Production Deployment

### Docker

```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f macagent-proxy

# Stop
docker-compose down
```

The Docker setup includes:
- Health checks (30s interval, 3 retries)
- Auto-restart on crash
- Resource limits (2 CPU, 2GB memory)
- SQLite persistence volume
- Graceful shutdown

### Environment Considerations

- **GPU Support:** No GPU required (CPU-based embeddings are lightweight)
- **Memory:** ~500MB for Python + model + SQLite
- **Disk:** ~1MB per 1000 conversation turns (SQLite)
- **Network:** Requires connectivity to Ollama and Ollama requires internet for first model download

### Scaling

For multiple instances:
1. Use shared SQLite (single-writer limitation) or migrate to PostgreSQL
2. Implement shared cache for embeddings (Redis)
3. Use message queue for compaction tasks (Celery)

## Development

### Project Structure

```
app/
├── main.py           # FastAPI app, endpoints, streaming, request handling
├── memory.py         # MemoryStore with SQLite + JSON hybrid backend
├── database.py       # SQLAlchemy models and DatabaseManager
├── retrieval.py      # SemanticRetriever with embeddings
├── tokenbudget.py    # TokenBudgetManager and TokenCounter
├── prompting.py      # MemoryCompactor and system prompt assembly
├── models.py         # Pydantic models (requests, responses, projects)
├── utils.py          # RateLimiter, RequestValidator, monitoring
└── __init__.py
templates/
├── system_prompt.txt          # System role for chat
└── memory_compactor_prompt.txt # Prompt for LLM compaction
data/
├── memory.db         # SQLite database (auto-created)
└── memory/           # Legacy JSON memory (auto-migrated)
```

### Testing

```bash
# Unit tests (to be added)
pytest tests/

# Manual test with curl
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma4:e2b",
    "messages": [{"role": "user", "content": "Hello"}],
    "project_id": "test"
  }'
```

## Troubleshooting

### SQLite Locked Error

**Problem:** `database is locked`

**Solution:** Ensure only one proxy instance writes to the database. For multiple readers, use PostgreSQL.

### Embeddings Not Working

**Problem:** Error in `retrieval.py` embedding generation

**Solution:**
1. Check `sentence-transformers` is installed: `pip install sentence-transformers`
2. First load may download model (~100MB) – give it time
3. Fallback is automatic; heuristic search still works

### Ollama Connection Failed

**Problem:** `HTTPException(502, "Ollama error")`

**Solution:**
1. Verify Ollama is running: `curl http://127.0.0.1:11434/api/tags`
2. Check `OLLAMA_BASE_URL` in `.env`
3. Proxy retries 3 times with exponential backoff (2-10s)

### Memory Not Updating

**Problem:** Conversations don't persist memory state

**Solution:**
1. Check `ENABLE_MEMORY_UPDATE=true` in `.env`
2. Verify `data/` directory is writable
3. Check Docker volume mounts (`docker-compose.yml`)
4. Inspect `data/memory.db` exists: `ls -la data/`

## Roadmap

- [ ] Multi-model support (router logic)
- [ ] PostgreSQL backend for horizontal scaling
- [ ] Redis cache for embeddings
- [ ] Celery task queue for compaction
- [ ] Web UI for project/conversation management
- [ ] Streaming memory compaction
- [ ] Custom embedding models per project
- [ ] Webhook notifications on compaction
- [ ] Analytics dashboard

## License

MIT

## Acknowledgments

- OpenAI for the chat completion API standard
- Ollama for local LLM inference
- Open WebUI for the web interface
- Sentence-Transformers for efficient embeddings
