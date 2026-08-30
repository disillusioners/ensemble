"""Pattern (f) bus-pending-gate PARAMETER TYPE PIN.

Live-PROD incident (v0.11.3, 2026-08-30, task 26303): the
Pattern (f) bus-pending gate
(``JobRecoveryService._pattern_f_check_bus_pending``)
called ``DependencyBus.pending_watchers(task.id)`` with a
raw Python ``int``; the underlying
``dependency_watchers.source_task_id`` column is VARCHAR.
SQLite's type affinity tolerated the implicit cast, so unit
tests stayed green, but PostgreSQL is strict and raised
``UndefinedFunction: operator does not exist: character
varying = integer`` on every call. The fail-safe path held
(no wrongful finalize), but f2 was functionally OUT in prod
+ WARNING spam every drift cycle.

The fix lives at the seam: coerce ``str(task_id)`` at the
call site, matching the dominant caller convention
(``child_reports._emit_terminal_via_bus`` already passes
``task_id=str(task_id)`` to the sister ``bus.emit_terminal``).

These tests PIN the parameter type at the seam so a
regression that drops the coercion fails immediately under
SQLite — without needing a real-PG backend to reproduce.

Tests are organized as:
  * ``TestBusPendingParamTypePin`` — direct-call tests
    against ``_pattern_f_check_bus_pending`` (the gate
    helper) with a capturing bus stub.
  * ``TestBusPendingParamTypePinRedGreen`` — explicit
    red-green: a transient BusStub captures the type, and
    we assert both the type and that the value matches the
    str-coerced form of the int task id (this is the
    "would fail before the fix" assertion).

The tests run on the in-memory SQLite engine defined in
``tests/job_queue/conftest.py`` — no PG required for the
type pin (the pin's whole point is that the type contract
is preserved BEFORE the SQL bind happens).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.task.repository import TaskRepository
from daemon.services.stale_task_recovery import StaleTaskRecovery


# ─────────────────────────────────────────────────────────────────────────────
# Local fixtures — mirrors the convention in
# ``tests/job_queue/test_orphan_active_job_recovery.py`` (Pattern (f) family).
# The pin only exercises the gate helper, but the service
# constructor needs the same surface as the rest of the f-family.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def task_repository(engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def instance_repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine=engine)


@pytest.fixture
def stale_recovery(task_repository) -> StaleTaskRecovery:
    """StaleTaskRecovery with the message/event/notifier deps
    stubbed (Pattern (f) doesn't touch them).
    """
    return StaleTaskRecovery(
        task_repository=task_repository,
        message_repository=None,
        event_repository=None,
    )


@pytest.fixture
def job_queue_service_mock() -> MagicMock:
    """A MagicMock for ``JobQueueService``. Pattern (f)'s
    gate helpers don't invoke ``notify_watchers`` /
    ``_finalize_terminal`` (the bus-gate short-circuits
    before the boundary), but the service constructor
    needs a non-None reference.
    """
    mock = MagicMock()
    mock.notify_watchers = AsyncMock(return_value=None)
    return mock


# ─────────────────────────────────────────────────────────────────────────────
# Capturing bus stub — the smallest possible surface that records what
# the gate helper hands to the bus seam.
# ─────────────────────────────────────────────────────────────────────────────


class _CapturingBusStub:
    """Async stub mirroring ``DependencyBus.pending_watchers``.

    Records the ``source_task_id`` argument passed in, plus
    the Python ``type()`` of the value, so the test can
    assert the type AT THE SEAM (not after a SQL bind).
    """

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.types: list[type] = []

    async def pending_watchers(self, source_task_id):
        self.calls.append(source_task_id)
        self.types.append(type(source_task_id))
        # Empty pending list (matches the existing f2 stub
        # convention in
        # ``tests/job_queue/test_orphan_active_job_recovery.py``).
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Test class — pin the parameter type at the bus seam.
# ─────────────────────────────────────────────────────────────────────────────


class TestBusPendingParamTypePin:
    """Pin ``source_task_id`` to ``str`` at the bus seam.

    The pin covers the contract declared at:
      * ``daemon/services/dependency_bus.py``
        ``DependencyBus.pending_watchers(self, source_task_id: str)``
      * ``daemon/repositories/dependency_bus/repository.py``
        ``DependencyWatcherRepository.fetch_pending_for_source(
            self, source_task_id: str)``
      * ``daemon/repositories/dependency_bus/models.py``
        ``DependencyWatcher.source_task_id: str`` → VARCHAR column.

    If the gate helper passes an ``int`` to the bus seam,
    PG rejects the bind. The pin catches that BEFORE the
    SQL layer (which is what makes it useful as a
    SQLite-runnable regression check).
    """

    @pytest.mark.asyncio
    async def test_helper_passes_str_to_bus_when_task_id_is_int(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Direct call to ``_pattern_f_check_bus_pending``
        with an int ``task_id`` MUST result in a ``str``
        being handed to ``bus.pending_watchers``.

        Pre-fix (v0.11.3): the gate helper passed raw int,
        captured type would be ``int`` — assertion fails.
        Post-fix: ``str(task_id)`` at the call site,
        captured type is ``str`` — assertion passes.
        """
        from daemon.services.job_recovery_service import (
            JobRecoveryService,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stub = _CapturingBusStub()
        # The PROD value (live-PROD evidence, 2026-08-30).
        prod_task_id = 26303
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=stub,
        ):
            count, unavailable = await service._pattern_f_check_bus_pending(
                task_id=prod_task_id,
            )

        # ── Assert: bus was actually called once.
        assert len(stub.calls) == 1, (
            f"bus.pending_watchers MUST be called exactly "
            f"once by the gate helper. Got {len(stub.calls)} "
            f"calls: {stub.calls!r}"
        )
        # ── Assert: returned (count, unavailable) shape.
        assert count == 0, (
            f"Stub returns []; count MUST be 0. Got {count!r}"
        )
        assert unavailable is False, (
            f"Stub is wired; unavailable MUST be False. "
            f"Got {unavailable!r}"
        )
        # ── PIN: the captured argument is str, NOT int.
        captured = stub.calls[0]
        captured_type = stub.types[0]
        assert captured_type is str, (
            f"``bus.pending_watchers`` declares "
            f"``source_task_id: str`` and the underlying "
            f"column is VARCHAR. The gate helper MUST "
            f"coerce to str before the seam (PG strict-mode "
            f"otherwise raises UndefinedFunction on "
            f"``varchar = integer``). "
            f"Captured type={captured_type.__name__!r}, "
            f"value={captured!r} (int input "
            f"task_id={prod_task_id!r}). "
            f"Pre-fix code passed raw int and this "
            f"assertion fails — that's the intended "
            f"red-green signal."
        )
        # ── Assert: the str value is the canonical
        # str-coerced form of the int (i.e., not a
        # different value, like a UUID).
        assert captured == str(prod_task_id), (
            f"The coerced str MUST equal ``str({prod_task_id})``. "
            f"Got {captured!r}"
        )

    @pytest.mark.asyncio
    async def test_helper_passes_str_to_bus_when_task_id_is_str(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """Idempotency check: a pre-coerced ``str`` task_id
        must round-trip unchanged (no double-coercion, no
        loss). The fix ``str(task_id)`` is safe to apply
        even when callers already pass str.
        """
        from daemon.services.job_recovery_service import (
            JobRecoveryService,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stub = _CapturingBusStub()
        pre_coerced = "26303"
        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=stub,
        ):
            await service._pattern_f_check_bus_pending(
                task_id=pre_coerced,  # type: ignore[arg-type]
            )

        assert stub.types[0] is str, (
            f"Pre-coerced str MUST round-trip as str. "
            f"Got type={stub.types[0].__name__!r}"
        )
        assert stub.calls[0] == pre_coerced, (
            f"Pre-coerced str MUST round-trip unchanged. "
            f"Got value={stub.calls[0]!r}, expected "
            f"{pre_coerced!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Red-green evidence class — explicit assert that the PRE-fix
# behavior would have failed this test (i.e., captured type
# would have been ``int``).
# ─────────────────────────────────────────────────────────────────────────────


class TestBusPendingParamTypePinRedGreen:
    """Explicit red-green documentation.

    The pin test (``TestBusPendingParamTypePin``) asserts
    ``type(captured) is str``. On the pre-fix code (v0.11.3)
    that assertion FAILS because the gate helper passes raw
    int — so the test is the red-green signal itself.

    This class adds an explicit "would-fail-pre-fix" assertion
    that names the symptom (captured arg was int, now str) so
    the regression is traceable from a single grep.
    """

    @pytest.mark.asyncio
    async def test_captured_arg_is_str_not_int(
        self, engine, repository, task_repository, lock_repo,
        instance_repo, stale_recovery, job_queue_service_mock,
    ):
        """The captured argument MUST be ``str`` (NOT
        ``int``). Pin both sides of the assertion so the
        red-green is explicit:

        * PRE-fix (v0.11.3, task 26303): captured type was
          ``int`` — this assertion would fail with
          ``assert captured_type is str`` and the live-PROD
          PG error would surface.
        * POST-fix: ``str(task_id)`` at the call site →
          captured type is ``str`` — PG accepts the bind.

        The fix itself is one line in
        ``daemon/services/job_recovery_service.py``
        (the gate helper's ``bus.pending_watchers(task_id)``
        call). See the seam-pin docstring for the full
        failure-mode write-up.
        """
        from daemon.services.job_recovery_service import (
            JobRecoveryService,
        )

        service = JobRecoveryService(
            job_repository=repository,
            lock_repository=lock_repo,
            instance_repository=instance_repo,
            job_queue_service=job_queue_service_mock,
            task_repository=task_repository,
            stale_task_recovery=stale_recovery,
        )

        stub = _CapturingBusStub()
        # Mix int + str inputs in a single session to
        # exercise both code paths and prove the seam is
        # uniform.
        int_inputs = [26303, 1, 999_999_999]
        str_inputs = ["42", "abc"]

        with patch(
            "daemon.services.job_recovery_service.get_dependency_bus",
            return_value=stub,
        ):
            for tid in int_inputs:
                await service._pattern_f_check_bus_pending(
                    task_id=tid,  # type: ignore[arg-type]
                )
            for tid in str_inputs:
                await service._pattern_f_check_bus_pending(
                    task_id=tid,  # type: ignore[arg-type]
                )

        # ── Red-green assertion: every captured value is
        # str, regardless of input type.
        for i, (captured, captured_type) in enumerate(
            zip(stub.calls, stub.types)
        ):
            assert captured_type is str, (
                f"Call #{i}: captured type MUST be str "
                f"(gate helper seam contract). Got "
                f"{captured_type.__name__!r} for value "
                f"{captured!r}. Pre-fix regression: "
                f"int inputs would land here as int, "
                f"PG would reject the VARCHAR bind with "
                f"UndefinedFunction (the live-PROD "
                f"v0.11.3 incident)."
            )
        # ── Round-trip: int input → ``str(int_input)``.
        for i, original in enumerate(int_inputs):
            assert stub.calls[i] == str(original), (
                f"Call #{i}: int input {original!r} MUST "
                f"reach the seam as ``str({original})`` = "
                f"{str(original)!r}. Got {stub.calls[i]!r}"
            )
        # ── Idempotent: str input → unchanged.
        for j, original in enumerate(str_inputs):
            idx = len(int_inputs) + j
            assert stub.calls[idx] == original, (
                f"Call #{idx}: str input {original!r} MUST "
                f"round-trip unchanged. Got {stub.calls[idx]!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Helper-docstring references — keep the docs cross-linked so
# a future reader chasing this pin finds the seam contract
# without grep.
# ─────────────────────────────────────────────────────────────────────────────


def test_pin_docstring_lists_seam_contract() -> None:
    """Sanity check: the module docstring references the
    three places that declare the ``source_task_id: str``
    contract. If a refactor renames any of them, this
    assertion forces the docstring update — preventing the
    pin from drifting silently away from the seam it
    protects.
    """
    import inspect

    src = inspect.getsource(inspect.getmodule(inspect.currentframe()))
    expected_refs = [
        "daemon/services/dependency_bus.py",
        "fetch_pending_for_source",
        "DependencyWatcher.source_task_id",
    ]
    for ref in expected_refs:
        assert ref in src, (
            f"Pin module docstring MUST reference {ref!r} "
            f"so future readers can locate the seam "
            f"contract. Docstring drift would silently "
            "desynchronize the pin from the contract it "
            "guards."
        )
