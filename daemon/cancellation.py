"""Thread-safe cancellation token system for request termination."""

import threading
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum


class CancellationReason(Enum):
    """Reason for cancellation."""
    TIMEOUT = "timeout"
    WATCHDOG_RETRY = "watchdog_retry"
    MANUAL = "manual"
    SHUTDOWN = "shutdown"


class OperationCancelledError(Exception):
    """Raised when an operation is cancelled via cancellation token."""
    
    def __init__(self, reason: CancellationReason, message: str = ""):
        self.reason = reason
        self.message = message or f"Operation cancelled: {reason.value}"
        super().__init__(self.message)


@dataclass
class CancellationToken:
    """Immutable token that can be checked for cancellation.
    
    Thread-safe and can be used in both sync and async contexts.
    """
    _cancelled: threading.Event = field(default_factory=threading.Event)
    _reason: Optional[CancellationReason] = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    
    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation was requested."""
        return self._cancelled.is_set()
    
    @property
    def reason(self) -> Optional[CancellationReason]:
        """Get the cancellation reason, if any."""
        with self._lock:
            return self._reason
    
    def check(self) -> None:
        """Check and raise if cancelled.
        
        Raises:
            OperationCancelledError: If cancellation was requested.
        """
        if self._cancelled.is_set():
            raise OperationCancelledError(
                reason=self._reason or CancellationReason.MANUAL
            )
    
    async def async_check(self) -> None:
        """Async version of check for use in async code."""
        self.check()
    
    def wait_for_cancellation(self, timeout: Optional[float] = None) -> bool:
        """Block until cancelled or timeout.
        
        Returns:
            True if cancelled, False if timeout.
        """
        return self._cancelled.wait(timeout=timeout)


@dataclass  
class CancellationTokenSource:
    """Factory that creates cancellation tokens and can trigger cancellation."""
    
    _token: CancellationToken = field(default_factory=CancellationToken, init=False)
    _callbacks: list[Callable[[], None]] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    
    @property
    def token(self) -> CancellationToken:
        """Get the cancellation token."""
        return self._token
    
    def cancel(self, reason: CancellationReason = CancellationReason.MANUAL) -> None:
        """Signal cancellation to all token holders."""
        with self._lock:
            if self._token._cancelled.is_set():
                return  # Already cancelled
            
            self._token._reason = reason
            self._token._cancelled.set()
            
            # Invoke callbacks
            for callback in self._callbacks:
                try:
                    callback()
                except Exception:
                    pass  # Don't let callback errors propagate
    
    def register_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be invoked on cancellation."""
        with self._lock:
            self._callbacks.append(callback)
    
    def is_cancelled(self) -> bool:
        """Check if this source has been cancelled."""
        return self._token.is_cancelled
