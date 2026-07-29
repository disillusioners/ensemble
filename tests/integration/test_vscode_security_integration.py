"""Security integration tests for the VS Code editor integration (C1 + C4).

These tests exercise the REAL ``create_app()`` (via ``httpx.ASGITransport``)
and the REAL proxy sub-application to catch wiring/mount bugs that unit tests
with mocked endpoints miss. They are intentionally narrower than the full
test suite: only the two security boundaries that are easiest to break by a
re-mount or a refactor:

**C1 — Path traversal via the ``GET /vscode/?folder=`` proxy parameter.**
The proxy validates ``?folder=`` against known project ``main_directory``
values using ``WorkspaceGuard.resolve_strict()`` (see
``daemon/routers/vscode_proxy.py::_validate_folder_param``). Path-traversal
attempts (``/etc``, ``../../etc``, null-byte injection) MUST be rejected
with a 4xx before reaching code-server, while the real repo path MUST NOT
be rejected with a validation error (it may 5xx if code-server is down,
but a 403/422 would mean valid folders are wrongly blocked).

**C4 — Port/PID leak via the editor status endpoints.**
``VSCodeServerState`` carries the OS-assigned loopback port and PID, but
those must NEVER be serialized into API responses — they would defeat the
proxy boundary (the whole point of the proxy is that clients never learn
the real port). The response schemas (``VSCodeStatus``,
``VSCodeStatusResponse``) intentionally omit ``port``/``pid``, and these
tests pin that defense-in-depth by asserting the keys are absent from the
raw response text (catching accidental re-additions even in nested keys).

Why these are integration tests and not unit tests:
- ``test_vscode_path_validation.py`` already pins ``resolve_strict()`` in
  isolation (13 tests). This file drives the REAL HTTP path through the
  proxy + Starlette routing + ``create_app()`` to catch wiring bugs.
- ``test_editor_settings.py`` already pins C4 via mocked endpoints. This
  file drives the REAL ``create_app()`` to catch schema/regression bugs
  that mocks hide.

Run only this file::

    timeout 300 .venv/bin/pytest tests/integration/test_vscode_security_integration.py -v --tb=short -q -m "not integration and not postgres"
"""
from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio

from daemon.api import create_app
from daemon.routers import settings as settings_module

# Absolute path to this repo's root — used as the "known project" workdir.
REPO_ROOT = str(Path(__file__).resolve().parents[2])


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_fastapi_api_route_default_response_model():
    """Same fixture as ``test_vscode_routing.py``.

    FastAPI >= 0.120 rejects ``StreamingResponse | JSONResponse`` as a
    return annotation on ``api_route``. The proxy factory registers a
    catch all via ``api_route`` without overriding ``response_model``, so
    calling it raises at registration time. This fixture defaults
    ``response_model=None`` so the factory can run unmodified.

    Auto-restored after each test to prevent leakage.
    """
    from fastapi.applications import FastAPI as FastAPIClass

    original = FastAPIClass.api_route

    def _patched(self, *args, **kwargs):
        if "response_model" not in kwargs:
            kwargs["response_model"] = None
        return original(self, *args, **kwargs)

    FastAPIClass.api_route = _patched
    try:
        yield
    finally:
        FastAPIClass.api_route = original


def _make_mock_manager(*, running: bool = True, port: int = 1) -> MagicMock:
    """Build a MagicMock vscode manager that reports ``running``.

    ``port=1`` is deliberately unreachable so a valid folder (one that
    PASSES validation) raises ``httpx.ConnectError`` from the proxy's
    upstream call — proving the request got PAST validation and reached
    the code-server forwarding step. That ConnectError is the "valid
    folder not wrongly blocked" signal we assert on.
    """
    mgr = MagicMock(name="vscode_manager")
    mgr.is_running.return_value = running
    mgr.get_port.return_value = port
    state = MagicMock(name="state")
    state.status = "running"
    # C4: state carries port/pid internally — schemas must NOT serialize them.
    state.port = 41293
    state.pid = 67890
    # vscode-reliability-fixes: ``_build_vscode_status`` now reads
    # ``state.last_error`` and ``state.exit_code``. Explicit ``None``
    # defaults (NOT MagicMock auto-attrs) so existing C4 tests don't
    # trip Pydantic's string/int validation.
    state.last_error = None
    state.exit_code = None
    mgr.state = state
    mgr.config = MagicMock(name="config")
    mgr.config.allow_remote = False
    mgr.config.binary_path = "/usr/local/bin/code-server"
    return mgr


def _build_in_memory_project_repo():
    """Create a real ``SQLModelProjectRepository`` backed by in-memory SQLite.

    Seeds a single project whose ``main_directory`` is the actual repo
    root — so ``_validate_folder_param`` can match it against the real
    ``main_directory`` the proxy receives. This avoids mocking the
    repository and exercises the real validation path.
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    # Import the project table models so SQLModel.metadata.create_all()
    # registers every table the repository touches.
    from daemon.repositories.project.models import (  # noqa: F401
        CriticalNoteModel,
        Project,
        ProjectHistoryEntry,
        ProjectMetadataRecord,
        ProjectShortnameLink,
        ProjectTagLink,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    from daemon.repositories.project.repository import SQLModelProjectRepository

    repo = SQLModelProjectRepository(engine)
    repo.create(name="agents-ensemble", main_directory=REPO_ROOT)
    return repo, engine


def _mount_proxy_with_repo(manager: MagicMock):
    """Mount the REAL proxy sub-app onto a fresh ``create_app()``.

    Mirrors the lifespan wiring in ``daemon/api.py`` (lines ~606-647):
    mount the proxy at ``/vscode``, then move the mount before the
    catch-all ``/{path:path}`` so routing isn't shadowed by the SPA
    fallback. We bypass the lifespan (no Postgres needed) and mount
    manually with our in-memory repo + mock manager.
    """
    repo, engine = _build_in_memory_project_repo()
    from daemon.routers.vscode_proxy import create_vscode_proxy_app

    app = create_app()
    proxy_app = create_vscode_proxy_app(manager, project_repo=repo)
    app.mount("/vscode", proxy_app)

    # Move the mount before the catch-all (same logic as api.py lifespan).
    _routes = app.router.routes
    _vscode_idx = next(
        (i for i, r in enumerate(_routes) if getattr(r, "path", None) == "/vscode"),
        None,
    )
    _catchall_idx = next(
        (i for i, r in enumerate(_routes) if getattr(r, "path", None) == "/{path:path}"),
        None,
    )
    if (
        _vscode_idx is not None
        and _catchall_idx is not None
        and _vscode_idx > _catchall_idx
    ):
        _mount = _routes.pop(_vscode_idx)
        _routes.insert(_catchall_idx, _mount)

    return app, engine


@pytest_asyncio.fixture
async def proxy_client():
    """HTTP client driving the REAL ``create_app()`` with the /vscode proxy mounted.

    Yields the client. The manager reports ``running=True`` with an
    unreachable port (``1``), so:
    - Path-traversal folders are rejected with 403 BEFORE forwarding.
    - Valid folders pass validation and then fail at the upstream
      connection (``httpx.ConnectError``) — proving they were NOT blocked
      by validation.
    """
    manager = _make_mock_manager(running=True, port=1)
    app, engine = _mount_proxy_with_repo(manager)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def settings_client():
    """HTTP client driving the REAL ``create_app()`` for C4 (port/pid leak).

    Wires a mock vscode manager (whose ``state`` DOES carry port/pid) onto
    ``app.state`` and a mock project repo onto the settings module, then
    patches ``get_editor_preference`` so the endpoint succeeds without a
    real metadata row. This exercises the REAL response serialization — if
    ``port``/``pid`` ever leak back into the schema, these tests catch it.
    """
    from unittest.mock import AsyncMock

    dummy_repo = MagicMock(name="project_repo")
    settings_module.set_project_repository(dummy_repo)

    get_mock = AsyncMock(name="get_editor_preference", return_value="builtin")
    manager = _make_mock_manager(running=True, port=41293)
    app = create_app()
    app.state.vscode_manager = manager
    app.state.start_time = 1000.0

    @contextlib.contextmanager
    def _patched():
        get_orig = settings_module.get_editor_preference
        settings_module.get_editor_preference = get_mock
        try:
            yield
        finally:
            settings_module.get_editor_preference = get_orig

    with _patched():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac

    # Reset module globals so other tests start clean.
    settings_module._project_repo = None
    if hasattr(app.state, "vscode_manager"):
        del app.state.vscode_manager


# ─────────────────────────────────────────────────────────────────────────────
# C1 — Path traversal via real proxy endpoint
# ─────────────────────────────────────────────────────────────────────────────


class TestC1PathTraversal:
    """Path-traversal folders MUST be rejected (4xx) before reaching code-server.

    Each test drives ``GET /vscode/?folder=<evil>`` through the REAL
    ``create_app()`` + mounted proxy sub-app. The manager is mocked as
    "running" on an unreachable port, so any folder that PASSES validation
    raises ``httpx.ConnectError`` (reaching the forwarding step). The
    security guarantee is: traversal folders return 403 and NEVER reach
    the forwarding step.
    """

    @pytest.mark.asyncio
    async def test_c1_etc_root_rejected(self, proxy_client):
        """``/etc`` is outside any known project → 403 (rejected)."""
        resp = await proxy_client.get("/vscode/?folder=/etc")
        assert resp.status_code == 403, (
            f"Expected 403 for /etc, got {resp.status_code}: {resp.text!r}"
        )
        assert "Invalid folder" in resp.text, (
            f"Expected validation rejection body, got: {resp.text!r}"
        )

    @pytest.mark.asyncio
    async def test_c1_etc_passwd_rejected(self, proxy_client):
        """``/etc/passwd`` → 403 (rejected, not proxied)."""
        resp = await proxy_client.get("/vscode/?folder=/etc/passwd")
        assert resp.status_code == 403, (
            f"Expected 403 for /etc/passwd, got {resp.status_code}: {resp.text!r}"
        )
        assert "Invalid folder" in resp.text

    @pytest.mark.asyncio
    async def test_c1_relative_traversal_rejected(self, proxy_client):
        """``../../etc`` traversal → 403 (rejected)."""
        resp = await proxy_client.get("/vscode/?folder=../../etc")
        assert resp.status_code == 403, (
            f"Expected 403 for ../../etc, got {resp.status_code}: {resp.text!r}"
        )
        assert "Invalid folder" in resp.text

    @pytest.mark.asyncio
    async def test_c1_null_byte_injection_rejected(self, proxy_client):
        """Null-byte injection (``/etc%00.txt``) → 403 (rejected).

        ``parse_qs`` decodes ``%00`` to a literal NUL in the folder value;
        ``resolve_strict`` then rejects it (NUL paths are invalid). This
        pins that the proxy doesn't fall through to forwarding when the
        decoded value contains a NUL byte.
        """
        resp = await proxy_client.get("/vscode/?folder=/etc%00.txt")
        assert resp.status_code == 403, (
            f"Expected 403 for null-byte injection, got {resp.status_code}: "
            f"{resp.text!r}"
        )
        assert "Invalid folder" in resp.text

    @pytest.mark.asyncio
    async def test_c1_valid_repo_folder_not_blocked(self, proxy_client):
        """The actual repo root is a VALID folder → NOT rejected with 403/422.

        Because the mock manager reports ``running=True`` on an unreachable
        port (``1``), a valid folder passes validation and then the proxy
        attempts the upstream connection, which raises
        ``httpx.ConnectError``. That exception propagates to the client as
        an httpx error — which is the signal we want: the folder was NOT
        blocked by validation. If instead we received a 403/422, valid
        paths would be wrongly blocked (a regression).
        """
        with pytest.raises(httpx.ConnectError):
            await proxy_client.get(f"/vscode/?folder={REPO_ROOT}")


# ─────────────────────────────────────────────────────────────────────────────
# C4 — Port/PID leak via real status endpoints
# ─────────────────────────────────────────────────────────────────────────────


class TestC4PortPidLeak:
    """Editor status endpoints MUST NOT serialize ``port`` or ``pid``.

    The mock manager's ``state`` carries ``port=41293`` and ``pid=67890``
    internally — exactly what a real running code-server would have. The
    response schemas (``VSCodeStatus``, ``VSCodeStatusResponse``) omit
    those fields by design (C4: defeats the proxy boundary). These tests
    assert the keys are absent from the RAW response text (defense in
    depth — catches accidental re-additions even in nested keys or extra
    fields that slip past the Pydantic model).
    """

    @pytest.mark.asyncio
    async def test_c4_editor_endpoint_no_port_pid(self, settings_client):
        """``GET /api/settings/editor`` → no ``port``/``pid`` anywhere in body."""
        resp = await settings_client.get("/api/settings/editor")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The vscode sub-object shape.
        assert "vscode" in body, f"Missing vscode block: {body!r}"
        assert "port" not in body["vscode"], (
            f"C4 LEAK: 'port' present in vscode block: {body['vscode']!r}"
        )
        assert "pid" not in body["vscode"], (
            f"C4 LEAK: 'pid' present in vscode block: {body['vscode']!r}"
        )
        # Defense in depth: raw response text must not contain either key
        # anywhere (catches nested/extra fields that bypass the schema).
        raw = resp.text.lower()
        assert '"port"' not in raw and '"pid"' not in raw, (
            f"C4 LEAK: 'port' or 'pid' found in raw response: {resp.text!r}"
        )

    @pytest.mark.asyncio
    async def test_c4_editor_status_endpoint_no_port_pid(self, settings_client):
        """``GET /api/settings/editor/status`` → no ``port``/``pid``."""
        resp = await settings_client.get("/api/settings/editor/status")
        assert resp.status_code == 200, resp.text
        raw = resp.text.lower()
        assert '"port"' not in raw and '"pid"' not in raw, (
            f"C4 LEAK: 'port' or 'pid' in editor/status: {resp.text!r}"
        )

    @pytest.mark.asyncio
    async def test_c4_vscode_status_alias_no_port_pid(self, settings_client):
        """``GET /api/settings/vscode/status`` (alias) → no ``port``/``pid``."""
        resp = await settings_client.get("/api/settings/vscode/status")
        assert resp.status_code == 200, resp.text
        raw = resp.text.lower()
        assert '"port"' not in raw and '"pid"' not in raw, (
            f"C4 LEAK: 'port' or 'pid' in vscode/status: {resp.text!r}"
        )
