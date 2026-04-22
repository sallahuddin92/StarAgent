# MacAgent Proxy v2.0 – Deployment Quick Start

## 🚀 Get Started in 5 Minutes

### Prerequisites
- Ollama running: `http://127.0.0.1:11434`
- Docker (recommended) or Python 3.12+

### Option 1: Docker (Recommended)

```bash
# Navigate to project directory
cd /path/to/macagent_proxy_starter

# Start the proxy
docker-compose up -d

# Verify it's running
curl http://localhost:8081/health

# View logs
docker-compose logs -f macagent-proxy
```

**Output:**
```json
{
  "ok": true,
  "service": "macagent-proxy",
  "version": "2.0.0",
  "features": {
    "streaming": true,
    "semantic_search": true,
    "memory_compaction": true,
    "token_budgeting": true,
    "per_project_memory": true
  }
}
```

### Option 2: Local Python

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment config
cp .env.example .env

# Run proxy
uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload
```

---

## ⚙️ Configure Open WebUI

1. Open Open WebUI settings
2. Go to **Connections** → **Add Model**
3. Set:
   - **Name:** MacAgent Proxy
   - **Base URL:** `http://localhost:8081/v1`
   - **Model Name:** `gemma4:e2b`
   - **API Key:** `Bearer local-dev-key`
4. Click **Save**
5. Select "MacAgent Proxy" as your model

---

## 🧪 Quick Test

### Test 1: Non-Streaming Chat

```bash
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma4:e2b",
    "messages": [{"role": "user", "content": "What is Docker?"}],
    "temperature": 0.2,
    "project_id": "test"
  }'
```

**Expected:** JSON response with assistant message + memory metadata

### Test 2: Streaming Chat

```bash
curl -X POST http://localhost:8081/v1/chat/completions \
  -H "Authorization: Bearer local-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Write a hello world program"}],
    "stream": true
  }' -N
```

**Expected:** Streaming response with delta chunks ending in `[DONE]`

### Test 3: Check Budget

```bash
curl http://localhost:8081/v1/budget/test-conv \
  -H "Authorization: Bearer local-dev-key"
```

**Expected:** JSON with token usage and remaining budget

---

## 📊 Monitor & Verify

### Check Logs
```bash
# Docker
docker-compose logs -f macagent-proxy

# Local
# Check terminal where you ran uvicorn
```

### Verify Database
```bash
# Check SQLite database was created
ls -lh data/memory.db

# View conversation count
sqlite3 data/memory.db "SELECT COUNT(*) FROM conversations;"

# View memory items
sqlite3 data/memory.db "SELECT category, COUNT(*) FROM memory_items GROUP BY category;"
```

### Check Health
```bash
curl http://localhost:8081/health | jq '.'
```

---

## 🔧 Configuration Tips

### Adjust Token Budgets
Edit `.env`:
```bash
DEFAULT_PROMPT_TOKENS=3000       # Increase for longer context
DEFAULT_COMPLETION_TOKENS=3000   # Increase for longer responses
```

### Enable/Disable Features
```bash
USE_STREAMING=true              # SSE streaming (true/false)
USE_SEMANTIC_SEARCH=true        # Embeddings (true/false)
COMPACTION_INTERVAL=100         # Compact every N turns
```

### Set API Key
```bash
PROXY_API_KEY=my-secret-key     # Bearer token
```

### Adjust Logging
```bash
LOG_LEVEL=DEBUG                 # More verbose logs
```

---

## 🆘 Troubleshooting

### "Connection refused" to Ollama
```bash
# Verify Ollama is running
curl http://127.0.0.1:11434/api/tags

# If not running, start it:
ollama serve

# If using Docker, check network:
docker-compose ps
```

### "database is locked"
```bash
# Only one process can write. Check:
ps aux | grep uvicorn

# Kill extra instances:
pkill -f "uvicorn app.main"

# Restart:
docker-compose restart macagent-proxy
```

### Embeddings not working
```bash
# Check if sentence-transformers installed:
pip install sentence-transformers

# Or disable embeddings in .env:
USE_SEMANTIC_SEARCH=false
```

### Permission denied on data/
```bash
# Ensure data directory is writable:
chmod -R 755 data/
```

---

## 📚 Next Steps

1. **Read the full docs:**
   - `README_v2.md` – Complete feature guide
   - `UPGRADE_GUIDE.md` – Migration from v1.0
   - `API_REFERENCE.md` – Full API documentation

2. **Explore features:**
   - Try streaming: `"stream": true`
   - Create projects: `POST /v1/projects`
   - Trigger compaction: `POST /v1/memory/compact`
   - Check budgets: `GET /v1/budget/{conv_id}`

3. **Monitor & optimize:**
   - Watch logs for errors
   - Check memory growth: `ls -lh data/memory.db`
   - Monitor token usage
   - Tune compaction interval

4. **Deploy to production:**
   - Update `.env` with production settings
   - Use Docker with proper volume mounts
   - Enable health checks
   - Set up logging/monitoring

---

## 📞 Support Resources

- **Health endpoint:** `GET /health` – Service status
- **Documentation:** See `README_v2.md`, `API_REFERENCE.md`, `UPGRADE_GUIDE.md`
- **Logs:** `docker-compose logs` or terminal output
- **Database:** `sqlite3 data/memory.db` for manual inspection

---

## ✅ Deployment Checklist

- [ ] Ollama is running (`curl http://127.0.0.1:11434/api/tags`)
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] `.env` configured (copied from `.env.example`)
- [ ] Proxy started (`docker-compose up -d` or `uvicorn ...`)
- [ ] Health check passes (`curl http://localhost:8081/health`)
- [ ] Open WebUI connected (base URL set to `http://localhost:8081/v1`)
- [ ] Test chat works (can send and receive messages)
- [ ] Memory is updating (check logs for "Retrieved X items")
- [ ] Database exists (`ls data/memory.db`)
- [ ] Logs show no errors (`docker-compose logs`)

---

## 🎉 You're Ready!

The MacAgent Proxy is now running with:
- ✅ Streaming responses
- ✅ Semantic search
- ✅ Token budgeting
- ✅ Per-project memory
- ✅ Intelligent compaction
- ✅ Production hardening

Start chatting with Open WebUI and enjoy long-context conversations! 🚀

---

**Version:** 2.0.0  
**Last Updated:** 2024-01-20
