"""RAG (Retrieval-Augmented Generation) module for LightRAG integration."""

from .client import AsyncLightRAGClient
from .config import (
    RAGConfig,
    RAGRequiredError,
    auto_test_rag,
    disable_rag,
    enable_rag,
    is_rag_enabled,
    is_rag_required,
)
from .exceptions import (
    RAGConnectionError,
    RAGError,
    RAGNotConfiguredError,
    RAGResponseError,
    RAGTimeoutError,
)
from .schemas import (
    InsertResponse,
    InsertTextRequest,
    InsertTextsRequest,
    QueryDataRequest,
    QueryDataResponse,
    QueryRequest,
    QueryResponse,
)

__all__ = [
    "AsyncLightRAGClient",
    "InsertResponse",
    "InsertTextRequest",
    "InsertTextsRequest",
    "QueryDataRequest",
    "QueryDataResponse",
    "QueryRequest",
    "QueryResponse",
    "RAGConfig",
    "RAGConnectionError",
    "RAGError",
    "RAGNotConfiguredError",
    "RAGRequiredError",
    "RAGResponseError",
    "RAGTimeoutError",
    "auto_test_rag",
    "disable_rag",
    "enable_rag",
    "is_rag_enabled",
    "is_rag_required",
]
