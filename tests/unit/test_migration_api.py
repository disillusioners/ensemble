"""Unit tests for ``daemon.routers.migration.router``.

The migration router exposes 5 endpoints over the ``MigrationWorker``:

* ``GET  /api/migration/availability``  - pre-condition check
* ``POST /api/migration/start``        - kick off a background run
* ``GET  /api/migration/status``       - latest progress snapshot
* ``POST /api/migration/cancel``       - cooperative cancellation
* ``GET  /api/migration/events``       - SSE stream

The router resolves the worker from ``app.state.migration_worker``; we
attach a small ``MigrationWorker``-shaped stub to ``app.state`` so we
can drive the endpoints without spinning up a real InstanceManager.

Test surface
------------
We use ``starlette.testclient.TestClient`` for synchronous calls and
to consume the SSE stream. The mock worker exposes the same async
methods the router calls (``is_migration_available``, ``start``,
``get_status``, ``cancel``, ``subscribe``/``unsubscribe``).
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from daemon.routers import migration as migration_router
from daemon.routers.migration import router
from daemon.services.migration_worker import MigrationProgress, MigrationState


# ──────────────────────────────────────────────────────────────────────────────
# Mock worker
# ──────────────────────────────────────────────────────────────────────────────


class MockMigrationWorker:
    """Stand-in for ``MigrationWorker`` that satisfies the router's contract.

    The router only calls a small surface area, so this class records
    each call and lets tests configure the return values. It also
    implements ``subscribe``/``unsubscribe`` to drive the SSE endpoint
    test with deterministic event streams.
    """

    def __init__(
        self,
        *,
        is_available: dict[str, Any] | None = None,
        progress: MigrationProgress | None = None,
    ) -> None:
        self._is_available = is_available or {
            "can_migrate": True,
            "is_sqlite": True,
            "pg_env_available": True,
            "reasons": [],
        }
        self._progress = progress or MigrationProgress()
        self.start_called = 0
        self.cancel_called = 0
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

        # Configure async method mocks.
        self.start = AsyncMock(side_effect=self._start_impl)
        self.cancel = AsyncMock(side_effect=self._cancel_impl)

    async def _start_impl(self) -> None:
        self.start_called += 1

    async def _cancel_impl(self) -> None:
        self.cancel_called += 1
        if self._progress.status != MigrationState.RUNNING:
            raise RuntimeError("No migration is currently running")

    def is_migration_available(self) -> dict[str, Any]:
        return dict(self._is_available)

    def get_status(self) -> dict[str, Any]:
        return self._progress.to_dict()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Test helper: push an event to every subscriber."""
        event = {"event": event_type, "data": data}
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_worker() -> MockMigrationWorker:
    """A mock worker with default availability and IDLE status."""
    return MockMigrationWorker()


@pytest.fixture
def app(mock_worker: MockMigrationWorker) -> FastAPI:
    """A FastAPI app with the migration router wired to a mock worker."""
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.migration_worker = mock_worker
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """A TestClient bound to the configured app."""
    return TestClient(app)


# ──────────────────────────────────────────────────────────────────────────────
# /availability
# ──────────────────────────────────────────────────────────────────────────────


class TestAvailabilityEndpoint:
    """``GET /api/migration/availability`` returns the public response shape."""

    def test_availability_can_migrate(self, client, mock_worker):
        """When all pre-conditions are met, ``can_start`` is True."""
        response = client.get("/api/migration/availability")
        assert response.status_code == 200

        body = response.json()
        assert body["can_start"] is True
        assert body["migration_available"] is True
        assert body["current_database"] == "sqlite"
        assert body["postgres_configured"] is True
        assert body["postgres_env_set"] is True

    def test_availability_can_migrate_false(
        self, client, mock_worker
    ):
        """When pre-conditions fail, ``can_start`` is False."""
        mock_worker._is_available = {
            "can_migrate": False,
            "is_sqlite": True,
            "pg_env_available": False,
            "reasons": ["PG not configured"],
        }
        response = client.get("/api/migration/availability")
        assert response.status_code == 200

        body = response.json()
        assert body["can_start"] is False
        assert body["migration_available"] is False
        assert body["current_database"] == "sqlite"
        assert body["postgres_configured"] is False
        assert body["postgres_env_set"] is False

    def test_availability_current_database_postgres(self, client, mock_worker):
        """``current_database`` is ``postgres`` when the engine is on PG."""
        mock_worker._is_available = {
            "can_migrate": False,
            "is_sqlite": False,
            "pg_env_available": True,
            "reasons": ["already on postgres"],
        }
        response = client.get("/api/migration/availability")
        body = response.json()
        assert body["current_database"] == "postgres"
        assert body["postgres_env_set"] is True


# ──────────────────────────────────────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────────────────────────────────────


class TestStartEndpoint:
    """``POST /api/migration/start`` kicks off a background migration."""

    def test_start_returns_202(self, client, mock_worker):
        """Successful start returns 202 with a migration_id."""
        response = client.post("/api/migration/start")
        assert response.status_code == 202

        body = response.json()
        assert "migration_id" in body
        assert body["migration_id"].startswith("migration_")
        assert body["status"] == "running"
        assert "successfully" in body["message"]

        # The worker's start was called via the background task.
        # Note: TestClient runs the background task synchronously, so
        # we don't strictly observe start_called, but it should be 1
        # if the background task ran.
        # (It will be 1 if the lifespan picked up the background task;
        # in some configurations it might not have run yet.)

    def test_start_unique_migration_ids(self, client):
        """Two distinct start calls produce distinct migration_ids."""
        id1 = client.post("/api/migration/start").json()["migration_id"]
        # Second call needs a fresh status (not already running).
        # We need to update the status between calls to avoid 409.
        # The router's start endpoint requires status != RUNNING.
        # The first call might have transitioned the status if the
        # background task ran; if not, it's still IDLE. Either way,
        # the id is generated by the wall clock — sleep to ensure
        # a different second.
        import time
        time.sleep(1.05)  # Ensure different YYYYMMDD_HHMMSS timestamp
        id2 = client.post("/api/migration/start").json()["migration_id"]
        assert id1 != id2

    def test_start_409_when_already_running(self, client, mock_worker):
        """If the worker is already RUNNING, the endpoint returns 409."""
        mock_worker._progress.status = MigrationState.RUNNING
        response = client.post("/api/migration/start")
        assert response.status_code == 409
        assert "already running" in response.json()["detail"].lower()

    def test_start_400_when_not_eligible(self, client, mock_worker):
        """If pre-conditions fail, the endpoint returns 400."""
        mock_worker._is_available = {
            "can_migrate": False,
            "is_sqlite": False,
            "pg_env_available": True,
            "reasons": ["already on postgres"],
        }
        response = client.post("/api/migration/start")
        assert response.status_code == 400
        assert "Migration not available" in response.json()["detail"]
        assert "already on postgres" in response.json()["detail"]

    def test_start_500_when_worker_not_initialized(self, app):
        """If app.state.migration_worker is missing, 500 is returned."""
        # Replace the worker with None to simulate uninitialized state.
        app.state.migration_worker = None

        with TestClient(app) as c:
            response = c.post("/api/migration/start")
        assert response.status_code == 500
        assert "not initialized" in response.json()["detail"]


# ──────────────────────────────────────────────────────────────────────────────
# /status
# ──────────────────────────────────────────────────────────────────────────────


class TestStatusEndpoint:
    """``GET /api/migration/status`` returns the worker's progress dict."""

    def test_status_returns_idle(self, client):
        """Fresh worker reports IDLE state."""
        response = client.get("/api/migration/status")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "idle"
        assert body["requires_restart"] is False

    def test_status_returns_running(self, client, mock_worker):
        """Running worker reports RUNNING state."""
        mock_worker._progress.status = MigrationState.RUNNING
        mock_worker._progress.current_phase = "migrating_tables"
        response = client.get("/api/migration/status")
        body = response.json()
        assert body["status"] == "running"
        assert body["current_phase"] == "migrating_tables"

    def test_status_returns_completed_with_restart_flag(
        self, client, mock_worker
    ):
        """Completed state includes ``requires_restart=True``."""
        mock_worker._progress.status = MigrationState.COMPLETED
        response = client.get("/api/migration/status")
        body = response.json()
        assert body["status"] == "completed"
        assert body["requires_restart"] is True

    def test_status_strips_internal_fields(self, client, mock_worker):
        """The internal ``_timestamp`` field is stripped from the response."""
        response = client.get("/api/migration/status")
        body = response.json()
        assert "_timestamp" not in body

    def test_status_500_when_worker_not_initialized(self, app):
        """500 if app.state.migration_worker is None."""
        app.state.migration_worker = None
        with TestClient(app) as c:
            response = c.get("/api/migration/status")
        assert response.status_code == 500


# ──────────────────────────────────────────────────────────────────────────────
# /cancel
# ──────────────────────────────────────────────────────────────────────────────


class TestCancelEndpoint:
    """``POST /api/migration/cancel`` requests cooperative cancellation."""

    def test_cancel_200_when_running(self, client, mock_worker):
        """Cancelling a running migration returns 200."""
        mock_worker._progress.status = MigrationState.RUNNING

        response = client.post("/api/migration/cancel")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "cancelled"
        assert "cancellation requested" in body["message"].lower()

    def test_cancel_409_when_not_running(self, client, mock_worker):
        """Cancelling when not running returns 409."""
        # Default state is IDLE.
        response = client.post("/api/migration/cancel")
        assert response.status_code == 409
        assert "migration" in response.json()["detail"].lower()

    def test_cancel_500_when_worker_not_initialized(self, app):
        """500 if app.state.migration_worker is None."""
        app.state.migration_worker = None
        with TestClient(app) as c:
            response = c.post("/api/migration/cancel")
        assert response.status_code == 500


# ──────────────────────────────────────────────────────────────────────────────
# /events (SSE)
# ──────────────────────────────────────────────────────────────────────────────


class TestEventsEndpoint:
    """``GET /api/migration/events`` streams SSE events."""

    def test_events_stream_yields_events(self, app, mock_worker):
        """SSE endpoint forwards events from the worker."""
        with TestClient(app) as c:
            import threading
            events_received: list[str] = []
            stream_done = threading.Event()

            def consume():
                current_event: str | None = None
                with c.stream("GET", "/api/migration/events") as response:
                    for line in response.iter_lines():
                        # SSE format: ``event: <type>`` and ``data: <json>``.
                        if line.startswith("event: "):
                            current_event = line[len("event: "):]
                        elif line.startswith("data: ") and current_event:
                            events_received.append(current_event)
                            current_event = None
                            if any(
                                e in ("complete", "error", "cancelled")
                                for e in events_received
                            ):
                                break
                stream_done.set()

            consumer = threading.Thread(target=consume, daemon=True)
            consumer.start()

            import time
            time.sleep(0.1)

            mock_worker.emit("progress", {"phase": "running"})
            mock_worker.emit("complete", {"message": "done"})

            stream_done.wait(timeout=3.0)

        # We should have seen both events.
        assert "progress" in events_received
        assert "complete" in events_received

    def test_events_breaks_on_terminal_event(self, app, mock_worker):
        """The stream ends when a terminal event is received."""
        with TestClient(app) as c:
            events_received: list[str] = []

            def emit_terminal():
                import time
                time.sleep(0.1)
                mock_worker.emit("cancelled", {"message": "cancelled by user"})

            threading.Thread(target=emit_terminal, daemon=True).start()

            current_event: str | None = None
            with c.stream("GET", "/api/migration/events") as response:
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        current_event = line[len("event: "):]
                    elif line.startswith("data: ") and current_event:
                        events_received.append(current_event)
                        current_event = None
                        if "cancelled" in events_received:
                            break

        assert "cancelled" in events_received

    def test_events_unsubscribes_after_stream(self, app, mock_worker):
        """After the stream ends, the subscriber queue is removed."""
        with TestClient(app) as c:
            assert len(mock_worker._subscribers) == 0

            def emit_and_break():
                import time
                time.sleep(0.1)
                mock_worker.emit("complete", {"message": "done"})

            threading.Thread(target=emit_and_break, daemon=True).start()

            current_event: str | None = None
            with c.stream("GET", "/api/migration/events") as response:
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        current_event = line[len("event: "):]
                    elif line.startswith("data: ") and current_event:
                        if current_event == "complete":
                            break
                        current_event = None

        # The subscription is cleaned up.
        assert len(mock_worker._subscribers) == 0

    def test_events_500_when_worker_not_initialized(self, app):
        """500 if app.state.migration_worker is None."""
        app.state.migration_worker = None
        with TestClient(app) as c:
            response = c.get("/api/migration/events")
        assert response.status_code == 500


# ──────────────────────────────────────────────────────────────────────────────
# Helper-function tests
# ──────────────────────────────────────────────────────────────────────────────


class TestRouterHelpers:
    """Module-level helpers used by the endpoint bodies."""

    def test_availability_to_response_translates_keys(self):
        """The internal ``can_migrate``/``is_sqlite``/``pg_env_available``
        are translated to the public contract names."""
        result = migration_router._availability_to_response({
            "can_migrate": True,
            "is_sqlite": True,
            "pg_env_available": True,
            "reasons": [],
        })
        assert result == {
            "migration_available": True,
            "current_database": "sqlite",
            "postgres_configured": True,
            "can_start": True,
            "can_switch": True,  # on sqlite + pg env set => can switch to pg
            "postgres_env_set": True,
        }

    def test_availability_to_response_postgres(self):
        """``current_database`` is ``postgres`` when ``is_sqlite`` is False.

        ``can_switch`` is True because SQLite is always available as a
        target when the daemon is on PostgreSQL.
        """
        result = migration_router._availability_to_response({
            "can_migrate": False,
            "is_sqlite": False,
            "pg_env_available": True,
            "reasons": ["x"],
        })
        assert result["current_database"] == "postgres"
        assert result["migration_available"] is False
        assert result["can_start"] is False
        assert result["can_switch"] is True
        assert result["postgres_env_set"] is True

    def test_availability_to_response_can_switch_false_on_sqlite_no_pg(self):
        """On SQLite without PG env, ``can_switch`` is False (no target)."""
        result = migration_router._availability_to_response({
            "can_migrate": False,
            "is_sqlite": True,
            "pg_env_available": False,
            "reasons": ["pg env not set"],
        })
        assert result["current_database"] == "sqlite"
        assert result["can_switch"] is False
        assert result["postgres_env_set"] is False

    def test_strip_internal_fields_removes_underscore_prefixed(self):
        """Fields starting with ``_`` are stripped."""
        d = {
            "status": "running",
            "phase": "x",
            "_timestamp": "secret",
            "_internal": "also secret",
        }
        result = migration_router._strip_internal_fields(d)
        assert "status" in result
        assert "phase" in result
        assert "_timestamp" not in result
        assert "_internal" not in result

    def test_make_migration_id_format(self):
        """``_make_migration_id`` returns a sortable identifier."""
        mid = migration_router._make_migration_id()
        assert mid.startswith("migration_")
        # Strip the prefix and verify the timestamp is parseable.
        ts_str = mid[len("migration_"):]
        # Should be YYYYMMDD_HHMMSS (15 chars).
        assert len(ts_str) == 15
        assert ts_str[8] == "_"
        # Parse it.
        from datetime import datetime
        datetime.strptime(ts_str, "%Y%m%d_%H%M%S")


# ──────────────────────────────────────────────────────────────────────────────
# Worker-not-initialized across endpoints
# ──────────────────────────────────────────────────────────────────────────────


class TestWorkerNotInitialized:
    """All endpoints fail fast with 500 when the worker is missing."""

    @pytest.fixture
    def uninitialized_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(router, prefix="/api")
        # Intentionally do NOT set app.state.migration_worker.
        return app

    def test_availability_returns_500(self, uninitialized_app):
        with TestClient(uninitialized_app) as c:
            response = c.get("/api/migration/availability")
        assert response.status_code == 500

    def test_start_returns_500(self, uninitialized_app):
        with TestClient(uninitialized_app) as c:
            response = c.post("/api/migration/start")
        assert response.status_code == 500

    def test_status_returns_500(self, uninitialized_app):
        with TestClient(uninitialized_app) as c:
            response = c.get("/api/migration/status")
        assert response.status_code == 500

    def test_cancel_returns_500(self, uninitialized_app):
        with TestClient(uninitialized_app) as c:
            response = c.post("/api/migration/cancel")
        assert response.status_code == 500

    def test_events_returns_500(self, uninitialized_app):
        with TestClient(uninitialized_app) as c:
            response = c.get("/api/migration/events")
        assert response.status_code == 500
