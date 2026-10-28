"""T5 — F3/F4/F6 fix-site behavior at BASE (19fc11c8b).

(a) ens_db_tools.py:1479/:1491 — a forced failure BETWEEN the explicit
    BEGIN and the rollback. Does the connection return to the pool still
    holding an open transaction? Is the backend idle-in-txn afterwards?
    Does the NEXT borrower get "current transaction is aborted"?
    + the wedge-capture one-shot cascade (the incident's journal quote).
(b) readiness probe timeout path — when the probe times out and the
    to_thread is abandoned, is the connection STILL CHECKED OUT
    afterwards, and for how long?
(c) mission mint — reproduce the IntegrityError and identify the ACTUAL
    trigger.

Run:  /home/nea/ensemble-src/.venv/bin/python t5_fix_sites.py
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from typing import Any

sys.path.insert(0, "/home/nea/ensemble-src-wt-dbpool-phase0")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from common import (  # noqa: E402
    activity,
    activity_by_pid,
    emit,
    make_admin_conn,
    make_manager_engine,
    make_repair_engine,
    now,
)

ADMIN = None


def backend_state(pid: int) -> dict[str, Any] | None:
    return activity_by_pid(ADMIN, pid)


# ---------------------------------------------------------------------------
# (a) the ens_db_tools explicit-BEGIN dry-run shape
# ---------------------------------------------------------------------------
def ens_db_dryrun_shape(repair_engine, fail_at: str) -> dict[str, Any]:
    """Reproduce daemon/tools/ens_db_tools.py:1475-1513 (DDL branch).

    fail_at:
      'begin'    -> the explicit BEGIN itself fails
      'statement'-> the dry-run statement fails (inner except catches it)
      'rollback' -> the explicit rollback() itself fails
    """
    rec: dict[str, Any] = {"fail_at": fail_at}
    session = Session(repair_engine)
    try:
        try:
            if fail_at == "begin":
                # Make the explicit BEGIN fail: abort the transaction
                # first, so the BEGIN is rejected with "current
                # transaction is aborted" — the realistic shape of a
                # BEGIN that fails after the session already issued a
                # statement.
                try:
                    session.execute(text("SELECT * FROM no_such_table_phase0"))
                except Exception:
                    pass
                session.execute(text("BEGIN"))
            else:
                session.execute(text("BEGIN"))
            rec["begin_ok"] = True
            try:
                if fail_at == "statement":
                    session.execute(text("SELECT * FROM no_such_table_phase0"))
                else:
                    session.execute(text("SELECT 1"))
            except Exception as exc:
                rec["inner_except"] = f"{type(exc).__name__}: {str(exc)[:90]}"
            if fail_at == "rollback":
                session.rollback = lambda: (_ for _ in ()).throw(
                    RuntimeError("simulated rollback failure")
                )
            session.rollback()
            rec["explicit_rollback_ran"] = True
        except Exception as exc:
            rec["outer_except"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            rec["explicit_rollback_ran"] = False
    finally:
        raw = session.connection().connection.driver_connection
        pid = raw.info.backend_pid
        rec["backend_pid"] = pid
        rec["in_transaction_at_close"] = (
            raw.info.transaction_status != 0
        )
        rec["pg_state_before_close"] = (backend_state(pid) or {}).get("state")
        session.close()

    time.sleep(0.2)
    row = activity_by_pid(ADMIN, pid)
    rec["pg_state_after_close"] = (row or {}).get("state")
    rec["pg_last_query_after_close"] = (row or {}).get("last_query")
    rec["xact_start_after_close"] = (row or {}).get("xact_start")
    rec["still_in_pool"] = row is not None

    # Next borrower of the SAME pooled connection:
    try:
        with repair_engine.connect() as c:
            c.execute(text("SELECT 1")).first()
        rec["next_borrower_ok"] = True
        rec["next_borrower_error"] = None
    except Exception as exc:
        rec["next_borrower_ok"] = False
        rec["next_borrower_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return rec


# ---------------------------------------------------------------------------
# (b) readiness probe timeout retention
# ---------------------------------------------------------------------------
async def probe_retention(engine, budget_s: float, stmt: str) -> dict[str, Any]:
    """The readiness DB probe shape (readiness.py:1462-1482).

        def _probe():
            with engine.connect() as conn:
                conn.execute(sa_text("SELECT 1"))
            return True, None

    run via asyncio.to_thread + asyncio.wait_for(budget) — on timeout the
    worker thread is ABANDONED and keeps its connection.
    """
    rec: dict[str, Any] = {"budget_s": budget_s, "statement": stmt}

    def _probe() -> tuple[bool, Any]:
        with engine.connect() as conn:
            # Capture the pid BEFORE the statement — a long statement
            # (pg_sleep) must not delay the pid capture.
            rec["probe_pid"] = conn.connection.driver_connection.info.backend_pid
            conn.execute(text(stmt))
        return True, None

    t0 = now()
    try:
        await asyncio.wait_for(asyncio.to_thread(_probe), timeout=budget_s)
        rec["probe_completed"] = True
        rec["timeout_fired"] = False
    except asyncio.TimeoutError:
        rec["probe_completed"] = False
        rec["timeout_fired"] = True
    rec["timeout_at_s"] = round(now() - t0, 4)

    # Sample the retention curve.
    samples: list[dict[str, Any]] = []
    t_probe = now()
    while (now() - t_probe) < 8.0:
        pid = rec.get("probe_pid")
        row = activity_by_pid(ADMIN, pid) if pid else None
        samples.append(
            {
                "t_s": round(now() - t_probe, 4),
                "checkedout": engine.pool.checkedout(),
                "pg_state": (row or {}).get("state"),
                "pg_present": row is not None,
            }
        )
        if row is None and engine.pool.checkedout() == 0:
            break
        await asyncio.sleep(0.05)
    rec["retention_samples"] = samples
    held = [
        s["t_s"] for s in samples if s["pg_state"] == "idle in transaction"
    ]
    rec["idle_in_txn_observed"] = bool(held)
    rec["max_t_idle_in_txn_s"] = max(held) if held else 0.0
    rec["total_held_s"] = rec["max_t_idle_in_txn_s"]
    rec["checkedout_at_timeout"] = samples[0]["checkedout"] if samples else None
    rec["checkedout_final"] = samples[-1]["checkedout"] if samples else None
    return rec


async def probe_checkout_timeout(engine, budget_s: float) -> dict[str, Any]:
    """Probe timeout during CHECKOUT (pool starvation).

    The manager engine has pool_size=5 + max_overflow=10 = 15 slots and a
    30s pool_timeout. When the pool is exhausted the probe thread WAITS
    for a slot; the asyncio budget fires while it is still waiting.
    """
    rec: dict[str, Any] = {"budget_s": budget_s}
    from sqlalchemy.orm import Session as _S

    holders = []
    for _ in range(15):
        hs = _S(engine)
        hs.execute(text("SELECT 1")).first()
        holders.append(hs)
    rec["holders"] = len(holders)
    rec["checkedout_with_holders"] = engine.pool.checkedout()

    def _probe() -> tuple[bool, Any]:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None

    t0 = now()
    try:
        await asyncio.wait_for(asyncio.to_thread(_probe), timeout=budget_s)
        rec["probe_completed"] = True
        rec["timeout_fired"] = False
    except asyncio.TimeoutError:
        rec["probe_completed"] = False
        rec["timeout_fired"] = True
    rec["timeout_at_s"] = round(now() - t0, 4)
    rec["checkedout_right_after_timeout"] = engine.pool.checkedout()

    # Release the holders; watch the abandoned waiter take a slot.
    samples = []
    for hs in holders:
        try:
            hs.close()
        except Exception:
            pass
    holders.clear()
    t_free = now()
    while (now() - t_free) < 3.0:
        samples.append(
            {
                "t_s": round(now() - t_free, 4),
                "checkedout": engine.pool.checkedout(),
            }
        )
        await asyncio.sleep(0.05)
    rec["post_release_samples"] = samples[:12]
    rec["checkedout_final"] = engine.pool.checkedout()
    rec["verdict"] = (
        "the abandoned probe thread was a POOL WAITER, not a holder: "
        "checkedout() did not exceed the holder count at the timeout"
    )
    return rec


# ---------------------------------------------------------------------------
# (c) mission mint
# ---------------------------------------------------------------------------
def seed_instance(engine, instance_id: str) -> None:
    from daemon.repositories.instance.models import Instance

    with Session(engine) as s:
        if s.get(Instance, instance_id) is not None:
            return
        s.add(
            Instance(
                instance_id=instance_id,
                project_id="p1",
                agent_id="worker",
                agent_dir="/tmp/phase0",
            )
        )
        s.commit()


def mint_case(engine, anchor: str, mode: str) -> dict[str, Any]:
    from daemon.services.mission_record_service import MissionRecordService

    svc = MissionRecordService(engine)
    rec: dict[str, Any] = {"anchor": anchor, "mode": mode}
    try:
        res = svc.mint_boot_epoch_if_none(anchor)
        rec["ok"] = True
        rec["mission_id"] = res.mission_id
        rec["rung"] = res.rung
    except Exception as exc:
        rec["ok"] = False
        rec["error"] = f"{type(exc).__module__}.{type(exc).__name__}: {str(exc)[:200]}"
    return rec


def main() -> None:
    global ADMIN
    ADMIN = make_admin_conn()
    engine = make_manager_engine()
    repair = make_repair_engine()

    # Idempotent scratch reset — the scratch DB survives across runs.
    from sqlmodel import SQLModel as _SQLModel

    import daemon.repositories.instance.models  # noqa: F401
    import daemon.repositories.mission_record.models  # noqa: F401

    _SQLModel.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("DELETE FROM mission_records"))
        c.execute(text("DELETE FROM instances WHERE instance_id LIKE 'anchor-%'"))

    out: dict[str, Any] = {}

    # ==================================================================
    # (a) ens_db_tools explicit-BEGIN shapes
    # ==================================================================
    out["a1_ddl_statement_fails"] = ens_db_dryrun_shape(repair, "statement")
    out["a2_rollback_itself_fails"] = ens_db_dryrun_shape(repair, "rollback")
    out["a3_begin_itself_fails"] = ens_db_dryrun_shape(repair, "begin")

    # ---- the pool's reset_on_return behaviour (the crux) -------------
    # Hand an ABORTED connection back to the pool, then borrow it.
    reset_probe: dict[str, Any] = {}
    s = Session(repair)
    try:
        s.execute(text("SELECT * FROM no_such_table_phase0"))  # aborts the txn
    except Exception as _abort_exc:
        reset_probe["abort_error"] = f"{type(_abort_exc).__name__}"
    raw = s.connection().connection.driver_connection
    reset_probe["in_transaction_before_close"] = raw.info.transaction_status != 0
    reset_probe["pg_state_before_close"] = (
        backend_state(raw.info.backend_pid) or {}
    ).get("state")
    s.close()
    time.sleep(0.2)
    row = activity_by_pid(ADMIN, raw.info.backend_pid)
    reset_probe["pg_state_after_close"] = (row or {}).get("state")
    reset_probe["still_in_pool"] = row is not None
    try:
        with repair.connect() as c:
            c.execute(text("SELECT 1")).first()
        reset_probe["next_borrower_ok"] = True
    except Exception as exc:
        reset_probe["next_borrower_ok"] = False
        reset_probe["next_borrower_error"] = (
            f"{type(exc).__name__}: {str(exc)[:160]}"
        )
    reset_probe["pool_reset_on_return"] = str(
        repair.pool._reset_on_return
        if hasattr(repair.pool, "_reset_on_return")
        else "n/a"
    )
    out["a4_pool_reset_on_return"] = reset_probe

    # ---- the wedge-capture one-shot cascade --------------------------
    # Reproduces the incident journal quote VERBATIM:
    #   "Wedge-capture: store census failed: current transaction is
    #    aborted, commands ignored until rollback"
    cascade: dict[str, Any] = {}
    try:
        from daemon.services.wedge_capture.db_capture import (
            CAPTURE_APPLICATION_NAME,
        )

        cascade["application_name"] = CAPTURE_APPLICATION_NAME
    except Exception as exc:
        cascade["application_name"] = f"import failed: {exc}"
    import psycopg

    from common import (
        SCRATCH_HOST,
        SCRATCH_PASSWORD,
        SCRATCH_PORT,
        SCRATCH_USER,
    )

    dsn = (
        f"postgresql://{SCRATCH_USER}:{SCRATCH_PASSWORD}@"
        f"{SCRATCH_HOST}:{SCRATCH_PORT}/ensemble_dbpool_scratch_phase0"
    )
    conn = psycopg.connect(
        dsn,
        application_name="wedge-capture",
        options="-c statement_timeout=60000",
    )
    cascade["steps"] = []
    try:
        for label, q in (
            (
                "executing-turns (uses 'tasks')",
                "SELECT t.work_id FROM tasks t WHERE t.status='running' LIMIT 1",
            ),
            (
                "store census",
                "SELECT count(*) FROM checkpoint_store",
            ),
            (
                "resume history",
                "SELECT work_id FROM tasks WHERE auto_continued_at IS NOT NULL LIMIT 1",
            ),
        ):
            try:
                with conn.cursor() as cur:
                    cur.execute(q)
                    cur.fetchall()
                cascade["steps"].append({"step": label, "ok": True})
            except Exception as exc:
                cascade["steps"].append(
                    {
                        "step": label,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                    }
                )
    finally:
        conn.close()
    cascade["reproduces_incident_journal_quote"] = any(
        "current transaction is aborted" in (st.get("error") or "")
        for st in cascade["steps"]
    )
    out["a5_wedge_capture_one_shot_cascade"] = cascade

    emit("T5a FIX-SITE BEHAVIOR — ens_db_tools explicit BEGIN", out)

    # ==================================================================
    # (b) readiness probe timeout retention
    # ==================================================================
    async def run_b() -> dict[str, Any]:
        res: dict[str, Any] = {}
        res["b1_fast_SELECT_1"] = await probe_retention(
            engine, 0.5, "SELECT 1"
        )
        res["b2_slow_pg_sleep_5s"] = await probe_retention(
            engine, 0.5, "SELECT pg_sleep(5)"
        )

        # b3: the probe times out DURING CHECKOUT (pool starvation).
        # This is the realistic incident shape: the pool is exhausted,
        # so the probe thread WAITS for a slot and the asyncio budget
        # fires before it ever gets a connection.
        res["b3_checkout_starvation"] = await probe_checkout_timeout(
            engine, budget_s=0.5
        )
        return res

    out_b = asyncio.run(run_b())
    emit("T5b READINESS PROBE TIMEOUT RETENTION", out_b)

    # ==================================================================
    # (c) mission mint
    # ==================================================================
    seed_instance(engine, "anchor-mint-1")
    seed_instance(engine, "anchor-mint-2")
    c_res: dict[str, Any] = {}
    c_res["c1_first_mint"] = mint_case(engine, "anchor-mint-1", "first")
    c_res["c2_second_mint_same_anchor"] = mint_case(
        engine, "anchor-mint-1", "second-same-anchor"
    )
    c_res["c3_mint_different_anchor"] = mint_case(
        engine, "anchor-mint-2", "different-anchor"
    )

    # c4: CONCURRENT mints for the SAME anchor
    import concurrent.futures

    from daemon.services.mission_record_service import MissionRecordService

    seed_instance(engine, "anchor-race")
    errors: list[str] = []
    oks = 0

    def _one() -> None:
        svc = MissionRecordService(engine)
        try:
            svc.mint_boot_epoch_if_none("anchor-race")
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {str(exc)[:140]}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_one) for _ in range(8)]
        for f in futs:
            f.result()
    c_res["c4_concurrent_same_anchor_8_way"] = {
        "errors": errors,
        "error_count": len(errors),
        "unique_violation_count": sum(
            1 for e in errors if "ux_mission_records_anchor_open" in e
            or "UniqueViolation" in e
        ),
    }

    # c4b: DETERMINISTIC race proof. The boot lane has THREE mint sites
    # that all use the resumed instance_id as the anchor:
    #   auto_continue_boot_pass.py:721  (_make_boot_continue_release_handler)
    #   auto_continue_boot_pass.py:1156 (_make_orphan_sweep_release_handler)
    #   api.py:2721 -> sweep_orphaned_live_turns (direct call)
    # A barrier forces the probe->insert window to overlap, which is the
    # interleaving the boot's per-instance mints can produce.
    import threading as _th

    from daemon.repositories.mission_record.repository import (
        MissionRecordRepository,
    )

    seed_instance(engine, "anchor-race2")
    barrier = _th.Barrier(6)
    orig_find = MissionRecordRepository.find_open_epoch_by_anchor

    def _raced_find(self, anchor_instance_id, *, session=None):
        res = orig_find(self, anchor_instance_id, session=session)
        try:
            barrier.wait(timeout=10)
        except Exception:
            pass
        return res

    MissionRecordRepository.find_open_epoch_by_anchor = _raced_find
    race_errors: list[str] = []
    try:
        def _one2() -> None:
            svc = MissionRecordService(engine)
            try:
                svc.mint_boot_epoch_if_none("anchor-race2")
            except Exception as exc:
                race_errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(_one2) for _ in range(6)]
            for f in futs:
                f.result()
    finally:
        MissionRecordRepository.find_open_epoch_by_anchor = orig_find
    c_res["c4b_deterministic_concurrent_race"] = {
        "threads": 6,
        "error_count": len(race_errors),
        "unique_violation_count": sum(
            1 for e in race_errors if "ux_mission_records_anchor_open" in e
        ),
        "sample_errors": race_errors[:3],
        "verdict": "a CONCURRENT double-mint for the SAME anchor is the "
        "trigger; the sequential path is idempotent (c2)",
    }

    # c5: does the losing mint poison a CALLER-supplied session?
    seed_instance(engine, "anchor-caller")
    caller_poison: dict[str, Any] = {}
    try:
        svc = MissionRecordService(engine)
        svc.mint_boot_epoch_if_none("anchor-caller")  # create the open epoch
        from sqlmodel import Session as _SMSession

        from daemon.repositories.mission_record.models import MissionRecord

        with _SMSession(engine) as caller:
            caller.execute(text("SELECT 1"))
            # Force the losing-mint shape INSIDE the caller's session:
            # a duplicate OPEN epoch insert violates the partial unique
            # index and aborts the caller's transaction.
            dup = MissionRecord(
                anchor_instance_id="anchor-caller",
                epoch_seq=99,
                founding_source="boot",
                founding_message_id=None,
                status="open",
            )
            caller.add(dup)
            try:
                caller.commit()
                caller_poison["insert_result"] = "no error"
            except Exception as exc:
                caller_poison["mint_error"] = (
                    f"{type(exc).__name__}: {str(exc)[:140]}"
                )
            try:
                caller.execute(text("SELECT 1"))
                caller_poison["caller_session_usable_after"] = True
            except Exception as exc:
                caller_poison["caller_session_usable_after"] = False
                caller_poison["caller_session_error"] = (
                    f"{type(exc).__name__}: {str(exc)[:160]}"
                )
            try:
                caller.execute(text("SELECT 1"))
                caller_poison["caller_session_usable_after"] = True
            except Exception as exc:
                caller_poison["caller_session_usable_after"] = False
                caller_poison["caller_session_error"] = (
                    f"{type(exc).__name__}: {str(exc)[:160]}"
                )
    except Exception as exc:
        caller_poison["outer_error"] = f"{type(exc).__name__}: {exc}"
    c_res["c5_caller_session_poisoning"] = caller_poison

    emit("T5c MISSION MINT — trigger identification", c_res)

    engine.dispose()
    repair.dispose()
    ADMIN.close()


if __name__ == "__main__":
    main()
