#!/usr/bin/env bash
# Test Pack: ensure_deferred_pg_smoke_integration_test — repository-level
# PG verifier for the ensure_deferred INSERT-ON-MISSING + self-heal fix
# (Debug Phase 4, commit e9aac370, branch
# feature/fix-report-delivery-ensure-deferred).
#
# Why repository-level on real PG: the fix's discriminating bug class is
# a CONCURRENT IntegrityError → zero-rows false-positive no-op. SQLite's
# coarse locking cannot reproduce the unique-index race; only PostgreSQL's
# row-level unique-index wait can. The new pytest files
# (tests/unit/test_ensure_deferred_insert_on_missing.py,
#  tests/integration/test_pause_resume_watcher_rearm.py,
#  tests/unit/test_report_delivery_self_heal_zero_row.py)
# install their own file-backed SQLite engines (no `postgres` marker), so
# `-m postgres` cannot select them — PG validation must go
# repository-level, exactly like the canonical
# test/packs/terminal_report_wake_pg_smoke_integration_test.sh.
#
# FOUR LEGS (per-leg [PASS]/[FAIL] evidence printed):
#   LEG A — partial unique index + INSERT gating:
#     schema via create_all; uq_report_injections_oblig_triple EXISTS in
#     pg_indexes (UNIQUE + PENDING/DEFERRED predicate); ensure_deferred
#     on a non-terminal pair twice → converges (count stays 1); reason
#     change → guarded in-place UPDATE (count stays 1); terminal-row
#     pair → terminal pre-check path (no new non-terminal row).
#   LEG B — REAL 2-session race (LOAD-BEARING): two REAL concurrent
#     sessions (two engines, two threads, barrier) call ensure_deferred
#     on the same fresh pair simultaneously with DIFFERENT reasons →
#     exactly ONE DEFERRED row wins; loser hits IntegrityError on the
#     partial unique index → fresh-session re-read converges → returns
#     the winner's row identity (never raises); final count == 1 and
#     both racers report the SAME injection_id.
#   LEG C — terminal pre-check coexistence: seeded terminal row (all
#     three states: INJECTED / TASK_DELIVERED / FAILED) + ensure_deferred
#     → positive-evidence no-op (returns None, no new non-terminal row,
#     state unchanged); a DIFFERENT fresh pair Y → INSERT happens.
#   LEG D — sweep self-heal end-to-end: the incident shape seeded on PG
#     (parent WAITING_CHILDREN + child COMPLETED + COMPLETED message +
#     ZERO report_injections rows + CANCELLED/unenqueued
#     dependency_watchers row) → REAL ReportDeliveryRecoveryService
#     Lane 2 (`_run_no_row_backstop_lane`) heals the pair in ONE pass:
#     report row created (state PENDING, recovery_attempted_at stamped,
#     reason=RESUME_ROUTER), manager re-enter fired once with
#     source="sweep_no_row_backstop"; pass 2 finds NO candidates (no
#     flap); the "racing delivery won"/"already delivered" logs NEVER
#     fire. (Watcher re-arm itself is the resume cascade's contract —
#     pinned by test/packs/watcher_rearm_integration_test.sh; LEG D
#     seeds the CANCELLED watcher for incident-shape fidelity and
#     asserts the lane leaves the healing contract intact.)
#
# LESSONS TRAPS BAKED IN
# (.agents/tester/LESSONS/2026-09-06-pg-smoke-verifier-dialect-traps.md):
#   1. psycopg3 URL: `postgresql+psycopg://` (bare postgresql:// →
#      psycopg2, not installed).
#   2. Table names EXACT: `report_injections`, `instances`,
#      `message_queue`, `dependency_watchers` (note: the task queue
#      table elsewhere is SINGULAR `task` — never assumed here; no raw
#      `tasks` references).
#   3. SQLModel `Session.exec()` takes `params=` keyword-only → all
#      parameterized raw SQL goes through `conn.execute(text(...), {...})`
#      on a connection instead; model queries use Session.exec without
#      positional params.
#   4. `claim_pending_task(worker_id=)` has no default → NOT USED here
#      (no task claiming in this verifier; nothing to trip).
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
#      clobber the 0/1/124 contract).
#
# HARD GUARDS (do NOT remove):
#   * Target DB name must NOT contain `ensemble_prod`; abort loud.
#   * Resolved connection URLs must NOT contain `ensemble_prod`; abort loud.
#   * Only `DROP IF EXISTS` + `CREATE` (the name is disposable).
#   * Only `DROP DATABASE` on the named DB — never connects to prod.
#
# Disposable DB: ensemble_test_ensdefer_e9aac370
#
# Connection: PG_TEST_* env vars (same convention as
# tests/postgres/conftest.py and the canonical PG smoke pack).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 280s` on the verifier
#     (covers psql provisioning + the 4-leg Python verifier; LEG B's
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

echo "=== Test Pack: ensure_deferred_pg_smoke_integration_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(PG smoke: ensure_deferred insert-on-missing — 4 legs incl. real 2-session race)"

cd "$PROJECT_DIR"

# ── PG connection defaults (overridable via env) ──────────────────────
PG_TEST_HOST="${PG_TEST_HOST:-localhost}"
PG_TEST_PORT="${PG_TEST_PORT:-5432}"
PG_TEST_ADMIN_DB="${PG_TEST_ADMIN_DB:-ensemble_test}"   # admin DB used only for CREATE/DROP DATABASE
PG_TEST_USER="${PG_TEST_USER:-ensemble}"
PG_TEST_PASSWORD="${PG_TEST_PASSWORD:-ensemble_dev}"

export PG_TEST_HOST PG_TEST_PORT PG_TEST_ADMIN_DB PG_TEST_USER PG_TEST_PASSWORD

# ── HARD GUARD #1: disposable DB name must not be production ─────────
TARGET_DB="ensemble_test_ensdefer_e9aac370"

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

# ── Write the 4-leg verifier to a tmp file (keeps the heredoc readable) ─
VERIFIER="$(mktemp -t ensdefer_pg_verify.XXXXXX.py)"
trap 'rm -f "$VERIFIER"; cleanup' EXIT INT TERM

cat >"$VERIFIER" <<PYEOF
"""4-leg repository-level PG verifier: ensure_deferred INSERT-ON-MISSING.

LEG A  partial unique index + INSERT gating
LEG B  REAL 2-session race → exactly one DEFERRED row (load-bearing)
LEG C  terminal pre-check coexistence (INJECTED/TASK_DELIVERED/FAILED)
LEG D  sweep self-heal end-to-end (incident shape on real PG)

All legs run REAL repository code against REAL PostgreSQL. Only the
sweep's manager seam is mocked in LEG D (mirrors the fix's own unit
tests — the DB path, repository, and lane logic are all real).
"""
from __future__ import annotations

import logging
import sys
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text
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
    """Capture log records for the anti-flap assertion (LEG D)."""

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


def leg_a() -> None:
    """LEG A — partial unique index + INSERT gating."""
    print("[LEG A] partial unique index + INSERT gating")
    repo = ReportInjectionRepository(engine=ENGINE)

    # 1. The partial unique index EXISTS with the right predicate.
    with ENGINE.begin() as conn:
        row = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_report_injections_oblig_triple'"
            )
        ).first()
    indexdef = row[0] if row else ""
    _check(
        "A",
        bool(indexdef),
        f"uq_report_injections_oblig_triple found in pg_indexes: {indexdef}",
    )
    _check(
        "A",
        (
            "UNIQUE" in indexdef.upper()
            and "PENDING" in indexdef.upper()
            and "DEFERRED" in indexdef.upper()
            and "parent_instance_id" in indexdef
            and "child_instance_id" in indexdef
            and "child_message_id" in indexdef
        ),
        "index is UNIQUE on the obligation triple with the "
        "WHERE state IN ('PENDING','DEFERRED') predicate",
    )

    # 2. Insert-on-missing on a zero-row pair → DEFERRED row.
    parent, child, msg = _fresh_pair("lega")
    first = repo.ensure_deferred(
        parent_instance_id=parent,
        child_instance_id=child,
        child_message_id=msg,
        deferred_reason="leg-a-first",
    )
    _check(
        "A",
        first is not None
        and first.state == ReportInjectionState.DEFERRED.value,
        f"first ensure_deferred INSERTed a DEFERRED row "
        f"(injection_id={getattr(first, 'injection_id', None)})",
    )

    # 3. Second call, same reason → converges to the SAME single row.
    second = repo.ensure_deferred(
        parent_instance_id=parent,
        child_instance_id=child,
        child_message_id=msg,
        deferred_reason="leg-a-first",
    )
    rows = _pair_rows(parent)
    _check(
        "A",
        len(rows) == 1,
        f"second call converged — exactly 1 row for the triple "
        f"(found {len(rows)}), no duplicate",
    )
    _check(
        "A",
        second is None or second.injection_id == rows[0].injection_id,
        "second call absorbed as duplicate (returned None or the same row), "
        f"got {second!r}",
    )

    # 4. Reason change → guarded in-place UPDATE, still one row.
    updated = repo.ensure_deferred(
        parent_instance_id=parent,
        child_instance_id=child,
        child_message_id=msg,
        deferred_reason="leg-a-changed",
    )
    rows = _pair_rows(parent)
    _check(
        "A",
        len(rows) == 1
        and rows[0].deferred_reason == "leg-a-changed"
        and updated is not None
        and updated.injection_id == rows[0].injection_id,
        f"reason change updated in place (reason={rows[0].deferred_reason!r}), "
        f"still 1 row",
    )

    # 5. Terminal-row pair → terminal pre-check path, no new
    #    non-terminal row (the index alone cannot detect this —
    #    terminal rows are OUTSIDE the predicate).
    tparent, tchild, tmsg = _fresh_pair("lega-term")
    _seed_terminal_row(tparent, tchild, tmsg, ReportInjectionState.INJECTED.value)
    noop = repo.ensure_deferred(
        parent_instance_id=tparent,
        child_instance_id=tchild,
        child_message_id=tmsg,
        deferred_reason="leg-a-terminal",
    )
    trows = _pair_rows(tparent)
    _check(
        "A",
        (
            noop is None
            and len(trows) == 1
            and trows[0].state == ReportInjectionState.INJECTED.value
        ),
        f"terminal INJECTED row → positive-evidence no-op "
        f"(returned {noop!r}), no new non-terminal row "
        f"({len(trows)} row, state={trows[0].state})",
    )


def leg_b() -> None:
    """LEG B — REAL 2-session race (the load-bearing leg)."""
    print("[LEG B] real 2-session race: exactly ONE DEFERRED row wins")
    parent, child, msg = _fresh_pair("legb")

    # Two REAL engines → two real connections/pools (NullPool: no
    # reuse) → genuinely concurrent sessions. LESSONS trap 6 applies.
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
            # DIFFERENT reasons per racer: the loser's post-rollback
            # re-read finds the winner's row with a differing reason →
            # guarded in-place UPDATE → returns the winner's row. This
            # makes the convergence assertion IDENTITY-explicit (same
            # injection_id from both racers) instead of a weak None.
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
        "B",
        status_a != "error" and status_b != "error",
        f"neither racer raised (A={status_a}:{value_a!r}, B={status_b}:{value_b!r}) "
        "— the loser converged via re-read instead of erroring",
    )

    rows = _pair_rows(parent)
    _check(
        "B",
        len(rows) == 1,
        f"exactly ONE DEFERRED row won the race (found {len(rows)} for the triple)",
    )

    if status_a == "row" and status_b == "row":
        _check(
            "B",
            value_a == value_b == rows[0].injection_id,
            f"both racers returned the WINNER's row identity "
            f"(A={value_a}, B={value_b}, row={rows[0].injection_id})",
        )
    else:
        # Same-reason duplicates legitimately return None; with
        # different reasons both MUST return the row. Flag anything else.
        _check(
            "B",
            False,
            f"expected both racers to return the winner's row "
            f"(different reasons) — got A={status_a}:{value_a!r}, "
            f"B={status_b}:{value_b!r}",
        )

    _check(
        "B",
        rows[0].deferred_reason in ("race-A", "race-B"),
        f"final row reason is one of the racers' reasons (in-place update "
        f"by the loser): {rows[0].deferred_reason!r}",
    )
    _check(
        "B",
        rows[0].state == ReportInjectionState.DEFERRED.value,
        f"winning row is DEFERRED (state={rows[0].state})",
    )


def leg_c() -> None:
    """LEG C — terminal pre-check coexistence."""
    print("[LEG C] terminal pre-check coexistence (all 3 terminal states)")
    repo = ReportInjectionRepository(engine=ENGINE)

    for terminal_state in (
        ReportInjectionState.INJECTED.value,
        ReportInjectionState.TASK_DELIVERED.value,
        ReportInjectionState.FAILED.value,
    ):
        parent, child, msg = _fresh_pair("legc")
        _seed_terminal_row(parent, child, msg, terminal_state)
        result = repo.ensure_deferred(
            parent_instance_id=parent,
            child_instance_id=child,
            child_message_id=msg,
            deferred_reason=f"leg-c-{terminal_state}",
        )
        rows = _pair_rows(parent)
        _check(
            "C",
            (
                result is None
                and len(rows) == 1
                and rows[0].state == terminal_state
            ),
            f"terminal {terminal_state}: positive-evidence no-op "
            f"(returned {result!r}), {len(rows)} row, state unchanged "
            f"{rows[0].state}",
        )

    # A DIFFERENT fresh pair Y (zero rows) → INSERT happens.
    yparent, ychild, ymsg = _fresh_pair("legc-y")
    yrow = repo.ensure_deferred(
        parent_instance_id=yparent,
        child_instance_id=ychild,
        child_message_id=ymsg,
        deferred_reason="leg-c-fresh",
    )
    yrows = _pair_rows(yparent)
    _check(
        "C",
        (
            yrow is not None
            and len(yrows) == 1
            and yrows[0].state == ReportInjectionState.DEFERRED.value
        ),
        f"fresh pair Y → INSERT happened (state="
        f"{yrows[0].state if yrows else 'NO ROW'}), "
        f"returned {yrow is not None}",
    )


def leg_d() -> None:
    """LEG D — sweep self-heal end-to-end (incident shape on real PG)."""
    print("[LEG D] sweep self-heal: zero-row stuck pair heals in ONE pass")
    _clear_tables()

    # Incident shape (leader b7ead8a4 / giter d90b18f9):
    leader_id = f"leader-{uuid.uuid4().hex[:8]}"
    child_id = f"giter-{uuid.uuid4().hex[:8]}"
    msg_id = f"msg-{uuid.uuid4().hex[:8]}"
    task_id = f"task-{uuid.uuid4().hex[:8]}"

    with Session(ENGINE) as session:
        session.add(
            Instance(
                instance_id=leader_id,
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
                parent_id=leader_id,
                status=InstanceStatus.COMPLETED.value,
                version=1,
                instance_metadata={},
            )
        )
        # The child's COMPLETED response message (Lane-2 join key).
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
        # CANCELLED + unenqueued watcher: pause-cascade fallout — the
        # resume cascade's re-arm (Fix 2) owns this row; the sweep lane
        # (Fix 3) owns the zero-row obligation. Both seeded for
        # incident-shape fidelity.
        session.add(
            DependencyWatcher(
                watch_id=f"watch-{uuid.uuid4().hex[:8]}",
                source_task_id=task_id,
                target_instance_id=leader_id,
                state="CANCELLED",
                fired_at=None,
                enqueued_at=None,
            )
        )
        session.commit()

    with Session(ENGINE) as session:
        seeded = session.exec(sm_select(ReportInjection)).all()
    _check(
        "D",
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
        "D",
        lane1.recovered == 1 and lane1.errors == 0,
        f"pass 1 healed the stuck pair in ONE pass "
        f"(recovered={lane1.recovered}, errors={lane1.errors}, "
        f"already_recovered={lane1.already_recovered})",
    )

    with Session(ENGINE) as session:
        healed = list(session.exec(sm_select(ReportInjection)).all())
    ok_row = (
        len(healed) == 1
        and healed[0].parent_instance_id == leader_id
        and healed[0].child_instance_id == child_id
        and healed[0].child_message_id == msg_id
        and healed[0].state == ReportInjectionState.PENDING.value
        and healed[0].recovery_attempted_at is not None
        and healed[0].deferred_reason == DEFERRED_REASON_RESUME_ROUTER
    )
    _check(
        "D",
        ok_row,
        f"report row created + recovered: state="
        f"{healed[0].state if healed else 'NO ROW'}, "
        f"recovery_attempted_at set="
        f"{bool(healed and healed[0].recovery_attempted_at)}, "
        f"reason={healed[0].deferred_reason if healed else None!r} "
        f"(INSERT → DEFERRED → PENDING → reconcile)",
    )

    call_count_1 = manager._handle_recover_deferred_report.call_count
    kwargs = (
        manager._handle_recover_deferred_report.call_args.kwargs
        if call_count_1 == 1
        else {}
    )
    _check(
        "D",
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
        "D",
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

    watcher_state = None
    with Session(ENGINE) as session:
        w = session.exec(
            sm_select(DependencyWatcher).where(
                DependencyWatcher.target_instance_id == leader_id
            )
        ).first()
        if w is not None:
            watcher_state = w.state
    _check(
        "D",
        watcher_state == "CANCELLED",
        f"CANCELLED watcher untouched by the sweep lane "
        f"(state={watcher_state!r}; re-arm is the resume cascade's "
        f"contract — covered by watcher_rearm_integration_test.sh)",
    )

    flap = [
        m
        for m in FLAP_COLLECTOR.messages
        if "racing delivery won" in m or "already delivered" in m
    ]
    _check(
        "D",
        not flap,
        f"anti-pattern: no 'racing delivery won'/'already delivered' "
        f"logs across both passes ({len(flap)} hits)",
    )


def main() -> int:
    # ── Schema (fresh disposable DB) ──────────────────────────────────
    SQLModel.metadata.create_all(ENGINE)

    _clear_tables()
    leg_a()
    _clear_tables()
    leg_b()
    _clear_tables()
    leg_c()
    _clear_tables()
    leg_d()

    print()
    if LEGS_FAILED:
        print(f"[FAIL] {len(LEGS_FAILED)} leg assertion(s) failed:")
        for failure in LEGS_FAILED:
            print(f"  - {failure}")
        return 1
    print(
        "[PASS] PG smoke: ensure_deferred INSERT-ON-MISSING verified on "
        "real PostgreSQL (4/4 legs)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF

# ── Run the verifier (Layer 2 timeout — full budget for psql + Python) ───
echo "[run] Executing 4-leg verifier against $TARGET_URL"
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
