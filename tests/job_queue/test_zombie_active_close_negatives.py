"""Pack C — healthy-tree negatives + PAUSED + live-sibling (wi-78967dba).

Pins (phase3-plan T3.3): the five verified starvation vectors
exercised as NON-triggers for HEALTHY trees — predicate leg 1 fails
(any non-terminal Task on the instance ⇒ never a candidate);
PAUSED task ⇒ never a candidate; live sibling member ⇒ discounted-E1
HOLDS (mission_live_holds, no write).

Mutation leg (matrix T3.3): plant the defect form — drop the
instance NOT EXISTS leg from the candidate SQL ⇒ a healthy
pending-sibling shape becomes a candidate ⇒ pins go RED.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
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
EXPECTED_COLLECTED = 6


@pytest.fixture
def ng_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def ng_bus():
    bus = make_bus_mock(pending=[])
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


async def _drive(engine):
    service = make_recovery_service(engine)
    return await (
        service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )
    )


class TestHealthyShapesNeverClose:
    async def test_pending_retry_child_blocks_candidacy(self, ng_engine, ng_bus):
        """The imminent-retry-mint vector: a PENDING retry child on
        the instance (fresh work_id, inherited instance_id) fails the
        instance inflight probe ⇒ the JobItem is NEVER a candidate —
        zero writes, zero examined."""
        ref = insert_zombie_fixture(
            ng_engine,
            verdict="failed",
            age_hours=3.0,
            extra_task_status="pending",
        )
        stats = await _drive(ng_engine)
        st = read_job_state(ng_engine, ref["job_id"])
        assert st["admission_state"] == "active", st
        assert st["terminal_reason"] is None, st
        zc = stats.get("zombie_close") or {}
        assert zc.get("examined", 0) == 0, (
            "a pending sibling on the instance must keep the job OUT "
            f"of the candidate set entirely: {zc}"
        )

    async def test_paused_task_blocks_candidacy(self, ng_engine, ng_bus):
        """PAUSED protection: a paused task is NON-terminal ⇒ the
        instance inflight probe fails ⇒ never a candidate."""
        ref = insert_zombie_fixture(
            ng_engine,
            verdict="completed",
            age_hours=3.0,
            extra_task_status="paused",
        )
        stats = await _drive(ng_engine)
        st = read_job_state(ng_engine, ref["job_id"])
        assert st["admission_state"] == "active", st
        zc = stats.get("zombie_close") or {}
        assert zc.get("examined", 0) == 0, zc

    async def test_running_sibling_blocks_candidacy(self, ng_engine, ng_bus):
        """Fresh dispatch: a RUNNING sibling task on the instance ⇒
        never a candidate."""
        ref = insert_zombie_fixture(
            ng_engine,
            verdict="completed",
            age_hours=3.0,
            extra_task_status="running",
        )
        await _drive(ng_engine)
        st = read_job_state(ng_engine, ref["job_id"])
        assert st["admission_state"] == "active", st
        assert st["lock_rows"] == 1, st

    async def test_young_zombie_not_examined(self, ng_engine, ng_bus):
        """Age unmet (30 min old, bound 2h) ⇒ never a candidate (the
        SQL pre-filter) — examined stays 0."""
        ref = insert_zombie_fixture(
            ng_engine, verdict="completed", age_hours=0.5
        )
        stats = await _drive(ng_engine)
        st = read_job_state(ng_engine, ref["job_id"])
        assert st["admission_state"] == "active", st
        zc = stats.get("zombie_close") or {}
        assert zc.get("examined", 0) == 0, zc

    async def test_live_sibling_member_holds_via_discounted_e1(
        self, ng_engine, ng_bus
    ):
        """A zombie whose mission tree has a LIVE OTHER MEMBER: the
        candidate query selects it (its own instance is clean), the
        discounted E1 verdict is live=True (the sibling holds) ⇒
        mission_live_holds, ZERO writes — subordination to E1's
        legitimate holds."""
        from tests.job_queue.zombie_close_helpers import _aged
        from sqlalchemy import text as _text

        ref = insert_zombie_fixture(
            ng_engine, verdict="completed", age_hours=3.0
        )
        # A live sibling member under the SAME mission root: the
        # fixture's instance IS the root (task-kind); add a child
        # member under it (non-terminal, fresh activity).
        sibling = f"inst-sib-{uuid.uuid4().hex[:8]}"
        with ng_engine.begin() as conn:
            conn.execute(
                _text(
                    """
                    INSERT INTO instances
                        (instance_id, agent_id, agent_dir, status,
                         project_id, created_at, updated_at, version,
                         parent_id, last_activity_at)
                    VALUES
                        (:iid, 'worker', 'agents/worker', 'running',
                         'test-project', :now, :now, 1, :parent_id,
                         :activity)
                    """
                ),
                {
                    "iid": sibling,
                    "now": str(_aged(3.0)),
                    "parent_id": ref["instance_id"],
                    "activity": str(_aged(0.05)),
                },
            )
        stats = await _drive(ng_engine)
        st = read_job_state(ng_engine, ref["job_id"])
        assert st["admission_state"] == "active", (
            "a live sibling member must HOLD the close (E1 "
            f"subordination): {st}"
        )
        assert st["lock_rows"] == 1, st
        zc = stats.get("zombie_close") or {}
        assert zc.get("mission_live_holds", 0) == 1, zc
        assert zc.get("finalized", 0) == 0, zc

    async def test_deleted_job_never_candidate(self, ng_engine, ng_bus):
        """Soft-deleted rows are excluded by the candidate filter."""
        from sqlalchemy import text as _text

        ref = insert_zombie_fixture(
            ng_engine, verdict="completed", age_hours=3.0
        )
        with ng_engine.begin() as conn:
            conn.execute(
                _text(
                    "UPDATE job_queue_items SET deleted_at = :now "
                    "WHERE job_id = :j"
                ),
                {"now": "2026-10-31T00:00:00", "j": ref["job_id"]},
            )
        stats = await _drive(ng_engine)
        zc = stats.get("zombie_close") or {}
        assert zc.get("examined", 0) == 0, zc
