"""Sync repository for the bash-spawn durable registry.

Bash-tool orphan reaper Phase 1 (plan §D3 / §1 task 1.1).

Sync class (the flusher pattern, ``daemon/services/resource_monitor/
flusher.py``) operating through ``engine.begin()`` blocks. No async;
callers bridge with ``asyncio.to_thread`` (the F4 spawn-seam form).

Contract (stable signatures — the bash tool seam and the reaper service
both depend on these):

* :meth:`insert_open_row` — insert ONE open row (``reaped_at`` NULL).
  Accepts either a :class:`BashSpawnRecord` instance OR keyword fields.
  Returns the persisted record (with ``id`` populated).
* :meth:`list_open_by_instance` — open rows for one instance.
* :meth:`list_open_previous_boot` — open rows whose ``boot_id`` is NULL /
  empty / != ``current_boot_id`` (D3 adjudication 2: raced/legacy writers
  land as previous-boot). The D8 daemon_pid live-sibling guard is NOT
  applied here (process liveness is a service-level concern); ``my_pid``
  is accepted for signature parity and is not used to filter rows — the
  service applies the guard.
* :meth:`mark_reaped` — close one row (set ``reaped_at``/``reaped_by``/
  ``reap_reason``). Idempotent-ish: a missing row returns ``None``.

Restart-lane exclusion is structural (no writer outside the bash seam);
this repository never reaches into the restart lane.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, SQLModel, select

from daemon.repositories.bash_spawn.models import BashSpawnRecord
from daemon.services.timestamps import now_utc_iso


class BashSpawnRepository:
    """Sync repository over ``bash_spawn_registry``."""

    def __init__(self, engine) -> None:
        self.engine = engine

    def insert_open_row(self, record: Optional[BashSpawnRecord] = None, **fields: Any) -> BashSpawnRecord:
        """Insert one open row. Accepts a record instance or keyword fields."""
        if record is None:
            record = BashSpawnRecord(**fields)
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def list_open_by_instance(self, instance_id: str) -> list[BashSpawnRecord]:
        with Session(self.engine) as session:
            stmt = select(BashSpawnRecord).where(
                BashSpawnRecord.instance_id == instance_id,
                BashSpawnRecord.reaped_at.is_(None),
            )
            return list(session.exec(stmt).all())

    def list_open_all(self) -> list[BashSpawnRecord]:
        """ALL open rows (Phase 2, task 2.4 — the shutdown lane).

        The D8 daemon_pid live-sibling guard + self-exemption are
        applied by the caller (service level), not here — same
        contract as :meth:`list_open_previous_boot`.
        """
        with Session(self.engine) as session:
            stmt = select(BashSpawnRecord).where(
                BashSpawnRecord.reaped_at.is_(None),
            )
            return list(session.exec(stmt).all())

    def list_open_previous_boot(
        self, current_boot_id: Optional[str], my_pid: Optional[int] = None
    ) -> list[BashSpawnRecord]:
        """Open rows whose boot_id is NULL/empty/foreign (D3 adjudication 2).

        ``my_pid`` is accepted for signature parity; the D8 live-sibling
        guard is applied by the caller (service-level), not here.
        """
        if current_boot_id is None:
            current_boot_id = ""
        with Session(self.engine) as session:
            stmt = select(BashSpawnRecord).where(
                BashSpawnRecord.reaped_at.is_(None),
            )
            rows = list(session.exec(stmt).all())
        # Filter in Python so NULL/empty/foreign are all included uniformly
        # (SQL ``!=`` is NULL-propagating; doing it here keeps the
        # candidate shape explicit and testable).
        out: list[BashSpawnRecord] = []
        for r in rows:
            bid = r.boot_id
            if bid is None or bid == "" or bid != current_boot_id:
                out.append(r)
        return out

    def mark_reaped(
        self,
        row_id: int,
        reaped_by: str,
        reason: Optional[str] = None,
    ) -> Optional[BashSpawnRecord]:
        with Session(self.engine) as session:
            row = session.get(BashSpawnRecord, row_id)
            if row is None:
                return None
            row.reaped_at = now_utc_iso()
            row.reaped_by = reaped_by
            row.reap_reason = reason
            session.add(row)
            session.commit()
            session.refresh(row)
            return row
