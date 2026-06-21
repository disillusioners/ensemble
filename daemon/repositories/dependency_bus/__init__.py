"""Dependency Bus repository module (Phase D).

DB-backed parent-waits-for-children mechanism that replaces the
CorrelationManager in-memory pending map when the
``use_dependency_bus`` flag is ON. The table is
``dependency_watchers`` (one row per registered FollowUp), the
state machine is PENDING → FIRED | CANCELLED, and the atomic
state transition is the backpressure primitive that prevents
double-fire under concurrent terminal events.
"""

from .models import DependencyWatcher, DependencyWatcherState
from .repository import DependencyWatcherRepository

__all__ = [
    # Models
    "DependencyWatcher",
    "DependencyWatcherState",
    # Repository
    "DependencyWatcherRepository",
]
