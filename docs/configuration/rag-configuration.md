# RAG Configuration

The Agents Ensemble RAG knowledge system uses LightRAG as the backend for storing and querying project knowledge.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LIGHTRAG_HOST` | Yes | — | LightRAG server URL (e.g., `http://localhost:8724`) |
| `LIGHTRAG_API_KEY` | No | — | API key for LightRAG authentication |
| `LIGHTRAG_WORKSPACE` | No | `default` | Workspace name for multi-tenant setups |
| `LIGHTRAG_TIMEOUT` | No | `60` | Request timeout in seconds |

## Quick Setup

### 1. Start LightRAG Server

```bash
# Using Docker
docker run -d \
  --name lightrag \
  -p 8724:8724 \
  -v lightrag-data:/root/lightrag_data \
  lightrag/lightrag:latest

# Or using pip
pip install lightrag-hku
python -m lightrag --host 0.0.0.0 --port 8724
```

### 2. Configure Environment

```bash
# In your .env file or environment
export LIGHTRAG_HOST=http://localhost:8724
export LIGHTRAG_API_KEY=your-api-key  # Optional
export LIGHTRAG_WORKSPACE=my-project  # Optional
export LIGHTRAG_TIMEOUT=60            # Optional
```

### 3. Restart Daemon

```bash
# Restart the agents-ensemble daemon to pick up new configuration
python -m daemon
```

## Health Check

The knowledge tools automatically check RAG availability:

```python
# The explore() tool checks if RAG is configured before querying
# If LIGHTRAG_HOST is not set, it returns a helpful configuration message
```

You can manually check:

```bash
curl http://localhost:8724/api/health
```

## Graceful Degradation

When RAG is not configured or unavailable:

| Component | Behavior |
|-----------|----------|
| `explore()` | Returns "RAG not configured" message with setup instructions |
| `experience()` | Returns "RAG not configured" message with setup instructions |
| `inner_soul` redirect | Still redirects knowledge requests, but experience() will report unavailable |
| Other tools | Work normally — no dependency on RAG |
| Agent core memory | Unaffected — soul, user, workflow updates work as before |

This means you can safely run the ensemble without RAG configured — knowledge tools will gracefully report unavailability while all other functionality works.

## Multi-Project Setup

Each project should use a different workspace:

```bash
# Project A
LIGHTRAG_WORKSPACE=project-a

# Project B
LIGHTRAG_WORKSPACE=project-b
```

Workspaces isolate knowledge between projects while sharing the same LightRAG server.

## Performance Tuning

| Setting | Recommendation | Notes |
|---------|---------------|-------|
| `LIGHTRAG_TIMEOUT` | 30-120s | Complex queries may need more time |
| LightRAG server RAM | 4GB+ | Depends on knowledge base size |
| Embedding model | Default (OpenAI) | Configured in LightRAG server |

## Troubleshooting

### Connection Refused

```
ERROR: Cannot connect to http://localhost:8724
```

**Solution:** Ensure LightRAG server is running:
```bash
curl http://localhost:8724/api/health
```

### Timeout Errors

```
ERROR: Request timed out after 60s
```

**Solution:** Increase timeout:
```bash
export LIGHTRAG_TIMEOUT=120
```

### Authentication Errors

```
ERROR: HTTP 401 Unauthorized
```

**Solution:** Check `LIGHTRAG_API_KEY` matches your LightRAG server configuration.

### Empty Results

If `explore()` returns no results even after recording knowledge:

1. Check workspace matches: `LIGHTRAG_WORKSPACE` must be the same for both recording and querying
2. Wait for indexing — LightRAG may need time to process new documents
3. Try different query modes — `explore(query="...", mode="hybrid")` is most comprehensive
