# MacAgent Proxy Starter

Thin local middleware for **Open WebUI -> Memory Proxy -> Ollama**.

This starter is designed for a **small local model** such as `gemma4:e2b`.
Instead of replaying the full raw chat history every turn, it:

1. stores a compact project memory
2. retrieves relevant prior context
3. assembles a cleaner prompt packet
4. forwards the request to Ollama
5. updates compact memory after the response

## What this gives you

- normal chat UX in Open WebUI
- background memory compaction
- long project continuity without stuffing raw history every turn
- local-first architecture

## Included

- `app/main.py` - FastAPI proxy with OpenAI-compatible `/v1/chat/completions`
- `app/memory.py` - compact memory store and retrieval logic
- `app/prompting.py` - prompt assembly helpers
- `app/models.py` - request/response models
- `templates/system_prompt.txt` - system policy for small-model prompt continuity
- `templates/memory_compactor_prompt.txt` - optional future compactor template
- `data/memory/` - JSON memory store
- `.env.example` - environment variables
- `docker-compose.yml` - proxy container starter
- `requirements.txt`

## Architecture

```text
Open WebUI -> Local Proxy -> Ollama
                 |
                 +-> compact memory store (JSON)
```

## Quick start

### 1. Run Ollama

Make sure Ollama is running locally and the model exists.

Example:

```bash
ollama serve
ollama pull gemma4:e2b
```

### 2. Create env file

```bash
cp .env.example .env
```

### 3. Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload
```

### 4. Connect Open WebUI

Point Open WebUI to:

```text
http://host.docker.internal:8081/v1
```

If you run everything on the same machine without Docker networking issues, use:

```text
http://127.0.0.1:8081/v1
```

Set any placeholder API key if the UI requires one.

## How memory works

Each conversation gets a JSON file in `data/memory/`:

- `project_summary`
- `decisions`
- `constraints`
- `issues`
- `style_preferences`
- `archive_turns`

The proxy builds each request from:

1. base system prompt
2. compact working memory
3. retrieved relevant history snippets
4. recent user request

## Current limitations

This starter is intentionally simple:

- JSON memory, not vector DB
- keyword retrieval, not embeddings
- lightweight heuristic compaction
- single-node local setup

## Best next upgrades for Antigravity

1. replace JSON retrieval with SQLite + embeddings
2. add explicit `/memory/lock` and `/memory/reset` endpoints
3. separate project memory from conversation memory
4. add token estimation and auto-trimming
5. support tool routing and local code execution
6. add import of repo summaries / docs / logs into memory

## Suggested Open WebUI usage

Use this as a normal chat endpoint. The proxy silently handles:

- context carry-forward
- compaction
- retrieval
- prompt assembly

You can still help the system by writing messages like:

- `remember this decision: use local sqlite`
- `replace previous plan with this`
- `ignore the old timezone workaround`

