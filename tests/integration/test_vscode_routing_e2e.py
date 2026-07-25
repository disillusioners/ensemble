"""C3 end-to-end routing tests — REAL proxy routing through the live daemon.

Companion to ``test_vscode_routing.py`` (which pins the routing *principle*
via a minimal mount + catch-all, and via direct endpoint invocation). This
file validates the **real, assembled** application end-to-end:

* ``/vscode/*`` reaches the mounted proxy (NOT the SPA catch-all).
* ``/api/*`` is still served by the API router.
* The SPA catch-all still serves ``index.html`` for unknown frontend routes.

Modes
-----
A fixture probes the live dev server (``http://localhost:8079``) at startup:

* **LIVE** — if ``GET /api/settings/editor`` returns 200, every test runs
  against the real daemon via ``httpx.AsyncClient(base_url=...)``. This is
  the preferred mode: it exercises the real lifespan (DB, manager wiring,
  mount reordering, etc.).
* **ASGI fallback** — if the dev server is down, tests run against
  ``create_app()`` via ``httpx.ASGITransport``. The mount is added during
  lifespan startup, so in this mode ``/vscode/*`` is handled by the
  catch-all's ``vscode`` prefix guard (returns 404) rather than the proxy
  readiness gate (503). Both are "NOT the SPA" — the tests assert the
  distinguishing invariant in each mode.

The mode actually used is reported at the top of the run.
"""
from __future__ import annotations

import os

import httpx
import pytest
import pytest_asyncio

from daemon.api import create_app

# ─────────────────────────────────────────────────────────────────────────────
# Mode detection — LIVE dev server vs ASGI fallback
# ─────────────────────────────────────────────────────────────────────────────

LIVE_BASE_URL = os.environ.get("ENS_TEST_LIVE_URL", "http://localhost:8079")
_LIVE_PROBE_PATH = "/api/settings/editor"
_LIVE_PROBE_TIMEOUT = 5.0


def _live_server_is_up() -> bool:
    """Return True iff the dev server answers the probe with HTTP 200."""
    try:
        resp = httpx.get(
            f"{LIVE_BASE_URL}{_LIVE_PROBE_PATH}", timeout=_LIVE_PROBE_TIMEOUT
        )
    except Exception:
        return False
    return resp.status_code == 200


_LIVE = _live_server_is_up()
_MODE = "LIVE" if _LIVE else "ASGI_FALLBACK"

# Collect for the module-level skip reason printed at collection time.
print(f"\n[vscode_routing_e2e] mode={_MODE} base_url={LIVE_BASE_URL}\n")


@pytest_asyncio.fixture
async def client():
    """Yield an httpx.AsyncClient pointed at LIVE or ASGI depending on mode.

    In ASGI mode we build the app lazily here (not at module import) so the
    fixture owns the lifecycle and there is no global app state leaking
    between tests.
    """
    if _LIVE:
        async with httpx.AsyncClient(
            base_url=LIVE_BASE_URL, timeout=10.0
        ) as ac:
            yield ac, _MODE
    else:
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            timeout=10.0,
        ) as ac:
            yield ac, _MODE


# Helper: expected "reached the proxy, not the SPA" status set per mode.
def _proxy_not_spa_statuses(mode: str) -> set[int]:
    """Statuses that indicate a /vscode/* request reached the proxy (NOT SPA).

    LIVE  → proxy readiness gate fires: {503}
    ASGI  → no mount (lifespan not run); catch-all prefix guard fires: {404}

    The key invariant in BOTH modes: status is NOT 200 + text/html (the SPA
    index.html response). The per-mode tests below assert this.
    """
    if mode == "LIVE":
        return {503}  # code-server not running → proxy readiness gate
    return {404}  # mount absent in ASGI mode → prefix guard


# ─────────────────────────────────────────────────────────────────────────────
# C3 — Mount isolation (real HTTP)
# ─────────────────────────────────────────────────────────────────────────────


class TestC3MountIsolation:
    """``/vscode/*`` reaches the proxy mount, not the SPA catch-all."""

    @pytest.mark.asyncio
    async def test_vscode_subpath_reaches_proxy_not_spa(self, client):
        """``GET /vscode/something`` must NOT be served as SPA index.html.

        LIVE: the mounted proxy's readiness gate returns 503 (code-server
        not running). ASGI: the catch-all prefix guard returns 404. Either
        way, the response must not look like the SPA fallback.
        """
        ac, mode = client
        resp = await ac.get("/vscode/something")

        ok_statuses = _proxy_not_spa_statuses(mode)
        assert resp.status_code in ok_statuses, (
            f"[{mode}] /vscode/something expected status in {ok_statuses} "
            f"(proxy/guard reached, not SPA), got {resp.status_code}. "
            f"Body: {resp.text[:200]!r}"
        )
        # Belt-and-suspenders: the response must NOT be the SPA index.html.
        ctype = resp.headers.get("content-type", "")
        assert "text/html" not in ctype, (
            f"[{mode}] /vscode/something returned text/html — SPA fallback "
            f"swallowed the /vscode mount!"
        )
        assert "<app-root" not in resp.text and "<!doctype html>" not in resp.text.lower(), (
            f"[{mode}] /vscode/something body looks like SPA index.html."
        )

    @pytest.mark.asyncio
    async def test_vscode_root_reaches_proxy_not_spa(self, client):
        """``GET /vscode/`` (trailing slash) reaches the proxy, not SPA."""
        ac, mode = client
        resp = await ac.get("/vscode/")

        ok_statuses = _proxy_not_spa_statuses(mode)
        assert resp.status_code in ok_statuses, (
            f"[{mode}] /vscode/ expected status in {ok_statuses}, "
            f"got {resp.status_code}. Body: {resp.text[:200]!r}"
        )
        ctype = resp.headers.get("content-type", "")
        assert "text/html" not in ctype, (
            f"[{mode}] /vscode/ returned text/html — SPA fallback swallowed it."
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("not_a_mount", ["/vscodefoo", "/vscode-not-a-mount"])
    async def test_vscode_like_path_isolated_from_mount(self, client, not_a_mount):
        """``/vscodefoo`` / ``/vscode-x`` must NOT match the ``/vscode`` mount.

        Starlette mount prefix matching is boundary-exact, so these fall
        through to the catch-all. In LIVE mode the catch-all prefix guard
        (``path.startswith('vscode')``) still fires → 404. In ASGI mode
        the same guard fires → 404. Both confirm the path is isolated from
        the proxy mount AND not served as SPA.
        """
        ac, mode = client
        resp = await ac.get(not_a_mount)

        # These paths start with 'vscode' so the catch-all prefix guard
        # rejects them with 404 (NOT served as SPA index.html).
        assert resp.status_code == 404, (
            f"[{mode}] {not_a_mount} expected 404 (prefix guard), "
            f"got {resp.status_code}. The /vscode mount may be matching "
            f"path-prefix strings instead of exact prefixes."
        )
        ctype = resp.headers.get("content-type", "")
        assert "text/html" not in ctype, (
            f"[{mode}] {not_a_mount} returned text/html — leaked into SPA."
        )


# ─────────────────────────────────────────────────────────────────────────────
# API still works
# ─────────────────────────────────────────────────────────────────────────────


class TestApiStillWorks:
    """The API router is not shadowed by the mount or the catch-all."""

    @pytest.mark.asyncio
    async def test_get_editor(self, client):
        """``GET /api/settings/editor`` returns 200 JSON with an ``editor`` key."""
        ac, mode = client
        resp = await ac.get("/api/settings/editor")

        assert resp.status_code == 200, (
            f"[{mode}] /api/settings/editor expected 200, got "
            f"{resp.status_code}. Body: {resp.text[:200]!r}"
        )
        body = resp.json()
        assert "editor" in body, (
            f"[{mode}] response missing 'editor' key: {body!r}"
        )

    @pytest.mark.asyncio
    async def test_get_editor_status(self, client):
        """``GET /api/settings/editor/status`` returns 200 JSON with ``status``."""
        ac, mode = client
        resp = await ac.get("/api/settings/editor/status")

        assert resp.status_code == 200, (
            f"[{mode}] /api/settings/editor/status expected 200, got "
            f"{resp.status_code}. Body: {resp.text[:200]!r}"
        )
        body = resp.json()
        assert "status" in body, (
            f"[{mode}] response missing 'status' key: {body!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# SPA catch-all still works
# ─────────────────────────────────────────────────────────────────────────────


class TestSpaCatchAll:
    """Unknown frontend routes still serve the SPA index.html."""

    @pytest.mark.asyncio
    async def test_unknown_route_serves_index_html(self, client):
        """``GET /some-frontend-route`` returns 200 text/html (SPA fallback).

        This confirms the catch-all still serves the frontend for paths that
        are not API, not ``/vscode/*``, and not a real static asset.
        """
        ac, mode = client
        resp = await ac.get("/some-frontend-route")

        assert resp.status_code == 200, (
            f"[{mode}] /some-frontend-route expected 200 (SPA fallback), "
            f"got {resp.status_code}. Body: {resp.text[:200]!r}"
        )
        ctype = resp.headers.get("content-type", "")
        assert "text/html" in ctype, (
            f"[{mode}] expected text/html for SPA fallback, got "
            f"content-type={ctype!r}"
        )
        body_lower = resp.text.lower()
        assert "<!doctype html>" in body_lower or "<app-root" in resp.text, (
            f"[{mode}] body does not look like SPA index.html "
            f"(no <!doctype html> / <app-root>). First 200 chars: "
            f"{resp.text[:200]!r}"
        )

    @pytest.mark.asyncio
    async def test_root_serves_index_html(self, client):
        """``GET /`` returns 200 text/html (the dedicated root handler)."""
        ac, mode = client
        resp = await ac.get("/")

        assert resp.status_code == 200, (
            f"[{mode}] / expected 200, got {resp.status_code}."
        )
        ctype = resp.headers.get("content-type", "")
        assert "text/html" in ctype, (
            f"[{mode}] expected text/html for root, got content-type={ctype!r}"
        )
