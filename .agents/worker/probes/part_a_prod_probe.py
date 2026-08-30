"""Part A — read-only prod probe for task 26303 fix shape.

Connects to ensemble_prod and runs SELECT-only queries (autocommit=False, commit nothing):
  A1 — column type for dependency_watchers.source_task_id + table schema
  A2 — REPRO: parameterized INT bind → expect UndefinedFunction (verbatim msg)
  A3 — FIX-SHAPE: parameterized STR bind → expect clean execution
  A4 — count of PENDING rows in dependency_watchers (context only)

PostgreSQL password is read from $POSTGRES_PASSWORD env var; never echoed.
The script enforces SELECT-only via a wrapper; SET/transaction-control calls
are isolated to a one-shot guard setup that never commits any data.
"""
from __future__ import annotations

import os
import sys
from textwrap import indent

import psycopg
from psycopg import errors as pgerrs

DSN_HOST = "localhost"
DSN_PORT = 5432
DSN_USER = "ensemble"
DSN_DB = "ensemble_prod"

pw = os.environ.get("POSTGRES_PASSWORD", "")
if not pw:
    print("FATAL: POSTGRES_PASSWORD env var is not set", file=sys.stderr)
    sys.exit(2)

CONNINFO = (
    f"host={DSN_HOST} port={DSN_PORT} user={DSN_USER} "
    f"dbname={DSN_DB} connect_timeout=5"
)

FORBIDDEN_PREFIXES = (
    "INSERT", "UPDATE", "DELETE", "TRUNCATE",
    "CREATE", "DROP", "ALTER", "GRANT", "REVOKE",
    "COPY", "VACUUM", "ANALYZE", "LOCK", "CALL",
)


def safe_select(cur, sql: str, params=None):
    """Issue a SELECT (or SHOW/EXPLAIN/WITH...SELECT) statement.

    Raises RuntimeError if the SQL does not look safe for prod.
    """
    head = sql.lstrip().split(None, 1)[0].upper() if sql else ""
    if head in FORBIDDEN_PREFIXES:
        raise RuntimeError(f"GUARD: forbidden statement {head!r} (read-only prod)")
    # Allow SELECT, WITH, SHOW, EXPLAIN, VALUES
    if head not in {"SELECT", "WITH", "SHOW", "EXPLAIN", "VALUES"}:
        raise RuntimeError(f"GUARD: unexpected statement head {head!r} (read-only prod)")
    return cur.execute(sql, params) if params is not None else cur.execute(sql)


print("=" * 78)
print("Part A — READ-ONLY prod probe for task 26303 fix shape")
print(f"DSN: postgresql://{DSN_USER}:***@{DSN_HOST}:{DSN_PORT}/{DSN_DB}")
print(f"PostgreSQL password source: $POSTGRES_PASSWORD (set, length={len(pw)})")
print("=" * 78)

try:
    conn = psycopg.connect(CONNINFO, password=pw, autocommit=False)
except Exception as e:
    print(f"FATAL: connect failed: {e}", file=sys.stderr)
    sys.exit(2)

READ_ONLY_GUARD_ACTIVE = False
try:
    with conn.cursor() as cur:
        # Per brief: try default_transaction_read_only first.
        try:
            cur.execute("SET default_transaction_read_only = on")
            cur.execute("SET statement_timeout = '5s'")
            # Show default (affects new transactions)
            cur.execute("SHOW default_transaction_read_only")
            dro = cur.fetchone()[0]
            if dro == "on":
                READ_ONLY_GUARD_ACTIVE = True
                print(f"[guard] default_transaction_read_only = on "
                      f"(PG-enforced read-only ACTIVE for new tx)")
            else:
                print(f"[guard] default_transaction_read_only = {dro!r}")
                conn.rollback()
        except Exception as e:
            print(f"[guard] SET default_transaction_read_only UNAVAILABLE "
                  f"({type(e).__name__}): {e}")
            print(f"[guard] falling back to SELECT-only wrapper + "
                  f"autocommit=False + commit nothing")
            conn.rollback()
            try:
                cur.execute("SET statement_timeout = '5s'")
                conn.commit()
            except Exception:
                conn.rollback()

        # ---- A1: locate schema + column type ----
        print("\n--- A1: locate schema + column type ---")
        safe_select(
            cur,
            """
            SELECT table_schema, column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'dependency_watchers'
              AND column_name = 'source_task_id'
            ORDER BY table_schema
            """,
        )
        rows = cur.fetchall()
        if not rows:
            print("A1 RESULT: dependency_watchers.source_task_id NOT FOUND")
            schema = None
            col_type = None
            cml = None
        else:
            for r in rows:
                schema_, name, dtype, cmaxlen = r
                print(f"A1: schema={schema_!r} column={name!r} "
                      f"data_type={dtype!r} character_maximum_length={cmaxlen}")
            schema = rows[0][0]
            col_type = rows[0][2]
            cml = rows[0][3]
            public_row = next((r for r in rows if r[0] == "public"), None)
            if public_row:
                schema = public_row[0]
                col_type = public_row[2]
                cml = public_row[3]
                print(f"A1 (selected public schema): schema={schema!r} "
                      f"data_type={col_type!r} character_maximum_length={cml}")
        conn.rollback()

        if not schema:
            print("FATAL: cannot find dependency_watchers — aborting A2/A3/A4")
        else:
            qual = f"{schema}.dependency_watchers"
            print(f"\n[qual] using {qual}")

            # ---- A2: REPRO with INT parameter ----
            print("\n--- A2: REPRO parameterized INT bind "
                  "(expect UndefinedFunction) ---")
            print(f"  SQL: SELECT count(*) FROM {qual} "
                  f"WHERE source_task_id = %s  param=(26303,)")
            try:
                safe_select(
                    cur,
                    f"SELECT count(*) FROM {qual} "
                    f"WHERE source_task_id = %s",
                    (26303,),
                )
                cnt = cur.fetchone()[0]
                print(f"A2 RESULT (UNEXPECTED SUCCESS): count={cnt}")
                print("  ^ if you see this, prod already has implicit cast "
                      "or column type changed")
            except pgerrs.UndefinedFunction as e:
                diag = getattr(e, "diag", None)
                sqlstate = getattr(diag, "sqlstate", None) if diag else None
                msg = str(e).strip()
                print(f"A2 RESULT: UndefinedFunction RAISED (expected)")
                print(f"  sqlstate: {sqlstate}")
                print(f"  message (verbatim):")
                print(indent(msg, "    | "))
            except Exception as e:
                print(f"A2 RESULT: NON-UndefinedFunction exception "
                      f"({type(e).__name__}):")
                print(indent(str(e).strip(), "    | "))
            conn.rollback()

            # ---- A3: FIX-SHAPE with STR parameter ----
            print("\n--- A3: FIX-SHAPE parameterized STR bind "
                  "(expect clean) ---")
            try:
                safe_select(
                    cur,
                    f"SELECT count(*) FROM {qual} "
                    f"WHERE source_task_id = %s",
                    ("26303",),
                )
                cnt = cur.fetchone()[0]
                print(f"A3 RESULT (clean): count={cnt}  "
                      "(0 or N rows both OK; fix-shape bound correctly)")
            except Exception as e:
                print(f"A3 RESULT (UNEXPECTED FAIL): "
                      f"{type(e).__name__}: {e}")
            conn.rollback()

            print("\n--- A3b: str-literal form (sanity) ---")
            try:
                safe_select(
                    cur,
                    f"SELECT count(*) FROM {qual} "
                    f"WHERE source_task_id = '26303'",
                )
                cnt = cur.fetchone()[0]
                print(f"A3b RESULT (clean): count={cnt}")
            except Exception as e:
                print(f"A3b RESULT (UNEXPECTED FAIL): "
                      f"{type(e).__name__}: {e}")
            conn.rollback()

            # ---- A4: pending watchers count ----
            print("\n--- A4: PENDING row count (context only) ---")
            try:
                safe_select(
                    cur,
                    f"SELECT count(*) FROM {qual} WHERE state ILIKE 'PENDING'",
                )
                pending_count = cur.fetchone()[0]
                print(f"A4 RESULT: ILIKE 'PENDING' → {pending_count} rows")
            except Exception as e:
                print(f"A4 RESULT: ILIKE 'PENDING' failed: {e}")
                pending_count = None
            conn.rollback()

            try:
                safe_select(
                    cur,
                    f"SELECT state, count(*) FROM {qual} "
                    f"GROUP BY state ORDER BY 2 DESC",
                )
                dist = cur.fetchall()
                print(f"A4 state distribution:")
                if not dist:
                    print("  (table empty)")
                for s, n in dist:
                    print(f"  {s!r}: {n}")
            except Exception as e:
                print(f"A4 state distribution: failed: {e}")
            conn.rollback()

    conn.rollback()
finally:
    conn.close()

print("\n[guard] connection closed cleanly; no writes performed")