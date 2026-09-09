"""SQLModel table definitions for the ``ens-db`` tool family.

Currently exposes :class:`RepairLog` — see ``models.py`` for the
audit-log shape. The module is imported by
:mod:`daemon.manager` at module-load time so ``RepairLog`` is
registered with ``SQLModel.metadata`` before
``SQLModel.metadata.create_all`` runs at startup (mirrors the
``InfraAsset`` registration pattern documented at
``daemon/manager.py:560-565``).
"""

from __future__ import annotations

from .models import RepairLog

__all__ = ["RepairLog"]
