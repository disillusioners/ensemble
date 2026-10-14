# LightRAG Document Ingestion Deep Dive

## Architecture Overview
- LightRAG is at `.inspiration-projects/LightRAG-main/`
- 4 storage types: Graph, Vector, KV, DocStatus — all independently configurable
- Default setup: zero external dependencies (NetworkX + NanoVectorDB + JSON files)
- Fully automatic: POST raw text → LightRAG handles chunking, entity extraction, relationship extraction, graph construction

## API Endpoints (FastAPI)
- `POST /documents/text` — single text document
- `POST /documents/texts` — batch text documents
- `POST /documents/upload` — file upload (40+ file types)
- `GET /documents/track_status/{track_id}` — check processing status
- Returns `InsertResponse` with `{status, message, track_id}`

## Insert Pipeline
1. Token-based chunking (default: 1200 tokens, 100 overlap)
2. LLM entity extraction (prompt in `lightrag/prompt.py`)
3. LLM relationship extraction (same LLM call)
4. Parse output → merge nodes/edges → storage

## Insert vs Query Modes
- **NO insert modes** — naive/local/global/hybrid are QUERY modes only
- QueryParam.mode: local, global, hybrid, naive, mix (default), bypass

## Async Processing
- API uses FastAPI BackgroundTasks — fully async
- Returns track_id immediately, processing in background
- Poll `/documents/track_status/{track_id}` for progress

## Storage Backends (all in `lightrag/kg/`)
- Graph: NetworkX (default), Neo4j, PostgreSQL+AGE, MongoDB, Memgraph, OpenSearch
- Vector: NanoVectorDB (default), FAISS, Milvus, Qdrant, PostgreSQL, MongoDB, OpenSearch
- KV: JSON (default), Redis, PostgreSQL, MongoDB, OpenSearch
- 3 vector namespaces: entities_vdb, relationships_vdb, chunks_vdb

## Key Takeaway for agents-ensemble
A consumer CAN simply POST raw text and have LightRAG handle everything automatically.
No pre-processing required — LLM handles entity/relationship extraction.
