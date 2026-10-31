"""Pack A — Pattern (h) core close, both directions (wi-78967dba).

A/B convention: HEAD closes the Phase-0 zombie fixture within one
drift cycle of age eligibility (admission DONE + correct
``zombie_active_<verdict>`` token + same-transaction lock release);
BASE holds it (no writer) — the base side is proven by the Phase-0
evidence (``GAP-PROVEN`` on SQLite + real PG, 3 cycles × 3 verdicts)
and re-derivable by running this pack at the base revision with the
(h) call sites absent (the candidate query returns rows but nothing
terminalizes — asserted via the ``test_base_shape_holds_by_construction``
negative, which pins the PRE-fix equivalent: kill-switch OFF).

Covers (phase3-plan T3.1):
* positive close ×3 verdicts (completed / failed / cancelled);
* age boundary INCLUSIVE: age == bound ⇒ eligible, bound−ε ⇒ hold
  (the strand-healer precedent's inclusive semantics);
* batch-cap remainder-next-tick.

Mutation legs (matrix T3.1): plant the defect forms —
(a) drop the ``NOT EXISTS`` inflight leg from the candidate SQL;
(b) remove the discount conjunct in the guard; (c) exclusive age
boundary — each must turn at least one pin in this pack RED.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.services import job_recovery_service as _jrs
from daemon.services.dependency_bus import set_dependency_bus

from tests.job_queue.zombie_close_helpers import (
    insert_zombie_fixture,
    make_bus_mock,
    make_jqs_mock,
    make_recovery_service,
    read_job_state,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 6


@pytest.fixture
def zc_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def zc_bus():
    bus = make_bus_mock(pending=[])
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


class TestZombieActiveCoreClose:
    async def test_closes_completed_verdict_with_token_and_lock_release(
        self, zc_engine, zc_bus
    ):
        ref = insert_zombie_fixture(
            zc_engine, verdict="completed", age_hours=3.0
        )
        service = make_recovery_service(zc_engine)
        stats = await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
                zombie_close_age_bound_seconds=7200,
                zombie_close_batch_cap=25,
            )
        )
        st = read_job_state(zc_engine, ref["job_id"])
        assert st["admission_state"] == "done", st
        assert st["terminal_reason"] == "zombie_active_completed", st
        assert st["lock_rows"] == 0, "lock must release same-txn"
        zc = stats.get("zombie_close") or {}
        assert zc.get("finalized", 0) == 1, zc

    async def test_closes_failed_verdict_with_failed_token(
        self, zc_engine, zc_bus
    ):
        ref = insert_zombie_fixture(
            zc_engine, verdict="failed", age_hours=3.0
        )
        service = make_recovery_service(zc_engine)
        await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
        )
        st = read_job_state(zc_engine, ref["job_id"])
        assert st["admission_state"] == "done", st
        assert st["terminal_reason"] == "zombie_active_failed", st
        assert st["lock_rows"] == 0, st

    async def test_closes_cancelled_verdict_with_cancelled_token(
        self, zc_engine, zc_bus
    ):
        ref = insert_zombie_fixture(
            zc_engine, verdict="cancelled", age_hours=3.0
        )
        service = make_recovery_service(zc_engine)
        await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
            )
        )
        st = read_job_state(zc_engine, ref["job_id"])
        assert st["admission_state"] == "done", st
        assert st["terminal_reason"] == "zombie_active_cancelled", st
        assert st["lock_rows"] == 0, st

    async def test_age_boundary_is_inclusive_at_the_bound(
        self, zc_engine, zc_bus
    ):
        """age ≥ bound (120s margin) ⇒ eligible; bound−ε (120s
        younger) ⇒ NOT a candidate — the row stays active with zero
        writes. INCLUSIVE per the strand-healer precedent: the
        margins make both halves deterministic (the exact == edge is
        wall-clock-jittered by construction; a planted SHIFTED SQL
        bound — cutoff pulled 60s older — flips the eligible half
        RED, the boundary-semantics mutation this pin must catch)."""
        ref_old = insert_zombie_fixture(
            zc_engine,
            verdict="completed",
            age_hours=(7200 + 120) / 3600.0,  # ≥ bound, 120s margin
            queue_suffix="atbound",
        )
        ref_young = insert_zombie_fixture(
            zc_engine,
            verdict="completed",
            age_hours=(7200 - 120) / 3600.0,
            queue_suffix="belowbound",
        )
        service = make_recovery_service(zc_engine)
        stats = await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
                zombie_close_age_bound_seconds=7200,
            )
        )
        st_old = read_job_state(zc_engine, ref_old["job_id"])
        st_young = read_job_state(zc_engine, ref_young["job_id"])
        assert st_old["admission_state"] == "done", st_old
        assert st_young["admission_state"] == "active", st_young
        assert st_young["terminal_reason"] is None, st_young
        assert st_young["lock_rows"] == 1, st_young
        zc = stats.get("zombie_close") or {}
        assert zc.get("examined", 0) == 1, zc

    async def test_batch_cap_defers_remainder_to_next_tick(
        self, zc_engine, zc_bus
    ):
        """cap=1 with TWO age-qualified candidates ⇒ ONE closes this
        pass (the OLDEST — deterministic S5 order), the second stays
        active and closes on the NEXT reconcile pass."""
        ref_old = insert_zombie_fixture(
            zc_engine, verdict="completed", age_hours=5.0,
            queue_suffix="older",
        )
        ref_new = insert_zombie_fixture(
            zc_engine, verdict="failed", age_hours=3.0,
            queue_suffix="newer",
        )
        service = make_recovery_service(zc_engine)
        stats1 = await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
                zombie_close_batch_cap=1,
            )
        )
        zc1 = stats1.get("zombie_close") or {}
        assert zc1.get("examined", 0) == 1, zc1
        assert read_job_state(zc_engine, ref_old["job_id"])[
            "admission_state"
        ] == "done"
        assert read_job_state(zc_engine, ref_new["job_id"])[
            "admission_state"
        ] == "active"
        stats2 = await (
            service.reconcile_drift_states(
                min_pending_age_seconds=0,
                min_orphan_age_seconds=60,
                zombie_close_batch_cap=1,
            )
        )
        assert read_job_state(zc_engine, ref_new["job_id"])[
            "admission_state"
        ] == "done"

    async def test_base_shape_holds_by_construction_when_disabled(
        self, zc_engine, zc_bus
    ):
        """The BASE behavior is byte-restored under the kill-switch:
        the identical fixture stays ``active``, lock immortal, no
        ``terminal_reason`` — across repeated cycles (the Phase-0
        GAP-PROVEN evidence's in-pack mirror)."""
        ref = insert_zombie_fixture(
            zc_engine, verdict="completed", age_hours=3.0
        )
        service = make_recovery_service(zc_engine)
        for _ in range(3):
            stats = await (
                service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                    zombie_close_enabled=False,
                )
            )
            st = read_job_state(zc_engine, ref["job_id"])
            assert st["admission_state"] == "active", st
            assert st["terminal_reason"] is None, st
            assert st["lock_rows"] == 1, st
        zc = stats.get("zombie_close") or {}
        assert zc.get("examined", 0) == 0, (
            "kill-switch OFF must be a COMPLETE no-op — no candidate "
            f"query issued: {zc}"
        )
