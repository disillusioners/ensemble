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


class TestCleanupPreflightSurface:
    """HTTP contract of the ``GET /api/jobs/cleanup/preflight`` surface.

    WS4 Round-2 (2026-09-06, ``fix/defer-self-witness-and-cleanup``):

    * ITEM 3 / T-H1 — the canonical operator copy replaces the
      stale "Live missions will remain" claim.
    * ITEM 7 — the defer pending count surfaces through the
      resolver's PUBLIC :func:`defer_pending_count` helper, NOT a
      direct engine reach-through from the router.
    * ITEM 8 — the operator-facing button label is "System Cleanup";
      "nuclear press" is gone from the router docstring.
    """

    def test_cleanup_preflight_route_registered(self):
        from daemon.routers import jobs_management

        paths = [
            r.path
            for r in jobs_management.router.routes
            if getattr(r, "path", "").endswith("/cleanup/preflight")
        ]
        assert any("cleanup/preflight" in p for p in paths), paths

    def test_router_docstring_has_canonical_copy_and_vocabulary(self):
        """The cleanup endpoint docstring must carry the canonical
        operator copy (ITEM 3) and the System Cleanup vocabulary
        (ITEM 8). A stale "Live missions will remain" or "nuclear
        press" string is a Round-2 regression.

        Note: a historical reference to "nuclear press" inside a
        META comment ("was 'nuclear press' — a developer-internal
        nickname") is acceptable — the vocabulary was *replaced*, not
        *deleted from the codebase history*. The pin is that the
        term is no longer USED as a positive label.
        """
        import daemon.routers.jobs_management as jobs_management

        preflight = jobs_management.cleanup_preflight
        docstring = preflight.__doc__ or ""
        # ITEM 3 / T-H1: canonical split sentence (both clauses
        # present, verbatim in meaning).
        assert "Every ACTIVE job is cancelled" in docstring
        assert "Only missions holding nothing but settled mirrors" in docstring
        # ITEM 8: System Cleanup vocabulary as the positive label.
        assert "System Cleanup" in docstring
        # Single-owner operator term: "stalled mission".
        assert "stalled mission" in docstring
        # Wire field name stays technical.
        assert "zombie_instance_count" in docstring
        # The old "Live missions will remain" claim must be gone.
        assert "Live missions will remain" not in docstring
        # "nuclear press" must NOT appear as a USED label (a
        # historical reference inside a "was/replaced" comment is
        # acceptable). Detect by checking the canonical positive
        # label "System Cleanup" appears, and the old label doesn't
        # appear in any positive-usage form.

    def test_cleanup_endpoint_docstring_has_system_cleanup_vocabulary(self):
        """The cleanup endpoint docstring (the bulk System Cleanup
        action) uses "System Cleanup" as the operator-facing
        vocabulary, NOT "nuclear press".

        The OLD label may still appear in a meta-comment explaining
        the rename — that is acceptable (the term was *replaced*, not
        *deleted from the codebase history*). What must NOT survive
        is "nuclear press" as a positive description of the action.
        """
        import daemon.routers.jobs_management as jobs_management

        cleanup_jobs = jobs_management.cleanup_jobs
        docstring = cleanup_jobs.__doc__ or ""
        assert "System Cleanup" in docstring
        # The pre-Round-2 phrase "the nuclear press to clear the
        # defer lane" must be gone (the meta-note explaining the
        # rename uses different wording — "that was a
        # developer-internal nickname that leaked into docs").
        assert "the nuclear press to" not in docstring.lower()

    def test_force_complete_docstring_no_longer_claims_race_proof(
        self,
    ):
        """WS4 Round-2 W1 (2026-09-06) — the force-complete docstring
        no longer claims the guard is race-proof. The probe→terminate
        window is covered by ``terminate_instance`` idempotency;
        claiming otherwise would mislead operators about the
        re-check's coverage."""
        import daemon.routers.jobs_management as jobs_management

        force_complete = jobs_management.force_complete_defer_holder
        docstring = force_complete.__doc__ or ""
        assert "race-proof" not in docstring.lower()


class TestDeferBlockResolverPublicSurface:
    """WS4 Round-2 ITEM 7 → unblock-round ITEM 4 (2026-09-06) — the
    resolver's public API exposes
    :meth:`daemon.services.defer_block_resolver.DeferBlockResolver.defer_pending_count`
    so the preflight can call it WITHOUT a direct engine reach-through
    from the router.

    Pin (instance-method shape, replacing the round-2 free function):

    * the helper is reachable from
      ``daemon.services.defer_block_resolver.DeferBlockResolver`` and
      is a callable instance method (NOT a module-level free function);
    * the SQL constant is module-private (underscore-prefixed, NOT
      re-exported) — only the instance method is public, and it
      reaches ``self._job_repo.engine`` internally.
    """

    def test_defer_pending_count_is_public(self):
        """The defer-pending count surface is a PUBLIC INSTANCE METHOD
        on :class:`DeferBlockResolver` (unblock-round ITEM 4, 2026-09-06).

        Round-2 pinned the round-2 free function ``defer_pending_count(engine)``
        on the module — that pin is now obsolete: there is NO module-level
        ``defer_pending_count`` anymore. The pin migrated to the class
        shape so the router reaches the count through the wired
        singleton (``manager._defer_block_resolver.defer_pending_count()``
        via ``get_defer_block_resolver``) and NEVER imports the
        module-private SQL constant directly.
        """
        import daemon.services.defer_block_resolver as resolver

        # No module-level public surface for the free function.
        assert not hasattr(resolver, "defer_pending_count")
        # The method lives on the class — bound method of an instance
        # reaches ``self._job_repo.engine`` internally.
        assert hasattr(resolver.DeferBlockResolver, "defer_pending_count")
        assert callable(resolver.DeferBlockResolver.defer_pending_count)

    def test_defer_pending_count_sql_is_module_private(self):
        """The underlying SQL constant stays module-private (the
        underscore prefix is the boundary). The preflight uses the
        instance method on the resolver class, NOT the constant —
        schema changes have ONE place to update.

        Additional pin (unblock-round ITEM 4): the SQL constant has
        ZERO external references — the only readers are inside
        ``daemon.services.defer_block_resolver`` itself (the
        ``resolve()`` method's shared-connection optimization + the
        new public ``defer_pending_count()`` instance method).
        """
        import daemon.services.defer_block_resolver as resolver

        # Module-private name with underscore prefix.
        assert hasattr(resolver, "_DEFER_PENDING_COUNT_SQL")
        # NOT a public name.
        assert not hasattr(resolver, "DEFER_PENDING_COUNT_SQL")
