"""Unit tests for the source-validation boundary on user-supplied ``source``.

The HTTP ``POST /jobs`` endpoint accepts a ``source`` field in its
``JobCreateRequest`` body. That field ultimately flows into
``JobItem.source`` (and from there into the message / job provenance
that gates dispatch-source selection — see
``daemon/services/instance_messaging.py:2280-2339``).

Internal callers must NOT be forgeable by an HTTP body. The internal
origins are reserved (see ``daemon.constants.RESERVED_SOURCE_PREFIXES``)
and rejected with 422 by ``POST /jobs`` when supplied by the user.

Coverage here:
    1. ``daemon.constants.RESERVED_SOURCE_PREFIXES`` — membership pin
       (same fork-prevention shape as the INJECTION_ELIGIBLE_STATUSES
       pin test in ``tests/unit/tools/test_instance_tools.py``).
    2. ``daemon.routers.jobs_crud.create_job`` rejects reserved
       prefixes while allowing legitimate custom user sources and the
       default ``"api"`` value.
    3. ``daemon.routers.messages.send_message`` (``POST /messages``) is
       NOT a user-supplied source vector — its source is hardcoded.
       Documented here so future readers do not mistakenly add the
       same check to ``messages.py`` and double-validate.

Pre-existing tests still pass:
    * ``tests/integration/test_job_create.py`` — uses default
      ``source='api'`` (omitted), must keep passing byte-identically.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Group 1 — Constants pin (fork-prevention)
# ---------------------------------------------------------------------------


class TestReservedSourcePrefixesConstant:
    """Pin the membership of ``daemon.constants.RESERVED_SOURCE_PREFIXES``.

    Same fork-prevention shape as the ``INJECTION_ELIGIBLE_STATUSES``
    pin test (``tests/unit/tools/test_instance_tools.py::
    test_injection_eligible_statuses_constant_exists``): the set is the
    single definition home, and no per-consumer fork may exist.
    """

    def test_reserved_source_prefixes_exist(self):
        """The set MUST live in ``daemon.constants`` and contain the
        internal origin families the dispatch-source guard
        (``daemon/services/instance_messaging.py:2280-2339``) treats as
        ``internal_report`` / ``internal_error_report`` /
        ``internal_agent`` / ``system:*``, plus the non-colon exact
        values ``cascade_resume`` / ``api_resume_fallback``."""
        from daemon.constants import RESERVED_SOURCE_PREFIXES

        # Internal families with colon-terminated prefixes — matched by
        # ``startswith`` so any concrete ``internal_report:<child>``
        # string is caught.
        assert "internal_report:" in RESERVED_SOURCE_PREFIXES
        assert "internal_error_report:" in RESERVED_SOURCE_PREFIXES
        assert "internal_agent:" in RESERVED_SOURCE_PREFIXES
        assert "system:" in RESERVED_SOURCE_PREFIXES

        # Non-colon exact values — matched by exact equality.
        assert "cascade_resume" in RESERVED_SOURCE_PREFIXES
        assert "api_resume_fallback" in RESERVED_SOURCE_PREFIXES

        # F2 P2.3 note: bare ``"api"`` is NOT reserved (it remains the
        # default + legitimate user origin). Verify it stays out.
        assert "api" not in RESERVED_SOURCE_PREFIXES

    def test_internal_invoke_and_wait_is_reserved(self):
        """``internal_invoke_and_wait:<parent_id>`` is stamped by the
        invoke_agent_and_wait tool (``daemon/utils.py:645``) and
        documented in ``daemon/tools/upgrade_journal.py:1070`` as a
        non-user origin. It MUST be in the reserved set."""
        from daemon.constants import RESERVED_SOURCE_PREFIXES

        assert "internal_invoke_and_wait:" in RESERVED_SOURCE_PREFIXES

    def test_helper_function_is_reserved_source(self):
        """The shared helper returns True for any reserved value and
        False for legitimate user sources / None."""
        from daemon.constants import is_reserved_source

        # Reserved families (prefix-matched).
        assert is_reserved_source("system:xyz") is True
        assert is_reserved_source("system:watchdog") is True
        assert is_reserved_source("internal_agent:abc") is True
        assert is_reserved_source("internal_report:abc:msg") is True
        assert is_reserved_source("internal_error_report:abc") is True
        assert is_reserved_source("internal_invoke_and_wait:p1") is True

        # Non-colon exact values (exact-matched).
        assert is_reserved_source("cascade_resume") is True
        assert is_reserved_source("api_resume_fallback") is True

        # Legitimate user origins — must NOT be flagged.
        assert is_reserved_source("api") is False
        assert is_reserved_source("telegram:user:1") is False
        assert is_reserved_source("webhook:gh-hook") is False
        assert is_reserved_source("scheduler") is False
        assert is_reserved_source("custom-app") is False

        # Edge cases — None and empty strings are NOT reserved
        # (internal callers may pass None; the validation helper
        # only fires on user-supplied string values).
        assert is_reserved_source(None) is False
        assert is_reserved_source("") is False


# ---------------------------------------------------------------------------
# Group 2 — HTTP boundary enforcement on POST /jobs
# ---------------------------------------------------------------------------


# Sentinel used to short-circuit the endpoint after our validation
# succeeds — the stub raises this from ``enqueue`` so the test can
# observe that the request reached the service (i.e. validation
# passed) without having to stub the downstream ``get_work`` /
# ``_job_to_response`` flow.
_SENTINEL_EXCEPTION = RuntimeError(
    "SENTINEL: source-validation boundary passed — test expects to "
    "catch this before downstream processing."
)


def _stub_enqueue_service():
    """Build a stub ``JobQueueService`` whose ``enqueue`` raises a
    sentinel exception. The test asserts the exception is reached
    (proving validation passed) or NOT reached (proving validation
    rejected the request)."""
    svc = MagicMock()
    svc.enqueue = AsyncMock(side_effect=_SENTINEL_EXCEPTION)
    return svc


@pytest.fixture
def create_job_test_app():
    """FastAPI app wired with ``jobs_crud`` router + stubbed manager.

    The boundary fires at the HTTP handler BEFORE the service is
    called, so we only need ``is_write_paused`` on the manager (the
    router's first guard).
    """
    app = FastAPI()
    from daemon.routers.jobs_crud import router as crud_router

    app.include_router(crud_router)
    app.state.manager = MagicMock(is_write_paused=False)
    yield app

    # Reset the singleton so the next test gets a fresh dependency.
    from daemon.routers.jobs_crud import get_job_queue_service

    get_job_queue_service.set_service(None)


@pytest.fixture
def create_job_client(create_job_test_app):
    with TestClient(create_job_test_app) as client:
        yield client


class TestCreateJobSourceBoundary:
    """``POST /jobs`` must reject reserved source prefixes at the HTTP
    boundary while still accepting legitimate custom user sources and
    the default ``api`` value."""

    # --- Reserved prefixes rejected with 422 -----------------------------

    def test_create_job_rejects_system_prefix(
        self, create_job_client
    ):
        """``source='system:xyz'`` must be rejected with 422. Internal
        callers do not pass this through HTTP — they bypass to
        ``enqueue_message`` directly."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = create_job_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
                "source": "system:xyz",
            },
        )

        assert resp.status_code == 422, resp.text
        body = resp.json()
        # The error envelope matches the router's existing 422
        # convention (JobValidationError-shaped detail). The source
        # field name appears in the detail message so the operator
        # can see WHICH value was rejected.
        assert "source" in str(body).lower()
        # Validation rejected — service was NEVER called.
        stub.enqueue.assert_not_called()

    def test_create_job_rejects_internal_agent_prefix(
        self, create_job_client
    ):
        """``source='internal_agent:xyz'`` must be rejected with 422.
        The colon-terminated family is matched by ``startswith`` so
        the concrete ``internal_agent:job_event:<work_id>:<status>``
        job-event pings (stamped by ``daemon/services/work_notifier.py:293``)
        are also blocked at the HTTP boundary."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = create_job_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
                "source": "internal_agent:job_event:abc:queued",
            },
        )

        assert resp.status_code == 422, resp.text
        stub.enqueue.assert_not_called()

    def test_create_job_rejects_internal_report_prefix(
        self, create_job_client
    ):
        """``source='internal_report:xyz'`` must be rejected with 422.
        The drain path stamps ``source='internal_report:<child_iid>'``
        directly (``daemon/graph.py:3169``,
        ``daemon/services/child_reports.py:2744``) — never through
        HTTP — so the boundary rejection does not affect those flows."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = create_job_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
                "source": "internal_report:abc:msg",
            },
        )

        assert resp.status_code == 422, resp.text
        stub.enqueue.assert_not_called()

    def test_create_job_rejects_cascade_resume_exact(
        self, create_job_client
    ):
        """``source='cascade_resume'`` (no colon) must be rejected with
        422. The value is stamped by ``manager._process_message_with_tracking``
        (``daemon/manager.py:9285``) and the watchover path
        (``daemon/services/watchover_service.py:676,722``) — never
        via HTTP. Boundary exact-match guard prevents collateral
        blocking of user values that merely ``startswith``-match."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = create_job_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
                "source": "cascade_resume",
            },
        )

        assert resp.status_code == 422, resp.text
        stub.enqueue.assert_not_called()

    def test_create_job_rejects_api_resume_fallback_exact(
        self, create_job_client
    ):
        """``source='api_resume_fallback'`` is stamped by the messages
        router at ``daemon/routers/messages.py:282`` (server-side
        cascade-resume fallback). It must NOT be forgeable by the
        user via POST /jobs. Exact-match guard."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = create_job_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
                "source": "api_resume_fallback",
            },
        )

        assert resp.status_code == 422, resp.text
        stub.enqueue.assert_not_called()

    # --- Legitimate sources accepted (validation passes) ---------------

    def test_create_job_accepts_default_api_source(
        self, create_job_client
    ):
        """``source`` omitted — defaults to ``'api'`` via Pydantic;
        must continue to pass byte-identically. This pins the
        pre-existing behavior so the new boundary is strictly
        additive. The endpoint reaches ``service.enqueue`` and the
        stub raises a sentinel exception that the router catches and
        surfaces as HTTP 500 — the test asserts the service WAS
        called (proving validation passed) AND that the response is
        NOT a 422 (proving the boundary did not reject the value)."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = create_job_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
            },
        )

        # Default source passes the boundary and reaches the service.
        # The sentinel RuntimeError is caught by the router's
        # ``except Exception`` block and surfaced as 500.
        assert resp.status_code == 500, resp.text
        stub.enqueue.assert_called_once()
        kwargs = stub.enqueue.call_args.kwargs
        assert kwargs["source"] == "api"

    def test_create_job_accepts_legitimate_custom_user_source(
        self, create_job_client
    ):
        """A legitimate user-supplied source string that does NOT match
        any reserved prefix must pass through unchanged. This is the
        primary use case for the field (``telegram:user:1``,
        ``webhook:gh-hook``, custom apps)."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = create_job_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
                "source": "telegram:user:1",
            },
        )

        assert resp.status_code == 500, resp.text  # sentinel -> 500
        stub.enqueue.assert_called_once()
        kwargs = stub.enqueue.call_args.kwargs
        assert kwargs["source"] == "telegram:user:1"

    def test_create_job_accepts_scheduler_source(
        self, create_job_client
    ):
        """``source='scheduler'`` is a legitimate user origin
        (scheduled triggers via ``daemon/sources/adapters/scheduler.py``)
        — NOT an internal family. Must pass through unchanged even
        though it shares no prefix with the reserved set. Pinned
        here to defend against a future over-broadening of the
        reserved set (F2 P2.3 note)."""
        from daemon.routers.jobs_crud import get_job_queue_service

        stub = _stub_enqueue_service()
        get_job_queue_service.set_service(stub)

        resp = create_job_client.post(
            "/jobs",
            json={
                "agent_id": "developer",
                "message": "hi",
                "source": "scheduler",
            },
        )

        assert resp.status_code == 500, resp.text  # sentinel -> 500
        stub.enqueue.assert_called_once()
        kwargs = stub.enqueue.call_args.kwargs
        assert kwargs["source"] == "scheduler"


# ---------------------------------------------------------------------------
# Group 3 — POST /messages is NOT a user-supplied source vector
# ---------------------------------------------------------------------------


class TestMessagesRouterIsNotUserSourceVector:
    """``POST /messages`` does NOT expose a user-supplied ``source``
    field — its source is hardcoded to ``"api"`` (line 399) or
    ``"api_resume_fallback"`` (line 282). This test pins that the
    seam in ``messages.py`` is the INTERNAL ``api_resume_fallback``
    call (which is itself a reserved value but stamped server-side,
    NOT user-supplied). Documented here so the same validation is not
    mistakenly added to ``messages.py`` (per the task constraint to
    confine ``messages.py`` edits to the source-validation seam).
    """

    def test_message_create_model_has_no_source_field(self):
        """The MessageCreate Pydantic model has NO ``source`` field —
        there is no user-supplied source vector on POST /messages."""
        from daemon.models.message import MessageCreate

        assert "source" not in MessageCreate.model_fields

    def test_messages_router_does_not_read_body_source(self):
        """The send_message endpoint body is MessageCreate (no source
        field) and the router hardcodes ``source='api'`` /
        ``source='api_resume_fallback'`` directly."""
        import inspect

        from daemon.routers import messages

        src = inspect.getsource(messages)
        # The two hardcoded stamps must remain byte-identical.
        assert 'source="api"' in src
        assert 'source="api_resume_fallback"' in src
        # There must be NO body.source reference.
        assert "body.source" not in src