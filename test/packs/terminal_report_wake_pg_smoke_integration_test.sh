#!/usr/bin/env bash
# Test Pack: terminal_report_wake_pg_smoke_integration_test — real-PG
# confirmation that the PROCESS_REPORT wake-lane ranking in
# ``TaskRepository.claim_pending_task`` evaluates correctly on
# PostgreSQL (Debug Phase 4 fix #1, ee66f0eb).
#
# Why this is NOT just running the new integration files with
# ``-m postgres``: tests/integration/test_report_wake_priority_claim.py
# and tests/integration/test_wake_vs_claim_exactly_once.py both
# install their own ``engine`` fixture that creates file-backed
# SQLite at ``tmp_path`` (NullPool + WAL + busy_timeout=10000).
# Neither test carries the ``postgres`` marker, and the
# ``pytest_collection_modifyitems`` hook in
# ``tests/postgres/conftest.py`` only applies the marker to tests
# under ``tests/postgres/`` (line 92: ``"tests/postgres/" in
# str(item.fspath)``). So those files simply do not exercise PG —
# their SQL passes through SQLite, which masks PostgreSQL-specific
# translation of the new ``ORDER BY CASE WHEN task_type = ...``.
#
# This pack fills that gap with a direct repository-level check:
#
#   1. Create a DISPOSABLE database ``ensemble_test_wake_ee66f0eb``
#      on local PG (DROP IF EXISTS first, then CREATE).
#   2. Create the SQLModel schema on that disposable DB.
#   3. Seed N=4 process_message PENDING tasks with strictly older
#      created_at timestamps (simulates a saturated FIFO backlog).
#   4. Seed a single process_report PENDING task with the LATEST
#      created_at (newest).
#   5. Call ``TaskRepository.claim_pending_task`` once and assert
#      the claim returns the process_report task — NOT the oldest
#      process_message. This proves the two-tier ORDER BY
#      translates correctly under PostgreSQL's CASE-WHEN
#      boolean-integer promotion rules.
#   6. Repeat the claim and assert the next claim returns the OLDEST
#      remaining process_message (FIFO preserved within the
#      non-report tier after the wake lane drained).
#   7. DROP the disposable DB (cleanup trap).
#
# HARD GUARDS (do NOT remove):
#   * Target DB name must NOT contain ``ensemble_prod``; abort loud
#     if it does.
#   * Resolved connection URL must NOT contain ``ensemble_prod``;
#     abort loud if a misconfigured env var leaks production.
#   * Only ``DROP IF EXISTS`` + ``CREATE`` (the name is disposable).
#   * Only ``DROP DATABASE`` on the named DB — never ``\c ensemble_prod``.
#
# Connection: PG_TEST_* env vars (same convention as
# tests/postgres/conftest.py and test/packs/admittable_work_pg_test.sh).
# Defaults match docker-compose.test.yml (ensemble / ensemble_dev on
# localhost:5432, admin DB = ensemble_test).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 280s` on the pytest
#     process (covers psql + Python verifier + pytest on the helper).
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
#   5   ABORT (production-DB guard tripped)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: terminal_report_wake_pg_smoke_integration_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(PG smoke: PROCESS_REPORT wake-lane claim SQL on real PostgreSQL)"

cd "$PROJECT_DIR"

# ── PG connection defaults (overridable via env) ──────────────────────
PG_TEST_HOST="${PG_TEST_HOST:-localhost}"
PG_TEST_PORT="${PG_TEST_PORT:-5432}"
PG_TEST_ADMIN_DB="${PG_TEST_ADMIN_DB:-ensemble_test}"   # admin DB used only for CREATE/DROP DATABASE
PG_TEST_USER="${PG_TEST_USER:-ensemble}"
PG_TEST_PASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}"

export PG_TEST_HOST PG_TEST_PORT PG_TEST_ADMIN_DB PG_TEST_USER PG_TEST_PASSWORD

# ── HARD GUARD #1: disposable DB name must not be production ─────────
TARGET_DB="ensemble_test_wake_ee66f0eb"

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
# Note: cleanup must NOT 'exit' — it runs from the EXIT trap AFTER the
# script has already set its exit code via the RESULT block below.
# Calling 'exit' here would clobber the script's RESULT signal with the
# in-function local rc (which is the previous command's exit code, not
# the verifier's). The trap fires on every exit path (success, failure,
# timeout, error) so the DB is always dropped.
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

# ── Write a tiny verifier to a tmp file (keeps the heredoc readable) ─
VERIFIER="$(mktemp -t wake_pg_verify.XXXXXX.py)"
trap 'rm -f "$VERIFIER"; cleanup' EXIT INT TERM

cat >"$VERIFIER" <<PYEOF
"""Direct repository-level check: PROCESS_REPORT wake lane on real PostgreSQL.

Asserts that ``TaskRepository.claim_pending_task`` (Debug Phase 4 fix #1,
ee66f0eb) ranks a younger process_report PENDING task AHEAD of older
process_message PENDING tasks under the new two-tier ORDER BY
(``CASE WHEN task_type = PROCESS_REPORT THEN 0 ELSE 1 END, created_at ASC``).

Seeds a saturated FIFO backlog (4 process_message tasks, oldest first),
then a single process_report task (newest). The first claim MUST return
the process_report; the next claim MUST return the oldest process_message.

Real engine — no mocks. The CASE-WHEN rank expression translates
differently across dialects, and SQLite's loose typing can mask a
regression that only surfaces under PostgreSQL's strict boolean → int
promotion.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

# Register every SQLModel table before create_all (mirrors
# tests/postgres/conftest.py:53-64 — the daemon registers models
# lazily, so create_all on a fresh DB produces an empty schema
# without these imports).
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.db_connection.models  # noqa: F401
import daemon.repositories.mcp_server.models  # noqa: F401
import daemon.repositories.infra.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.source.models  # noqa: F401
import daemon.migrations.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository

ENGINE_URL = sys.argv[1]
# Force UTC on every connection — PG server is in Asia/Ho_Chi_Minh
# (UTC+7), and the Task.created_at column is TIMESTAMP WITHOUT TIME
# ZONE, so naive datetimes round-trip as local time. Without forcing
# UTC, a seeded (NOW - 30min) round-trips as +7 hours ahead, making
# the second-claim age check return a negative value.
ENGINE = create_engine(ENGINE_URL, future=True, connect_args={"options": "-c TimeZone=UTC"})

# ── 1. Schema ──────────────────────────────────────────────────────────
SQLModel.metadata.create_all(ENGINE)

# ── 2. Seed: 4 process_message PENDING tasks (oldest first) + 1 process_report (newest)
NOW = datetime.now(timezone.utc)
with ENGINE.begin() as conn:
    conn.execute(text("DELETE FROM task"))
    conn.execute(text("DELETE FROM instances"))

with Session(ENGINE) as s:
    for w in range(4):
        s.add(
            Instance(
                instance_id=f"inst-busy-{w}",
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                status=InstanceStatus.RUNNING.value,
            )
        )
    # Separate instance for the report task — the per-instance concurrency
    # gate filters out candidates whose instance has a RUNNING task. If we
    # shared inst-busy-0 with the 30-min-old process_message, that
    # message would be filtered out after the report claims, and the
    # second-claim FIFO assertion would target the 25-min survivor
    # instead of the 30-min-old one.
    s.add(
        Instance(
            instance_id="inst-report",
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            agent_name="developer",
            status=InstanceStatus.RUNNING.value,
        )
    )
    s.commit()

# 4 process_message tasks: 30, 25, 20, 15 minutes old — strict FIFO backlog.
# 1 process_report task: 1 minute old — newest; would lose every claim under
# strict created_at ASC FIFO.
with Session(ENGINE) as s:
    for w, minutes in enumerate((30.0, 25.0, 20.0, 15.0)):
        s.add(
            Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=f"inst-busy-{w}",
                status=TaskStatus.PENDING.value,
                created_at=NOW - timedelta(minutes=minutes),
            )
        )
    s.add(
        Task(
            task_type=TaskType.PROCESS_REPORT.value,
            instance_id="inst-report",
            status=TaskStatus.PENDING.value,
            created_at=NOW - timedelta(minutes=1.0),
        )
    )
    s.commit()

# ── 3. Claim via the real repository ──────────────────────────────────
# TaskRepository takes engine positionally. The repository applies every
# existing gate (defer / background / queue-awareness / per-instance /
# pause / cross-system) BEFORE the CASE ranking — but with only one
# parent instance seeded, per-instance is satisfied and the lane kicks in.
repo = TaskRepository(engine=ENGINE)

# Find the seeded report task id so we can assert it by identity.
with Session(ENGINE) as s:
    report_task = s.exec(
        text("SELECT id FROM task WHERE task_type = :tt AND status = :st ORDER BY id ASC LIMIT 1"),
        params={"tt": TaskType.PROCESS_REPORT.value, "st": TaskStatus.PENDING.value},
    ).first()
    if report_task is None:
        print("[FAIL] No process_report task found in DB", file=sys.stderr)
        sys.exit(1)
    report_task_id = int(report_task[0])

# Real repository call — the SQL under test.
claimed = repo.claim_pending_task(worker_id="pg-smoke-test")
if claimed is None:
    print("[FAIL] claim_pending_task returned None — no candidate visible", file=sys.stderr)
    sys.exit(1)

if int(claimed.id) != report_task_id:
    print(
        f"[FAIL] First claim returned task_type={claimed.task_type!r} "
        f"id={claimed.id} (expected the process_report id={report_task_id}). "
        "PROCESS_REPORT wake lane did NOT rank ahead of older FIFO backlog — "
        "the two-tier ORDER BY likely did not translate under PostgreSQL.",
        file=sys.stderr,
    )
    sys.exit(1)

print(
    f"[PASS] First claim returned process_report (id={claimed.id}, "
    f"created_at={claimed.created_at.isoformat()}) ahead of older FIFO backlog — "
    "wake lane confirmed under PostgreSQL."
)

# ── 4. Second claim — FIFO must hold within the non-report tier ─────
claimed2 = repo.claim_pending_task(worker_id="pg-smoke-test")
if claimed2 is None:
    print("[FAIL] Second claim returned None — FIFO backlog was supposed to remain", file=sys.stderr)
    sys.exit(1)
if claimed2.task_type != TaskType.PROCESS_MESSAGE.value:
    print(
        f"[FAIL] Second claim returned task_type={claimed2.task_type!r} "
        "(expected process_message — oldest FIFO survivor).",
        file=sys.stderr,
    )
    sys.exit(1)

# Verify it's the OLDEST remaining process_message (created 30 minutes ago).
expected_minutes_old = 30.0
created_at = claimed2.created_at
if created_at.tzinfo is None:
    # PostgreSQL TIMESTAMP WITHOUT TIME ZONE round-trips as naive; treat as UTC
    # to subtract from NOW (offset-aware).
    created_at = created_at.replace(tzinfo=timezone.utc)
delta = (NOW - created_at).total_seconds() / 60.0
if abs(delta - expected_minutes_old) > 0.5:
    print(
        f"[FAIL] Second claim returned process_message with age {delta:.1f} min "
        f"(expected ~{expected_minutes_old} min — oldest FIFO survivor).",
        file=sys.stderr,
    )
    sys.exit(1)

print(
    f"[PASS] Second claim returned oldest process_message "
    f"(id={claimed2.id}, age={delta:.1f} min) — FIFO preserved within the "
    "non-report tier after the wake lane drained."
)

print("[PASS] PG smoke: PROCESS_REPORT wake lane verified on real PostgreSQL.")
PYEOF

# ── Run the verifier (Layer 2 timeout — full budget for psql + Python) ───
echo "[run] Executing verifier against $TARGET_URL"
timeout 280s .venv/bin/python "$VERIFIER" "$TARGET_URL" 2>&1
EXIT_CODE=$?

# Tidy the tmp verifier; the cleanup trap will drop the DB on exit.
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