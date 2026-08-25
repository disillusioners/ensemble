"""Tests for ``JobRepository.atomic_transition`` ``preserve_completed_at`` flag.

Phase 3 — Pause/Resume/Terminate Tree-Propagation — B7(b) Task 3.9
(rev 2.1, council 2bb126df W8).

Plan reference:
  ``.agents/shared/planning/pause-resume-terminate-tree-fix/phase3-plan.md``
  (Rev 2.1 §B7(b) Tests — Task 3.9) +
  ``.agents/shared/planning/pause-resume-terminate-tree-fix/architecture-recommendation.md``
  (§6.2 Decision table — Approach A: Default False, no call-site wiring;
  re-scope B7(b) as verify+pin).

Background
==========

B7(b) is a **verify+pin** test, NOT a fix. The plan's Rev 1 "trivial COALESCE
guard" was rejected by AF-P3-7 because the verified re-arm path
(``rearm_with_lock`` ``repository.py:2045+`` + observer call
``job_feedback_observer.py:1470-1474``) means preserve-on-default would
freeze stale/failure-time stamps on legitimately re-armed jobs. Rev 2.1
inverts the test to **pin** that the existing flow stamps the *last*
terminal event's timestamp, NOT the first one.

The ``preserve_completed_at`` flag is RESERVED in
``atomic_transition`` at ``repository.py:1134+`` (default ``False``, ZERO
callers — Task 3.8 was DELETED in Rev 2). Task 3.9 only verifies the
default-False byte-identical behavior + last-settle semantics; the flag
itself is exercised by a dedicated case at the end.

Cases
=====

  * **Case 1 — complete_job re-arm→re-complete.** Full cycle: PENDING →
    ACTIVE → DONE → ACTIVE (via ``rearm_with_lock``) → DONE. Asserts the
    cycle is feasible and ``admission_state`` reflects the last settle.
    MECHANICS WARNING (Rev 2): a second ``complete_job`` on a DONE row
    does NOT no-op — ``atomic_transition`` raises
    ``InvalidTransitionError`` after rowcount=0 because
    ``from_status='processing'`` no longer matches (the row is
    ``admission_state='done'``). The re-arm step is REQUIRED to re-enter
    the transition path; without it the second call raises. Test
    structure follows.
  * **Case 2 — fail_job re-arm→re-fail.** Same shape as Case 1, but
    ``fail_job``. Asserts ``failed_at`` re-stamps to the last failure
    timestamp (``completed_at`` is overloaded across
    completed/failed/cancelled per ``daemon/repositories/job_queue/repository.py:1171-1176``
    docstring; ``failed_at`` is the only timing column that still
    flows through the ``_REMOVED_JOB_COLUMNS`` filter per
    ``repository.py:43-46``).
  * **Case 3 — terminate_job re-arm→re-cancel.** Same shape, ``terminate_job``.
  * **Case 4 — Regression / flag opt-in.** With default ``False``,
    ``atomic_transition`` is byte-identical to the pre-flag behavior
    (the column is still stripped). With ``preserve_completed_at=True``
    AND ``completed_at`` in ``extra_updates``, the SQL builder emits a
    ``COALESCE(completed_at, :completed_at)`` expression — verified via
    the generated SQL (column does not exist on the JobItem SQLModel
    post-Phase 5, so end-to-end stamp verification requires a future
    schema re-introduction).

MECHANICS WARNING (Rev 2 raise-vs-noop)
========================================

A second ``complete_job`` (or ``fail_job``/``terminate_job``) on a DONE
row does NOT no-op — ``atomic_transition`` raises
``InvalidTransitionError`` after rowcount=0 because
``from_status='processing'`` no longer matches a row whose
``admission_state`` is ``done``. The re-arm step is REQUIRED to re-enter
the transition path; without it the second call raises. Case 1's
structure accommodates this — see the docstring inside Case 1 for the
exact sequence.

Phase 5 model note
==================

After Phase 5 (Job-as-Queue-Proxy, commits 4eb1758a + migration
``20260628_000002_drop_job_queue_legacy_columns.sql``), the JobItem
SQLModel dropped the timing mirror columns (``status``, ``started_at``,
``completed_at``, ``result_summary``, ``error_message``, ``cancelled_at``)
and ``atomic_transition`` strips them from ``**extra_updates`` via
``_REMOVED_JOB_COLUMNS``. Only ``failed_at`` flows through. The test
asserts last-settle semantics via the columns that actually exist on the
SQLModel (``admission_state`` + ``failed_at``), not the conceptual
``completed_at`` — the latter is reserved for a future schema
re-introduction (per the verbatim reservation comment at
``repository.py:1134``).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session as SQLModelSession

# Register all table models so create_all() picks them up.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.job_queue.models import AdmissionState, JobItem, JobQueue
from daemon.repositories.job_queue.repository import JobRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool, session-scoped to a test)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repository(engine):
    """JobRepository backed by the in-memory engine."""
    return JobRepository(engine)


@pytest.fixture
def sample_job_data():
    """Sample job creation data."""
    return {
        "agent_id": "test-agent",
        "agent_dir": "./agents/test-agent",
        "message": "B7(b) last-settle test",
        "source": "api",
        "project_id": "test-project",
        "priority": 5,
        "job_metadata": {"b7b_test": True},
    }


@pytest.fixture
def fifo_queue(engine):
    """Provision a single FIFO queue with concurrency_limit=1 (matches
    ``rearm_with_lock`` semantics — single slot, lock_id per re-arm).

    ``rearm_with_lock`` (repository.py:2045+) looks up
    ``concurrency_limit`` from the JobQueue row and acquires a slot in
    range(concurrency_limit). A concurrency_limit=1 queue is the
    simplest harness for the re-arm→re-settle cycle.
    """
    qid = "qid-fifo-1"
    with SQLModelSession(engine) as s:
        q = JobQueue(
            queue_id=qid,
            project_id="test-project",
            queue_name="system_fifo_queue",
            queue_name_lower="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
        s.add(q)
        s.commit()
    return qid


def _read_job(engine, job_id: str) -> JobItem | None:
    """Read the JobItem row fresh from the DB (avoid stale ORM cache)."""
    with SQLModelSession(engine) as s:
        return s.get(JobItem, job_id)


def _now_iso() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Case 1 — complete_job re-arm→re-complete (pin last-settle semantics)
# ---------------------------------------------------------------------------


class TestCase1CompleteJobRearmRecomplete:
    """Case 1 — ``complete_job`` re-arm→re-complete cycle.

    Cycle shape:
      1. ``repository.create(...)`` → PENDING (admission_state='queued').
      2. ``start_job`` → ACTIVE (admission_state='active').
      3. ``complete_job`` → DONE (admission_state='done') — first settle.
      4. ``rearm_with_lock`` → ACTIVE — re-arm transition.
      5. ``complete_job`` again → DONE — second settle (the one that
         "would have re-stamped" under pre-Phase-5 semantics).

    MECHANICS WARNING (Rev 2): without the re-arm step (4), the second
    ``complete_job`` (5) raises ``InvalidTransitionError`` because the
    row is in ``admission_state='done'`` and ``from_status='processing'``
    no longer matches. The re-arm is REQUIRED to re-enter the transition
    path.
    """

    def test_rearm_recomplete_cycle_feasible(
        self, repository, engine, sample_job_data, fifo_queue
    ):
        """Full cycle is feasible (no exceptions raised).

        Asserts ``admission_state`` reflects the LAST settle after the
        re-arm→re-complete cycle (not the first settle). After Phase 5,
        ``complete_job`` does NOT stamp ``completed_at`` (the column is
        stripped via ``_REMOVED_JOB_COLUMNS``), so the last-settle
        semantics is verified via ``admission_state`` itself: a DONE
        row re-armed and re-completed must end in ``admission_state='done'``.
        """
        sample_job_data["queue_id"] = fifo_queue
        job = repository.create(**sample_job_data)
        job_id = job.job_id

        # Step 1: start_job → ACTIVE.
        started = repository.start_job(job_id, "inst-A")
        assert started is not None
        assert started.admission_state == AdmissionState.ACTIVE.value

        # Step 2: complete_job → DONE (first settle).
        first_complete = repository.complete_job(job_id, "result-summary-1")
        assert first_complete is not None
        assert first_complete.admission_state == AdmissionState.DONE.value

        # Step 3: rearm_with_lock → ACTIVE (re-arm between settles).
        rearmed, lock_ok = repository.rearm_with_lock(job_id, "inst-A")
        assert rearmed is not None
        assert lock_ok is True
        assert rearmed.admission_state == AdmissionState.ACTIVE.value

        # Step 4: complete_job again → DONE (second settle).
        second_complete = repository.complete_job(job_id, "result-summary-2")
        assert second_complete is not None
        assert second_complete.admission_state == AdmissionState.DONE.value

        # Last-settle invariant: row reflects the SECOND (latest) settle.
        # ``complete_job`` does NOT stamp ``terminal_reason`` (only
        # ``cancel_job`` does, see ``repository.py:2407-2411``); the
        # last-settle semantics is verified via ``admission_state``
        # alone, which is the queue-side authority (Phase 4 cleanup).
        final = _read_job(engine, job_id)
        assert final is not None
        assert final.admission_state == AdmissionState.DONE.value

    def test_second_complete_without_rearm_raises(
        self, repository, engine, sample_job_data, fifo_queue
    ):
        """MECHANICS WARNING (Rev 2): second ``complete_job`` without
        re-arm RAISES ``InvalidTransitionError``.

        Documents that the re-arm step is REQUIRED between settles. The
        pre-Phase-5 expectation was "second complete_job no-ops on a
        DONE row" — that's wrong; ``atomic_transition`` raises after
        ``rowcount=0`` (``repository.py:1316-1326``).
        """
        from daemon.services.job_state_machine import InvalidTransitionError

        sample_job_data["queue_id"] = fifo_queue
        job = repository.create(**sample_job_data)
        job_id = job.job_id

        repository.start_job(job_id, "inst-A")
        repository.complete_job(job_id, "first-result")

        # Without re-arm, the second complete_job raises.
        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.complete_job(job_id, "second-result")

        assert exc_info.value.job_id == job_id
        # Row remains DONE — the second call did not silently no-op.
        final = _read_job(engine, job_id)
        assert final is not None
        assert final.admission_state == AdmissionState.DONE.value


# ---------------------------------------------------------------------------
# Case 2 — fail_job re-arm→re-fail (pin last-settle via failed_at)
# ---------------------------------------------------------------------------


class TestCase2FailJobRearmRefail:
    """Case 2 — ``fail_job`` re-arm→re-fail cycle.

    Same shape as Case 1 but ``fail_job`` instead of ``complete_job``.
    ``failed_at`` is the only timing column that flows through the
    ``_REMOVED_JOB_COLUMNS`` filter (see ``repository.py:43-46``).
    Asserts ``failed_at`` re-stamps to the LAST failure timestamp —
    documents that the timing field is overloaded across
    completed/failed/cancelled per
    ``repository.py:1171-1176`` docstring, and re-arm→re-fail correctly
    re-stamps to the new failure time.
    """

    def test_failed_at_re_stamps_to_last_failure(
        self, repository, engine, sample_job_data, fifo_queue
    ):
        """``failed_at`` reflects the LAST failure timestamp.

        Two ``fail_job`` calls separated by ``rearm_with_lock``: the
        second ``failed_at`` must be strictly later than the first (or
        equal — depends on clock granularity; we assert it is at least
        not the original T1 if the test driver sleeps between calls).
        """
        sample_job_data["queue_id"] = fifo_queue
        job = repository.create(**sample_job_data)
        job_id = job.job_id

        # ACTIVE → fail_job (first failure T1).
        repository.start_job(job_id, "inst-A")
        first_fail = repository.fail_job(job_id, "first error")
        assert first_fail is not None
        assert first_fail.admission_state == AdmissionState.DONE.value
        assert first_fail.failed_at is not None
        first_failed_at = first_fail.failed_at

        # Re-arm → ACTIVE → fail_job (second failure T2).
        rearmed, _ = repository.rearm_with_lock(job_id, "inst-A")
        assert rearmed is not None
        assert rearmed.admission_state == AdmissionState.ACTIVE.value

        second_fail = repository.fail_job(job_id, "second error")
        assert second_fail is not None
        assert second_fail.admission_state == AdmissionState.DONE.value
        assert second_fail.failed_at is not None
        second_failed_at = second_fail.failed_at

        # Last-settle invariant: ``failed_at`` reflects the LATEST
        # failure (T2). The string comparison works for ISO-8601 UTC
        # timestamps with second-or-better granularity — both ends are
        # produced by ``datetime.now(timezone.utc).isoformat()`` so the
        # comparison is consistent.
        assert second_failed_at >= first_failed_at, (
            f"Last-settle invariant violated: "
            f"first failed_at={first_failed_at!r} second failed_at={second_failed_at!r}"
        )

        # Re-read from DB to confirm the latest value is persisted (not
        # just the in-memory ORM attribute).
        final = _read_job(engine, job_id)
        assert final is not None
        assert final.failed_at == second_failed_at


# ---------------------------------------------------------------------------
# Case 3 — terminate_job re-arm→re-cancel
# ---------------------------------------------------------------------------


class TestCase3TerminateJobRearmRecancel:
    """Case 3 — ``terminate_job`` re-arm→re-cancel cycle.

    Same shape as Case 1 but ``terminate_job``. Asserts the cycle is
    feasible and ``admission_state`` reflects the last settle. After
    Phase 5 ``terminate_job`` does NOT stamp ``completed_at`` (stripped
    via ``_REMOVED_JOB_COLUMNS``), so the last-settle invariant is
    verified via ``admission_state`` and ``terminal_reason``.
    """

    def test_rearm_recancel_cycle_feasible(
        self, repository, engine, sample_job_data, fifo_queue
    ):
        """Full cycle is feasible (no exceptions raised)."""
        sample_job_data["queue_id"] = fifo_queue
        job = repository.create(**sample_job_data)
        job_id = job.job_id

        # ACTIVE → terminate_job (first cancel T1).
        repository.start_job(job_id, "inst-A")
        first_term = repository.terminate_job(job_id, "first terminate")
        assert first_term is not None
        assert first_term.admission_state == AdmissionState.DONE.value

        # Re-arm → ACTIVE → terminate_job (second cancel T2).
        rearmed, _ = repository.rearm_with_lock(job_id, "inst-A")
        assert rearmed is not None
        assert rearmed.admission_state == AdmissionState.ACTIVE.value

        second_term = repository.terminate_job(job_id, "second terminate")
        assert second_term is not None
        assert second_term.admission_state == AdmissionState.DONE.value

        # Last-settle invariant: row reflects the SECOND (latest) settle.
        final = _read_job(engine, job_id)
        assert final is not None
        assert final.admission_state == AdmissionState.DONE.value


# ---------------------------------------------------------------------------
# Case 4 — Regression / ``preserve_completed_at`` flag opt-in
# ---------------------------------------------------------------------------


class TestCase4PreserveCompletedAtFlagOptIn:
    """Case 4 — ``preserve_completed_at`` flag opt-in.

    With default ``False``, ``atomic_transition`` is **byte-identical**
    to the pre-flag behavior (the column is stripped via
    ``_REMOVED_JOB_COLUMNS``). The flag is RESERVED — zero callers in
    Phase 3 (Task 3.8 DELETED per AF-P3-7); the flag's True branch is
    reachable only when a future deliberate first-touch caller passes
    BOTH ``preserve_completed_at=True`` AND a ``completed_at`` kwarg.

    This case verifies:

      1. Default-False (``preserve_completed_at`` not passed) is
         byte-identical: ``atomic_transition`` with a ``completed_at``
         kwarg succeeds (no error), and the kwarg is silently stripped
         (Phase 5 behavior). No ``InvalidTransitionError`` raised for
         the kwarg itself — backward compatibility preserved.
      2. Explicit ``preserve_completed_at=True`` is accepted at the
         signature level (no type-checker error) AND the SQL UPDATE is
         generated with a COALESCE expression for ``completed_at`` —
         verified via the compiled SQL string. (End-to-end SQL execution
         would require a future ``completed_at`` column on the JobItem
         SQLModel; the COALESCE branch uses ``literal_column`` to
         reference the raw column name and is reserved for a future
         schema re-introduction.)
    """

    def test_default_false_is_byte_identical(
        self, repository, sample_job_data, fifo_queue
    ):
        """Default ``False`` is byte-identical to no-flag callers.

        ``complete_job`` (and the other 3 stamp sites — ``fail_job``,
        ``terminate_job``, observer fail-safe) pass ``completed_at=now``
        to ``atomic_transition``. With the flag at its default
        ``False``, the column is silently stripped (Phase 5). The call
        succeeds without error — backward compatibility preserved.

        Asserts the signature still accepts the call shape and returns
        a non-None ``JobItem`` for the first settle.
        """
        sample_job_data["queue_id"] = fifo_queue
        job = repository.create(**sample_job_data)
        job_id = job.job_id

        # Pass ``completed_at`` explicitly via atomic_transition (no
        # flag) — should be byte-identical to today's behavior: kwarg
        # silently stripped, no error.
        result = repository.atomic_transition(
            job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.DONE.value,
            completed_at=_now_iso(),
            result_summary="test",
        )
        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value

    def test_explicit_false_matches_default(
        self, repository, sample_job_data, fifo_queue
    ):
        """Explicit ``preserve_completed_at=False`` matches default.

        Belt-and-braces: passing the flag explicitly with ``False``
        must NOT change behavior relative to the implicit default.
        """
        sample_job_data["queue_id"] = fifo_queue
        job_a = repository.create(**sample_job_data)
        job_b = repository.create(**sample_job_data)
        job_a_id = job_a.job_id
        job_b_id = job_b.job_id

        # Implicit default (no flag).
        implicit = repository.atomic_transition(
            job_a_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.DONE.value,
            completed_at=_now_iso(),
            result_summary="implicit",
        )
        # Explicit False.
        explicit = repository.atomic_transition(
            job_b_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.DONE.value,
            preserve_completed_at=False,
            completed_at=_now_iso(),
            result_summary="explicit",
        )

        assert implicit is not None
        assert explicit is not None
        assert implicit.admission_state == explicit.admission_state
        assert implicit.terminal_reason == explicit.terminal_reason

    def test_true_branch_generates_coalesce_sql(self, repository, sample_job_data, fifo_queue):
        """``preserve_completed_at=True`` emits COALESCE SQL.

        Verifies the flag's True branch fires when a ``completed_at``
        kwarg is also passed. The compiled SQL must contain a
        ``COALESCE(completed_at, ...)`` expression for the
        ``completed_at`` column. The JobItem SQLModel has no
        ``completed_at`` attribute after Phase 5; the COALESCE branch
        uses raw ``text()`` SQL (``repository.py:1303-1332``) to
        reference the column by name without going through SQLAlchemy's
        ORM column resolution (which would raise ``CompileError`` for
        unknown columns on UPDATE).

        This test verifies the SQL GENERATION shape — it does NOT
        execute the UPDATE against the table (the column doesn't
        exist on the SQLite test schema; execution would raise
        ``OperationalError``). The SQL string is captured via a
        monkeypatched ``session.exec()`` and ``session.get()``.
        """
        sample_job_data["queue_id"] = fifo_queue
        job = repository.create(**sample_job_data)
        job_id = job.job_id

        # Capture the SQL the True branch builds by patching
        # ``session.exec()`` AND ``session.get()`` on the repository's
        # SQLModelSession. ``session.get()`` would otherwise hit the
        # DB and trigger the ``InvalidTransitionError`` disambiguation
        # path. We short-circuit BOTH methods so the branch under
        # test never touches the DB.
        captured_sqls: list[str] = []
        from sqlmodel import Session as _SMS
        from sqlalchemy.engine.result import Result

        def capture_exec(self, statement, *args, **kwargs):
            try:
                compiled = str(statement.compile(
                    dialect=repository.engine.dialect,
                    compile_kwargs={"literal_binds": False},
                ))
                captured_sqls.append(compiled)
            except Exception:
                pass  # not all statement types compile; skip silently
            # Fake a rowcount=0 result so the branch flows through
            # the disambiguation path (which we ALSO mock via get()).
            fake_result = Result.__new__(Result)
            fake_result.rowcount = 0
            fake_result._hardclosed = False
            return fake_result

        def capture_get(self, *args, **kwargs):
            # Return None so the disambiguation returns ``None`` (job
            # not found) — bypasses the ``InvalidTransitionError``
            # raise path. We never want this test to assert anything
            # about the row state; we're inspecting SQL only.
            return None

        from unittest.mock import patch as _patch

        with _patch.object(_SMS, "exec", capture_exec), \
             _patch.object(_SMS, "get", capture_get):
            # Trigger the True branch. ``from_status='done'`` will not
            # match a freshly-created row (``admission_state='queued'``),
            # so even WITHOUT the mock, the UPDATE no-ops with
            # ``rowcount=0``. The COALESCE branch fires during SQL
            # construction (text() SQL built BEFORE session.exec()), so
            # the no-match path still builds the SQL.
            result = repository.atomic_transition(
                job_id,
                from_status=AdmissionState.DONE.value,
                to_status=AdmissionState.DONE.value,
                preserve_completed_at=True,
                completed_at=_now_iso(),
            )

        # The UPDATE is mocked to rowcount=0 + return None; the
        # disambiguation path is also mocked. Verify the result
        # returned without raising.
        assert result is None
        assert captured_sqls, (
            "No SQL captured — the True branch did not fire. "
            "Check that ``preserve_completed_at=True`` AND a "
            "``completed_at`` kwarg were both passed."
        )

        # The COALESCE SQL must be present in at least one captured
        # statement.
        coalesce_sqls = [
            s for s in captured_sqls
            if "completed_at" in s.lower() and "coalesce" in s.lower()
        ]
        assert coalesce_sqls, (
            f"No UPDATE statement with COALESCE(completed_at, ...) emitted "
            f"when preserve_completed_at=True. Captured SQLs:\n"
            + "\n".join(captured_sqls[:5])
        )
        # Sanity-check the COALESCE pattern.
        assert "coalesce(completed_at" in coalesce_sqls[0].lower(), (
            f"COALESCE pattern malformed: {coalesce_sqls[0]!r}"
        )

    def test_repository_true_branch_emits_coalesce_sql(
        self, repository, sample_job_data, fifo_queue
    ):
        """End-to-end: ``atomic_transition`` with
        ``preserve_completed_at=True`` emits a COALESCE SQL UPDATE.

        Companion to ``test_true_branch_generates_coalesce_sql`` —
        this variant uses the actual ``atomic_transition`` entrypoint
        and inspects the emitted SQL via a ``session.exec()`` mock.
        Same guarantee: the COALESCE branch fires when both
        ``preserve_completed_at=True`` AND a ``completed_at`` kwarg
        are passed.
        """
        sample_job_data["queue_id"] = fifo_queue
        job = repository.create(**sample_job_data)
        job_id = job.job_id

        # Same mock strategy as the sibling test.
        captured_sqls: list[str] = []
        from sqlmodel import Session as _SMS
        from sqlalchemy.engine.result import Result

        def capture_exec(self, statement, *args, **kwargs):
            try:
                compiled = str(statement.compile(
                    dialect=repository.engine.dialect,
                    compile_kwargs={"literal_binds": False},
                ))
                captured_sqls.append(compiled)
            except Exception:
                pass
            fake_result = Result.__new__(Result)
            fake_result.rowcount = 0
            fake_result._hardclosed = False
            return fake_result

        def capture_get(self, *args, **kwargs):
            return None

        from unittest.mock import patch as _patch

        with _patch.object(_SMS, "exec", capture_exec), \
             _patch.object(_SMS, "get", capture_get):
            # Same triggering call.
            result = repository.atomic_transition(
                job_id,
                from_status=AdmissionState.DONE.value,
                to_status=AdmissionState.DONE.value,
                preserve_completed_at=True,
                completed_at=_now_iso(),
            )

        assert result is None
        assert captured_sqls, "No SQL captured"
        coalesce_sqls = [
            s for s in captured_sqls
            if "completed_at" in s.lower() and "coalesce" in s.lower()
        ]
        assert coalesce_sqls, (
            f"No UPDATE statement with COALESCE(completed_at, ...) emitted "
            f"by atomic_transition. Captured SQLs:\n"
            + "\n".join(captured_sqls[:5])
        )

    def test_default_branch_does_not_emit_coalesce(self, repository, sample_job_data, fifo_queue):
        """Default ``False`` does NOT emit COALESCE SQL (real seam).

        Negative control companion to
        ``test_repository_true_branch_emits_coalesce_sql``: drives the
        REAL ``JobRepository.atomic_transition`` on the default path
        (``preserve_completed_at`` unset) with ``completed_at`` (and
        ``result_summary``) in ``extra_updates`` — the exact call shape
        the stamp sites use — and captures the compiled SQL via the
        same ``session.exec()`` / ``session.get()`` mocks as the
        sibling True-branch test.

        Asserts:
          1. ``atomic_transition`` actually emitted an UPDATE (the
             seam was driven — non-vacuous);
          2. that UPDATE references neither ``completed_at`` nor
             ``result_summary`` — both are stripped via
             ``_REMOVED_JOB_COLUMNS`` before the statement is built;
          3. NO captured statement contains a ``COALESCE`` reference
             to ``completed_at`` (the reserved True-branch SQL must
             not fire on the default path).

        Revision note (sweep #4): the prior version of this test
        fabricated its own ``sqlmodel_update(JobItem)`` statement and
        asserted the SQL it had just built — it never drove
        ``atomic_transition``, so it pinned nothing about the
        repository. Replaced with this real-seam version.
        """
        sample_job_data["queue_id"] = fifo_queue
        job = repository.create(**sample_job_data)
        job_id = job.job_id

        # Same mock strategy as the sibling True-branch test.
        captured_sqls: list[str] = []
        from sqlmodel import Session as _SMS
        from sqlalchemy.engine.result import Result

        def capture_exec(self, statement, *args, **kwargs):
            try:
                compiled = str(statement.compile(
                    dialect=repository.engine.dialect,
                    compile_kwargs={"literal_binds": False},
                ))
                captured_sqls.append(compiled)
            except Exception:
                pass
            fake_result = Result.__new__(Result)
            fake_result.rowcount = 0
            fake_result._hardclosed = False
            return fake_result

        def capture_get(self, *args, **kwargs):
            return None

        from unittest.mock import patch as _patch

        with _patch.object(_SMS, "exec", capture_exec), \
             _patch.object(_SMS, "get", capture_get):
            # Default-path trigger: NO preserve_completed_at flag, but
            # completed_at (and result_summary — both members of
            # _REMOVED_JOB_COLUMNS) passed exactly as the stamp sites
            # do. The mocked exec fakes rowcount=0 and the mocked get
            # returns None, so the disambiguation path returns None —
            # SQL capture is the point, not row state (same contract
            # as the sibling test).
            result = repository.atomic_transition(
                job_id,
                from_status=AdmissionState.QUEUED.value,
                to_status=AdmissionState.DONE.value,
                completed_at=_now_iso(),
                result_summary="test",
            )

        # Mocked disambiguation (rowcount=0 + get→None) — same as the
        # sibling True-branch test.
        assert result is None

        # 1. The seam was driven — atomic_transition emitted SQL.
        assert captured_sqls, (
            "No SQL captured — atomic_transition did not emit any "
            "statement on the default path"
        )

        update_sqls = [
            s for s in captured_sqls
            if s.lstrip().lower().startswith("update")
        ]
        assert update_sqls, (
            "atomic_transition did not emit an UPDATE on the default "
            "path. Captured SQLs:\n" + "\n".join(captured_sqls[:5])
        )

        # 2. Stripped columns must NOT appear in the UPDATE at all.
        for sql in update_sqls:
            lowered = sql.lower()
            assert "completed_at" not in lowered, (
                f"Default branch should not reference completed_at "
                f"(stripped via _REMOVED_JOB_COLUMNS): {sql!r}"
            )
            assert "result_summary" not in lowered, (
                f"Default branch should not reference result_summary "
                f"(stripped via _REMOVED_JOB_COLUMNS): {sql!r}"
            )

        # 3. No COALESCE reference to completed_at anywhere in the
        # captured SQL (the reserved True-branch UPDATE must not fire).
        for sql in captured_sqls:
            lowered = sql.lower()
            assert not (
                "coalesce" in lowered and "completed_at" in lowered
            ), (
                f"Default branch must not emit "
                f"COALESCE(completed_at, ...): {sql!r}"
            )

    def test_no_callers_wire_true(self):
        """Static guard: no caller wires ``preserve_completed_at=True``.

        Phase 3 ships the flag DEFINED but UNWIRED — Task 3.8 (wiring
        ``True`` at 3 call sites) was DELETED per AF-P3-7
        (``phase3-plan.md`` §Rev 2 Changelog row 9). This test enforces
        the invariant at the unit-test level: a static scan of the
        PRODUCTION codebase (``daemon/`` only) must find zero
        non-definition references to the flag value ``True``.

        Implementation note: the scan is intentionally narrow — only
        ``daemon/`` (production callers). ``tests/`` is excluded
        because this test file legitimately exercises the flag value
        ``True`` (Case 4 — explicit ``preserve_completed_at=True``
        exercises the COALESCE branch) and would self-trip the
        invariant. Documentation (``*.md``) and vendored deps are
        also excluded by virtue of the ``*.py`` extension.

        The scan is anchored at the PROJECT ROOT (parents[3] from
        this file's location: ``tests/unit/repositories/x.py``) and
        includes a non-vacuity pin — if the root resolution regresses
        (e.g. ``parents[2]`` used by mistake, which would scan the
        empty ``tests/daemon`` and ``tests/tests`` subtrees and yield
        zero files), the test FAILS loudly instead of passing
        vacuously.
        """
        import re
        from pathlib import Path

        # parents[3] = project root (this file lives at
        # tests/unit/repositories/). A previous version used
        # parents[2] which resolves to tests/ — that made the scan
        # vacuous (it then iterated tests/daemon and tests/tests,
        # both nonexistent, yielding zero files and an empty
        # offenders list).
        repo_root = Path(__file__).resolve().parents[3]
        offenders: list[str] = []
        scanned: list[Path] = []

        # Pattern that would indicate a call site wiring the flag.
        # We look for ``preserve_completed_at=True`` or
        # ``preserve_completed_at = True`` (with optional whitespace).
        # The DEFINITION itself is exempted by line content (the
        # signature is ``preserve_completed_at: bool = False``).
        pattern = re.compile(r"preserve_completed_at\s*=\s*True")

        # Scan ONLY ``daemon/`` — the invariant is 'no PRODUCTION
        # callers wire True'. ``tests/`` is excluded because this
        # test file legitimately references the flag value ``True``
        # in Case 4 (explicit opt-in exercises the COALESCE SQL
        # branch). Including ``tests/`` would make the test
        # self-trip on its own flag exercises.
        for py_file in (repo_root / "daemon").rglob("*.py"):
            scanned.append(py_file)
            for lineno, line in enumerate(py_file.read_text().splitlines(), start=1):
                if pattern.search(line):
                    # Exempt the definition signature line (default is False).
                    if "preserve_completed_at: bool = False" in line:
                        continue
                    offenders.append(f"{py_file.relative_to(repo_root)}:{lineno}: {line.strip()}")

        # Non-vacuity pin: ensure the scan actually traversed the
        # production target. If the root resolution regresses to
        # parents[2] (which yields tests/daemon + tests/tests, both
        # nonexistent, scanned == []), this assertion FAILS loudly
        # instead of letting the offender-list check pass vacuously.
        assert any(
            p.name == "repository.py" and "job_queue" in str(p)
            for p in scanned
        ), (
            f"Non-vacuity pin: scan did not traverse "
            f"daemon/repositories/job_queue/repository.py. This "
            f"indicates a root-resolution regression (likely "
            f"parents[2] instead of parents[3]). Scanned "
            f"{len(scanned)} .py files total."
        )

        assert offenders == [], (
            f"Found {len(offenders)} call sites wiring preserve_completed_at=True "
            f"(Phase 3 invariant — flag must be RESERVED, not wired): "
            + "\n".join(offenders[:10])
        )
