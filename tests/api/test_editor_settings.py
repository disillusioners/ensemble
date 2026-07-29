"""API tests for the editor / VS Code server settings endpoints.

Covers (all under ``/api/settings``):

    * ``GET  /editor``              — read editor pref + vscode status
    * ``PUT  /editor``              — set editor pref + lifecycle side-effects
    * ``GET  /editor/status``       — lightweight vscode status (no metadata read)
    * ``POST /vscode/start``        — ensure_running()
    * ``POST /vscode/stop``         — stop()

Plus direct unit coverage of ``daemon.services.editor_utils`` that pins the
two regression-critical signatures:

    * **R1**: read uses ``record.meta_value`` (NOT ``record.metadata_value``).
    * **R2**: ``set_metadata(project_id, key, value)`` — NO session arg
      (the method opens its own Session internally).

Strategy:
    * Drive the real FastAPI ``app`` via ``httpx.AsyncClient`` + ``ASGITransport``.
    * Mock ``get_editor_preference`` / ``set_editor_preference`` at the router
      module binding (they are imported by name, so the patch target is
      ``daemon.routers.settings.<name>``).
    * Stash a MagicMock vscode manager on ``app.state.vscode_manager``.
    * For R1/R2: construct a MagicMock repo and assert the exact attribute /
      call signature the editor_utils helpers exercise.

Run only this file::

    python -m pytest tests/api/test_editor_settings.py -v
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio

from daemon.api import app
from daemon.routers import settings as settings_module
from daemon import constants


# ─────────────────────────────────────────────────────────────────────────────
# VS Code manager fake
# ─────────────────────────────────────────────────────────────────────────────


def _make_vscode_manager(
    *,
    running: bool = False,
    status: str = "stopped",
    port: int | None = None,
    pid: int | None = None,
) -> MagicMock:
    """Build a MagicMock vscode manager with the surface the router reads.

    The router touches: ``state`` (a ``VSCodeServerState``-shaped object),
    ``is_running()``, ``ensure_running()`` (async), ``stop()`` (async).
    """
    mgr = MagicMock(name="vscode_manager")
    state = MagicMock(name="state")
    state.status = status
    state.port = port
    state.pid = pid
    # vscode-reliability-fixes: ``_build_vscode_status`` now reads
    # ``state.last_error`` and ``state.exit_code``. Explicit ``None``
    # defaults (NOT MagicMock auto-attrs) so existing tests that don't
    # care about crashes don't trip Pydantic's string/int validation.
    state.last_error = None
    state.exit_code = None
    mgr.state = state
    mgr.is_running.return_value = running
    mgr.ensure_running = AsyncMock(name="ensure_running")
    mgr.stop = AsyncMock(name="stop")
    # ``_build_vscode_status`` reads ``getattr(manager, "config", None)``
    # and then ``getattr(config, "allow_remote", False)`` + binary_path.
    mgr.config = MagicMock(name="config")
    mgr.config.allow_remote = False
    mgr.config.binary_path = "/usr/local/bin/code-server"
    return mgr


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client():
    """Async HTTP client with mocked editor_utils + a default vscode manager.

    Yields ``(client, mocks)`` where ``mocks`` exposes:
        * ``mocks.get_editor_preference`` — AsyncMock
        * ``mocks.set_editor_preference`` — AsyncMock
        * ``mocks.vscode_manager`` — MagicMock on ``app.state``
    """
    # Wire a dummy repo so ``get_project_repository()`` doesn't 503.
    # The actual repo is never touched because editor_utils is mocked.
    dummy_repo = MagicMock(name="project_repo")
    settings_module.set_project_repository(dummy_repo)

    # Mock the editor_utils functions at the router binding.
    get_mock = AsyncMock(name="get_editor_preference", return_value=constants.EDITOR_DEFAULT)
    set_mock = AsyncMock(name="set_editor_preference", return_value="builtin")

    vscode_manager = _make_vscode_manager()

    # app.state wiring
    app.state.vscode_manager = vscode_manager
    app.state.start_time = 1000.0

    mocks = SimpleNamespace(
        get_editor_preference=get_mock,
        set_editor_preference=set_mock,
        vscode_manager=vscode_manager,
        repo=dummy_repo,
    )

    @contextlib.contextmanager
    def _patched():
        get_orig = settings_module.get_editor_preference
        set_orig = settings_module.set_editor_preference
        settings_module.get_editor_preference = get_mock
        settings_module.set_editor_preference = set_mock
        try:
            yield
        finally:
            settings_module.get_editor_preference = get_orig
            settings_module.set_editor_preference = set_orig

    with _patched():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac, mocks

    # Reset the module-level repo global so other tests start clean.
    settings_module._project_repo = None
    # Drop the vscode_manager so subsequent tests see "not wired".
    if hasattr(app.state, "vscode_manager"):
        del app.state.vscode_manager


@pytest_asyncio.fixture
async def client_no_manager():
    """HTTP client with NO vscode_manager on app.state (simulates Phase 3b gap).

    Used to exercise the 503 "manager not initialized" branches.
    """
    dummy_repo = MagicMock(name="project_repo")
    settings_module.set_project_repository(dummy_repo)

    get_mock = AsyncMock(name="get_editor_preference", return_value=constants.EDITOR_DEFAULT)
    set_mock = AsyncMock(name="set_editor_preference", return_value="builtin")

    # Ensure no vscode_manager attribute is present.
    if hasattr(app.state, "vscode_manager"):
        del app.state.vscode_manager

    mocks = SimpleNamespace(
        get_editor_preference=get_mock,
        set_editor_preference=set_mock,
        repo=dummy_repo,
    )

    @contextlib.contextmanager
    def _patched():
        get_orig = settings_module.get_editor_preference
        set_orig = settings_module.set_editor_preference
        settings_module.get_editor_preference = get_mock
        settings_module.set_editor_preference = set_mock
        try:
            yield
        finally:
            settings_module.get_editor_preference = get_orig
            settings_module.set_editor_preference = set_orig

    with _patched():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac, mocks

    settings_module._project_repo = None


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/settings/editor
# ─────────────────────────────────────────────────────────────────────────────


class TestGetEditor:
    """GET /api/settings/editor — returns editor pref + vscode status."""

    @pytest.mark.asyncio
    async def test_returns_default_builtin_when_no_metadata(self, client):
        """No stored metadata → editor == EDITOR_DEFAULT ("builtin")."""
        ac, mocks = client
        mocks.get_editor_preference.return_value = constants.EDITOR_DEFAULT

        resp = await ac.get("/api/settings/editor")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["editor"] == "builtin"
        assert "vscode" in body
        # vscode block shape — C4: port and pid are intentionally absent
        # (they would defeat the proxy boundary by leaking the OS-assigned
        # loopback port and the code-server PID to API consumers).
        assert {"available", "binary_path", "status", "allow_remote"} <= set(
            body["vscode"].keys()
        )
        assert "port" not in body["vscode"]
        assert "pid" not in body["vscode"]

    @pytest.mark.asyncio
    async def test_returns_stored_value_when_metadata_exists(self, client):
        """Stored "vscode" preference is surfaced."""
        ac, mocks = client
        mocks.get_editor_preference.return_value = "vscode"

        resp = await ac.get("/api/settings/editor")

        assert resp.status_code == 200, resp.text
        assert resp.json()["editor"] == "vscode"

    @pytest.mark.asyncio
    async def test_returns_vscode_status_dict_shape(self, client):
        """vscode block reflects the manager's state."""
        ac, mocks = client
        mocks.vscode_manager.state.status = "running"
        mocks.vscode_manager.state.port = 8443
        mocks.vscode_manager.state.pid = 4242
        mocks.vscode_manager.is_running.return_value = True

        resp = await ac.get("/api/settings/editor")

        assert resp.status_code == 200, resp.text
        vscode = resp.json()["vscode"]
        assert vscode["status"] == "running"
        # C4: port and pid are no longer exposed in the API response.
        assert "port" not in vscode
        assert "pid" not in vscode
        # available is True because the fake config has a binary_path.
        assert vscode["available"] is True
        assert vscode["binary_path"] == "/usr/local/bin/code-server"

    @pytest.mark.asyncio
    async def test_status_endpoint_reads_no_metadata(self, client):
        """GET /editor/status must NOT touch the metadata KV (lightweight)."""
        ac, mocks = client
        mocks.vscode_manager.state.status = "running"
        mocks.vscode_manager.state.port = 9000
        mocks.vscode_manager.state.pid = 1234

        resp = await ac.get("/api/settings/editor/status")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "running"
        # C4: port and pid are no longer exposed in the API response.
        assert "port" not in body
        assert "pid" not in body
        # The lightweight endpoint must not read editor preference metadata.
        mocks.get_editor_preference.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# PUT /api/settings/editor
# ─────────────────────────────────────────────────────────────────────────────


class TestPutEditor:
    """PUT /api/settings/editor — stores pref + lifecycle side-effects."""

    @pytest.mark.asyncio
    async def test_put_vscode_starts_server(self, client):
        """PUT editor=vscode → ensure_running() called, returns updated status."""
        ac, mocks = client

        resp = await ac.put("/api/settings/editor", json={"editor": "vscode"})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["editor"] == "vscode"
        # set_editor_preference called with cleaned "vscode".
        mocks.set_editor_preference.assert_awaited_once()
        args, _ = mocks.set_editor_preference.call_args
        assert args[1] == "vscode"
        # Lifecycle: ensure_running was invoked.
        mocks.vscode_manager.ensure_running.assert_awaited_once()
        # And stop was NOT called.
        mocks.vscode_manager.stop.assert_not_called()

    @pytest.mark.asyncio
    async def test_put_builtin_stops_running_server(self, client):
        """PUT editor=builtin when server running → stop() called."""
        ac, mocks = client
        mocks.vscode_manager.is_running.return_value = True

        resp = await ac.put("/api/settings/editor", json={"editor": "builtin"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["editor"] == "builtin"
        mocks.set_editor_preference.assert_awaited_once()
        # Lifecycle: stop was invoked because the server was running.
        mocks.vscode_manager.stop.assert_awaited_once()
        # And ensure_running was NOT called.
        mocks.vscode_manager.ensure_running.assert_not_called()

    @pytest.mark.asyncio
    async def test_put_builtin_does_not_stop_when_not_running(self, client):
        """PUT editor=builtin when server already stopped → stop() NOT called."""
        ac, mocks = client
        mocks.vscode_manager.is_running.return_value = False

        resp = await ac.put("/api/settings/editor", json={"editor": "builtin"})

        assert resp.status_code == 200, resp.text
        mocks.vscode_manager.stop.assert_not_called()

    @pytest.mark.asyncio
    async def test_put_invalid_value_returns_422(self, client):
        """Invalid editor value ("monaco") is rejected by the schema → 422."""
        ac, mocks = client

        resp = await ac.put("/api/settings/editor", json={"editor": "monaco"})

        assert resp.status_code == 422
        # Nothing should have been persisted.
        mocks.set_editor_preference.assert_not_called()
        mocks.vscode_manager.ensure_running.assert_not_called()

    @pytest.mark.asyncio
    async def test_put_vscode_binary_not_found_returns_503(self, client):
        """ensure_running raises VSCodeServerNotInstalledError → 503 with install hint."""
        ac, mocks = client
        from daemon.services.vscode_server_manager import VSCodeServerNotInstalledError

        mocks.vscode_manager.ensure_running.side_effect = VSCodeServerNotInstalledError(
            "code-server not found in PATH"
        )

        resp = await ac.put("/api/settings/editor", json={"editor": "vscode"})

        assert resp.status_code == 503, resp.text
        body = resp.json()
        detail = body["detail"]
        # Install instructions present.
        assert "code-server.dev/install.sh" in str(detail)

    @pytest.mark.asyncio
    async def test_put_vscode_no_manager_returns_503(self, client_no_manager):
        """PUT editor=vscode with no manager wired → 503 with install hint."""
        ac, mocks = client_no_manager

        resp = await ac.put("/api/settings/editor", json={"editor": "vscode"})

        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert "install" in str(body["detail"]).lower() or "restart" in str(
            body["detail"]
        ).lower()

    @pytest.mark.asyncio
    async def test_put_strips_control_characters(self, client, monkeypatch):
        """Defense-in-depth: control chars stripped before storage.

        The Pydantic ``_validate_editor`` rejects values not in
        ``EDITOR_OPTIONS``. To exercise the router's own ``re.sub`` strip
        path, we temporarily widen ``EDITOR_OPTIONS`` to accept the
        control-char-laden input. The router must then strip the control
        chars and persist ``"vscode"``.
        """
        ac, mocks = client
        original_options = list(constants.EDITOR_OPTIONS)
        # Allow the dirty value through Pydantic, then watch the router
        # strip it to the canonical "vscode".
        monkeypatch.setattr(
            constants, "EDITOR_OPTIONS", original_options + ["vscode\n\t"]
        )

        resp = await ac.put("/api/settings/editor", json={"editor": "vscode\n\t"})

        assert resp.status_code == 200, resp.text
        # The stored value must be the cleaned "vscode" (control chars gone).
        args, _ = mocks.set_editor_preference.call_args
        assert args[1] == "vscode"

    @pytest.mark.asyncio
    async def test_w13_ensure_running_fail_does_not_persist_preference(self, client):
        """W13/W14: side-effect must run BEFORE the preference is persisted.

        If the VS Code server fails to start (e.g. port collision, binary
        crash on startup), the preference must NOT be flipped to ``vscode``
        — otherwise the user is left in an inconsistent state where the
        metadata says "vscode" but no server is running, and the only way
        to recover is to manually flip back to "builtin".
        """
        ac, mocks = client
        from daemon.services.vscode_server_manager import VSCodeServerStartError

        # ensure_running succeeds-free raises the base VSCodeServerError
        # subclass (not the NotInstalledError, which is handled separately
        # with a 503 + install hint).
        mocks.vscode_manager.ensure_running.side_effect = VSCodeServerStartError(
            "port 8443 already in use"
        )

        resp = await ac.put("/api/settings/editor", json={"editor": "vscode"})

        assert resp.status_code == 503, resp.text
        # Side-effect ran first (otherwise the test wouldn't have triggered).
        mocks.vscode_manager.ensure_running.assert_awaited_once()
        # But the preference must NOT be persisted — this is the W13/W14 fix.
        mocks.set_editor_preference.assert_not_called()

    @pytest.mark.asyncio
    async def test_w13_vscode_not_installed_does_not_persist_preference(self, client):
        """W13/W14: binary-missing failure mode also must not persist.

        Mirrors the above for the more specific ``VSCodeServerNotInstalledError``
        branch, which has its own 503 + install hint but must still leave
        the preference untouched.
        """
        ac, mocks = client
        from daemon.services.vscode_server_manager import VSCodeServerNotInstalledError

        mocks.vscode_manager.ensure_running.side_effect = VSCodeServerNotInstalledError(
            "code-server not found in PATH"
        )

        resp = await ac.put("/api/settings/editor", json={"editor": "vscode"})

        assert resp.status_code == 503, resp.text
        mocks.vscode_manager.ensure_running.assert_awaited_once()
        # The preference must NOT be persisted.
        mocks.set_editor_preference.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/settings/vscode/start
# ─────────────────────────────────────────────────────────────────────────────


class TestVSCodeStart:
    """POST /api/settings/vscode/start — ensure_running()."""

    @pytest.mark.asyncio
    async def test_start_calls_ensure_running(self, client):
        ac, mocks = client
        mocks.vscode_manager.state.status = "running"
        mocks.vscode_manager.state.port = 7777
        mocks.vscode_manager.state.pid = 999

        resp = await ac.post("/api/settings/vscode/start")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "running"
        # C4: port and pid are no longer exposed in the API response.
        assert "port" not in body
        assert "pid" not in body
        mocks.vscode_manager.ensure_running.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_binary_not_found_returns_503(self, client):
        ac, mocks = client
        from daemon.services.vscode_server_manager import VSCodeServerNotInstalledError

        mocks.vscode_manager.ensure_running.side_effect = VSCodeServerNotInstalledError(
            "not found"
        )

        resp = await ac.post("/api/settings/vscode/start")

        assert resp.status_code == 503
        assert "install" in str(resp.json()["detail"]).lower()

    @pytest.mark.asyncio
    async def test_start_no_manager_returns_503(self, client_no_manager):
        ac, _ = client_no_manager

        resp = await ac.post("/api/settings/vscode/start")

        assert resp.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/settings/vscode/stop
# ─────────────────────────────────────────────────────────────────────────────


class TestVSCodeStop:
    """POST /api/settings/vscode/stop — stop()."""

    @pytest.mark.asyncio
    async def test_stop_calls_stop_when_running(self, client):
        ac, mocks = client
        mocks.vscode_manager.is_running.return_value = True
        mocks.vscode_manager.state.status = "stopped"
        mocks.vscode_manager.state.port = None
        mocks.vscode_manager.state.pid = None

        resp = await ac.post("/api/settings/vscode/stop")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "stopped"
        mocks.vscode_manager.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_idempotent_when_not_running(self, client):
        """stop() is NOT called if is_running() is False."""
        ac, mocks = client
        mocks.vscode_manager.is_running.return_value = False
        mocks.vscode_manager.state.status = "stopped"

        resp = await ac.post("/api/settings/vscode/stop")

        assert resp.status_code == 200, resp.text
        mocks.vscode_manager.stop.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_no_manager_returns_stopped(self, client_no_manager):
        """No manager wired → graceful "stopped" response (no 503)."""
        ac, _ = client_no_manager

        resp = await ac.post("/api/settings/vscode/stop")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "stopped"
        # C4: port and pid are no longer exposed in the API response.
        assert "port" not in body
        assert "pid" not in body


# ─────────────────────────────────────────────────────────────────────────────
# PUT /api/settings/default-agent-versions
# ─────────────────────────────────────────────────────────────────────────────


class TestDefaultAgentVersions:
    """Default agent-version validation and concurrent updates."""

    @staticmethod
    def _wire_metadata_repo(mocks, monkeypatch):
        """Configure the client repo as an in-memory metadata store."""
        stored: dict[str, str | None] = {}

        class _DummySession:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def get_record(_session, _project_id, _key):
            if not stored:
                return None
            return SimpleNamespace(meta_value=json.dumps(stored))

        def set_metadata(_project_id, _key, value):
            stored.clear()
            stored.update(value)

        mocks.repo.get_metadata_record.side_effect = get_record
        mocks.repo.set_metadata.side_effect = set_metadata
        monkeypatch.setattr(settings_module, "Session", _DummySession)
        monkeypatch.setattr(constants, "SYSTEM_DEFAULT_PROJECT_ID", "system-default")
        return stored

    @pytest.mark.asyncio
    async def test_invalid_version_tag_returns_422(self, client, monkeypatch):
        ac, mocks = client
        self._wire_metadata_repo(mocks, monkeypatch)
        registry = MagicMock()
        registry.list_versions.return_value = [None, "v2"]
        monkeypatch.setattr(settings_module, "get_registry", lambda: registry)

        resp = await ac.put(
            "/api/settings/default-agent-versions",
            json={"agent_id": "developer", "version_tag": "missing"},
        )

        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_none_version_tag_allows_base_reset(self, client, monkeypatch):
        ac, mocks = client
        self._wire_metadata_repo(mocks, monkeypatch)
        registry = MagicMock()
        registry.list_versions.side_effect = AssertionError("base reset must not validate")
        monkeypatch.setattr(settings_module, "get_registry", lambda: registry)

        resp = await ac.put(
            "/api/settings/default-agent-versions",
            json={"agent_id": "developer", "version_tag": None},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"default_versions": {}}

    @pytest.mark.asyncio
    async def test_concurrent_puts_preserve_both_agents(self, client, monkeypatch):
        ac, mocks = client
        self._wire_metadata_repo(mocks, monkeypatch)
        registry = MagicMock()
        registry.list_versions.return_value = [None, "v2", "v3"]
        monkeypatch.setattr(settings_module, "get_registry", lambda: registry)

        first, second = await asyncio.gather(
            ac.put(
                "/api/settings/default-agent-versions",
                json={"agent_id": "developer", "version_tag": "v2"},
            ),
            ac.put(
                "/api/settings/default-agent-versions",
                json={"agent_id": "tester", "version_tag": "v3"},
            ),
        )
        result = await ac.get("/api/settings/default-agent-versions")

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert result.status_code == 200, result.text
        assert result.json() == {
            "default_versions": {"developer": "v2", "tester": "v3"}
        }


# ─────────────────────────────────────────────────────────────────────────────
# R1 / R2 regression tests (direct editor_utils coverage)
# ─────────────────────────────────────────────────────────────────────────────


class TestEditorUtilsR1R2:
    """Pin the two regression-critical signatures in editor_utils.

    * **R1**: ``get_editor_preference`` reads ``record.meta_value`` (NOT
      ``record.metadata_value``). A typo here silently returns the default.
    * **R2**: ``set_metadata(project_id, key, value)`` is called with NO
      session argument — the method opens its own Session internally.
    """

    @pytest.mark.asyncio
    async def test_r1_reads_meta_value_not_metadata_value(self):
        """The helper must read ``.meta_value``, not ``.metadata_value``.

        If the attribute name regresses to ``metadata_value``, the
        MagicMock would auto-create a new child mock (truthy) and the
        membership check would fail, falling back to the default. We
        assert the returned value matches the explicitly-set ``meta_value``.
        """
        from daemon.services import editor_utils as editor_utils_module

        # Build a fake repo whose get_metadata_record returns a record
        # exposing ONLY meta_value (the correct attribute). If the helper
        # read metadata_value, MagicMock would auto-vivify a different
        # attribute and the assertion below would fail.
        record = MagicMock(name="record")
        record.meta_value = "vscode"
        # Explicitly poison metadata_value so a regression is caught loudly
        # rather than silently returning the default.
        record.metadata_value = "BUILTIN_FROM_WRONG_ATTR"

        repo = MagicMock(name="repo")
        repo.get_metadata_record.return_value = record

        # Patch Session so the helper's ``with Session(repo.engine)`` works
        # against the MagicMock engine without a real DB.
        class _DummySession:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(editor_utils_module, "Session", _DummySession)
            result = await editor_utils_module.get_editor_preference(repo)

        assert result == "vscode", (
            "R1 regression: get_editor_preference must read record.meta_value, "
            "not record.metadata_value"
        )
        # C6: get_metadata_record requires a session arg as first positional.
        repo.get_metadata_record.assert_called_once()
        call_args = repo.get_metadata_record.call_args.args
        assert len(call_args) == 3, (
            f"get_metadata_record expected (session, project_id, key) — got "
            f"{len(call_args)} positional args"
        )

    @pytest.mark.asyncio
    async def test_r1_falls_back_to_default_when_record_missing(self):
        """No record → EDITOR_DEFAULT (helper must not crash)."""
        from daemon.services import editor_utils as editor_utils_module

        repo = MagicMock(name="repo")
        repo.get_metadata_record.return_value = None

        class _DummySession:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(editor_utils_module, "Session", _DummySession)
            result = await editor_utils_module.get_editor_preference(repo)

        assert result == constants.EDITOR_DEFAULT

    @pytest.mark.asyncio
    async def test_r2_set_metadata_called_without_session_arg(self):
        """R2: set_metadata is called with (project_id, key, value) — NO session.

        The method opens its own Session internally; passing a session
        positional arg would shift key/value and silently corrupt the write.
        """
        from daemon.services import editor_utils as editor_utils_module

        repo = MagicMock(name="repo")
        # set_metadata is sync; asyncio.to_thread will call it in a worker.
        # W12: set_metadata returns Project | None; on success it returns the
        # enriched Project. Mock a truthy value so the function succeeds.
        repo.set_metadata.return_value = MagicMock(name="project")

        result = await editor_utils_module.set_editor_preference(repo, "vscode")

        assert result == "vscode"
        repo.set_metadata.assert_called_once_with(
            constants.SYSTEM_DEFAULT_PROJECT_ID,
            constants.EDITOR_METADATA_KEY,
            "vscode",
        )
        # Specifically: exactly 3 positional args, NOT 4 (no session).
        args = repo.set_metadata.call_args.args
        assert len(args) == 3, (
            "R2 regression: set_metadata must be called with exactly "
            "(project_id, key, value) — no session argument"
        )

    @pytest.mark.asyncio
    async def test_r2_set_metadata_signature_stable(self):
        """The call signature is (project_id, key, value) in that order."""
        from daemon.services import editor_utils as editor_utils_module

        repo = MagicMock(name="repo")
        # W12: set_metadata returns Project | None; mock a truthy value
        # so the function succeeds (None-return path is tested separately).
        repo.set_metadata.return_value = MagicMock(name="project")
        await editor_utils_module.set_editor_preference(repo, "builtin")

        args = repo.set_metadata.call_args.args
        assert args[0] == constants.SYSTEM_DEFAULT_PROJECT_ID
        assert args[1] == constants.EDITOR_METADATA_KEY
        assert args[2] == "builtin"

    @pytest.mark.asyncio
    async def test_w12_set_metadata_returns_none_raises_runtime_error(self):
        """W12: set_metadata returning None indicates a no-op write.

        ``set_metadata`` returns ``None`` only when the system default
        project row is missing. The helper must surface this as a
        ``RuntimeError`` rather than silently treating it as a successful
        write.
        """
        from daemon.services import editor_utils as editor_utils_module

        repo = MagicMock(name="repo")
        repo.set_metadata.return_value = None  # project row missing

        with pytest.raises(RuntimeError, match="metadata write returned None"):
            await editor_utils_module.set_editor_preference(repo, "vscode")


# ─────────────────────────────────────────────────────────────────────────────
# C4: port/pid must NEVER be exposed in API responses
# ─────────────────────────────────────────────────────────────────────────────


class TestVscodeStatusNoPortNoPid:
    """C4 regression: ``port`` and ``pid`` are NEVER exposed in API responses.

    The OS-assigned loopback port and the code-server PID are internal
    implementation details. Leaking them through the API would defeat the
    proxy boundary — clients could bypass the proxy and connect directly
    to the loopback port. The C4 fix removes these fields from every
    response shape; this class pins the contract explicitly.

    These tests are intentionally named with the bug ID (``test_vscode_status_no_port_exposed``,
    ``test_vscode_status_no_pid_exposed``, ``test_editor_status_response_no_port``)
    so a future regression that re-introduces the fields is caught
    immediately by name.
    """

    @pytest.mark.asyncio
    async def test_vscode_status_no_port_exposed(self, client):
        """C4: GET /api/settings/editor — vscode block MUST NOT contain ``port``."""
        ac, mocks = client
        # Set the manager to a state where port/pid would be populated.
        mocks.vscode_manager.state.status = "running"
        mocks.vscode_manager.state.port = 41293
        mocks.vscode_manager.state.pid = 1234

        resp = await ac.get("/api/settings/editor")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "vscode" in body
        # The whole HTTP response body must NOT contain the word "port"
        # in the vscode block. We assert at the JSON level so a future
        # addition elsewhere in the response (e.g. an unrelated nested
        # field) doesn't accidentally remove the substring.
        assert "port" not in body["vscode"], (
            f"C4 regression: vscode block must NOT expose 'port' — "
            f"got {body['vscode']!r}"
        )

    @pytest.mark.asyncio
    async def test_vscode_status_no_pid_exposed(self, client):
        """C4: GET /api/settings/editor — vscode block MUST NOT contain ``pid``."""
        ac, mocks = client
        mocks.vscode_manager.state.status = "running"
        mocks.vscode_manager.state.port = 41293
        mocks.vscode_manager.state.pid = 1234

        resp = await ac.get("/api/settings/editor")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "pid" not in body["vscode"], (
            f"C4 regression: vscode block must NOT expose 'pid' — "
            f"got {body['vscode']!r}"
        )

    @pytest.mark.asyncio
    async def test_editor_status_response_no_port(self, client):
        """C4: GET /api/settings/editor/status — body MUST NOT contain ``port``."""
        ac, _ = client

        resp = await ac.get("/api/settings/editor/status")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "port" not in body, (
            f"C4 regression: status response must NOT expose 'port' — "
            f"got {body!r}"
        )
        assert "pid" not in body, (
            f"C4 regression: status response must NOT expose 'pid' — "
            f"got {body!r}"
        )

    @pytest.mark.asyncio
    async def test_vscode_start_response_no_port_no_pid(self, client):
        """C4: POST /api/settings/vscode/start — body MUST NOT contain ``port`` or ``pid``."""
        ac, mocks = client
        mocks.vscode_manager.state.status = "running"
        mocks.vscode_manager.state.port = 41293
        mocks.vscode_manager.state.pid = 1234

        resp = await ac.post("/api/settings/vscode/start")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "port" not in body
        assert "pid" not in body

    @pytest.mark.asyncio
    async def test_vscode_stop_response_no_port_no_pid(self, client):
        """C4: POST /api/settings/vscode/stop — body MUST NOT contain ``port`` or ``pid``."""
        ac, mocks = client
        mocks.vscode_manager.is_running.return_value = True
        mocks.vscode_manager.state.status = "stopped"
        mocks.vscode_manager.state.port = None
        mocks.vscode_manager.state.pid = None

        resp = await ac.post("/api/settings/vscode/stop")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "port" not in body
        assert "pid" not in body


# ─────────────────────────────────────────────────────────────────────────────
# vscode-reliability-fixes: ``last_error`` + ``exit_code`` surfaced in API
# ─────────────────────────────────────────────────────────────────────────────


class TestVSCodeStatusCrashFields:
    """``VSCodeStatus`` exposes ``last_error`` and ``exit_code`` so the
    frontend can render WHY code-server crashed.

    These fields are populated by the watchdog (``_watchdog_loop``) when
    code-server dies at runtime and by ``_wait_for_port`` when it dies
    during startup. Both paths converge through ``state.last_error`` /
    ``state.exit_code`` and surface via ``_build_vscode_status()``.
    """

    def test_vscode_status_schema_declares_crash_fields(self):
        """Pydantic model declares both fields with ``None`` defaults."""
        from daemon.routers.schemas import VSCodeStatus

        # Empty constructor → both fields default to None.
        status = VSCodeStatus()
        assert status.last_error is None
        assert status.exit_code is None

        # Explicit construction round-trips through the model.
        status = VSCodeStatus(
            available=True,
            binary_path="/usr/bin/code-server",
            status="crashed",
            allow_remote=False,
            last_error="code-server exited unexpectedly (code=137)",
            exit_code=137,
        )
        assert status.last_error == (
            "code-server exited unexpectedly (code=137)"
        )
        assert status.exit_code == 137

    @pytest.mark.asyncio
    async def test_build_vscode_status_populates_crash_fields_from_state(
        self, client
    ) -> None:
        """``_build_vscode_status()`` reads ``state.last_error`` and
        ``state.exit_code`` when the manager has recorded a crash.

        Drives the pure helper directly (not through HTTP) because the
        surface contract is the helper's job, not the route layer.
        """
        from daemon.routers.settings import _build_vscode_status

        ac, mocks = client
        # Simulate a post-crash snapshot in the manager's state.
        mocks.vscode_manager.state.status = "crashed"
        mocks.vscode_manager.state.last_error = (
            "code-server exited unexpectedly (code=42)"
        )
        mocks.vscode_manager.state.exit_code = 42

        result = _build_vscode_status(mocks.vscode_manager)

        assert result.last_error == (
            "code-server exited unexpectedly (code=42)"
        )
        assert result.exit_code == 42
        # Other fields are still populated from the manager.
        assert result.status == "crashed"
        assert result.allow_remote is False
        assert result.binary_path == "/usr/local/bin/code-server"

    @pytest.mark.asyncio
    async def test_build_vscode_status_returns_none_when_no_crash(
        self, client
    ) -> None:
        """When ``state.last_error`` / ``state.exit_code`` are ``None``
        (no crash yet), the helper round-trips them as ``None`` —
        the contract is that ``None`` means "no crash recorded".
        """
        from daemon.routers.settings import _build_vscode_status

        ac, mocks = client
        mocks.vscode_manager.state.status = "running"
        mocks.vscode_manager.state.last_error = None
        mocks.vscode_manager.state.exit_code = None

        result = _build_vscode_status(mocks.vscode_manager)

        assert result.last_error is None
        assert result.exit_code is None
        assert result.status == "running"

    @pytest.mark.asyncio
    async def test_build_vscode_status_none_when_no_manager(self):
        """When no manager is wired, the no-manager branch also returns
        ``None`` for both crash fields (the defaults) — there is no
        observed crash to surface."""
        from daemon.routers.settings import _build_vscode_status

        result = _build_vscode_status(None)

        assert result.last_error is None
        assert result.exit_code is None
        assert result.status == "stopped"

    @pytest.mark.asyncio
    async def test_editor_endpoint_includes_crash_fields(self, client):
        """End-to-end: GET /api/settings/editor surfaces ``last_error``
        and ``exit_code`` in the JSON body so the frontend can render
        them.

        C4 (port/pid removed) still holds — the new fields MUST NOT
        reintroduce the removed ones.
        """
        ac, mocks = client
        mocks.vscode_manager.state.status = "crashed"
        mocks.vscode_manager.state.last_error = (
            "code-server crashed\n"
            "--- code-server output (tail) ---\n"
            "fatal: cannot bind port 4321\n"
        )
        mocks.vscode_manager.state.exit_code = 137

        resp = await ac.get("/api/settings/editor")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        vscode_block = body["vscode"]
        # New fields are present and carry the manager's values.
        assert vscode_block["last_error"] == mocks.vscode_manager.state.last_error
        assert vscode_block["exit_code"] == 137
        # C4 still holds: no port/pid in the response.
        assert "port" not in vscode_block
        assert "pid" not in vscode_block
