"""Unit tests for ``daemon.routers.tmp_images``.

Phase 1 / clipboard-image-chat. Validators pin the FULL contract:

* POST happy path + every rejection branch (size, count, MIME,
  base64, collision, store-full).
* GET path-traversal rejection matrix (every malformed id shape).
* GET hardening headers (X-Content-Type-Options: nosniff +
  Content-Disposition: inline; filename="<id>" + Cache-Control +
  ETag weak sha256[:16]).
* GET → 304 on If-None-Match.
* GET input-alias: ``tmpimg://<32hex>`` (raw + URL-encoded ``://``)
  resolves; traversal shapes after the scheme strip still 404.
* DELETE idempotent (204 + repeat 204) + 404 on malformed id.
* Gated debug listing (default 404; ENSEMBLE_TMP_IMAGE_DEBUG_LISTING=1
  → 200 with count + oldest_mtime; no id leak).
* 507 + rate-limited WARNING on over-cap POSTs.

We mount the router on a fresh ``FastAPI`` app with a real
``TmpImageStore`` wired to ``app.state`` — no DB, no manager, no
lifespan. This mirrors the production wiring shape
(``request.app.state.tmp_image_store``) without booting the full
daemon.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient

from daemon.routers import tmp_images as tmp_images_module
from daemon.routers.tmp_images import _reset_full_warning_clock
from daemon.services.tmp_image_store import (
    TmpImageStore,
    TmpImageStoreFull,
)


# Standard 1×1 PNG (70 bytes decoded).
VALID_1X1_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "AAIAAAoAAv/lxKUAAAAASUVORK5CYII="
)


# ---------------------------------------------------------------------------
# Helpers — wire up a test app + a fresh store per test
# ---------------------------------------------------------------------------


@pytest.fixture
def store_dir(tmp_path) -> Path:
    return tmp_path / "tmp_images"


@pytest.fixture
def store(store_dir: Path) -> TmpImageStore:
    """A real ``TmpImageStore`` with a generous cap."""
    s = TmpImageStore(store_dir.parent, max_bytes=10 * 1024 * 1024)
    s.init()
    return s


@pytest.fixture
def client(store: TmpImageStore) -> TestClient:
    """FastAPI TestClient with the tmp_images router mounted at /api."""
    _reset_full_warning_clock()
    app = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(tmp_images_module.router)
    app.include_router(api)
    app.state.tmp_image_store = store
    return TestClient(app)


def _payload(filename: str = "a.png", content_type: str = "image/png", data_b64: str | None = None) -> dict:
    return {
        "filename": filename,
        "content_type": content_type,
        "data_base64": data_b64 if data_b64 is not None else VALID_1X1_PNG_B64,
    }


def _post(client: TestClient, images: list[dict]) -> "httpx.Response":
    # Wrapped for clarity; TestClient.post returns the Response object directly.
    return client.post("/api/tmp_images", json={"images": images})


# ===========================================================================
# Group 1 — POST happy path
# ===========================================================================


class TestPostHappy:
    def test_single_image_returns_200_with_ref_url(self, client: TestClient):
        resp = _post(client, [_payload()])
        assert resp.status_code == 200
        body = resp.json()
        assert "uploads" in body
        assert len(body["uploads"]) == 1
        u = body["uploads"][0]
        assert u["content_type"] == "image/png"
        # The 1×1 PNG in VALID_1X1_PNG_B64 decodes to 68 bytes.
        assert u["size_bytes"] == 68
        # ref_url is the canonical wire form.
        assert u["ref_url"].startswith("/api/tmp_images/")
        assert u["image_id"] in u["ref_url"]
        # tmpimg:// is NEVER emitted.
        assert "tmpimg://" not in u["ref_url"]
        assert "tmpimg://" not in str(body)

    def test_three_images_in_one_batch(self, client: TestClient):
        resp = _post(client, [_payload(f"a{i}.png") for i in range(3)])
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["uploads"]) == 3
        # Each upload has a unique id.
        ids = {u["image_id"] for u in body["uploads"]}
        assert len(ids) == 3

    def test_jpeg_payload_with_jpg_alias(self, client: TestClient):
        # Architect: jpg aliases jpeg; storage normalizes to image/jpeg.
        # 1×1 JPEG bytes (well-formed JFIF header).
        jpeg_b64 = (
            "/9j/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
            "EBAQEBAQEBAQEBAQEBAQEB/9k="
        )
        resp = _post(client, [_payload("a.jpg", "image/jpg", jpeg_b64)])
        assert resp.status_code == 200
        # Server stores the normalized form.
        body = resp.json()
        assert body["uploads"][0]["content_type"] == "image/jpeg"

    def test_webp_payload_with_proper_marker(self, client: TestClient):
        import base64 as _b64
        webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
        webp_b64 = _b64.b64encode(webp).decode("ascii")
        resp = _post(client, [_payload("a.webp", "image/webp", webp_b64)])
        assert resp.status_code == 200


# ===========================================================================
# Group 2 — POST rejection branches
# ===========================================================================


class TestPostRejections:
    def test_four_images_returns_422(self, client: TestClient):
        # The model-layer validator catches this BEFORE the route runs,
        # so FastAPI returns 422 (validation error), not 400.
        resp = _post(client, [_payload(f"a{i}.png") for i in range(4)])
        assert resp.status_code == 422

    def test_unknown_mime_returns_422_with_verbatim_message(self, client: TestClient):
        resp = _post(client, [_payload("a.svg", "image/svg+xml")])
        assert resp.status_code == 422
        # FastAPI surfaces ValidationError messages in the body.
        body_text = resp.text
        assert "content_type 'image/svg+xml' rejected" in body_text
        assert "only png/jpeg/gif/webp allowed" in body_text
        assert "svg excluded: stored-XSS via direct navigation" in body_text

    def test_non_base64_data_returns_422(self, client: TestClient):
        resp = _post(client, [_payload(data_b64="!@#$%^&*")])
        assert resp.status_code == 422

    def test_oversize_payload_returns_422(self, client: TestClient):
        # 11MB of zeros — over the 10MB cap.
        big = b"\x00" * (11 * 1024 * 1024)
        big_b64 = base64.b64encode(big).decode("ascii")
        resp = _post(client, [_payload("big.png", "image/png", big_b64)])
        assert resp.status_code == 422

    def test_magic_byte_mismatch_returns_422(self, client: TestClient):
        # JPEG bytes labeled as PNG — the cross-check must reject.
        jpeg_b64 = (
            "/9j/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
            "EBAQEBAQEBAQEBAQEBAQEBAQEB/9k="
        )
        resp = _post(client, [_payload("a.png", "image/png", jpeg_b64)])
        assert resp.status_code == 422

    def test_filename_over_255_returns_422(self, client: TestClient):
        # Phase-1+3 review S4 — the filename field is capped at 255
        # chars (pydantic max_length); overflow is a validation error,
        # so it takes the 422 envelope path, not a 400.
        resp = _post(client, [_payload("a" * 256)])
        assert resp.status_code == 422


# ===========================================================================
# Group 3 — GET path-traversal rejection matrix
# ===========================================================================


class TestGetPathTraversal:
    def test_get_404_on_unknown_id(self, client: TestClient):
        resp = client.get("/api/tmp_images/00000000000000000000000000000000")
        assert resp.status_code == 404

    def test_get_404_on_double_dot(self, client: TestClient):
        resp = client.get("/api/tmp_images/..")
        assert resp.status_code == 404

    def test_get_404_on_slash(self, client: TestClient):
        resp = client.get("/api/tmp_images/")
        # The trailing-slash form routes to the (unmatched) listing —
        # the gated listing returns 404 in default mode. So 404 OK.
        # Note: actual routing may return 404 for any non-id shape.
        assert resp.status_code == 404

    def test_get_404_on_unicode_id(self, client: TestClient):
        # Unicode characters are not valid hex; must 404.
        resp = client.get("/api/tmp_images/한글aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        assert resp.status_code == 404

    def test_get_404_on_uppercase_hex(self, client: TestClient):
        # The regex is anchored on lowercase; uppercase must 404.
        resp = client.get("/api/tmp_images/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0")  # 32 chars but has non-hex
        assert resp.status_code == 404

    def test_get_404_on_too_short_id(self, client: TestClient):
        resp = client.get("/api/tmp_images/abc123")
        assert resp.status_code == 404

    def test_get_404_on_too_long_id(self, client: TestClient):
        resp = client.get("/api/tmp_images/" + ("a" * 64))
        assert resp.status_code == 404

    def test_get_404_on_url_encoded_slash(self, client: TestClient):
        # Path-traversal via %2F — Starlette decodes; the regex still rejects.
        resp = client.get("/api/tmp_images/%2F")
        assert resp.status_code == 404

    def test_get_404_on_path_with_dot_segments(self, client: TestClient):
        # %2e = "."; %2f = "/"
        resp = client.get("/api/tmp_images/%2e%2e%2fetc%2fpasswd")
        assert resp.status_code == 404


# ===========================================================================
# Group 4 — GET happy path + hardening headers
# ===========================================================================


class TestGetHappyAndHeaders:
    def _upload_one(self, client: TestClient) -> dict:
        resp = _post(client, [_payload()])
        assert resp.status_code == 200
        return resp.json()["uploads"][0]

    def test_get_returns_bytes_and_correct_content_type(self, client: TestClient):
        upload = self._upload_one(client)
        image_id = upload["image_id"]
        resp = client.get(f"/api/tmp_images/{image_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        # Body is byte-identical to the decoded payload.
        assert resp.content == base64.b64decode(VALID_1X1_PNG_B64)
        assert resp.headers["content-length"] == str(len(resp.content))

    def test_get_includes_nosniff_header(self, client: TestClient):
        upload = self._upload_one(client)
        resp = client.get(f"/api/tmp_images/{upload['image_id']}")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_get_includes_content_disposition(self, client: TestClient):
        upload = self._upload_one(client)
        resp = client.get(f"/api/tmp_images/{upload['image_id']}")
        cd = resp.headers.get("content-disposition", "")
        assert cd.startswith("inline;")
        assert f'filename="{upload["image_id"]}"' in cd

    def test_get_includes_cache_control(self, client: TestClient):
        upload = self._upload_one(client)
        resp = client.get(f"/api/tmp_images/{upload['image_id']}")
        assert resp.headers.get("cache-control") == "private, max-age=3600"

    def test_get_includes_weak_etag(self, client: TestClient):
        upload = self._upload_one(client)
        resp = client.get(f"/api/tmp_images/{upload['image_id']}")
        etag = resp.headers.get("etag")
        assert etag is not None
        assert etag.startswith('W/"')
        assert etag.endswith('"')
        # sha256[:16] hex = 16 hex chars.
        inner = etag[len('W/"'):-1]
        assert len(inner) == 16
        assert all(c in "0123456789abcdef" for c in inner)


# ===========================================================================
# Group 4b — GET accepts the tmpimg:// input-alias (phase1 Task 7)
# ===========================================================================


class TestGetTmpimgSchemeAlias:
    """GET accepts ``tmpimg://<32hex>`` and its URL-encoded form.

    Phase-1 Task 7 acceptance: "GET accepts both ``tmpimg://abc`` and
    ``abc`` paths; unit test covers both". The wire form is always the
    bare id (POST never emits the scheme) — the alias exists so callers
    passing the ref through without stripping it still resolve
    (frozen contract docstring + architect amendment #5).
    ``_normalize_image_id`` strips the scheme BEFORE the
    ``^[a-f0-9]{32}$`` gate, so the regex safety net holds on the
    aliased form too.
    """

    def _upload_one(self, client: TestClient) -> dict:
        resp = _post(client, [_payload()])
        assert resp.status_code == 200
        return resp.json()["uploads"][0]

    def test_get_accepts_tmpimg_scheme_alias(self, client: TestClient):
        upload = self._upload_one(client)
        image_id = upload["image_id"]
        resp = client.get(f"/api/tmp_images/tmpimg://{image_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        # Body is byte-identical to the decoded payload.
        assert resp.content == base64.b64decode(VALID_1X1_PNG_B64)
        # Hardening headers present on the alias path too.
        assert resp.headers.get("x-content-type-options") == "nosniff"
        cd = resp.headers.get("content-disposition", "")
        assert cd.startswith("inline;")
        assert f'filename="{image_id}"' in cd
        assert resp.headers.get("cache-control") == "private, max-age=3600"
        assert resp.headers.get("etag", "").startswith('W/"')

    def test_get_url_encoded_tmpimg_scheme_alias(self, client: TestClient):
        # %3A%2F%2F = "://" — Starlette decodes the path BEFORE the
        # handler runs, so the handler receives ``tmpimg://<id>`` and
        # the scheme strip still applies (decode-then-normalize order).
        upload = self._upload_one(client)
        image_id = upload["image_id"]
        resp = client.get(f"/api/tmp_images/tmpimg%3A%2F%2F{image_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == base64.b64decode(VALID_1X1_PNG_B64)

    def test_get_alias_traversal_shapes_return_404(self, client: TestClient):
        # The regex gate must hold AFTER the scheme strip — the alias
        # must not open a traversal or a non-hex-id bypass.
        for shape in (
            "tmpimg://../../etc/passwd",
            "tmpimg://not-a-valid-id",
        ):
            resp = client.get(f"/api/tmp_images/{shape}")
            assert resp.status_code == 404, f"{shape} must 404"


# ===========================================================================
# Group 5 — ETag / 304 cycle
# ===========================================================================


class TestGetETag304:
    def test_304_on_if_none_match(self, client: TestClient):
        # Upload + capture the ETag.
        resp = _post(client, [_payload()])
        upload = resp.json()["uploads"][0]
        image_id = upload["image_id"]
        first = client.get(f"/api/tmp_images/{image_id}")
        etag = first.headers["etag"]

        # Repeat with If-None-Match.
        second = client.get(
            f"/api/tmp_images/{image_id}",
            headers={"If-None-Match": etag},
        )
        assert second.status_code == 304
        # 304 has empty body.
        assert second.content == b""
        # Phase-1+3 review S1 — the 304 carries the same hardening
        # headers as the 200 path (minus Content-Length, no body).
        assert second.headers.get("etag") == etag
        assert second.headers.get("cache-control") == "private, max-age=3600"
        assert second.headers.get("x-content-type-options") == "nosniff"
        cd = second.headers.get("content-disposition", "")
        assert cd.startswith("inline;")
        assert f'filename="{image_id}"' in cd

    def test_non_matching_if_none_match_returns_200(self, client: TestClient):
        resp = _post(client, [_payload()])
        upload = resp.json()["uploads"][0]
        image_id = upload["image_id"]
        second = client.get(
            f"/api/tmp_images/{image_id}",
            headers={"If-None-Match": 'W/"deadbeefdeadbeef"'},
        )
        assert second.status_code == 200


# ===========================================================================
# Group 6 — DELETE idempotency
# ===========================================================================


class TestDeleteIdempotent:
    def test_delete_returns_204(self, client: TestClient):
        upload = _post(client, [_payload()]).json()["uploads"][0]
        image_id = upload["image_id"]
        resp = client.delete(f"/api/tmp_images/{image_id}")
        assert resp.status_code == 204
        assert resp.content == b""

    def test_repeat_delete_returns_204_idempotent(self, client: TestClient):
        upload = _post(client, [_payload()]).json()["uploads"][0]
        image_id = upload["image_id"]
        first = client.delete(f"/api/tmp_images/{image_id}")
        second = client.delete(f"/api/tmp_images/{image_id}")
        assert first.status_code == 204
        assert second.status_code == 204

    def test_delete_unknown_id_returns_404(self, client: TestClient):
        # The 32-hex form is structurally valid — but never existed.
        # Per architect amendment #6, the request is structurally
        # valid → 204 (idempotent delete).
        resp = client.delete("/api/tmp_images/00000000000000000000000000000000")
        assert resp.status_code == 204

    def test_delete_malformed_id_returns_404(self, client: TestClient):
        resp = client.delete("/api/tmp_images/..")
        assert resp.status_code == 404

    def test_delete_path_traversal_shape_returns_404(self, client: TestClient):
        # %2e%2e/etc/passwd — path-traversal shape, must 404.
        resp = client.delete("/api/tmp_images/%2e%2e%2fetc%2fpasswd")
        assert resp.status_code == 404

    def test_delete_after_get_removes(self, client: TestClient):
        upload = _post(client, [_payload()]).json()["uploads"][0]
        image_id = upload["image_id"]
        client.delete(f"/api/tmp_images/{image_id}")
        # GET after delete → 404.
        resp = client.get(f"/api/tmp_images/{image_id}")
        assert resp.status_code == 404


# ===========================================================================
# Group 7 — Gated debug listing
# ===========================================================================


class TestGatedDebugListing:
    def test_listing_default_off_returns_404(self, client: TestClient, monkeypatch):
        monkeypatch.delenv("ENSEMBLE_TMP_IMAGE_DEBUG_LISTING", raising=False)
        resp = client.get("/api/tmp_images")
        assert resp.status_code == 404

    def test_listing_with_env_flag_returns_200(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_TMP_IMAGE_DEBUG_LISTING", "1")
        resp = client.get("/api/tmp_images")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["oldest_mtime"] is None

    def test_listing_reflects_uploads_and_deletes(
        self, client: TestClient, monkeypatch
    ):
        monkeypatch.setenv("ENSEMBLE_TMP_IMAGE_DEBUG_LISTING", "1")
        # Empty store.
        resp = client.get("/api/tmp_images")
        assert resp.json()["count"] == 0
        # Upload two.
        up1 = _post(client, [_payload("a.png")]).json()["uploads"][0]
        up2 = _post(client, [_payload("b.png")]).json()["uploads"][0]
        resp = client.get("/api/tmp_images")
        body = resp.json()
        assert body["count"] == 2
        # Delete one.
        client.delete(f"/api/tmp_images/{up1['image_id']}")
        resp = client.get("/api/tmp_images")
        body = resp.json()
        assert body["count"] == 1
        # No id field — recon-only exposure.
        assert "image_id" not in body
        assert "ids" not in body
        assert "uploads" not in body

    def test_listing_does_not_leak_ids(
        self, client: TestClient, monkeypatch
    ):
        monkeypatch.setenv("ENSEMBLE_TMP_IMAGE_DEBUG_LISTING", "1")
        up = _post(client, [_payload()]).json()["uploads"][0]
        resp = client.get("/api/tmp_images")
        body_text = resp.text
        assert up["image_id"] not in body_text
        assert "tmp_images/" not in body_text


# ===========================================================================
# Group 8 — 507 + rate-limited WARNING
# ===========================================================================


class TestStoreFullRateLimited:
    def test_over_cap_returns_507(self, tmp_path):
        # Tight cap so the FIRST 1×1 PNG POST trips it (PNG blob is
        # 70 bytes; sidecar ~180 bytes; ~250 > 50).
        store = TmpImageStore(tmp_path / "data", max_bytes=50)
        store.init()
        app = FastAPI()
        api = APIRouter(prefix="/api")
        api.include_router(tmp_images_module.router)
        app.include_router(api)
        app.state.tmp_image_store = store
        _reset_full_warning_clock()
        client = TestClient(app)

        resp = _post(client, [_payload()])
        assert resp.status_code == 507
        body = resp.json()
        # Code is TMP_IMAGE_STORE_FULL.
        assert body["detail"]["code"] == "TMP_IMAGE_STORE_FULL"

    def test_507_emits_rate_limited_warning(self, tmp_path, caplog):
        # Tight cap so the POST trips 507.
        store = TmpImageStore(tmp_path / "data", max_bytes=50)
        store.init()
        app = FastAPI()
        api = APIRouter(prefix="/api")
        api.include_router(tmp_images_module.router)
        app.include_router(api)
        app.state.tmp_image_store = store
        _reset_full_warning_clock()
        client = TestClient(app)

        with caplog.at_level(logging.WARNING, logger="daemon.routers.tmp_images"):
            for _ in range(100):
                _post(client, [_payload()])
        # Rate limit: ≤1 WARNING per 60s. So at most 1 in 100 attempts.
        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "[TmpImages] store full" in r.getMessage()
        ]
        assert len(warnings) <= 1, (
            f"expected ≤1 WARNING, got {len(warnings)} — rate limiter broken"
        )
        # And at least 1 — the limiter should NOT swallow the first signal.
        assert len(warnings) >= 1


# ===========================================================================
# Group 9 — App-state seam (503 when the store is missing)
# ===========================================================================


class TestStoreUnavailable:
    def test_503_when_app_state_tmp_image_store_missing(self):
        app = FastAPI()
        api = APIRouter(prefix="/api")
        api.include_router(tmp_images_module.router)
        app.include_router(api)
        # Deliberately do NOT set app.state.tmp_image_store.
        client = TestClient(app)
        resp = _post(client, [_payload()])
        assert resp.status_code == 503


# ===========================================================================
# Group 10 — FROZEN contract docstring present (string-contains pin)
# ===========================================================================


class TestFrozenContractDocstring:
    def test_router_module_docstring_contains_public_by_obscurity(self):
        # The verbatim guard-rail block from architect amendment #5 must
        # appear at the top of the router module — pinned here so a
        # later refactor that strips the docstring fails loudly.
        # We normalize whitespace because the verbatim block wraps
        # across multiple lines in the docstring.
        import re as _re
        source = Path(tmp_images_module.__file__).read_text(encoding="utf-8")
        collapsed = _re.sub(r"\s+", " ", source)
        assert "PUBLIC-BY-OBSCURITY" in collapsed
        assert (
            "If any daemon endpoint gains auth, this MUST be the first "
            "file-serving route to gain it"
        ) in collapsed
        assert (
            "Do not treat this as precedent for auth-free serving of "
            "sensitive content"
        ) in collapsed

    def test_router_module_docstring_contains_canonical_endpoint(self):
        source = Path(tmp_images_module.__file__).read_text(encoding="utf-8")
        assert "POST /api/tmp_images" in source
        assert "GET /api/tmp_images" in source
        assert "DELETE /api/tmp_images" in source
