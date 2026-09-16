"""Shared engine factory + builders for the service-tool test files.

Eight byte-identical file-backed SQLite engines were duplicated across
the service-tool unit + integration suites — each carrying its own
copy of:

* a tmpdir path
* ``NullPool + WAL + busy_timeout`` connect_args
* the PRAGMA-on-connect event listener

Two drifts had crept in by inspection:

* ``busy_timeout`` was set to ``30000`` in every service-tool file,
  while the broader test house (``routers``, ``tools``,
  ``critical_notes``) uses ``10000``.
* the engine name + db-path label differed file-by-file.

This module consolidates the engine factory, picks the **house value
of 10000** (matching ``tests/unit/routers/test_jobs_mission_id_filter.py``
and friends), and exposes the per-file ``_set_sqlite_pragmas`` helper
that pins the dialect-neutral contract for every caller. Tests that
demonstrably need a different busy_timeout should pass
``busy_timeout_seconds`` directly (none currently do).

Also exposed: :func:`seed_service_row` — the inline
``ServiceTracking(...)`` + ``session.add/commit/refresh`` pattern that
the flag-OFF byte-identical integration test repeated five times.
The other suites use the same shape in a single site each; the helper
collapses all of them without changing observable behavior.

Usage: each test file replaces its ~30-line local fixture body with a
4-line wrapper that calls :func:`make_file_backed_engine` and yields
the engine, keeping the per-file fixture name intact (so test
signatures don't change). See
``tests/integration/test_service_tool_kill_site_exemption.py`` for the
canonical migration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    pass


#: House value for ``PRAGMA busy_timeout``. Matches the broader test
#: house (``tests/unit/routers/``, ``tests/unit/tools/``,
#: ``tests/unit/test_critical_notes_lifecycle.py``). Per-file overrides
#: can set this higher; none currently do — the service-tool suite
#: inherited the 30000 drift, but no test demonstrably relied on it
#: (the busiest concurrent write is the inline ``mark_exited``
#: UPDATE, which completes well under 10s on every fixture).
DEFAULT_BUSY_TIMEOUT_SECONDS: int = 10000


def make_file_backed_engine(
    tmp_path: Path,
    *,
    busy_timeout_seconds: int = DEFAULT_BUSY_TIMEOUT_SECONDS,
) -> Engine:
    """Build a file-backed SQLite engine with the house PRAGMA set.

    Returns a fresh engine; the caller owns the ``dispose()`` lifecycle
    (the per-file fixtures below yield + dispose). ``tmp_path`` ensures
    the caller isolates per-test (each pytest tmp_path is unique).
    """
    db_path = tmp_path / "service-tool-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute(f"PRAGMA busy_timeout={busy_timeout_seconds}")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


def seed_service_row(
    engine: Engine,
    *,
    name: str,
    pid: "int | None",
    start_time: "int | None",
    status: str = "running",
    cwd: str | None = None,
    started_by_instance_id: str = "test-instance",
    started_by_agent_id: str = "tester",
    log_path: str | None = None,
    command: str = "echo hello",
) -> Any:
    """Insert one ``service_tracking`` row, returning the refreshed
    ORM object.

    Collapses the inline ``ServiceTracking(...)`` + ``session.add /
    commit / refresh`` shape the flag-OFF integration test repeated
    five times (and the reconciliation suite used once). The shape is
    byte-identical to the pre-refactor copies — same defaults, same
    column set, same commit/refresh — only the source of truth moves
    here. Tests that need a back-dated ``created_at`` (the A3
    eternal-``starting`` reaper trick) should keep their inline
    construction or extend this helper with an optional
    ``age_seconds`` kwarg.
    """
    from sqlmodel import Session

    from daemon.repositories.service_tool.models import ServiceTracking

    row = ServiceTracking(
        name=name,
        command=command,
        pid=pid,
        start_time=start_time,
        cwd=cwd or str(Path.cwd()),
        status=status,
        started_by_instance_id=started_by_instance_id,
        started_by_agent_id=started_by_agent_id,
        log_path=log_path or str(Path.cwd() / f".tmp_svc_{name}.log"),
    )
    with Session(engine) as s:
        s.add(row)
        s.commit()
        s.refresh(row)
        return row


__all__ = [
    "DEFAULT_BUSY_TIMEOUT_SECONDS",
    "make_file_backed_engine",
    "seed_service_row",
]
