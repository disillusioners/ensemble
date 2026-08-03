"""Unit tests for the blueprint save-plan / write-budget management (C9).

Covers:
  - ``plan_publication`` budget calculation (``budget_available``,
    ``will_rate_limit_at``)
  - ``execute_save_plan`` complete execution
  - ``execute_save_plan`` partial_rate_limited on rate-limit hit
  - ``execute_save_plan`` resume (skip completed ops)
  - per-op failure logged, not aborting the plan
  - ``SavePlanResult.is_complete`` is False for partial results
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.services.blueprint_rate_limiter import BlueprintRateLimiter
from daemon.services.blueprint_save_plan import SavePlan, SavePlanResult, WriteOp
from daemon.services.blueprint_write_service import (
    BlueprintNotFoundError,
    BlueprintWriteService,
)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_service(
    repo: Any = None,
    rate_limiter: Any = None,
    project_id: str = "proj-1",
    manager: Any = None,
) -> BlueprintWriteService:
    """Build a BlueprintWriteService for save-plan tests."""
    if manager is None:
        manager = MagicMock()
        # set_metadata / delete_metadata are sync; to_thread calls them.
        manager._project_repository = MagicMock()
    return BlueprintWriteService(
        repository=repo or MagicMock(),
        embedding_repository=MagicMock(),
        embedding_service=None,
        rate_limiter=rate_limiter,
        config=MagicMock(),
        project_id=project_id,
        manager=manager,
    )


class _FakeBlueprint:
    def __init__(self, id: str = "bp-x") -> None:
        self.id = id
        self.project_id = "proj-1"
        self.slug = "s"
        self.name = "n"
        self.kind = "area"
        self.content = "c"
        self.version = 1
        self.source = "auto"
        self.is_active = True
        self.tags: list = []
        self.file_refs: list = []
        self.trigger_queries: list = []


# ─── plan_publication budget calculation ────────────────────────────────────


class TestPlanPublicationBudget:
    """plan_publication counts writes against current rate-limit budget."""

    def test_plan_publication_calculates_budget(self) -> None:
        """Limiter at 3/5 → plan with 5 ops → will_rate_limit_at == 2."""
        limiter = BlueprintRateLimiter(max_revisions_per_hour=5)
        # Pre-fill 3 ticks.
        for _ in range(3):
            limiter.record_success("proj-1")

        svc = _make_service(rate_limiter=limiter)
        ops = [WriteOp(op="create", payload={"slug": f"s{i}", "name": "n", "kind": "area", "content": "c"}) for i in range(5)]

        plan = _run(svc.plan_publication(ops))
        assert plan.budget_available == 2  # 5 max - 3 used
        assert plan.will_rate_limit_at == 3  # 5 ops - 2 budget = 3 will rate-limit

    def test_plan_publication_budget_sufficient(self) -> None:
        """Limiter at 0/5 → plan with 5 ops → will_rate_limit_at == 0."""
        limiter = BlueprintRateLimiter(max_revisions_per_hour=5)

        svc = _make_service(rate_limiter=limiter)
        ops = [WriteOp(op="create", payload={"slug": f"s{i}", "name": "n", "kind": "area", "content": "c"}) for i in range(5)]

        plan = _run(svc.plan_publication(ops))
        assert plan.budget_available == 5
        assert plan.will_rate_limit_at == 0

    def test_plan_publication_no_limiter(self) -> None:
        """rate_limiter is None → effectively unlimited budget, nothing rate-limits."""
        svc = _make_service(rate_limiter=None)
        ops = [WriteOp(op="create", payload={})]
        plan = _run(svc.plan_publication(ops))
        # No limiter → budget equals op count (nothing will rate-limit).
        assert plan.budget_available >= len(ops)
        assert plan.will_rate_limit_at == 0


# ─── execute_save_plan ─────────────────────────────────────────────────────


class TestExecuteSavePlanComplete:
    """execute_save_plan processes all ops when budget is sufficient."""

    def test_execute_save_plan_complete(self) -> None:
        limiter = BlueprintRateLimiter(max_revisions_per_hour=10)
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()

        svc = _make_service(repo=repo, rate_limiter=limiter)
        ops = [
            WriteOp(op="create", payload={"slug": f"s{i}", "name": "n", "kind": "area", "content": "c"})
            for i in range(3)
        ]
        plan = _run(svc.plan_publication(ops))

        result = _run(svc.execute_save_plan(plan))

        assert result.status == "complete"
        assert result.completed == 3
        assert result.total == 3
        assert result.is_complete is True
        assert all(op.completed for op in plan.operations)


class TestExecuteSavePlanRateLimited:
    """execute_save_plan returns partial_rate_limited on rate-limit hit."""

    def test_execute_save_plan_partial_rate_limited(self) -> None:
        """Limiter at 5/5 → plan with 7 ops → 0 complete, rate-limited at index 0."""
        limiter = BlueprintRateLimiter(max_revisions_per_hour=5)
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()

        svc = _make_service(repo=repo, rate_limiter=limiter)
        ops = [
            WriteOp(op="create", payload={"slug": f"s{i}", "name": "n", "kind": "area", "content": "c"})
            for i in range(7)
        ]
        # Pre-fill the limiter to capacity.
        for _ in range(5):
            limiter.record_success("proj-1")

        plan = _run(svc.plan_publication(ops))

        result = _run(svc.execute_save_plan(plan))

        assert result.status == "partial_rate_limited"
        assert result.is_complete is False
        assert result.rate_limited_at_index == 0
        assert result.completed == 0

    def test_execute_save_plan_persists_on_rate_limit(self) -> None:
        """On rate-limit hit, SavePlan is persisted to project metadata."""
        limiter = BlueprintRateLimiter(max_revisions_per_hour=5)
        for _ in range(5):
            limiter.record_success("proj-1")

        manager = MagicMock()
        manager._project_repository = MagicMock()
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()

        svc = _make_service(repo=repo, rate_limiter=limiter, manager=manager)
        ops = [WriteOp(op="create", payload={"slug": "s", "name": "n", "kind": "area", "content": "c"})]
        plan = _run(svc.plan_publication(ops))

        result = _run(svc.execute_save_plan(plan))

        assert result.status == "partial_rate_limited"
        # Persist was called.
        manager._project_repository.set_metadata.assert_called()
        assert result.cooldown_resume_at is not None

    def test_save_plan_not_reported_as_success_on_partial(self) -> None:
        """partial_rate_limited → is_complete is False."""
        result = SavePlanResult(
            status="partial_rate_limited",
            completed=2,
            total=5,
        )
        assert result.is_complete is False


class TestExecuteSavePlanResume:
    """execute_save_plan(resume=True) skips completed ops."""

    def test_execute_save_plan_resume(self) -> None:
        limiter = BlueprintRateLimiter(max_revisions_per_hour=10)
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()

        svc = _make_service(repo=repo, rate_limiter=limiter)
        # Plan with 3 ops completed, 2 remaining.
        ops = [
            WriteOp(op="create", payload={"slug": f"s{i}", "name": "n", "kind": "area", "content": "c"}, completed=True)
            for i in range(3)
        ]
        ops.extend([
            WriteOp(op="create", payload={"slug": f"s{i}", "name": "n", "kind": "area", "content": "c"})
            for i in range(3, 5)
        ])

        plan = SavePlan(
            project_id="proj-1",
            run_id="run-1",
            mode="rebuild",
            operations=ops,
            total_operations=5,
            completed_operations=3,
        )

        result = _run(svc.execute_save_plan(plan, resume=True))

        assert result.status == "complete"
        assert result.completed == 5
        # Only 2 creates called (the 3 completed were skipped).
        assert repo.create.call_count == 2


class TestExecuteSavePlanPerOpFailure:
    """Per-op failure is logged; plan continues."""

    def test_execute_save_plan_per_op_failure_logged(self) -> None:
        limiter = BlueprintRateLimiter(max_revisions_per_hour=10)
        repo = MagicMock()
        # First create succeeds, second raises.
        repo.create.side_effect = [_FakeBlueprint(), RuntimeError("boom")]

        svc = _make_service(repo=repo, rate_limiter=limiter)
        ops = [
            WriteOp(op="create", payload={"slug": "s1", "name": "n", "kind": "area", "content": "c"}),
            WriteOp(op="create", payload={"slug": "s2", "name": "n", "kind": "area", "content": "c"}),
        ]
        plan = _run(svc.plan_publication(ops))

        result = _run(svc.execute_save_plan(plan))

        # One succeeded, one errored → partial.
        assert result.completed == 1
        assert result.status == "partial_rate_limited"
        # The failed op has an error.
        failed_ops = [op for op in plan.operations if op.error]
        assert len(failed_ops) == 1
        assert "boom" in failed_ops[0].error


# ─── Single-reservation-per-op regression (C2 fix follow-up) ───────────────


class TestExecuteSavePlanSingleReservation:
    """Each save-plan op must consume exactly ONE rate-limit slot.

    After the C2 ``reserve()`` migration, ``_check_rate_limit()`` is
    atomic (check+record). The previous ``execute_save_plan`` called
    it both in the outer per-op guard AND inside ``create_blueprint``,
    which double-reserved. These tests guard against the regression.
    """

    def test_execute_save_plan_single_reservation_per_op(self) -> None:
        """3 creates → exactly 3 limiter slots consumed (NOT 6)."""
        limiter = BlueprintRateLimiter(max_revisions_per_hour=5)
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()

        svc = _make_service(repo=repo, rate_limiter=limiter)
        ops = [
            WriteOp(
                op="create",
                payload={"slug": f"s{i}", "name": "n", "kind": "area", "content": "c"},
            )
            for i in range(3)
        ]
        plan = _run(svc.plan_publication(ops))

        result = _run(svc.execute_save_plan(plan))

        assert result.status == "complete"
        assert result.completed == 3
        # The whole point: exactly 3 slots consumed, not 6.
        state = limiter._state["proj-1"]
        assert len(state.revision_timestamps) == 3

    def test_execute_save_plan_rate_limited_consumes_one_slot(self) -> None:
        """On rate-limit hit, the failed op's reserve() did NOT consume.

        Setup: max_revisions_per_hour=2, 5 creates. Ops 0 and 1
        reserve successfully. Op 2's reserve() returns False (capacity
        full) → raises BlueprintRateLimitError. Op 2 does NOT consume
        a slot. Ops 3 and 4 are never attempted.
        """
        limiter = BlueprintRateLimiter(max_revisions_per_hour=2)
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()

        svc = _make_service(repo=repo, rate_limiter=limiter)
        ops = [
            WriteOp(
                op="create",
                payload={"slug": f"s{i}", "name": "n", "kind": "area", "content": "c"},
            )
            for i in range(5)
        ]
        plan = _run(svc.plan_publication(ops))

        result = _run(svc.execute_save_plan(plan))

        assert result.status == "partial_rate_limited"
        assert result.completed == 2
        assert result.rate_limited_at_index == 2
        state = limiter._state["proj-1"]
        # Exactly 2 slots consumed — the 2 successful ops. The 3rd
        # op's reserve() returned False and did NOT append a timestamp.
        assert len(state.revision_timestamps) == 2


class TestExecuteSavePlanClearedOnComplete:
    """On complete execution, the metadata key is deleted."""

    def test_save_plan_removed_on_complete(self) -> None:
        limiter = BlueprintRateLimiter(max_revisions_per_hour=10)
        repo = MagicMock()
        repo.create.return_value = _FakeBlueprint()
        manager = MagicMock()
        manager._project_repository = MagicMock()

        svc = _make_service(repo=repo, rate_limiter=limiter, manager=manager)
        ops = [WriteOp(op="create", payload={"slug": "s", "name": "n", "kind": "area", "content": "c"})]
        plan = _run(svc.plan_publication(ops))

        result = _run(svc.execute_save_plan(plan))

        assert result.status == "complete"
        manager._project_repository.delete_metadata.assert_called_once()
