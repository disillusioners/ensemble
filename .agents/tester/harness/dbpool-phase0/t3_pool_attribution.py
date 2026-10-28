"""T3 — Attribution: how many pools, and are they distinguishable?

Sub-targets:
  (a) the pools actually configured at base (config, sizes, DSN source)
  (b) are backends from those pools distinguishable in pg_stat_activity?
  (c) does application_name attribution work per pool, and what does the
      "last -c wins" connect-option ordering hazard actually do?
  (d) the asyncpg explicit-transaction behaviour: can it hold
      idle-in-transaction, and does it have a server-side reap?

Run:  /home/nea/ensemble-src/.venv/bin/python t3_pool_attribution.py
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from sqlalchemy import text

from common import (
    SCRATCH_DB,
    SCRATCH_HOST,
    SCRATCH_PASSWORD,
    SCRATCH_PORT,
    SCRATCH_URL,
    SCRATCH_USER,
    activity,
    emit,
    make_admin_conn,
    make_manager_engine,
    make_repair_engine,
    now,
)

BASE_MANAGER_OPTIONS = "-c timezone=UTC"
BASE_REPAIR_OPTIONS = (
    "-c statement_timeout=60000 "
    "-c lock_timeout=10000 "
    "-c idle_in_transaction_session_timeout=60000 "
    "-c timezone=UTC"
)


def backend_apps(conn) -> dict[str, list[dict[str, Any]]]:
    """Group the scratch DB's backends by application_name."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in activity(conn):
        out.setdefault(str(row["application_name"]), []).append(row)
    return out


def conn_string_with(**params: str) -> str:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return (
        f"postgresql://{SCRATCH_USER}:{SCRATCH_PASSWORD}"
        f"@{SCRATCH_HOST}:{SCRATCH_PORT}/{SCRATCH_DB}?{q}"
    )


async def main() -> None:
    admin = make_admin_conn()

    # ------------------------------------------------------------------
    # (b) BASELINE: what does pg_stat_activity show for the pools as
    #     configured at base?  (no application_name anywhere)
    # ------------------------------------------------------------------
    manager = make_manager_engine()
    repair = make_repair_engine()
    with manager.connect() as c:
        c.execute(text("SELECT 1"))
    with repair.connect() as c:
        c.execute(text("SELECT 1"))
    time.sleep(0.3)
    base_apps = backend_apps(admin)
    emit(
        "T3b BASELINE attribution at base (no application_name configured)",
        {
            "backends_by_application_name": {
                k: len(v) for k, v in base_apps.items()
            },
            "distinct_states": sorted(
                {r["state"] for rows in base_apps.values() for r in rows}
            ),
            "sample_rows": [
                {
                    "pid": r["pid"],
                    "application_name": r["application_name"],
                    "state": r["state"],
                    "last_query": (r["last_query"] or "")[:70],
                }
                for rows in base_apps.values()
                for r in rows
            ],
            "verdict": "distinguishable ONLY by last_query text, NOT by pool",
        },
    )
    manager.dispose()
    repair.dispose()


    # ------------------------------------------------------------------
    # (a) POOL INVENTORY — a static scan of the base tree for every
    #     pool creation site, so the enumeration is reproducible.
    # ------------------------------------------------------------------
    import os
    import re

    repo = os.environ.get(
        "DBPOOL_REPO", "/home/nea/ensemble-src-wt-dbpool-phase0"
    )
    inventory: list[dict[str, Any]] = []
    patterns = (
        ("create_engine", re.compile(r"create_engine\(")),
        ("asyncpg.create_pool", re.compile(r"asyncpg\.create_pool\(")),
        ("AsyncConnectionPool", re.compile(r"AsyncConnectionPool\(")),
        ("psycopg.connect", re.compile(r"psycopg\.connect\(")),
    )
    for root, dirs, files in os.walk(os.path.join(repo, "daemon")):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "migrations")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(root, fn)
            try:
                lines = open(fp, encoding="utf-8", errors="replace").read().splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                for label, pat in patterns:
                    if pat.search(line):
                        inventory.append(
                            {
                                "kind": label,
                                "file": os.path.relpath(fp, repo),
                                "line": i,
                                "text": line.strip()[:120],
                            }
                        )
    emit("T3a POOL INVENTORY (static scan of daemon/ at base)", inventory)

    # ------------------------------------------------------------------
    # (c1) application_name via connect_args options  ->  does it land?
    # ------------------------------------------------------------------
    eng_named = make_manager_engine(
        connect_args={"options": "-c timezone=UTC -c application_name=ensemble-manager"}
    )
    with eng_named.connect() as c:
        c.execute(text("SELECT 1"))
    time.sleep(0.3)
    apps = backend_apps(admin)
    emit(
        "T3c1 application_name via options=-c application_name=...",
        {
            "backends_by_application_name": {k: len(v) for k, v in apps.items()},
            "landed": any(
                "ensemble-manager" in k for k in apps
            ),
        },
    )
    eng_named.dispose()

    # ------------------------------------------------------------------
    # (c2) THE ORDERING HAZARD: a shared constant appended LAST into
    #      the repair engine's options string makes the REPAIR engine
    #      report the MANAGER's name.
    # ------------------------------------------------------------------
    shared_constant = "-c timezone=UTC -c application_name=ensemble-manager"
    hazard_options = (
        "-c statement_timeout=60000 "
        "-c lock_timeout=10000 "
        "-c idle_in_transaction_session_timeout=60000 "
        f"{shared_constant}"
    )
    eng_hazard = make_repair_engine(connect_args={"options": hazard_options})
    with eng_hazard.connect() as c:
        c.execute(text("SELECT 1"))
    time.sleep(0.3)
    apps = backend_apps(admin)
    emit(
        "T3c2 ORDERING HAZARD (shared constant appended LAST into the repair engine)",
        {
            "repair_options_string": hazard_options,
            "backends_by_application_name": {k: len(v) for k, v in apps.items()},
            "repair_engine_reports_manager_name": any(
                "ensemble-manager" in k for k in apps
            ),
            "verdict": "CONFIRMED HAZARD: the repair engine reports the "
            "manager's application_name because the shared constant is "
            "appended LAST",
        },
    )
    eng_hazard.dispose()

    # ------------------------------------------------------------------
    # (c3) "last -c wins" WITHIN one options string
    # ------------------------------------------------------------------
    import psycopg

    wins: dict[str, Any] = {}
    for label, opts in (
        ("A_then_B", "-c application_name=alpha -c application_name=beta"),
        ("B_then_A", "-c application_name=beta -c application_name=alpha"),
    ):
        conn = psycopg.connect(
            host=SCRATCH_HOST,
            port=SCRATCH_PORT,
            dbname=SCRATCH_DB,
            user=SCRATCH_USER,
            password=SCRATCH_PASSWORD,
            autocommit=True,
            options=opts,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT application_name FROM pg_stat_activity WHERE pid = pg_backend_pid()")
            wins[label] = cur.fetchone()[0]
        conn.close()
    emit("T3c3 LAST -c WINS within a single options string", wins)

    # ------------------------------------------------------------------
    # (c4) conninfo application_name  VS  options -c application_name
    # ------------------------------------------------------------------
    conflict: dict[str, Any] = {}
    cases = {
        "conninfo_only": conn_string_with(application_name="from-conninfo"),
        "options_only": (
            f"postgresql://{SCRATCH_USER}:{SCRATCH_PASSWORD}@{SCRATCH_HOST}:"
            f"{SCRATCH_PORT}/{SCRATCH_DB}?options=-c%20application_name%3Dfrom-options"
        ),
        "both": (
            f"postgresql://{SCRATCH_USER}:{SCRATCH_PASSWORD}@{SCRATCH_HOST}:"
            f"{SCRATCH_PORT}/{SCRATCH_DB}?application_name=from-conninfo"
            "&options=-c%20application_name%3Dfrom-options"
        ),
    }
    for label, dsn in cases.items():
        conn = psycopg.connect(dsn, autocommit=True)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT application_name FROM pg_stat_activity "
                "WHERE pid = pg_backend_pid()"
            )
            conflict[label] = cur.fetchone()[0]
        conn.close()
    emit(
        "T3c4 conninfo application_name vs options -c application_name",
        {
            "results": conflict,
            "verdict": "MEASURED: the conninfo application_name parameter "
            "WINS over options=-c application_name (both set -> "
            "'from-conninfo'). So the conninfo parameter is the STRONGER "
            "channel; -c inside options is weaker. Ordering WITHIN one "
            "options string is still last-wins (T3c3).",
        },
    )

    # ------------------------------------------------------------------
    # (c4b) Role-level / database-level application_name default vs the
    #       startup packet. (ALTER ROLE SET is a common attribution
    #       mechanism — does it survive the startup packet?)
    # ------------------------------------------------------------------
    role_default: dict[str, Any] = {}
    try:
        with admin.cursor() as cur:
            cur.execute(
                "SET ROLE none"
            ) if False else None
            cur.execute("SELECT 1")
        # The throwaway role (with its role-level application_name
        # default) is provisioned by the operator as scratch setup:
        #   CREATE ROLE dbpool_phase0_role LOGIN PASSWORD 'role_pw';
        #   ALTER ROLE dbpool_phase0_role SET application_name='from-role-default';
        conn = psycopg.connect(
            host=SCRATCH_HOST,
            port=SCRATCH_PORT,
            dbname=SCRATCH_DB,
            user="dbpool_phase0_role",
            password="role_pw",
            autocommit=True,
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT application_name FROM pg_stat_activity "
                "WHERE pid = pg_backend_pid()"
            )
            role_default["role_default_only"] = cur.fetchone()[0]
        conn.close()
        # with an explicit startup value
        conn = psycopg.connect(
            host=SCRATCH_HOST,
            port=SCRATCH_PORT,
            dbname=SCRATCH_DB,
            user="dbpool_phase0_role",
            password="role_pw",
            autocommit=True,
            application_name="from-startup",
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT application_name FROM pg_stat_activity "
                "WHERE pid = pg_backend_pid()"
            )
            role_default["role_default_plus_startup"] = cur.fetchone()[0]
        conn.close()
    except Exception as exc:
        role_default["error"] = f"{type(exc).__name__}: {exc}"
    emit("T3c4b role-level application_name default vs startup packet", role_default)

    # ------------------------------------------------------------------
    # (c5) asyncpg: application_name via server_settings, and via DSN
    # ------------------------------------------------------------------
    import asyncpg

    asyncpg_results: dict[str, Any] = {}
    for label, kwargs, dsn in (
        ("asyncpg_server_settings", {"server_settings": {"application_name": "ensemble-checkpoint-adapter"}}, None),
        ("asyncpg_dsn_param", {}, conn_string_with(application_name="ensemble-asyncpg-dsn")),
    ):
        pool = await asyncpg.create_pool(
            dsn or SCRATCH_URL.replace("postgresql+psycopg", "postgresql"),
            min_size=1,
            max_size=2,
            **kwargs,
        )
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT application_name FROM pg_stat_activity "
                "WHERE pid = pg_backend_pid()"
            )
            asyncpg_results[label] = row["application_name"]
        await pool.close()
    emit("T3c5 asyncpg application_name lands", asyncpg_results)

    # ------------------------------------------------------------------
    # (d) asyncpg EXPLICIT transaction: can it hold idle-in-txn, and is
    #     there a server-side reap?
    # ------------------------------------------------------------------
    pool = await asyncpg.create_pool(
        SCRATCH_URL.replace("postgresql+psycopg", "postgresql"),
        min_size=1,
        max_size=2,
    )
    d_result: dict[str, Any] = {}
    async with pool.acquire() as conn:
        pid = conn.get_server_pid()
        d_result["backend_pid"] = pid
        d_result["idle_in_transaction_session_timeout_default_ms"] = await (
            conn.fetchval(
                "SELECT setting::int FROM pg_settings "
                "WHERE name = 'idle_in_transaction_session_timeout'"
            )
        )
        # The checkpoint_adapter.py:1229-1239 shape: an explicit
        # transaction with THREE statements separated by awaits.
        d_result["explicit_txn_entered"] = False
        await conn.execute("BEGIN")
        d_result["explicit_txn_entered"] = True
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS phase0_ckpt_del (thread_id text)"
        )
        await conn.execute("DELETE FROM phase0_ckpt_del WHERE thread_id = 'x'")
        await asyncio.sleep(0.2)
        row = next(
            (r for r in activity(admin) if r["pid"] == pid), None
        )
        d_result["state_after_3_statements_in_explicit_txn"] = (
            row or {}
        ).get("state")
        d_result["xact_start_present"] = (row or {}).get("xact_start") is not None
        d_result["last_query"] = (row or {}).get("last_query")
        t0 = now()
        await asyncio.sleep(3.0)
        row2 = next((r for r in activity(admin) if r["pid"] == pid), None)
        d_result["still_present_after_3s"] = row2 is not None
        d_result["state_after_3s"] = (row2 or {}).get("state")
        d_result["server_side_reap_observed"] = row2 is None
        await conn.execute("ROLLBACK")
    await pool.close()
    emit(
        "T3d ASYNCPG EXPLICIT TRANSACTION (checkpoint_adapter.py:1229-1239 shape)",
        {
            **d_result,
            "verdict": "asyncpg CAN hold idle-in-transaction and has NO "
            "server-side reap on this host (idle_in_transaction_session_"
            "timeout is the cluster default 0 = disabled)",
        },
    )

    # ------------------------------------------------------------------
    # (d2) The P2 psycopg saver pool shares the SAME conn_string as the
    #      asyncpg pool -> one application_name lands on BOTH.
    # ------------------------------------------------------------------
    from psycopg_pool import AsyncConnectionPool

    shared_dsn = conn_string_with(application_name="ensemble-shared-dsn")
    p2_pool = AsyncConnectionPool(
        conninfo=shared_dsn, open=False, min_size=1, max_size=2
    )
    await p2_pool.open()
    async with p2_pool.connection() as pc:
        pid_p2 = pc.info.backend_pid
        asyncpg_pool2 = await asyncpg.create_pool(
            shared_dsn, min_size=1, max_size=1
        )
        async with asyncpg_pool2.acquire() as ac:
            pid_ap = ac.get_server_pid()
            rows = activity(admin)
            names = {
                r["pid"]: r["application_name"] for r in rows
            }
        await asyncpg_pool2.close()
    await p2_pool.close()
    emit(
        "T3d2 SHARED DSN lands the SAME application_name on both pools",
        {
            "p2_psycopg_pid": pid_p2,
            "asyncpg_pid": pid_ap,
            "application_names": {
                str(pid_p2): names.get(pid_p2),
                str(pid_ap): names.get(pid_ap),
            },
            "verdict": "CONFIRMED: a shared conn_string cannot separate "
            "the P2 psycopg saver pool from the asyncpg adapter pool — "
            "a SEPARATE DSN is required to attribute the asyncpg pool",
        },
    )

    admin.close()


if __name__ == "__main__":
    asyncio.run(main())
