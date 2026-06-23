"""Dependency Bus repository module.

DB-backed parent-waits-for-children mechanism — the SOLE completion
authority. The table is ``dependency_watchers`` (one row per
registered FollowUp), the state machine is PENDING → FIRED |
CANCELLED, and the atomic state transition is the backpressure
primitive that prevents double-fire under concurrent terminal
events.
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
