"""Main loop bridge for thread-safe asyncio operations from worker threads."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class MainLoopBridge:
    """Singleton that holds reference to the main asyncio event loop.
    
    Allows worker threads to run coroutines on the main event loop in a
    thread-safe manner using asyncio.run_coroutine_threadsafe().
    
    Usage:
        # In the main thread (during startup):
        MainLoopBridge.set_loop(loop)
        
        # In worker threads:
        MainLoopBridge.run_async(coro, timeout=300)
    """
    
    _instance: MainLoopBridge | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    _lock: asyncio.Lock | None = None
    
    def __new__(cls) -> MainLoopBridge:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def set_loop(cls, loop: asyncio.AbstractEventLoop) -> None:
        """Set the main event loop reference. Call from main thread only."""
        cls._loop = loop
        cls._lock = asyncio.Lock()
        logger.info(f"MainLoopBridge: event loop set to {loop}")
    
    @classmethod
    def get_loop(cls) -> asyncio.AbstractEventLoop | None:
        """Get the main event loop reference."""
        return cls._loop
    
    @classmethod
    def run_async(
        cls, 
        coro: Coroutine[Any, Any, T], 
        timeout: float | None = None
    ) -> T:
        """Run a coroutine on the main event loop from a worker thread.
        
        This is the thread-safe way to call async code from worker threads.
        Uses asyncio.run_coroutine_threadsafe() pattern.
        
        Args:
            coro: The coroutine to run.
            timeout: Optional timeout in seconds. If None, uses a default of 300s.
        
        Returns:
            The result of the coroutine.
        
        Raises:
            RuntimeError: If the event loop is not set or is closed.
            TimeoutError: If the coroutine doesn't complete within timeout.
        """
        loop = cls._loop
        if loop is None:
            raise RuntimeError(
                "MainLoopBridge: event loop not set. "
                "Call MainLoopBridge.set_loop(loop) during initialization."
            )
        
        if loop.is_closed():
            raise RuntimeError("MainLoopBridge: event loop is closed.")
        
        if timeout is None:
            timeout = 300.0  # 5 minutes default
        
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            logger.error(f"MainLoopBridge: coroutine timed out after {timeout}s")
            raise
        except Exception as e:
            logger.error(f"MainLoopBridge: error running coroutine: {e}")
            raise
    
    @classmethod
    def run_async_no_wait(cls, coro: Coroutine[Any, Any, Any]) -> None:
        """Run a coroutine on the main event loop without waiting for result.
        
        Fire-and-forget pattern. Exceptions are logged but not raised.
        
        Args:
            coro: The coroutine to run.
        """
        loop = cls._loop
        if loop is None or loop.is_closed():
            logger.warning("MainLoopBridge: cannot run coroutine, loop not available")
            return
        
        def _log_exception(f: asyncio.Future[Any]) -> None:
            try:
                f.result()
            except Exception as e:
                logger.error(f"MainLoopBridge: unhandled exception in fire-and-forget coroutine: {e}")
        
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        future.add_done_callback(_log_exception)
    
    @classmethod
    def reset(cls) -> None:
        """Reset the singleton. For testing only."""
        cls._loop = None
        cls._lock = None
        cls._instance = None
