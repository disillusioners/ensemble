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
    "complete": "done",
    "closed": "done",
    "resolved": "done",
    "finished": "done",
    "started": "in_progress",
    "wip": "in_progress",
    "doing": "in_progress",
    "active": "in_progress",
    "in progress": "in_progress",
    "in-progress": "in_progress",
    "inprogress": "in_progress",
    "in_progress": "in_progress",
    "cancelled": "pending",
    "canceled": "pending",
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
        comment: Optional user-supplied annotation. Separate from ``text``
            — editing ``text`` is not allowed; ``comment`` is a side-channel
            for human feedback on a completed item (e.g. corrections,
            follow-up notes). Defaults to empty string.
    """

    index: int
    text: str
    status: str
    comment: str = ""


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
            TodoItem(index=i, text=text, status="pending", comment="")
            for i, text in enumerate(items)
        ]
        with self._lock:
            self._instance_todos[instance_id] = new_items
            return [self._to_dict(item) for item in new_items]

    def update(self, instance_id: str, index: int, status: str) -> dict | None:
        """Update the status of a single item and return the reminder payload.

        The return value is a dict ``{"todos": [...], "reminder": str}`` so
        callers (notably the ``todo_update`` tool) get the updated list AND
        a human-readable reminder string in one round-trip. The reminder
        points at the next pending item, prefixed with the user's comment
        (when present) so the agent sees human feedback on the just-completed
        item in the same breath as the next task to work on.

        Reminder shape:
            * When ``status == "done"`` and the completed item carries a
              non-empty ``comment``: ``"User commented: {comment}\\n{next}"``
              where ``{next}`` is the original next-pending reminder (or the
              all-completed message when no item remains).
            * Otherwise: ``"⏭️ Next: {text}"`` or ``"All items completed! ✅"``
              — the same wording the tool used to produce inline.

        Args:
            instance_id: Owning instance identifier.
            index: Position of the item to update.
            status: New status; must be one of ``pending``, ``in_progress``,
                ``done``.

        Returns:
            Dict with keys ``todos`` (full list snapshot) and ``reminder``
            (formatted string), or ``None`` if the status is invalid or the
            index does not exist for the instance.
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
            target = items[index]
            target.status = normalized
            completed_comment = target.comment
            # Compute reminder against the now-mutated list snapshot. We
            # use _to_dict on each item so we don't depend on the dataclass
            # vs dict asymmetry.
            snapshot = [self._to_dict(item) for item in items]
            next_pending = next(
                (t for t in snapshot if t["status"] == "pending"),
                None,
            )
            if next_pending is not None:
                base_reminder = f"\n\n⏭️ Next: {next_pending['text']}"
            else:
                base_reminder = "\n\nAll items completed! ✅"
            if normalized == "done" and completed_comment:
                reminder = f"User commented: {completed_comment}" + base_reminder
            else:
                reminder = base_reminder
            return {"todos": snapshot, "reminder": reminder}

    def set_comment(self, instance_id: str, index: int, comment: str) -> dict:
        """Set the comment on the item at ``index``.

        Args:
            instance_id: Owning instance identifier.
            index: Position of the item to annotate.
            comment: The annotation text. Empty string clears the comment.

        Returns:
            The updated item as a plain dict.

        Raises:
            ValueError: If the instance has no todo list or the index is
                out of range. Matches the ``update``-style error contract
                but raised (not returned) so HTTP callers can map it to a
                404/400 response.
        """
        with self._lock:
            items = self._instance_todos.get(instance_id)
            if items is None or index < 0 or index >= len(items):
                raise ValueError(
                    f"index {index} out of range for instance {instance_id!r} "
                    f"(list length: {0 if items is None else len(items)})"
                )
            items[index].comment = comment
            return self._to_dict(items[index])

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
        return {
            "index": item.index,
            "text": item.text,
            "status": item.status,
            "comment": item.comment,
        }