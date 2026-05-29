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

## Prerequisites

Before deploying LightRAG, ensure you have:

- **Kubernetes cluster** (v1.24+)
- **Helm 3.x** installed and configured
- **PostgreSQL 15+** — for KV storage and document status tracking
- **Neo4j** — for graph storage (entity relationships)
- **Qdrant** — for vector storage (semantic search)
- **LLM API** — OpenAI-compatible endpoint for text generation
- **Embedding API** — OpenAI-compatible endpoint for embeddings

---

## Setup Steps

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

### 2. Configure Your Values

Copy `values.example.yaml` and customize:

```bash
curl -O https://raw.githubusercontent.com/your-repo/values.example.yaml
mv values.example.yaml my-values.yaml
$EDITOR my-values.yaml
```

### 3. Set Required Credentials

Update these environment variables in your values file:

```yaml
env:
  # Authentication
  AUTH_ACCOUNTS: "admin:YOUR_SECURE_PASSWORD"
  TOKEN_SECRET: "YOUR_TOKEN_SECRET"
  LIGHTRAG_API_KEY: "YOUR_API_KEY"

  # LLM Configuration
  LLM_BINDING_HOST: "https://your-llm-provider/v1"
  LLM_BINDING_API_KEY: "YOUR_LLM_API_KEY"

  # Embedding Configuration
  EMBEDDING_BINDING_HOST: "https://your-embedding-provider/v1"
  EMBEDDING_MODEL: "text-embedding-3-small"
  EMBEDDING_BINDING_API_KEY: "YOUR_EMBEDDING_API_KEY"

  # Storage Endpoints
  POSTGRES_HOST: "postgres.lightrag.svc.cluster.local"
  NEO4J_URI: "neo4j://neo4j.lightrag.svc.cluster.local:7687"
  QDRANT_URL: "http://qdrant.lightrag.svc.cluster.local:6333"
```

### 4. Install LightRAG

```bash
helm install lightrag . -n lightrag -f my-values.yaml
```

### 5. Verify Deployment

```bash
kubectl get pods -n lightrag
kubectl logs -n lightrag -l app.kubernetes.io/name=lightrag
```

### 6. Access the API

LightRAG will be available at `http://lightrag.lightrag.svc.cluster.local:9621`

For local access:

```bash
kubectl port-forward -n lightrag svc/lightrag 9621:9621
```

---

## Configuration Reference

### Full Example Values File

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
| **General** | `WEBUI_TITLE` | Web UI title | `LightRAG` |
| **General** | `WEBUI_DESCRIPTION` | Web UI description | — |
| **General** | `WORKSPACE_ISOLATION` | Enable workspace isolation | `true` |
| **Service** | `service.type` | Service type | `ClusterIP` |
| **Service** | `service.port` | HTTP port | `9621` |
| **Resources** | `resources.limits.cpu` | CPU limit | `1000m` |
| **Resources** | `resources.limits.memory` | Memory limit | `4Gi` |
| **Persistence** | `persistence.ragStorage.size` | RAG data storage | `10Gi` |
| **Persistence** | `persistence.inputs.size` | Input documents storage | `5Gi` |
| **Auth** | `AUTH_ACCOUNTS` | User:password pairs | — |
| **Auth** | `TOKEN_SECRET` | JWT signing secret | — |
| **Auth** | `LIGHTRAG_API_KEY` | API access key | — |
| **LLM** | `LLM_BINDING` | LLM provider type | `openai` |
| **LLM** | `LLM_MODEL` | Model name | `quick` |
| **LLM** | `LLM_BINDING_HOST` | API endpoint | — |
| **LLM** | `LLM_BINDING_API_KEY` | API key for LLM service | — |
| **Embedding** | `EMBEDDING_BINDING` | Embedding provider | `openai` |
| **Embedding** | `EMBEDDING_BINDING_HOST` | Embedding API endpoint | — |
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
| **Neo4j** | `NEO4J_USERNAME` | Neo4j username | — |
| **Neo4j** | `NEO4J_PASSWORD` | Neo4j password | — |
| **Qdrant** | `QDRANT_URL` | Qdrant server URL | — |
| **Qdrant** | `QDRANT_COLLECTION` | Qdrant collection name | — |
| **Qdrant** | `QDRANT_API_KEY` | Qdrant API key | — |
| **Entity Types** | `ENTITY_TYPES` | Graph entity categories | See example |

---

## Next Steps

After setup:

1. **Add documents** via the LightRAG API or WebUI
2. **Configure agents-ensemble** to use LightRAG for knowledge retrieval
3. **Test queries** to verify semantic search works correctly

For API documentation, access the Swagger UI at `/docs` when LightRAG is running.
