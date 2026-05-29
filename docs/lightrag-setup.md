# LightRAG Setup Guide

LightRAG is an optional but **highly recommended** component for the agents-ensemble system.

---

## What is LightRAG

LightRAG is a graph-based Retrieval-Augmented Generation system that enables semantic document querying. It combines:

- **Vector search** for semantic similarity matching
- **Knowledge graphs** for relationship-aware retrieval
- **Full-text indexing** for keyword search
- **Hybrid retrieval** for comprehensive answers

LightRAG indexes documents into a multi-layered graph structure, allowing agents to query your knowledge base with natural language questions.

---

## Why Recommended

For a persistent multi-agent daemon like agents-ensemble, LightRAG provides:

| Benefit | Description |
|---------|-------------|
| **Persistent Knowledge** | Agents can access indexed documents across sessions |
| **Contextual Answers** | RAG retrieves relevant context before LLM processing |
| **Scalable Vector Search** | Handles large document collections efficiently |
| **Graph Relationships** | Understands connections between entities |
| **API-First Design** | Easy integration via HTTP endpoints |

Without RAG, agents rely solely on their training data and immediate context. LightRAG bridges this gap by enabling real-time knowledge retrieval.

---

## Choose Your Setup Path

### Light / Local *(Recommended Starting Point)*

Simple single-instance deployment. No Kubernetes required. All you need is the LightRAG container and environment variables.

**Best for:** Development, small teams, single-server deployments.

### Full / K8s *(Advanced)*

Production-grade deployment with Helm, separate storage backends, and horizontal scaling.

**Best for:** Production workloads, multiple workspaces, high availability needs.

---

## CRITICAL Configuration

Both setup paths require these two environment variables:

### `WORKSPACE_ISOLATION: "true"`

**Required for agents-ensemble.** This prevents cross-contamination between workspaces in the knowledge graph.

Without isolation, documents and entities from different projects get mixed together, causing agents to retrieve irrelevant context and produce confused responses. This setting ensures each workspace maintains its own isolated graph.

### `ENTITY_TYPES`

Defines the entity types used for knowledge graph extraction. These categories determine what "things" LightRAG identifies when processing documents.

```bash
ENTITY_TYPES='["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject","Experience","Decision","Convention"]'
```

**This specific list is tuned for the agents-ensemble domain** — it covers the entity types relevant to software projects, teams, and workflows. Do not change unless you have specific domain requirements.

### All Other Variables Are Optional

All other environment variables have sensible defaults. Only tune them when you have specific requirements:

| Category | What to Configure |
|----------|-------------------|
| **Auth** | Set `AUTH_ACCOUNTS`, `TOKEN_SECRET`, `LIGHTRAG_API_KEY` to secure your instance |
| **LLM** | Configure `LLM_BINDING_HOST` and `LLM_BINDING_API_KEY` for your LLM provider |
| **Embedding** | Configure `EMBEDDING_BINDING_HOST`, `EMBEDDING_MODEL`, and API key |
| **Storage** | Only needed for Full/K8s setup with external databases |

---

## Light / Local Setup

### Prerequisites

- **Docker** or container runtime
- **LLM API** — OpenAI-compatible endpoint for text generation
- **Embedding API** — OpenAI-compatible endpoint for embeddings

### Quick Start

1. **Create environment file** (`lightrag.env`):

```bash
# Required for agents-ensemble
WORKSPACE_ISOLATION="true"
ENTITY_TYPES='["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject","Experience","Decision","Convention"]'

# LLM Configuration
LLM_BINDING_HOST="https://api.openai.com/v1"
LLM_BINDING_API_KEY="your-llm-api-key"
LLM_MODEL="gpt-4o"

# Embedding Configuration
EMBEDDING_BINDING_HOST="https://api.openai.com/v1"
EMBEDDING_MODEL="text-embedding-3-small"
EMBEDDING_BINDING_API_KEY="your-embedding-api-key"
```

2. **Run the container:**

```bash
docker run -d \
  --name lightrag \
  -p 9621:9621 \
  --env-file lightrag.env \
  disillusioners/lightrag:v1.6.7
```

3. **Verify:**

```bash
curl http://localhost:9621/health
```

### Minimal Example

For testing, you can start with just the critical variables and rely on defaults:

```bash
docker run -d \
  --name lightrag \
  -p 9621:9621 \
  -e WORKSPACE_ISOLATION="true" \
  -e ENTITY_TYPES='["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject","Experience","Decision","Convention"]' \
  -e LLM_BINDING_HOST="https://api.openai.com/v1" \
  -e LLM_BINDING_API_KEY="your-key" \
  -e EMBEDDING_BINDING_HOST="https://api.openai.com/v1" \
  -e EMBEDDING_MODEL="text-embedding-3-small" \
  -e EMBEDDING_BINDING_API_KEY="your-key" \
  disillusioners/lightrag:v1.6.7
```

---

## Full / K8s Setup

### Prerequisites

- **Kubernetes cluster** (v1.24+)
- **Helm 3.x** installed and configured
- **PostgreSQL 15+** — for KV storage and document status tracking
- **Neo4j** — for graph storage (entity relationships)
- **Qdrant** — for vector storage (semantic search)
- **LLM API** — OpenAI-compatible endpoint for text generation
- **Embedding API** — OpenAI-compatible endpoint for embeddings

### 1. Prepare Infrastructure

Deploy the required storage backends:

```bash
# PostgreSQL
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install postgres bitnami/postgresql -n lightrag --create-namespace

# Neo4j
helm install neo4j neo4j-community -n lightrag

# Qdrant
helm install qdrant qdrant/qdrant -n lightrag
```

### 2. Install LightRAG with Helm

```bash
helm repo add lightrag https://charts.example.com
helm install lightrag lightrag/lightrag -n lightrag
```

### 3. Configure Values

Create `my-values.yaml` with at minimum:

```yaml
env:
  # Required for agents-ensemble
  WORKSPACE_ISOLATION: "true"
  ENTITY_TYPES: '["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject","Experience","Decision","Convention"]'

  # Auth (set secure values)
  AUTH_ACCOUNTS: "admin:YOUR_SECURE_PASSWORD"
  TOKEN_SECRET: "YOUR_TOKEN_SECRET"
  LIGHTRAG_API_KEY: "YOUR_API_KEY"

  # LLM
  LLM_BINDING_HOST: "https://your-llm-provider/v1"
  LLM_BINDING_API_KEY: "YOUR_LLM_API_KEY"

  # Embedding
  EMBEDDING_BINDING_HOST: "https://your-embedding-provider/v1"
  EMBEDDING_MODEL: "text-embedding-3-small"
  EMBEDDING_BINDING_API_KEY: "YOUR_EMBEDDING_API_KEY"

  # Storage (K8s service discovery)
  POSTGRES_HOST: "postgres.lightrag.svc.cluster.local"
  NEO4J_URI: "neo4j://neo4j.lightrag.svc.cluster.local:7687"
  QDRANT_URL: "http://qdrant.lightrag.svc.cluster.local:6333"
```

Apply:

```bash
helm upgrade lightrag lightrag/lightrag -n lightrag -f my-values.yaml
```

### 4. Verify Deployment

```bash
kubectl get pods -n lightrag
kubectl logs -n lightrag -l app.kubernetes.io/name=lightrag
```

### 5. Access the API

```bash
# Within cluster
curl http://lightrag.lightrag.svc.cluster.local:9621/health

# Local access
kubectl port-forward -n lightrag svc/lightrag 9621:9621
curl http://localhost:9621/health
```

---

## Configuration Reference (Full Options)

For the complete list of all available configuration options, see the Helm chart's `values.example.yaml` or the [LightRAG GitHub repository](https://github.com/your-repo/lightrag).

### Full Configuration Example

This is a complete Helm values file with all available options:

```yaml
replicaCount: 1

image:
  repository: disillusioners/lightrag
  tag: v1.6.7
  imagePullSecrets: []

updateStrategy:
  type: Recreate

service:
  type: ClusterIP
  port: 9621

resources:
  limits:
    cpu: 1000m
    memory: 4Gi
  requests:
    cpu: 500m
    memory: 1Gi

persistence:
  enabled: true
  ragStorage:
    size: 10Gi
  inputs:
    size: 5Gi

envFrom:
  configmaps: []
  secrets: []

env:
  HOST: 0.0.0.0
  PORT: 9621
  WEBUI_TITLE: LightRAG
  WEBUI_DESCRIPTION: Graph-based RAG system for semantic document querying
  WORKSPACE_ISOLATION: "true"
  ENTITY_TYPES: '["Person","Creature","Organization","Location","Event","Concept","Method","Content","Data","Artifact","NaturalObject","Experience","Decision","Convention"]'

  AUTH_ACCOUNTS: "admin:example_password"
  TOKEN_SECRET: "example_token_secret"
  LIGHTRAG_API_KEY: "example_api_key"


  LLM_BINDING: openai
  LLM_MODEL: quick
  LLM_BINDING_HOST: https://example.llm.api/v1
  LLM_BINDING_API_KEY: example_llm_api_key

  EMBEDDING_BINDING: openai
  EMBEDDING_BINDING_HOST: https://example.embedding.api/v1
  EMBEDDING_MODEL: text-embedding-3-small
  EMBEDDING_DIM: "1536"
  EMBEDDING_BINDING_API_KEY: example_embedding_api_key

  LIGHTRAG_KV_STORAGE: PGKVStorage
  LIGHTRAG_VECTOR_STORAGE: QdrantVectorDBStorage
  LIGHTRAG_GRAPH_STORAGE: Neo4JStorage
  LIGHTRAG_DOC_STATUS_STORAGE: PGDocStatusStorage

  POSTGRES_HOST: example.postgres.example
  POSTGRES_PORT: "5432"
  POSTGRES_USER: lightrag
  POSTGRES_PASSWORD: example_password
  POSTGRES_DATABASE: lightrag

  NEO4J_URI: neo4j://example.neo4j.example:7687
  NEO4J_USERNAME: neo4j
  NEO4J_PASSWORD: example_password

  QDRANT_URL: http://example.qdrant.example:6333
  QDRANT_COLLECTION: lightrag
  QDRANT_API_KEY: example_qdrant_api_key

nodeSelector: {}

tolerations: []

affinity: {}
```

### Key Configuration Options

| Section | Key | Description | Default |
|---------|-----|-------------|---------|
| **General** | `HOST` | Server host | `0.0.0.0` |
| **General** | `PORT` | Server port | `9621` |
| **General** | `WORKSPACE_ISOLATION` | Enable workspace isolation | `true` |
| **Auth** | `AUTH_ACCOUNTS` | User:password pairs | — |
| **Auth** | `TOKEN_SECRET` | JWT signing secret | — |
| **Auth** | `LIGHTRAG_API_KEY` | API access key | — |
| **LLM** | `LLM_BINDING` | LLM provider type | `openai` |
| **LLM** | `LLM_MODEL` | Model name | `quick` |
| **LLM** | `LLM_BINDING_HOST` | API endpoint | — |
| **LLM** | `LLM_BINDING_API_KEY` | API key for LLM service | — |
| **Embedding** | `EMBEDDING_BINDING` | Embedding provider | `openai` |
| **Embedding** | `EMBEDDING_HOST` | Embedding API endpoint | — |
| **Embedding** | `EMBEDDING_MODEL` | Embedding model | `text-embedding-3-small` |
| **Embedding** | `EMBEDDING_DIM` | Vector dimensions | `1536` |
| **Embedding** | `EMBEDDING_BINDING_API_KEY` | API key for embedding service | — |
| **Storage** | `LIGHTRAG_KV_STORAGE` | KV backend | `PGKVStorage` |
| **Storage** | `LIGHTRAG_VECTOR_STORAGE` | Vector backend | `QdrantVectorDBStorage` |
| **Storage** | `LIGHTRAG_GRAPH_STORAGE` | Graph backend | `Neo4JStorage` |
| **Storage** | `LIGHTRAG_DOC_STATUS_STORAGE` | Document status storage | `PGDocStatusStorage` |
| **PostgreSQL** | `POSTGRES_HOST` | PostgreSQL host | — |
| **PostgreSQL** | `POSTGRES_PORT` | PostgreSQL port | `5432` |
| **PostgreSQL** | `POSTGRES_USER` | PostgreSQL user | — |
| **PostgreSQL** | `POSTGRES_PASSWORD` | PostgreSQL password | — |
| **PostgreSQL** | `POSTGRES_DATABASE` | PostgreSQL database name | — |
| **Neo4j** | `NEO4J_URI` | Neo4j connection URI | — |
| **Neo4j** | `NEO4J_USERNAME` | Neo4j username | `neo4j` |
| **Neo4j** | `NEO4J_PASSWORD` | Neo4j password | — |
| **Qdrant** | `QDRANT_URL` | Qdrant server URL | — |
| **Qdrant** | `QDRANT_COLLECTION` | Qdrant collection name | `lightrag` |
| **Qdrant** | `QDRANT_API_KEY` | Qdrant API key | — |
| **Entity Types** | `ENTITY_TYPES` | Graph entity categories | See critical config |

### Storage Backend Options

LightRAG supports pluggable storage backends:

| Storage Type | Options |
|--------------|---------|
| **KV Storage** | `PGKVStorage` (PostgreSQL), `RedisKVStorage` |
| **Vector Storage** | `QdrantVectorDBStorage`, `MilvusVectorDBStorage`, ` ChromaVectorDBStorage` |
| **Graph Storage** | `Neo4JStorage`, `NetworkXStorage` |
| **Doc Status** | `PGDocStatusStorage` |

---

## Next Steps

After setup:

1. **Add documents** via the LightRAG API or WebUI
2. **Configure agents-ensemble** to use LightRAG for knowledge retrieval
3. **Test queries** to verify semantic search works correctly

For API documentation, access the Swagger UI at `/docs` when LightRAG is running.
