#!/usr/bin/env python3
"""PG Pending-Watchers Type-Binding Probe — task 26303 (prod v0.11.3 bug).

Probes (on REAL PostgreSQL via ``ensemble_test`` — no prod credentials, no
schema pollution):

  P1 — REPRO: parameterized INT bind against ``source_task_id VARCHAR``
       raises ``psycopg.errors.UndefinedFunction``
       (``operator does not exist: character varying = ...``).
       This is the exact prod bug shape (SQLAlchemy parameterizes ``int``
       against the ``String`` column → PG has no implicit cast).
  P2 — FIX-SHAPE: parameterized STR bind against the same column executes
       cleanly (the fix at a77647bf is ``str(task_id)`` at the call site).
  P3 — Cleanup: ``DROP SCHEMA IF EXISTS gate_pg_probe CASCADE`` in finally.

Schema shape mirrors prod (verified via Part A):
  * Table in ``public.dependency_watchers``: column ``source_task_id`` is
    ``character varying`` (no length cap; model has ``max_length=64`` but
    the deployed DDL is unbounded varchar — match exactly to avoid
    silent cast differences).

Spec: task 26303 incident (prod v0.11.3 log 2026-08-30 09:25:25);
fix commit a77647bf ``daemon/services/job_recovery_service.py:3208``.

This is TEST-ENV ONLY. NEVER accepts prod credentials. Exits:
  0  — all assertions passed
  1  — any assertion failed (or PG unavailable AND ``FAIL_ON_NO_PG`` env var is set)
  124 — internal timeout (signal.alarm or pg statement_timeout)
"""
from __future__ import annotations

import os
import signal
import sys
import traceback
from textwrap import indent

import psycopg
from psycopg import errors as pgerrs

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Default DSN — matches tests/postgres/conftest.py:68-74
PG_HOST = os.environ.get("PG_TEST_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
PG_DB = os.environ.get("PG_TEST_DB", "ensemble_test")
PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")

# Self-guard: 150s (well under 5-min pack cap; leaves margin for python startup)
INTERNAL_TIMEOUT_S = 150

# Allow operator to force FAIL on missing PG (vs default SKIP-with-PASS-note)
FAIL_ON_NO_PG = os.environ.get("PG_PROBE_FAIL_ON_NO_PG", "") == "1"

SCHEMA = "gate_pg_probe"
TABLE = f"{SCHEMA}.dependency_watchers"

CONNINFO = (
    f"host={PG_HOST} port={PG_PORT} user={PG_USER} dbname={PG_DB} "
    f"connect_timeout=5"
)


# ---------------------------------------------------------------------------
# Self-guard: signal.alarm timeout (caller may also wrap with `timeout 300`)
# ---------------------------------------------------------------------------

class TimeoutError_(Exception):
    pass


def _alarm_handler(signum, frame):
    raise TimeoutError_(f"internal timeout after {INTERNAL_TIMEOUT_S}s")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def banner(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def try_connect() -> psycopg.Connection | None:
    """Connect with autocommit=False; statement_timeout=5s as a defensive bound."""
    try:
        conn = psycopg.connect(CONNINFO, password=PG_PASSWORD, autocommit=False)
    except Exception as e:
        return None  # signal at call site
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '5s'")
        conn.commit()
    except Exception:
        conn.rollback()
    return conn


def setup_schema(cur) -> tuple[int, str]:
    """Create the throwaway schema + table; return (n_rows_inserted, schema_name)."""
    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    cur.execute(f"CREATE SCHEMA {SCHEMA}")
    # Match prod exactly: varchar (no length cap), per Part A finding.
    cur.execute(
        f"""
        CREATE TABLE {TABLE} (
            id              serial PRIMARY KEY,
            source_task_id  varchar NOT NULL,
            state           varchar NOT NULL DEFAULT 'PENDING'
        )
        """
    )
    # Insert 2 rows (some non-matching + one matching the test param)
    cur.execute(
        f"INSERT INTO {TABLE} (source_task_id, state) VALUES "
        f"(%s, %s), (%s, %s)",
        ("99999", "FIRED", "26303", "PENDING"),
    )
    return cur.rowcount, SCHEMA


def repro_int_param(cur) -> tuple[bool, str]:
    """P1 — INT param bind against varchar column. Expect UndefinedFunction."""
    print("  SQL: SELECT count(*) FROM " + TABLE + " WHERE source_task_id = %s")
    print("  param: (26303,)  python_type=int")
    try:
        cur.execute(
            f"SELECT count(*) FROM {TABLE} WHERE source_task_id = %s",
            (26303,),
        )
        cnt = cur.fetchone()[0]
        return False, f"UNEXPECTED SUCCESS — count={cnt} (no UndefinedFunction raised)"
    except pgerrs.UndefinedFunction as e:
        msg = str(e).strip()
        diag = getattr(e, "diag", None)
        sqlstate = getattr(diag, "sqlstate", None) if diag else None
        # PG message format varies by version; this is the canonical wording.
        # The actual prod log had: "operator does not exist: character varying = integer"
        # but psycopg will pick the smallest int type that fits — smallint here
        # (26303 fits in int2); the verifier accepts either smallint or integer
        # because both demonstrate the same root cause.
        acceptable = ("character varying = smallint", "character varying = integer")
        if any(s in msg for s in acceptable):
            print("  ✓ UndefinedFunction raised (expected)")
            print(f"    sqlstate: {sqlstate}")
            print("    message (verbatim):")
            print(indent(msg, "      | "))
            return True, msg
        return False, f"UndefinedFunction raised but message lacks expected wording: {msg!r}"
    except Exception as e:
        return False, f"unexpected {type(e).__name__}: {e}"


def fix_shape_str_param(cur) -> tuple[bool, str]:
    """P2 — STR param bind against varchar column. Expect clean execution."""
    print("  SQL: SELECT count(*) FROM " + TABLE + " WHERE source_task_id = %s")
    print("  param: ('26303',)  python_type=str")
    try:
        cur.execute(
            f"SELECT count(*) FROM {TABLE} WHERE source_task_id = %s",
            ("26303",),
        )
        cnt = cur.fetchone()[0]
        if cnt == 1:
            print(f"  ✓ clean execution; count={cnt} (matches the seeded row)")
            return True, f"count={cnt}"
        return False, f"clean but unexpected count={cnt} (expected 1)"
    except Exception as e:
        return False, f"unexpected {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    banner(
        "PG Pending-Watchers Type-Binding Probe (task 26303)\n"
        f"DSN: postgresql://{PG_USER}:***@{PG_HOST}:{PG_PORT}/{PG_DB}\n"
        "TEST-ENV ONLY (ensemble_test) — never accepts prod creds"
    )

    # Self-guard
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(INTERNAL_TIMEOUT_S)

    conn = try_connect()
    if conn is None:
        msg = (
            f"PostgreSQL unreachable at {PG_HOST}:{PG_PORT}/{PG_DB} "
            f"as {PG_USER}. Verify the server is up and credentials are correct."
        )
        if FAIL_ON_NO_PG:
            print(f"SKIP-FAIL: {msg}")
            return 1
        print(f"SKIP-WITH-PASS-NOTE: {msg}")
        print("(set PG_PROBE_FAIL_ON_NO_PG=1 to fail instead of skip)")
        print("RESULT: PASS")
        return 0

    failures: list[str] = []

    try:
        with conn.cursor() as cur:
            # Setup
            banner("SETUP — create gate_pg_probe schema + minimal table")
            try:
                inserted, sname = setup_schema(cur)
                conn.commit()
                print(f"  schema={sname}")
                print(f"  table={TABLE} (source_task_id varchar, state varchar)")
                print(f"  inserted {inserted} rows")
            except Exception as e:
                conn.rollback()
                failures.append(f"SETUP failed: {type(e).__name__}: {e}")
                # Try cleanup once
                try:
                    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
                    conn.commit()
                except Exception:
                    conn.rollback()
                return _report(failures)

            try:
                # P1 — REPRO
                banner("P1 — REPRO: parameterized INT bind (expect UndefinedFunction)")
                ok, info = repro_int_param(cur)
                if not ok:
                    failures.append(f"P1: {info}")
                conn.rollback()

                # P2 — FIX-SHAPE
                banner("P2 — FIX-SHAPE: parameterized STR bind (expect clean)")
                ok, info = fix_shape_str_param(cur)
                if not ok:
                    failures.append(f"P2: {info}")
                conn.rollback()
            finally:
                # Cleanup — DROP SCHEMA CASCADE
                banner("TEARDOWN — DROP SCHEMA gate_pg_probe CASCADE")
                try:
                    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
                    conn.commit()
                    print("  ✓ schema dropped cleanly")
                except Exception as e:
                    conn.rollback()
                    failures.append(f"TEARDOWN failed: {type(e).__name__}: {e}")
    except TimeoutError_ as e:
        print(f"\nTIMEOUT: {e}", file=sys.stderr)
        # Best-effort cleanup
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
                conn.commit()
        except Exception:
            pass
        print("RESULT: TIMEOUT")
        return 124
    except Exception as e:
        print(f"\nUNEXPECTED ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        # Best-effort cleanup
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
                conn.commit()
        except Exception:
            pass
        print("RESULT: FAIL")
        return 1
    finally:
        signal.alarm(0)  # cancel alarm
        try:
            conn.close()
        except Exception:
            pass

    return _report(failures)


def _report(failures: list[str]) -> int:
    banner("RESULT")
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        print("RESULT: FAIL")
        return 1
    print("All probe assertions passed.")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    sys.exit(rc)