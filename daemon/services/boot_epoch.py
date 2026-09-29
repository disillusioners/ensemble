"""Process-wide daemon boot epoch — stop-frozen heartbeat amnesty anchor.

Incident r-20260929-170301-0cb2 (2026-09-29): a live promote's own
daemon stop froze RUNNING task turns mid-flight — their
``last_heartbeat_at`` stopped advancing the moment the process died.
The replacement daemon booted, passed every gate, and boot-time
JobRecovery DELIBERATELY left those tasks PROCESSING ("instance alive
(running)"). ~120s later the readiness queue_freshness probe read the
frozen beats as evidence of a CURRENT stall, ``/readyz`` went red
mid-soak, and the promote auto-rolled-back — the promote's own stop
had manufactured the failure it was rolled back for. The asymmetry
(readiness threshold ~120s vs StaleTaskRecovery 10min) makes this a
STANDING hazard: any promote stopping a busy daemon self-fails its
own soak.

The amnesty: a heartbeat older than the current process's boot epoch
was written by a DEAD process — it cannot prove a stall of THIS
daemon. The task is either resumed (fresh beats resume → normal
accounting) or reaped by StaleTaskRecovery measured from the new
epoch. Consumers of heartbeat-staleness evidence therefore treat the
boot epoch as a floor:

* readiness ``queue_freshness`` (daemon/services/readiness.py) —
  only RUNNING-task heartbeats at/after the epoch participate in the
  MAX() staleness aggregate; pre-epoch beats are invisible.
* StaleTaskRecovery (daemon/repositories/task/repository.py,
  ``find_cancellable_tasks`` / ``find_stale_running_tasks``) — the
  stale clock for stop-frozen tasks runs from the epoch: they are
  reaped ~boot+threshold, never instantly (a beat frozen 9 minutes
  before boot must NOT be reaped one minute after it) and never
  skipped (an abandoned task still reaps once the daemon outlives
  the threshold).

Signal preservation (the invariant the amnesty may NOT break): a
task going stale while the daemon has been continuously up — the
beat is at/after the epoch — degrades readiness exactly as before.

The epoch is captured in DB time (``SELECT now()``) so it lives in
the same clock the readiness aggregate computes ages in (SQL-side
``now()``); it is stored as NAIVE-UTC digits, matching the
``last_heartbeat_at`` bind frame (see daemon/services/timestamps.py
DC-A). Capture is best-effort: on failure the epoch stays ``None``
and every consumer falls back to the pre-amnesty (stricter)
behavior — the hazard can recur on that boot, but nothing new can
fail.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Sentinel meaning "no boot epoch / amnesty off". Substituted where a
# datetime bind is required but no epoch was captured: every real
# heartbeat is at/after year 1, so the epoch filter degenerates to a
# no-op and consumers behave exactly as they did pre-amnesty.
# (Datetime, not -infinity float, so both PostgreSQL timestamps and
# SQLite's lexicographic TEXT comparison stay well-defined.)
BOOT_EPOCH_FLOOR = datetime.min

# Process-global epoch (naive-UTC digits). ``None`` = never captured.
_boot_epoch: datetime | None = None


def get_boot_epoch() -> datetime | None:
    """Return this process's boot epoch, or ``None`` if never captured.

    Cheap (O(1) attribute read) — safe on the readiness refresh path
    and inside StaleTaskRecovery's sweep thread.
    """
    return _boot_epoch


def set_boot_epoch(value: datetime | None) -> None:
    """Explicitly set (or clear) the process boot epoch.

    Test/ops seam — the daemon itself captures via
    :func:`capture_boot_epoch`. Accepts naive-UTC or aware datetimes
    (aware values are normalized to naive-UTC digits, matching the
    heartbeat bind frame).
    """
    global _boot_epoch
    _boot_epoch = _to_naive_utc(value)


def capture_boot_epoch(engine: Engine) -> datetime | None:
    """Capture the daemon boot epoch as DB time and store it.

    Idempotent per process: the FIRST successful capture wins (the
    epoch of a process is fixed at its boot; a re-invoked lifespan in
    the same process must not silently move it). Reads the clock from
    the daemon's own database — PG ``now()`` / SQLite
    ``CURRENT_TIMESTAMP`` — so epoch-vs-heartbeat comparisons share
    one clock with the SQL-side freshness arithmetic. Best-effort:
    never raises; on failure returns ``None`` WITHOUT setting the
    epoch (callers fall back to pre-amnesty behavior).
    """
    global _boot_epoch
    if _boot_epoch is not None:
        return _boot_epoch
    statement = (
        sa_text("SELECT now()")
        if engine.dialect.name == "postgresql"
        else sa_text("SELECT CURRENT_TIMESTAMP")
    )
    try:
        with engine.connect() as conn:
            raw = conn.execute(statement).scalar()
        epoch = _to_naive_utc(raw)
    except Exception as exc:
        # Capture failure must not crash boot: epoch stays None and
        # every consumer falls back to legacy stricter freshness
        # semantics (the pre-amnesty behavior). See module docstring.
        logger.warning(
            "Boot-epoch capture failed (stop-frozen heartbeat amnesty "
            "disabled for this process — legacy freshness semantics): %s",
            exc,
        )
        return None
    if epoch is None:
        logger.warning(
            "Boot-epoch capture returned no usable timestamp (amnesty "
            "disabled for this process — legacy freshness semantics)"
        )
        return None
    _boot_epoch = epoch
    logger.info("Boot epoch captured (DB clock): %s", epoch.isoformat())
    return epoch


def _to_naive_utc(value: datetime | str | None) -> datetime | None:
    """Normalize a DB timestamp to naive-UTC digits.

    psycopg returns aware datetimes in the (UTC-pinned) session zone;
    SQLite returns ISO strings. ``None``/garbage pass through as
    ``None`` so callers can fail soft.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value
