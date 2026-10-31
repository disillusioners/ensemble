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
  ``reap_reason``). CAS (minor-1): conditional ``UPDATE ... WHERE
  reaped_at IS NULL`` guarded by rowcount — a second sweeper never
  overwrites the first's close; the rowcount is exposed on the returned
  row as ``cas_rowcount`` (lost-race detection: log, don't fail). A
  missing row returns ``None``.
* :meth:`prune_reaped_older_than` — minor-4 one-shot retention prune of
  REAPED rows older than the window (called by the boot reconcile;
  open rows are never touched).

Restart-lane exclusion is structural (no writer outside the bash seam);
this repository never reaches into the restart lane.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, update
from sqlmodel import Session, select

from daemon.repositories.bash_spawn.models import BashSpawnRecord
from daemon.services.timestamps import now_utc_iso

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

# minor-4: closed-row retention window (boot-reconcile housekeeping;
# keep in lockstep with bash_spawn_reaper.BASH_SPAWN_RETENTION_DAYS).
BASH_SPAWN_RETENTION_DAYS = 30


class BashSpawnRepository:
    """Sync repository over ``bash_spawn_registry``."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def insert_open_row(
        self, record: BashSpawnRecord | None = None, **fields: Any
    ) -> BashSpawnRecord:
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
        self, current_boot_id: str | None, my_pid: int | None = None
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
        reason: str | None = None,
    ) -> BashSpawnRecord | None:
        """Close one row — CAS (minor-1).

        The close is a conditional ``UPDATE ... WHERE reaped_at IS NULL``
        guarded by rowcount: a SECOND sweeper NEVER overwrites the
        first's ``reaped_by``/``reap_reason``. Returns the persisted row
        (post-CAS state) or ``None`` when the row does not exist. The
        UPDATE rowcount is exposed on the returned row as the
        ``cas_rowcount`` attribute so callers can DETECT lost races
        (log, don't fail): ``1`` = this caller performed the close,
        ``0`` = the row was already closed by another sweeper (the
        existing close is kept) — or the row was missing.
        """
        closed_at = now_utc_iso()
        with Session(self.engine) as session:
            stmt = (
                update(BashSpawnRecord)
                .where(BashSpawnRecord.id == row_id)
                .where(BashSpawnRecord.reaped_at.is_(None))
                .values(
                    reaped_at=closed_at,
                    reaped_by=reaped_by,
                    reap_reason=reason,
                )
            )
            result = session.exec(stmt)
            session.commit()
            rowcount = int(result.rowcount or 0)
            row = session.get(BashSpawnRecord, row_id)
            if row is None:
                return None
            try:
                # Expose the CAS outcome (lost-race detection) without
                # changing the stable return contract. SQLModel's
                # pydantic setattr silently drops unknown attributes,
                # so write through ``object.__setattr__``.
                object.__setattr__(row, "cas_rowcount", rowcount)
            except Exception:  # noqa: BLE001 — attribute is best-effort
                pass
            return row

    def prune_reaped_older_than(self, days: int = BASH_SPAWN_RETENTION_DAYS) -> int:
        """Delete REAPED rows older than ``days`` (minor-4 housekeeping).

        ONE-SHOT — called once inside the boot reconcile (NOT a polling
        service). OPEN rows are never touched; the partial open-subset
        indexes are unaffected. ``reaped_at`` is same-format aware-UTC
        ISO (``now_utc_iso()``), so the bound compares lexicographically
        correct. Returns the number of rows deleted.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()
        with Session(self.engine) as session:
            stmt = delete(BashSpawnRecord).where(
                BashSpawnRecord.reaped_at.is_not(None),
                BashSpawnRecord.reaped_at < cutoff,
            )
            result = session.exec(stmt)
            session.commit()
            return int(result.rowcount or 0)
