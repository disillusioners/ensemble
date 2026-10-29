"""Phase-0 empirical baseline harness — DB pool idle-in-transaction lane.

NOT SHIPPED. Non-production measurement scaffolding under
``.agents/tester/harness/``. Zero production code is imported or patched
here; the harness only *reads* the daemon's constants where possible and
otherwise replicates the base-tree engine configuration byte-for-byte
(verified against ``daemon/repositories/factory.py`` and
``daemon/tools/ens_db_tools.py`` at commit 19fc11c8b).

FENCES (must hold for every script in this dir):
  * The scratch DB is lane-owned and created/dropped by the operator.
  * Every connection targets the scratch DB explicitly; the harness
    NEVER reads POSTGRES_* from the ambient environment.
  * No live daemon, no promote/rollback/stage script, no live install
    path is ever touched.

Usage (from the harness dir):
    /home/nea/ensemble-src/.venv/bin/python t2_stat_activity.py
"""

from __future__ import annotations

import os
import statistics
import time
from typing import Any

# ---------------------------------------------------------------------------
# Fence: scratch DB connection parameters (explicit, never inherited).
# ---------------------------------------------------------------------------
SCRATCH_HOST = os.environ.get("DBPOOL_SCRATCH_HOST", "127.0.0.1")
SCRATCH_PORT = int(os.environ.get("DBPOOL_SCRATCH_PORT", "5432"))
SCRATCH_DB = os.environ.get("DBPOOL_SCRATCH_DB", "ensemble_dbpool_scratch_phase0")
SCRATCH_USER = os.environ.get("DBPOOL_SCRATCH_USER", "dbpool_phase0")
SCRATCH_PASSWORD = os.environ.get(
    "DBPOOL_SCRATCH_PASSWORD", "phase0_scratch_pw"
)

# SQLAlchemy URL for the scratch DB (manager-engine shape).
SCRATCH_URL = (
    f"postgresql+psycopg://{SCRATCH_USER}:{SCRATCH_PASSWORD}"
    f"@{SCRATCH_HOST}:{SCRATCH_PORT}/{SCRATCH_DB}"
)

# Admin URL — used ONLY for pg_stat_activity reads and role/database
# inspection (pg_read_all_stats is implied by superuser).
ADMIN_URL = (
    f"postgresql://{SCRATCH_USER}:{SCRATCH_PASSWORD}"
    f"@{SCRATCH_HOST}:{SCRATCH_PORT}/postgres"
)


def now() -> float:
    return time.perf_counter()


def summarize(samples: list[float]) -> dict[str, Any]:
    """min/median/p95/max + n for a duration distribution."""
    if not samples:
        return {"n": 0}
    ordered = sorted(samples)
    # p95 via nearest-rank (deterministic, no interpolation ambiguity).
    idx = max(0, min(len(ordered) - 1, int(round(0.95 * len(ordered))) - 1))
    return {
        "n": len(ordered),
        "min_s": round(ordered[0], 4),
        "median_s": round(statistics.median(ordered), 4),
        "p95_s": round(ordered[idx], 4),
        "max_s": round(ordered[-1], 4),
        "mean_s": round(statistics.fmean(ordered), 4),
    }


# ---------------------------------------------------------------------------
# pg_stat_activity reader
# ---------------------------------------------------------------------------
ACTIVITY_SQL = """
SELECT pid,
       application_name,
       state,
       wait_event_type,
       wait_event,
       xact_start,
       query_start,
       state_change,
       backend_start,
       left(query, 200) AS last_query,
       EXTRACT(EPOCH FROM (now() - xact_start))::float AS xact_age_s,
       EXTRACT(EPOCH FROM (now() - state_change))::float AS state_age_s
  FROM pg_stat_activity
 WHERE datname = %(db)s
   AND pid <> pg_backend_pid()
 ORDER BY pid
"""


def activity(conn) -> list[dict[str, Any]]:
    """Snapshot pg_stat_activity for the scratch DB."""
    with conn.cursor() as cur:
        cur.execute(ACTIVITY_SQL, {"db": SCRATCH_DB})
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def activity_by_pid(conn, pid: int) -> dict[str, Any] | None:
    for row in activity(conn):
        if row["pid"] == pid:
            return row
    return None


def make_admin_conn():
    """psycopg3 autocommit connection for observation queries."""
    import psycopg

    conn = psycopg.connect(
        host=SCRATCH_HOST,
        port=SCRATCH_PORT,
        dbname="postgres",
        user=SCRATCH_USER,
        password=SCRATCH_PASSWORD,
        autocommit=True,
    )
    return conn


def make_manager_engine(**overrides):
    """The BASE manager engine, replicated byte-for-byte.

    Source: daemon/repositories/factory.py:323-338 at 19fc11c8b
        create_engine(url, echo=False, pool_size=5, max_overflow=10,
                      pool_pre_ping=True,
                      connect_args={'options': '-c timezone=UTC'})
    """
    from sqlalchemy import create_engine

    kwargs: dict[str, Any] = dict(
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC"},
    )
    kwargs.update(overrides)
    return create_engine(SCRATCH_URL, **kwargs)


def make_repair_engine(**overrides):
    """The BASE repair engine, replicated byte-for-byte.

    Source: daemon/tools/ens_db_tools.py:604-633 at 19fc11c8b
        pool_size=2, max_overflow=3, pool_pre_ping=True,
        pool_recycle=3600, pool_timeout=10,
        connect_args={'options': '-c statement_timeout=60000 '
                                 '-c lock_timeout=10000 '
                                 '-c idle_in_transaction_session_timeout=60000 '
                                 '-c timezone=UTC'}
    """
    from sqlalchemy import create_engine

    kwargs: dict[str, Any] = dict(
        pool_size=2,
        max_overflow=3,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_timeout=10,
        connect_args={
            "options": (
                "-c statement_timeout=60000 "
                "-c lock_timeout=10000 "
                "-c idle_in_transaction_session_timeout=60000 "
                "-c timezone=UTC"
            )
        },
    )
    kwargs.update(overrides)
    return create_engine(SCRATCH_URL, **kwargs)


def emit(title: str, payload: Any) -> None:
    import json

    print(f"\n===== {title} =====", flush=True)
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, indent=2, default=str), flush=True)
    else:
        print(payload, flush=True)
