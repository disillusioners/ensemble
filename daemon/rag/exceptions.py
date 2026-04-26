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
