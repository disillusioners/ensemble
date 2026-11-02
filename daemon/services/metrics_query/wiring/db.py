"""The DB-pull discipline (AM-11) — a ONE-SHOT connection per tick.

Every DB-backed adapter (F0.3's spine census, F8's store sizes, F10's
queue depth, F13's instance census, F18's audit counts) opens a **direct
``psycopg.connect`` per tick** and **closes it inside the tick**.

WHY NOT A PERSISTENT CONNECTION
--------------------------------
A persistent per-adapter connection is a POOL IN DISGUISE: it holds a
backend between ticks, which is exactly the idle-in-transaction class
cured in the v0.27.13 DB-pool lane.  The census is the precedent
(``daemon/services/metrics_census.py`` — ``connect_census`` + a
``finally: conn.close()``) and this module copies it verbatim.

THE TIMEBOUNDS ARE THE IDENTITY
--------------------------------
``connect_timeout`` bounds the CONNECT, ``statement_timeout`` bounds
each statement, and ``application_name`` names the reader so an
operator can see WHO is holding a backend.  A pull that exceeds its
budget fails the tick — it never blocks the sampler thread forever.

NOTHING HERE IS POOLED, AND NOTHING HERE RUNS ON THE REQUEST PATH
-----------------------------------------------------------------
The adapters run on the SAMPLER thread.  The query door reads the
PUBLISHED registry state only — never this module.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
import time
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

#: The connect budget (seconds) — the census precedent.
CONNECT_TIMEOUT_S = 3

#: The per-statement budget (milliseconds) — the census precedent.
STATEMENT_TIMEOUT_MS = 5_000

#: The application name prefix of the metrics-query readers.
APPLICATION_NAME = "ensemble-metrics-query"

#: The rate limit of the failure log (seconds, per statement
#: fingerprint).  A family whose query keeps failing used to be
#: swallowed at DEBUG — invisible in a live log, so a persistently
#: broken family looked exactly like a family with no observations
#: (A-3).  The failure is now a WARNING, but RATE LIMITED so a broken
#: family cannot flood the log at the sampler's cadence.
FAILURE_LOG_INTERVAL_S = 60.0

#: The last-logged timestamp per statement fingerprint.
_FAILURE_LOG_LOCK = threading.Lock()
_FAILURE_LOGGED_AT: dict[str, float] = {}


def _log_failure_rate_limited(kind: str, statement: str, exc: Exception) -> None:
    """Log a DB failure at WARNING, at most once per minute per query."""
    fingerprint = hashlib.sha256(
        f"{kind}:{statement}".encode("utf-8", "replace")
    ).hexdigest()[:12]
    now = time.monotonic()
    with _FAILURE_LOG_LOCK:
        last = _FAILURE_LOGGED_AT.get(fingerprint)
        if last is not None and (now - last) < FAILURE_LOG_INTERVAL_S:
            return
        _FAILURE_LOGGED_AT[fingerprint] = now
    logger.warning(
        "metrics wiring: DB %s failed (rate-limited to one line per %.0fs "
        "per query): %s",
        kind,
        FAILURE_LOG_INTERVAL_S,
        exc,
    )


def connect_once(
    dsn: str,
    *,
    application_name: str = APPLICATION_NAME,
    connect_timeout_s: int = CONNECT_TIMEOUT_S,
    statement_timeout_ms: int = STATEMENT_TIMEOUT_MS,
) -> Any:
    """Open ONE direct connection (never a pool).

    The caller MUST close it inside the tick — use :func:`one_shot`.
    """
    import psycopg

    return psycopg.connect(
        dsn,
        connect_timeout=connect_timeout_s,
        application_name=application_name,
        options=f"-c statement_timeout={statement_timeout_ms}",
    )


@contextlib.contextmanager
def one_shot(
    dsn: str | None,
    *,
    application_name: str = APPLICATION_NAME,
    connect: Callable[..., Any] | None = None,
) -> Iterator[Any]:
    """Yield a one-shot connection and CLOSE it inside the tick.

    ``None`` DSN ⇒ no connection (the family renders no series).  A
    connect/close failure is swallowed and yields ``None`` — a broken
    database degrades the family, it never kills the tick.
    """
    if not dsn:
        yield None
        return
    opener = connect or connect_once
    conn = None
    try:
        conn = opener(dsn, application_name=application_name)
    except Exception as exc:  # noqa: BLE001 — a dead DB degrades the family
        # A-3 — a persistently failing connect is a WARNING (rate
        # limited), not a DEBUG line: a family whose database is gone
        # must be VISIBLE in the log, and it must not flood it.
        _log_failure_rate_limited("connect", application_name, exc)
        yield None
        return
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — a close failure is not fatal
            logger.debug("metrics wiring: DB close failed", exc_info=True)


def fetch_rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Run one query on a one-shot connection and return the rows.

    ``None`` connection ⇒ no rows (the family renders no series).

    A failing query is a WARNING (rate limited per statement, A-3): the
    family degrades to "no series", and the log says WHY — a silently
    broken family is indistinguishable from a family with no
    observations.
    """
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    except Exception as exc:  # noqa: BLE001 — a failed query degrades the family
        _log_failure_rate_limited("query", sql, exc)
        return []


__all__ = [
    "APPLICATION_NAME",
    "CONNECT_TIMEOUT_S",
    "STATEMENT_TIMEOUT_MS",
    "connect_once",
    "fetch_rows",
    "one_shot",
]
