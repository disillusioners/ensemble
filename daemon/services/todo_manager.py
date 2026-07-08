"""Per-instance in-memory todo state manager.

Holds ephemeral todo lists keyed by instance_id. Todos are not persisted;
they exist for the lifetime of the daemon process and are used by the
todo tool surface during a single instance run.

Thread Safety:
    All dict mutations are guarded by threading.Lock. The lock is held
    only for the duration of a single mutation/serialization step so
    callers (sync or async) can compose freely without reentrancy concerns.
"""

import logging
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_VALID_STATUSES = ("pending", "in_progress", "done")

# Aliases mapping common variants (including case variants) to canonical
# statuses. Lookup is performed against the lower-cased input.
_STATUS_ALIASES: dict[str, str] = {
    "completed": "done",
    "finished": "done",
    "started": "in_progress",
    "in progress": "in_progress",
    "in-progress": "in_progress",
    "inprogress": "in_progress",
    "in_progress": "in_progress",
    "pending": "pending",
    "done": "done",
}


def _normalize_status(status: str) -> str | None:
    """Normalize a status string to its canonical value, or ``None`` if invalid.

    Case-insensitive: input is lowercased before lookup. Empty strings and
    unrecognized values return ``None`` so callers can raise or reject.
    """
    if not isinstance(status, str):
        return None
    key = status.strip().lower()
    if not key:
        return None
    return _STATUS_ALIASES.get(key)


@dataclass
class TodoItem:
    """A single todo entry.

    Attributes:
        index: Position within the instance's todo list (0-based).
        text: Human-readable description of the item.
        status: One of ``pending``, ``in_progress``, ``done``.
    """

    index: int
    text: str
    status: str


class TodoManager:
    """In-memory, per-instance todo state manager.

    Each ``instance_id`` owns an ordered list of :class:`TodoItem`. The
    manager exposes create/update/get/clear primitives that return plain
    dicts for JSON-serializable transport to tool callers.

    Thread Safety:
        All state mutations are serialized through a single
        :class:`threading.Lock`. Reads that produce returned dicts also
        take the lock so callers observe a consistent snapshot.
    """

    def __init__(self) -> None:
        """Initialize the todo manager with empty per-instance state."""
        self._instance_todos: dict[str, list[TodoItem]] = {}
        self._lock = threading.Lock()

    def create(self, instance_id: str, items: list[str]) -> list[dict]:
        """Replace the instance's todo list with ``items`` (all pending).

        Args:
            instance_id: Owning instance identifier.
            items: Ordered list of todo text entries.

        Returns:
            The newly stored todo list as plain dicts.
        """
        new_items = [
            TodoItem(index=i, text=text, status="pending")
            for i, text in enumerate(items)
        ]
        with self._lock:
            self._instance_todos[instance_id] = new_items
            return [self._to_dict(item) for item in new_items]

    def update(self, instance_id: str, index: int, status: str) -> list[dict] | None:
        """Update the status of a single item.

        Args:
            instance_id: Owning instance identifier.
            index: Position of the item to update.
            status: New status; must be one of ``pending``, ``in_progress``,
                ``done``.

        Returns:
            The full todo list as plain dicts, or ``None`` if the status
            is invalid or the index does not exist for the instance.
        """
        normalized = _normalize_status(status)
        if normalized is None:
            logger.warning(
                "TodoManager.update rejected invalid status %r for instance %s",
                status,
                instance_id,
            )
            return None

        with self._lock:
            items = self._instance_todos.get(instance_id)
            if items is None or index < 0 or index >= len(items):
                return None
            items[index].status = normalized
            return [self._to_dict(item) for item in items]

    def get_all(self, instance_id: str) -> list[dict]:
        """Return the instance's current todo list.

        Args:
            instance_id: Owning instance identifier.

        Returns:
            Plain-dict copy of the stored todos, or ``[]`` if none.
        """
        with self._lock:
            items = self._instance_todos.get(instance_id, [])
            return [self._to_dict(item) for item in items]

    def clear(self, instance_id: str) -> None:
        """Drop the instance's todo list entirely.

        Args:
            instance_id: Owning instance identifier.
        """
        with self._lock:
            self._instance_todos.pop(instance_id, None)

    @staticmethod
    def _to_dict(item: TodoItem) -> dict:
        """Serialize a :class:`TodoItem` to a plain dict."""
        return {"index": item.index, "text": item.text, "status": item.status}