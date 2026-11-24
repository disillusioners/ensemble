"""PG-EXECUTED pin for the b4 candidate-selection SQL (D1).

THE DURABLE LESSON (fix/post-outage-recovery D1)
-------------------------------------------------
**SQLite-substrate pins false-pass PostgreSQL-dialect SQL.** The 51
b4 pins (and the 187-node suite that rides them) run on a SQLite
substrate, and ALL of them GREEN-passed the pre-D1 selection
statement: its ``ORDER BY`` referenced the SELECT-list aliases
(``task_completed_at`` / ``message_completed_at``) INSIDE a CASE
expression — legal in SQLite, ``UndefinedColumn`` on PostgreSQL
(executed repro: psycopg ``ProgrammingError: column
"task_completed_at" does not exist``). The failure was fail-safe per
tick (``[STALE_RUNNING_SWEEP_DB_ERROR]`` logged) but ``candidates=0``
forever, so the b4 lane healed NOTHING on production PG.

A dialect-sensitive statement therefore needs AT LEAST ONE PG-EXECUTED
LEG. This file is that leg: it executes the REAL production statement
on the fenced dev PostgreSQL.

WHAT IT PROVES
--------------
1. The production statement — the SAME object
   ``StaleRunningSweepService._select_candidates`` hands to
   ``conn.execute`` (built by the module-level
   ``_candidate_selection_statement()``) — EXECUTES on PostgreSQL
   (pre-D1: ``UndefinedColumn``).
2. It selects the right rows: an instance whose evidence is COMPLETE
   is selected; an instance still HOLDING a non-terminal task is not.
3. It ranks by the NIT-4 NEWEST evidence (the portable two-column
   max), so a row whose newest evidence is OLD sorts ahead of a row
   whose newest evidence is NEW — the cap-starvation ordering.

FENCE (zero persistent dev-DB writes)
-------------------------------------
The fixture rows are created inside ONE transaction the pin owns and
ROLLED BACK at the end — the pin never commits to the dev database.
The connection env is read PER VARIABLE (never an ``env -u`` glob —
bash does not glob env names); an unreachable database SKIPS the pin
instead of failing (the SQLite-only environments stay green).
"""

from __future__ import annotations

import os
from datetime import timedelta
from urllib.parse import quote_plus

import pytest
from sqlalchemy import create_engine, text

from daemon.services import stale_running_sweep as srs
from daemon.services.timestamps import now_utc_naive

# ── The fenced dev PostgreSQL (per-variable env; the house e2e pattern) ────

PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_DB = os.environ.get("POSTGRES_DB", "ensemble_dev")
PG_USER = os.environ.get("POSTGRES_USER", "ensemble_dev_app")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")

#: The fixture namespace — unique per lane so a stray row can never be
#: confused with a production instance.
PREFIX = "pg-pin-stale-running-"

#: The three specimens (see the module docstring).
ELIGIBLE = f"{PREFIX}eligible"   # task evidence OLD (3h)  → newest = 3h
YOUNG = f"{PREFIX}young"         # message evidence NEW (1h) → newest = 1h
HOLDING = f"{PREFIX}holding"     # a NON-terminal task → never selected


def _pg_url() -> str:
    """The sync PG URL (psycopg driver) — house e2e pattern."""
    return (
        f"postgresql+psycopg://{quote_plus(PG_USER)}:"
        f"{quote_plus(PG_PASSWORD)}@{PG_HOST}:{PG_PORT}/{PG_DB}"
    )


def _pg_available() -> bool:
    """Probe the fenced dev PG. Skip the pin when it is unreachable."""
    try:
        eng = create_engine(_pg_url(), connect_args={"connect_timeout": 3})
        try:
            with eng.connect() as conn:
                conn.execute(text("SELECT 1"))
        finally:
            eng.dispose()
    except Exception:  # noqa: BLE001 — any failure ⇒ the DB is not there
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason=(
        f"fenced dev PostgreSQL ({PG_HOST}:{PG_PORT}/{PG_DB}) is not "
        f"reachable — the PG-executed leg of the D1 pin cannot run"
    ),
)


def _fenced_dev_target() -> bool:
    """True iff the resolved target is the FENCED dev database.

    SAFETY FENCE: the pin writes fixture rows (rolled back, but still
    writes) — it must NEVER touch a live/production database. The
    env-resolved target is therefore checked against the fenced dev
    coordinates BEFORE the first write.
    """
    return (
        PG_DB == "ensemble_dev"
        and PG_HOST in ("127.0.0.1", "localhost", "::1")
        and PG_PORT == 5432
    )


def _seed_instance(conn, instance_id: str, agent_id: str = "pin-agent") -> None:
    conn.execute(
        text(
            """
            INSERT INTO instances (
                instance_id, agent_id, agent_dir, status, version,
                created_at, updated_at
            ) VALUES (
                :iid, :aid, :adir, 'running', 1,
                :created, :created
            )
            """
        ),
        {
            "iid": instance_id,
            "aid": agent_id,
            "adir": "/tmp/pin-agent",
            "created": now_utc_naive().isoformat(),
        },
    )


def _seed_completed_task(conn, instance_id: str, completed_at) -> None:
    conn.execute(
        text(
            """
            INSERT INTO task (
                instance_id, status, task_type, work_id, created_at,
                completed_at, retry_count, retry_scheduled,
                cancel_requested
            ) VALUES (
                :iid, 'completed', 'pin-task', :wid, :created,
                :completed, 0, false, false
            )
            """
        ),
        {
            "iid": instance_id,
            "wid": f"{instance_id}-work",
            "created": now_utc_naive(),
            "completed": completed_at,
        },
    )


def _seed_completed_message(conn, instance_id: str, completed_at) -> None:
    conn.execute(
        text(
            """
            INSERT INTO message_queue (
                message_id, instance_id, content, status, type, priority,
                max_retries, retry_count, enqueued_at, completed_at
            ) VALUES (
                :mid, :iid, 'pin content', 'completed', 'pin', 0,
                0, 0, :enqueued, :completed
            )
            """
        ),
        {
            "mid": f"{instance_id}-msg",
            "iid": instance_id,
            "enqueued": now_utc_naive(),
            "completed": completed_at,
        },
    )


def _seed_non_terminal_task(conn, instance_id: str) -> None:
    conn.execute(
        text(
            """
            INSERT INTO task (
                instance_id, status, task_type, work_id, created_at,
                completed_at, retry_count, retry_scheduled,
                cancel_requested
            ) VALUES (
                :iid, 'processing', 'pin-task', :wid, :created,
                NULL, 0, false, false
            )
            """
        ),
        {
            "iid": instance_id,
            "wid": f"{instance_id}-work",
            "created": now_utc_naive(),
        },
    )


def test_the_production_selection_sql_executes_on_postgres():
    """D1: the REAL selection statement executes on PostgreSQL and
    ranks by the NEWEST evidence.

    FAIL-HONEST: against the pre-D1 statement this raises psycopg's
    ``ProgrammingError: column "task_completed_at" does not exist``
    (the ORDER BY referenced the bare SELECT-list aliases inside the
    CASE expression) — the pin REDs there and GREENs after the
    derived-table fix.
    """
    engine = create_engine(_pg_url(), connect_args={"connect_timeout": 5})
    now = now_utc_naive()
    old_stamp = now - timedelta(hours=3)   # the ELIGIBLE row's only evidence
    new_stamp = now - timedelta(hours=1)   # the YOUNG row's only evidence

    if not _fenced_dev_target():
        engine.dispose()
        pytest.skip(
            f"the resolved PG target ({PG_HOST}:{PG_PORT}/{PG_DB}) is not the "
            f"fenced dev database — the pin never writes to another target"
        )

    try:
        with engine.connect() as conn:
            # ONE transaction the pin owns. NOTE: SQLAlchemy 2.0's
            # ``conn.begin()`` block COMMITS on exit — the pin needs a
            # ROLLBACK, so the transaction is rolled back EXPLICITLY in
            # the finally leg below (the fixtures must never persist).
            trans = conn.begin()
            try:
                _seed_instance(conn, ELIGIBLE)
                _seed_instance(conn, YOUNG)
                _seed_instance(conn, HOLDING)
                _seed_completed_task(conn, ELIGIBLE, old_stamp)
                _seed_completed_message(conn, YOUNG, new_stamp)
                _seed_non_terminal_task(conn, HOLDING)

                # THE PRODUCTION STATEMENT — the same object
                # ``_select_candidates`` executes.
                statement = srs._candidate_selection_statement()
                rows = [
                    dict(row)
                    for row in conn.execute(
                        statement, {"cap": 25},
                    ).mappings().all()
                ]

                mine = [
                    r for r in rows
                    if str(r.get("instance_id") or "").startswith(PREFIX)
                ]
                by_id = {r["instance_id"]: r for r in mine}

                # (1) The HOLDING specimen is NOT selectable — it still
                #     holds a non-terminal task.
                assert HOLDING not in by_id, (
                    "an instance holding a non-terminal task must not be "
                    f"selected, got {sorted(by_id)}"
                )
                # (2) Both evidence-complete specimens ARE selected.
                assert ELIGIBLE in by_id, (
                    f"the eligible specimen must be selected, got {sorted(by_id)}"
                )
                assert YOUNG in by_id, (
                    f"the young specimen must be selected, got {sorted(by_id)}"
                )
                # (3) The projection survived the derived-table wrap.
                assert by_id[ELIGIBLE]["task_completed_at"] is not None
                assert by_id[ELIGIBLE]["message_completed_at"] is None
                assert by_id[YOUNG]["message_completed_at"] is not None
                assert by_id[YOUNG]["task_completed_at"] is None
                # (4) NIT-4 ranking: the NEWEST evidence decides. The
                #     ELIGIBLE row's newest evidence is 3h old, the
                #     YOUNG row's is 1h old ⇒ ASC order puts the
                #     ELIGIBLE row FIRST (the cap-starvation ordering:
                #     the eligible row must never be starved behind a
                #     younger one).
                order = [r["instance_id"] for r in mine]
                assert order.index(ELIGIBLE) < order.index(YOUNG), (
                    f"the newest-evidence ranking is broken on PG: {order}"
                )
            finally:
                # ZERO persistent dev-DB writes — the fixtures are gone.
                trans.rollback()
    finally:
        engine.dispose()
    # The transaction block exited ⇒ ROLLBACK: zero persistent writes.
