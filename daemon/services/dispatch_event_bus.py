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
        _global_event: Event for jobs without project_id.
        _loop: Reference to the running event loop.
    """
    
    def __init__(self):
        self._events: dict[str, asyncio.Event] = {}
        self._global_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
    
    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the event loop reference for thread-safe notification."""
        self._loop = loop
    
    def _get_or_create_event(self, project_id: str) -> asyncio.Event:
        """Get or create an asyncio.Event for a project."""
        if project_id not in self._events:
            self._events[project_id] = asyncio.Event()
        return self._events[project_id]
    
    def _get_global_event(self) -> asyncio.Event:
        """Get or create the global event (for jobs without project_id)."""
        if self._global_event is None:
            self._global_event = asyncio.Event()
        return self._global_event
    
    def notify_new_job(self, project_id: str | None) -> None:
        """Signal that a new job is available for a project.
        
        Can be called from any thread. Uses call_soon_threadsafe for
        thread-safe event setting if called from a non-async context.
        
        Args:
            project_id: The project ID that has a new job. None for global jobs.
        """
        if self._loop is None:
            logger.debug("DispatchEventBus: no event loop set, skipping notification")
            return
        
        def _set_events():
            if project_id is not None:
                event = self._get_or_create_event(project_id)
                event.set()
                logger.debug(f"DispatchEventBus: notified project {project_id}")
            # Always set global event too for catch-all wakeup
            global_event = self._get_global_event()
            global_event.set()
        
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
            project_id: The project ID to wait for. None waits on global event.
            timeout: Maximum seconds to wait.
            
        Returns:
            True if event was set (new job available), False if timed out.
        """
        if project_id is not None:
            event = self._get_or_create_event(project_id)
        else:
            event = self._get_global_event()
        
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
            for project_id, event in self._events.items():
                event.set()
            if self._global_event is not None:
                self._global_event.set()
            logger.debug(f"DispatchEventBus: notified all {len(self._events)} projects")
        
        try:
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(_set_all)
            else:
                _set_all()
        except RuntimeError:
            pass
