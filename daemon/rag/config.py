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


def is_rag_enabled() -> bool:
    """Check if RAG is enabled via environment configuration."""
    return RAGConfig.from_env().is_configured
