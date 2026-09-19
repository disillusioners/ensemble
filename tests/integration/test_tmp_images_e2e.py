"""Integration tests for ``/api/tmp_images`` end-to-end.

Phase 1 / clipboard-image-chat. The plan's Test strategy pins:

* Real FastAPI TestClient + a real ``TmpImageStore`` on ``tmp_path``.
* Round-trip POST → GET returns byte-identical PNG.
* Multi-image POST (3 images).
* 4-image rejection (422).
* **Critical**: route-order vs real SPA catch-all — GET registered
  BEFORE the SPA catch-all — assert by mounting the catch-all in a
  fixture and verifying ``/api/tmp_images/<id>`` does NOT serve
  ``index.html``.

The integration app mirrors the production wiring:

* ``api_router`` with the ``/tmp_images`` router mounted at
  ``/api/tmp_images`` (matches ``daemon/api.py:2520-2522``).
* A SPA catch-all at ``/{path:path}`` that mirrors the production
  ``daemon/api.py:2612`` guard (rejects ``api``/``ws``/``vscode``
  prefixes — the production catch-all returns 404 for those paths,
  but the catch-all order-vs-router order is what we exercise here).
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi import FastAPI, APIRouter
from fastapi.responses import FileResponse, JSONResponse
from fastapi.testclient import TestClient

from daemon.routers import tmp_images as tmp_images_module
from daemon.services.tmp_image_store import TmpImageStore


# Standard 1×1 PNG (decodes to 68 bytes).
VALID_1X1_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "AAIAAAoAAv/lxKUAAAAASUVORK5CYII="
)
VALID_1X1_PNG_BYTES = base64.b64decode(VALID_1X1_PNG_B64)


# ---------------------------------------------------------------------------
# Helpers — wire up a production-shaped test app
# ---------------------------------------------------------------------------


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "tmp_images"


@pytest.fixture
def store(store_dir: Path) -> TmpImageStore:
    s = TmpImageStore(store_dir.parent, max_bytes=10 * 1024 * 1024)
    s.init()
    return s


def _build_app(
    store: TmpImageStore, frontend_dist: Path | None = None
) -> FastAPI:
    """Build a FastAPI app that mirrors the production wiring.

    Includes:
    * ``api_router`` with ``tmp_images`` router mounted at
      ``/api/tmp_images``.
    * A SPA catch-all at ``/{path:path}`` that mirrors
      ``daemon/api.py:2612-2636`` — returns 404 JSON for ``api``/
      ``ws``/``vscode`` prefixes (those would be served by the
      router/api_router); serves ``index.html`` for non-API paths
      when a frontend_dist with an ``index.html`` is provided.

    The mount ORDER is the critical property: ``api_router`` is
    included BEFORE the catch-all — Starlette's first-match-wins
    router would otherwise send ``/api/tmp_images/<id>`` to
    ``index.html``.
    """
    app = FastAPI()
    app.state.tmp_image_store = store

    api_router = APIRouter(prefix="/api")
    api_router.include_router(tmp_images_module.router)
    app.include_router(api_router)

    @app.get("/{path:path}")
    async def serve_ui_assets(path: str):
        # Mirror the production catch-all's prefix guard exactly.
        if (
            path.startswith("api")
            or path.startswith("ws")
            or path.startswith("vscode")
        ):
            return JSONResponse(status_code=404, content={"error": "Not found"})

        # If a frontend dist exists, serve index.html for SPA fallback.
        if frontend_dist is not None:
            asset_path = frontend_dist / path
            if asset_path.exists():
                return FileResponse(str(asset_path))
            index_path = frontend_dist / "index.html"
            if index_path.exists():
                return FileResponse(str(index_path))
        return JSONResponse(status_code=404, content={"error": "Asset not found"})

    return app


@pytest.fixture
def client(store: TmpImageStore) -> TestClient:
    return TestClient(_build_app(store, frontend_dist=None))


@pytest.fixture
def client_with_index(store: TmpImageStore, tmp_path: Path) -> TestClient:
    """Build an app with a real index.html fallback to test route order."""
    frontend_dist = tmp_path / "fake_frontend"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text(
        "<html><body>INDEX_HTML_FALLBACK</body></html>", encoding="utf-8"
    )
    return TestClient(_build_app(store, frontend_dist=frontend_dist))


def _payload(
    filename: str = "a.png",
    content_type: str = "image/png",
    data_b64: str | None = None,
) -> dict:
    return {
        "filename": filename,
        "content_type": content_type,
        "data_base64": data_b64 if data_b64 is not None else VALID_1X1_PNG_B64,
    }


# ===========================================================================
# Group 1 — round-trip byte-identity
# ===========================================================================


class TestRoundTripByteIdentical:
    def test_post_then_get_returns_byte_identical_png(self, client: TestClient):
        # POST 1 image → 200 with upload metadata.
        post = client.post("/api/tmp_images", json={"images": [_payload()]})
        assert post.status_code == 200
        upload = post.json()["uploads"][0]
        image_id = upload["image_id"]
        ref_url = upload["ref_url"]

        # GET on the canonical ref_url → byte-identical PNG.
        get = client.get(ref_url)
        assert get.status_code == 200
        assert get.content == VALID_1X1_PNG_BYTES
        assert get.headers["content-type"].startswith("image/png")
        assert int(get.headers["content-length"]) == len(VALID_1X1_PNG_BYTES)

    def test_post_then_get_on_bare_id(self, client: TestClient):
        post = client.post("/api/tmp_images", json={"images": [_payload()]})
        upload = post.json()["uploads"][0]
        image_id = upload["image_id"]
        # The bare id is the same wire form as ref_url minus the prefix.
        get = client.get(f"/api/tmp_images/{image_id}")
        assert get.status_code == 200
        assert get.content == VALID_1X1_PNG_BYTES


# ===========================================================================
# Group 2 — multi-image POST (3 images) + 4-image rejection
# ===========================================================================


class TestBatchPost:
    def test_three_images_in_one_batch(self, client: TestClient):
        post = client.post(
            "/api/tmp_images",
            json={"images": [_payload(f"a{i}.png") for i in range(3)]},
        )
        assert post.status_code == 200
        uploads = post.json()["uploads"]
        assert len(uploads) == 3
        ids = {u["image_id"] for u in uploads}
        assert len(ids) == 3  # all distinct uuid4 hex

        # Each GET returns byte-identical content.
        for u in uploads:
            get = client.get(u["ref_url"])
            assert get.status_code == 200
            assert get.content == VALID_1X1_PNG_BYTES

    def test_four_images_returns_422(self, client: TestClient):
        post = client.post(
            "/api/tmp_images",
            json={"images": [_payload(f"a{i}.png") for i in range(4)]},
        )
        assert post.status_code == 422


# ===========================================================================
# Group 3 — Route order vs SPA catch-all (architect risk #1)
# ===========================================================================


class TestRouteOrderVsSpaCatchAll:
    def test_get_tmp_image_does_not_serve_index_html(
        self, client_with_index: TestClient
    ):
        # POST an image first.
        post = client_with_index.post(
            "/api/tmp_images", json={"images": [_payload()]}
        )
        assert post.status_code == 200
        upload = post.json()["uploads"][0]
        ref_url = upload["ref_url"]

        # GET on /api/tmp_images/<id> must reach the tmp-image
        # endpoint — NOT the SPA catch-all's index.html fallback.
        # If the catch-all shadowed the route, the response body
        # would contain "INDEX_HTML_FALLBACK" and Content-Type would
        # be text/html.
        get = client_with_index.get(ref_url)
        assert get.status_code == 200
        body_text = get.content.decode("utf-8", errors="replace")
        assert "INDEX_HTML_FALLBACK" not in body_text
        assert get.headers["content-type"].startswith("image/png")

    def test_get_unknown_api_path_falls_through_to_catch_all_404(
        self, client_with_index: TestClient
    ):
        # Sanity: a /api/* path that does NOT match any router still
        # gets 404 from the catch-all's prefix guard (not the SPA
        # fallback). Production behavior — preserved here so a
        # future refactor that moves the guard doesn't break silently.
        resp = client_with_index.get("/api/does-not-exist")
        assert resp.status_code == 404
        assert "INDEX_HTML_FALLBACK" not in resp.text

    def test_get_non_api_path_falls_through_to_index(
        self, client_with_index: TestClient
    ):
        # Sanity: a NON-api path (e.g. ``/foo``) still hits the
        # SPA fallback when one is configured. This confirms the
        # catch-all still works for legitimate SPA routing.
        resp = client_with_index.get("/some-spa-route")
        assert resp.status_code == 200
        assert "INDEX_HTML_FALLBACK" in resp.text


# ===========================================================================
# Group 4 — DELETE end-to-end (real store)
# ===========================================================================


class TestDeleteE2E:
    def test_delete_then_get_returns_404(self, client: TestClient):
        post = client.post("/api/tmp_images", json={"images": [_payload()]})
        upload = post.json()["uploads"][0]
        image_id = upload["image_id"]

        # DELETE → 204.
        d = client.delete(f"/api/tmp_images/{image_id}")
        assert d.status_code == 204

        # GET → 404 (blob + sidecar are gone).
        g = client.get(f"/api/tmp_images/{image_id}")
        assert g.status_code == 404

    def test_delete_idempotent_in_e2e(self, client: TestClient):
        post = client.post("/api/tmp_images", json={"images": [_payload()]})
        upload = post.json()["uploads"][0]
        image_id = upload["image_id"]

        d1 = client.delete(f"/api/tmp_images/{image_id}")
        d2 = client.delete(f"/api/tmp_images/{image_id}")
        assert d1.status_code == 204
        assert d2.status_code == 204


# ===========================================================================
# Group 5 — Hardening headers verified end-to-end
# ===========================================================================


class TestHardeningHeadersE2E:
    def test_get_e2e_includes_nosniff_and_disposition(self, client: TestClient):
        post = client.post("/api/tmp_images", json={"images": [_payload()]})
        upload = post.json()["uploads"][0]
        ref_url = upload["ref_url"]
        get = client.get(ref_url)
        assert get.headers["x-content-type-options"] == "nosniff"
        assert get.headers["content-disposition"].startswith("inline;")
        assert f'filename="{upload["image_id"]}"' in get.headers["content-disposition"]
        assert get.headers["cache-control"] == "private, max-age=3600"
        assert get.headers["etag"].startswith('W/"')

    def test_e2e_etag_304_cycle(self, client: TestClient):
        post = client.post("/api/tmp_images", json={"images": [_payload()]})
        upload = post.json()["uploads"][0]
        ref_url = upload["ref_url"]
        first = client.get(ref_url)
        etag = first.headers["etag"]

        # Second GET with If-None-Match → 304.
        second = client.get(ref_url, headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""

    def test_e2e_no_xss_through_sniff(
        self, client: TestClient
    ):
        # Negative test: even if a stored file's Content-Type were
        # somehow text/html (it can't, allowlist is enforced), the
        # nosniff header prevents the browser from re-interpreting.
        # We can only assert the header is set on every response —
        # the actual sniff-attack scenario is closed by the
        # 4-type allowlist + the ``nosniff`` directive combined.
        post = client.post("/api/tmp_images", json={"images": [_payload()]})
        upload = post.json()["uploads"][0]
        get = client.get(upload["ref_url"])
        assert get.headers.get("x-content-type-options") == "nosniff"


# ===========================================================================
# Group 6 — W4 real-app route-order pin (phase-1+3 review)
# ===========================================================================


class TestRealAppTmpImagesRouteOrder:
    """W4 (phase-1+3 review): pin the REAL ``create_app()`` route table.

    The e2e at this file uses a hand-built mini-app with its own
    catch-all (Group 3 above). That proves the route-order principle
    but NOT that the production ``create_app()`` actually registers
    the tmp_images router BEFORE its SPA catch-all — a future
    refactor that re-orders the mount would silently break the
    route, and the mini-app e2e would still pass.

    This suite calls the real ``daemon.api.create_app()`` (no
    lifespan drive — we only inspect the static route table) and
    pins:

    * every ``/api/tmp_images`` route appears BEFORE the SPA
      catch-all ``/{path:path}`` in ``app.routes``;
    * Starlette first-match-wins would otherwise send
      ``GET /api/tmp_images/<id>`` to ``index.html``.
    """

    def test_tmp_images_routes_registered_before_spa_catch_all(self):
        from daemon.api import create_app

        app = create_app()

        # Find the SPA catch-all index — same shape as the
        # ``test_vscode_routing.py`` precedent.
        catchall_idx = None
        for i, route in enumerate(app.routes):
            if (
                getattr(route, "path", None) == "/{path:path}"
                and "GET" in getattr(route, "methods", set())
            ):
                catchall_idx = i
                break

        assert catchall_idx is not None, (
            "W4: SPA catch-all /{path:path} not found in create_app() routes"
        )

        # Find every tmp_images route — the router was included via
        # ``api_router.include_router(tmp_images_router)`` where
        # api_router has prefix /api and tmp_images_router has prefix
        # /tmp_images, so the final mounted paths are /api/tmp_images
        # and /api/tmp_images/{image_id_or_ref:path}.
        tmp_image_route_idxs = []
        for i, route in enumerate(app.routes):
            path = getattr(route, "path", None)
            if path is None:
                continue
            # Match the exact ``/api/tmp_images`` POST + debug GET
            # and ``/api/tmp_images/<param>`` GET + DELETE shapes.
            if path == "/api/tmp_images":
                tmp_image_route_idxs.append(i)
            elif path.startswith("/api/tmp_images/"):
                tmp_image_route_idxs.append(i)

        assert tmp_image_route_idxs, (
            "W4: no /api/tmp_images routes found in create_app() output — "
            "the tmp_images router may not be wired into api_router. "
            f"All routes: {[getattr(r, 'path', '?') for r in app.routes]!r}"
        )

        # Pin: every tmp_images route index is strictly less than the
        # catch-all index. Starlette matches in registration order;
        # the first matching route wins. A route AFTER the catch-all
        # is unreachable for GET (the catch-all's GET swallows it).
        offenders = [i for i in tmp_image_route_idxs if i > catchall_idx]
        assert not offenders, (
            f"W4: tmp_images routes at indices {offenders} are AFTER the "
            f"SPA catch-all at index {catchall_idx} — Starlette "
            f"first-match-wins would shadow them. Routes after the "
            f"catch-all: "
            f"{[getattr(r, 'path', '?') for r in app.routes[catchall_idx + 1:]]!r}"
        )
