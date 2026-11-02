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
import logging
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

#: The connect budget (seconds) — the census precedent.
CONNECT_TIMEOUT_S = 3

#: The per-statement budget (milliseconds) — the census precedent.
STATEMENT_TIMEOUT_MS = 5_000

#: The application name prefix of the metrics-query readers.
APPLICATION_NAME = "ensemble-metrics-query"


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
    except Exception:  # noqa: BLE001 — a dead DB degrades the family
        logger.debug("metrics wiring: DB connect failed", exc_info=True)
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
    """
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    except Exception:  # noqa: BLE001 — a failed query degrades the family
        logger.debug("metrics wiring: DB query failed", exc_info=True)
        return []


__all__ = [
    "APPLICATION_NAME",
    "CONNECT_TIMEOUT_S",
    "STATEMENT_TIMEOUT_MS",
    "connect_once",
    "fetch_rows",
    "one_shot",
]
