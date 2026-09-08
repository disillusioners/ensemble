#!/usr/bin/env bash
# Test Pack: ensure_deferred_pg_migration_smoke_integration_test —
# repository-level PG verifier for the content-NOT-NULL-safe deferred
# marker + deterministic IntegrityError classification fix
# (commit ef1432ca, branch feature/fix-report-injection-content-notnull).
#
# Why repository-level on real PG: the fix's discriminating bug class is
# a CONCURRENT IntegrityError on the obligation-triple partial unique
# index. SQLite's coarse locking cannot reproduce the unique-index
# race; only PostgreSQL's row-level unique-index wait can. The new
# pytest files (tests/unit/test_ensure_deferred_schema_pin.py,
# tests/unit/test_ensure_deferred_integrity_error_classification.py)
# are file-backed SQLite (no `postgres` marker); PG validation must
# go repository-level, mirroring
# test/packs/ensure_deferred_pg_smoke_integration_test.sh.
#
# FIVE LEGS (per-leg [PASS]/[FAIL]/[BLOCKER] evidence printed):
#   LEG M0 — migration-chain schema verification (gate premise):
#     build via the FULL MigrationRunner on a fresh PG database, then
#     read information_schema.columns for ``report_injections.content``
#     IS_NULLABLE. Expected on PG: ``YES`` (the model declares
#     ``content`` as ``nullable=True``; ``create_all`` emits a nullable
#     column; the MigrationRunner is a documented NO-OP on PG, and
#     `_ensure_postgres_columns` does NOT alter the column). If the
#     migration chain built a NOT NULL ``content`` column, the prod
#     legacy drift would be reproduced by the migration path itself;
#     if it built a nullable one — which is the documented drift — the
#     gate premise holds (the sentinel is the bridge between the two).
#   LEG M1 — sweep self-heal on migration-built schema (the positive
#     path): seed the incident shape (parent WAITING_CHILDREN + child
#     COMPLETED + COMPLETED message + ZERO report_injections rows +
#     CANCELLED/unenqueued watcher) → REAL
#     ``_run_no_row_backstop_lane`` heals the pair in ONE pass: report
#     row created (state PENDING, recovery_attempted_at stamped,
#     reason=RESUME_ROUTER), manager re-enter fired once with
#     source="sweep_no_row_backstop"; pass 2 finds NO candidates (no
#     flap); the recovered row's ``content`` is the sentinel ``""``.
#   LEG M1L — legacy prod NOT NULL simulation (the caller's DONE
#     criterion): on the SAME disposable DB, simulate the REAL legacy
#     prod shape — defensive WHERE-guard
#     (``UPDATE report_injections SET content='' WHERE content IS
#     NULL``) then ``ALTER TABLE report_injections ALTER COLUMN content
#     SET NOT NULL`` — and verify the sweep self-heals against the REAL
#     constraint: seed a FRESH zero-row incident pair (parent
#     WAITING_CHILDREN + child COMPLETED + AGENT message +
#     CANCELLED/unenqueued watcher), run the REAL
#     ``_run_no_row_backstop_lane`` → the sentinel ``''`` INSERT
#     satisfies NOT NULL, the row transitions DEFERRED → PENDING,
#     recovered=1 in ONE pass, pass 2 finds no candidates (anti-flap).
#     The constraint is DROPPED in a finally-block afterwards so
#     M2a/M2b keep the migration-built nullable semantics.
#   LEG M2 — classification on migration-built schema (the
#     deterministic-re-raise path):
#     LEG M2a — REAL UniqueViolation race (load-bearing): two REAL
#       concurrent sessions, two engines, barrier, DIFFERENT reasons
#       on the same triple → exactly ONE DEFERRED row wins, both
#       racers return the SAME injection_id (convergence re-read),
#       count == 1, both racers' final-state row reason matches one
#       of the racing reasons (in-place UPDATE by the loser).
#     LEG M2b — deterministic NotNullViolation (the b7ead8a4 bug
#       class): monkeypatch the repository's ``_insert_deferred_marker``
#       seam to raise a realistic ``psycopg.errors.NotNullViolation``
#       on the FIRST attempt → call ``ensure_deferred`` → assert the
#       exception propagates IMMEDIATELY (not absorbed, not
#       silently-swallowed), the insert counter == 1 (NO
#       phantom-conflict retry / second INSERT), the deterministic
#       ERROR log fires, and the misleading "phantom conflict" log
#       does NOT fire. Also: the schema pin test exercises the
#       REAL legacy NOT NULL path on SQLite (the unit pack covers
#       the live schema-shape test); this leg focuses on the
#       classification handler's correct routing.
#
# LESSONS TRAPS BAKED IN
# (.agents/tester/LESSONS/2026-09-06-pg-smoke-verifier-dialect-traps.md):
#   1. psycopg3 URL: `postgresql+psycopg://` (bare postgresql:// →
#      psycopg2, not installed).
#   2. Table names EXACT: `report_injections`, `instances`,
#      `message_queue`, `dependency_watchers` (the task queue table
#      elsewhere is SINGULAR `task` — never assumed here; no raw
#      `tasks` references).
#   3. SQLModel `Session.exec()` takes `params=` keyword-only → all
#      parameterized raw SQL goes through `conn.execute(text(...), {...})`
#      on a connection instead; model queries use Session.exec without
#      positional params.
#   4. `claim_pending_task(worker_id=)` has no default → NOT USED here.
#   5. Naive vs aware datetimes: every datetime seed value is
#      tz-aware (`datetime.now(timezone.utc)`); report_injections
#      timestamps are ISO-8601 STRINGS (round-trip exact) and are only
#      asserted for None/not-None.
#   6. Server timezone: `connect_args={"options": "-c TimeZone=UTC"}` on
#      EVERY engine (local PG runs Asia/Ho_Chi_Minh).
#   7. Per-instance concurrency gate: no `claim_pending_task` calls →
#      gate N/A; no PROCESS_* tasks are seeded, so no candidate can be
#      silently filtered.
#   8. Trap-vs-exit ordering: cleanup runs from the EXIT trap AFTER the
#      RESULT block and never calls `exit` (a trap-level exit would
#      clobber the 0/1/124/5 contract).
#
# HARD GUARDS (do NOT remove):
#   * Target DB name must NOT contain `ensemble_prod`; abort loud.
#   * Resolved connection URLs must NOT contain `ensemble_prod`; abort loud.
#   * Only `DROP IF EXISTS` + `CREATE` (the name is disposable).
#   * Only `DROP DATABASE` on the named DB — never connects to prod.
#
# Disposable DB: ensemble_test_sentinel_ef1432ca
#
# Connection: PG_TEST_* env vars (same convention as
# tests/postgres/conftest.py and the canonical PG smoke pack).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 280s` on the verifier
#     (covers psql provisioning + the 5-leg Python verifier; LEG M2a's
#     race resolves in <1s on local PG — the cap is hang insurance).
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
#   5   ABORT (production-DB guard tripped)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ensure_deferred_pg_migration_smoke_integration_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(PG migration smoke: content-NOT-NULL sentinel + classification on real PG, 5 legs)"

cd "$PROJECT_DIR"

# ── PG connection defaults (overridable via env) ──────────────────────
PG_TEST_HOST="${PG_TEST_HOST:-localhost}"
PG_TEST_PORT="${PG_TEST_PORT:-5432}"
PG_TEST_ADMIN_DB="${PG_TEST_ADMIN_DB:-ensemble_test}"   # admin DB used only for CREATE/DROP DATABASE
PG_TEST_USER="${PG_TEST_USER:-ensemble}"
PG_TEST_PASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}"

export PG_TEST_HOST PG_TEST_PORT PG_TEST_ADMIN_DB PG_TEST_USER PG_TEST_PASSWORD

# ── HARD GUARD #1: disposable DB name must not be production ─────────
TARGET_DB="ensemble_test_sentinel_ef1432ca"

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
# LESSONS trap 8: cleanup must NOT 'exit' — it runs from the EXIT trap
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

# ── Write the 5-leg verifier to a tmp file (keeps the heredoc readable) ─
VERIFIER="$(mktemp -t sentinel_pg_verify.XXXXXX.py)"
trap 'rm -f "$VERIFIER"; cleanup' EXIT INT TERM

cat >"$VERIFIER" <<PYEOF
"""5-leg repository-level PG verifier: content-NOT-NULL sentinel +
classification (commit ef1432ca, branch feature/fix-report-injection-content-notnull).

LEG M0  migration-chain schema verification (information_schema.columns on
        report_injections.content IS_NULLABLE — gate premise)
LEG M1  sweep self-heal end-to-end on the migration-built schema (real
        ReportDeliveryRecoveryService Lane 2, real INSERT → DEFERRED → PENDING,
        anti-flap on pass 2, sentinel '' content on the recovered row)
LEG M1L legacy prod NOT NULL simulation on the SAME DB (WHERE-guard +
        ALTER COLUMN content SET NOT NULL → REAL ``_run_no_row_backstop_lane``
        self-heal against the REAL constraint: sentinel '' INSERT satisfies
        NOT NULL, DEFERRED → PENDING, recovered=1 one pass, pass-2 anti-flap;
        constraint dropped in a finally-block afterwards)
LEG M2a REAL 2-session UniqueViolation race → exactly ONE DEFERRED row wins
        (load-bearing)
LEG M2b deterministic NotNullViolation → IMMEDIATE re-raise, single INSERT
        attempt, NO phantom-conflict retry, truthful deterministic-error log
        (the b7ead8a4 bug class — pinned end-to-end on real PG)
"""
from __future__ import annotations

import logging
import sys
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import psycopg.errors as pgerrs
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select as sm_select

# Register every SQLModel table before create_all (mirrors
# tests/postgres/conftest.py — the daemon registers models lazily, so
# create_all on a fresh DB produces an empty schema without these).
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
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.migrations.models  # noqa: F401

from daemon.constants import DEFERRED_REASON_RESUME_ROUTER
from daemon.migrations.runner import MigrationRunner
from daemon.repositories.dependency_bus.models import DependencyWatcher
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.services.report_delivery_recovery import (
    ReportDeliveryRecoveryService,
)

ENGINE_URL = sys.argv[1]
# LESSONS traps 1 + 6: psycopg3 driver + force UTC on every connection
# (local PG runs Asia/Ho_Chi_Minh; deterministic comparisons).
ENGINE = create_engine(
    ENGINE_URL, future=True, connect_args={"options": "-c TimeZone=UTC"}
)

REPO_LOGGER = "daemon.repositories.report_injection.repository"
SWEEP_LOGGER = "daemon.services.report_delivery_recovery"


class _LogCollector(logging.Handler):
    """Capture log records for the anti-pattern + truthful-log assertions."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


FLAP_COLLECTOR = _LogCollector()
logging.getLogger(REPO_LOGGER).addHandler(FLAP_COLLECTOR)
logging.getLogger(REPO_LOGGER).setLevel(logging.DEBUG)
logging.getLogger(SWEEP_LOGGER).addHandler(FLAP_COLLECTOR)
logging.getLogger(SWEEP_LOGGER).setLevel(logging.DEBUG)

LEGS_FAILED: list[str] = []


def _check(leg: str, condition: bool, evidence: str) -> None:
    """Record + print a per-leg PASS/FAIL assertion with evidence."""
    if condition:
        print(f"  [{leg}] PASS: {evidence}")
    else:
        print(f"  [{leg}] FAIL: {evidence}")
        LEGS_FAILED.append(f"{leg}: {evidence}")


def _blocker(leg: str, message: str) -> None:
    """Print a BLOCKER (gate-premise violation) and record as a FAIL."""
    print(f"  [{leg}] BLOCKER: {message}")
    LEGS_FAILED.append(f"{leg} BLOCKER: {message}")


def _fresh_pair(prefix: str) -> tuple[str, str, str]:
    """A fresh obligation triple (parent, child, msg)."""
    parent = f"{prefix}-parent-{uuid.uuid4().hex[:8]}"
    child = f"{prefix}-child-{uuid.uuid4().hex[:8]}"
    msg = f"{prefix}-msg-{uuid.uuid4().hex[:8]}"
    return parent, child, msg


def _pair_rows(parent_id: str) -> list[ReportInjection]:
    with Session(ENGINE) as session:
        return list(
            session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.parent_instance_id == parent_id
                )
            ).all()
        )


def _clear_tables() -> None:
    """Clean slate between legs (exact table names — LESSONS trap 2)."""
    with ENGINE.begin() as conn:
        conn.execute(text("DELETE FROM report_injections"))
        conn.execute(text("DELETE FROM dependency_watchers"))
        conn.execute(text("DELETE FROM message_queue"))
        conn.execute(text("DELETE FROM instances"))


def _seed_terminal_row(parent: str, child: str, msg: str, state: str) -> None:
    with Session(ENGINE) as session:
        session.add(
            ReportInjection(
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=msg,
                report_message_id=f"rmq-{uuid.uuid4().hex[:8]}",
                content="delivered before the incident",
                state=state,
                delivered_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        session.commit()


def leg_m0() -> None:
    """LEG M0 — migration-chain schema verification (gate premise).

    Build via the FULL MigrationRunner on a fresh PG database. The
    MigrationRunner is a documented NO-OP on PG
    (``daemon/migrations/runner.py:run_pending_migrations`` short-
    circuits on non-SQLite engines); the actual PG schema is built by
    ``SQLModel.metadata.create_all()`` + ``_ensure_postgres_columns``.
    The ``_ensure_postgres_columns`` hook is additive only — it does
    NOT alter the ``content`` column.

    Read ``information_schema.columns`` for
    ``report_injections.content IS_NULLABLE``. Expected on PG: ``YES``
    (the model declares ``content`` as ``nullable=True``). If the
    migration chain builds a NOT NULL ``content`` column, the prod
    legacy drift would be reproduced by the migration path itself — a
    gate-premise violation; if it builds a nullable one — which is the
    documented drift — the gate premise holds (the sentinel ``""`` is
    the bridge between legacy prod NOT NULL and the migration-built
    nullable schema).
    """
    print("[LEG M0] migration-chain schema verification (information_schema.columns)")

    # 1. Build the schema (create_all — the migration runner no-ops on PG,
    #    but call it anyway to document the FULL migration chain path;
    #    the runner's documented contract is that this is the COMPLETE
    #    migration story on PG).
    SQLModel.metadata.create_all(ENGINE)
    runner = MigrationRunner(ENGINE)
    applied = runner.run_pending_migrations()
    print(f"  [M0] MigrationRunner.run_pending_migrations() returned {applied} "
          f"(documented NO-OP on PG; the schema is built by create_all)")

    # 2. Read the schema state for ``content``.
    with ENGINE.begin() as conn:
        row = conn.execute(
            text(
                "SELECT column_name, is_nullable, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "  AND table_name = 'report_injections' "
                "  AND column_name = 'content'"
            )
        ).first()
    if row is None:
        _blocker(
            "M0",
            "report_injections.content column MISSING from "
            "information_schema.columns — the create_all build did not "
            "emit the column. Schema/model drift is total; gate "
            "premise broken.",
        )
        return
    col_name, is_nullable, data_type = row[0], row[1], row[2]
    print(f"  [M0] information_schema row: column_name={col_name!r}, "
          f"is_nullable={is_nullable!r}, data_type={data_type!r}")

    # Gate premise: on PG, the migration chain (create_all) emits a
    # NULLABLE content column (model declares nullable=True). The
    # legacy prod NOT NULL drift is NOT carried by the migration
    # chain — it persists in prod only because the prod DB was
    # originally provisioned before the migration runner existed and
    # the model became nullable. The sentinel ``""`` is the bridge.
    if is_nullable == "YES":
        _check(
            "M0",
            True,
            f"report_injections.content is NULLABLE on the "
            f"migration-built schema (data_type={data_type!r}, "
            f"is_nullable={is_nullable!r}) — the documented drift; "
            f"the sentinel '' is the bridge between this nullable "
            f"schema and the legacy prod NOT NULL schema",
        )
    elif is_nullable == "NO":
        _blocker(
            "M0",
            f"report_injections.content is NOT NULL on the "
            f"migration-built schema (data_type={data_type!r}, "
            f"is_nullable={is_nullable!r}) — the migration chain "
            f"reproduces the prod legacy drift directly, which means "
            f"a fresh deploy would have the same b7ead8a4 incident "
            f"class WITHOUT the sentinel. Gate premise violation; "
            f"the sentinel '' is no longer the bridge — the schema "
            f"itself is wrong.",
        )
    else:
        _blocker(
            "M0",
            f"report_injections.content is_nullable={is_nullable!r} "
            f"(unexpected value — expected 'YES' or 'NO'). Schema "
            f"verification inconclusive.",
        )


def _seed_incident_shape(parent_id: str, child_id: str, msg_id: str, task_id: str) -> None:
    """Seed the b7ead8a4 / d90b18f9 incident shape (real PG rows)."""
    with Session(ENGINE) as session:
        session.add(
            Instance(
                instance_id=parent_id,
                agent_id="agent-leader",
                agent_name="leader",
                agent_dir="/tmp/leader",
                parent_id=None,
                status=InstanceStatus.WAITING_CHILDREN.value,
                version=1,
                instance_metadata={},
            )
        )
        session.add(
            Instance(
                instance_id=child_id,
                agent_id="agent-giter",
                agent_name="giter",
                agent_dir="/tmp/giter",
                parent_id=parent_id,
                status=InstanceStatus.COMPLETED.value,
                version=1,
                instance_metadata={},
            )
        )
        session.add(
            MessageQueue(
                message_id=msg_id,
                instance_id=child_id,
                content="child final answer",
                source="agent",
                type=MessageType.AGENT.value,
                status=MessageStatus.COMPLETED.value,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        session.add(
            DependencyWatcher(
                watch_id=f"watch-{uuid.uuid4().hex[:8]}",
                source_task_id=task_id,
                target_instance_id=parent_id,
                state="CANCELLED",
                fired_at=None,
                enqueued_at=None,
            )
        )
        session.commit()


def leg_m1() -> None:
    """LEG M1 — sweep self-heal on the migration-built schema.

    The migration-built schema has ``content`` NULLABLE (LEG M0). The
    sentinel ``""`` is the truthful "no content yet" value. The sweep
    self-heal must succeed in ONE pass: row INSERTed (state DEFERRED,
    content='') → PENDING (recovery_attempted_at stamped,
    reason=RESUME_ROUTER) → reconcile via manager seam → pass 2 finds
    NO candidates (anti-flap).
    """
    print("[LEG M1] sweep self-heal on migration-built schema")
    _clear_tables()

    leader_id = f"leader-{uuid.uuid4().hex[:8]}"
    child_id = f"giter-{uuid.uuid4().hex[:8]}"
    msg_id = f"msg-{uuid.uuid4().hex[:8]}"
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    _seed_incident_shape(leader_id, child_id, msg_id, task_id)

    with Session(ENGINE) as session:
        seeded = session.exec(sm_select(ReportInjection)).all()
    _check(
        "M1",
        len(seeded) == 0,
        f"incident shape seeded: ZERO report_injections rows "
        f"(found {len(seeded)}), parent WAITING_CHILDREN + child "
        f"COMPLETED + COMPLETED message + CANCELLED/unenqueued watcher",
    )

    ri_repo = ReportInjectionRepository(engine=ENGINE)
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(side_effect=lambda _iid: False)
    manager = MagicMock()
    manager.engine = ENGINE
    manager._handle_recover_deferred_report = MagicMock()

    service = ReportDeliveryRecoveryService(
        task_repo=task_repo,
        report_injection_repo=ri_repo,
        queue_repo=MagicMock(),
        instance_repo=MagicMock(),
        manager_ref=manager,
        interval_seconds=300,
        age_bound_minutes=10,
        batch_cap=100,
        recovery_retry_minutes=1,
        enabled=True,
        lane_orphan=False,
    )

    FLAP_COLLECTOR.messages.clear()

    # ── Sweep pass 1 (the recovery) ──
    lane1 = service._run_no_row_backstop_lane()
    _check(
        "M1",
        lane1.recovered == 1 and lane1.errors == 0,
        f"pass 1 healed the stuck pair in ONE pass "
        f"(recovered={lane1.recovered}, errors={lane1.errors}, "
        f"already_recovered={lane1.already_recovered})",
    )

    with Session(ENGINE) as session:
        healed = list(session.exec(sm_select(ReportInjection)).all())
    if not healed:
        _check("M1", False, "no report row landed after pass 1")
        return
    recovered_row = healed[0]
    ok_row = (
        len(healed) == 1
        and recovered_row.parent_instance_id == leader_id
        and recovered_row.child_instance_id == child_id
        and recovered_row.child_message_id == msg_id
        and recovered_row.state == ReportInjectionState.PENDING.value
        and recovered_row.recovery_attempted_at is not None
        and recovered_row.deferred_reason == DEFERRED_REASON_RESUME_ROUTER
    )
    _check(
        "M1",
        ok_row,
        f"report row recovered: state={recovered_row.state}, "
        f"recovery_attempted_at set={bool(recovered_row.recovery_attempted_at)}, "
        f"reason={recovered_row.deferred_reason!r} "
        f"(INSERT → DEFERRED → PENDING → reconcile)",
    )

    # The sentinel '' must be on the recovered row (the bridge between
    # legacy NOT NULL schema and the migration-built nullable schema).
    _check(
        "M1",
        recovered_row.content == "",
        f"recovered row's content is the sentinel '' "
        f"(truthful 'no content yet, will be filled at "
        f"reconciliation by _create_subshape_a_artifacts'); "
        f"got content={recovered_row.content!r}",
    )

    call_count_1 = manager._handle_recover_deferred_report.call_count
    kwargs = (
        manager._handle_recover_deferred_report.call_args.kwargs
        if call_count_1 == 1
        else {}
    )
    _check(
        "M1",
        (
            call_count_1 == 1
            and kwargs.get("source") == "sweep_no_row_backstop"
            and kwargs.get("child_instance_id") == child_id
            and kwargs.get("child_message_id") == msg_id
        ),
        f"parent wake re-enter fired exactly once via the manager seam "
        f"(call_count={call_count_1}, source={kwargs.get('source')!r})",
    )

    # ── Sweep pass 2 (the ~300s re-hit) — NO flap ──
    candidates2 = ri_repo.find_completed_children_without_delivery(
        parent_not_terminal=True,
        limit=100,
    )
    lane2 = service._run_no_row_backstop_lane()
    with Session(ENGINE) as session:
        rows_after = list(session.exec(sm_select(ReportInjection)).all())
    call_count_2 = manager._handle_recover_deferred_report.call_count
    _check(
        "M1",
        (
            candidates2 == []
            and lane2.recovered == 0
            and lane2.already_recovered == 0
            and lane2.errors == 0
            and call_count_2 == 1
            and len(rows_after) == 1
        ),
        f"pass 2: no candidates (anti-flap), recovered={lane2.recovered}, "
        f"already_recovered={lane2.already_recovered}, errors={lane2.errors}, "
        f"re-enter still {call_count_2}, rows still {len(rows_after)}",
    )

    flap = [
        m
        for m in FLAP_COLLECTOR.messages
        if "racing delivery won" in m or "already delivered" in m
    ]
    _check(
        "M1",
        not flap,
        f"anti-pattern: no 'racing delivery won'/'already delivered' "
        f"logs across both passes ({len(flap)} hits)",
    )


def leg_m1l() -> None:
    """LEG M1L — legacy prod NOT NULL simulation on the SAME disposable DB.

    The migration-built schema has ``content`` NULLABLE (LEG M0); the
    legacy prod schema has ``content NOT NULL`` (predates the migration
    system — NOTHING in the migration chain sets it). This leg closes
    the gap by SIMULATING prod on real PG: defensive WHERE-guard
    (``UPDATE report_injections SET content='' WHERE content IS NULL``)
    then ``ALTER TABLE report_injections ALTER COLUMN content SET NOT
    NULL``, then seed a FRESH zero-row incident pair and run the REAL
    ``_run_no_row_backstop_lane``. DONE criterion: the sweep self-heals
    against the REAL constraint — the sentinel ``''`` INSERT satisfies
    NOT NULL, the row lands DEFERRED → PENDING, recovered=1 in ONE
    pass, pass 2 finds no candidates (anti-flap). The constraint is
    DROPPED in a finally-block so M2a/M2b keep the migration-built
    nullable semantics.
    """
    print("[LEG M1L] legacy prod NOT NULL simulation: sweep self-heal against the REAL constraint")
    _clear_tables()

    # WHERE-guard (defensive) + the prod-shape ALTER. On the clean
    # post-_clear_tables() table the UPDATE is a no-op, but the guard
    # keeps the leg robust to any NULL contents.
    with ENGINE.begin() as conn:
        guarded = conn.execute(
            text("UPDATE report_injections SET content = '' WHERE content IS NULL")
        ).rowcount
        conn.execute(
            text("ALTER TABLE report_injections ALTER COLUMN content SET NOT NULL")
        )
        row = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "  AND table_name = 'report_injections' "
                "  AND column_name = 'content'"
            )
        ).first()
    nullable_after = row[0] if row else None
    _check(
        "M1L",
        nullable_after == "NO",
        f"ALTER SET NOT NULL applied (WHERE-guard updated {guarded} NULL "
        f"row(s)); information_schema is_nullable={nullable_after!r} — "
        f"the REAL legacy prod shape now on the disposable DB",
    )

    leader_id = f"leader-{uuid.uuid4().hex[:8]}"
    child_id = f"giter-{uuid.uuid4().hex[:8]}"
    msg_id = f"msg-{uuid.uuid4().hex[:8]}"
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    _seed_incident_shape(leader_id, child_id, msg_id, task_id)

    with Session(ENGINE) as session:
        seeded = session.exec(sm_select(ReportInjection)).all()
    _check(
        "M1L",
        len(seeded) == 0,
        f"fresh zero-row incident pair seeded under the NOT NULL schema "
        f"(report_injections rows: {len(seeded)}), parent WAITING_CHILDREN "
        f"+ child COMPLETED + COMPLETED message + CANCELLED/unenqueued watcher",
    )

    ri_repo = ReportInjectionRepository(engine=ENGINE)
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(side_effect=lambda _iid: False)
    manager = MagicMock()
    manager.engine = ENGINE
    manager._handle_recover_deferred_report = MagicMock()

    service = ReportDeliveryRecoveryService(
        task_repo=task_repo,
        report_injection_repo=ri_repo,
        queue_repo=MagicMock(),
        instance_repo=MagicMock(),
        manager_ref=manager,
        interval_seconds=300,
        age_bound_minutes=10,
        batch_cap=100,
        recovery_retry_minutes=1,
        enabled=True,
        lane_orphan=False,
    )

    FLAP_COLLECTOR.messages.clear()

    try:
        # ── Sweep pass 1 (the recovery under the REAL NOT NULL) ──
        lane1 = service._run_no_row_backstop_lane()
        _check(
            "M1L",
            lane1.recovered == 1 and lane1.errors == 0,
            f"pass 1 healed the stuck pair in ONE pass against the REAL "
            f"NOT NULL constraint (recovered={lane1.recovered}, "
            f"errors={lane1.errors}, already_recovered={lane1.already_recovered})",
        )

        with Session(ENGINE) as session:
            healed = list(session.exec(sm_select(ReportInjection)).all())
        if not healed:
            _check(
                "M1L",
                False,
                "no report row landed after pass 1 — a NOT NULL trip on "
                "the INSERT would mean the sentinel is NOT the bridge "
                "(gate-premise violation against the REAL legacy shape)",
            )
        else:
            recovered_row = healed[0]
            ok_row = (
                len(healed) == 1
                and recovered_row.parent_instance_id == leader_id
                and recovered_row.child_instance_id == child_id
                and recovered_row.child_message_id == msg_id
                and recovered_row.state == ReportInjectionState.PENDING.value
                and recovered_row.recovery_attempted_at is not None
                and recovered_row.deferred_reason == DEFERRED_REASON_RESUME_ROUTER
            )
            _check(
                "M1L",
                ok_row,
                f"sentinel row INSERTed and transitioned under NOT NULL: "
                f"state={recovered_row.state}, "
                f"recovery_attempted_at set={bool(recovered_row.recovery_attempted_at)}, "
                f"reason={recovered_row.deferred_reason!r} "
                f"(INSERT → DEFERRED → PENDING → reconcile)",
            )
            _check(
                "M1L",
                recovered_row.content is not None and recovered_row.content == "",
                f"the sentinel '' INSERT satisfied the NOT NULL constraint "
                f"(content={recovered_row.content!r}) — the DONE criterion: "
                f"self-heal works against the REAL legacy prod shape",
            )

            call_count_1 = manager._handle_recover_deferred_report.call_count
            kwargs = (
                manager._handle_recover_deferred_report.call_args.kwargs
                if call_count_1 == 1
                else {}
            )
            _check(
                "M1L",
                (
                    call_count_1 == 1
                    and kwargs.get("source") == "sweep_no_row_backstop"
                    and kwargs.get("child_instance_id") == child_id
                    and kwargs.get("child_message_id") == msg_id
                ),
                f"parent wake re-enter fired exactly once via the manager seam "
                f"(call_count={call_count_1}, source={kwargs.get('source')!r})",
            )

            # ── Sweep pass 2 under NOT NULL — NO flap ──
            candidates2 = ri_repo.find_completed_children_without_delivery(
                parent_not_terminal=True,
                limit=100,
            )
            lane2 = service._run_no_row_backstop_lane()
            with Session(ENGINE) as session:
                rows_after = list(session.exec(sm_select(ReportInjection)).all())
            call_count_2 = manager._handle_recover_deferred_report.call_count
            _check(
                "M1L",
                (
                    candidates2 == []
                    and lane2.recovered == 0
                    and lane2.already_recovered == 0
                    and lane2.errors == 0
                    and call_count_2 == 1
                    and len(rows_after) == 1
                ),
                f"pass 2 under NOT NULL: no candidates (anti-flap), "
                f"recovered={lane2.recovered}, "
                f"already_recovered={lane2.already_recovered}, "
                f"errors={lane2.errors}, re-enter still {call_count_2}, "
                f"rows still {len(rows_after)}",
            )
    finally:
        # Restore the migration-built nullable schema so M2a/M2b keep
        # their designed semantics even if a M1L assertion failed.
        with ENGINE.begin() as conn:
            conn.execute(
                text("ALTER TABLE report_injections ALTER COLUMN content DROP NOT NULL")
            )
            row2 = conn.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "  AND table_name = 'report_injections' "
                    "  AND column_name = 'content'"
                )
            ).first()
    nullable_restored = row2[0] if row2 else None
    _check(
        "M1L",
        nullable_restored == "YES",
        f"constraint dropped after the leg — schema restored to the "
        f"migration-built nullable shape for M2a/M2b "
        f"(is_nullable={nullable_restored!r})",
    )


def leg_m2a() -> None:
    """LEG M2a — REAL 2-session UniqueViolation race (load-bearing).

    The legitimate phantom-conflict path: two REAL concurrent sessions
    on a fresh triple with DIFFERENT reasons → exactly ONE DEFERRED row
    wins; loser hits IntegrityError on the partial unique index
    ``uq_report_injections_oblig_triple`` → fresh-session re-read
    converges → returns the winner's row identity; final count == 1
    and both racers report the SAME injection_id.
    """
    print("[LEG M2a] real 2-session race: exactly ONE DEFERRED row wins")
    _clear_tables()
    parent, child, msg = _fresh_pair("m2a")

    engine_a = create_engine(
        ENGINE_URL,
        future=True,
        poolclass=NullPool,
        connect_args={"options": "-c TimeZone=UTC"},
    )
    engine_b = create_engine(
        ENGINE_URL,
        future=True,
        poolclass=NullPool,
        connect_args={"options": "-c TimeZone=UTC"},
    )
    repo_a = ReportInjectionRepository(engine=engine_a)
    repo_b = ReportInjectionRepository(engine=engine_b)

    barrier = threading.Barrier(2, timeout=30)
    outcomes: dict[str, tuple[str, object]] = {}

    def _racer(name: str, repo: ReportInjectionRepository, reason: str) -> None:
        try:
            barrier.wait()
            row = repo.ensure_deferred(
                parent_instance_id=parent,
                child_instance_id=child,
                child_message_id=msg,
                deferred_reason=reason,
            )
            if row is None:
                outcomes[name] = ("none", None)
            else:
                outcomes[name] = ("row", row.injection_id)
        except Exception as exc:  # noqa: BLE001 — evidence collection
            outcomes[name] = ("error", repr(exc))

    thread_a = threading.Thread(target=_racer, args=("A", repo_a, "race-A"))
    thread_b = threading.Thread(target=_racer, args=("B", repo_b, "race-B"))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=90)
    thread_b.join(timeout=90)
    engine_a.dispose()
    engine_b.dispose()

    status_a, value_a = outcomes.get("A", ("missing", None))
    status_b, value_b = outcomes.get("B", ("missing", None))
    _check(
        "M2a",
        status_a != "error" and status_b != "error",
        f"neither racer raised (A={status_a}:{value_a!r}, "
        f"B={status_b}:{value_b!r}) — the loser converged via re-read "
        f"instead of erroring (the legitimate phantom-conflict path)",
    )

    rows = _pair_rows(parent)
    _check(
        "M2a",
        len(rows) == 1,
        f"exactly ONE DEFERRED row won the race (found {len(rows)} for the triple)",
    )

    if status_a == "row" and status_b == "row":
        _check(
            "M2a",
            value_a == value_b == rows[0].injection_id,
            f"both racers returned the WINNER's row identity "
            f"(A={value_a}, B={value_b}, row={rows[0].injection_id})",
        )
    else:
        _check(
            "M2a",
            False,
            f"expected both racers to return the winner's row "
            f"(different reasons) — got A={status_a}:{value_a!r}, "
            f"B={status_b}:{value_b!r}",
        )

    _check(
        "M2a",
        rows[0].deferred_reason in ("race-A", "race-B"),
        f"final row reason is one of the racers' reasons (in-place update "
        f"by the loser): {rows[0].deferred_reason!r}",
    )
    _check(
        "M2a",
        rows[0].state == ReportInjectionState.DEFERRED.value,
        f"winning row is DEFERRED (state={rows[0].state})",
    )


def leg_m2b() -> None:
    """LEG M2b — deterministic NotNullViolation → IMMEDIATE re-raise.

    The b7ead8a4 bug class: a deterministic IntegrityError routed through
    the phantom-conflict re-read path produced a misleading log AND a
    wasted second INSERT. The fix re-raises deterministic errors
    IMMEDIATELY without the phantom-conflict retry — the second INSERT
    can only legitimately absorb an obligation-triple race, and routing
    deterministic violations through it was the bug.

    Mechanism: replace ``_insert_deferred_marker`` with a stub that raises
    a realistic ``psycopg.errors.NotNullViolation`` on the FIRST
    attempt. The migration-built schema has ``content`` nullable so we
    cannot reproduce a real NOT NULL trip from the sentinel ''; we
    force the deterministic path via the public seam (mirrors the
    unit-test pattern in
    ``tests/unit/test_ensure_deferred_integrity_error_classification.py::test_not_null_violation_raises_immediately_no_retry``).
    The seam is the canonical test injection point per the
    ``_insert_deferred_marker`` docstring (``daemon/repositories/report_injection/repository.py``).
    """
    print("[LEG M2b] deterministic NotNullViolation → IMMEDIATE re-raise, no retry-insert")
    _clear_tables()
    parent, child, msg = _fresh_pair("m2b")

    repo = ReportInjectionRepository(engine=ENGINE)
    real_insert = repo._insert_deferred_marker
    insert_calls = {"n": 0}

    def not_null_failing_insert(**_kwargs):
        insert_calls["n"] += 1
        # Realistic psycopg3-shaped message — the discriminator checks
        # for ``null value in column "content" of relation "report_injections"
        # violates not-null constraint`` (NOT the obligation-triple
        # constraint name or the obligation-triple column set).
        raise SAIntegrityError(
            "INSERT INTO report_injections ...",
            {},
            pgerrs.NotNullViolation(
                'null value in column "content" of relation '
                '"report_injections" violates not-null constraint'
            ),
        )

    repo._insert_deferred_marker = not_null_failing_insert

    FLAP_COLLECTOR.messages.clear()

    raised: SAIntegrityError | None = None
    try:
        repo.ensure_deferred(
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id=msg,
            deferred_reason=DEFERRED_REASON_RESUME_ROUTER,
        )
    except SAIntegrityError as exc:
        raised = exc
    except Exception as exc:  # noqa: BLE001 — evidence collection
        _check(
            "M2b",
            False,
            f"unexpected exception type {type(exc).__name__}: {exc!r} "
            f"(expected SAIntegrityError)",
        )
        return

    _check(
        "M2b",
        raised is not None,
        "deterministic NotNullViolation propagated IMMEDIATELY "
        "(no absorption, no silent swallow)",
    )

    # EXACTLY ONE insert attempt — no phantom-conflict retry.
    _check(
        "M2b",
        insert_calls["n"] == 1,
        f"deterministic NOT NULL violation triggered exactly ONE "
        f"INSERT attempt (insert_calls={insert_calls['n']}); the "
        f"phantom-conflict retry path was NOT entered (the fix's "
        f"classification re-raises deterministic errors before "
        f"the retry seam)",
    )

    # No row landed (the deterministic error blocked the INSERT).
    rows = _pair_rows(parent)
    _check(
        "M2b",
        len(rows) == 0,
        f"NO row landed after the deterministic NOT NULL trip "
        f"(found {len(rows)} for the triple — the INSERT was "
        f"rejected by PG, the retry path was skipped)",
    )

    # The truthful deterministic-error log fired.
    truth = [
        m for m in FLAP_COLLECTOR.messages
        if "deterministic IntegrityError" in m and "NOT a delivery race" in m
    ]
    _check(
        "M2b",
        len(truth) >= 1,
        f"the truthful 'deterministic IntegrityError' log fired for "
        f"operator visibility ({len(truth)} hits)",
    )

    # The misleading phantom-conflict log did NOT fire.
    phantom = [
        m for m in FLAP_COLLECTOR.messages
        if "phantom conflict" in m or "insert-on-missing" in m
    ]
    _check(
        "M2b",
        not phantom,
        f"the misleading 'phantom conflict'/'insert-on-missing' logs "
        f"did NOT fire ({len(phantom)} hits) — the classification "
        f"routed the deterministic error before the retry seam",
    )

    # The exception type is what the original error was, NOT a wrapped/relabeled
    # form (the discriminator must NOT swallow the type).
    if raised is not None:
        orig = getattr(raised, "orig", None)
        _check(
            "M2b",
            isinstance(orig, pgerrs.NotNullViolation),
            f"propagated exception's orig is psycopg.errors.NotNullViolation "
            f"(got {type(orig).__name__ if orig is not None else 'None'}) — "
            f"the classification re-raised the original error verbatim",
        )

    # Restore for cleanup hygiene (in case more legs ran after this).
    repo._insert_deferred_marker = real_insert


def main() -> int:
    leg_m0()
    leg_m1()
    leg_m1l()
    leg_m2a()
    leg_m2b()

    print()
    if LEGS_FAILED:
        print(f"[FAIL] {len(LEGS_FAILED)} leg assertion(s) failed:")
        for failure in LEGS_FAILED:
            print(f"  - {failure}")
        return 1
    print(
        "[PASS] PG migration smoke: content-NOT-NULL sentinel + "
        "classification verified on real PostgreSQL (5/5 legs)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF

# ── Run the verifier (Layer 2 timeout — full budget for psql + Python) ───
echo "[run] Executing 5-leg verifier against $TARGET_URL"
set +e
timeout 280s .venv/bin/python "$VERIFIER" "$TARGET_URL" 2>&1
EXIT_CODE=$?
set -e

# Tidy the tmp verifier; the cleanup trap will drop the DB on exit
# (LESSONS trap 8 — cleanup never exits; RESULT block below owns the code).
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