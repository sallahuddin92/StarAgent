# StarAgent – API Reference

## Base URL

StarAgent exposes an OpenAI-compatible API under `/v1`.

- Health base: `http://127.0.0.1:8095`
- OpenAI-compatible base: `http://127.0.0.1:8095/v1`

If you changed the port, substitute `${PORT}` accordingly.

## Authentication

All endpoints except `/health` require Bearer token:

```
Authorization: Bearer {PROXY_API_KEY}
```

Example:
```bash
curl -H "Authorization: Bearer local-dev-key" \
     http://127.0.0.1:8095/v1/chat/completions
```

---

## Endpoints

### Health & Status

#### GET /health
Service health check and feature status.

**Response:**
```json
{
  "ok": true,
  "service": "macagent-proxy",
  "version": "2.0.0",
  "ollama_base_url": "http://127.0.0.1:11434",
  "default_model": "gemma4:e2b",
  "features": {
    "streaming": true,
    "semantic_search": true,
    "memory_compaction": true,
    "token_budgeting": true,
    "per_project_memory": true
  }
}
```

Note: API identity branding strings can be toggled via `STARAGENT_BRAND_API=true` without changing endpoint shapes.

---

### Models

#### GET /v1/models
List available models.

**Response:**
```json
{
  "object": "list",
  "data": [
    {
      "id": "gemma4:e2b",
      "object": "model",
      "created": 1234567890,
      "owned_by": "local"
    }
  ]
}
```

---

### Chat Completions

#### POST /v1/chat/completions
Main chat endpoint with streaming support.

**Request:**
```json
{
  "model": "gemma4:e2b",
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful assistant."
    },
    {
      "role": "user",
      "content": "What is Python?"
    }
  ],
  "temperature": 0.2,
  "max_tokens": 1500,
  "stream": false,
  "project_id": "default",
  "conversation_id": "conv-123",
  "user": "alice@example.com"
}
```

**Parameters:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | `DEFAULT_MODEL` | Model to use (currently ignored, uses default) |
| `messages` | array | required | Chat messages with role (system/user/assistant) |
| `temperature` | float | 0.2 | Sampling temperature (0.0-1.0) |
| `max_tokens` | integer | 1500 | Max tokens in completion |
| `stream` | boolean | false | Enable SSE streaming |
| `project_id` | string | "default" | Project namespace for memory |
| `conversation_id` | string | auto | Unique conversation ID |
| `user` | string | optional | User identifier |

**Response (non-streaming):**
```json
{
  "id": "chatcmpl-abc123def456",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "gemma4:e2b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Python is a high-level programming language..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 245,
    "completion_tokens": 156,
    "total_tokens": 401
  },
  "x_memory": {
    "conversation_id": "conv-123",
    "project_id": "default",
    "retrieved_items": 4,
    "project_summary_items": 8,
    "turn_count": 12,
    "budget_status": {
      "total_tokens_used": 1200,
      "remaining_tokens": 2800,
      "budget_exhausted": false
    }
  }
}
```

**Response (streaming):**
```
data: {"id":"chatcmpl-...", "object":"chat.completion.chunk", "created":1234567890, "model":"gemma4:e2b", "choices":[{"index":0, "delta":{"content":"Python"}, "finish_reason":null}], "x_memory":{...}}

data: {"id":"chatcmpl-...", "object":"chat.completion.chunk", "created":1234567890, "model":"gemma4:e2b", "choices":[{"index":0, "delta":{"content":" is"}, "finish_reason":null}]}

data: {"id":"chatcmpl-...", "object":"chat.completion.chunk", "created":1234567890, "model":"gemma4:e2b", "choices":[{"index":0, "delta":{}, "finish_reason":"stop"}]}

data: [DONE]
```

**Error Responses:**

| Status | Error | Description |
|--------|-------|-------------|
| 400 | invalid request | Missing/invalid parameters |
| 401 | invalid api key | Missing or incorrect Authorization header |
| 429 | token_budget_exceeded | Token budget exhausted for conversation |
| 502 | ollama_error | Ollama server error (retried 3 times) |

---

### Project Management

#### POST /v1/projects
Create a new project with custom settings.

**Request:**
```json
{
  "project_id": "my-project",
  "name": "My Project",
  "description": "Project description",
  "custom_system_prompt": "You are a code reviewer...",
  "custom_compaction_prompt": "Summarize decisions...",
  "embedding_model": "all-MiniLM-L6-v2",
  "token_budget_prompt": 2500,
  "token_budget_completion": 3000
}
```

**Response (201 Created):**
```json
{
  "project_id": "my-project",
  "name": "My Project",
  "description": "Project description",
  "created_at": "2024-01-20T10:15:00",
  "updated_at": "2024-01-20T10:15:00",
  "conversation_count": 0,
  "custom_system_prompt": "You are a code reviewer...",
  "custom_compaction_prompt": "Summarize decisions...",
  "embedding_model": "all-MiniLM-L6-v2",
  "token_budget_prompt": 2500,
  "token_budget_completion": 3000
}
```

#### GET /v1/projects/{project_id}
Retrieve project information.

**Response (200 OK):**
```json
{
  "project_id": "my-project",
  "name": "My Project",
  ...
}
```

**Error:** 404 Project not found

---

### Memory Management

#### POST /v1/memory/compact
Trigger memory compaction for a conversation.

**Request:**
```json
{
  "conversation_id": "conv-123",
  "project_id": "my-project",
  "force": false
}
```

**Parameters:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `conversation_id` | string | required | Target conversation |
| `project_id` | string | "default" | Project namespace |
| `force` | boolean | false | Skip interval check, compact immediately |

**Response (200 OK):**
```json
{
  "conversation_id": "conv-123",
  "project_id": "my-project",
  "items_compacted": 50,
  "items_created": 5,
  "summary": "Found new decision about async/await pattern. Constraints on GPU memory. Issues with timeout handling.",
  "compacted_at": "2024-01-20T10:15:00"
}
```

---

### Token Budget

#### GET /v1/budget/{conversation_id}
Get token usage and budget status.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | "default" | Project namespace |

**Response (200 OK):**
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
  "last_reset": "2024-01-20T10:15:00Z",
  "reset_in_hours": 18
}
```

---

## Examples

### Example 1: Basic Chat (Non-streaming)

```bash
curl -X POST http://127.0.0.1:8095/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma4:e2b",
    "messages": [
      {"role": "user", "content": "What is Docker?"}
    ],
    "temperature": 0.2,
    "project_id": "learning",
    "conversation_id": "docker-101"
  }'
```

### Example 2: Streaming Chat

```bash
curl -X POST http://127.0.0.1:8095/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Write a Python hello world"}
    ],
    "stream": true
  }' \
  -N  # Disable buffering to see streaming in real-time
```

### Example 3: Multi-turn Conversation with Memory

**Turn 1:**
```bash
curl -X POST http://127.0.0.1:8095/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "I am building a React app. Remember: I prefer TypeScript."}
    ],
    "conversation_id": "react-app",
    "project_id": "my-startup"
  }'
```

**Turn 2 (later):**
```bash
curl -X POST http://127.0.0.1:8095/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "How should I structure my components?"}
    ],
    "conversation_id": "react-app",
    "project_id": "my-startup"
  }'
  # System automatically injects: "STYLE: I prefer TypeScript"
```

### Example 4: Trigger Memory Compaction

```bash
curl -X POST http://127.0.0.1:8095/v1/memory/compact \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "long-convo",
    "project_id": "my-startup",
    "force": true
  }'
```

### Example 5: Check Token Budget

```bash
curl http://127.0.0.1:8095/v1/budget/react-app \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json"
```

### Example 6: Create Project

```bash
curl -X POST http://127.0.0.1:8095/v1/projects \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "webapp-2024",
    "name": "Web App 2024",
    "description": "Spring 2024 product launch",
    "token_budget_prompt": 3000,
    "token_budget_completion": 3500
  }'
```

---

## Response Headers

All responses include:

```
Content-Type: application/json
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1234567890
```

---

## Rate Limiting

- **Limit:** 100 requests per 60 seconds per conversation
- **Status Code:** 429 Too Many Requests when exceeded
- **Header:** `X-RateLimit-Reset` contains Unix timestamp of reset time

---

## Common Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request (validation error) |
| 401 | Unauthorized (invalid API key) |
| 404 | Not Found (missing resource) |
| 429 | Too Many Requests (rate limit or budget exhausted) |
| 502 | Bad Gateway (Ollama error; retried automatically) |

---

## OpenAI Compatibility

This API is compatible with OpenAI's Chat Completions format. Libraries that support OpenAI can work with StarAgent by changing the base URL:

```python
from openai import OpenAI

client = OpenAI(
    api_key="local-dev-key",
    base_url="http://127.0.0.1:8095/v1"
)

# Now use normally
response = client.chat.completions.create(
    model="gemma4:e2b",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
    project_id="my-project"
)
```

---

## SDK Examples

### Python (openai library)

```python
from openai import OpenAI

client = OpenAI(
    api_key="local-dev-key",
    base_url="http://127.0.0.1:8095/v1"
)

# Non-streaming
response = client.chat.completions.create(
    model="gemma4:e2b",
    messages=[{"role": "user", "content": "Hello"}],
    project_id="default"
)
print(response.choices[0].message.content)

# Streaming
with client.chat.completions.create(
    model="gemma4:e2b",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### JavaScript (openai library)

```javascript
import OpenAI from "openai";

const openai = new OpenAI({
  apiKey: "local-dev-key",
  baseURL: "http://127.0.0.1:8095/v1",
  dangerouslyAllowBrowser: true
});

// Non-streaming
const message = await openai.chat.completions.create({
  model: "gemma4:e2b",
  messages: [{ role: "user", content: "Hello" }],
  project_id: "default"
});
console.log(message.choices[0].message.content);

// Streaming
const stream = await openai.chat.completions.create({
  model: "gemma4:e2b",
  messages: [{ role: "user", content: "Hello" }],
  stream: true
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content || "");
}
```

---

## Versioning

API version: **2.0.0**

Breaking changes from v1.0:
- None (backward compatible)
- New optional fields: `stream`, `project_id`
- New endpoints: `/v1/projects`, `/v1/memory/compact`, `/v1/budget/{id}`

---

## Rate Limits & Quotas

| Resource | Limit | Reset |
|----------|-------|-------|
| Requests/minute (per conv) | 100 | 60s |
| Token budget (per conv) | Configurable | 24h |
| Message length | 16,000 chars | per request |
| Conversation ID length | 256 chars | per request |

---

**Last Updated:** 2024-01-20  
**API Version:** 2.0.0
