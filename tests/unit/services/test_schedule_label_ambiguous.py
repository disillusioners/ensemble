"""PIN — the ``SCHEDULE_LABEL_AMBIGUOUS`` (HTTP 409) token on the REST
GET ``/api/schedules/{id}`` and DELETE ``/api/schedules/{id}`` routes.

FIX ROUND #3 / ITEM 2.  Before this file, the token was RAISED by the
router (``daemon/routers/schedules.py`` GET :305-316, DELETE :386-405)
but referenced by ZERO tests.  These pins drive the REAL router and the
REAL service label-resolution path (``resolve_label_row`` in
``daemon/services/scheduling_service.py`` :771-807) with ONLY the
source repository mocked — no resolver is monkeypatched to raise.  Two
LIVE rows sharing a label make the real resolver raise
``ValueError("ambiguous label ...")`` (:792-796), which the router maps
to 409 + ``detail.code == "SCHEDULE_LABEL_AMBIGUOUS"``.

TOOL-SURFACE EQUIVALENT (pinned below): ``task_schedule_cancel(label=...)``
calls the SAME real ``resolve_label_row`` (``daemon/tools/scheduling.py``
:262-266); its broad ``except Exception`` (:275-276) converts the
ambiguous ``ValueError`` into the house error STRING
``"ERROR: ambiguous label ..."`` — the tool surface has NO HTTP token
and never raises.  ``task_schedule_update`` (:280-304) takes only a
``source_id`` and does NOT resolve a label, so it cannot reach the
ambiguous path.

NEGATIVE CONTROLS (so the 409 pins cannot pass vacuously): one LIVE row
carrying the label is NOT ambiguous (GET → 200), and two rows sharing the
label with only ONE live are NOT ambiguous (GET → 200, live row wins).
A DELETE single-live-row control proves the DELETE route's non-409 path
too.

SAFETY FENCE: pure mocks — no DB, no daemon boot.  The FastAPI app is
built at import time with auth env scrubbed BEFORE the import (same
module-level discipline as ``tests/test_scheduler_api.py`` :14-30).
"""

from __future__ import annotations

import os

# The auth config is captured when ``daemon.api`` builds the app at IMPORT
# time; scrub the seven ambient ENSEMBLE_AUTH_* vars BEFORE that import
# (per-variable, never a family glob — ``env -u ENSEMBLE_AUTH_*`` is a
# silent NO-OP).  Replicated from tests/test_scheduler_api.py :21-30.
for _auth_env_name in (
    "ENSEMBLE_AUTH_ENABLED",
    "ENSEMBLE_AUTH_ISSUER",
    "ENSEMBLE_AUTH_DISCOVERY_URL",
    "ENSEMBLE_AUTH_CLIENT_ID",
    "ENSEMBLE_AUTH_CLIENT_SECRET",
    "ENSEMBLE_AUTH_ALLOWED_ORIGINS",
    "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE",
):
    os.environ.pop(_auth_env_name, None)

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest
import pytest_asyncio

from daemon.services import scheduling_service as svc


# ---------------------------------------------------------------------------
# In-memory doubles (replicated from tests/unit/services/test_schedule_namespace.py)
# ---------------------------------------------------------------------------


def _row(
    source_id: str,
    *,
    name: str,
    status: str = "running",
    source_type: str = "scheduler",
) -> SimpleNamespace:
    """A well-formed scheduler row (a future one-shot so GET renders 200)."""
    return SimpleNamespace(
        source_id=source_id,
        source_type=source_type,
        name=name,
        config={
            "agent": "ari",
            "message": "hi",
            "timezone": "UTC",
            "recurrence": "once",
            "run_at": "2030-01-15T09:00:00+00:00",
            "instance_mode": "new_instance",
        },
        credentials=None,
        enabled=status != "cancelled",
        autostart=True,
        status=status,
        error_message=None,
        created_at="2026-11-01T00:00:00+00:00",
        updated_at="2026-11-01T00:00:00+00:00",
    )


class _FakeRepo:
    """In-memory source repository double with PAGE-AWARE listing."""

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    def add(self, row: SimpleNamespace) -> SimpleNamespace:
        self.rows[row.source_id] = row
        return row

    def get_source_config(self, source_id):
        return self.rows.get(source_id)

    def get_source_config_by_name(self, name):
        for row in self.rows.values():
            if row.name == name:
                return row
        return None

    def list_source_configs(self, enabled=None, status=None, limit=100, offset=0):
        rows = list(self.rows.values())
        if status is not None:
            rows = [r for r in rows if r.status == status]
        if enabled is not None:
            rows = [r for r in rows if r.enabled == enabled]
        return rows[offset : offset + limit]

    def cancel_source_config(self, source_id):
        row = self.rows.get(source_id)
        if row is not None:
            row.status = "cancelled"
            row.enabled = False
        return row

    def get_latest_execution(self, schedule_id):
        return None


# ---------------------------------------------------------------------------
# Fixtures — the REAL service is configured with the fake repo; the router
# receives the REAL service functions (no resolver monkeypatch).
# ---------------------------------------------------------------------------


@pytest.fixture
def configured_repo(monkeypatch):
    """Configure the real scheduling service with a fake repo + registry.

    The service deps are process-global, so they are reset after each test
    (same discipline as TestSchedulePhase1GuardsRest._reset_svc_deps in
    tests/test_scheduler_api.py :2231-2241).
    """
    repo = _FakeRepo()
    registry = MagicMock()
    registry.get = MagicMock(return_value=None)
    registry.unregister = MagicMock(return_value=True)
    registry._create_adapter_from_config = AsyncMock(return_value=MagicMock())
    registry.register = MagicMock()
    registry.start_adapter = AsyncMock(return_value=True)
    svc.configure(source_repo=repo, source_registry=registry)
    monkeypatch.setattr(svc, "_source_locks", {})
    yield repo
    svc._deps.source_repo = None
    svc._deps.source_registry = None
    svc._deps.project_repository = None
    svc._source_locks.clear()


@pytest.fixture
def mock_manager(configured_repo):
    """A mock manager whose scheduling_service seams ARE the real funcs."""
    manager = Mock()
    manager.is_write_paused = False
    manager._source_repository = configured_repo
    manager.source_registry = None
    manager.scheduling_service = MagicMock()
    # REAL service functions — the ambiguity is produced by the real
    # resolve_label_row, not by a stub that raises on command.
    manager.scheduling_service.get_schedule = svc.get_schedule
    manager.scheduling_service.cancel_schedule = svc.cancel_schedule
    manager.scheduling_service.create_schedule = AsyncMock(return_value=None)
    return manager


@pytest_asyncio.fixture
async def client(mock_manager):
    """ASGITransport client against the real app (precedent :182-188)."""
    from daemon.api import app

    app.state.manager = mock_manager
    app.state.start_time = 1000.0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test/api"
    ) as ac:
        yield ac


def _two_live_rows(repo: _FakeRepo) -> None:
    repo.add(_row("live-1", name="shared"))
    repo.add(_row("live-2", name="shared"))


# ---------------------------------------------------------------------------
# 1 + 2 — the two REST 409 pins
# ---------------------------------------------------------------------------


class TestScheduleLabelAmbiguous409:
    @pytest.mark.asyncio
    async def test_get_ambiguous_label_is_409(self, client, configured_repo):
        """GET /api/schedules/{id}: two LIVE rows share the label ⇒ the
        real resolver refuses the guess ⇒ 409 SCHEDULE_LABEL_AMBIGUOUS."""
        _two_live_rows(configured_repo)

        response = await client.get("/schedules/shared")

        assert response.status_code == 409, response.text
        body = response.json()
        assert body["detail"]["code"] == "SCHEDULE_LABEL_AMBIGUOUS"
        assert "ambiguous" in body["detail"]["message"].lower()

    @pytest.mark.asyncio
    async def test_delete_ambiguous_label_is_409(self, client, configured_repo):
        """DELETE /api/schedules/{id}: same ambiguity ⇒ 409 token, and
        NO row is cancelled (the refusal precedes the write)."""
        _two_live_rows(configured_repo)

        response = await client.delete("/schedules/shared")

        assert response.status_code == 409, response.text
        body = response.json()
        assert body["detail"]["code"] == "SCHEDULE_LABEL_AMBIGUOUS"
        assert "ambiguous" in body["detail"]["message"].lower()
        # The resolver refused before any terminal write.
        assert all(r.status == "running" for r in configured_repo.rows.values())

    # -- 4 — negative controls (non-vacuity) --------------------------------

    @pytest.mark.asyncio
    async def test_get_single_live_row_label_is_not_409(self, client, configured_repo):
        """Negative control (a): ONE live row owns the label ⇒ NOT
        ambiguous.  Observed status: HTTP 200 (the single row is returned)."""
        configured_repo.add(_row("solo-1", name="solo"))

        response = await client.get("/schedules/solo")

        assert response.status_code != 409, response.text
        assert response.status_code == 200, response.text
        assert response.json()["source_id"] == "solo-1"

    @pytest.mark.asyncio
    async def test_get_one_live_plus_one_cancelled_same_label_is_not_ambiguous(
        self, client, configured_repo
    ):
        """Negative control (b): two rows share the label but only ONE is
        live ⇒ NOT ambiguous; the LIVE row wins.  Observed: HTTP 200."""
        configured_repo.add(_row("dead-1", name="shared", status="cancelled"))
        configured_repo.add(_row("live-1", name="shared", status="running"))

        response = await client.get("/schedules/shared")

        assert response.status_code != 409, response.text
        assert response.status_code == 200, response.text
        assert response.json()["source_id"] == "live-1"

    @pytest.mark.asyncio
    async def test_delete_single_live_row_label_is_not_409(self, client, configured_repo):
        """DELETE negative control: one live row ⇒ the real cancel runs ⇒
        HTTP 200 (not the 409 token).  Keeps the DELETE pin non-vacuous."""
        configured_repo.add(_row("solo-1", name="solo"))

        response = await client.delete("/schedules/solo")

        assert response.status_code != 409, response.text
        assert response.status_code == 200, response.text
        assert configured_repo.rows["solo-1"].status == "cancelled"


# ---------------------------------------------------------------------------
# 3 — tool-surface equivalent (the observable behaviour is an ERROR STRING,
#     not an HTTP token; documented in the class docstring above).
# ---------------------------------------------------------------------------


class TestCancelToolAmbiguousLabel:
    @pytest.mark.asyncio
    async def test_cancel_by_ambiguous_label_returns_error_string(self, configured_repo):
        """``task_schedule_cancel(label=...)`` calls the real
        ``resolve_label_row`` (daemon/tools/scheduling.py :262-266); two
        live rows ⇒ ValueError ⇒ the tool's broad handler (:275-276)
        returns ``"ERROR: ambiguous label ..."`` and never awaits the
        service cancel.  The HTTP token is NOT present on this surface."""
        from daemon.tools.scheduling import create_scheduling_tools

        _two_live_rows(configured_repo)
        service = MagicMock()
        service.cancel_schedule = AsyncMock(return_value=None)
        tools = {
            t.name: t
            for t in create_scheduling_tools(
                service,
                source_repo=configured_repo,
                current_instance_id="inst-test",
                agent_id="worker",
            )
        }

        result = await tools["task_schedule_cancel"].coroutine(label="shared")

        assert isinstance(result, str)
        assert result.startswith("ERROR:")
        assert "ambiguous label" in result
        assert "2 live schedules share it" in result
        assert "SCHEDULE_LABEL_AMBIGUOUS" not in result
        service.cancel_schedule.assert_not_awaited()
