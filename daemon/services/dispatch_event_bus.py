"""DispatchEventBus - In-process event notification for job dispatch.

Uses per-project asyncio.Event instances to notify JobProcessor of new jobs
immediately, replacing the pure polling approach with event-driven wakeup.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


class DispatchEventBus:
    """In-process event bus for job dispatch signaling.
    
    Maintains per-project asyncio.Event instances. When a new job is enqueued,
    the corresponding project event is set, waking up the JobProcessor immediately.
    
    This is separate from the existing EventBus (daemon/services/event_bus.py)
    which handles SSE/task-level events. DispatchEventBus operates at the JOB level.
    
    Attributes:
        _events: Dict mapping project_id to asyncio.Event.
        _loop: Reference to the running event loop.
    """
    
    def __init__(self):
        self._events: dict[str, asyncio.Event] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
    
    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the event loop reference for thread-safe notification."""
        self._loop = loop
    
    def _get_or_create_event(self, project_id: str) -> asyncio.Event:
        """Get or create an asyncio.Event for a project."""
        if project_id not in self._events:
            self._events[project_id] = asyncio.Event()
        return self._events[project_id]
    
    def notify_new_job(self, project_id: str | None) -> None:
        """Signal that a new job is available for a project.
        
        Can be called from any thread. Uses call_soon_threadsafe for
        thread-safe event setting if called from a non-async context.
        
        Args:
            project_id: The project ID that has a new job.
        """
        if project_id is None:
            logger.debug("DispatchEventBus: no project_id, skipping notification")
            return
        
        if self._loop is None:
            logger.debug("DispatchEventBus: no event loop set, skipping notification")
            return
        
        def _set_events():
            event = self._get_or_create_event(project_id)
            event.set()
            logger.debug(f"DispatchEventBus: notified project {project_id}")
        
        try:
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(_set_events)
            else:
                _set_events()
        except RuntimeError:
            logger.debug("DispatchEventBus: event loop closed, skipping notification")
    
    async def wait_for_job(self, project_id: str | None, timeout: float) -> bool:
        """Wait for a new job event for a project.
        
        Args:
            project_id: The project ID to wait for.
            timeout: Maximum seconds to wait.
            
        Returns:
            True if event was set (new job available), False if timed out
            or if project_id is None (graceful degradation).
        """
        # Graceful degradation: project_id=None means no event to wait on,
        # so we fall back to simple timeout-based polling.
        if project_id is None:
            await asyncio.sleep(timeout)
            return False
        
        event = self._get_or_create_event(project_id)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            # Auto-clear after wakeup
            event.clear()
            return True
        except asyncio.TimeoutError:
            event.clear()
            return False
    
    def notify_all(self) -> None:
        """Set events for ALL known projects. Used for startup/recovery."""
        if self._loop is None:
            return
        
        def _set_all():
            for event in self._events.values():
                event.set()
            logger.debug(f"DispatchEventBus: notified all {len(self._events)} projects")
        
        try:
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(_set_all)
            else:
                _set_all()
        except RuntimeError:
            pass
