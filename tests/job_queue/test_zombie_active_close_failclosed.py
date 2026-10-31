"""Pack E — fail-closed polarity + bus-unavailable skip (wi-78967dba).

Pins (phase3-plan T3.5): guard error/timeout ⇒ SKIP +
``zombie_close_skipped_guard_error`` detail + ``error_skips``
counter, ZERO writes; bus unavailable ⇒ fail-safe skip with the
``zombie_close_skipped_bus_pending`` token (f2 Gate-1 precedent).

Mutation leg (matrix T3.5): plant the fail-OPEN form (treat
error/timeout as proceed) ⇒ the error-polarity pin goes RED.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.services import job_recovery_service as _jrs
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.mission_live_guard import MissionLiveVerdict

from tests.job_queue.zombie_close_helpers import (
    insert_zombie_fixture,
    make_bus_mock,
    make_recovery_service,
    read_job_state,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 3


@pytest.fixture
def fc_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


class TestFailClosedPolarity:
    async def test_guard_error_skips_with_zero_writes(self, fc_engine):
        """Guard raises (evaluation failed → the wrapper collapses to
        live=False error=True) ⇒ SKIP + error_skips + detail token,
        the JobItem stays ACTIVE with its lock intact."""
        ref = insert_zombie_fixture(
            fc_engine, verdict="completed", age_hours=3.0
        )
        service = make_recovery_service(fc_engine)
        bus = make_bus_mock(pending=[])
        set_dependency_bus(bus)
        try:
            async def _fake_evaluate(**kwargs):
                # Shape-aware plant: NON-discounted callers (f2)
                # still see the self-hold (live=True — f2 keeps
                # holding); the (h) leg's DISCOUNTED call returns
                # live=False + error=True — the unprovable shape.
                if kwargs.get("discount_instance_id"):
                    return MissionLiveVerdict(
                        live=False,
                        reason="planted (h) guard error (probe)",
                        error=True,
                    )
                return MissionLiveVerdict(
                    live=True,
                    reason="planted self-hold (non-discounted caller)",
                )

            with patch.object(
                _jrs, "evaluate_mission_live", _fake_evaluate
            ):
                stats = await (
                    service.reconcile_drift_states(
                        min_pending_age_seconds=0,
                        min_orphan_age_seconds=60,
                    )
                )
        finally:
            set_dependency_bus(None)
        st = read_job_state(fc_engine, ref["job_id"])
        assert st["admission_state"] == "active", (
            "fail-closed: an unprovable guard leg must NEVER "
            f"terminalize: {st}"
        )
        assert st["terminal_reason"] is None, st
        assert st["lock_rows"] == 1, st
        zc = stats.get("zombie_close") or {}
        assert zc.get("error_skips", 0) == 1, zc
        assert zc.get("finalized", 0) == 0, zc
        tokens = {
            d.get("pattern") for d in stats.get("details", [])
        }
        assert "zombie_close_skipped_guard_error" in tokens, tokens

    async def test_guard_timeout_skips_with_zero_writes(self, fc_engine):
        """timed_out=True with live=False is ALSO unprovable ⇒ SKIP
        (fail-closed — the deliberate divergence from the f-arms'
        fail-open WARN)."""
        ref = insert_zombie_fixture(
            fc_engine, verdict="completed", age_hours=3.0
        )
        service = make_recovery_service(fc_engine)
        bus = make_bus_mock(pending=[])
        set_dependency_bus(bus)
        try:
            async def _fake_evaluate(**kwargs):
                if kwargs.get("discount_instance_id"):
                    return MissionLiveVerdict(
                        live=False,
                        reason="planted (h) zombie-break timeout (probe)",
                        timed_out=True,
                    )
                return MissionLiveVerdict(
                    live=True,
                    reason="planted self-hold (non-discounted caller)",
                )

            with patch.object(
                _jrs, "evaluate_mission_live", _fake_evaluate
            ):
                stats = await (
                    service.reconcile_drift_states(
                        min_pending_age_seconds=0,
                        min_orphan_age_seconds=60,
                    )
                )
        finally:
            set_dependency_bus(None)
        st = read_job_state(fc_engine, ref["job_id"])
        assert st["admission_state"] == "active", st
        assert st["lock_rows"] == 1, st
        zc = stats.get("zombie_close") or {}
        assert zc.get("error_skips", 0) == 1, zc

    async def test_bus_unavailable_fails_safe_skip(self, fc_engine):
        """Bus NOT wired (singleton None — the f2 Gate-1
        'unavailable' shape) ⇒ skip with the bus token, zero
        writes."""
        ref = insert_zombie_fixture(
            fc_engine, verdict="completed", age_hours=3.0
        )
        service = make_recovery_service(fc_engine)
        set_dependency_bus(None)
        stats = await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
        )
        st = read_job_state(fc_engine, ref["job_id"])
        assert st["admission_state"] == "active", st
        assert st["lock_rows"] == 1, st
        tokens = {
            d.get("pattern") for d in stats.get("details", [])
        }
        assert "zombie_close_skipped_bus_pending" in tokens, tokens
        zc = stats.get("zombie_close") or {}
        assert zc.get("finalized", 0) == 0, zc
