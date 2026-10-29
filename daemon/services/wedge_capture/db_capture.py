"""The DB sub-capture + the metadata row write (D11 / D12 / D22 / T10 / T11).

THE IRONY THIS SOLVES (RK-1)
----------------------------
The capture path needs the DB it may be capturing **exhausted**. The
saver pool is hard-capped at 5 connections
(``daemon/persistence.py``) and is the thing that may be saturated at
capture time (the H2 shape). Borrowing it would make the instrument
depend on the resource it is diagnosing.

Therefore:

* the capture NEVER touches ``adapter._pool`` / the saver pool — the
  writer pack asserts it with a spy pool that raises on ANY access;
* the DB work runs over an **own one-shot SYNC** ``psycopg``
  connection, opened by the watchdog thread, with ``connect_timeout``
  bounded by the MODULE-LOCAL ``CAPTURE_CONNECT_TIMEOUT_S`` (10 s —
  A8: ``daemon/constants.py`` is seal-pinned, so the constant lives
  here rather than growing a daemon import) and a per-statement
  timeout (``CAPTURE_STATEMENT_TIMEOUT_MS``), BOTH scaled to the
  capture's remaining phase budget (W2);
* ``application_name='wedge-capture'`` makes the capture's own
  connection identifiable in ``pg_stat_activity`` — this is the
  CAPTURE path only; the daemon's own connections carry an empty
  ``application_name`` (a note-for-architect, out of scope here);
* there is NO loop bridge: a ``run_coroutine_threadsafe`` onto the
  loop would hang the capture behind the very stall it observes.

``connect_timeout`` covers TCP, NOT DNS (A5 / RK-27) — the DSN is
resolved ONCE at watchdog start for that reason.

THE READS
----------
* the executing turns — ``tasks`` rows with ``status='running'``,
  joined to ``instances`` for the agent identity;
* the checkpoint-store census — ``pg_stat_user_tables`` drift
  (``n_live_tup`` / ``reltuples``) plus the relation sizes;
* the resume history — the tasks carrying ``auto_continued_at``;
* the ACTIVITY CENSUS (plan Track-1 item 6) — ``pg_stat_activity``
  grouped by state (total backends = the sum, per-state
  oldest-transaction age), with a capped per-backend detail list for
  the incident family's bucket — ``idle in transaction`` — carrying
  each backend's transaction age (``now() - xact_start``, seconds)
  and the age of the OLDEST idle-in-txn backend;
* the boot epoch.

``msg_count`` is NOT cheaply available in the application DB (the
messages live in the LangGraph checkpoint store), so it is reported as
``None`` with an explicit reason — never a wrong number.

NO QUERY-PLAN CAPTURE
---------------------
Out of scope per Addendum 2: the ``aget`` latest-checkpoint read is
index-driven; the cold path is byte-identical.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("daemon.wedge_capture.db_capture")

#: The capture connection's ``application_name`` (D11).
CAPTURE_APPLICATION_NAME = "wedge-capture"

#: The DB connect timeout (SECONDS) for the capture's own one-shot
#: connections — MODULE-LOCAL by design (A8): ``daemon/constants.py``
#: is seal-pinned, and this package must not grow a daemon import to
#: read a timeout. Carries the boot-DB timeout's 10 s intent; W2's
#: ``budget_s`` scaling clamps it further at capture time.
CAPTURE_CONNECT_TIMEOUT_S = 10

#: The per-statement timeout (MILLISECONDS) for the capture's reads
#: and the metadata-row insert (W2: scaled to the phase budget at
#: capture time).
CAPTURE_STATEMENT_TIMEOUT_MS = 5000

#: The DB sub-capture's typed statuses.
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_DENIED = "denied"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"

#: How many executing turns to report (the payload is a SUMMARY, not
#: a dump — the capture must stay small).
MAX_EXECUTING_TURNS = 50

#: How many idle-in-transaction backends to report INDIVIDUALLY (the
#: same summary-not-dump discipline as ``MAX_EXECUTING_TURNS``; the
#: grouped census and the total cover the rest).
MAX_IDLE_IN_TXN_BACKENDS = 20

#: The ``pg_stat_activity`` state bucket the 2026-10-28 incident
#: family cares about most (idle-in-transaction connection leaks).
IDLE_IN_TXN_STATE = "idle in transaction"

#: The bucket name a ``NULL`` ``pg_stat_activity.state`` renders under
#: (background workers / auxiliary processes carry no state).
UNKNOWN_STATE_BUCKET = "unknown"

#: The checkpoint-store tables the census looks at.
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations")


#: The typed statuses of the metadata-row write.
ROW_STATUS_OK = "ok"
ROW_STATUS_FAILED = "failed"
#: The budget-exhausted status (W2) — the phase was SKIPPED, not
#: attempted against an exhausted budget. The snapshot payload and the
#: row-write result both carry ``reason="skipped:budget"``.
STATUS_SKIPPED = "skipped"
SKIPPED_BUDGET_REASON = "skipped:budget"


def _apply_budget(
    *,
    budget_s: float,
    connect_timeout_s: int,
    statement_timeout_ms: int,
    statements: int,
) -> tuple[int, int] | None:
    """Scale one DB phase's timeouts to its phase budget (W2).

    The capture deadline is END-TO-END: a DB phase may never run
    against a budget the capture does not have. Returns ``None`` when
    the budget is exhausted — the caller SKIPS the phase and records
    ``skipped:budget``. Otherwise the connect gets at most 40% of the
    phase budget (``psycopg``'s ``connect_timeout`` is integral
    seconds, so the granularity floor is 1 s) and each statement gets
    an equal share of what remains.

    Without a budget (``budget_s=None`` at the call site) the caller's
    defaults stand — the hermetic shape the unit packs drive.
    """
    if budget_s <= 0:
        return None
    connect = max(1, min(connect_timeout_s, int(budget_s * 0.4)))
    remaining_ms = max(0.0, budget_s - connect) * 1000.0
    statement = max(
        1,
        min(statement_timeout_ms, int(remaining_ms) // max(1, statements)),
    )
    return connect, statement


def _strip_driver(dsn: str) -> str:
    """``postgresql+psycopg://`` → ``postgresql://`` (raw psycopg)."""
    if "://" not in dsn:
        return dsn
    scheme, _, rest = dsn.partition("://")
    if "+" in scheme:
        scheme = scheme.split("+", 1)[0]
    return f"{scheme}://{rest}"


def resolve_dsn(engine: Any = None) -> str | None:
    """The DSN for the capture's own connection.

    Preference order:

    1. the RUNNING engine's URL (the process's truth — no credential
       re-derivation, no env precedence guess);
    2. ``POSTGRES_URL``;
    3. the ``POSTGRES_*`` parts.

    Returns:
        The DSN, or ``None`` when the deployment is not PostgreSQL
        (a SQLite backend ⇒ the DB sub-capture is simply unavailable).
    """
    if engine is not None:
        url = getattr(engine, "url", None)
        if url is not None:
            try:
                rendered = url.render_as_string(hide_password=False)
            except Exception:  # pragma: no cover — defensive
                rendered = None
            if rendered:
                return _strip_driver(rendered)
    direct = os.environ.get("POSTGRES_URL")
    if direct:
        return _strip_driver(direct)
    host = os.environ.get("POSTGRES_HOST")
    db = os.environ.get("POSTGRES_DB")
    if host and db:
        # stdlib-only: no import failure surface — kept local to its one use site.
        from urllib.parse import quote_plus

        user = os.environ.get("POSTGRES_USER", "")
        password = os.environ.get("POSTGRES_PASSWORD", "")
        port = os.environ.get("POSTGRES_PORT", "5432")
        if user:
            auth = f"{quote_plus(user)}:{quote_plus(password)}@"
        else:
            auth = ""
        return f"postgresql://{auth}{host}:{port}/{db}"
    return None


def _connect(
    dsn: str,
    *,
    connect_timeout_s: int,
    statement_timeout_ms: int,
) -> "psycopg.Connection":
    """Open the own one-shot connection (raises — the caller guards)."""
    # lazy: psycopg is the optional driver — a missing driver must not break module import.
    import psycopg

    return psycopg.connect(
        dsn,
        connect_timeout=connect_timeout_s,
        application_name=CAPTURE_APPLICATION_NAME,
        options=f"-c statement_timeout={statement_timeout_ms}",
    )


def capture_db_snapshot(
    dsn: str | None,
    *,
    boot_epoch: str | None = None,
    connect_timeout_s: int = CAPTURE_CONNECT_TIMEOUT_S,
    statement_timeout_ms: int = CAPTURE_STATEMENT_TIMEOUT_MS,
    budget_s: float | None = None,
) -> dict[str, Any]:
    """The DB-side sub-capture.

    Never raises. Returns a dict whose ``status`` is one of ``ok`` /
    ``unavailable`` / ``denied`` / ``timeout`` / ``error`` / — since
    W2 — ``skipped`` (``reason="skipped:budget"``): the capture's phase
    budget was exhausted BEFORE this phase ran, so it was never
    attempted.

    Args:
        budget_s: This phase's budget in seconds (the capture's
            remaining budget, scaled — W2). ``None`` keeps the raw
            ``connect_timeout_s`` / ``statement_timeout_ms`` defaults —
            the hermetic shape the unit packs drive directly.
    """
    if not dsn:
        return {"status": STATUS_UNAVAILABLE, "reason": "no_postgres_dsn"}

    scaled = (
        None
        if budget_s is None
        else _apply_budget(
            budget_s=budget_s,
            connect_timeout_s=connect_timeout_s,
            statement_timeout_ms=statement_timeout_ms,
            # 3 application reads + 2 census statements (item 6).
            statements=5,
        )
    )
    if budget_s is not None and scaled is None:
        return {
            "status": STATUS_SKIPPED,
            "reason": SKIPPED_BUDGET_REASON,
            "boot_epoch": boot_epoch,
            "executing_turns": [],
            "executing_turns_count": 0,
            "checkpoint_store_census": {},
            "activity_census": {},
            "resume_history": [],
        }
    if scaled is not None:
        connect_timeout_s, statement_timeout_ms = scaled

    payload: dict[str, Any] = {
        "status": STATUS_OK,
        "boot_epoch": boot_epoch,
        "executing_turns": [],
        "executing_turns_count": 0,
        "checkpoint_store_census": {},
        "activity_census": {},
        "resume_history": [],
    }

    connection = None
    try:
        connection = _connect(
            dsn,
            connect_timeout_s=connect_timeout_s,
            statement_timeout_ms=statement_timeout_ms,
        )
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        message = str(exc).lower()
        if "timeout" in message or name in ("OperationalError", "InterfaceError"):
            payload["status"] = STATUS_TIMEOUT
        elif "permission" in message or "denied" in message:
            payload["status"] = STATUS_DENIED
        else:
            payload["status"] = STATUS_UNAVAILABLE
        payload["reason"] = f"{name}: {exc}"
        return payload

    try:
        with connection.cursor() as cursor:
            payload["executing_turns"], payload["executing_turns_count"] = (
                _read_executing_turns(cursor)
            )
            payload["checkpoint_store_census"] = _read_store_census(cursor)
            payload["activity_census"] = _read_activity_census(cursor)
            payload["resume_history"] = _read_resume_history(cursor)
    except Exception as exc:  # noqa: BLE001
        payload["status"] = STATUS_ERROR
        payload["reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if connection is not None:
                connection.close()
        except Exception:  # pragma: no cover — defensive
            pass

    return payload


def _read_executing_turns(cursor: Any) -> tuple[list[dict[str, Any]], int]:
    """The executing turns — the ``tasks`` rows that are ``running``."""
    try:
        cursor.execute(
            """
            SELECT t.work_id, t.instance_id, t.status, t.task_type,
                   t.created_at, t.started_at, t.last_heartbeat_at,
                   t.suspension_reason, t.resume_target_turn_id,
                   i.agent_id, i.status AS instance_status
              FROM tasks t
              LEFT JOIN instances i ON i.instance_id = t.instance_id
             WHERE t.status = 'running'
             ORDER BY t.last_heartbeat_at DESC NULLS LAST
             LIMIT %s
            """,
            (MAX_EXECUTING_TURNS,),
        )
        rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wedge-capture: executing-turns read failed: %s", exc)
        return [], 0

    turns: list[dict[str, Any]] = []
    for row in rows:
        turns.append(
            {
                "work_id": row[0],
                "instance_id": row[1],
                "status": row[2],
                "task_type": row[3],
                "created_at": _iso(row[4]),
                "started_at": _iso(row[5]),
                "last_heartbeat_at": _iso(row[6]),
                "suspension_reason": row[7],
                "resume_target_turn_id": row[8],
                "agent_id": row[9],
                "instance_status": row[10],
                # The messages live in the LangGraph checkpoint store,
                # not the application DB — the count is NOT cheaply
                # available here, and a wrong number is worse than an
                # absent one.
                "msg_count": None,
                "msg_count_reason": "not_cheaply_available_in_the_app_db",
            }
        )
    return turns, len(turns)


def _read_store_census(cursor: Any) -> dict[str, Any]:
    """The checkpoint-store census — the H2 evidence (D10 / T10)."""
    census: dict[str, Any] = {}
    try:
        cursor.execute(
            """
            SELECT c.relname,
                   c.reltuples::bigint,
                   c.n_live_tup::bigint,
                   pg_total_relation_size(c.oid)
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = current_schema()
               AND c.relname = ANY(%s)
            """,
            (list(CHECKPOINT_TABLES),),
        )
        rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wedge-capture: store census failed: %s", exc)
        return {"status": f"failed:{type(exc).__name__}"}

    for name, reltuples, n_live_tup, total_bytes in rows:
        census[name] = {
            "reltuples": int(reltuples) if reltuples is not None else None,
            "n_live_tup": int(n_live_tup) if n_live_tup is not None else None,
            "total_bytes": int(total_bytes) if total_bytes is not None else None,
        }
    census["checkpoints_rows"] = census.get("checkpoints", {}).get("n_live_tup")
    census["checkpoint_blobs_rows"] = census.get("checkpoint_blobs", {}).get("n_live_tup")
    census["checkpoint_blobs_bytes"] = census.get("checkpoint_blobs", {}).get("total_bytes")
    return census


def _read_activity_census(cursor: Any) -> dict[str, Any]:
    """The idle-in-transaction census — ``pg_stat_activity`` (item 6 / B).

    The 2026-10-28 incident family is idle-in-transaction connection
    leaks (73-99s checkpoint ``aget``s + ``VACUUM FULL`` ACCESS
    EXCLUSIVE windows); a wedge capture that did not count backends
    could not see that shape. The census answers it:

    * ``total_backends`` — every row in ``pg_stat_activity`` (the sum
      of the per-state counts);
    * ``by_state`` — backends grouped by ``state`` (``NULL`` states —
      background/auxiliary processes — bucket under
      ``UNKNOWN_STATE_BUCKET``), each with the age of the state's
      OLDEST open transaction (seconds, server-side
      ``now() - xact_start``);
    * ``idle_in_transaction`` — the incident bucket's own block: the
      backend count, the oldest transaction age, and a capped
      per-backend detail list (pid / usename / application_name /
      backend_type / xact_start / state_change / xact age).

    HARD CONSTRAINT honored by construction: both statements run on
    the CURSOR THIS FUNCTION IS HANDED — the capture's own one-shot
    connection. A wedge-time census must never open extra pool
    connections (that is the amplification the capture exists to
    diagnose).

    Fail-open per statement (same discipline as the other reads): a
    statement-1 failure degrades the whole census to
    ``status="failed:<ExcName>"``; a statement-2 failure keeps the
    grouped census and degrades only the detail list.
    """
    # ── statement 1: the grouped census ─────────────────────────
    by_state: dict[str, int] = {}
    oldest_per_state: dict[str, float | None] = {}
    try:
        cursor.execute(
            """
            SELECT CASE WHEN state IS NULL THEN %s ELSE state END,
                   COUNT(*)::int,
                   MAX(EXTRACT(EPOCH FROM (now() - xact_start)))
                       ::double precision
              FROM pg_stat_activity
             GROUP BY 1
            """,
            (UNKNOWN_STATE_BUCKET,),
        )
        for state, backends, oldest_age_s in cursor.fetchall():
            bucket = str(state)
            by_state[bucket] = int(backends)
            oldest_per_state[bucket] = (
                float(oldest_age_s) if oldest_age_s is not None else None
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wedge-capture: activity census failed: %s", exc)
        return {"status": f"failed:{type(exc).__name__}"}

    census: dict[str, Any] = {
        "status": STATUS_OK,
        "total_backends": int(sum(by_state.values())),
        "by_state": by_state,
    }
    idle_block: dict[str, Any] = {
        "count": int(by_state.get(IDLE_IN_TXN_STATE, 0)),
        "oldest_xact_age_seconds": oldest_per_state.get(IDLE_IN_TXN_STATE),
    }

    # ── statement 2: the idle-in-txn detail (capped) ────────────
    try:
        cursor.execute(
            """
            SELECT pid, usename, application_name, backend_type,
                   xact_start, state_change,
                   EXTRACT(EPOCH FROM (now() - xact_start))
                       ::double precision
              FROM pg_stat_activity
             WHERE state = %s
             ORDER BY xact_start ASC NULLS LAST
             LIMIT %s
            """,
            (IDLE_IN_TXN_STATE, MAX_IDLE_IN_TXN_BACKENDS),
        )
        rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wedge-capture: idle-in-txn detail failed: %s", exc)
        idle_block["backends"] = []
        idle_block["backends_status"] = f"failed:{type(exc).__name__}"
        census["idle_in_transaction"] = idle_block
        return census

    idle_block["backends"] = [
        {
            "pid": int(row[0]),
            "usename": row[1],
            "application_name": row[2],
            "backend_type": row[3],
            "xact_start": _iso(row[4]),
            "state_change": _iso(row[5]),
            "xact_age_seconds": float(row[6]) if row[6] is not None else None,
        }
        for row in rows
    ]
    idle_block["backends_truncated"] = len(rows) >= MAX_IDLE_IN_TXN_BACKENDS
    census["idle_in_transaction"] = idle_block
    return census


def _read_resume_history(cursor: Any) -> list[dict[str, Any]]:
    """The tasks that were auto-continued — the resume chain."""
    try:
        cursor.execute(
            """
            SELECT work_id, instance_id, auto_continued_at, started_at
              FROM tasks
             WHERE auto_continued_at IS NOT NULL
             ORDER BY auto_continued_at DESC
             LIMIT %s
            """,
            (MAX_EXECUTING_TURNS,),
        )
        rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wedge-capture: resume-history read failed: %s", exc)
        return []
    return [
        {
            "work_id": row[0],
            "instance_id": row[1],
            "auto_continued_at": _iso(row[2]),
            "started_at": _iso(row[3]),
        }
        for row in rows
    ]


def _iso(value: Any) -> str | None:
    """Render a DB timestamp as an ISO string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat() + "Z"
    return str(value)


def write_capture_row(
    dsn: str | None,
    row: dict[str, Any],
    *,
    connect_timeout_s: int = CAPTURE_CONNECT_TIMEOUT_S,
    statement_timeout_ms: int = CAPTURE_STATEMENT_TIMEOUT_MS,
    budget_s: float | None = None,
) -> dict[str, Any]:
    """Insert the metadata row over the OWN one-shot connection (T11).

    The row write is **deferred-tolerant** (D22): a failure is logged
    with the capture directory path and the capture is still valid on
    disk — the caller writes the ``.pending_row.json`` sidecar.
    Since W2 the write is ALSO budget-bounded: an exhausted phase
    budget (``budget_s <= 0``) returns ``{"status": "skipped",
    "reason": "skipped:budget"}`` WITHOUT attempting a connect — the
    pending-sidecar path recovers the row on the next reconcile.

    Args:
        budget_s: This phase's budget in seconds (the capture's
            remaining budget, scaled — W2). ``None`` keeps the raw
            defaults — the hermetic shape the unit packs drive.

    Returns:
        ``{"status": "ok"|"failed"|"skipped", "reason": ...}``.
    """
    if not dsn:
        return {"status": ROW_STATUS_FAILED, "reason": "no_postgres_dsn"}
    scaled = (
        None
        if budget_s is None
        else _apply_budget(
            budget_s=budget_s,
            connect_timeout_s=connect_timeout_s,
            statement_timeout_ms=statement_timeout_ms,
            statements=1,
        )
    )
    if budget_s is not None and scaled is None:
        return {"status": STATUS_SKIPPED, "reason": SKIPPED_BUDGET_REASON}
    if scaled is not None:
        connect_timeout_s, statement_timeout_ms = scaled
    connection = None
    try:
        connection = _connect(
            dsn,
            connect_timeout_s=connect_timeout_s,
            statement_timeout_ms=statement_timeout_ms,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO wedge_captures (
                    id, capture_id, capture_dir, trigger_reason,
                    trigger_source, captured_at, boot_epoch, host,
                    release_version, summary, auto_continue_mode,
                    file_count, total_bytes, metrics_snapshot, created_at
                ) VALUES (
                    %(id)s, %(capture_id)s, %(capture_dir)s, %(trigger_reason)s,
                    %(trigger_source)s, %(captured_at)s, %(boot_epoch)s, %(host)s,
                    %(release_version)s, %(summary)s, %(auto_continue_mode)s,
                    %(file_count)s, %(total_bytes)s, %(metrics_snapshot)s,
                    %(created_at)s
                )
                ON CONFLICT (capture_dir) DO NOTHING
                """,
                {
                    "id": row.get("id") or uuid.uuid4().hex,
                    "capture_id": row.get("capture_id"),
                    "capture_dir": row.get("capture_dir"),
                    "trigger_reason": row.get("trigger_reason"),
                    "trigger_source": row.get("trigger_source") or "",
                    "captured_at": row.get("captured_at"),
                    "boot_epoch": row.get("boot_epoch"),
                    "host": row.get("host"),
                    "release_version": row.get("release_version"),
                    "summary": row.get("summary"),
                    "auto_continue_mode": row.get("auto_continue_mode"),
                    "file_count": row.get("file_count"),
                    "total_bytes": row.get("total_bytes"),
                    "metrics_snapshot": _json_or_none(row.get("metrics_snapshot")),
                    "created_at": row.get("created_at")
                    or datetime.now(timezone.utc).isoformat(),
                },
            )
        connection.commit()
        return {"status": ROW_STATUS_OK}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Wedge-capture: metadata row write failed for %s: %s",
            row.get("capture_dir"), exc,
        )
        return {"status": ROW_STATUS_FAILED, "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            if connection is not None:
                connection.close()
        except Exception:  # pragma: no cover — defensive
            pass


def _json_or_none(value: Any) -> Any:
    """Coerce a value into something psycopg can bind as JSON.

    psycopg 3 does NOT adapt a bare ``dict``/``list`` for a ``%s``
    placeholder — it raises ``ProgrammingError: cannot adapt type 'dict'``
    and the row write fails on a REAL PostgreSQL backend (observed
    against a live PG during this lane: the insert never landed). The
    value must be wrapped in a ``Jsonb`` adapter. The import is lazy so
    a SQLite-only / no-psycopg deployment still imports this module.
    """
    if value is None:
        return None
    payload = value if isinstance(value, (dict, list)) else str(value)
    try:
        from psycopg.types.json import Jsonb

        return Jsonb(payload)
    except Exception:  # noqa: BLE001 — no psycopg ⇒ the write fails later anyway
        return payload


__all__ = [
    "CAPTURE_APPLICATION_NAME",
    "CAPTURE_CONNECT_TIMEOUT_S",
    "CAPTURE_STATEMENT_TIMEOUT_MS",
    "CHECKPOINT_TABLES",
    "IDLE_IN_TXN_STATE",
    "MAX_EXECUTING_TURNS",
    "MAX_IDLE_IN_TXN_BACKENDS",
    "ROW_STATUS_FAILED",
    "ROW_STATUS_OK",
    "SKIPPED_BUDGET_REASON",
    "STATUS_DENIED",
    "STATUS_ERROR",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "STATUS_TIMEOUT",
    "STATUS_UNAVAILABLE",
    "capture_db_snapshot",
    "resolve_dsn",
    "write_capture_row",
]
