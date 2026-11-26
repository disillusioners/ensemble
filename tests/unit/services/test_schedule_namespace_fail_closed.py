"""Pins for the 503 SCHEDULE_LABEL_CHECK_UNAVAILABLE fail-closed mapping on
POST create, GET /{id} and DELETE /{id}.

The PUT rename route's 503 branch is ALREADY pinned by
``tests/test_scheduler_api.py::TestScheduleRenameNamespace::
test_put_rename_with_an_unavailable_scan_is_503`` (:2470-2488); this module
deliberately does NOT duplicate it.

Each 503 pin mounts the REAL ``scheduling_service`` function on the mocked
manager and makes its namespace scan (``repo.list_source_configs``) raise,
exactly as the service's ``_rows_named`` fail-closed seam does
(``daemon/services/scheduling_service.py:656``, raised at :732 and :805).
The genuine ``LabelNamespaceUnavailable`` then travels through the router's
``except`` branch (:242 create / :297 GET / :378 DELETE) to 503.  Fail-closed
side effects are pinned by asserting the repository WRITE was never reached.
Negative controls show the same request is NOT 503 when the scan is healthy.

COUNTER-MUTATIONS (each proven RED during the gate, then restored byte-for-
byte): change ``except LabelNamespaceUnavailable`` to ``status_code=500`` at
:242 / :297 / :378 (or re-raise) -> the pin's status assertion fails.
"""

import asyncio
import os

import pytest
import pytest_asyncio
import httpx
from unittest.mock import Mock, MagicMock, AsyncMock


# ``daemon.api`` captures the auth config at IMPORT time (create_app ->
# load_config().auth -> AuthMiddleware).  Scrub the ENSEMBLE_AUTH_* family
# per-variable BEFORE that import — the same module-level fence the
# precedent file (tests/test_scheduler_api.py:14-30) uses.  ``env -u
# ENSEMBLE_AUTH_*`` is a silent NO-OP (bash does not glob env names).
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

from daemon import api as api_module  # noqa: F401 — import AFTER the scrub
from daemon.services import scheduling_service as svc
from daemon.services.scheduling_service import LabelNamespaceUnavailable  # noqa: F401


_TOKEN = "SCHEDULE_LABEL_CHECK_UNAVAILABLE"

# Valid POST body (identical shape to the precedent's _BODY at :1603-1612).
_CREATE_BODY = {
    "source_id": "morning-briefing",
    "name": "Morning Briefing",
    "agent_id": "ari",
    "message": "Give me a morning briefing",
    "project_id": "default",
    "recurrence": "daily",
    "local_time": "06:00",
    "timezone": "America/New_York",
}


# ---------------------------------------------------------------------------
# Fixtures — replicate the tests/test_scheduler_api.py pattern locally
# (pure mocks, no DB, no daemon boot).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _hermetic_auth_env(monkeypatch):
    """Re-apply the per-variable auth scrub at test time (restored after)."""
    for _name in (
        "ENSEMBLE_AUTH_ENABLED",
        "ENSEMBLE_AUTH_ISSUER",
        "ENSEMBLE_AUTH_DISCOVERY_URL",
        "ENSEMBLE_AUTH_CLIENT_ID",
        "ENSEMBLE_AUTH_CLIENT_SECRET",
        "ENSEMBLE_AUTH_ALLOWED_ORIGINS",
        "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE",
    ):
        monkeypatch.delenv(_name, raising=False)


@pytest.fixture(autouse=True)
def _reset_scheduling_deps():
    """The service uses a module-level _deps singleton; leave it as found."""
    yield
    svc._deps.source_repo = None
    svc._deps.source_registry = None
    svc._deps.project_repository = None
    svc._source_locks.clear()


@pytest_asyncio.fixture
async def mock_manager():
    manager = Mock()
    manager.is_write_paused = False

    repo = Mock()
    repo.list_source_configs = Mock(return_value=[])
    repo.get_source_config = Mock(return_value=None)
    repo.get_source_config_by_name = Mock(return_value=None)
    repo.create_source_config = Mock()
    repo.cancel_source_config = Mock()
    repo.update_source_config = Mock()
    repo.delete_source_config = Mock(return_value=True)
    repo.get_latest_execution = Mock(return_value=None)
    repo.list_schedule_executions = Mock(return_value=[])
    manager._source_repository = repo
    manager.source_registry = None

    manager.scheduling_service = MagicMock()
    manager.scheduling_service.create_schedule = AsyncMock(return_value=None)
    manager.scheduling_service.get_schedule = AsyncMock(return_value=None)
    manager.scheduling_service.cancel_schedule = AsyncMock(return_value=None)
    return manager


@pytest.fixture
def app_with_mock_manager(mock_manager):
    from daemon.api import app

    app.state.manager = mock_manager
    app.state.start_time = 1000.0
    return mock_manager


@pytest_asyncio.fixture
async def client(app_with_mock_manager):
    from daemon.api import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test/api"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wire_real_service(manager, method_name):
    """Mount the REAL async service function on the manager's service seam
    with the mocked repository as its dependency.  A raising
    ``list_source_configs`` then produces a genuine LabelNamespaceUnavailable
    from the service's own fail-closed scan."""
    svc.configure(source_repo=manager._source_repository)
    setattr(manager.scheduling_service, method_name, AsyncMock(side_effect=getattr(svc, method_name)))


def _scan_unavailable(repo):
    """The :2470 precedent pattern: the namespace scan raises."""

    def _boom(*args, **kwargs):
        raise RuntimeError("repository unavailable")

    repo.list_source_configs = Mock(side_effect=_boom)


def _create_response():
    from daemon.services.scheduling_service import ScheduleCreateResponse

    return ScheduleCreateResponse(
        source_id="morning-briefing",
        label="Morning Briefing",
        status="running",
        next_run_at_local="2026-10-02T06:00:00-04:00",
        next_run_at_utc="2026-10-02T10:00:00+00:00",
        tz_warning="",
    )


def _detail_response():
    from daemon.services.scheduling_service import ScheduleDetail

    return ScheduleDetail(
        source_id="sched-1",
        label="sched-1",
        status="running",
        recurrence="daily",
        local_time="06:00",
        timezone="America/New_York",
        weekday=None,
        cron_expression=None,
        instance_mode="new_instance",
        next_run_at_local="2026-10-02T06:00:00-04:00",
        next_run_at_utc="2026-10-02T10:00:00+00:00",
        agent="ari",
        project_id="default",
        last_run_at=None,
        cancelled_at=None,
        tz_warning="",
    )


def _cancel_response():
    from daemon.services.scheduling_service import ScheduleCancelResponse

    return ScheduleCancelResponse(
        source_id="morning-briefing",
        status="cancelled",
        cancelled_at="2026-10-01T20:00:00+00:00",
        last_execution_id=None,
    )


def _assert_503(response):
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == _TOKEN, response.text


# ---------------------------------------------------------------------------
# POST /api/schedules (create) — 503 at router :242
# ---------------------------------------------------------------------------


class TestCreateNamespaceFailClosed:
    @pytest.mark.asyncio
    async def test_create_unavailable_scan_is_503_and_writes_no_row(self, client, mock_manager):
        """The namespace scan fails on create -> 503 + token, and the row
        write is NEVER reached (fail-closed).  Counter-mutation: :242
        status_code 503 -> 500 (or re-raise) -> RED."""
        _wire_real_service(mock_manager, "create_schedule")
        _scan_unavailable(mock_manager._source_repository)

        response = await client.post("/schedules", json=dict(_CREATE_BODY))

        _assert_503(response)
        mock_manager._source_repository.create_source_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_healthy_scan_is_not_503(self, client, mock_manager):
        """Negative control: with the scan healthy the same POST succeeds.
        Observed status = 201 (documented), never 503."""
        mock_manager.scheduling_service.create_schedule = AsyncMock(return_value=_create_response())

        response = await client.post("/schedules", json=dict(_CREATE_BODY))

        assert response.status_code != 503, response.text
        assert response.status_code == 201, response.text
        assert response.json()["source_id"] == "morning-briefing"


# ---------------------------------------------------------------------------
# GET /api/schedules/{id} — 503 at router :297
# ---------------------------------------------------------------------------


class TestGetNamespaceFailClosed:
    @pytest.mark.asyncio
    async def test_get_unavailable_scan_is_503(self, client, mock_manager):
        """A label reference forces the namespace scan; when it fails the
        GET is 503 + token (never a silent 404 'no conflict').  Counter-
        mutation: :297 status_code 503 -> 500 (or re-raise) -> RED."""
        _wire_real_service(mock_manager, "get_schedule")
        mock_manager._source_repository.get_source_config = Mock(return_value=None)
        _scan_unavailable(mock_manager._source_repository)

        response = await client.get("/schedules/any-label")

        _assert_503(response)

    @pytest.mark.asyncio
    async def test_get_healthy_scan_is_not_503(self, client, mock_manager):
        """Negative control: a healthy service resolves the row.  Observed
        status = 200 (documented), never 503."""
        mock_manager.scheduling_service.get_schedule = AsyncMock(return_value=_detail_response())

        response = await client.get("/schedules/sched-1")

        assert response.status_code != 503, response.text
        assert response.status_code == 200, response.text
        assert response.json()["id"] == "sched-1"


# ---------------------------------------------------------------------------
# DELETE /api/schedules/{id} — 503 at router :378
# ---------------------------------------------------------------------------


class TestCancelNamespaceFailClosed:
    @pytest.mark.asyncio
    async def test_delete_unavailable_scan_is_503_and_mutates_no_row(self, client, mock_manager):
        """The namespace scan fails on cancel -> 503 + token, and the atomic
        cancel write is NEVER reached (fail-closed = no mutation).  Counter-
        mutation: :378 status_code 503 -> 500 (or re-raise) -> RED."""
        _wire_real_service(mock_manager, "cancel_schedule")
        mock_manager._source_repository.get_source_config = Mock(return_value=None)
        _scan_unavailable(mock_manager._source_repository)

        response = await client.delete("/schedules/any-label")

        _assert_503(response)
        mock_manager._source_repository.cancel_source_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_healthy_scan_is_not_503(self, client, mock_manager):
        """Negative control: with a healthy service the same DELETE cancels.
        Observed status = 200 (documented), never 503."""
        mock_manager.scheduling_service.cancel_schedule = AsyncMock(return_value=_cancel_response())

        response = await client.delete("/schedules/morning-briefing")

        assert response.status_code != 503, response.text
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "cancelled"
