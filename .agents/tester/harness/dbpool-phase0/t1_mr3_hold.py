"""T1 — M-R3 hold duration at BASE under load.

The M-R3 shape (daemon/manager.py:11515-11571 at 19fc11c8b):

    with self._write_guard:
        with self._session_scope() as session:      # SQLModel Session(self._engine)
            inj = session.get(ReportInjection, ...)  # autobegin -> BEGIN + SELECT
            ...
            content = await self._fetch_subshape_a_content_async(
                child_instance_id=child_instance_id, session=session,
            )                                        # <-- AWAIT INSIDE THE SCOPE
            return self._create_subshape_a_artifacts(session=session, ...)

The sync twin (daemon/manager.py:11208-11224) holds the scope across an
8s bridge (``asyncio.run_coroutine_threadsafe(...).result(timeout=8.0)``).

UNITS: every duration in this script is MILLISECONDS. ``summarize_ms``
labels are explicit. The flip-condition verdict converts to seconds.

WHAT IS MEASURED per iteration
  * hold_total_ms   — manager-pool backend held while idle-in-txn
  * await_ms        — the awaited content-fetch call
  * loop_delay_ms   — event-loop scheduling delay observed concurrently
  * pg state during the await (sampled concurrently)
  * engine.pool.checkedout() during the await
  * pooled-connection identity across the suspension
  * pool-checkout wait when the pool is over-subscribed

LOAD REGIMES
  L0  no load
  L1  manager-pool pressure (8 concurrent slot holders)
  L2  CONTINUOUS event-loop peg (CPU saturation, like the 104% incident)
  L3  pool pressure + loop peg
  L4  pool OVER-subscribed (>15 slots) -> checkout wait
  L5  loop peg + modeled slow content fetch

Run:  /home/nea/ensemble-src/.venv/bin/python t1_mr3_hold.py
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from sqlalchemy import text

from common import (
    SCRATCH_URL,
    activity_by_pid,
    emit,
    make_admin_conn,
    make_manager_engine,
    now,
)

N_CHECKPOINT_ROWS = 40
N_MESSAGES = 60
MESSAGE_CHARS = 900

SETUP_SQL = """
CREATE TABLE IF NOT EXISTS phase0_checkpoints (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL,
    checkpoint jsonb NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns)
);
CREATE TABLE IF NOT EXISTS phase0_instances (
    instance_id text PRIMARY KEY,
    project_id text NOT NULL,
    agent_id text NOT NULL,
    status text NOT NULL
);
"""


def summarize_ms(samples: list[float]) -> dict[str, Any]:
    if not samples:
        return {"n": 0}
    o = sorted(samples)
    import statistics

    idx = max(0, min(len(o) - 1, int(round(0.95 * len(o))) - 1))
    return {
        "n": len(o),
        "min_ms": round(o[0], 3),
        "median_ms": round(statistics.median(o), 3),
        "p95_ms": round(o[idx], 3),
        "max_ms": round(o[-1], 3),
        "mean_ms": round(statistics.fmean(o), 3),
    }


def seed(engine) -> None:
    with engine.begin() as c:
        for stmt in SETUP_SQL.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
    with engine.begin() as c:
        c.execute(text("DELETE FROM phase0_instances"))
        c.execute(
            text(
                "INSERT INTO phase0_instances (instance_id, project_id, agent_id, "
                "status) VALUES (:i, 'p1', 'worker', 'running')"
            ),
            {"i": "inst-0001"},
        )
        c.execute(text("DELETE FROM phase0_checkpoints"))
        blob = {
            "channel_values": {
                "messages": [
                    {
                        "role": "assistant" if i % 3 else "user",
                        "content": "x" * MESSAGE_CHARS,
                        "type": "ai" if i % 3 else "human",
                        "id": f"m{i}",
                    }
                    for i in range(N_MESSAGES)
                ]
            },
            "checkpoint": {"v": 4},
        }
        for i in range(N_CHECKPOINT_ROWS):
            c.execute(
                text(
                    "INSERT INTO phase0_checkpoints (thread_id, checkpoint_ns, "
                    "checkpoint) VALUES (:t, :n, CAST(:b AS jsonb))"
                ),
                {"t": "inst-0001", "n": f"{i:020d}", "b": json.dumps(blob)},
            )


class CheckpointPool:
    """asyncpg pool with the SAME shape as persistence.py:355-359.

    persistence.py:355 -> asyncpg.create_pool(conn_string, min_size=1,
    max_size=checkpoint_pool_max_size) — NO application_name, NO
    idle_in_transaction_session_timeout, SHARED conn_string.
    """

    def __init__(self) -> None:
        self.pool = None

    async def open(self) -> None:
        import asyncpg

        self.pool = await asyncpg.create_pool(
            SCRATCH_URL.replace("postgresql+psycopg", "postgresql"),
            min_size=1,
            max_size=10,
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()

    async def aget(self, thread_id: str) -> list[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT checkpoint FROM phase0_checkpoints WHERE thread_id = $1 "
                "ORDER BY checkpoint_ns DESC LIMIT 1",
                thread_id,
            )
        if row is None:
            return []
        blob = row["checkpoint"]
        if isinstance(blob, (str, bytes)):
            blob = json.loads(blob)
        return blob.get("channel_values", {}).get("messages", [])

    async def fetch_and_filter(self, thread_id: str) -> dict[str, Any]:
        """REAL PG read (asyncpg pool) + a MODEL of the python-side filter.

        The python-side portion models child_reports.py:1597+ post-read
        analysis; it is disclosed as a model in the report. The PG read
        half is real.
        """
        t0 = now()
        raw = await self.aget(thread_id)
        t_pg = now() - t0

        t1 = now()
        all_assistant = [m for m in raw if m.get("role") == "assistant"]
        assistant_msgs = [
            m for m in all_assistant if (m.get("content") or "").strip()
        ]
        last = assistant_msgs[-1] if assistant_msgs else None
        content = (last.get("content") or "").strip() if last else ""
        _ = len(content) * 3
        t_py = now() - t1
        return {"pg_ms": t_pg * 1000, "py_ms": t_py * 1000, "n_msgs": len(raw)}


class LoopDelayProbe:
    """Measures event-loop scheduling delay (the cost of a pegged loop)."""

    def __init__(self) -> None:
        self.samples: list[float] = []
        self._stop = False

    async def run(self) -> None:
        while not self._stop:
            t0 = now()
            await asyncio.sleep(0)
            self.samples.append((now() - t0) * 1000)

    def stop(self) -> None:
        self._stop = True


async def peg_background(chunk_iters: int) -> None:
    """CONTINUOUS event-loop CPU saturation (models the 104% incident).

    Burns in chunks with a cooperative yield between chunks so the loop
    stays saturated but remains schedulable — the faithful model of a
    daemon whose event loop is busy retrying.
    """

    def burn(n: int) -> int:
        x = 0
        for i in range(n):
            x = (x * 31 + i) % 1_000_003
        return x

    while True:
        burn(chunk_iters)
        await asyncio.sleep(0)


async def pool_pressure(engine, n: int, hold_s: float) -> None:
    from sqlalchemy.orm import Session

    sessions = []
    try:
        for _ in range(n):
            s = Session(engine)
            s.execute(text("SELECT 1")).first()
            sessions.append(s)
        await asyncio.sleep(hold_s)
    finally:
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass


async def mr3_iteration(
    engine,
    ckpt_pool: CheckpointPool,
    admin,
    extra_delay_s: float = 0.0,
) -> dict[str, Any]:
    from sqlalchemy.orm import Session

    rec: dict[str, Any] = {}
    t_scope_open = now()
    t_await0 = None
    session = Session(engine)
    try:
        session.execute(
            text(
                "SELECT instance_id, project_id, agent_id, status "
                "FROM phase0_instances WHERE instance_id = :i"
            ),
            {"i": "inst-0001"},
        ).first()
        rec["stmt_ms"] = round((now() - t_scope_open) * 1000, 3)

        dbapi = session.connection().connection.driver_connection
        pid = dbapi.info.backend_pid
        rec["pid"] = pid
        conn_fairy = session.connection().connection
        conn_obj_before = id(conn_fairy)
        rec["in_transaction_before_await"] = dbapi.info.transaction_status != 0
        rec["checkedout_before_await"] = engine.pool.checkedout()

        t_await0 = now()
        fetched = await ckpt_pool.fetch_and_filter("inst-0001")
        if extra_delay_s:
            await asyncio.sleep(extra_delay_s)
        rec["await_ms"] = round((now() - t_await0) * 1000, 3)
        rec["fetch_pg_ms"] = round(fetched["pg_ms"], 3)
        rec["fetch_py_ms"] = round(fetched["py_ms"], 3)

        snap = activity_by_pid(admin, pid)
        rec["pg_state_during_await"] = (snap or {}).get("state")
        rec["pg_last_query_during_await"] = (snap or {}).get("last_query")
        rec["checkedout_during_await"] = engine.pool.checkedout()

        rec["same_conn_across_suspension"] = (
            conn_obj_before == id(session.connection().connection)
        )
        rec["in_transaction_after_await"] = (
            session.connection().connection.driver_connection.info.transaction_status
            != 0
        )
    finally:
        session.close()
        t_close = now()
    rec["hold_total_ms"] = round((t_close - t_scope_open) * 1000, 3)
    return rec


async def sync_twin_iteration(
    engine,
    admin,
    inner_sleep_s: float = 60.0,
    bridge_timeout_s: float = 8.0,
) -> dict[str, Any]:
    """The SYNC twin of M-R3 (daemon/manager.py:11208-11224).

    The sync path holds ``with self._session_scope() as session:`` across
    ``asyncio.run_coroutine_threadsafe(<fetch>, loop).result(timeout=8.0)``
    — the bridge timeout is the ONLY bound on the hold.
    """
    from sqlalchemy.orm import Session

    rec: dict[str, Any] = {}
    t_scope_open = now()
    session = Session(engine)
    try:
        session.execute(
            text(
                "SELECT instance_id, project_id, agent_id, status "
                "FROM phase0_instances WHERE instance_id = :i"
            ),
            {"i": "inst-0001"},
        ).first()
        dbapi = session.connection().connection.driver_connection
        pid = dbapi.info.backend_pid
        rec["pid"] = pid
        rec["checkedout_before_bridge"] = engine.pool.checkedout()

        async def never_completes() -> str:
            await asyncio.sleep(inner_sleep_s)
            return "never"

        loop = asyncio.get_running_loop()
        t_bridge0 = now()
        try:
            fut = asyncio.run_coroutine_threadsafe(never_completes(), loop)
            fut.result(timeout=bridge_timeout_s)
        except Exception as exc:
            rec["bridge_exception"] = f"{type(exc).__name__}"
        rec["bridge_ms"] = round((now() - t_bridge0) * 1000, 3)

        snap = activity_by_pid(admin, pid)
        rec["pg_state_during_bridge"] = (snap or {}).get("state")
        rec["pg_last_query_during_bridge"] = (snap or {}).get("last_query")
        rec["checkedout_during_bridge"] = engine.pool.checkedout()
    finally:
        session.close()
        t_close = now()
    rec["hold_total_ms"] = round((t_close - t_scope_open) * 1000, 3)
    return rec


async def run_regime(
    name: str,
    engine,
    ckpt_pool: CheckpointPool,
    admin,
    iters: int,
    n_pressure: int = 0,
    peg_chunk: int = 0,
    extra_delay_s: float = 0.0,
) -> dict[str, Any]:
    holds: list[float] = []
    awaits: list[float] = []
    states: dict[str, int] = {}
    same_conn_all = True
    in_txn_all = True
    peak_checkedout = 0
    checkout_waits: list[float] = []

    peg_task = None
    probe = LoopDelayProbe()
    if peg_chunk:
        peg_task = asyncio.create_task(peg_background(peg_chunk))
        probe_task = asyncio.create_task(probe.run())

    try:
        for _ in range(iters):
            pressure = (
                asyncio.create_task(pool_pressure(engine, n_pressure, 0.30))
                if n_pressure
                else None
            )
            if pressure:
                await asyncio.sleep(0.05)
            t_co = now()
            rec = await mr3_iteration(
                engine, ckpt_pool, admin, extra_delay_s
            )
            checkout_waits.append((now() - t_co) * 1000)
            if pressure:
                try:
                    await asyncio.wait_for(asyncio.shield(pressure), timeout=1.0)
                except Exception:
                    pressure.cancel()
            holds.append(rec["hold_total_ms"])
            awaits.append(rec["await_ms"])
            states[rec.get("pg_state_during_await")] = (
                states.get(rec.get("pg_state_during_await"), 0) + 1
            )
            same_conn_all = same_conn_all and rec["same_conn_across_suspension"]
            in_txn_all = in_txn_all and rec["in_transaction_after_await"]
            peak_checkedout = max(peak_checkedout, rec["checkedout_during_await"])
    finally:
        if peg_task:
            peg_task.cancel()
        if peg_chunk:
            probe.stop()

    return {
        "regime": name,
        "iters": iters,
        "hold_total_ms": summarize_ms(holds),
        "await_ms": summarize_ms(awaits),
        "iteration_wall_ms": summarize_ms(checkout_waits),
        "loop_scheduling_delay_ms": summarize_ms(probe.samples),
        "pg_state_during_await_counts": states,
        "same_conn_across_suspension_all": same_conn_all,
        "in_transaction_after_await_all": in_txn_all,
        "peak_engine_pool_checkedout": peak_checkedout,
    }


async def main() -> None:
    admin = make_admin_conn()
    engine = make_manager_engine()
    seed(engine)

    ckpt_pool = CheckpointPool()
    await ckpt_pool.open()

    results = []
    results.append(
        await run_regime("L0 no-load", engine, ckpt_pool, admin, iters=30)
    )
    results.append(
        await run_regime(
            "L1 pool-pressure(8)", engine, ckpt_pool, admin, iters=30, n_pressure=8
        )
    )
    results.append(
        await run_regime(
            "L2 loop-peg(chunk=20000)",
            engine,
            ckpt_pool,
            admin,
            iters=30,
            peg_chunk=20_000,
        )
    )
    results.append(
        await run_regime(
            "L3 pressure(8)+loop-peg",
            engine,
            ckpt_pool,
            admin,
            iters=30,
            n_pressure=8,
            peg_chunk=20_000,
        )
    )
    # L4 uses a DEDICATED engine with pool_timeout=3s (the base manager
    # engine's 30s default would make the run exceed the pack budget).
    # The checkout-wait MECHANISM is identical; only the bound differs.
    l4_engine = make_manager_engine(pool_timeout=3)
    results.append(
        await run_regime(
            "L4 pool OVER-subscribed(20) [pool_timeout=3s]",
            l4_engine,
            ckpt_pool,
            admin,
            iters=4,
            n_pressure=20,
        )
    )
    l4_engine.dispose()
    results.append(
        await run_regime(
            "L5 loop-peg + 1.0s fetch tail",
            engine,
            ckpt_pool,
            admin,
            iters=15,
            peg_chunk=20_000,
            extra_delay_s=1.0,
        )
    )


    # --- L4b: genuine OVER-subscription. The holders are created and
    #     checked out SYNCHRONOUSLY before the M-R3 borrow, so the pool
    #     really is over capacity (15 slots) when M-R3 asks for one.
    over_engine = make_manager_engine(pool_timeout=3)
    over_sessions = []
    try:
        from sqlalchemy.orm import Session as _S

        over_err = None
        for _ in range(20):
            try:
                s_ = over_engine.connect()
                s_.execute(text("SELECT 1")).first()
                over_sessions.append(s_)
            except Exception as exc:
                over_err = f"{type(exc).__name__}: {exc}"
                break
        over_rec: dict[str, Any] = {}
        over_borrow_err = None
        t_borrow = now()
        try:
            over_rec = await mr3_iteration(over_engine, ckpt_pool, admin)
        except Exception as exc:
            over_borrow_err = f"{type(exc).__name__}: {str(exc)[:200]}"
        emit(
            "T1 L4b GENUINE OVER-SUBSCRIPTION (15-slot pool, pool_timeout=3s)",
            {
                "holders_created": len(over_sessions),
                "pool_slots": 15,
                "holder_creation_error": over_err,
                "mr3_borrow_error": over_borrow_err,
                "mr3_borrow_wait_ms": round((now() - t_borrow) * 1000, 3),
                "record": over_rec,
                "note": "the M-R3 borrow is the 16th; the 30s (base) / 3s "
                "(this regime) pool_timeout bounds the wait",
            },
        )
        results.append(
            {
                "regime": "L4b over-subscribed(16th borrow)",
                "iters": 1,
                "hold_total_ms": summarize_ms(
                    [over_rec.get("hold_total_ms", 0.0)]
                ),
                "await_ms": summarize_ms([over_rec.get("await_ms", 0.0)]),
                "iteration_wall_ms": summarize_ms(
                    [round((now() - t_borrow) * 1000, 3)]
                ),
                "loop_scheduling_delay_ms": summarize_ms([]),
                "pg_state_during_await_counts": {
                    over_rec.get("pg_state_during_await"): 1
                },
                "same_conn_across_suspension_all": over_rec.get(
                    "same_conn_across_suspension", False
                ),
                "in_transaction_after_await_all": over_rec.get(
                    "in_transaction_after_await", False
                ),
                "peak_engine_pool_checkedout": over_rec.get(
                    "checkedout_during_await", 0
                ),
                "borrow_error": over_borrow_err,
            }
        )
    finally:
        for s_ in over_sessions:
            try:
                s_.close()
            except Exception:
                pass
        over_engine.dispose()

    # --- L6: the SYNC twin of M-R3 (8s bridge timeout) ---
    sync_rec = await sync_twin_iteration(engine, admin)
    emit("T1 L6 SYNC TWIN (session scope held across the 8s bridge)", sync_rec)
    results.append(
        {
            "regime": "L6 sync-twin(8s bridge, inner never completes)",
            "iters": 1,
            "hold_total_ms": summarize_ms([sync_rec["hold_total_ms"]]),
            "await_ms": summarize_ms([sync_rec["bridge_ms"]]),
            "iteration_wall_ms": summarize_ms([sync_rec["hold_total_ms"]]),
            "loop_scheduling_delay_ms": summarize_ms([]),
            "pg_state_during_await_counts": {
                sync_rec.get("pg_state_during_bridge"): 1
            },
            "same_conn_across_suspension_all": True,
            "in_transaction_after_await_all": True,
            "peak_engine_pool_checkedout": sync_rec.get(
                "checkedout_during_bridge", 0
            ),
        }
    )

    emit("T1 M-R3 HOLD DISTRIBUTION (all durations in ms)", results)

    worst_max_ms = max(r["hold_total_ms"].get("max_ms", 0) for r in results)
    no_load_median = results[0]["hold_total_ms"]["median_ms"]
    flip = "NOT MET" if worst_max_ms >= 2000 else "MET"
    emit(
        "T1 FLIP CONDITION (hold < 2s)",
        {
            "flip_condition": flip,
            "flip_condition_text": (
                "hold < 2000ms"
                + ("  -> CONDITION MET (approach C collapses to B)"
                   if flip == "MET"
                   else "  -> CONDITION NOT MET (F8 stands)")
            ),
            "worst_max_hold_ms": worst_max_ms,
            "no_load_median_hold_ms": no_load_median,
            "per_regime": {
                r["regime"]: {
                    "median_hold_ms": r["hold_total_ms"]["median_ms"],
                    "max_hold_ms": r["hold_total_ms"]["max_ms"],
                    "median_await_ms": r["await_ms"]["median_ms"],
                    "median_loop_delay_ms": r["loop_scheduling_delay_ms"].get(
                        "median_ms"
                    ),
                }
                for r in results
            },
        },
    )

    await ckpt_pool.close()
    engine.dispose()
    admin.close()


if __name__ == "__main__":
    asyncio.run(main())
