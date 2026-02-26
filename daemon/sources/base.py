"""Base interfaces and dataclasses for pluggable message sources."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from enum import Enum


class SourceStatus(Enum):
    """Status of a message source adapter."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class IncomingMessage:
    """Normalized incoming message from any source."""
    external_user_id: str       # Telegram chat_id, webhook client_id
    content: str                # Message text/content
    source_id: str              # Which source adapter this came from
    metadata: dict = field(default_factory=dict)
    message_type: str = "text"  # "text", "image", "command"
    reply_to_id: Optional[str] = None


@dataclass
class OutgoingMessage:
    """Normalized outgoing message to any source."""
    external_user_id: str
    content: str
    source_id: str
    metadata: dict = field(default_factory=dict)
    message_type: str = "text"
    reply_to_id: Optional[str] = None


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
        self._error: Optional[str] = None
    
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
    def error(self) -> Optional[str]:
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
    
    async def reload(self, new_config: SourceConfig) -> None:
        """Reload configuration (restart if needed)."""
        if self.config != new_config:
            await self.stop()
            self.config = new_config
            await self.start()
    
    async def _emit_message(self, msg: IncomingMessage) -> None:
        """Internal: call the message handler."""
        await self._on_message(msg)
