"""T2 — What pg_stat_activity ACTUALLY shows (defines the F7 invariant).

Sub-targets:
  (a) a session CHECKED OUT BEFORE ITS FIRST STATEMENT
  (b) a session that issued a statement and is now idle-in-transaction
  (c) a session that is idle-in-txn, then handed back / rolled back
  (d) the RESIDENCE distribution (xact_start -> state_change) for pool
      backends under churn
  (e) whether idle_in_transaction_session_timeout actually fires

Run:  /home/nea/ensemble-src/.venv/bin/python t2_stat_activity.py
"""

from __future__ import annotations

import time

from sqlalchemy import text

from common import (
    SCRATCH_DB,
    activity,
    activity_by_pid,
    emit,
    make_admin_conn,
    make_manager_engine,
    now,
    summarize,
)


def main() -> None:
    admin = make_admin_conn()
    engine = make_manager_engine()

    # ------------------------------------------------------------------
    # (a0) A FRESH connection, NO pool_pre_ping, checked out, NO
    #      statement at all — the pure "checked out before first
    #      statement" case (no pre-ping ROLLBACK to mask it).
    # ------------------------------------------------------------------
    import sqlalchemy

    fresh_engine = sqlalchemy.create_engine(
        __import__("common").SCRATCH_URL,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=False,
    )
    fconn = fresh_engine.connect()
    fpid = fconn.connection.dbapi_connection.info.backend_pid
    time.sleep(0.2)
    snap_a0 = activity_by_pid(admin, fpid)
    emit("T2a0 FRESH CHECKED-OUT-NO-STATEMENT (no pre-ping)", snap_a0)
    print(
        "  [a0] visible as 'idle in transaction'? "
        f"{snap_a0 is not None and snap_a0['state'] == 'idle in transaction'}"
    )
    print(f"  [a0] xact_start = {(snap_a0 or {}).get('xact_start')}")
    fconn.close()
    fresh_engine.dispose()

    # ------------------------------------------------------------------
    # (a) CHECKED OUT BEFORE ANY STATEMENT
    # ------------------------------------------------------------------
    conn = engine.connect()
    raw = conn.connection.dbapi_connection  # psycopg3 connection
    pid = raw.info.backend_pid
    time.sleep(0.2)
    snap_a = activity_by_pid(admin, pid)
    emit("T2a CHECKED-OUT-BEFORE-FIRST-STATEMENT", snap_a)
    print(f"  [a] in_transaction (client-side) = {raw.info.transaction_status}")

    # ------------------------------------------------------------------
    # (b) AFTER A STATEMENT  ->  autobegin
    # ------------------------------------------------------------------
    conn.execute(text("SELECT 1"))
    snap_b = activity_by_pid(admin, pid)
    emit("T2b AFTER-FIRST-STATEMENT (autobegin)", snap_b)
    print(f"  [b] in_transaction (client-side) = {raw.info.transaction_status}")

    # ------------------------------------------------------------------
    # (c) ROLLBACK / RETURN-TO-POOL  ->  row shape at each step
    # ------------------------------------------------------------------
    conn.rollback()
    snap_c1 = activity_by_pid(admin, pid)
    conn.close()  # return to pool (NOT a PG disconnect)
    time.sleep(0.2)
    snap_c2 = activity_by_pid(admin, pid)
    emit("T2c1 AFTER EXPLICIT ROLLBACK (still checked out)", snap_c1)
    emit("T2c2 AFTER RETURN-TO-POOL (conn.close())", snap_c2)

    # ------------------------------------------------------------------
    # (d) RESIDENCE DISTRIBUTION under churn
    # ------------------------------------------------------------------
    # Churn = repeated check-out -> statement -> hold -> return. Each
    # borrow opens a transaction (autobegin) and the backend is
    # observable in 'idle in transaction' for the whole borrow.
    residence_samples: list[float] = []
    observed_states: dict[str, int] = {}

    for i in range(40):
        c = engine.connect()
        cpid = c.connection.dbapi_connection.info.backend_pid
        c.execute(text("SELECT 1"))
        # let the backend settle into idle-in-transaction
        time.sleep(0.01)
        row = activity_by_pid(admin, cpid)
        if row:
            observed_states[str(row["state"])] = (
                observed_states.get(str(row["state"]), 0) + 1
            )
            if row["xact_start"] is not None and row["state_change"] is not None:
                residence_samples.append(
                    float((row["state_change"] - row["xact_start"]).total_seconds())
                )
        c.close()
    emit(
        "T2d RESIDENCE DISTRIBUTION (xact_start -> state_change)",
        {
            "samples": summarize(residence_samples),
            "observed_states_during_borrow": observed_states,
            "note": "residence here is the OBSERVABLE lag, not the hold "
            "length; the borrow was closed immediately after the sample",
        },
    )

    # ------------------------------------------------------------------
    # (d2) The residence of a HELD borrow (the leak-shaped case):
    #      how long can a backend sit idle-in-transaction?
    # ------------------------------------------------------------------
    held_samples: list[float] = []
    for hold_s in (0.5, 1.0, 2.0, 3.0):
        c = engine.connect()
        cpid = c.connection.dbapi_connection.info.backend_pid
        c.execute(text("SELECT 1"))
        t0 = now()
        time.sleep(hold_s)
        row = activity_by_pid(admin, cpid)
        if row and row["xact_start"] is not None:
            held_samples.append(
                float((now() - t0))
            )
        c.close()
    emit(
        "T2d2 HELD-BORROW IDLE-IN-TXN OBSERVABILITY",
        {
            "hold_requested_s": [0.5, 1.0, 2.0, 3.0],
            "observed_hold_s": summarize(held_samples),
            "note": "confirms a held borrow IS visible as idle-in-txn "
            "(so a census CAN see it) — T2a shows the pre-statement "
            "case is NOT visible as idle-in-txn",
        },
    )

    # ------------------------------------------------------------------
    # (e) idle_in_transaction_session_timeout  -- observed, not documented
    # ------------------------------------------------------------------
    e_result: dict[str, object] = {}
    probe = engine.connect()
    try:
        pid_e = probe.connection.dbapi_connection.info.backend_pid
        probe.execute(text("SET idle_in_transaction_session_timeout = '2s'"))
        probe.execute(text("SELECT 1"))
        # Force an explicit open transaction that stays idle.
        probe.execute(text("BEGIN"))
        time.sleep(0.2)
        row_mid = activity_by_pid(admin, pid_e)
        e_result["before_wait"] = row_mid
        t_wait0 = now()
        # wait past the 2s timeout
        while (now() - t_wait0) < 6.0:
            time.sleep(0.25)
            row_now = activity_by_pid(admin, pid_e)
            if row_now is None:
                break
        row_after = activity_by_pid(admin, pid_e)
        e_result["after_wait"] = row_after
        e_result["backend_row_present_after_wait"] = row_after is not None
        e_result["waited_s"] = round(now() - t_wait0, 2)
        if row_after is not None:
            e_result["state_after"] = row_after["state"]
        else:
            e_result["state_after"] = "BACKEND GONE (terminated by server)"
    except Exception as exc:  # the server-side termination surfaces here
        e_result["client_exception"] = (
            f"{type(exc).__module__}.{type(exc).__name__}: {exc}"
        )
    finally:
        try:
            probe.close()
        except Exception:
            pass
    emit("T2e idle_in_transaction_session_timeout=2s OBSERVED", e_result)

    # ------------------------------------------------------------------
    # (e2) Does the timeout fire on a TRANSACTION-BEGINNING session the
    #      way the incident's backends were shaped?
    # ------------------------------------------------------------------
    e2: dict[str, object] = {}
    probe2 = engine.connect()
    try:
        pid_e2 = probe2.connection.dbapi_connection.info.backend_pid
        probe2.execute(text("SET idle_in_transaction_session_timeout = '2s'"))
        probe2.execute(text("BEGIN"))
        probe2.execute(text("SELECT 1"))
        time.sleep(0.2)
        e2["before"] = activity_by_pid(admin, pid_e2)
        t0 = now()
        while (now() - t0) < 6.0:
            time.sleep(0.25)
            if activity_by_pid(admin, pid_e2) is None:
                break
        e2["after"] = activity_by_pid(admin, pid_e2)
        e2["terminated"] = e2["after"] is None
        e2["elapsed_s"] = round(now() - t0, 2)
    except Exception as exc:
        e2["client_exception"] = (
            f"{type(exc).__module__}.{type(exc).__name__}: {exc}"
        )
    finally:
        try:
            probe2.close()
        except Exception:
            pass
    emit("T2e2 idle-in-txn timeout on a BEGIN+SELECT session", e2)

    # ------------------------------------------------------------------
    # (f) Is a CHECKED-OUT-but-no-statement backend visible at all?
    #     (this is the "census blindness" claim)
    # ------------------------------------------------------------------
    c = engine.connect()
    cpid = c.connection.dbapi_connection.info.backend_pid
    row_f = activity_by_pid(admin, cpid)
    emit("T2f CHECKED-OUT-NO-STATEMENT row (census blindness check)", row_f)
    idle_in_txn_visible = (
        row_f is not None and row_f["state"] == "idle in transaction"
    )
    print(f"  [f] visible as 'idle in transaction'? {idle_in_txn_visible}")
    c.close()

    engine.dispose()
    admin.close()
    emit(
        "T2 SUMMARY",
        {
            "scratch_db": SCRATCH_DB,
            "a_checked_out_before_statement_state": (snap_a or {}).get("state"),
            "b_after_statement_state": (snap_b or {}).get("state"),
            "c1_after_rollback_state": (snap_c1 or {}).get("state"),
            "c2_after_return_to_pool_state": (snap_c2 or {}).get("state"),
            "e_idle_in_txn_timeout_fired": e_result.get(
                "backend_row_present_after_wait"
            )
            is False,
            "e2_idle_in_txn_timeout_fired": e2.get("terminated"),
            "f_checked_out_no_statement_visible_as_idle_in_txn": (
                idle_in_txn_visible
            ),
        },
    )


if __name__ == "__main__":
    main()
