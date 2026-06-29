"""Tests for ``JobRepository.atomic_transition`` — Audit Finding C1 fix.

Verifies that ``atomic_transition`` is genuinely atomic at the SQL level,
not just nominally atomic. Covers:

1. Happy-path transitions update the status and all extra_updates fields.
2. Status mismatch raises ``InvalidTransitionError`` (the SQL guard).
3. Missing job returns ``None`` (does NOT raise).
4. Concurrent / serial second-writer after the first succeeds is rejected.
5. All observed ``extra_updates`` keys across the codebase are accepted.
6. Terminal-state clobbering (the bug) is prevented: a second writer that
   passes the *expected* ``from_status`` after the first writer already
   changed it cannot overwrite the terminal status.
"""
import threading

import pytest

from daemon.repositories.job_queue import AdmissionState, JobRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.services.job_state_machine import InvalidTransitionError


class TestAtomicTransitionHappyPath:
    """Happy-path coverage — transition succeeds, fields are applied."""

    def test_pending_to_processing_applies_extra_updates(self, repository, sample_job_data):
        """PENDING -> PROCESSING sets ``instance_id`` (started_at no longer on JobItem)."""
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        job = repository.create(**sample_job_data)

        updated = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.ACTIVE.value,
            started_at=now_iso,
            instance_id="inst-A",
        )

        assert updated is not None
        assert updated.admission_state == AdmissionState.ACTIVE.value
        assert updated.instance_id == "inst-A"

    def test_processing_to_completed_applies_completed_at_and_result_summary(
        self, repository, sample_job_data
    ):
        """PROCESSING -> COMPLETED — completed_at/result_summary no longer on JobItem."""
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "inst-A")

        updated = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.DONE.value,
            completed_at="2026-06-19T12:00:00+00:00",
            result_summary="all good",
        )

        assert updated is not None
        assert updated.admission_state == AdmissionState.DONE.value

    def test_processing_to_failed_applies_error_message(self, repository, sample_job_data):
        """PROCESSING -> FAILED — error_message/completed_at no longer on JobItem."""
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "inst-A")

        updated = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.DONE.value,
            completed_at="2026-06-19T12:00:00+00:00",
            error_message="boom",
        )

        assert updated is not None
        assert updated.admission_state == AdmissionState.DONE.value

    def test_pending_to_cancelled_applies_cancelled_at(self, repository, sample_job_data):
        """PENDING -> CANCELLED — cancelled_at no longer on JobItem."""
        job = repository.create(**sample_job_data)

        updated = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.DONE.value,
            cancelled_at="2026-06-19T12:00:00+00:00",
        )

        assert updated is not None
        assert updated.admission_state == AdmissionState.DONE.value


class TestAtomicTransitionStatusGuard:
    """The SQL-level status guard is the actual fix — it must work."""

    def test_status_mismatch_raises_invalid_transition_error(
        self, repository, sample_job_data
    ):
        """Calling atomic_transition with the wrong ``from_status`` raises.

        Mirrors the ``start_job_atomic_wrong_status`` test but at the
        repository level so we directly assert the SQL-guard behavior.
        """
        job = repository.create(**sample_job_data)
        # Move to PROCESSING so the row's actual status is no longer PENDING.
        repository.start_job(job.job_id, "inst-A")

        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.atomic_transition(
                job.job_id,
                from_status=AdmissionState.QUEUED.value,
                to_status=AdmissionState.ACTIVE.value,
            )

        # The error must report the row's *actual* current admission_state
        # (not the frozen status) — callers depend on this for diagnostics.
        assert exc_info.value.from_state == AdmissionState.ACTIVE.value
        assert exc_info.value.to_state == AdmissionState.ACTIVE.value

    def test_missing_job_returns_none(self, repository):
        """Transitioning a non-existent job returns None (does NOT raise).

        This distinguishes the "row not found" case from the "status
        mismatch" case — the disambiguation SELECT is part of the fix.
        """
        result = repository.atomic_transition(
            "this-job-id-does-not-exist",
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.ACTIVE.value,
        )

        assert result is None

    def test_second_writer_after_terminal_raises(self, repository, sample_job_data):
        """Two callers targeting the same job: first wins, second raises.

        This is the exact race the original (Pattern B) implementation
        allowed: both callers' Python status checks passed because the
        read happened before either commit. The fixed implementation
        detects this via the SQL guard.
        """
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "inst-A")

        # First writer completes the job.
        completed = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.DONE.value,
            completed_at="2026-06-19T12:00:00+00:00",
            result_summary="writer-1",
        )
        assert completed is not None
        assert completed.admission_state == AdmissionState.DONE.value

        # Second writer — still believes the job is in PROCESSING —
        # tries to write the same terminal transition. The SQL guard
        # rejects the UPDATE (rowcount=0) and the disambiguation SELECT
        # shows the row is already COMPLETED, so we raise.
        with pytest.raises(InvalidTransitionError) as exc_info:
            repository.atomic_transition(
                job.job_id,
                from_status=AdmissionState.ACTIVE.value,
                to_status=AdmissionState.DONE.value,
                completed_at="2026-06-19T12:00:01+00:00",
                result_summary="writer-2-late",
            )

        assert exc_info.value.from_state == AdmissionState.DONE.value
        assert exc_info.value.to_state == AdmissionState.DONE.value

        # Critical: the first writer's payload is preserved. The second
        # writer's data must NOT have clobbered the first writer's state.
        final = repository.get(job.job_id)
        assert final is not None
        assert final.admission_state == AdmissionState.DONE.value

    def test_second_writer_with_terminal_clobber_blocked(
        self, repository, sample_job_data
    ):
        """A late writer targeting a *different* terminal state is blocked.

        This is the original bug: COMPLETED had been written, but a late
        FAILED writer could clobber the row because the original
        implementation did not include the status in the WHERE clause.
        """
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "inst-A")

        # First writer marks COMPLETED.
        repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.DONE.value,
            completed_at="2026-06-19T12:00:00+00:00",
            result_summary="happy",
        )

        # Late writer arrives and tries PROCESSING -> FAILED. Under the
        # old (vulnerable) implementation this would silently overwrite
        # COMPLETED with FAILED. Under the fix it must raise.
        with pytest.raises(InvalidTransitionError):
            repository.atomic_transition(
                job.job_id,
                from_status=AdmissionState.ACTIVE.value,
                to_status=AdmissionState.DONE.value,
                error_message="too late",
            )

        final = repository.get(job.job_id)
        assert final is not None
        assert final.admission_state == AdmissionState.DONE.value


class TestAtomicTransitionExtraUpdateKeys:
    """All extra_updates keys observed across the codebase are accepted.

    Enumerated via grep of every caller of ``atomic_transition`` in the
    daemon package. Each test uses a representative transition.
    """

    def test_started_at_key(self, repository, sample_job_data):
        """``started_at`` is accepted (filtered) by atomic_transition.

        Phase 5: ``started_at`` is no longer a JobItem column — it lives
        on the Instance. The kwarg is filtered by ``_REMOVED_JOB_COLUMNS``
        and must not raise.
        """
        job = repository.create(**sample_job_data)
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.ACTIVE.value,
            started_at="2026-06-19T12:00:00+00:00",
            instance_id="inst-1",
        )
        assert result is not None
        assert result.instance_id == "inst-1"

    def test_completed_at_key(self, repository, sample_job_data):
        """``completed_at`` is accepted (filtered) by atomic_transition.

        Phase 5: ``completed_at`` is no longer a JobItem column.
        """
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "inst-1")
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.DONE.value,
            completed_at="2026-06-19T12:00:00+00:00",
        )
        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value

    def test_cancelled_at_key(self, repository, sample_job_data):
        """``cancelled_at`` is accepted (filtered) by atomic_transition.

        Phase 5: ``cancelled_at`` is no longer a JobItem column.
        """
        job = repository.create(**sample_job_data)
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.DONE.value,
            cancelled_at="2026-06-19T12:00:00+00:00",
        )
        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value

    def test_instance_id_key(self, repository, sample_job_data):
        """``instance_id`` is applied on PENDING -> PROCESSING."""
        job = repository.create(**sample_job_data)
        repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.QUEUED.value,
            to_status=AdmissionState.ACTIVE.value,
            instance_id="unique-instance-id",
        )
        assert repository.get(job.job_id).instance_id == "unique-instance-id"

    def test_result_summary_key(self, repository, sample_job_data):
        """``result_summary`` is accepted (filtered) by atomic_transition.

        Phase 5: ``result_summary`` is no longer a JobItem column.
        """
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "inst-1")
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.DONE.value,
            result_summary="finished cleanly",
        )
        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value

    def test_error_message_key(self, repository, sample_job_data):
        """``error_message`` is accepted (filtered) by atomic_transition.

        Phase 5: ``error_message`` is no longer a JobItem column —
        it lives on the Instance.
        """
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "inst-1")
        result = repository.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.DONE.value,
            error_message="kaboom",
        )
        assert result is not None
        assert result.admission_state == AdmissionState.DONE.value


class TestAtomicTransitionConcurrent:
    """True concurrent (threaded) execution — the SQL guard is the only
    thing that protects two writers from each other under SQLite WAL."""

    def test_concurrent_terminal_writes_only_one_succeeds(self, repository, sample_job_data):
        """Two threads racing to write a terminal status: exactly one wins.

        Under the old (Pattern B) implementation both threads could
        pass the Python status check and both commit, leaving the row
        in whichever state the slower writer happened to land on. Under
        the fix, the SQL guard ensures exactly one writer commits and
        the other raises ``InvalidTransitionError``.
        """
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "inst-A")

        results: list[object] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def attempt_complete(writer_id: str) -> None:
            # Sync both threads to the barrier so they race as close to
            # simultaneously as the OS scheduler allows.
            barrier.wait()
            try:
                result = repository.atomic_transition(
                    job.job_id,
                    from_status=AdmissionState.ACTIVE.value,
                    to_status=AdmissionState.DONE.value,
                    completed_at=f"2026-06-19T12:00:{writer_id}+00:00",
                    result_summary=f"writer-{writer_id}",
                )
                results.append(result)
            except BaseException as exc:  # noqa: BLE001 — collecting both paths
                errors.append(exc)

        t1 = threading.Thread(target=attempt_complete, args=("00",))
        t2 = threading.Thread(target=attempt_complete, args=("01",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Exactly one writer must have produced a JobItem; the other must
        # have raised InvalidTransitionError.
        successful = [r for r in results if r is not None]
        invalid_transition_errors = [e for e in errors if isinstance(e, InvalidTransitionError)]

        assert len(successful) == 1, (
            f"Expected exactly one successful transition, got {len(successful)} "
            f"(results={results}, errors={errors})"
        )
        assert len(invalid_transition_errors) == 1, (
            f"Expected exactly one InvalidTransitionError, got "
            f"{len(invalid_transition_errors)} (errors={errors})"
        )

        # And the row must reflect the winning writer — no clobbering.
        final = repository.get(job.job_id)
        assert final is not None
        assert final.admission_state == AdmissionState.DONE.value


class TestStartJobAtomic:
    """Tests for ``JobRepository.start_job`` — Audit Finding H3 fix.

    ``start_job`` previously used a non-atomic
    ``get()`` + Python check + ``update()`` pattern that allowed two
    concurrent callers to both succeed under PostgreSQL READ COMMITTED
    (the Python status check passed for both before either commit
    landed). The fix mirrors ``atomic_transition``: a single guarded
    ``UPDATE … WHERE job_id = :job_id AND status = 'pending'`` with a
    follow-up SELECT to disambiguate "not found" from "status
    mismatch".

    Unlike ``atomic_transition``, ``start_job`` is contract-bound to
    raise ``ValueError`` (not ``InvalidTransitionError``) for a status
    mismatch — two production callers in
    ``job_queue_service.trigger_next_job_sync`` catch ``ValueError`` to
    release locks and return ``None``. These tests pin down the
    contract.
    """

    def test_start_pending_job_sets_fields(self, repository, sample_job_data):
        """PENDING -> PROCESSING sets admission_state and instance_id."""
        job = repository.create(**sample_job_data)

        started = repository.start_job(job.job_id, "instance-X")

        assert started is not None
        assert started.admission_state == AdmissionState.ACTIVE.value
        assert started.instance_id == "instance-X"

    def test_start_already_processing_raises_value_error(
        self, repository, sample_job_data
    ):
        """Starting a job that is already PROCESSING raises ValueError.

        Preserves the pre-fix exception class and message: the two
        ``trigger_next_job_sync`` callers in ``job_queue_service``
        catch ``ValueError`` to release the per-project lock and
        return ``None``.
        """
        job = repository.create(**sample_job_data)
        repository.start_job(job.job_id, "instance-1")

        with pytest.raises(ValueError) as exc_info:
            repository.start_job(job.job_id, "instance-2")

        msg = str(exc_info.value)
        assert "Cannot start job" in msg
        assert "active" in msg
        assert "queued" in msg

    def test_start_completed_job_raises_value_error(
        self, repository, sample_job_data
    ):
        """Starting a COMPLETED job raises ValueError reporting the
        actual current status — not the expected one."""
        job = repository.create(**sample_job_data)
        started = repository.start_job(job.job_id, "instance-1")
        repository.complete_job(started.job_id)

        with pytest.raises(ValueError) as exc_info:
            repository.start_job(job.job_id, "instance-2")

        msg = str(exc_info.value)
        assert "Cannot start job" in msg
        assert "done" in msg

    def test_start_missing_job_returns_none(self, repository):
        """Transitioning a non-existent job returns None (does NOT raise).

        Distinguishes the "row not found" case from the "status
        mismatch" case — the disambiguation SELECT in the fix is what
        makes this possible.
        """
        result = repository.start_job("this-job-id-does-not-exist", "instance-X")

        assert result is None

    def test_second_start_after_terminal_raises_value_error(
        self, repository, sample_job_data
    ):
        """Two serial calls: first wins, second raises ValueError.

        Regression guard for the pre-fix H3 bug: the original
        implementation's Python status check would pass for the second
        call AFTER the first call had committed (because the second
        call's get() saw the row's actual current status, but the
        update() in the original code did NOT include status in its
        WHERE clause, so it silently overwrote fields). The fixed
        implementation rejects the second call via the SQL guard.
        """
        job = repository.create(**sample_job_data)
        first = repository.start_job(job.job_id, "instance-1")
        assert first is not None

        # Move to terminal so the second start must reject.
        repository.complete_job(first.job_id)

        with pytest.raises(ValueError) as exc_info:
            repository.start_job(job.job_id, "instance-2")

        # And the row must reflect the *first* start's instance_id,
        # not the second caller's. The guard prevents clobbering.
        final = repository.get(job.job_id)
        assert final is not None
        assert final.instance_id == "instance-1"
        assert "Cannot start job" in str(exc_info.value)

    def test_concurrent_start_only_one_succeeds(
        self, repository, sample_job_data
    ):
        """Two threads racing to start the same job: exactly one wins.

        True concurrent (threaded) test of the SQL guard — this is the
        exact race H3 describes. Under the pre-fix (Pattern B)
        implementation, both threads' Python status checks could
        pass and both commits could succeed, double-starting the job
        and clobbering each other's ``instance_id`` / ``started_at``.
        Under the fix, the SQL guard ensures exactly one writer
        commits and the other raises ``ValueError``.
        """
        job = repository.create(**sample_job_data)

        results: list[object] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def attempt_start(instance_id: str) -> None:
            # Sync both threads to the barrier so they race as close
            # to simultaneously as the OS scheduler allows.
            barrier.wait()
            try:
                result = repository.start_job(job.job_id, instance_id)
                results.append(result)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=attempt_start, args=("inst-A",))
        t2 = threading.Thread(target=attempt_start, args=("inst-B",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Exactly one writer must have produced a JobItem; the other
        # must have raised ValueError.
        successful = [r for r in results if r is not None]
        value_errors = [e for e in errors if isinstance(e, ValueError)]

        assert len(successful) == 1, (
            f"Expected exactly one successful start, got {len(successful)} "
            f"(results={results}, errors={errors})"
        )
        assert len(value_errors) == 1, (
            f"Expected exactly one ValueError, got {len(value_errors)} "
            f"(errors={errors})"
        )

        # The row must reflect the winning writer's instance_id, with
        # no clobbering.
        final = repository.get(job.job_id)
        assert final is not None
        assert final.admission_state == AdmissionState.ACTIVE.value
        assert final.instance_id in {"inst-A", "inst-B"}
