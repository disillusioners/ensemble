"""Bash-spawn durable registry package (bash-tool orphan reaper Phase 1).

Exposes:
* :mod:`daemon.repositories.bash_spawn.models` — :class:`BashSpawnRecord`
  SQLModel + index-name constants.
* :mod:`daemon.repositories.bash_spawn.repository` —
  :class:`BashSpawnRepository` sync class.
"""

from __future__ import annotations

from .models import (
    IDX_BOOT_OPEN,
    IDX_INSTANCE_OPEN,
    IDX_PGID,
    UQ_TRACKING_ID,
    BashSpawnRecord,
)
from .repository import BashSpawnRepository

__all__ = [
    "BashSpawnRecord",
    "BashSpawnRepository",
    "IDX_INSTANCE_OPEN",
    "IDX_BOOT_OPEN",
    "IDX_PGID",
    "UQ_TRACKING_ID",
]
