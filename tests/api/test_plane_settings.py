"""API tests for the Plane integration config endpoint.

Covers:

    * ``GET /api/settings/plane`` — read PLANE_BASE_URL env var, validate
      scheme (http/https only, case-insensitive), and return either an
      enabled=true response with the URL or a disabled sentinel.

Strategy:
    * Drive the real FastAPI ``app`` via ``httpx.AsyncClient`` + ``ASGITransport``.
    * Use ``monkeypatch.setenv`` / ``monkeypatch.delenv`` to control the
      ``PLANE_BASE_URL`` environment variable per test (the endpoint reads
      ``os.environ.get("PLANE_BASE_URL", ...)`` at request time, so a fresh
      monkeypatch per test is the cleanest isolation strategy).
    * The endpoint has no project-repository dependency (it only reads an
      env var), so no manager / repo fixtures are required.

Run only this file::

    python -m pytest tests/api/test_plane_settings.py -v
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from daemon.api import app


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client():
    """Async HTTP client wired to the real FastAPI app.

    The Plane endpoint reads ``os.environ`` at request time, so no
    router-binding mocks or app.state wiring are required — monkeypatching
    ``PLANE_BASE_URL`` per test is sufficient.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/settings/plane
# ─────────────────────────────────────────────────────────────────────────────


class TestGetPlaneConfig:
    """GET /api/settings/plane — read PLANE_BASE_URL + validate scheme."""

    @pytest.mark.asyncio
    async def test_plane_config_disabled_when_env_unset(
        self, client, monkeypatch
    ):
        """PLANE_BASE_URL not set → response {enabled: false, url: ""}."""
        # Ensure the var is absent for this test (other tests or shell
        # exports could leak in otherwise).
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)

        resp = await client.get("/api/settings/plane")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"enabled": False, "url": ""}

    @pytest.mark.asyncio
    async def test_plane_config_enabled_with_valid_url(
        self, client, monkeypatch
    ):
        """PLANE_BASE_URL=https://plane.mtri.app → enabled=true, url echoed."""
        monkeypatch.setenv("PLANE_BASE_URL", "https://plane.mtri.app")

        resp = await client.get("/api/settings/plane")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"enabled": True, "url": "https://plane.mtri.app"}

    @pytest.mark.asyncio
    async def test_plane_config_rejects_non_http_scheme(
        self, client, monkeypatch
    ):
        """PLANE_BASE_URL with a non-http(s) scheme is rejected (no XSS).

        ``data:text/html,<script>alert(1)</script>`` is the canonical
        XSS-via-iframe-srcdoc payload — the endpoint must refuse any
        scheme that isn't http or https.
        """
        monkeypatch.setenv(
            "PLANE_BASE_URL", "data:text/html,<script>alert(1)</script>"
        )

        resp = await client.get("/api/settings/plane")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"enabled": False, "url": ""}

    @pytest.mark.asyncio
    async def test_plane_config_disabled_when_empty(
        self, client, monkeypatch
    ):
        """PLANE_BASE_URL="" (empty string) → disabled with empty url."""
        monkeypatch.setenv("PLANE_BASE_URL", "")

        resp = await client.get("/api/settings/plane")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"enabled": False, "url": ""}
