# Phase 2: RAG HTTP Client Module

## Objective

Create the `daemon/rag/` module — an async HTTP client (httpx) for the external LightRAG API. Handles configuration from environment variables, authentication via headers, workspace isolation, Pydantic schemas for request/response, and graceful degradation when LightRAG is not configured.

## Coupling

- **Depends on**: None (independent module)
- **Coupling type**: — (can run parallel with Phase 1)
- **Shared files with other phases**:
  - `daemon/rag/client.py` — imported by Phase 3 (rag_tools.py)
  - `daemon/rag/schemas.py` — imported by Phase 3 (rag_tools.py)
- **Why this coupling**: Phase 3's RAG tools directly instantiate `AsyncLightRAGClient`

## Context

- LightRAG is an external service — all interaction via HTTP REST API
- Workspace isolation via `LIGHTRAG-WORKSPACE` header for per-project scoping
- If ENV vars not set, RAG features are disabled (not an error)
- Client should be reusable — singleton pattern or module-level instance

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create RAG configuration | ENV-based config with defaults, disabled state detection | `daemon/rag/config.py` |
| 2 | Create exceptions | RAGError, RAGConnectionError, RAGTimeoutError, RAGNotConfiguredError | `daemon/rag/exceptions.py` |
| 3 | Create Pydantic schemas | Request/response models for all LightRAG endpoints | `daemon/rag/schemas.py` |
| 4 | Create endpoint constants | URL path constants for all LightRAG endpoints | `daemon/rag/endpoints.py` |
| 5 | Create async HTTP client | httpx-based client with auth headers, workspace header, retry logic | `daemon/rag/client.py` |
| 6 | Create module exports | `__init__.py` with public API | `daemon/rag/__init__.py` |
| 7 | Write unit tests | Client tests with httpx mock, schema validation | `tests/unit/rag/test_client.py` |

### Task 2.1: RAG Configuration

**File**: `daemon/rag/config.py` (NEW)

```python
"""RAG configuration from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RAGConfig:
    """LightRAG connection configuration.
    
    All values come from environment variables.
    If LIGHTRAG_HOST is not set, RAG is disabled.
    """
    host: str | None = None
    api_key: str | None = None
    workspace: str = "default"
    timeout: float = 120.0
    
    @property
    def is_configured(self) -> bool:
        """Check if LightRAG is properly configured."""
        return bool(self.host)
    
    @property
    def base_url(self) -> str:
        """Get the full base URL for the LightRAG API."""
        return self.host.rstrip("/") if self.host else ""
    
    @classmethod
    def from_env(cls) -> "RAGConfig":
        """Load configuration from environment variables."""
        return cls(
            host=os.getenv("LIGHTRAG_HOST"),
            api_key=os.getenv("LIGHTRAG_API_KEY"),
            workspace=os.getenv("LIGHTRAG_WORKSPACE", "default"),
            timeout=float(os.getenv("LIGHTRAG_TIMEOUT", "120")),
        )
```

### Task 2.2: Custom Exceptions

**File**: `daemon/rag/exceptions.py` (NEW)

```python
"""RAG-specific exceptions."""


class RAGError(Exception):
    """Base exception for RAG operations."""
    pass


class RAGNotConfiguredError(RAGError):
    """Raised when RAG operations are attempted without configuration."""
    def __init__(self):
        super().__init__("LightRAG is not configured. Set LIGHTRAG_HOST environment variable.")


class RAGConnectionError(RAGError):
    """Raised when connection to LightRAG server fails."""
    pass


class RAGTimeoutError(RAGError):
    """Raised when a RAG request times out."""
    pass


class RAGResponseError(RAGError):
    """Raised when LightRAG returns an error response."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"LightRAG error {status_code}: {detail}")
```

### Task 2.3: Pydantic Schemas

**File**: `daemon/rag/schemas.py` (NEW)

Define Pydantic models for all LightRAG API endpoints. Key schemas:

**Requests:**
- `InsertTextRequest` — text, description, optional metadata
- `InsertTextsRequest` — list of texts for bulk insert
- `QueryRequest` — query, mode (local/global/hybrid/naive), optional stream
- `QueryDataRequest` — query with structured data return (no LLM)
- `LabelSearchRequest` — label, max_results
- `CreateEntityRequest` — entity name, description, entity_type, metadata
- `CreateRelationRequest` — source, target, description, relation_type, metadata
- `UpdateEntityRequest` — entity_name, new fields
- `MergeEntitiesRequest` — source_entities, target_entity
- `DeleteDocsRequest` — document IDs to delete

**Responses:**
- `InsertResponse` — track_id for async processing
- `QueryResponse` — response text
- `QueryDataResponse` — structured data (entities, relations)
- `LabelSearchResponse` — matching labels
- `GraphResponse` — subgraph data
- `TrackStatusResponse` — processing status
- `ListDocsResponse` — paginated document list
- `PipelineStatusResponse` — pipeline processing status

### Task 2.4: Endpoint Constants

**File**: `daemon/rag/endpoints.py` (NEW)

```python
"""LightRAG API endpoint paths."""

# Text insertion
INSERT_TEXT = "/documents/text"
INSERT_TEXTS = "/documents/texts"

# Querying
QUERY = "/query"
QUERY_DATA = "/query/data"

# Graph operations
SEARCH_LABELS = "/graph/label/search"
GET_GRAPH = "/graphs"

# Entity operations
CREATE_ENTITY = "/graph/entity/create"
UPDATE_ENTITY = "/graph/entity/update"
MERGE_ENTITIES = "/graph/entity/merge"
DELETE_ENTITY = "/documents/delete_entity"

# Relation operations
CREATE_RELATION = "/graph/relation/create"
DELETE_RELATION = "/documents/delete_relation"

# Document operations
DELETE_DOCS = "/documents/delete_document"
LIST_DOCS = "/documents/paginated"

# Status
TRACK_STATUS = "/documents/track_status/{track_id}"
PIPELINE_STATUS = "/pipeline/status"
```

### Task 2.5: Async HTTP Client

**File**: `daemon/rag/client.py` (NEW)

Key design:
- `httpx.AsyncClient` with connection pooling
- Auto-injects `X-API-Key` and `LIGHTRAG-WORKSPACE` headers
- Graceful error: returns `RAGNotConfiguredError` if not configured
- Timeout handling with configurable timeout
- Retry on connection errors (1 retry)
- JSON parsing with error handling

```python
class AsyncLightRAGClient:
    """Async HTTP client for LightRAG API."""
    
    def __init__(self, config: RAGConfig | None = None):
        self._config = config or RAGConfig.from_env()
        self._client: httpx.AsyncClient | None = None
    
    @property
    def is_available(self) -> bool:
        return self._config.is_configured
    
    async def _ensure_client(self) -> httpx.AsyncClient:
        if not self.is_available:
            raise RAGNotConfiguredError()
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                headers=self._build_headers(),
                timeout=httpx.Timeout(self._config.timeout),
            )
        return self._client
    
    def _build_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["X-API-Key"] = self._config.api_key
        headers["LIGHTRAG-WORKSPACE"] = self._config.workspace
        return headers
    
    async def insert_text(self, text: str, description: str = "", ...) -> InsertResponse:
        ...
    
    async def query(self, query: str, mode: str = "hybrid", ...) -> QueryResponse:
        ...
    
    # ... all other endpoint methods
    
    async def close(self):
        if self._client:
            await self._client.aclose()
```

### Task 2.6: Module Exports

**File**: `daemon/rag/__init__.py` (NEW)

```python
from .client import AsyncLightRAGClient
from .config import RAGConfig
from .exceptions import (
    RAGError,
    RAGNotConfiguredError,
    RAGConnectionError,
    RAGTimeoutError,
    RAGResponseError,
)

__all__ = [
    "AsyncLightRAGClient",
    "RAGConfig",
    "RAGError",
    "RAGNotConfiguredError",
    "RAGConnectionError",
    "RAGTimeoutError",
    "RAGResponseError",
]
```

### Task 2.7: Write Unit Tests

**File**: `tests/unit/rag/test_client.py` (NEW)

Test cases:
1. `test_config_from_env` — ENV vars parsed correctly
2. `test_config_not_configured` — missing LIGHTRAG_HOST
3. `test_client_raises_not_configured` — client methods fail gracefully
4. `test_client_insert_text` — mock httpx, verify request
5. `test_client_query` — mock httpx, verify request with mode
6. `test_client_error_response` — verify RAGResponseError raised
7. `test_client_timeout` — verify RAGTimeoutError raised

## Key Files

- `daemon/rag/__init__.py` — **NEW**: Module exports
- `daemon/rag/config.py` — **NEW**: ENV configuration
- `daemon/rag/exceptions.py` — **NEW**: Custom exceptions
- `daemon/rag/schemas.py` — **NEW**: Pydantic models for all endpoints
- `daemon/rag/endpoints.py` — **NEW**: URL path constants
- `daemon/rag/client.py` — **NEW**: Async HTTP client
- `tests/unit/rag/test_client.py` — **NEW**: Unit tests

## Constraints

1. **No external dependency on LightRAG** — all interaction via HTTP API
2. **Graceful degradation** — `RAGNotConfiguredError` if ENV not set, not a crash
3. **httpx only** — use httpx for async HTTP (already a project dependency or easy to add)
4. **No retry storms** — max 1 retry on connection errors
5. **Timeout** — configurable via ENV, default 120s
6. **Workspace isolation** — `LIGHTRAG-WORKSPACE` header sent on every request
7. **Thread safety** — httpx.AsyncClient is not thread-safe; create per-coroutine or use lock

## Deliverables

- [ ] `daemon/rag/config.py` — RAGConfig dataclass with from_env()
- [ ] `daemon/rag/exceptions.py` — Exception hierarchy
- [ ] `daemon/rag/schemas.py` — Pydantic models for all endpoints
- [ ] `daemon/rag/endpoints.py` — URL path constants
- [ ] `daemon/rag/client.py` — AsyncLightRAGClient with all endpoint methods
- [ ] `daemon/rag/__init__.py` — Public API exports
- [ ] `tests/unit/rag/test_client.py` — Unit tests passing

## Verification

```bash
# Verify module imports
python -c "from daemon.rag import AsyncLightRAGClient, RAGConfig; print('OK')"

# Run tests (without LIGHTRAG_HOST set — should test graceful degradation)
pytest tests/unit/rag/test_client.py -v

# Test with mock LightRAG
LIGHTRAG_HOST=http://localhost:8080 LIGHTRAG_API_KEY=test pytest tests/unit/rag/test_client.py -v
```
