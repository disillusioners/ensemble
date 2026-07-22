"""File change monitor with optional watchdog integration.

Uses watchdog for efficient filesystem notifications when available.
Falls back to polling (5s interval) when watchdog is not installed.

Events are debounced: rapid successive changes to the same file are
coalesced into a single event with a minimum 2-second gap.
"""
import asyncio
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Try importing watchdog — optional dependency
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False


class FileChangeMonitor:
    """Per-workdir file change monitor.

    Uses watchdog for efficient filesystem notifications when available.
    Falls back to polling (5s interval) when watchdog is not installed.

    Events are debounced: rapid successive changes to the same file are
    coalesced into a single event with a minimum 2-second gap.

    Thread safety: watchdog's Observer runs on its own thread. asyncio.Queue
    is NOT thread-safe, so ``_emit`` uses ``loop.call_soon_threadsafe`` to
    schedule the ``put_nowait`` on the event loop (same pattern as
    ``daemon/services/dispatch_event_bus.py:67`` and
    ``daemon/services/completion_registry.py:133``).
    """

    _instances: dict[str, "FileChangeMonitor"] = {}

    @classmethod
    def get_or_create(cls, workdir: str) -> "FileChangeMonitor":
        """Get existing monitor or create a new one.

        If the existing instance was stopped (no subscribers), it is evicted
        from the registry and a fresh one is created. This prevents returning
        a dead instance with a terminated Observer.
        """
        key = str(Path(workdir).resolve())
        existing = cls._instances.get(key)
        if existing is not None and existing._started:
            return existing
        # Evict dead instance (W2)
        if existing is not None:
            cls._instances.pop(key, None)
        instance = cls(workdir)
        cls._instances[key] = instance
        return instance

    def __init__(self, workdir: str):
        self.workdir = Path(workdir)
        self._subscribers: dict[str, asyncio.Queue] = {}
        self._debounce: dict[str, float] = {}  # path -> last_emit_time
        self._observer: "Observer | None" = None
        self._poll_task: asyncio.Task | None = None
        self._started = False
        self._loop: asyncio.AbstractEventLoop | None = None

    async def add_subscriber(self, queue: asyncio.Queue) -> str:
        import uuid
        # Capture the running loop for thread-safe callbacks (Blocking Fix 1).
        # Must be set here (inside the async context) rather than __init__
        # which may be called from get_or_create without a running loop.
        self._loop = asyncio.get_running_loop()
        conn_id = str(uuid.uuid4())
        self._subscribers[conn_id] = queue
        if not self._started:
            self._start()
        return conn_id

    async def remove_subscriber(self, conn_id: str):
        self._subscribers.pop(conn_id, None)
        if not self._subscribers:
            self._stop()

    def _emit(self, event_data: dict):
        """Emit event to all subscribers with debounce.

        Called from watchdog's Observer thread (non-async context).
        Uses ``call_soon_threadsafe`` to schedule the queue put on the event
        loop, since asyncio.Queue is not thread-safe.
        """
        path = event_data.get("path", "")
        now = time.time()
        if path in self._debounce and now - self._debounce[path] < 2.0:
            return  # debounce: skip
        self._debounce[path] = now

        if self._loop is None:
            # No event loop captured yet — can happen if _emit fires before
            # any subscriber connects. Safe to drop.
            return

        for queue in list(self._subscribers.values()):
            try:
                # Thread-safe: schedule put_nowait on the event loop
                self._loop.call_soon_threadsafe(self._safe_put, queue, event_data)
            except RuntimeError:
                # Event loop closed between check and call — drop event
                logger.debug("Event loop closed, dropping file-change event")
                continue

    def _safe_put(self, queue: asyncio.Queue, event_data: dict):
        """Scheduled on the event loop via call_soon_threadsafe."""
        try:
            queue.put_nowait(event_data)
        except asyncio.QueueFull:
            logger.warning("File change queue full, dropping event")

    def _start(self):
        """Start monitoring."""
        self._started = True
        if HAS_WATCHDOG:
            self._start_watchdog()
        else:
            self._poll_task = asyncio.create_task(self._poll_loop())

    def _start_watchdog(self):
        """Create a fresh Observer and schedule it.

        Blocking Fix 3: watchdog.Observer is single-shot — once ``stop()`` is
        called, its internal thread terminates and the instance cannot be
        restarted. We always create a new Observer here, never reuse.
        """
        class _Handler(FileSystemEventHandler):
            def __init__(self, monitor: "FileChangeMonitor"):
                self._monitor = monitor

            def on_any_event(self, event):
                if event.is_directory:
                    return
                rel = os.path.relpath(
                    event.src_path, str(self._monitor.workdir)
                )
                self._monitor._emit({
                    "path": rel,
                    "change_type": event.event_type,
                    "timestamp": time.time(),
                })

        self._observer = Observer()
        self._observer.schedule(
            _Handler(self), str(self.workdir), recursive=True
        )
        self._observer.start()

    def _stop(self):
        """Stop monitoring and evict from registry when no subscribers remain.

        Blocking Fix 3 + W2: When the last subscriber disconnects, stop the
        monitor and remove it from ``_instances`` so the next
        ``get_or_create`` call creates a fresh instance with a new Observer.

        W3: Detach the Observer thread without blocking the event loop. The
        blocking ``join(timeout=5)`` is run on a daemon thread so callers in
        async context (``remove_subscriber``) return promptly.
        """
        self._started = False
        if self._observer is not None:
            self._observer.stop()
            # Don't block the event loop — detach and let the thread finish in background
            import threading
            threading.Thread(
                target=self._observer.join, kwargs={"timeout": 5}, daemon=True
            ).start()
            self._observer = None
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        # Evict from singleton registry — use the resolved key to match
        # get_or_create() so non-canonical workdir paths don't get orphaned.
        key = str(self.workdir.resolve())
        FileChangeMonitor._instances.pop(key, None)

    async def _poll_loop(self):
        """Fallback polling implementation (no watchdog).

        Snapshots the directory listing and compares against the previous
        snapshot every 5 seconds, emitting events for changed/added/removed
        files.
        """
        last_snapshot: dict[str, float] = {}
        # Initial snapshot
        last_snapshot = self._scan_mtimes()
        while self._started:
            await asyncio.sleep(5)
            current = self._scan_mtimes()
            for path, mtime in current.items():
                if path not in last_snapshot or last_snapshot[path] != mtime:
                    self._emit({
                        "path": path,
                        "change_type": "modified",
                        "timestamp": time.time(),
                    })
            for path in last_snapshot:
                if path not in current:
                    self._emit({
                        "path": path,
                        "change_type": "deleted",
                        "timestamp": time.time(),
                    })
            last_snapshot = current

    def _scan_mtimes(self) -> dict[str, float]:
        """Walk workdir and return {relative_path: mtime} for all files."""
        # Import WorkspaceGuard for IGNORE_PATTERNS (used in the poll loop to prune)
        from daemon.services.workspace_guard import WorkspaceGuard

        result: dict[str, float] = {}
        try:
            for root, dirs, files in os.walk(self.workdir):
                # Prune ignored dirs
                dirs[:] = [
                    d for d in dirs
                    if d not in WorkspaceGuard.IGNORE_PATTERNS
                ]
                for fname in files:
                    full = os.path.join(root, fname)
                    try:
                        rel = os.path.relpath(full, str(self.workdir))
                        result[rel] = os.path.getmtime(full)
                    except OSError:
                        continue
        except OSError:
            pass
        return result