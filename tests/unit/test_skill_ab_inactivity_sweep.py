"""W9 (2026-11-17) — the ``skill_ab_inactivity_sweep`` behavior pack.

The sweep is the periodic leg of the ONE sanctioned resolution
mechanism (user ruling E-6 + orchestrator Q1). It finds zero-traffic
A/B pairs (F5, 2026-11-26: BOTH ARMS at zero traffic —
``total_selections == 0`` AND ``total_applied == 0`` on both skill
rows — AND age >= ``ab_stall_timeout_days``), resolves the
E-6-unambiguous ones, and parks + surfaces the rest.

This pack pins the sweep's BEHAVIOUR:

* registration — the maintenance service registers the sweep beside
  ``skill_metric_scan`` / ``skill_orphan_sweep`` (source pin: the
  registration is inside the skill-evolution init block, so it rides
  the same configuration gate);
* one stalled resolve — an E-6-decidable pair IS resolved;
* one tie park — a generation tie parks (no winner guessed);
* per-item isolation — one failing pair never stops the batch;
* never-raising — a raising service never breaks the maintenance loop;
* traffic-bearing refusal — a pair with serving traffic on its ARMS
  is never listed (the sweep must not pick winners on normal pairs).
  F5: the signal is the arms' counters, not ``comparisons`` (which
  only tracks injection-side serving).

FULL-BOOT INTEGRATION IS DISCLOSED-UNVERIFIED: this pack drives the
wrapper directly (the maintenance service's boot-time registration and
idle-gate are covered by the staging smoke, not here).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER_SOURCE = REPO_ROOT / "daemon" / "manager.py"


class TestSweepRegistration:
    """The sweep is registered beside its siblings."""

    def test_manager_registers_the_sweep(self):
        source = MANAGER_SOURCE.read_text(encoding="utf-8")
        assert '"skill_ab_inactivity_sweep"' in source, (
            "the sweep must be registered with the maintenance service"
        )
        # …beside the two sibling skill sweeps (same registration
        # block, same house pattern).
        assert '"skill_metric_scan"' in source
        assert '"skill_orphan_sweep"' in source

    def test_manager_registers_the_sweep_call_shape(self):
        """GAP-5: bind the REGISTRATION CALL, not just the literal.

        The bare-literal pin above survives when the
        ``_maintenance_service.register(...)`` call is deleted — the
        sweep name still appears in the decision comment and in the
        wrapper body (``actor="skill_ab_inactivity_sweep"``). This pin
        anchors the actual call: ``register(`` → the sweep name → the
        interval expression → the wrapper callback must appear as ONE
        statement. Deleting the register call (mutA) ⇒ RED.
        """
        import re

        source = MANAGER_SOURCE.read_text(encoding="utf-8")
        pattern = re.compile(
            r"\.register\(\s*"
            r"[\"']skill_ab_inactivity_sweep[\"']\s*,\s*"
            r"[^,]+,\s*"
            r"self\._run_skill_ab_inactivity_sweep\s*,?\s*\)",
            re.DOTALL,
        )
        match = pattern.search(source)
        assert match is not None, (
            "the sweep must be registered via an actual "
            "`_maintenance_service.register(\"skill_ab_inactivity_sweep\", "
            "<interval>, self._run_skill_ab_inactivity_sweep)` call — the "
            "bare literal surviving in a comment/actor tag is not evidence "
            "of registration"
        )
        # The registration lives in the skill-evolution init block
        # beside its two sibling sweeps (bounded proximity).
        window = source[max(0, match.start() - 4000):match.end()]
        assert '"skill_metric_scan"' in window
        assert '"skill_orphan_sweep"' in window

    def test_the_sweep_wrapper_exists(self):
        source = MANAGER_SOURCE.read_text(encoding="utf-8")
        assert "async def _run_skill_ab_inactivity_sweep" in source

    def test_the_manager_hunk_carries_the_decision_marker(self):
        """The drift-guard lane requires a sanctioned marker comment on
        committed manager.py hunks ('decision' / 'memoir' / 'group
        chat')."""
        source = MANAGER_SOURCE.read_text(encoding="utf-8")
        marker_index = source.find("skill_ab_inactivity_sweep")
        assert marker_index != -1
        window = source[max(0, marker_index - 1500):marker_index + 1500]
        assert "decision" in window, (
            "the sweep registration hunk must carry the 'decision' "
            "marker comment"
        )


def _manager_with_service(svc):
    manager = MagicMock()
    manager._skill_evolution_service = svc
    return manager


async def _drive(svc):
    from daemon.manager import InstanceManager

    manager = _manager_with_service(svc)
    await InstanceManager._run_skill_ab_inactivity_sweep(manager)


class TestSweepBehaviour:
    """The sweep's decision + isolation behavior."""

    @pytest.mark.asyncio
    async def test_one_stalled_pair_is_resolved(self):
        svc = MagicMock()
        svc.find_stalled_ab_tests = AsyncMock(
            return_value=[
                {
                    "ab_test_group": "g-stalled",
                    "age_days": 12.0,
                    "skill_id_old": "old",
                    "skill_id_new": "new",
                    "comparisons": 0,
                    "e6_winner_id": "new",
                    "park_reason": None,
                }
            ]
        )
        svc.resolve_ab_test_sanctioned = AsyncMock(
            return_value={"resolved": True, "parked": False}
        )
        await _drive(svc)
        svc.resolve_ab_test_sanctioned.assert_awaited_once()
        kwargs = svc.resolve_ab_test_sanctioned.await_args.kwargs
        assert kwargs["basis"] == "stalled"
        assert kwargs["actor"] == "skill_ab_inactivity_sweep"
        assert kwargs["reason"], "the sanctioned resolution needs a reason"

    @pytest.mark.asyncio
    async def test_a_tie_pair_parks_without_resolution(self):
        svc = MagicMock()
        svc.find_stalled_ab_tests = AsyncMock(
            return_value=[
                {
                    "ab_test_group": "g-tie",
                    "age_days": 12.0,
                    "skill_id_old": "old",
                    "skill_id_new": "new",
                    "comparisons": 0,
                    "e6_winner_id": None,
                    "park_reason": "stalled_generation_tie_parked",
                }
            ]
        )
        svc.resolve_ab_test_sanctioned = AsyncMock(
            return_value={"resolved": False, "parked": True}
        )
        await _drive(svc)
        # The sweep DOES hand the tie to the sanctioned resolver — the
        # resolver is what parks it (it owns the E-6 tie rule + the
        # park announcement). What the sweep must never do is decide a
        # winner itself.
        svc.resolve_ab_test_sanctioned.assert_awaited_once()
        assert (
            svc.resolve_ab_test_sanctioned.await_args.kwargs["basis"]
            == "stalled"
        )

    @pytest.mark.asyncio
    async def test_per_item_isolation(self):
        """One failing pair never stops the batch."""
        svc = MagicMock()
        svc.find_stalled_ab_tests = AsyncMock(
            return_value=[
                {"ab_test_group": "g-bad", "age_days": 12.0},
                {"ab_test_group": "g-good", "age_days": 12.0},
            ]
        )

        async def _resolve(group, **kwargs):
            if group == "g-bad":
                raise RuntimeError("boom")
            return {"resolved": True, "parked": False}

        svc.resolve_ab_test_sanctioned = AsyncMock(side_effect=_resolve)
        await _drive(svc)  # must NOT raise
        assert svc.resolve_ab_test_sanctioned.await_count == 2

    @pytest.mark.asyncio
    async def test_never_raising(self):
        """A raising service never breaks the maintenance loop."""
        svc = MagicMock()
        svc.find_stalled_ab_tests = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        svc.resolve_ab_test_sanctioned = AsyncMock()
        await _drive(svc)  # must NOT raise
        svc.resolve_ab_test_sanctioned.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_stalled_pairs_means_no_resolution(self):
        svc = MagicMock()
        svc.find_stalled_ab_tests = AsyncMock(return_value=[])
        svc.resolve_ab_test_sanctioned = AsyncMock()
        await _drive(svc)
        svc.resolve_ab_test_sanctioned.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_service_is_a_no_op(self):
        manager = MagicMock()
        manager._skill_evolution_service = None
        from daemon.manager import InstanceManager

        await InstanceManager._run_skill_ab_inactivity_sweep(manager)


class TestSweepPredicate:
    """Traffic-bearing ⇒ NEVER listed (normal pairs are untouchable)."""

    @pytest.mark.asyncio
    async def test_traffic_bearing_pairs_are_not_listed(self):
        """Service-level predicate: a pair whose arms carry serving
        traffic (increment_counter) is not stalled, even when it is
        old and comparisons == 0."""
        from daemon.repositories.skill.repository import (
            SkillABTestRepository,
            SkillRepository,
        )
        from daemon.services.skill_evolution_service import (
            SkillEvolutionService,
        )
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        skill_repo = SkillRepository(engine)
        ab_test_repo = SkillABTestRepository(engine)
        svc = SkillEvolutionService(
            skill_repo=skill_repo,
            lineage_repo=MagicMock(),
            usage_repo=MagicMock(),
            embedding_service=MagicMock(),
            metrics_service=MagicMock(),
            ab_test_repo=ab_test_repo,
            config=MagicMock(ab_stall_timeout_days=7.0),
            llm_config={"base_url": "http://test", "api_key": "test"},
        )
        old = skill_repo.create(
            name="traffic-old",
            description="d",
            content="c",
            project_id="p",
        )
        new = skill_repo.create(
            name="traffic-new",
            description="d",
            content="c",
            project_id="p",
        )
        group = "g-traffic"
        for row in (old, new):
            skill_repo.update(
                row.id, ab_test_group=group, status="ab_testing"
            )
        ab_test_repo.create_ab_test(group, old.id, new.id)
        # Age the pair past the timeout, then give it TRAFFIC.
        from datetime import datetime, timedelta, timezone
        from sqlmodel import Session

        from daemon.repositories.skill.models import SkillABTest

        with Session(engine) as session:
            row = session.exec(
                select_skill_ab_test(group)
            ).first()
            row.created_at = (
                datetime.now(timezone.utc) - timedelta(days=30)
            ).isoformat()
            row.comparisons = 0
            session.add(row)
            session.commit()

        # FIX 3 (M13) — comparisons is 0 (the live 98c3e028
        # shape): the arm counters alone must carry the refusal. If the
        # arm-traffic check is removed/broken, this test goes GREEN for
        # the wrong reason, so row.comparisons is pinned to 0.
        skill_repo.increment_counter(old.id, "total_selections", 2)
        skill_repo.increment_counter(old.id, "total_applied", 2)

        stalled = await svc.find_stalled_ab_tests()
        assert all(
            entry["ab_test_group"] != group for entry in stalled
        ), "a traffic-bearing pair must never be listed as stalled"


def select_skill_ab_test(group: str):
    from sqlmodel import select

    from daemon.repositories.skill.models import SkillABTest

    return select(SkillABTest).where(SkillABTest.ab_test_group == group)
