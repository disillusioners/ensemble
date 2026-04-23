"""TimeoutMonitor - daemon thread that cancels a token after a timeout."""

import logging
import threading

logger = logging.getLogger(__name__)


class TimeoutMonitor:
    """Monitors task timeout and cancels token when exceeded.
    
    Starts a daemon thread that waits for the specified timeout.
    If not stopped before timeout, cancels the provided token.
    
    Usage:
        source = CancellationTokenSource()
        monitor = TimeoutMonitor(task_id=42, source=source, timeout_seconds=900)
        monitor.start()
        try:
            # ... do work ...
        finally:
            monitor.stop()
    """
    
    def __init__(
        self,
        task_id: int,
        source: "CancellationTokenSource",
        timeout_seconds: float,
    ):
        self._task_id = task_id
        self._source = source
        self._timeout = timeout_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._fired = False
    
    @property
    def fired(self) -> bool:
        """Whether the timeout was fired (not cancelled early)."""
        return self._fired
    
    def start(self) -> None:
        """Start the timeout monitor thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning(f"TimeoutMonitor for task {self._task_id} already running")
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"TimeoutMonitor-task-{self._task_id}",
            daemon=True,
        )
        self._thread.start()
        logger.debug(
            f"TimeoutMonitor started for task {self._task_id} "
            f"(timeout={self._timeout}s)"
        )
    
    def _run(self) -> None:
        """Wait for timeout or stop signal."""
        if self._stop_event.wait(timeout=self._timeout):
            # Stopped before timeout
            return
        
        # Timeout elapsed — cancel the token
        self._fired = True
        from daemon.cancellation import CancellationReason
        self._source.cancel(CancellationReason.TIMEOUT)
        logger.warning(
            f"TimeoutMonitor: task {self._task_id} timed out "
            f"after {self._timeout}s"
        )
    
    def stop(self) -> None:
        """Stop the monitor before timeout fires."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                logger.warning(
                    f"TimeoutMonitor for task {self._task_id} "
                    f"did not stop within 2s"
                )
    
    def is_running(self) -> bool:
        """Check if the monitor thread is alive."""
        return self._thread is not None and self._thread.is_alive()
