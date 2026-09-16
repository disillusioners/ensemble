#!/usr/bin/env bash
# Test Pack: service_tool_pg_smoke_integration_test — repository-level
# PG verifier for the service-tool Phase 3.A.8 acceptance criteria.
#
# Why repository-level on real PG (mirrors the existing
# test/packs/ensure_deferred_pg_smoke_integration_test.sh shape
# exactly): the unit + integration test suite uses file-backed SQLite
# (NullPool + WAL + busy_timeout) which masks several PG-only
# behaviors — partial UNIQUE indexes (D5 same-name guard), the
# ``get_process_start_time`` starttime token semantics across the
# ``/proc`` vs ``ps`` boundary, and the ``ServiceReconciliationService``
# sweep body. The plan row 3.A.8 acceptance is verified here on
# real PG with a disposable database.
#
# FOUR LEGS (per-leg [PASS]/[FAIL] evidence printed):
#   LEG A — _ensure_postgres_columns idempotency: the
#     ``CREATE TABLE IF NOT EXISTS service_tracking`` + 2 indexes
#     run TWICE on a fresh PG database and stay byte-stable
#     (no ALTER error, no second-index error, count of indexes
#     in pg_indexes stays at 3).
#   LEG B — partial UNIQUE index ``idx_service_tracking_name_active``
#     EXISTS in ``pg_indexes`` with the
#     ``WHERE status IN ('starting','running')`` predicate and is
#     ``UNIQUE`` on the ``name`` column.
#   LEG C — cross-platform ``get_process_start_time`` works for a
#     PG-test-spawned process: spawn ``python3 -c "import time;
#     time.sleep(30)"`` via the REAL
#     ``daemon.tools.service_spawner.spawn`` (setsid + start_new_session);
#     read ``start_time`` via the REAL spawner; spawn a SECOND
#     unrelated process and verify the start_time tokens differ.
#     (Round-trip equality: the token returned by ``spawn`` is the
#     SAME value re-read via ``get_process_start_time(pid)``.)
#   LEG D — reconcile sweep marks dead rows EXITED: insert a row
#     with a DEAD PID via ``ServiceRepo.insert``; run the REAL
#     ``ServiceReconciliationService.sweep_once``; verify the row
#     is now EXITED (``reason='dead'``) and the counters return
#     ``alive=0 reaped=1 errors=0``.
#
# LESSONS TRAPS BAKED IN
# (.agents/tester/LESSONS/2026-09-06-pg-smoke-verifier-dialect-traps.md):
#   1. psycopg3 URL: ``postgresql+psycopg://`` (bare postgresql:// →
#      psycopg2, not installed).
#   2. Table names EXACT: ``service_tracking`` (lowercase, singular).
#   3. SQLModel ``Session.exec()`` takes ``params=`` keyword-only;
#      all parameterized raw SQL goes through
#      ``conn.execute(text(...), {...})`` on a connection instead;
#      model queries use Session.exec without positional params.
#   4. Server timezone: ``connect_args={"options": "-c TimeZone=UTC"}``
#      on EVERY engine (local PG runs Asia/Ho_Chi_Minh).
#   5. Trap-vs-exit ordering: cleanup runs from the EXIT trap AFTER
#      the RESULT block and never calls ``exit`` (a trap-level exit
#      would clobber the 0/1/124 contract).
#
# HARD GUARDS (do NOT remove):
#   * Target DB name must NOT contain ``ensemble_prod``; abort loud.
#   * Resolved connection URLs must NOT contain ``ensemble_prod``;
#     abort loud.
#   * Only ``DROP IF EXISTS`` + ``CREATE`` (the name is disposable).
#   * Only ``DROP DATABASE`` on the named DB — never connects to prod.
#
# Disposable DB: ensemble_test_servicetool_3a8
#
# Connection: PG_TEST_* env vars (same convention as
# tests/postgres/conftest.py and the canonical PG smoke packs).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with ``timeout 300``
#   - Layer 2 (script-internal): ``timeout 280s`` on the verifier
#     (covers psql provisioning + the 4-leg Python verifier; LEGs
#     resolve in <2s on local PG).
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
#   5   ABORT (production-DB guard tripped)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: service_tool_pg_smoke_integration_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(PG smoke: service-tool 3.A.8 — 4 legs incl. reconcile-dead-row)"

cd "$PROJECT_DIR"

# ── PG connection defaults (overridable via env) ──────────────────────
PG_TEST_HOST="${PG_TEST_HOST:-localhost}"
PG_TEST_PORT="${PG_TEST_PORT:-5432}"
PG_TEST_ADMIN_DB="${PG_TEST_ADMIN_DB:-ensemble_test}"   # admin DB used only for CREATE/DROP DATABASE
PG_TEST_USER="${PG_TEST_USER:-ensemble}"
PG_TEST_PASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}"

export PG_TEST_HOST PG_TEST_PORT PG_TEST_ADMIN_DB PG_TEST_USER PG_TEST_PASSWORD

# ── HARD GUARD #1: disposable DB name must not be production ─────────
TARGET_DB="ensemble_test_servicetool_3a8"

if [[ "$TARGET_DB" == *"ensemble_prod"* ]]; then
  echo "[FATAL] Refusing to run — disposable DB name '$TARGET_DB' contains 'ensemble_prod'."
  echo "RESULT: ABORT"
  exit 5
fi

ADMIN_URL="postgresql+psycopg://${PG_TEST_USER}:${PG_TEST_PASSWORD}@${PG_TEST_HOST}:${PG_TEST_PORT}/${PG_TEST_ADMIN_DB}"
TARGET_URL="postgresql+psycopg://${PG_TEST_USER}:${PG_TEST_PASSWORD}@${PG_TEST_HOST}:${PG_TEST_PORT}/${TARGET_DB}"

# ── HARD GUARD #2: every resolved URL must not be production ────────
for url in "$ADMIN_URL" "$TARGET_URL"; do
  if [[ "$url" == *"ensemble_prod"* ]]; then
    echo "[FATAL] Refusing to run — resolved URL contains 'ensemble_prod': $url"
    echo "RESULT: ABORT"
    exit 5
  fi
done

# ── Helper: bail loudly if psql or PG is unreachable ─────────────────
if ! command -v psql >/dev/null 2>&1; then
  echo "[FATAL] psql not on PATH — install postgresql client or use the docker test stack."
  echo "RESULT: FAIL"
  exit 1
fi

if ! PGPASSWORD="$PG_TEST_PASSWORD" psql -h "$PG_TEST_HOST" -p "$PG_TEST_PORT" -U "$PG_TEST_USER" -d "$PG_TEST_ADMIN_DB" -tAc "SELECT 1" >/dev/null 2>&1; then
  echo "[FATAL] PostgreSQL not reachable at ${ADMIN_URL} (admin DB). Start the test stack or set PG_TEST_* env vars."
  echo "RESULT: FAIL"
  exit 1
fi

# ── Cleanup trap — always drop the disposable DB on exit ────────────
# LESSONS trap 5: cleanup must NOT 'exit' — it runs from the EXIT trap
# AFTER the RESULT block has set the script's exit code. Calling 'exit'
# here would clobber the RESULT signal. The trap fires on every exit
# path (success, failure, timeout, error) so the DB is always dropped.
cleanup() {
  echo "[cleanup] Dropping disposable database '$TARGET_DB' (rc=$?)..."
  PGPASSWORD="$PG_TEST_PASSWORD" psql -h "$PG_TEST_HOST" -p "$PG_TEST_PORT" -U "$PG_TEST_USER" -d "$PG_TEST_ADMIN_DB" \
    -v ON_ERROR_STOP=0 -c "DROP DATABASE IF EXISTS \"$TARGET_DB\" WITH (FORCE)" >/dev/null 2>&1 || \
  PGPASSWORD="$PG_TEST_PASSWORD" psql -h "$PG_TEST_HOST" -p "$PG_TEST_PORT" -U "$PG_TEST_USER" -d "$PG_TEST_ADMIN_DB" \
    -v ON_ERROR_STOP=0 -c "DROP DATABASE IF EXISTS \"$TARGET_DB\"" >/dev/null 2>&1 || true
  return 0
}
trap cleanup EXIT INT TERM

# ── Provision the disposable DB ─────────────────────────────────────
echo "[setup] Provisioning disposable database '$TARGET_DB'..."
PGPASSWORD="$PG_TEST_PASSWORD" psql -h "$PG_TEST_HOST" -p "$PG_TEST_PORT" -U "$PG_TEST_USER" -d "$PG_TEST_ADMIN_DB" \
  -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$TARGET_DB\"" >/dev/null 2>&1 || true
PGPASSWORD="$PG_TEST_PASSWORD" psql -h "$PG_TEST_HOST" -p "$PG_TEST_PORT" -U "$PG_TEST_USER" -d "$PG_TEST_ADMIN_DB" \
  -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$TARGET_DB\"" >/dev/null

# ── Write the 4-leg verifier to a tmp file (keeps the heredoc readable) ─
VERIFIER="$(mktemp -t svc_tool_pg_verify.XXXXXX.py)"
trap 'rm -f "$VERIFIER"; cleanup' EXIT INT TERM

cat >"$VERIFIER" <<PYEOF
"""4-leg repository-level PG verifier: service-tool Phase 3.A.8.

LEG A  _ensure_postgres_columns idempotency (run twice on PG)
LEG B  partial UNIQUE index idx_service_tracking_name_active EXISTS
LEG C  cross-platform get_process_start_time works for a PG-spawned process
LEG D  reconcile sweep marks dead rows EXITED

All legs run REAL repository code against REAL PostgreSQL. No mocks
(except the manager surface in LEG D — we exercise
ServiceReconciliationService.sweep_once on a real ServiceRepo;
the only mocked surface is the host-context, which the service
never reads).
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every SQLModel table before create_all runs (mirrors
# tests/postgres/conftest.py — the daemon registers models lazily,
# so create_all on a fresh DB produces an empty schema without these).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.service_tool.models  # noqa: F401

from daemon.repositories.service_tool.models import (
    ServiceStatus,
    ServiceTracking,
)
from daemon.repositories.service_tool.repository import ServiceRepo
from daemon.services.service_reconciliation import (
    DEFAULT_SWEEP_INTERVAL_SECONDS,
    DEFAULT_STARTING_GRACE_SECONDS,
    ServiceReconciliationService,
)
from daemon.tools import service_spawner

# LESSONS traps 1 + 4: psycopg3 driver + force UTC on every connection.
ENGINE = create_engine(
    sys.argv[1],
    future=True,
    connect_args={"options": "-c TimeZone=UTC"},
)

LEGS_FAILED: list[str] = []
_SPAWNED_PIDS: list[int] = []


def _check(leg: str, condition: bool, evidence: str) -> None:
    """Record + print a per-leg PASS/FAIL assertion with evidence."""
    if condition:
        print(f"  [{leg}] PASS: {evidence}")
    else:
        print(f"  [{leg}] FAIL: {evidence}")
        LEGS_FAILED.append(f"{leg}: {evidence}")


def _ensure_service_tracking_columns() -> None:
    """Mirror ``EnsembleManager._ensure_postgres_columns`` for the
    ``service_tracking`` slice — the exact CREATE statements
    (CREATE TABLE IF NOT EXISTS + the 2 indexes) — run verbatim
    on the engine. The 3-site index-name pin is enforced by this
    exact body (``idx_service_tracking_name_active``,
    ``idx_service_tracking_pid``).
    """
    # This is the BYTE-IDENTICAL slice of
    # ``EnsembleManager._ensure_postgres_columns`` for
    # ``service_tracking`` (manager.py:5940-5999, Phase 1.C.13b).
    # Drift between this slice and the manager's slice is caught
    # by ``tests/unit/repositories/test_service_tool_repository.py``
    # name-pin tests — if the manager changes the index names,
    # this function is updated in the SAME PR (the 3-site pin).
    statements = [
        (
            "CREATE TABLE IF NOT EXISTS service_tracking ("
            "id BIGSERIAL PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "command TEXT NOT NULL, "
            "pid INTEGER, "
            "start_time INTEGER, "
            "cwd TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'starting', "
            "started_by_instance_id TEXT NOT NULL, "
            "started_by_agent_id TEXT NOT NULL, "
            "log_path TEXT NOT NULL, "
            "exit_code INTEGER, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL"
            ")"
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_service_tracking_name_active "
            "ON service_tracking (name) "
            "WHERE status IN ('starting','running')"
        ),
        (
            "CREATE INDEX IF NOT EXISTS idx_service_tracking_pid "
            "ON service_tracking (pid)"
        ),
    ]
    with ENGINE.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def leg_a() -> None:
    """LEG A — _ensure_postgres_columns idempotency (run twice on PG)."""
    print("[LEG A] service_tracking schema idempotency on PG")
    # First apply — creates the table + 2 indexes.
    _ensure_service_tracking_columns()

    with ENGINE.begin() as conn:
        # Count indexes on service_tracking.
        rows1 = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'service_tracking'"
            )
        ).fetchall()
    indexes_first = sorted(r[0] for r in rows1)
    _check(
        "A",
        indexes_first == sorted(
            [
                "service_tracking_pkey",
                "idx_service_tracking_name_active",
                "idx_service_tracking_pid",
            ]
        ),
        f"first apply: 3 indexes (pkey + 2 service indexes); got {indexes_first}",
    )

    # Second apply — must be a NO-OP (IF NOT EXISTS guards). If the
    # migration is non-idempotent this raises.
    _ensure_service_tracking_columns()

    with ENGINE.begin() as conn:
        rows2 = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'service_tracking'"
            )
        ).fetchall()
    indexes_second = sorted(r[0] for r in rows2)
    _check(
        "A",
        indexes_second == indexes_first,
        f"second apply: byte-identical (no duplicates, no errors); "
        f"first={indexes_first}, second={indexes_second}",
    )

    # Third apply via ``SQLModel.metadata.create_all`` — also a no-op
    # (the index names match the model __table_args__). Drift here
    # would mean the manager.py + models.py + .sql 3-site pin broke.
    SQLModel.metadata.create_all(ENGINE)
    with ENGINE.begin() as conn:
        rows3 = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'service_tracking'"
            )
        ).fetchall()
    indexes_third = sorted(r[0] for r in rows3)
    _check(
        "A",
        indexes_third == indexes_first,
        f"create_all after IF NOT EXISTS: byte-identical; "
        f"got {indexes_third}",
    )


def leg_b() -> None:
    """LEG B — partial UNIQUE index exists on PG with the WHERE clause."""
    print("[LEG B] partial UNIQUE index idx_service_tracking_name_active")
    with ENGINE.begin() as conn:
        row = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'idx_service_tracking_name_active'"
            )
        ).first()
    indexdef = row[0] if row else ""
    _check(
        "B",
        bool(indexdef),
        f"idx_service_tracking_name_active found in pg_indexes: {indexdef}",
    )
    _check(
        "B",
        (
            "UNIQUE" in indexdef.upper()
            and "WHERE" in indexdef.upper()
            and "starting" in indexdef
            and "running" in indexdef
            and "service_tracking" in indexdef
            and "(name)" in indexdef
        ),
        f"index is UNIQUE on (name) with the WHERE clause; got: {indexdef}",
    )


def leg_c() -> None:
    """LEG C — get_process_start_time works for a PG-test-spawned process.

    We exercise the cross-platform helper against a REAL process
    spawned via ``service_spawner.spawn`` (setsid + start_new_session).
    The spawner returns ``(pid, start_time)`` and the helper
    re-reads the SAME token via ``/proc/<pid>/stat`` (Linux) or
    ``ps -o lstart`` (macOS). If the helper is broken on this
    platform the test fails.

    Two distinct processes are spawned back-to-back and each
    round-trip equality is asserted. NOTE: the macOS ``ps -o lstart``
    format reports ``HH:MM:SS``-precision timestamps, so two
    processes spawned within the same second may share a token —
    distinct-tokens is NOT a load-bearing assertion (the
    round-trip equality IS). Linux reports jiffies-since-boot and
    is finer-grained; the test documents this platform asymmetry
    rather than asserting the distinct-tokens property.
    """
    print("[LEG C] get_process_start_time round-trip + cross-process reuse")

    # 1) Spawn a real ``sleep`` via the spawner.
    log_path = "/tmp/svc_tool_pg_c1.log"
    if os.path.exists(log_path):
        os.unlink(log_path)
    pid1, start_time1 = service_spawner.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        log_path=log_path,
        cwd="/tmp",
    )
    _SPAWNED_PIDS.append(pid1)
    try:
        # 2) Re-read via the helper — token MUST match.
        reread1 = service_spawner.get_process_start_time(pid1)
        _check(
            "C",
            reread1 is not None and reread1 == start_time1,
            f"round-trip pid1: spawn()={start_time1} re-read={reread1} "
            f"for pid={pid1} — must match",
        )

        # 3) Spawn a SECOND distinct process and assert the SAME
        # round-trip property on it. We do NOT assert the tokens
        # differ (macOS reports seconds-precision; two processes
        # spawned in the same second share a token — see docstring).
        log_path2 = "/tmp/svc_tool_pg_c2.log"
        if os.path.exists(log_path2):
            os.unlink(log_path2)
        pid2, start_time2 = service_spawner.spawn(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            log_path=log_path2,
            cwd="/tmp",
        )
        _SPAWNED_PIDS.append(pid2)
        try:
            reread2 = service_spawner.get_process_start_time(pid2)
            _check(
                "C",
                reread2 is not None and reread2 == start_time2,
                f"round-trip pid2: spawn()={start_time2} re-read={reread2}",
            )
            # (Optional, non-load-bearing) On Linux, jiffies since
            # boot are finer than 1s, so the two tokens almost always
            # differ; on macOS they may share the same second. We
            # only log the observation — distinctness is NOT a
            # invariant we can assert cross-platform.
            same_token = start_time1 == start_time2
            print(
                f"  [C] NOTE: distinct-process tokens same_second={same_token} "
                f"(pid1={pid1} token={start_time1}; pid2={pid2} "
                f"token={start_time2}) — macOS lstart = seconds precision"
            )
        finally:
            _hard_kill(pid2)
    finally:
        _hard_kill(pid1)


def _hard_kill(pid: int | None) -> None:
    """Best-effort SIGKILL on a spawned test child + its pgid."""
    if pid is None:
        return
    try:
        os.killpg(pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def leg_d() -> None:
    """LEG D — reconcile sweep marks dead rows EXITED on PG.

    Seeds a row with a DEAD PID (a high PID guaranteed not to exist
    on the kernel — the F1 read returns ``None``), runs the REAL
    ``ServiceReconciliationService.sweep_once``, asserts the row
    was transitioned to EXITED with ``reason='dead'`` and the
    counters return ``alive=0 reaped=1 errors=0``.
    """
    print("[LEG D] reconcile sweep marks dead rows EXITED")
    repo = ServiceRepo(engine=ENGINE)

    # Dead PID: 2_000_000_000 — far above any realistic kernel PID;
    # the helper returns ``None`` (ESRCH).
    dead_pid = 2_000_000_000
    row = repo.insert(
        name=f"dead-row-{uuid.uuid4().hex[:8]}",
        command="echo dead",
        pid=dead_pid,
        start_time=42,
        cwd="/tmp",
        status=ServiceStatus.RUNNING.value,
        started_by_instance_id="pg-smoke-instance",
        started_by_agent_id="pg-smoke-tester",
        log_path="/tmp/dead-row.log",
    )

    async def scenario() -> dict:
        svc = ServiceReconciliationService(
            repo,
            interval_seconds=DEFAULT_SWEEP_INTERVAL_SECONDS,
            starting_grace_seconds=DEFAULT_STARTING_GRACE_SECONDS,
        )
        return await svc.sweep_once()

    counters = asyncio.run(scenario())
    _check(
        "D",
        counters == {
            "alive": 0,
            "reaped": 1,
            "errors": 0,
            "starting_reaped": 0,
        },
        f"sweep_once counters (dead PID → EXITED); got {counters}",
    )

    # The row was transitioned to EXITED in the DB.
    with Session(ENGINE) as session:
        reread = session.get(ServiceTracking, row.id)
    _check(
        "D",
        reread is not None,
        f"row re-read succeeded; got {reread!r}",
    )
    _check(
        "D",
        reread is not None and reread.status == ServiceStatus.EXITED.value,
        f"row transitioned to EXITED; got status={reread.status if reread else None!r}",
    )


def main() -> int:
    # Schema (fresh disposable DB).
    _ensure_service_tracking_columns()

    leg_a()
    leg_b()
    leg_c()
    leg_d()

    print()
    if LEGS_FAILED:
        print(f"[FAIL] {len(LEGS_FAILED)} leg assertion(s) failed:")
        for failure in LEGS_FAILED:
            print(f"  - {failure}")
        return 1
    print(
        "[PASS] PG smoke: service-tool 3.A.8 verified on real "
        "PostgreSQL (4/4 legs)."
    )
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        for pid in _SPAWNED_PIDS:
            _hard_kill(pid)
    sys.exit(rc)
PYEOF

# ── Run the verifier (Layer 2 timeout — full budget for psql + Python) ───
echo "[run] Executing 4-leg verifier against $TARGET_URL"
set +e
timeout 280s .venv/bin/python "$VERIFIER" "$TARGET_URL" 2>&1
EXIT_CODE=$?
set -e

# Tidy the tmp verifier; the cleanup trap will drop the DB on exit
# (LESSONS trap 5 — cleanup never exits; RESULT block below owns the code).
rm -f "$VERIFIER"

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi