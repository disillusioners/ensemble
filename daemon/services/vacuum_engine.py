"""Shared per-cycle VACUUM engine factory (tidier pass T1, commission
``maintenance-tabs-vacuum``).

Single home for the ``create_engine`` shape TWO maintenance lanes were
duplicating byte-for-byte:

* ``VacuumMaintenanceJob._build_vacuum_engine``
  (``daemon/services/vacuum_maintenance.py``) — the scheduled plain
  VACUUM (ANALYZE) cycle;
* ``CheckpointCleanupJob._build_vacuum_full_engine``
  (``daemon/services/maintenance.py``) — the interactive VACUUM FULL
  escalation engine.

Neutral on purpose: the module is NOT nested in either consumer file
so neither lane owns the other's import (and the scheduled lane's
regression-surface pins — ``tests/integration/test_vacuum_maintenance_job_no_full.py``
— keep their file-local scope).

Engine contract (preserved exactly from both former builders):

* URL = the SHARED engine's URL rendered with credentials
  (``hide_password=False``) — the per-cycle engine connects to the
  same DB as the daemon.
* ``connect_args={'options': …, 'autocommit': True}`` — VACUUM (plain
  or FULL) cannot run inside an explicit transaction (PG raises
  25001), so each statement is its own implicit txn; the ``options``
  string carries the three per-call timeouts (statement / lock /
  idle-in-transaction) and MUST end with the trailing
  ``PG_SESSION_CONNECT_ARGS['options']`` so the FULL/VACUUM connection
  inherits the daemon's UTC session frame (libpq merges server
  command-line options).
* ``poolclass=NullPool`` — per-cycle engine, ``dispose()`` in the
  caller's ``finally``; pooling would persist one connection's session
  timeouts and smudge the per-cycle timeouts across cycles.
* ``echo=False``.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from daemon.repositories.factory import PG_SESSION_CONNECT_ARGS


def _null_pool():
    """Lazy import of ``NullPool`` to keep the module import graph
    slim (single home for the two former per-file copies)."""
    from sqlalchemy.pool import NullPool

    return NullPool


def create_vacuum_engine(
    shared_engine: Engine, *, statement_ms: int, lock_ms: int, idle_ms: int
) -> Engine:
    """Build ONE per-cycle dedicated autocommit VACUUM engine.

    Args:
        shared_engine: the daemon's shared engine — only its ``.url``
            is read (rendered with credentials for the fresh engine).
        statement_ms: ``statement_timeout`` in ms.
        lock_ms: ``lock_timeout`` in ms.
        idle_ms: ``idle_in_transaction_session_timeout`` in ms.

    The ``options`` string shape is the exact one both former builders
    used (options merged via libpq server command-line syntax, the
    daemon's UTC session frame riding the SAME string). The caller
    owns the engine lifecycle: ``dispose()`` in its ``finally``.
    """
    url = shared_engine.url.render_as_string(hide_password=False)
    options_str = (
        f"-c statement_timeout={statement_ms} "
        f"-c lock_timeout={lock_ms} "
        f"-c idle_in_transaction_session_timeout="
        f"{idle_ms} "
        f"{PG_SESSION_CONNECT_ARGS['options']}"
    )
    return create_engine(
        url,
        echo=False,
        connect_args={"options": options_str, "autocommit": True},
        # Per-cycle engine — no pool. ``NullPool`` ensures every
        # connect/dispose is fresh; pooling the VACUUM engine would
        # persist a connection's session timeouts and smudge the
        # per-cycle timeouts across cycles.
        poolclass=_null_pool(),
    )
