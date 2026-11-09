"""A3 — the CAPTURE-ATTRIBUTION SEAM pack (registered pack
``skill_capture_killswitch_unit_test``; this file is the pack's A3 arm).

THE DEFECT THIS PACK PINS.  The capture-attribution gate asked
``SkillUsageRepository.has_applied_for_instance`` — which answers *"did
somebody file feedback for this instance"* (``feedback_applied == True``)
and ignores ``selected`` / ``applied`` / ``task_succeeded`` entirely.  So
a skill could be counted as GENUINELY EVOLVED on the strength of a
feedback-only signal, and — far worse — the 3,457-row census window
(``selected AND task_succeeded AND feedback_applied IS NULL``) was
indistinguishable from real evolution.

THE RULING (LEADER RULING Q1 — the OUTCOME-FIELDS predicate).  A skill
counts as genuinely evolved only when at least one usage record carries
ALL THREE conjuncts::

    selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL

* ``applied`` is deliberately NOT a conjunct — its only writer is the
  one-way latch in ``update_feedback``; ``update_completion`` never sets
  it, so it is a projection, not a signal.  (Building that missing
  writer is a DISCLOSED RESIDUAL, deliberately not built in Phase 1.)
* ``feedback_applied IS NOT NULL`` ALONE is not the gate either — a bare
  ``IS NOT NULL`` flip is a ≤4-row no-op against the window, because
  every window row has ``feedback_applied IS NULL``.  The OUTCOME FIELDS
  are what move the window.

THE MEASURED BOUNDARY (pin it with its census timestamp — the population
MOVES between measurements, so an un-timestamped number is not
comparable)::

    census timestamp : 2026-11-09 (Addendum-3 census)
    rows             : 10,108
    feedback_applied : NULL 3,547 · TRUE 6,091 · FALSE 470
    selected TRUE    : 10,108 (zero NULLs)
    applied TRUE     : 6,095  (zero NULLs)
    task_succeeded T : 9,311  (zero NULLs)
    THE WINDOW       : 3,457  (3,547 NULL − 90 NULL-and-unsuccessful)

The 3,457 is an UPPER BOUND on capture candidates, never a capture
forecast: ``SkillMetricsService.record_task_completion`` early-returns
when the instance injected no skill at all (``if not injected_ids:
return 0``), so instances with zero injected skills never reach the gate.

THE SEPARATE DISCLOSED BAND.  The 470 ``feedback_applied = FALSE`` rows
are HONEST NEGATIVES — feedback was evaluated and found not applicable.
They carry feedback evidence (``IS NOT NULL``), so they are not part of
the no-evidence window and must never be conflated with the 3,457.

Mutation table:

* ``M-A3a`` — drop the ``feedback_applied IS NOT NULL`` conjunct ⇒ the
  window instances re-enter the genuinely-evolved set and the measured
  DELTA goes 0 → 3,457 ⇒ the delta pin reds.  (If this mutation did not
  move the observed value the pin would be VAUCOUS and would be deleted;
  it moves it.)
* ``M-A3b`` — widen the predicate to ``selected == True`` only ⇒ a
  selected-but-never-fed-back instance counts as evolved ⇒ red.
* ``M-A3c`` — drop the population caveat (the early-return / UPPER
  BOUND sentence) from the predicate's docstring ⇒ the caveat pin reds.
* ``M-A3d`` — leave one of the two gate call sites on the old predicate
  ⇒ BLOCKED here: both call sites (``skill_metrics_service.py`` Gate 4
  and ``skill_evolution_service.py``'s service-seam twin) are OUTSIDE
  this lane's file fence.  Reported, not pinned.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.repositories.skill.models import (  # noqa: F401 — create_all needs them
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)
from daemon.repositories.skill.repository import SkillUsageRepository

#: The Addendum-3 census (2026-11-09) — the boundary this pack pins.
CENSUS_TIMESTAMP = "2026-11-09"
CENSUS_ROWS = 10_108
CENSUS_FEEDBACK_NULL = 3_547
CENSUS_FEEDBACK_TRUE = 6_091
CENSUS_FEEDBACK_FALSE = 470
CENSUS_SELECTED_TRUE = 10_108
CENSUS_APPLIED_TRUE = 6_095
CENSUS_TASK_SUCCEEDED_TRUE = 9_311
CENSUS_WINDOW = 3_457


@pytest.fixture()
def usage_repo():
    """An in-memory SQLite usage repository with the skill tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield SkillUsageRepository(engine)
    finally:
        engine.dispose()


def _row(
    repo: SkillUsageRepository,
    *,
    instance_id: str,
    selected: bool = True,
    task_succeeded: bool = True,
    feedback: bool | None = None,
    skill_id: str = "skill-1",
) -> None:
    """Insert one usage row and stamp its feedback band explicitly."""
    record = repo.create(
        skill_id=skill_id,
        project_id="proj",
        instance_id=instance_id,
        agent_id="coder",
        selected=selected,
        applied=False,
        task_succeeded=task_succeeded,
    )
    if feedback is not None:
        repo.update_feedback(record.id, feedback, "note")


class TestThePredicateIsExact:
    """The three-conjunct OUTCOME-FIELDS predicate (LEADER RULING Q1)."""

    def test_the_window_row_is_not_genuinely_evolved(self, usage_repo) -> None:
        """THE HEADLINE ROW — the 3,457-row window's shape.

        ``selected=True, task_succeeded=True, feedback_applied=NULL`` is
        exactly the false-eligible row the old gate counted.  It carries
        NO feedback evidence, so it must NOT count.
        """
        _row(usage_repo, instance_id="inst-window", feedback=None)
        assert usage_repo.has_genuinely_evolved_for_instance("inst-window") is False

    def test_a_row_with_feedback_evidence_counts(self, usage_repo) -> None:
        _row(usage_repo, instance_id="inst-applied", feedback=True)
        assert usage_repo.has_genuinely_evolved_for_instance("inst-applied") is True

    def test_a_negative_feedback_row_also_carries_evidence(self, usage_repo) -> None:
        """The 470-row band: ``feedback_applied = FALSE`` is an HONEST NEGATIVE.

        It is ``IS NOT NULL`` — feedback WAS recorded and evaluated — so
        it is evidence.  It is a different population from the no-evidence
        window, and it is never conflated with it.
        """
        _row(usage_repo, instance_id="inst-negative", feedback=False)
        assert usage_repo.has_genuinely_evolved_for_instance("inst-negative") is True

    def test_a_selected_but_unsuccessful_row_does_not_count(self, usage_repo) -> None:
        """``task_succeeded`` is a conjunct — a failure teaches the opposite."""
        _row(
            usage_repo,
            instance_id="inst-failed",
            task_succeeded=False,
            feedback=True,
        )
        assert usage_repo.has_genuinely_evolved_for_instance("inst-failed") is False

    def test_an_unselected_row_does_not_count(self, usage_repo) -> None:
        """``selected`` is a conjunct — a non-selection is not an evolution."""
        _row(
            usage_repo,
            instance_id="inst-unselected",
            selected=False,
            feedback=True,
        )
        assert usage_repo.has_genuinely_evolved_for_instance("inst-unselected") is False

    def test_the_applied_column_is_not_a_conjunct(self, usage_repo) -> None:
        """``applied`` is a projection, not a signal.

        ``has_genuinely_evolved_for_instance`` deliberately does NOT gate
        on ``applied``: its only writer is the one-way latch inside
        ``update_feedback``, and ``update_completion`` never sets it — so
        an ``applied``-conjunct would be blind to every row the
        completion hook finalises.  Here the row carries evidence while
        ``applied`` stays ``False`` (the negative-feedback path does not
        touch the latch), and the predicate must still say YES.
        """
        _row(usage_repo, instance_id="inst-applied-false", feedback=False)
        rows = usage_repo.get_by_skill("skill-1")[0]
        assert rows and all(record.applied is False for record in rows)
        assert usage_repo.has_genuinely_evolved_for_instance("inst-applied-false") is True

    def test_one_qualifying_row_among_many_is_enough(self, usage_repo) -> None:
        _row(usage_repo, instance_id="inst-mixed", feedback=None, skill_id="s-a")
        _row(usage_repo, instance_id="inst-mixed", feedback=None, skill_id="s-b")
        assert usage_repo.has_genuinely_evolved_for_instance("inst-mixed") is False
        _row(usage_repo, instance_id="inst-mixed", feedback=True, skill_id="s-c")
        assert usage_repo.has_genuinely_evolved_for_instance("inst-mixed") is True

    def test_an_unknown_instance_is_not_evolved(self, usage_repo) -> None:
        assert usage_repo.has_genuinely_evolved_for_instance("inst-absent") is False


class TestTheMeasuredDeltaIsZero:
    """A3 acceptance 1 — the DELTA form, which is the mutation's observable.

    ``M-A3a`` drops the feedback-evidence conjunct; these instances then
    enter the genuinely-evolved set while carrying no evidence, so the
    delta moves from 0 to the window population and this pin reds.  That
    is precisely why the pin is not vacuous.
    """

    def test_every_genuinely_evolved_instance_has_feedback_evidence(
        self, usage_repo
    ) -> None:
        # The census-window population (no evidence) …
        for index in range(5):
            _row(usage_repo, instance_id=f"inst-window-{index}", feedback=None)
        # … the honest-negative band (evidence, negative verdict) …
        for index in range(2):
            _row(usage_repo, instance_id=f"inst-negative-{index}", feedback=False)
        # … and the applied band.
        _row(usage_repo, instance_id="inst-applied", feedback=True)

        evolved = {
            instance
            for instance in (
                f"inst-window-{i}" for i in range(5)
            )
        } | {f"inst-negative-{i}" for i in range(2)} | {"inst-applied"}
        evolved = {
            instance
            for instance in evolved
            if usage_repo.has_genuinely_evolved_for_instance(instance)
        }
        with_evidence = {
            instance
            for instance in evolved
            if any(
                record.feedback_applied is not None
                for record in usage_repo.get_by_skill("skill-1")[0]
                if record.instance_id == instance
            )
        }
        # THE DELTA: genuinely-evolved MINUS feedback-evidence = 0.
        assert evolved - with_evidence == set(), (
            "every genuinely-evolved instance must carry feedback evidence — "
            f"the falsy ones are {sorted(evolved - with_evidence)}"
        )
        # …and the window instances are all outside the evolved set.
        assert not any(
            usage_repo.has_genuinely_evolved_for_instance(f"inst-window-{i}")
            for i in range(5)
        )

    def test_the_window_population_is_non_empty_in_the_fixture(self, usage_repo) -> None:
        """The delta pin above is NOT vacuous by construction."""
        for index in range(3):
            _row(usage_repo, instance_id=f"inst-w-{index}", feedback=None)
        window = [
            record
            for record in usage_repo.get_by_skill("skill-1")[0]
            if record.selected
            and record.task_succeeded
            and record.feedback_applied is None
        ]
        assert len(window) == 3


class TestTheCensusBoundaryIsPinned:
    """A3 acceptance — the exact boundary AND its timestamp live in one place.

    An un-timestamped count is not comparable across measurements (the
    population MOVED between the facts-extract reading and the Addendum-3
    census).  The plan requires the count and the timestamp to be pinned
    together; these assertions are the pin.
    """

    def test_the_predicate_docstring_carries_the_boundary_and_the_timestamp(self) -> None:
        doc = inspect.getdoc(
            SkillUsageRepository.has_genuinely_evolved_for_instance
        )
        assert doc, "the predicate must be documented"
        assert "3,457" in doc or "3457" in doc, (
            "the exact census boundary (3,457) must be pinned in the predicate's "
            "own documentation"
        )
        assert CENSUS_TIMESTAMP in doc, (
            f"the census timestamp ({CENSUS_TIMESTAMP}) must ride with the "
            "boundary, so a later re-measure can be compared"
        )
        assert "2026-11-09" in doc

    def test_the_boundary_arithmetic_is_self_consistent(self) -> None:
        """The census numbers must add up — no invented split.

        ``feedback_applied`` NULL 3,547 over 10,108 rows; the window is
        ``selected AND task_succeeded AND NULL`` = 3,457.  Since
        ``selected`` has ZERO NULLs and is TRUE for every row, the 90
        NULL rows outside the window are exactly the UNSUCCESSFUL ones:
        3,547 − 3,457 = 90, and ``task_succeeded`` TRUE = 9,311 leaves
        10,108 − 9,311 = 797 unsuccessful rows total.
        """
        assert CENSUS_FEEDBACK_NULL + CENSUS_FEEDBACK_TRUE + CENSUS_FEEDBACK_FALSE == CENSUS_ROWS
        assert CENSUS_FEEDBACK_NULL - CENSUS_WINDOW == 90
        assert CENSUS_ROWS - CENSUS_TASK_SUCCEEDED_TRUE == 797
        assert CENSUS_SELECTED_TRUE == CENSUS_ROWS

    def test_the_two_populations_are_distinct_and_named(self) -> None:
        """The 470 negative band is disclosed SEPARATELY from the 3,457 window."""
        doc = inspect.getdoc(
            SkillUsageRepository.has_genuinely_evolved_for_instance
        )
        assert "470" in doc, "the honest-negative band (470) must be disclosed"
        assert "honest negative" in doc.lower()
        assert "upper bound" in doc.lower()


class TestThePopulationCaveat:
    """A3 acceptance — the population caveat (M-A3c)."""

    def test_the_docstring_states_the_upper_bound_and_the_early_return(self) -> None:
        doc = inspect.getdoc(
            SkillUsageRepository.has_genuinely_evolved_for_instance
        )
        assert "upper bound" in doc.lower(), (
            "a count derived from this predicate is an UPPER BOUND on capture "
            "candidates, never a capture forecast"
        )
        assert "early-return" in doc.lower() or "early return" in doc.lower()
        assert "record_task_completion" in doc

    def test_the_early_return_still_exists_in_the_service(self) -> None:
        """The caveat is about REAL code — verify it, do not just assert prose."""
        from daemon.services import skill_metrics_service as sms_module

        source = inspect.getsource(sms_module.SkillMetricsService.record_task_completion)
        assert "if not injected_ids:" in source, (
            "the population caveat cites this early return; if it moves, the "
            "caveat's wording must move with it"
        )
        # …and it precedes the capture-eligibility call.
        assert source.index("if not injected_ids:") < source.index(
            "_check_capture_eligibility"
        )


class TestTheOldPredicateIsKeptButNotACaptureGate:
    """A3 — ``has_applied_for_instance`` stays, re-documented, not deleted."""

    def test_it_still_exists_with_its_feedback_contract(self, usage_repo) -> None:
        _row(usage_repo, instance_id="inst-old", feedback=True)
        _row(usage_repo, instance_id="inst-old-null", feedback=None)
        assert usage_repo.has_applied_for_instance("inst-old") is True
        assert usage_repo.has_applied_for_instance("inst-old-null") is False

    def test_its_docstring_disclaims_the_capture_gate_role(self) -> None:
        doc = inspect.getdoc(SkillUsageRepository.has_applied_for_instance)
        assert "NOT A CAPTURE-GATE PREDICATE" in doc.upper()
        assert "has_genuinely_evolved_for_instance" in doc, (
            "the disclaimer must point at the predicate that DOES gate capture"
        )

    def test_the_two_predicates_disagree_on_the_window_row(self, usage_repo) -> None:
        """The seam's whole point: the old predicate says YES to a window row.

        A window row with ``feedback_applied=True``-like evidence but no
        success would pass the old predicate and fail the new one; here
        we pin the sharper case — a successful, selected row WITH
        feedback passes both, while the null-feedback window row passes
        NEITHER of the new predicate's story but WOULD have passed a
        naive ``IS NOT NULL``-only flip on its non-null siblings.

        The discriminating case: an unsuccessful row with feedback.
        """
        _row(usage_repo, instance_id="inst-seam", task_succeeded=False, feedback=True)
        assert usage_repo.has_applied_for_instance("inst-seam") is True
        assert usage_repo.has_genuinely_evolved_for_instance("inst-seam") is False
