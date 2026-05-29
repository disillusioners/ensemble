# LightRAG Setup Guide

LightRAG is an optional but **highly recommended** component for the agents-ensemble system. It provides graph-based knowledge retrieval that enables agents to store, query, and manage knowledge graphs.

---

## What is LightRAG?

LightRAG is a graph-based Retrieval-Augmented Generation system that combines:

- **Vector search** for semantic similarity matching
- **Knowledge graphs** for relationship-aware retrieval
- **Full-text indexing** for keyword search
- **Hybrid retrieval** for comprehensive answers

LightRAG indexes documents into a multi-layered graph structure, allowing agents to query your knowledge base with natural language questions.

### Why Use LightRAG?

| Benefit | Description |
|---------|-------------|
| **Persistent Knowledge** | Agents can access indexed documents across sessions |
| **Contextual Answers** | RAG retrieves relevant context before LLM processing |
| **Graph Relationships** | Understands connections between entities |
| **Project Isolation** | Each project gets its own isolated workspace |
| **Graceful Degradation** | System works fine without RAG (RAG tools return errors) |

---

## Setup Steps

### Step 1: Create lightrag.env

Create a file named `lightrag.env` with the following configuration:

```bash
# ============================================
# REQUIRED: agents-ensemble Critical Settings
# ============================================

# CRITICAL: Must be "true" for project isolation
WORKSPACE_ISOLATION="true"

# Entity types for knowledge graph extraction (14 types)
ENTITY_TYPES='["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject","Experience","Decision","Convention"]'

# ============================================
# LLM Configuration
# ============================================

# LLM API endpoint (OpenAI-compatible)
LLM_BINDING_HOST="https://api.openai.com/v1"
LLM_BINDING_API_KEY="your-llm-api-key"

# LLM model to use
LLM_MODEL="gpt-4o"

# LLM binding type (default: openai)
# LLM_BINDING="openai"

# ============================================
# Embedding Configuration
# ============================================

# Embedding API endpoint (OpenAI-compatible)
EMBEDDING_BINDING_HOST="https://api.openai.com/v1"
EMBEDDING_BINDING_API_KEY="your-embedding-api-key"

# Embedding model
EMBEDDING_MODEL="text-embedding-3-small"

# Embedding dimensions (1536 for text-embedding-3-small, 3072 for text-embedding-3-large)
EMBEDDING_DIM="1536"

# Embedding binding type (default: openai)
# EMBEDDING_BINDING="openai"

# ============================================
# Authentication (Recommended for production)
# ============================================

# User accounts for web UI and API access (format: user:password,user2:password2)
AUTH_ACCOUNTS="admin:your-secure-password"

# JWT token secret (generate a strong random string)
TOKEN_SECRET="your-token-secret-here"

# API key for programmatic access
LIGHTRAG_API_KEY="your-api-key"

# ============================================
# Storage Backends (Optional - uses local storage by default)
# ============================================

# For production, consider external storage backends:
# LIGHTRAG_KV_STORAGE="PGKVStorage"
# LIGHTRAG_VECTOR_STORAGE="QdrantVectorDBStorage"
# LIGHTRAG_GRAPH_STORAGE="Neo4JStorage"
# LIGHTRAG_DOC_STATUS_STORAGE="PGDocStatusStorage"
```

### Step 2: Run Docker Container

Start the LightRAG container with your environment file:

```bash
docker run -d \
  --name lightrag \
  -p 9621:9621 \
  --env-file lightrag.env \
  disillusioners/lightrag:v1.6.7
```

#### Verify the container is running:

```bash
docker ps | grep lightrag
curl http://localhost:9621/health
```

You should see a health response indicating the server is running.

### Step 3: Configure agents-ensemble

Add the following to your agents-ensemble `.env` file:

```bash
# LightRAG connection (required for RAG integration)
LIGHTRAG_HOST=http://localhost:9621

# API key (must match LIGHTRAG_API_KEY in lightrag.env)
LIGHTRAG_API_KEY=your-api-key

# Optional: Request timeout in seconds (default: 120)
# LIGHTRAG_TIMEOUT=120

# Optional: Default workspace scope (usually not needed - auto-detected per project)
# LIGHTRAG_WORKSPACE=my-default-workspace
```

### Step 4: Verify Connection

Start (or restart) agents-ensemble. On startup, it will automatically test the RAG connection:

```
INFO: Running RAG auto-test...
INFO: RAG auto-test passed: LightRAG is reachable
```

If the test fails, check the logs for the specific error (see Troubleshooting below).

---

## Configuration Reference

### agents-ensemble Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LIGHTRAG_HOST` | Yes | - | LightRAG server URL (e.g., `http://localhost:9621`) |
| `LIGHTRAG_API_KEY` | No | - | API key for authentication |
| `LIGHTRAG_TIMEOUT` | No | `120` | Request timeout in seconds |
| `LIGHTRAG_WORKSPACE` | No | - | Default workspace scope (usually auto-detected) |

### LightRAG Server Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WORKSPACE_ISOLATION` | **Yes** | `false` | Must be `"true"` for project isolation |
| `ENTITY_TYPES` | **Yes** | - | JSON array of entity types for graph extraction |
| `LLM_BINDING_HOST` | Yes | - | LLM API endpoint (OpenAI-compatible) |
| `LLM_BINDING_API_KEY` | Yes | - | LLM API key |
| `LLM_MODEL` | No | `quick` | LLM model name |
| `LLM_BINDING` | No | `openai` | LLM binding type |
| `EMBEDDING_BINDING_HOST` | Yes | - | Embedding API endpoint |
| `EMBEDDING_BINDING_API_KEY` | Yes | - | Embedding API key |
| `EMBEDDING_MODEL` | Yes | - | Embedding model name |
| `EMBEDDING_DIM` | Yes | `1536` | Embedding vector dimensions |
| `EMBEDDING_BINDING` | No | `openai` | Embedding binding type |
| `AUTH_ACCOUNTS` | No | - | Web UI user:password pairs |
| `TOKEN_SECRET` | No | - | JWT signing secret |
| `LIGHTRAG_API_KEY` | No | - | API key for programmatic access |

### Entity Types (14 types)

The default entity types are tuned for software project knowledge:

| Type | Description |
|------|-------------|
| `Person` | People, developers, team members |
| `Creature` | (Available for domain-specific use) |
| `Organization` | Companies, teams, departments |
| `Location` | File paths, directories, URLs |
| `Event` | Meetings, releases, deployments |
| `Concept` | Architectural patterns, design principles |
| `Method` | Functions, algorithms, procedures |
| `Content` | Documentation, comments, descriptions |
| `Data` | Database schemas, data structures |
| `Artifact` | Files, configurations, builds |
| `NaturalObject` | (Available for domain-specific use) |
| `Experience` | Lessons learned, decisions made |
| `Decision` | Architectural decisions, choices |
| `Convention` | Coding standards, practices |

---

## Available Agent Tools

The RAG integration provides **16 low-level tools** for agents, plus **2 high-level knowledge tools** (see below) that wrap the low-level tools with agent-powered intelligence.

### Low-Level RAG Tools (16 tools)

#### Text Insertion

| Tool | Description |
|------|-------------|
| `rag_insert_text` | Insert a single text into the knowledge graph |
| `rag_insert_texts` | Insert multiple texts at once |

#### Querying

| Tool | Description |
|------|-------------|
| `rag_query` | Query the knowledge graph and get a generated LLM response |
| `rag_query_data` | Query for structured data (entities, relations, chunks) |
| `rag_search_labels` | Search for labels in the knowledge graph |

#### Graph Operations

| Tool | Description |
|------|-------------|
| `rag_get_graph` | Get the knowledge graph or a filtered subgraph |

#### Entity Operations

| Tool | Description |
|------|-------------|
| `rag_create_entity` | Create a new entity |
| `rag_get_entity` | Get an entity by name |
| `rag_update_entity` | Update an existing entity |
| `rag_merge_entities` | Merge multiple entities into one |
| `rag_delete_entity` | Delete an entity |

#### Relation Operations

| Tool | Description |
|------|-------------|
| `rag_create_relation` | Create a relation between two entities |
| `rag_delete_relation` | Delete a relation |

#### Document Operations

| Tool | Description |
|------|-------------|
| `rag_delete_docs` | Delete documents by IDs |
| `rag_list_docs` | List documents with pagination |

#### Status Operations

| Tool | Description |
|------|-------------|
| `rag_track_status` | Track the status of async insert operations |

### High-Level Knowledge Tools

These tools are **NOT part of the 16 RAG tools** above — they are higher-level wrappers in `knowledge_tools.py` that delegate to specialized agents (Explorer, Experiencer) for enhanced functionality:

| Tool | Description |
|------|-------------|
| `explore()` | Query the knowledge base using the Explorer agent. Searches RAG and optionally browses files. Supports modes: `local`, `global`, `hybrid`, `naive`. Automatically queues knowledge updates when new insights are found. |
| `experience()` | Record new knowledge using the Experiencer agent. Analyzes text, extracts entities and relationships, inserts into RAG. Runs asynchronously in background. |

**Usage Example:**

```
explore(query="What authentication patterns are used in this project?")
experience(text="We decided to use JWT tokens for API authentication after evaluating session cookies.")
```

---

## Workspace Scoping

agents-ensemble automatically scopes RAG operations to the current project.

### How It Works

1. When an agent calls a RAG tool, the system extracts the `project.name` from the instance metadata
2. The project name is sent as the `LIGHTRAG-WORKSPACE` header
3. LightRAG uses this to scope all operations to that workspace

### Workspace Sanitization

LightRAG sanitizes workspace names to alphanumeric + underscore only:
- Hyphens become underscores: `my-project` → `my_project`
- Special characters become underscores: `proj@123!` → `proj_123_`

### Multi-Project Isolation

With `WORKSPACE_ISOLATION="true"` (required), each project maintains its own:
- Knowledge graph (entities, relations)
- Indexed documents
- Query results

This prevents cross-contamination between projects.

### Manual Workspace Override

If needed, you can set a default workspace in agents-ensemble:

```bash
LIGHTRAG_WORKSPACE=my-default-workspace
```

---

## Graceful Degradation

The system is designed to work without RAG:

### When LightRAG is Unavailable

| Behavior | Details |
|----------|---------|
| **Startup** | Auto-test runs (15s timeout). If it fails, RAG is disabled and a warning is logged. |
| **RAG Tools** | Return error messages like "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable." |
| **Other Tools** | All other agent tools work normally. |
| **System Status** | Fully functional - just without knowledge retrieval. |

### Manual RAG Control

You can manually enable/disable RAG at runtime:

```python
from daemon.rag import enable_rag, disable_rag, is_rag_enabled

# Check if RAG is enabled
if is_rag_enabled():
    print("RAG is available")

# Disable RAG (e.g., after detecting issues)
disable_rag()

# Re-enable RAG (bypasses auto-test)
enable_rag()
```

---

## Troubleshooting

### Connection Refused

**Error:** `RAG auto-test failed: connection refused`

**Causes:**
- LightRAG container not running
- Wrong port in `LIGHTRAG_HOST`
- Firewall blocking the port

**Solutions:**
```bash
# Check if container is running
docker ps | grep lightrag

# Check port accessibility
curl http://localhost:9621/health

# Restart container if needed
docker restart lightrag
```

### Timeout Errors

**Error:** `RAG auto-test failed: timeout after 15.0s`

**Causes:**
- LightRAG server is slow to respond
- Network latency
- LLM/embedding service is slow

**Solutions:**
```bash
# Increase timeout in agents-ensemble .env
LIGHTRAG_TIMEOUT=300

# Check LightRAG logs
docker logs lightrag
```

### 401 Unauthorized

**Error:** `RAG auto-test failed: LightRAG error 401`

**Causes:**
- `LIGHTRAG_API_KEY` doesn't match between agents-ensemble and LightRAG
- Missing API key when LightRAG requires authentication

**Solutions:**
```bash
# Ensure keys match in both .env files
# lightrag.env:
LIGHTRAG_API_KEY="your-key"

# agents-ensemble .env:
LIGHTRAG_API_KEY=your-key
```

### Workspace Errors

**Error:** `RAG auto-test failed` with workspace-related message

**Causes:**
- `WORKSPACE_ISOLATION` not set to `"true"`
- Invalid characters in project name

**Solutions:**
```bash
# Ensure WORKSPACE_ISOLATION is set
WORKSPACE_ISOLATION="true"

# Check project names for invalid characters
# Only alphanumeric + underscore allowed in workspace names
```

### Empty Query Results

**Error:** Queries return no results even with known content

**Causes:**
- Documents not yet indexed (async processing)
- Wrong workspace
- No matching content

**Solutions:**
```bash
# Check pipeline status
curl http://localhost:9621/documents/pipeline_status

# List documents
curl http://localhost:9621/documents/paginated -X POST -H "Content-Type: application/json" -d '{"page": 1, "page_size": 50}'

# Check track status for pending indexing
# (use rag_track_status tool with track_id from insert response)
```

### Container Crash or OOM

**Error:** LightRAG container keeps restarting or running out of memory

**Solutions:**
```bash
# Increase memory limits
docker update --memory 4g --memory-swap 4g lightrag

# Or use docker-compose with resource limits
```

---

## Quick Reference

### Minimal lightrag.env

```bash
WORKSPACE_ISOLATION="true"
ENTITY_TYPES='["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject","Experience","Decision","Convention"]'
LLM_BINDING_HOST="https://api.openai.com/v1"
LLM_BINDING_API_KEY="your-key"
LLM_MODEL="gpt-4o"
EMBEDDING_BINDING_HOST="https://api.openai.com/v1"
EMBEDDING_BINDING_API_KEY="your-key"
EMBEDDING_MODEL="text-embedding-3-small"
```

### Minimal Docker Run

```bash
docker run -d \
  --name lightrag \
  -p 9621:9621 \
  -e WORKSPACE_ISOLATION="true" \
  -e ENTITY_TYPES='["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject","Experience","Decision","Convention"]' \
  -e LLM_BINDING_HOST="https://api.openai.com/v1" \
  -e LLM_BINDING_API_KEY="your-key" \
  -e EMBEDDING_BINDING_HOST="https://api.openai.com/v1" \
  -e EMBEDDING_BINDING_API_KEY="your-key" \
  -e EMBEDDING_MODEL="text-embedding-3-small" \
  disillusioners/lightrag:v1.6.7
```

### agents-ensemble .env (minimal)

```bash
LIGHTRAG_HOST=http://localhost:9621
```
