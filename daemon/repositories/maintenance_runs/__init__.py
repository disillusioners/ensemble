"""Repository package for the ``maintenance_runs`` audit table.

* ``models`` — :class:`MaintenanceRun` SQLModel + FINAL schema (AM-15).
* ``repository`` — :class:`MaintenanceRunsRepository` sync CRUD with
  ``asyncio.to_thread`` bridging at the call site.

Single source of truth for the Section 1 audit channel (AM-15):
``maintenance_runs`` IS the audit trail (decision inputs + pre-state +
outcome + failure). No separate channel.
"""

from .models import MaintenanceRun
from .repository import MaintenanceRunsRepository

__all__ = ["MaintenanceRun", "MaintenanceRunsRepository"]