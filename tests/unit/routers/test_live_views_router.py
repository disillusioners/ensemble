"""Unit tests for ``daemon.routers.live_views`` (Phase 1).

Mirrors the pattern at ``tests/unit/routers/test_tmp_image_router.py``:
mount the router on a fresh ``FastAPI`` app with the
``LiveViewsService`` wired to ``app.state`` (no DB, no
manager, no lifespan). The router is the HTTP-shaped view on
top of the service; this file tests the response shape,
headers, and method whitelist.
"""

from __future__ import annotations

import os
import pathlib
from typing import Callable

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.routers.live_views import build_router
from daemon.services.live_views import LiveViewsService
from daemon.services.tmp_image_store import TmpImageStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """The tmp-image store's parent directory."""
    return tmp_path / "tmp_images"


@pytest.fixture
def tmp_store(store_dir: pathlib.Path) -> TmpImageStore:
    """A real ``TmpImageStore`` with a generous cap."""
    s = TmpImageStore(store_dir.parent, max_bytes=10 * 1024 * 1024)
    s.init()
    return s


@pytest.fixture
def filesystem_root_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A real filesystem root for ``designer-artifact`` tests.

    Creates ``.agents/shared/planning/{feature}/design/mockups/``
    with one HTML file inside.
    """
    workdir = tmp_path / "project"
    mockup = workdir / ".agents" / "shared" / "planning" / "feat" / "design" / "mockups"
    mockup.mkdir(parents=True)
    (mockup / "landing.html").write_text("<h1>Landing</h1>")
    (mockup / "pricing.html").write_text("<h1>Pricing</h1>")
    return workdir


def _build_service(  # pragma: no cover - kept for future tests
    *,
    enabled: bool = True,
    filesystem_root: str | None = None,
    filesystem_path: str | None = None,
    filesystem_allowlist: list[str] | None = None,
    shortname_map: dict[str, str | None] | None = None,
    planning_path: str = ".agents/shared/planning",
    external_base_url: str | None = None,
    tmp_store: TmpImageStore | None = None,
    include_tmp_images_root: bool = False,
) -> LiveViewsService:
    cfg = LiveViewsConfig(
        enabled=enabled, external_base_url=external_base_url
    )
    if filesystem_root is not None:
        cfg.roots[filesystem_root] = LiveViewsRootConfig(
            type="filesystem",
            path=filesystem_path or str(filesystem_root),
            allowed_extensions=filesystem_allowlist,
        )
    if shortname_map is not None:
        cfg.roots["planning"] = LiveViewsRootConfig(
            type="project_scoped", path=planning_path
        )
    if include_tmp_images_root:
        cfg.roots["tmp-images"] = LiveViewsRootConfig(type="tmp_images")
    return LiveViewsService(
        config=cfg,
        tmp_image_store=tmp_store,
        project_workdir_by_shortname_resolver=(
            (lambda shortname: shortname_map.get(shortname))
            if shortname_map is not None
            else None
        ),
    )


@pytest.fixture
def client_with_filesystem_root(filesystem_root_dir: pathlib.Path) -> TestClient:
    """A client with a real ``designer-artifact`` project-scoped root.

    REWORK 2026-10-07 (M2): ``designer-artifact`` is
    project_scoped (not filesystem-against-calling-instance),
    so the URL pattern is
    ``/views/designer-artifact/<shortname>/<rel>``. The
    shortname ``ens`` maps to the test workdir; the rel must
    include ``design/mockups`` as a contiguous subpath
    (M3 structural gate). The mockup files are at
    ``<workdir>/.agents/shared/planning/feat/design/mockups/``;
    the URL is ``/views/designer-artifact/ens/feat/design/mockups/landing.html``.
    """
    cfg = LiveViewsConfig()
    # REWORK 2026-10-07 (M2+M3): the root is project_scoped with
    # the canonical ``design/mockups`` subpath enforcement. The
    # test wires the per-shortname workdir resolver to the test
    # workdir (the router's call to ``resolve_for_instance`` is
    # anonymous — no ``request.state.instance_id``).
    workdir = filesystem_root_dir
    cfg.roots["designer-artifact"] = LiveViewsRootConfig(
        type="project_scoped",
        path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"],
        # Kept for the extension-not-allowed 404 test below —
        # only HTML is allowed under the mockups subtree.
        allowed_extensions=["html"],
    )
    service = LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=lambda shortname: (
            str(workdir) if shortname == "ens" else None
        ),
    )
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = service
    return TestClient(app)


@pytest.fixture
def client_with_tmp_store(tmp_store: TmpImageStore) -> TestClient:
    cfg = LiveViewsConfig()
    cfg.roots["tmp-images"] = LiveViewsRootConfig(type="tmp_images")
    service = LiveViewsService(config=cfg, tmp_image_store=tmp_store)
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = service
    return TestClient(app)


# ===========================================================================
# Group 1 — Uniform 404 envelope
# ===========================================================================


class TestUniform404:
    """Every miss returns the same body, the same code, the same
    nosniff header. The body never distinguishes the case.
    """

    def test_unknown_root_returns_uniform_404(
        self, client_with_filesystem_root: TestClient
    ):
        resp = client_with_filesystem_root.get("/views/unknown/foo.html")
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_unknown_path_returns_uniform_404(
        self, client_with_filesystem_root: TestClient
    ):
        resp = client_with_filesystem_root.get(
            "/views/designer-artifact/ens/no-such.html"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_traversal_returns_uniform_404(
        self, client_with_filesystem_root: TestClient
    ):
        resp = client_with_filesystem_root.get(
            "/views/designer-artifact/ens/../etc/passwd"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_double_encoded_traversal_returns_uniform_404(
        self, client_with_filesystem_root: TestClient
    ):
        # %2e%2e → '..' after URL decode (FastAPI decodes for
        # us). The shape check rejects.
        resp = client_with_filesystem_root.get(
            "/views/designer-artifact/ens/%2e%2e/etc/passwd"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_double_double_encoded_traversal_returns_uniform_404(
        self, client_with_filesystem_root: TestClient
    ):
        # REWORK 2026-10-07 (m2 router pin): %252e%252e →
        # '%2e%2e' (literal three-char string) after one URL
        # decode. The shape check does NOT trip on '%2e%2e'
        # (that's not '..'), and the literal string cannot
        # resolve to a file. The result MUST still be a uniform
        # 404 — an attacker who can get the literal three-char
        # string past the decoder must not see a path-disclosure
        # or a different status code.
        resp = client_with_filesystem_root.get(
            "/views/designer-artifact/ens/%252e%252e/etc/passwd"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_extension_not_allowed_returns_uniform_404(
        self, client_with_filesystem_root: TestClient
    ):
        # The root's allowlist is ["html"]. A .exe file
        # (hypothetically present) would 404.
        resp = client_with_filesystem_root.get(
            "/views/designer-artifact/ens/feat/design/mockups/landing.exe"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_subsystem_disabled_returns_uniform_404(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig(enabled=False)
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        service = LiveViewsService(config=cfg)
        app = FastAPI()
        app.include_router(build_router())
        app.state.live_views_service = service
        c = TestClient(app)
        resp = c.get("/views/docs/foo")
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_no_service_returns_uniform_404(self):
        # Lifespan did not run; ``app.state.live_views_service``
        # is None. The router returns the uniform 404 — never a
        # 500 / stack trace.
        app = FastAPI()
        app.include_router(build_router())
        c = TestClient(app)
        resp = c.get("/views/docs/foo")
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}


# ===========================================================================
# Group 2 — Happy path: filesystem root
# ===========================================================================


class TestFilesystemRootHappy:
    def test_happy_path_serves_html(
        self, client_with_filesystem_root: TestClient
    ):
        resp = client_with_filesystem_root.get(
            "/views/designer-artifact/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 200
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert resp.content == b"<h1>Landing</h1>"
        assert resp.headers["content-length"] == str(len(b"<h1>Landing</h1>"))

    def test_head_returns_same_headers_no_body(
        self, client_with_filesystem_root: TestClient
    ):
        resp = client_with_filesystem_root.head(
            "/views/designer-artifact/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 200
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert resp.headers["content-length"] == str(len(b"<h1>Landing</h1>"))
        # HEAD must NOT return a body.
        assert resp.content == b""


# ===========================================================================
# Group 3 — Happy path: tmp-images root
# ===========================================================================


class TestTmpImagesRoot:
    def test_happy_path_serves_with_sidecar_mime(
        self, client_with_tmp_store: TestClient, tmp_store: TmpImageStore
    ):
        rec = tmp_store.save(
            image_id="0123456789abcdef0123456789abcdef",
            content_bytes=b"BYTES",
            content_type="image/png",
        )
        resp = client_with_tmp_store.get(f"/views/tmp-images/{rec.image_id}")
        assert resp.status_code == 200
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["content-type"] == "image/png"
        assert resp.content == b"BYTES"

    def test_malformed_id_returns_uniform_404(
        self, client_with_tmp_store: TestClient
    ):
        resp = client_with_tmp_store.get("/views/tmp-images/not-32-hex-at-all!!!")
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_unknown_id_returns_uniform_404(
        self, client_with_tmp_store: TestClient
    ):
        resp = client_with_tmp_store.get(
            "/views/tmp-images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaab"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_torn_sidecar_returns_uniform_404(
        self, client_with_tmp_store: TestClient, tmp_store: TmpImageStore
    ):
        rec = tmp_store.save(
            image_id="fedcba9876543210fedcba9876543210",
            content_bytes=b"BYTES",
            content_type="image/png",
        )
        # Delete the sidecar only (the design the original
        # /api/tmp_images router was hardened against).
        sidecar = tmp_store.dir / f"{rec.image_id}.json"
        sidecar.unlink()
        resp = client_with_tmp_store.get(
            f"/views/tmp-images/{rec.image_id}"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}


# ===========================================================================
# Group 4 — Hardening headers
# ===========================================================================


class TestHardeningHeaders:
    def test_nosniff_on_happy_path(
        self, client_with_filesystem_root: TestClient
    ):
        resp = client_with_filesystem_root.get(
            "/views/designer-artifact/ens/feat/design/mockups/landing.html"
        )
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_nosniff_on_404_path(self, client_with_filesystem_root: TestClient):
        # nosniff on the 404 too — the browser must not sniff a
        # 404 to image/svg+xml (architect ruling, mirrors
        # /api/tmp_images/:430).
        resp = client_with_filesystem_root.get(
            "/views/designer-artifact/ens/no-such"
        )
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_cache_control_on_happy_path(
        self, client_with_filesystem_root: TestClient
    ):
        resp = client_with_filesystem_root.get(
            "/views/designer-artifact/ens/feat/design/mockups/landing.html"
        )
        # private caching, short TTL — the artifacts can change
        # (design revisions) so we don't want long browser caches.
        assert "private" in resp.headers.get("cache-control", "")


# ===========================================================================
# Group 4b — MIME pins (REWORK 2026-10-07, m1)
# ===========================================================================


class TestMimePins:
    """REWORK 2026-10-07 (m1): router-level MIME pins for SVG
    and markdown. The MIME map at
    ``daemon/services/live_views.py:_EXT_TO_MIME`` is the
    single source of truth; these tests pin the
    Content-Type the router ACTUALLY returns for the served
    extensions, so a future map change that drifts the
    response trips here.
    """

    def test_svg_served_as_image_svg_xml(
        self, client_with_filesystem_root: TestClient,
        filesystem_root_dir: pathlib.Path,
    ):
        # Stage an SVG file in the mockups subtree.
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "logo.svg").write_bytes(
            b'<svg xmlns="http://www.w3.org/2000/svg" />'
        )
        # Need to widen the allowlist to include svg.
        cfg = LiveViewsConfig()
        cfg.roots["designer-artifact"] = LiveViewsRootConfig(
            type="project_scoped",
            path=".agents/shared/planning",
            required_rel_subpath=["design", "mockups"],
            allowed_extensions=["html", "svg"],
        )
        workdir = filesystem_root_dir
        service = LiveViewsService(
            config=cfg,
            project_workdir_by_shortname_resolver=lambda shortname: (
                str(workdir) if shortname == "ens" else None
            ),
        )
        app = FastAPI()
        app.include_router(build_router())
        app.state.live_views_service = service
        c = TestClient(app)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/logo.svg"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/svg+xml; charset=utf-8"

    def test_markdown_served_as_text_markdown(
        self, client_with_filesystem_root: TestClient,
        filesystem_root_dir: pathlib.Path,
    ):
        # Stage a markdown file in the mockups subtree.
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "notes.md").write_bytes(b"# Heading\n")
        # The default allowlist is ["html"]; rebuild the
        # service with markdown allowed too.
        cfg = LiveViewsConfig()
        cfg.roots["designer-artifact"] = LiveViewsRootConfig(
            type="project_scoped",
            path=".agents/shared/planning",
            required_rel_subpath=["design", "mockups"],
            allowed_extensions=["html", "md"],
        )
        workdir = filesystem_root_dir
        service = LiveViewsService(
            config=cfg,
            project_workdir_by_shortname_resolver=lambda shortname: (
                str(workdir) if shortname == "ens" else None
            ),
        )
        app = FastAPI()
        app.include_router(build_router())
        app.state.live_views_service = service
        c = TestClient(app)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/markdown; charset=utf-8"


# ===========================================================================
# Group 4c — m2 router pins (REWORK 2026-10-07)
# ===========================================================================


class TestRouterM2Pins:
    """REWORK 2026-10-07 (m2 router pin set): extras beyond the
    Phase 1 happy / 404 / 405 coverage.

    * no-auto-index on a directory path (a GET on what would
      resolve to a directory must return the uniform 404,
      NOT a directory listing — the public-by-obscurity
      file-serving shape forbids listing);
    * image/* served at the filesystem root of a root
      (the ``image/*`` family is the most common content
      type after HTML — verify the MIME pin in the same
      way the SVG / markdown pins do, but at the
      filesystem-root of a generic root).
    """

    def test_no_auto_index_on_directory_path(
        self, tmp_path: pathlib.Path
    ):
        # Stage a real directory at the root of a filesystem
        # root. A GET on the directory path must NOT return a
        # listing — uniform 404, same body as every other miss.
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "secret.html").write_text("nope")
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        service = LiveViewsService(config=cfg)
        app = FastAPI()
        app.include_router(build_router())
        app.state.live_views_service = service
        c = TestClient(app)
        resp = c.get("/views/docs/subdir")
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}
        # Confirm a child of the directory also 404s (it would
        # resolve, but the path-collapsing-for-200 vs the
        # directory-test-for-404 split is the contract).
        resp = c.get("/views/docs/subdir/secret.html")
        # A regular file under the directory IS served — the
        # auto-index contract is about the directory path
        # itself, not its children.
        assert resp.status_code == 200

    def test_image_served_at_filesystem_root_of_root(
        self, tmp_path: pathlib.Path
    ):
        # A PNG file directly under the filesystem root of
        # a root must be served with ``image/png``. This
        # pins the MIME map for the ``image/*`` family at
        # the filesystem-root edge (no subtree prefix).
        (tmp_path / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        service = LiveViewsService(config=cfg)
        app = FastAPI()
        app.include_router(build_router())
        app.state.live_views_service = service
        c = TestClient(app)
        resp = c.get("/views/docs/hero.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        # The body is the PNG bytes we wrote.
        assert resp.content == b"\x89PNG\r\n\x1a\n"
        # X-Content-Type-Options is on every served response.
        assert resp.headers["x-content-type-options"] == "nosniff"


# ===========================================================================
# Group 5 — Method whitelist
# ===========================================================================


class TestMethodWhitelist:
    """The router exposes GET + HEAD only. POST / DELETE / PUT
    on a ``/views/<root>/<rel>`` URL must NOT match the
    route family (FastAPI's default 405 / 404 is fine).
    """

    def test_post_is_not_routed(
        self, client_with_filesystem_root: TestClient
    ):
        # POST on /views/* doesn't match any declared method.
        # FastAPI returns 405 Method Not Allowed (NOT a 200).
        resp = client_with_filesystem_root.post(
            "/views/designer-artifact/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 405

    def test_delete_is_not_routed(
        self, client_with_filesystem_root: TestClient
    ):
        resp = client_with_filesystem_root.delete(
            "/views/designer-artifact/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 405

    def test_put_is_not_routed(
        self, client_with_filesystem_root: TestClient
    ):
        resp = client_with_filesystem_root.put(
            "/views/designer-artifact/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 405


# ===========================================================================
# Group 6 — /views/livez operator probe
# ===========================================================================


class TestLivez:
    def _build_app_with_livez(self, service: LiveViewsService) -> TestClient:
        """Mount the router + the ``/views/livez`` operator probe.

        The probe is defined alongside the router in
        ``daemon/api.py``; in the test app we replicate the
        shape (router + a separate ``@app.get`` for livez).
        """
        app = FastAPI()
        app.include_router(build_router())

        @app.get("/views/livez", include_in_schema=False)
        async def live_views_livez(request: Request):
            from daemon.routers.live_views import _uniform_404
            svc = getattr(request.app.state, "live_views_service", None)
            if svc is None or not svc.enabled():
                return _uniform_404()
            return JSONResponse(
                status_code=200,
                content={
                    "status": "ok",
                    "enabled": True,
                    "roots": svc.root_names(),
                },
            )

        app.state.live_views_service = service
        return TestClient(app)

    def test_livez_200_when_enabled_and_wired(
        self, filesystem_root_dir: pathlib.Path
    ):
        cfg = LiveViewsConfig()
        # REWORK 2026-10-07 (M2): designer-artifact is
        # project_scoped. The livez probe only inspects the
        # registry shape; the resolver is never called.
        cfg.roots["designer-artifact"] = LiveViewsRootConfig(
            type="project_scoped",
            path=".agents/shared/planning",
            required_rel_subpath=["design", "mockups"],
            allowed_extensions=["html"],
        )
        service = LiveViewsService(
            config=cfg,
            project_workdir_by_shortname_resolver=lambda _s: str(
                filesystem_root_dir
            ),
        )
        c = self._build_app_with_livez(service)
        resp = c.get("/views/livez")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["enabled"] is True
        assert "designer-artifact" in body["roots"]

    def test_livez_404_when_subsystem_disabled(self):
        cfg = LiveViewsConfig(enabled=False)
        service = LiveViewsService(config=cfg)
        c = self._build_app_with_livez(service)
        resp = c.get("/views/livez")
        assert resp.status_code == 404

    def test_livez_404_when_no_service(self):
        app = FastAPI()
        app.include_router(build_router())
        c = TestClient(app)
        resp = c.get("/views/livez")
        assert resp.status_code == 404


# ===========================================================================
# Group 7 — fd-based read (REWORK 2026-10-07, M4) — O_NOFOLLOW + fstat
# ===========================================================================


class TestFdReadNoFollow:
    """REWORK 2026-10-07 (M4): the helper that backs the GET path
    for filesystem + project_scoped roots. The fd-based read
    with ``O_NOFOLLOW`` closes the final-component symlink-swap
    race that the prior ``Path.read_bytes()`` shape exposed.

    The kernel rejects the open with ``ELOOP`` when the final
    component is a symlink; the helper returns ``None`` and the
    router collapses to the uniform 404 (never a stack trace,
    never a path-disclosure log line). The service's
    realpath-based containment check still runs FIRST — these
    tests verify the second layer, the one that closes the
    race window between service-resolve and router-read.
    """

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink only")
    def test_final_component_symlink_returns_none(self, tmp_path: pathlib.Path):
        from daemon.routers.live_views import _fd_read

        outside = tmp_path / "outside.txt"
        outside.write_text("SECRET")
        target = tmp_path / "leak.txt"
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported here")
        # The O_NOFOLLOW open refuses to follow the final-
        # component symlink; the helper returns None.
        assert _fd_read(target, max_bytes=10 * 1024 * 1024) is None

    def test_regular_file_reads_bytes(self, tmp_path: pathlib.Path):
        from daemon.routers.live_views import _fd_read

        target = tmp_path / "regular.txt"
        target.write_bytes(b"BYTES")
        data = _fd_read(target, max_bytes=10 * 1024 * 1024)
        assert data == b"BYTES"

    def test_oversize_file_returns_none(self, tmp_path: pathlib.Path):
        from daemon.routers.live_views import _fd_read

        target = tmp_path / "big.bin"
        target.write_bytes(b"x" * 16)
        # Cap at 8 bytes → over-cap → None.
        assert _fd_read(target, max_bytes=8) is None
        # Exact cap → not over.
        data = _fd_read(target, max_bytes=16)
        assert data == b"x" * 16
        # Below cap → not over.
        data = _fd_read(target, max_bytes=64)
        assert data == b"x" * 16

    def test_missing_file_returns_none(self, tmp_path: pathlib.Path):
        from daemon.routers.live_views import _fd_read

        target = tmp_path / "nope.txt"
        assert _fd_read(target, max_bytes=1024) is None
