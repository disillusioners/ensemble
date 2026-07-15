"""Unit tests for the C2 finalize-on-replace behavior of the Skill Evolution system.

When a ``<meta>`` tag replaces an existing skill (``load_skill=X`` while the
worker already had skill ``Y``), the previously injected skill ``Y`` must be
finalized as ``SUPERSEDED`` BEFORE the new skill ``X`` takes effect. This
preserves an audit trail of every skill that was ever attached to a worker,
while excluding superseded rows from completion-rate aggregation (the
completion-rate query filters on ``superseded=False``).

Coverage:

* :class:`TestFinalizeSupersededSkills` — verifies
  :meth:`SkillMetricsService.finalize_superseded_skills` writes the
  ``SUPERSEDED`` usage record, does NOT bump ``total_selections``
  (superseded rows are neutral markers), short-circuits on
  empty input, handles multiple dropped skills in one call, and soft-fails
  per skill when the DB raises.
* :class:`TestMetaTagFinalizeIntegration` — pure-logic checks for the
  dropped-set computation (``dropped = existing - new_set``) that the caller
  uses to decide which skill IDs to finalize, plus a smoke test of the
  ``parse_meta_tag`` / ``extract_load_skill`` pipeline.
* :class:`TestSweepOrphanedSkillRecords` — async orphan sweep that catches
  any usage records that escaped the finalize-on-replace path.

All tests use ``unittest.mock.MagicMock`` for the repositories — no real
database is touched. ``asyncio_mode = "auto"`` means the async tests in
:class:`TestSweepOrphanedSkillRecords` are picked up automatically (no
``@pytest.mark.asyncio`` decorators needed).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, call

import pytest

from daemon.services.skill_meta_parser import (
    extract_load_skill,
    parse_meta_tag,
)
from daemon.services.skill_metrics_service import SkillMetricsService


# =============================================================================
# Helpers
# =============================================================================


def _build_service() -> tuple[SkillMetricsService, MagicMock, MagicMock]:
    """Build a ``SkillMetricsService`` with mocked ``usage_repo`` / ``skill_repo``.

    Other collaborators (``trigger_repo``, ``ab_test_repo``, ``config``,
    ``instance_repo``, etc.) are stubbed with plain ``MagicMock()`` — none of
    them are touched by the methods exercised in this file.

    Returns:
        Tuple of ``(service, usage_repo_mock, skill_repo_mock)``.
    """
    usage_repo = MagicMock()
    skill_repo = MagicMock()
    service = SkillMetricsService(
        usage_repo=usage_repo,
        skill_repo=skill_repo,
        trigger_repo=MagicMock(),
        ab_test_repo=MagicMock(),
        config=MagicMock(),
    )
    return service, usage_repo, skill_repo


# =============================================================================
# finalize_superseded_skills
# =============================================================================


class TestFinalizeSupersededSkills:
    """Verify :meth:`SkillMetricsService.finalize_superseded_skills` semantics."""

    def test_dropped_skill_gets_superseded_record(self) -> None:
        """A single dropped skill gets one ``SUPERSEDED`` usage record.

        The record must carry ``superseded=True``, ``selected=True``, and all
        outcome flags zeroed (``applied``, ``task_succeeded``, ``fallback``,
        ``iterations``, ``duration_seconds``). SUPERSEDED rows are neutral
        markers — they MUST NOT bump any denormalized counter on the skill
        row (the trigger engine reads ``total_selections`` without filtering
        on ``superseded``, so inflating it would cause false-positive
        evolution triggers).
        """
        service, usage_repo, skill_repo = _build_service()

        result = service.finalize_superseded_skills(
            instance_id="inst-1",
            agent_id="agent-x",
            project_id="proj-1",
            dropped_skill_ids=["skill_a"],
        )

        assert result == 1
        usage_repo.create.assert_called_once_with(
            skill_id="skill_a",
            project_id="proj-1",
            instance_id="inst-1",
            agent_id="agent-x",
            selected=True,
            applied=False,
            task_succeeded=False,
            iterations=0,
            duration_seconds=0,
            fallback=False,
            superseded=True,
        )
        # SUPERSEDED rows must NOT bump any counter.
        skill_repo.increment_counter.assert_not_called()

    def test_same_skill_no_finalize(self) -> None:
        """Empty ``dropped_skill_ids`` short-circuits to ``0`` with no DB calls.

        Re-injecting the same skill (``load_skill=X`` while the worker already
        had ``X``) means the dropped set is empty — there is nothing to
        finalize, and the method must NOT write any SUPERSEDED rows.
        """
        service, usage_repo, skill_repo = _build_service()

        result = service.finalize_superseded_skills(
            instance_id="inst-1",
            agent_id="agent-x",
            project_id="proj-1",
            dropped_skill_ids=[],
        )

        assert result == 0
        usage_repo.create.assert_not_called()
        skill_repo.increment_counter.assert_not_called()

    def test_multiple_dropped_all_finalized(self) -> None:
        """All three dropped skills get a SUPERSEDED record, but no counter bumps.

        Order is preserved: the loop processes ``a`` first, then ``b``,
        then ``c``. Each one gets exactly one usage insert. SUPERSEDED
        rows MUST NOT bump ``total_selections`` (or any other counter) —
        the completion-rate aggregation already filters on
        ``superseded=False``, so the counter stays neutral.
        """
        service, usage_repo, skill_repo = _build_service()

        result = service.finalize_superseded_skills(
            instance_id="inst-1",
            agent_id="agent-x",
            project_id="proj-1",
            dropped_skill_ids=["a", "b", "c"],
        )

        assert result == 3
        assert usage_repo.create.call_count == 3

        # Collect the skill_ids each create() call was invoked with.
        skill_ids_in_order = [
            c.kwargs["skill_id"] for c in usage_repo.create.call_args_list
        ]
        assert skill_ids_in_order == ["a", "b", "c"]

        # Every call carries superseded=True (the whole point of finalize).
        for c in usage_repo.create.call_args_list:
            assert c.kwargs["superseded"] is True

        # SUPERSEDED rows are neutral markers — no counter bumps at all.
        skill_repo.increment_counter.assert_not_called()

    def test_individual_failure_soft_fail(self) -> None:
        """One bad skill does not block the others; returns only successes.

        ``usage_repo.create`` is programmed to raise on the FIRST call
        (``a``). The per-skill ``try/except`` swallows the error, so ``b``
        and ``c`` proceed normally. The return value reflects ONLY the
        successful inserts (``2``). SUPERSEDED rows never bump any
        counter, so ``increment_counter`` is not called at all.
        """
        service, usage_repo, skill_repo = _build_service()
        usage_repo.create.side_effect = [Exception("boom"), None, None]

        result = service.finalize_superseded_skills(
            instance_id="inst-1",
            agent_id="agent-x",
            project_id="proj-1",
            dropped_skill_ids=["a", "b", "c"],
        )

        assert result == 2

        # Three calls were attempted (a, b, c) — side_effect yielded one
        # value per call.
        assert usage_repo.create.call_count == 3

        # No counter bumps at all — SUPERSEDED rows are neutral markers.
        skill_repo.increment_counter.assert_not_called()


# =============================================================================
# Meta-tag finalize integration (pure logic)
# =============================================================================


class TestMetaTagFinalizeIntegration:
    """Verify the dropped-set computation that drives finalize-on-replace.

    These tests are intentionally pure-Python — they verify the algorithmic
    invariant ``dropped = existing - new_set`` and the parsing pipeline
    that produces ``new_set`` from a ``<meta>`` tag. The actual finalize
    call is exercised by :class:`TestFinalizeSupersededSkills` above.
    """

    def test_meta_tag_replace_triggers_finalize(self) -> None:
        """Replacing one skill with another drops the old one.

        ``existing = ["skill_a"]`` and the ``<meta>`` tag requests
        ``"skill_b"`` — the dropped set is exactly ``["skill_a"]``.
        The new skill itself never appears in the dropped set.
        """
        existing: list[str] = ["skill_a"]
        new_loaded_skill_id: str = "skill_b"

        new_set: set[str] = {new_loaded_skill_id}
        dropped: list[str] = [s for s in existing if s not in new_set]

        assert dropped == ["skill_a"]
        assert "skill_b" not in dropped

    def test_same_skill_meta_tag_no_drop(self) -> None:
        """Re-injecting the same skill yields an empty dropped set.

        The finalize step must NOT touch the skill that's still active —
        doing so would corrupt the completion-rate aggregation.
        """
        existing: list[str] = ["skill_a"]
        new_loaded_skill_id: str = "skill_a"

        new_set: set[str] = {new_loaded_skill_id}
        dropped: list[str] = [s for s in existing if s not in new_set]

        assert dropped == []

    def test_explicitly_replaced_ids_logic(self) -> None:
        """Three existing skills + one new request → all three are dropped.

        This mirrors the ``explicitly_replaced_ids`` path: when a fresh
        ``<meta>load_skill</meta>`` arrives, every prior injected skill
        is treated as superseded because the meta tag is a full
        replacement, not an additive injection.
        """
        existing_injected: list[str] = ["a", "b", "c"]
        new_loaded_skill_id: str = "d"

        new_set: set[str] = {new_loaded_skill_id}
        dropped: list[str] = [s for s in existing_injected if s not in new_set]

        assert dropped == ["a", "b", "c"]
        assert "d" not in dropped

    def test_meta_tag_parsing_extracts_load_skill(self) -> None:
        """The full ``<meta>`` parsing pipeline yields a usable skill ID.

        ``parse_meta_tag`` strips the tag from the visible message and
        returns the JSON payload as a dict; ``extract_load_skill`` then
        pulls out the ``load_skill`` field. This is the first step of
        the finalize-on-replace flow: a clean string for the user and
        a structured ID for the finalize call.
        """
        message = 'Hello <meta>{"load_skill": "skill_x"}</meta> world'

        cleaned, parsed = parse_meta_tag(message)

        assert isinstance(parsed, dict)
        assert parsed == {"load_skill": "skill_x"}
        # The tag content is fully removed from the visible message.
        assert "skill_x" not in cleaned
        assert "<meta>" not in cleaned
        assert "</meta>" not in cleaned

        # And the helper hands back the bare skill ID the rest of the
        # pipeline consumes.
        assert extract_load_skill(parsed) == "skill_x"


# =============================================================================
# sweep_orphaned_skill_records
# =============================================================================


class TestSweepOrphanedSkillRecords:
    """Verify the async orphan-sweep that catches missed finalizations."""

    async def test_sweep_finds_stale_records(self) -> None:
        """Two stale records are flipped to ``superseded=True`` and counted.

        The sweep computes a UTC ISO-8601 threshold and passes it to
        ``find_stale_pending``; the exact timestamp value is non-deterministic
        so we only check the argument shape (string + timezone marker).
        Each returned record is then fed to ``update_superseded``; a
        non-None return value counts as one swept record.
        """
        service, usage_repo, _skill_repo = _build_service()

        # Two fake "stale" records — only ``.id`` is read by the sweep.
        fake_rec_1: Any = MagicMock()
        fake_rec_1.id = "rec-1"
        fake_rec_2: Any = MagicMock()
        fake_rec_2.id = "rec-2"

        usage_repo.find_stale_pending.return_value = [fake_rec_1, fake_rec_2]
        usage_repo.update_superseded.return_value = "ok"  # non-None → counted

        result = await service.sweep_orphaned_skill_records(max_age_hours=24)

        assert result == 2

        # find_stale_pending received a single string argument. Don't pin
        # the exact timestamp — just sanity-check the shape: it's an ISO
        # string with a "T" separator and a tz offset on the end.
        usage_repo.find_stale_pending.assert_called_once()
        threshold_arg = usage_repo.find_stale_pending.call_args.args[0]
        assert isinstance(threshold_arg, str)
        assert "T" in threshold_arg
        assert threshold_arg.endswith("+00:00")

        # update_superseded was called once per stale record, in order.
        assert usage_repo.update_superseded.call_count == 2
        assert usage_repo.update_superseded.call_args_list == [
            call("rec-1"),
            call("rec-2"),
        ]

    async def test_sweep_no_records(self) -> None:
        """Empty result from ``find_stale_pending`` → nothing to do.

        The sweep must NOT invoke ``update_superseded`` when there are
        no stale rows. Returns ``0`` so the scheduler can log a clean
        "nothing to clean up" message.
        """
        service, usage_repo, _skill_repo = _build_service()
        usage_repo.find_stale_pending.return_value = []

        result = await service.sweep_orphaned_skill_records(max_age_hours=24)

        assert result == 0
        usage_repo.update_superseded.assert_not_called()