"""Unit + wiring tests for the F16 fix.

Bug F16: when ``WorkResolverService`` is unwired / unreachable, four
production fallback paths derived ``status`` from the lossy
``_ADMISSION_TO_LEGACY_STATUS`` map WITHOUT consulting
``terminal_reason``. The map collapses ``done → completed`` regardless
of the discriminator, so a ``done`` job with
``terminal_reason='failed'`` mis-reported as ``"completed"``.

Fix: introduce ``_derive_legacy_status`` in
``daemon.services.work_status`` and route all four fallback paths
through it:

* ``daemon.routers.jobs_crud._job_to_response`` (legacy fallback when
  ``work_record is None``)
* ``daemon.routers.jobs_management.retry_job`` (fallback when the
  resolver is unreachable)
* ``daemon.routers.dlq.replay_dlq_item`` (post-replay status
  projection)
* ``daemon.tools.job_queue.job_create`` (watch-limit error path)

This file covers:

* The helper itself — table-driven tests over the full
  ``(admission_state, terminal_reason) → status`` matrix (the core
  logic).
* ``_job_to_response`` legacy-fallback wiring — direct invocation
  with a synthesised JobItem + ``work_record=None`` proves the
  helper is consulted (the helper is pure, the fallback branch in
  ``_job_to_response`` is the most-trafficked site, and a pure
  function is cheap to exercise).
* ``dlq.replay_dlq_item`` wiring — a focused endpoint test that
  mocks ``DeadLetterService.replay_from_dlq`` to return a
  ``done + terminal_reason='failed'`` JobItem and asserts the
  response carries ``status='failed'`` (not the lossy
  ``'completed'``). The other two wiring sites
  (``jobs_management.retry_job`` and
  ``tools/job_queue.job_create``) are mechanical 1-line swaps and
  are covered indirectly by the helper unit tests + the
  existing endpoint test suites
  (``tests/job_queue/test_job_retry_dlq.py``,
  ``tests/test_job_queue_tools.py``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.job_queue.dead_letter_repository import (
    DeadLetterRepository,
    set_dead_letter_repository,
)
from daemon.repositories.job_queue.models import (
    AdmissionState,
    DeadLetterItem,
    JobItem,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.routers.dlq import router as dlq_router
from daemon.routers.dlq import set_dead_letter_service
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.work_status import _derive_legacy_status


# =============================================================================
# Unit tests for the helper — the core logic
# =============================================================================


class TestDeriveLegacyStatusHelper:
    """``_derive_legacy_status`` — the shared status-derivation helper.

    Mirrors the F3 priority chain in
    ``WorkResolverService._job_to_record`` (Phase 2 defer-seam
    bugfix), applied directly to the JobItem row so the four legacy
    fallback paths (which bypass the resolver) get the same
    discriminator-aware behaviour.
    """

    @pytest.mark.parametrize(
        "admission_state, terminal_reason, expected",
        [
            # ── done + terminal_reason — the F16 discriminator ──
            # The five terminal_reason values that exercise the F16/Fix B
            # discriminator. Pre-F16, all of these would surface as
            # ``"completed"`` because the raw map collapses
            # ``done → completed``.
            ("done", "completed", "completed"),
            ("done", "failed", "failed"),
            ("done", "cancelled", "cancelled"),
            # F16: ``aborted`` collapses onto ``"cancelled"`` per the
            # canonical vocabulary (``_STATUS_CANONICAL_MAP`` in
            # ``work_status.py``). The work-record surface has no
            # distinct ``aborted`` state — an aborted job (killed by
            # its parent's instance-terminate cascade) is
            # semantically a cancellation.
            ("done", "aborted", "cancelled"),
            # Fix B: system retirement of a legacy message mirror is
            # likewise a cancellation, not a terminal failure.
            ("done", "orphan_retired", "cancelled"),
            # ── done + NULL terminal_reason — pre-7c backward compat ──
            # The legacy map's lossy ``done → completed`` value must
            # still be returned for pre-7c rows where the
            # ``terminal_reason`` column did not exist.
            ("done", None, "completed"),
            # ── non-done states — terminal_reason is not consulted ──
            # ``queued`` / ``active`` / ``dead`` map cleanly onto the
            # legacy vocabulary; ``terminal_reason`` is never
            # consulted because the terminal-write boundary
            # (``JobQueueService._finalize_terminal``) always pairs
            # ``active → done`` with a ``terminal_reason`` write and
            # ``dead`` is a separate queue endpoint (no
            # discriminator).
            ("queued", None, "pending"),
            ("queued", "failed", "pending"),     # terminal_reason ignored
            ("active", None, "processing"),
            ("active", "cancelled", "processing"),
            ("dead", None, "dead_letter"),
            ("dead", "failed", "dead_letter"),
            # ── unknown admission_state — defensive fallback ──
            # Future admission_state values the map has not been
            # taught about fall through to ``"pending"``, matching
            # the pre-F16 behaviour.
            ("unknown_state", None, "pending"),
        ],
    )
    def test_derives_status_for_each_combination(
        self, admission_state, terminal_reason, expected
    ):
        """Every ``(admission_state, terminal_reason)`` pair produces
        the correct legacy status string.
        """
        assert _derive_legacy_status(admission_state, terminal_reason) == expected

    def test_done_with_empty_string_terminal_reason_falls_through_to_map(self):
        """Empty-string ``terminal_reason`` is falsy — the helper
        treats it the same as ``None`` and falls through to the lossy
        map (``"completed"``).

        Matches the F3 fix's ``if terminal_reason:`` check on
        ``work_resolver._job_to_record`` (line 1302). Pre-7c rows
        can have empty string in some migration scenarios; the
        legacy map value is the safe backward-compatible answer and
        keeps the helper aligned with the resolver's discriminator
        gate.
        """
        assert _derive_legacy_status("done", "") == "completed"

    def test_done_with_completed_terminal_reason_does_not_collapse(self):
        """Explicit ``terminal_reason='completed'`` (non-NULL) takes
        the discriminator branch — the result is ``"completed"`` via
        canonicalisation, NOT via the lossy map fallback.

        This is the regression-prevention test: before F16, the raw
        map also returned ``"completed"`` for this case, so the bug
        was hidden by coincidence. After F16, the helper goes
        through ``canonicalize_status`` (a no-op for
        ``"completed"``) — the externally observable behaviour is
        identical but the code path is discriminator-aware.
        """
        # Both pre-F16 (raw map) and post-F16 (helper) produce
        # ``"completed"``. Asserting here locks the public contract
        # so a future refactor of the helper cannot silently regress
        # ``done + completed`` to a different value.
        assert _derive_legacy_status("done", "completed") == "completed"

    def test_helper_matches_canonicalize_status_for_terminal_reasons(self):
        """For all known ``terminal_reason`` values, the helper
        matches ``canonicalize_status(terminal_reason)`` when
        ``admission_state == 'done'`` — i.e. the helper is just a
        guarded wrapper around ``canonicalize_status`` for the
        ``done`` branch.
        """
        from daemon.services.work_status import canonicalize_status

        for tr in ("completed", "failed", "cancelled", "aborted"):
            assert _derive_legacy_status("done", tr) == canonicalize_status(tr)


# =============================================================================
# Wiring test for jobs_crud._job_to_response (legacy fallback path)
# =============================================================================
#
# ``_job_to_response`` is the workhorse fallback path — when the
# resolver is unreachable (older tests, partial wirings, resolver
# raises), ``work_record is None`` and the function derives ``status``
# directly from the JobItem row. This is the test that proves the F16
# helper is actually consulted at the call site (not bypassed via a
# leftover reference to the raw map).


def _make_job_item(
    *,
    admission_state: str = AdmissionState.DONE.value,
    terminal_reason: str | None = "failed",
    job_id: str | None = None,
) -> JobItem:
    """Build a JobItem row with the given ``(admission_state,
    terminal_reason)`` pair — used as the input to each fallback
    path test.
    """
    return JobItem(
        job_id=job_id or f"jid-{uuid.uuid4().hex[:8]}",
        agent_id="developer",
        agent_dir="/tmp/agents/developer",
        message="test message",
        source="api",
        project_id="test-project",
        priority=5,
        admission_state=admission_state,
        instance_id=None,
        created_at=datetime.now(timezone.utc).isoformat(),
        deleted_at=None,
        job_metadata={},
        terminal_reason=terminal_reason,
    )


class TestJobsCrudLegacyFallback:
    """F16 wiring: ``_job_to_response`` legacy fallback path
    (``work_record is None``) consults ``_derive_legacy_status``.

    Before F16, the fallback used ``_ADMISSION_TO_LEGACY_STATUS.get(
    admission_state, "pending")`` directly — which collapsed every
    ``done`` row onto ``"completed"`` regardless of
    ``terminal_reason``. After F16, the fallback routes through
    ``_derive_legacy_status`` so ``done + 'failed'`` reports
    ``"failed"``.
    """

    def test_done_with_failed_terminal_reason_reports_failed(self):
        """``done + terminal_reason='failed'`` → ``status='failed'``
        on the legacy fallback path.
        """
        from daemon.routers.jobs_crud import _job_to_response

        job = _make_job_item(
            admission_state=AdmissionState.DONE.value,
            terminal_reason="failed",
        )
        response = _job_to_response(job, work_record=None)

        assert response.status == "failed"
        assert response.terminal_reason == "failed"
        assert response.admission_state == AdmissionState.DONE.value

    def test_done_with_cancelled_terminal_reason_reports_cancelled(self):
        """``done + terminal_reason='cancelled'`` → ``status='cancelled'``."""
        from daemon.routers.jobs_crud import _job_to_response

        job = _make_job_item(
            admission_state=AdmissionState.DONE.value,
            terminal_reason="cancelled",
        )
        response = _job_to_response(job, work_record=None)

        assert response.status == "cancelled"

    def test_done_with_aborted_terminal_reason_reports_cancelled(self):
        """``done + terminal_reason='aborted'`` → ``status='cancelled'``
        (canonical vocabulary collapse — see ``_STATUS_CANONICAL_MAP``).
        """
        from daemon.routers.jobs_crud import _job_to_response

        job = _make_job_item(
            admission_state=AdmissionState.DONE.value,
            terminal_reason="aborted",
        )
        response = _job_to_response(job, work_record=None)

        assert response.status == "cancelled"

    def test_done_with_null_terminal_reason_still_reports_completed(self):
        """Backward compat: ``done + terminal_reason=NULL`` →
        ``status='completed'`` (the legacy map value).

        This is the pre-Phase-7c behaviour preserved for rows where
        the ``terminal_reason`` column did not exist or was not
        backfilled.
        """
        from daemon.routers.jobs_crud import _job_to_response

        job = _make_job_item(
            admission_state=AdmissionState.DONE.value,
            terminal_reason=None,
        )
        response = _job_to_response(job, work_record=None)

        assert response.status == "completed"

    def test_queued_with_terminal_reason_reports_pending(self):
        """``queued + terminal_reason=*`` → ``status='pending'``
        regardless of ``terminal_reason``.

        ``terminal_reason`` is only consulted for ``done`` rows;
        non-terminal admission states fall through to the map.
        Stamping ``terminal_reason`` on a ``queued`` row would be a
        production bug (the terminal-write boundary always pairs an
        ``active → done`` transition with a ``terminal_reason``
        write), but the helper handles it defensively.
        """
        from daemon.routers.jobs_crud import _job_to_response

        job = _make_job_item(
            admission_state=AdmissionState.QUEUED.value,
            terminal_reason="failed",   # ignored
        )
        response = _job_to_response(job, work_record=None)

        assert response.status == "pending"


# =============================================================================
# Wiring test for dlq.replay_dlq_item (post-replay status projection)
# =============================================================================
#
# The DLQ replay endpoint returns a ``DLQReplayResponse`` whose
# ``status`` field is derived from the post-replay JobItem. Before
# F16 this used the raw map; after F16 it routes through
# ``_derive_legacy_status``. Post-replay the admission_state is
# typically ``queued`` (the replay resets the job), but the helper
# is defensive against any future code path that surfaces a
# ``done`` JobItem with a ``terminal_reason`` here.


@pytest.fixture
def engine():
    """In-memory SQLite engine for the DLQ endpoint fixture."""
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
def dlq_repository(engine):
    return DeadLetterRepository(engine)


@pytest.fixture
def job_repository(engine):
    return JobRepository(engine)


@pytest.fixture
def dlq_service(job_repository, dlq_repository):
    return DeadLetterService(
        job_repository=job_repository,
        dlq_repository=dlq_repository,
    )


@pytest.fixture
def dlq_test_app(dlq_service, dlq_repository):
    """FastAPI app wired with the DLQ router + singleton service.

    ``app.state.manager`` is explicitly set to a stub whose
    ``is_write_paused`` is ``False`` (matches the production
    post-migration posture) so the write-pause guard at the top of
    ``replay_dlq_item`` doesn't 503 the endpoint. The autouse
    ``_ensure_app_state_manager`` fixture in ``tests/conftest.py``
    would provide a similar default, but setting it explicitly here
    makes the test self-contained and resistant to upstream fixture
    changes.
    """
    app = FastAPI()
    app.include_router(dlq_router)
    set_dead_letter_service(dlq_service)
    set_dead_letter_repository(dlq_repository)
    app.state.manager = MagicMock(is_write_paused=False)
    yield app
    # Reset singletons to avoid bleed into other tests.
    set_dead_letter_service(None)  # type: ignore[arg-type]
    set_dead_letter_repository(None)  # type: ignore[arg-type]


@pytest.fixture
def dlq_client(dlq_test_app):
    with TestClient(dlq_test_app) as client:
        yield client


class TestDLQReplayStatusFallback:
    """F16 wiring: ``dlq.replay_dlq_item`` post-replay status
    projection consults ``_derive_legacy_status``.

    The replay endpoint returns ``DLQReplayResponse`` whose
    ``status`` is derived from the post-replay JobItem. The test
    mocks ``DeadLetterService.replay_from_dlq`` to return a JobItem
    with ``admission_state='done'`` + a specific
    ``terminal_reason`` and asserts the response carries the
    discriminator-aware status.
    """

    @pytest.mark.parametrize(
        "terminal_reason, expected_status",
        [
            ("failed", "failed"),
            ("cancelled", "cancelled"),
            ("aborted", "cancelled"),     # canonical collapse
            ("completed", "completed"),
            (None, "completed"),          # pre-7c backward compat
        ],
    )
    def test_replay_response_status_discriminates_by_terminal_reason(
        self,
        dlq_service,
        dlq_repository,
        dlq_client,
        terminal_reason,
        expected_status,
    ):
        """Post-replay status reflects ``terminal_reason`` via the
        F16 helper.
        """
        # Seed a DLQ item so the endpoint can look it up. The
        # endpoint calls ``service.get_dlq(dlq_id)`` first and
        # returns 404 if the row doesn't exist — so the item must
        # actually be persisted via the repository.
        dlq_item = DeadLetterItem(
            dlq_id="dlq-f16-test",
            job_id="job-f16-test",
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="test message",
            source="api",
            project_id="project-f16",
            queue_id="queue-f16",  # NOT NULL in the schema
            priority=5,
            error_message="Connection timeout",
            retry_count=3,
            failed_at=datetime.now(timezone.utc).isoformat(),
            moved_to_dlq_at=datetime.now(timezone.utc).isoformat(),
            reason="MAX_RETRIES",
            metadata_json={},
        )
        dlq_repository.enqueue(dlq_item)

        # Build the post-replay JobItem directly — the actual replay
        # logic is not under test, only the status projection.
        replayed_job = _make_job_item(
            admission_state=AdmissionState.DONE.value,
            terminal_reason=terminal_reason,
            job_id="job-f16-test",
        )

        # Patch the service method to return our synthesised job.
        # The endpoint uses ``service.replay_from_dlq`` to perform
        # the actual replay; we replace it so the test is
        # deterministic.
        dlq_service.replay_from_dlq = MagicMock(return_value=replayed_job)

        response = dlq_client.post(
            "/projects/project-f16/dlq/dlq-f16-test/replay",
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body["status"] == expected_status, (
            f"terminal_reason={terminal_reason!r} expected "
            f"status={expected_status!r}, got {body['status']!r}"
        )
        assert body["job_id"] == "job-f16-test"


# =============================================================================
# Wiring test for job_queue_service._reconcile_terminal_watches_legacy
# (the 5th F16 site — discovered after the original four-site fix)
# =============================================================================
#
# ``JobQueueService._reconcile_terminal_watches_legacy`` is the
# JobItem-only reconcile path used when ``WorkResolverService`` is not
# wired (older test doubles, partial init). It walks every active watch,
# fetches the JobItem, and notifies watchers when the row is terminal
# (``admission_state`` in ``{done, dead}``). Pre-F16, the legacy status
# string was derived via ``_ADMISSION_TO_LEGACY_STATUS.get(...)``
# directly — which collapsed every ``done`` row onto ``"completed"``
# regardless of ``terminal_reason``. Post-F16, it routes through
# ``_derive_legacy_status``.
#
# The test wires a real ``JobWatcherRepository`` over an in-memory
# SQLite engine, then stubs ``JobRepository.get`` so we can drive the
# reconcile helper with synthesised JobItem rows carrying specific
# ``(admission_state, terminal_reason)`` pairs — proving the F16 helper
# is consulted at the call site (not bypassed via a leftover reference
# to the raw map).


@pytest.fixture
def f16_watcher_repository(engine):
    """JobWatcherRepository bound to the in-memory engine from the
    ``engine`` fixture (defined earlier in this file)."""
    from daemon.repositories.job_queue.watcher_repository import (
        JobWatcherRepository,
    )

    return JobWatcherRepository(engine)


@pytest.fixture
def f16_queue_service(f16_watcher_repository):
    """JobQueueService wired with a real ``JobWatcherRepository``
    (so ``get_all_active_watches`` returns persisted rows) and a stub
    ``JobRepository`` (so we can return synthesised JobItem rows with
    custom ``(admission_state, terminal_reason)`` pairs without
    touching the repository's ``create`` API — the production
    ``create`` only writes ``admission_state=QUEUED``).

    ``_work_resolver`` is intentionally NOT set so the legacy reconcile
    path (``_reconcile_terminal_watches_legacy``) is the one exercised.
    ``_instance_manager`` is a MagicMock — the reconcile helper invokes
    ``notify_watchers``, which (in the legacy fallback branch)
    eventually calls ``instance_manager.enqueue_message``, but the test
    patches ``notify_watchers`` directly so the mock's
    ``enqueue_message`` is never actually invoked.
    """
    from daemon.services.job_queue_service import JobQueueService
    from unittest.mock import MagicMock

    service = JobQueueService(
        repository=MagicMock(),  # stubbed per-test via .get side_effect
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=MagicMock(),
    )
    service.set_watcher_repo(f16_watcher_repository)
    # Deliberately NOT calling set_work_resolver — the legacy path is
    # the one under test.
    return service


class TestReconcileTerminalWatchesLegacy:
    """F16 wiring: ``_reconcile_terminal_watches_legacy`` derives the
    legacy status via ``_derive_legacy_status`` (5th production site).

    The reconcile helper fires when a watched JobItem has reached a
    terminal ``admission_state``. Pre-F16, the lossy raw map collapsed
    every ``done`` row onto ``"completed"`` regardless of
    ``terminal_reason``. Post-F16, the helper routes through
    ``_derive_legacy_status`` so ``done + 'failed'`` reports
    ``"failed"``.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "admission_state, terminal_reason, expected_status",
        [
            # ── done + terminal_reason — the F16 discriminator ──
            # Pre-F16 all four cases would report ``"completed"``.
            ("done", "completed", "completed"),
            ("done", "failed", "failed"),
            ("done", "cancelled", "cancelled"),
            ("done", "aborted", "cancelled"),  # canonical collapse
            # ── done + NULL terminal_reason — pre-7c backward compat ──
            ("done", None, "completed"),
            # ── dead — separate queue endpoint, no discriminator ──
            ("dead", None, "dead_letter"),
        ],
    )
    async def test_reconcile_legacy_consults_helper(
        self,
        f16_watcher_repository,
        f16_queue_service,
        admission_state,
        terminal_reason,
        expected_status,
    ):
        """``_reconcile_terminal_watches_legacy`` notifies watchers
        with the F16-discriminator-aware legacy status.

        The test patches ``notify_watchers`` on the service instance
        so the side-effecting instance_manager call is bypassed — only
        the ``status`` argument is captured and asserted on. This
        isolates the helper-routing concern from the notification
        machinery.
        """
        from unittest.mock import patch

        # Build a JobItem with the (admission_state, terminal_reason)
        # pair under test. The repository stub returns this row when
        # ``get(job_id)`` is called inside the reconcile loop.
        job = _make_job_item(
            admission_state=admission_state,
            terminal_reason=terminal_reason,
        )

        # Stub JobRepository.get so the reconcile loop finds the
        # synthesised JobItem by job_id.
        f16_queue_service._repository.get = MagicMock(return_value=job)

        # Register a watcher so the reconcile loop has something to
        # walk. The watcher instance_id is a placeholder — the test
        # patches notify_watchers so it never reaches enqueue_message.
        f16_watcher_repository.add_watch(
            job_id=job.job_id,
            instance_id="instance-test",
            watch_events=["completed", "failed", "cancelled", "dead_letter"],
        )

        # Capture notify_watchers invocations. ``reconcile_terminal_watches``
        # routes to the legacy path because ``_work_resolver`` is None.
        captured: list[tuple[str, str, str | None]] = []

        async def capture(job_id, status, error=None, progress=None):
            captured.append((job_id, status, error))
            return 1

        with patch.object(
            f16_queue_service, "notify_watchers", side_effect=capture
        ):
            reconciled = await f16_queue_service.reconcile_terminal_watches()

        # Sanity: one watch was found and reconciled.
        assert reconciled == 1, f"expected 1 reconcile, got {reconciled}"
        # The captured status reflects the F16 helper, not the lossy
        # raw map. Pre-F16 every ``done`` row would have surfaced as
        # ``"completed"``.
        assert len(captured) == 1
        job_id, status, _ = captured[0]
        assert job_id == job.job_id
        assert status == expected_status, (
            f"admission_state={admission_state!r}, "
            f"terminal_reason={terminal_reason!r} → "
            f"expected status={expected_status!r}, got {status!r}"
        )


# =============================================================================
# Module export sanity — make sure the helper is importable from the
# public surface (so future fallback paths can be wired in without
# reaching into private attributes).
# =============================================================================


class TestHelperExport:
    """Smoke test: the helper is importable from its public surface."""

    def test_helper_importable_from_work_status(self):
        from daemon.services.work_status import _derive_legacy_status
        assert callable(_derive_legacy_status)

    def test_helper_used_by_jobs_crud_module(self):
        """``daemon.routers.jobs_crud`` imports the helper — this
        catches accidental removal of the import in a future
        refactor."""
        import daemon.routers.jobs_crud as mod
        assert "_derive_legacy_status" in dir(mod) or any(
            getattr(getattr(mod, name, None), "__name__", "") == "_derive_legacy_status"
            for name in dir(mod)
        )

    def test_helper_used_by_jobs_management_module(self):
        import daemon.routers.jobs_management as mod
        assert any(
            getattr(getattr(mod, name, None), "__name__", "") == "_derive_legacy_status"
            for name in dir(mod)
        )

    def test_helper_used_by_dlq_module(self):
        import daemon.routers.dlq as mod
        assert any(
            getattr(getattr(mod, name, None), "__name__", "") == "_derive_legacy_status"
            for name in dir(mod)
        )

    def test_helper_used_by_job_queue_tool_module(self):
        import daemon.tools.job_queue as mod
        assert any(
            getattr(getattr(mod, name, None), "__name__", "") == "_derive_legacy_status"
            for name in dir(mod)
        )

    def test_helper_used_by_job_queue_service_module(self):
        """``daemon.services.job_queue_service`` is the 5th F16 migration
        site — it imports ``_derive_legacy_status`` at module load and
        uses it as the legacy-status fallback in the service layer
        response shaping. Catches accidental removal of the import in
        a future refactor of the service module.
        """
        import daemon.services.job_queue_service as mod
        assert any(
            getattr(getattr(mod, name, None), "__name__", "") == "_derive_legacy_status"
            for name in dir(mod)
        )