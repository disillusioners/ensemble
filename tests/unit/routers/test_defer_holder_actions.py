"""Unit tests for the WS4 defer-holder actions (2026-09-06).

Endpoints under test (``daemon/routers/jobs_management.py``):

* ``POST /api/jobs/defer-holders/{instance_id}/force-complete`` —
  terminate a STALLED (mirrors-only) defer-gate holder after
  re-deriving the mirrors-only state server-side via the canonical
  WS1 carve-out probe. The service-level guard semantics (probe busy
  → refuse; fail-closed) are pinned in
  ``tests/integration/test_nuclear_cleanup_bucket5.py``; this module
  pins the HTTP contract: routing, response shapes, the 200-refused
  case (evaluated-and-declined is NOT an error), 404 on a missing
  holder, 400 on an empty re-send scope, and the write-pause 503s.

Both actions reuse registered admission-state writers only (the
termination goes through the existing ``terminate_instance`` cascade;
the re-send goes through ``cancel_job`` + the ``enqueue_message_job``
front primitive), so the constitution's census stays at 23 — proven by
``tests/unit/job_state/test_constitution_drift.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def holder_app():
    """FastAPI app with the management router and a permissive manager."""
    app = FastAPI()
    from daemon.routers.jobs_management import router as management_router

    app.include_router(management_router)
    app.state.manager = MagicMock(is_write_paused=False)
    yield app
    from daemon.routers.jobs_crud import get_job_queue_service

    get_job_queue_service.set_service(None)


@pytest.fixture
def holder_client(holder_app):
    with TestClient(holder_app) as client:
        yield client


class TestDeferHolderActionRegistration:
    """Pin the endpoint shapes: path / method / router prefix."""

    def test_force_complete_route_registered_under_jobs_prefix(self):
        from daemon.routers import jobs_management

        paths = [
            r.path
            for r in jobs_management.router.routes
            if getattr(r, "path", "").endswith("/force-complete")
        ]
        assert any("defer-holders" in p for p in paths), paths

    def test_resend_foreground_route_registered_under_jobs_prefix(self):
        from daemon.routers import jobs_management

        paths = [
            r.path
            for r in jobs_management.router.routes
            if getattr(r, "path", "").endswith("/resend-foreground")
        ]
        assert any("defer-holders" in p for p in paths), paths


class TestForceCompleteEndpoint:
    """HTTP contract of the force-complete holder action."""

    def _set_service(self, result=None, exc: Exception | None = None):
        from daemon.routers.jobs_crud import get_job_queue_service

        service = MagicMock()
        if exc is not None:
            service.force_complete_defer_holder = AsyncMock(side_effect=exc)
        else:
            service.force_complete_defer_holder = AsyncMock(
                return_value=result
                or {
                    "instance_id": "inst-abc12345",
                    "terminated": True,
                    "probe_busy": False,
                }
            )
        get_job_queue_service.set_service(service)
        return service

    def test_200_terminated(self, holder_client):
        service = self._set_service(
            {"instance_id": "inst-abc12345", "terminated": True, "probe_busy": False}
        )

        response = holder_client.post("/jobs/defer-holders/inst-abc12345/force-complete")

        assert response.status_code == 200
        body = response.json()
        assert body["instance_id"] == "inst-abc12345"
        assert body["terminated"] is True
        assert body["probe_busy"] is False
        assert "force-completed" in body["message"]
        service.force_complete_defer_holder.assert_awaited_once_with("inst-abc12345")

    def test_200_refused_when_probe_busy_is_not_an_error(self, holder_client):
        """A guard refusal is an EVALUATED outcome — 200 with
        ``terminated=false`` so the FE can render the refusal, not a
        4xx/5xx that would read as a transport failure."""
        self._set_service(
            {"instance_id": "inst-live99", "terminated": False, "probe_busy": True}
        )

        response = holder_client.post("/jobs/defer-holders/inst-live99/force-complete")

        assert response.status_code == 200
        body = response.json()
        assert body["terminated"] is False
        assert body["probe_busy"] is True
        assert "Refused" in body["message"]

    def test_404_when_holder_missing(self, holder_client):
        self._set_service(exc=LookupError("Instance inst-missing does not exist"))

        response = holder_client.post(
            "/jobs/defer-holders/inst-missing/force-complete"
        )

        assert response.status_code == 404

    def test_503_when_writes_paused(self, holder_app):
        from daemon.routers.jobs_crud import get_job_queue_service

        get_job_queue_service.set_service(MagicMock())
        holder_app.state.manager = MagicMock(is_write_paused=True)
        with TestClient(holder_app) as client:
            response = client.post(
                "/jobs/defer-holders/inst-abc12345/force-complete"
            )
        assert response.status_code == 503

    def test_500_when_service_raises_unexpected(self, holder_client):
        self._set_service(exc=RuntimeError("boom"))

        response = holder_client.post(
            "/jobs/defer-holders/inst-abc12345/force-complete"
        )

        assert response.status_code == 500


class TestResendForegroundEndpoint:
    """HTTP contract of the re-send-foreground holder action."""

    def _set_service(self, result=None, exc: Exception | None = None):
        from daemon.routers.jobs_crud import get_job_queue_service

        service = MagicMock()
        if exc is not None:
            service.resend_deferred_foreground = AsyncMock(side_effect=exc)
        else:
            service.resend_deferred_foreground = AsyncMock(
                return_value=result
                or {
                    "instance_id": "inst-abc12345",
                    "found_defer_jobs": 1,
                    "cancelled_defer_jobs": 1,
                    "resend_results": [
                        {
                            "cancelled_job_id": "job-old",
                            "job_id": "job-new",
                            "message_id": "msg-new",
                        }
                    ],
                    "skipped_empty_content": 0,
                }
            )
        get_job_queue_service.set_service(service)
        return service

    def test_200_with_resend_results(self, holder_client):
        service = self._set_service()

        response = holder_client.post(
            "/jobs/defer-holders/inst-abc12345/resend-foreground"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["found_defer_jobs"] == 1
        assert body["cancelled_defer_jobs"] == 1
        assert body["resend_results"][0]["job_id"] == "job-new"
        assert body["skipped_empty_content"] == 0
        service.resend_deferred_foreground.assert_awaited_once_with("inst-abc12345")

    def test_400_when_no_queued_defer_jobs(self, holder_client):
        """``found_defer_jobs == 0`` → 400 so the FE surfaces an honest
        "nothing to re-send" instead of a fake success."""
        self._set_service(
            {
                "instance_id": "inst-empty1",
                "found_defer_jobs": 0,
                "cancelled_defer_jobs": 0,
                "resend_results": [],
                "skipped_empty_content": 0,
            }
        )

        response = holder_client.post(
            "/jobs/defer-holders/inst-empty1/resend-foreground"
        )

        assert response.status_code == 400
        assert "no queued defer-lane" in response.json()["detail"]["message"]

    def test_404_when_holder_missing(self, holder_client):
        self._set_service(exc=LookupError("Instance inst-missing does not exist"))

        response = holder_client.post(
            "/jobs/defer-holders/inst-missing/resend-foreground"
        )

        assert response.status_code == 404

    def test_503_when_writes_paused(self, holder_app):
        from daemon.routers.jobs_crud import get_job_queue_service

        get_job_queue_service.set_service(MagicMock())
        holder_app.state.manager = MagicMock(is_write_paused=True)
        with TestClient(holder_app) as client:
            response = client.post(
                "/jobs/defer-holders/inst-abc12345/resend-foreground"
            )
        assert response.status_code == 503
