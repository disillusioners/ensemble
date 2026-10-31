"""Pack B — E1 surface zero-change + discount semantics (wi-78967dba).

Pins (phase3-plan T3.2):
* default-None byte-identity: every existing-caller shape driven
  with and without the param yields IDENTICAL verdicts (live/reason
  class/error/timed_out);
* discount scoping — member-job shape (member discounted, root
  still counts ⇒ hold) and root-job shape (root discounted, members
  still hold);
* the discounted instance is excluded from the non-terminal count at
  BOTH conjunct sites (walk loop + strand-exclusion recompute).

Mutation legs (matrix T3.2): remove the discount conjunct at either
site ⇒ the discount-scoping pins go RED (live=True where False
expected).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.services.mission_live_guard import evaluate_mission_live

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 4


def _iso(hours: float) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()


@pytest.fixture
def e1_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _seed_instance(
    engine, iid: str, status: str, *, parent_id=None, activity_hours=1.0
):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version, parent_id,
                     last_activity_at)
                VALUES
                    (:iid, 'developer', 'agents/developer', :status,
                     'test-project', :now, :now, 1, :parent_id, :activity)
                """
            ),
            {
                "iid": iid,
                "status": status,
                "now": _iso(3.0),
                "parent_id": parent_id,
                "activity": _iso(activity_hours),
            },
        )


class TestDefaultNoneZeroChange:
    async def test_default_none_verdict_identical_on_tree_shapes(
        self, e1_engine
    ):
        """Byte-identity: driving the guard with the param ABSENT vs
        explicitly None yields identical verdict tuples across a
        shape matrix (all-terminal / live-root / live-member /
        childless-root)."""
        repo = SQLModelInstanceRepository(engine=e1_engine)
        root = f"e1-root-{uuid.uuid4().hex[:6]}"
        child = f"e1-child-{uuid.uuid4().hex[:6]}"
        _seed_instance(e1_engine, root, "completed")
        _seed_instance(e1_engine, child, "running", parent_id=root)
        shapes = [root, child, root + "-missing"]
        for iid in shapes:
            v_implicit = await (
                evaluate_mission_live(
                    instance_repository=repo, instance_id=iid
                )
            )
            v_explicit = await (
                evaluate_mission_live(
                    instance_repository=repo,
                    instance_id=iid,
                    discount_instance_id=None,
                )
            )
            assert v_implicit.live == v_explicit.live
            assert v_implicit.error == v_explicit.error
            assert v_implicit.timed_out == v_explicit.timed_out
            assert v_implicit.reason == v_explicit.reason


class TestDiscountScoping:
    async def test_root_job_shape_discounts_root_only(self, e1_engine):
        """Root discounted + live MEMBER ⇒ still live (member
        liveness holds through the normal verdict)."""
        repo = SQLModelInstanceRepository(engine=e1_engine)
        root = f"e1-rj-{uuid.uuid4().hex[:6]}"
        member = f"e1-rjm-{uuid.uuid4().hex[:6]}"
        _seed_instance(e1_engine, root, "running")
        _seed_instance(e1_engine, member, "running", parent_id=root)
        v = await (
            evaluate_mission_live(
                instance_repository=repo,
                instance_id=root,
                discount_instance_id=root,
                bus_pending_count=0,
            )
        )
        assert v.live is True, (
            "a genuinely live MEMBER must still hold the mission "
            f"open when only the root is discounted: {v.reason}"
        )

    async def test_member_job_shape_discounts_member_root_still_counts(
        self, e1_engine
    ):
        """Member discounted + non-terminal ROOT in the walked tree ⇒
        the walk rooted at the member sees only the member's subtree;
        the discounted member is excluded ⇒ all-terminal-minus-
        discount branch ⇒ live=False CLEAN (the (h) close shape)."""
        repo = SQLModelInstanceRepository(engine=e1_engine)
        member = f"e1-mj-{uuid.uuid4().hex[:6]}"
        _seed_instance(e1_engine, member, "running")
        v = await (
            evaluate_mission_live(
                instance_repository=repo,
                instance_id=member,
                discount_instance_id=member,
                bus_pending_count=0,
            )
        )
        assert v.live is False, (
            "the discounted instance's own non-terminal status is "
            "circular bookkeeping — it must NOT hold the mission "
            f"open: {v.reason}"
        )
        assert v.error is False and v.timed_out is False

    async def test_discount_neutralizes_root_self_count_starvation_vector(
        self, e1_engine
    ):
        """The verified starvation vector (leg-c self-count on a
        stalled childless root, fresh-activity fallback): discounted
        ⇒ live=False. WITHOUT the discount the SAME shape is
        live=True (the self-hold) — asserted both directions."""
        repo = SQLModelInstanceRepository(engine=e1_engine)
        root = f"e1-sv-{uuid.uuid4().hex[:6]}"
        # Childless root, RUNNING (stalled), activity refreshed 1h
        # ago (the enqueue-refreshable fallback vector — inside the
        # 6h backstop window).
        _seed_instance(e1_engine, root, "running", activity_hours=1.0)
        v_hold = await (
            evaluate_mission_live(
                instance_repository=repo,
                instance_id=root,
                bus_pending_count=0,
            )
        )
        assert v_hold.live is True, (
            "pre-discount: the childless-root fallback holds — the "
            f"self-hold demonstrated: {v_hold.reason}"
        )
        v_disc = await (
            evaluate_mission_live(
                instance_repository=repo,
                instance_id=root,
                discount_instance_id=root,
                bus_pending_count=0,
            )
        )
        assert v_disc.live is False, (
            "with the discount the root's own circular status must "
            f"not hold: {v_disc.reason}"
        )
