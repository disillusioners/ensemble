"""Pack H — candidate query shape (static/exact-string) + S4
NULL-instance + S5 ORDER BY (wi-78967dba).

Pins (phase3-plan T3.8): exact-string source pins over the candidate
SQL (task-kind filter, active filter, ``deleted_at IS NULL``,
work_id JOIN, instance NOT EXISTS, ``MAX(completed_at)`` age key —
NOT ``last_activity_at``, LIMIT, deterministic ORDER BY
oldest-terminal-first with job_id tiebreak); greppable log-line
assertions in the test_running_zombie_sweep.py:1158 style; S4 —
NULL-instance candidate closes WITHOUT lock release (the seam's
NULL-instance skip; no crash, no vacuous DELETE observable).

Mutation legs (matrix T3.8): (a) strip the ORDER BY ⇒ pin RED;
(b) swap the age key to ``last_activity_at`` ⇒ pin RED; (c) break
the NULL-instance skip ⇒ pin RED.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.services.dependency_bus import set_dependency_bus

from tests.job_queue.zombie_close_helpers import (
    insert_zombie_fixture,
    make_bus_mock,
    make_recovery_service,
    read_job_state,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 9

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_REPO_SRC = (
    _SOURCE_ROOT
    / "daemon"
    / "repositories"
    / "job_queue"
    / "repository.py"
).read_text(encoding="utf-8")
_SERVICE_SRC = (
    _SOURCE_ROOT
    / "daemon"
    / "services"
    / "job_recovery_service.py"
).read_text(encoding="utf-8")


@pytest.fixture
def st_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def st_bus():
    bus = make_bus_mock(pending=[])
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


class TestCandidateSqlShape:
    async def test_task_kind_active_deleted_filters_present(self):
        assert "j.job_type = 'task'" in _REPO_SRC
        assert "j.admission_state = 'active'" in _REPO_SRC
        assert "j.deleted_at IS NULL" in _REPO_SRC

    async def test_work_id_join_and_terminal_filter(self):
        assert "ON t.work_id = j.job_id" in _REPO_SRC
        assert "t.status IN (:terminal_completed" in _REPO_SRC

    async def test_instance_not_exists_probe_shape(self):
        assert (
            "FROM task t2" in _REPO_SRC
            and "t2.instance_id = j.instance_id" in _REPO_SRC
            and ":nt_pending, :nt_running" in _REPO_SRC
        ), "the instance-scoped inflight NOT EXISTS leg must exist"

    async def test_age_key_is_max_completed_at_not_activity(self):
        """The monotone terminal-stamp clock — MAX(completed_at) —
        and NEVER instances.last_activity_at (enqueue-refreshable /
        frozen-by-design for a stalled instance)."""
        assert "MAX(completed_at) AS max_completed_at" in _REPO_SRC
        # The candidate SQL statement must not reference
        # last_activity_at (the DOCSTRING may name it when explaining
        # why it is NOT used — the pin scopes to the SQL region).
        method_block = _REPO_SRC.split(
            "def find_zombie_active_candidates", 1
        )[1]
        sql_block = method_block.split("SELECT j.job_id", 1)[1]
        sql_block = "SELECT j.job_id" + sql_block.split(
            "LIMIT :batch_cap"
        )[0] + "LIMIT :batch_cap"
        assert "last_activity_at" not in sql_block, (
            "the candidate query must key age on task terminal "
            "stamps, NOT last_activity_at"
        )

    async def test_deterministic_order_by_with_tiebreak(self):
        """S5: oldest-terminal-first ASC + job_id tiebreak — LIMIT
        without ORDER BY is non-deterministic across ticks."""
        assert re.search(
            r"ORDER BY COALESCE\(tm\.max_completed_at, "
            r"t\.completed_at\) ASC,\s*\n\s*j\.job_id ASC",
            _REPO_SRC,
        ), "the deterministic ORDER BY + tiebreak must be present"

    async def test_batch_cap_limit_present(self):
        assert "LIMIT :batch_cap" in _REPO_SRC


class TestLogLines:
    async def test_close_line_and_skip_tokens_greppable(self):
        """The (h) close line + skip reason tokens are greppable in
        the service source (the running_zombie_sweep.py:1158 pin
        style)."""
        assert (
            "Pattern (h) zombie-active close" in _SERVICE_SRC
        )
        for token in (
            "zombie_close_skipped_mission_live",
            "zombie_close_skipped_age_unmet",
            "zombie_close_skipped_bus_pending",
            "zombie_close_skipped_guard_error",
        ):
            assert token in _SERVICE_SRC, (
                f"skip reason token {token} must be greppable"
            )


class TestSsotCardinality:
    def test_non_terminal_task_statuses_cardinality_four(self):
        """W2 (code-review council, 2026-10-31): the candidate SQL +
        the A2 recheck carry FIXED arity-4 IN-binds over the SSOT
        non-terminal set — a drifted SSOT raises LOUD at both sites;
        this pin locks the contract so a 5th status can never land
        silently (planted 5th status ⇒ RED here AND the repository
        guards raise)."""
        from daemon.constants import NON_TERMINAL_TASK_STATUSES

        assert len(NON_TERMINAL_TASK_STATUSES) == 4, (
            sorted(NON_TERMINAL_TASK_STATUSES)
        )
        assert set(NON_TERMINAL_TASK_STATUSES) == {
            "pending",
            "running",
            "paused",
            "processing",
        }


class TestS4NullInstanceClose:
    async def test_null_instance_closes_without_lock_release(
        self, st_engine, st_bus
    ):
        """S4 (reviewer council): a NULL-instance candidate passes
        the NOT EXISTS vacuously and CLOSES; the lock DELETE is
        skipped safely (no lock row exists for the virtual job —
        assert no crash and admission DONE)."""
        import uuid as _uuid

        jid = f"job-zc-nullinst-{_uuid.uuid4().hex[:8]}"
        aged = "2026-10-30 00:00:00"
        # The Task row itself carries a REAL instance (task.
        # instance_id is NOT NULL) — the JobItem alone is
        # instance-less (the virtual-job shape).
        tinst = f"inst-zc-nullinst-taskowner-{_uuid.uuid4().hex[:6]}"
        with st_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO instances
                        (instance_id, agent_id, agent_dir, status,
                         project_id, created_at, updated_at, version,
                         parent_id, last_activity_at)
                    VALUES
                        (:iid, 'developer', 'agents/developer',
                         'completed', 'test-project', :t, :t, 1,
                         NULL, :t)
                    """
                ),
                {"iid": tinst, "t": aged},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO job_queue_items
                        (job_id, agent_id, agent_dir, message, source,
                         project_id, queue_id, priority, admission_state,
                         created_at, instance_id, job_type, retry_count,
                         metadata)
                    VALUES
                        (:job_id, 'developer', 'agents/developer', 'hi',
                         'api', 'test-project', NULL, 0, 'active',
                         :created_at, NULL, 'task', 0, '{}')
                    """
                ),
                {"job_id": jid, "created_at": aged},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO task
                        (task_type, instance_id, message_id, status,
                         retry_count, created_at, cancel_requested,
                         retry_scheduled, work_id, is_deferred,
                         is_background, completed_at, started_at)
                    VALUES
                        ('process_message', :tinst, NULL, 'completed',
                         0, :created_at, False, False, :work_id,
                         False, False, :completed_at, :created_at)
                    """
                ),
                {
                    "work_id": jid,
                    "tinst": tinst,
                    "created_at": aged,
                    "completed_at": aged,
                },
            )
        service = make_recovery_service(st_engine)
        stats = await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
        )
        st = read_job_state(st_engine, jid)
        assert st["admission_state"] == "done", (
            "the NULL-instance candidate must close (no tree to "
            f"consult): {st}"
        )
        assert st["terminal_reason"] == "zombie_active_completed", st
        zc = stats.get("zombie_close") or {}
        assert zc.get("finalized", 0) == 1, zc
