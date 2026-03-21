"""TaskProcessor - Background worker for processing queued tasks."""

import asyncio
import logging
from typing import Optional

from daemon.services.task_queue_service import TaskQueueService
from daemon.services.task_lock_manager import TaskLockManager
from daemon.manager import SessionManager

logger = logging.getLogger(__name__)


class TaskProcessor:
    """Background worker that processes queued tasks.
    
    Continuously polls for pending tasks and processes them one at a time
    per worker instance. Acquires project locks before processing and
    releases them when complete.
    
    Attributes:
        _queue_service: TaskQueueService instance for task operations.
        _session_manager: SessionManager instance for spawning sessions.
        _poll_interval: Time in seconds between poll cycles.
        _running: Flag to control the processing loop.
    """
    
    def __init__(
        self,
        queue_service: TaskQueueService,
        session_manager: SessionManager,
        poll_interval: float = 2.0,
    ):
        """Initialize the TaskProcessor.
        
        Args:
            queue_service: TaskQueueService for task operations.
            session_manager: SessionManager for spawning sessions.
            poll_interval: Seconds between poll cycles (default: 2.0).
        """
        self._queue_service = queue_service
        self._session_manager = session_manager
        self._poll_interval = poll_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start the background processing loop."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("TaskProcessor started")
    
    async def stop(self) -> None:
        """Stop the background processing loop gracefully."""
        if not self._running:
            return
        
        self._running = False
        
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        
        logger.info("TaskProcessor stopped")
    
    async def _process_loop(self) -> None:
        """Main processing loop - polls for and processes tasks."""
        while self._running:
            try:
                await self._process_next_task()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"Error in processing loop: {e}")
            
            await asyncio.sleep(self._poll_interval)
    
    async def _process_next_task(self) -> None:
        """Get the next pending task and process it."""
        # Get next pending task (highest priority, oldest first)
        task = self._queue_service.get_next_pending_task()
        if task is None:
            return
        
        logger.info(f"Processing task {task.task_id} for project {task.project_id}")
        
        try:
            # Acquire lock and start task
            started_task = await self._queue_service.start_task(task.task_id)
            if started_task is None:
                # Lock acquisition failed or task was cancelled
                logger.warning(f"Could not start task {task.task_id} - may be cancelled or lock held")
                return
            
            # Spawn session for this task
            try:
                session_id = self._session_manager.spawn_session(
                    agent_dir=task.agent_dir,
                    session_id=started_task.session_id,
                )
            except Exception as e:
                logger.error(f"Failed to spawn session for task {task.task_id}: {e}")
                await self._queue_service.complete_task(task.task_id, success=False, error=str(e))
                return
            
            # Send the task message to the session
            try:
                await self._session_manager.enqueue_message(
                    session_id=session_id,
                    message=task.message,
                    source=task.source,
                )
            except Exception as e:
                logger.error(f"Failed to enqueue message for task {task.task_id}: {e}")
                await self._queue_service.complete_task(task.task_id, success=False, error=str(e))
                return
            
            # Mark task as successfully queued for processing
            await self._queue_service.complete_task(task.task_id, success=True)
            logger.info(f"Task {task.task_id} queued successfully for session {session_id}")
            
            # Trigger next task for this project (if any)
            if task.project_id:
                await self._queue_service.trigger_next_task(task.project_id)
                
        except Exception as e:
            logger.exception(f"Failed to process task {task.task_id}: {e}")
            await self._queue_service.complete_task(task.task_id, success=False, error=str(e))
