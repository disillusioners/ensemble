"""Sources module for message source management.

This module provides components for managing message sources including:
- Base classes and types for sources
- Circuit breaker pattern implementation
- Rate limiting for message sources
- Credential management
- Persistence layer
- Session mapping
- Source registry
- Response dispatching
- Cleanup utilities
"""

# Base types and classes
from daemon.sources.base import (
    SourceStatus,
    IncomingMessage,
    OutgoingMessage,
    SourceConfig,
    MessageSourceAdapter,
)

# Circuit breaker
from daemon.sources.circuit_breaker import CircuitState, CircuitBreaker

# Rate limiting
from daemon.sources.rate_limiter import (
    RateLimit,
    TokenBucketLimiter,
    DEFAULT_RATE_LIMITS,
)

# Credential management
from daemon.sources.credentials import CredentialManager

# Persistence layer - import all functions
from daemon.sources import persistence

# Session mapping
from daemon.sources.mapper import SessionMapper, validate_external_user_id

# Source registry
from daemon.sources.registry import SourceRegistry

# Response dispatcher
from daemon.sources.dispatcher import ResponseDispatcher

# Cleanup utilities
from daemon.sources.cleanup import SourceCleanup

# Re-export persistence module for convenience
__all__ = [
    # Base
    "SourceStatus",
    "IncomingMessage",
    "OutgoingMessage",
    "SourceConfig",
    "MessageSourceAdapter",
    # Circuit breaker
    "CircuitState",
    "CircuitBreaker",
    # Rate limiter
    "RateLimit",
    "TokenBucketLimiter",
    "DEFAULT_RATE_LIMITS",
    # Credentials
    "CredentialManager",
    # Persistence module (functions require conn parameter)
    "persistence",
    # Mapper
    "SessionMapper",
    "validate_external_user_id",
    # Registry
    "SourceRegistry",
    # Dispatcher
    "ResponseDispatcher",
    # Cleanup
    "SourceCleanup",
]
