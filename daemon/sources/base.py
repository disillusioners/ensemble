"""Base interfaces and dataclasses for pluggable message sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Tuple
from enum import Enum


class SourceStatus(Enum):
    """Status of a message source adapter."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"
    # Scheduled-tasks phase 1 / ADR-007: terminal state — the boot filter
    # (registry.py:298) and the stop_adapter clobber guard (registry.py:677)
    # reference ``SourceStatus.CANCELLED`` from THIS import. The phase-1
    # commit extended the two model enums (``daemon/models/source.py``
    # lowercase ``cancelled`` + ``daemon/repositories/source/models.py``
    # uppercase ``CANCELLED``) but missed this adapter-base third site,
    # which made every ``start_all()`` raise AttributeError. Kept in lockstep
    # with the other two sites — extend all three together.
    CANCELLED = "cancelled"


@dataclass
class IncomingMessage:
    """Normalized incoming message from any source.

    Phase 2 / clipboard-image-chat (round-2 amendment #38):
    ``IncomingMessage`` MUST NOT carry ``image_refs`` — refs are
    POST-only on ``MessageCreate``. Sources mint text / images via the
    legacy ``images`` field; the clipboard-image-chat display channel
    is the user-API surface (``MessageCreate.image_refs``). The static
    ``dataclasses.fields(IncomingMessage)`` assertion (test freeze
    list A8) pins this invariant — a future contributor adding
    ``image_refs`` here would re-open the chat-source invariant
    bypass (sources could mint refs that ride the live-injection
    lane).
    """
    external_user_id: str       # Telegram chat_id, webhook client_id
    content: str                # Message text/content
    source_id: str              # Which source adapter this came from
    images: list[str] | None = None
    metadata: dict = field(default_factory=dict)
    message_type: str = "text"  # "text", "image", "command"
    reply_to_id: str | None = None


@dataclass(frozen=True)
class ImageAttachment:
    """Resolved image bytes for native chat-source delivery.

    Transport-only — NEVER persisted, NEVER logged whole (chart-image-delivery
    architecture-recommendation.md §3 amendment #12). The base64 bytes payload
    is excluded from repr/dataclasses-asdict via ``field(repr=False)`` plus a
    redacting ``__repr__``. Logging is ``image_id[:8]`` + ``size_bytes`` +
    ``content_type`` only at dispatcher AND all three adapters (helper:
    ``_log_image_metadata``).

    Bytes encoding rationale: no BytesIO across the adapter boundary —
    pickling + cross-task handoff is simpler with a string. Adapters decode
    per their platform's upload API.
    """
    image_id: str            # 32-hex (regex-validated at extraction)
    content_type: str        # e.g. "image/png"
    filename: str            # e.g. "chart-abc12345.png"
    size_bytes: int          # captured at resolution; logs use this
    bytes_b64: str = field(repr=False)  # base64 of the PNG bytes — NEVER LOGGED

    def __repr__(self) -> str:  # redacting — bytes payload never in repr
        return (
            f"ImageAttachment(image_id={self.image_id[:8]}..., "
            f"content_type={self.content_type!r}, "
            f"size_bytes={self.size_bytes}, "
            f"filename={self.filename!r}, "
            f"bytes_b64=<redacted>)"
        )


@dataclass
class OutgoingMessage:
    """Normalized outgoing message to any source.

    ``images`` is transport-only: NEVER persisted, NEVER logged whole
    (chart-image-delivery architecture-recommendation.md §3 amendment #12).
    The dispatcher populates this field at the BOTH chat-bound construction
    sites (progressive lane + completed lane); HTTP-API sources (``api`` and
    similar no-colon identifiers) skip the dispatch path entirely and
    never populate ``images``. The /new confirmation construction site
    (``daemon/sources/registry.py``) is also NEVER populated — /new
    confirmation messages never carry markers.

    ``delivered_image_ids`` (F5 / chart-image-delivery council-review): the
    adapter MAY populate this during ``send()`` with the subset of
    ``images`` actually delivered to the platform (excludes per-id drops
    from decode failure, oversize guard, MIME-miss, capability-short-circuit,
    transport error). The dispatcher then deletes only the delivered subset
    from the tmp-images store; non-delivered ids stay queryable. The field
    defaults to ``None`` — adapters that don't populate it fall back to
    "delete all images" for backward compatibility.
    """
    external_user_id: str
    content: str
    source_id: str
    metadata: dict = field(default_factory=dict)
    message_type: str = "text"
    reply_to_id: str | None = None
    images: list[ImageAttachment] | None = None  # NEW — defaulted, backward-compat
    delivered_image_ids: list[str] | None = None  # F5 — adapters set on success

    def __post_init__(self) -> None:
        # Lightweight invariant: ``images`` MUST be either ``None`` or a list.
        # Other callers construct ``OutgoingMessage`` everywhere; this guard
        # only catches accidental misuse (a string, a dict) at construction
        # time instead of much later when the dispatcher iterates ``images``.
        if self.images is not None and not isinstance(self.images, list):
            raise TypeError(
                f"OutgoingMessage.images must be a list or None, "
                f"got {type(self.images).__name__}"
            )


@dataclass
class SourceConfig:
    """Configuration for a message source."""
    source_id: str
    source_type: str
    name: str
    config: dict
    credentials: dict
    enabled: bool = True


class MessageSourceAdapter(ABC):
    """Abstract base class for all message source adapters.
    
    Each adapter handles:
    - Connecting to external service
    - Receiving and normalizing messages
    - Sending responses back
    - Lifecycle management
    """
    
    def __init__(self, config: SourceConfig,
                 on_message: Callable[[IncomingMessage], Awaitable[None]]):
        self.config = config
        self._on_message = on_message
        self._status = SourceStatus.STOPPED
        self._error: str | None = None
    
    @property
    def source_id(self) -> str:
        return self.config.source_id
    
    @property
    def source_type(self) -> str:
        return self.config.source_type
    
    @property
    def status(self) -> SourceStatus:
        return self._status
    
    @property
    def error(self) -> str | None:
        return self._error
    
    @abstractmethod
    async def start(self) -> None:
        """Start the adapter (connect, begin listening)."""
        ...
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the adapter gracefully."""
        ...
    
    @abstractmethod
    async def send(self, message: OutgoingMessage) -> bool:
        """Send message to external service. Returns success."""
        ...
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if adapter is healthy and connected."""
        ...
    
    @classmethod
    async def test_connection(cls, config: SourceConfig) -> Tuple[bool, str]:
        """Test connection to external service without full initialization.

        Args:
            config: Source configuration to test

        Returns:
            Tuple of (success: bool, message: str)
            - success: True if connection test passed
            - message: Human-readable result or error message
        """
        # Default implementation - subclasses should override
        return True, "Test not implemented for this source type"

    async def _emit_message(self, msg: IncomingMessage) -> None:
        """Internal: call the message handler."""
        await self._on_message(msg)
