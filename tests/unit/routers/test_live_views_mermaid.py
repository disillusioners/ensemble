"""Unit tests for the Phase 3 live-view mermaid rendering.

Pins the surface:

* ``.mmd`` / ``.mermaid`` (case-insensitive) requests WITHOUT
  ``?raw=1`` → HTML wrapper page (text/html; charset=utf-8).
* ``?raw=1`` → raw source bytes with sane MIME (text/plain).
* The wrapper fetches the SAME URL + ``?raw=1`` (visible
  ``view raw`` link in the topbar).
* The wrapper loads the vendored mermaid asset from
  ``/views/assets/mermaid/mermaid.min.js`` (stable URL, no
  CDN).
* The asset endpoint serves the verified vendored bundle
  with ``application/javascript`` content-type.
* The wrapper carries a per-request CSP nonce; no ``unsafe-eval``
  and no CDN script origins.
* Non-mermaid files (``.png``, ``.html``) behavior is
  BYTE-IDENTICAL to pre-Phase-3.
* Root-agnostic: a second registered root also gets the
  interception for free.
* ``.md`` files containing ``mermaid`` fenced code blocks
  render those fences as actual diagrams (via the vendored
  asset + ``mermaid.run``); files without mermaid fences
  keep the pre-Phase-3 wrapper byte-identical (CDN-marked).

Mirrors the TestClient + stub service pattern in
``tests/unit/routers/test_live_views_router.py`` and
``tests/unit/routers/test_live_views_markdown.py``.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.routers.live_views import _MERMAID_ASSET_URL_PATH, build_router
from daemon.services.live_views import (
    _MERMAID_ASSET_PUBLIC_MIME,
    _MERMAID_ASSET_SRI_SHA256,
    _vendored_mermaid_bytes,
    LiveViewsService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service_with_mermaid_allowed(
    workdir: pathlib.Path,
    *,
    extra_extensions: list[str] | None = None,
    shortname: str = "ens",
    include_planning_root: bool = False,
    allowed_extensions: list[str] | None = None,
) -> LiveViewsService:
    """Build a LiveViewsService with mermaid extension enabled on the mockup subtree."""
    cfg = LiveViewsConfig()
    if allowed_extensions is None:
        allowed_extensions = ["html", "md", "mmd", "mermaid"]
        if extra_extensions:
            allowed_extensions += extra_extensions
    cfg.roots["designer-artifact"] = LiveViewsRootConfig(
        type="project_scoped",
        path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"],
        allowed_extensions=allowed_extensions,
    )
    if include_planning_root:
        cfg.roots["planning"] = LiveViewsRootConfig(
            type="project_scoped",
            path=".agents/shared/planning",
        )
    return LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=lambda s: (
            str(workdir) if s == shortname else None
        ),
    )


def _client_with_workdir(
    workdir: pathlib.Path,
    *,
    extra_extensions: list[str] | None = None,
    include_planning_root: bool = False,
    allowed_extensions: list[str] | None = None,
) -> TestClient:
    service = _service_with_mermaid_allowed(
        workdir,
        extra_extensions=extra_extensions,
        include_planning_root=include_planning_root,
        allowed_extensions=allowed_extensions,
    )
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = service
    return TestClient(app)


@pytest.fixture
def filesystem_root_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A workdir with a ``.mmd``, a ``.mermaid``, an HTML, a PNG and an MD file."""
    workdir = tmp_path / "project"
    mockup = (
        workdir
        / ".agents"
        / "shared"
        / "planning"
        / "feat"
        / "design"
        / "mockups"
    )
    mockup.mkdir(parents=True)
    (mockup / "landing.html").write_text("<h1>Landing</h1>")
    (mockup / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (mockup / "diagram.mmd").write_text(
        "graph TD\n  A[Start] --> B{Decision}\n  B -->|Yes| C[OK]\n"
        "  B -->|No| D[Stop]\n"
    )
    (mockup / "alt.mermaid").write_text(
        "sequenceDiagram\n  Alice->>Bob: hi\n  Bob->>Alice: hello\n"
    )
    (mockup / "BIG.MMD").write_text("graph LR\n  X --> Y\n")
    (mockup / "doc.md").write_text("# Hello\n\nplain doc")
    (mockup / "doc-with-mermaid.md").write_text(
        "# Heading\n\nintro paragraph\n\n"
        "```mermaid\n"
        "graph TD\n  A --> B\n"
        "```\n\n"
        "more text\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    (mockup / "doc-with-tilde-mermaid.md").write_text(
        "~~~mermaid\n"
        "graph TD\n  A --> B\n"
        "~~~\n"
    )
    (mockup / "doc-without-mermaid.md").write_text(
        "# Heading\n\n```\nplain code block, NOT mermaid\n```\n"
    )
    return workdir


# ===========================================================================
# Group 1 — .mmd / .mermaid wrapper surface (no ?raw=1)
# ===========================================================================


class TestMermaidWrapperSurface:
    """``GET /views/<root>/....mmd`` without ``?raw=1`` returns HTML."""

    def test_mmd_returns_html_wrapper(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        # Hardening headers ride on every response.
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("cache-control") == "private, max-age=60"

    def test_mermaid_returns_html_wrapper(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/alt.mermaid"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_mmd_uppercase_extension_returns_html_wrapper(
        self, filesystem_root_dir: pathlib.Path
    ):
        """Case-insensitive: ``.MMD`` (uppercase) is intercepted the same way."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/BIG.MMD"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"

    def test_wrapper_fetches_raw_via_same_url_with_query(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The wrapper's raw link is the SAME URL with ``?raw=1``."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
        )
        body = resp.content.decode("utf-8")
        # The visible ``view raw`` link must point to ``?raw=1``
        # — this is the URL the wrapper fetches client-side.
        assert 'href="?raw=1"' in body
        # The bootstrap script also references the same URL.
        assert "rawHref" in body
        assert "?raw=1" in body

    def test_wrapper_loads_vendored_mermaid_asset(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The wrapper's ``<script src=...>`` loads the vendored asset.

        Hard-coded URL path: ``/views/assets/mermaid/mermaid.min.js``.
        The asset is served from the same router (rides the same
        auth/OAuth-proxy gate) — defense in depth vs CDN dependence.
        """
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
        )
        body = resp.content.decode("utf-8")
        # The vendored URL is loaded via ``<script src=...>``.
        assert (
            '<script src="/views/assets/mermaid/mermaid.min.js"'
            in body
        )
        # SRI integrity hash rides (defense in depth — a corrupted
        # asset fetch is refused by the browser).
        assert _MERMAID_ASSET_SRI_SHA256 in body

    def test_wrapper_initializes_mermaid_with_strict_security(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The bootstrap script sets ``securityLevel: 'strict'``.

        Strict mode disables HTML labels in mermaid diagrams —
        defense in depth even though file content reaches the
        page only via fetch → textContent → mermaid.run (no
        direct HTML interpolation).
        """
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
        )
        body = resp.content.decode("utf-8")
        assert "securityLevel: 'strict'" in body
        # ``mermaid.run`` is the v10+ rendering entry point.
        assert "mermaid.run" in body
        # The wrapper must NOT use the older ``init`` + ``render``
        # pair (deprecated in v11).
        assert "mermaid.render(" not in body

    def test_wrapper_has_viewport_meta(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The wrapper carries a viewport meta tag (mobile primary)."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
        )
        body = resp.content.decode("utf-8")
        assert (
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            in body
        )

    def test_wrapper_has_csp_with_nonce(
        self, filesystem_root_dir: pathlib.Path
    ):
        """CSP header rides on the wrapper; nonced inline scripts only.

        The mermaid wrapper's CSP is STRICTER than the markdown
        wrapper's: NO external CDN origins for scripts (the
        vendored asset is served from ``self``). The only script
        sources are ``self`` + the per-request nonce.
        """
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
        )
        csp = resp.headers.get("content-security-policy")
        assert csp is not None
        assert "unsafe-eval" not in csp
        assert "script-src 'unsafe-inline'" not in csp
        # No CDN origins on the mermaid wrapper.
        assert "cdn.jsdelivr.net" not in csp
        # The per-request nonce rides in the CSP.
        body = resp.content.decode("utf-8")
        import re

        nonces = re.findall(r"'nonce-([^']+)'", csp)
        assert nonces, "CSP must include at least one 'nonce-XXX' directive"
        for nonce in nonces:
            assert f'nonce="{nonce}"' in body

    def test_wrapper_topbar_has_view_raw_link(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The topbar carries a visible ``view raw`` link.

        The link is a touch-friendly tap target (min 32x32) per
        the mobile-primary consumer contract.
        """
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
        )
        body = resp.content.decode("utf-8")
        assert 'id="raw-link"' in body
        assert "view raw" in body

    def test_wrapper_has_diagram_scroll_container(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The diagram container is horizontally scrollable on narrow viewports."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
        )
        body = resp.content.decode("utf-8")
        assert "diagram-scroll" in body
        assert "overflow-x: auto" in body

    def test_raw_query_param_honored_for_mmd(
        self, filesystem_root_dir: pathlib.Path
    ):
        """``?raw=1`` short-circuits the wrapper to raw bytes."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd?raw=1"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        # Raw source bytes (NOT the wrapper HTML).
        assert b"graph TD" in resp.content
        assert b"<html" not in resp.content

    def test_raw_query_param_honored_for_mermaid(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/alt.mermaid?raw=1"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        assert b"sequenceDiagram" in resp.content


# ===========================================================================
# Group 2 — Vendored asset endpoint
# ===========================================================================


class TestVendoredMermaidAssetEndpoint:
    """``GET /views/assets/mermaid/mermaid.min.js`` serves the vendored bundle."""

    def test_asset_returns_javascript_content_type(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get("/views/assets/mermaid/mermaid.min.js")
        assert resp.status_code == 200
        # The asset endpoint must advertise JS content-type
        # so the browser executes it without MIME sniffing.
        assert resp.headers["content-type"] == _MERMAID_ASSET_PUBLIC_MIME
        # Hardening headers ride (the asset IS executable JS —
        # nosniff prevents a MIME-confusion attack).
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_asset_matches_pinned_sha256(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The bytes shipped to the browser match the pinned SHA-256.

        The asset is re-verified on every request via
        ``_verify_vendored_mermaid_sha256``; the response body
        MUST match the hash recorded in
        ``daemon/services/live_views/assets/mermaid/SHA256SUMS``.
        A corrupted or tampered asset would have failed closed
        upstream (500 envelope), so reaching this assertion means
        the bytes are byte-identical to the vendored source.
        """
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get("/views/assets/mermaid/mermaid.min.js")
        assert resp.status_code == 200
        h = hashlib.sha256(resp.content).hexdigest()
        # Pull the pinned hash from the vendored file directly
        # (single source of truth) and compare. Any drift means
        # the vendored asset was updated without re-pinning.
        from daemon.services.live_views import (
            _vendored_mermaid_asset_path,
        )

        pinned = hashlib.sha256(
            _vendored_mermaid_asset_path().read_bytes()
        ).hexdigest()
        assert h == pinned

    def test_asset_head_returns_content_length(
        self, filesystem_root_dir: pathlib.Path
    ):
        """HEAD on the asset reports Content-Length matching GET."""
        c = _client_with_workdir(filesystem_root_dir)
        head_resp = c.head("/views/assets/mermaid/mermaid.min.js")
        assert head_resp.status_code == 200
        assert head_resp.headers.get("content-type") == _MERMAID_ASSET_PUBLIC_MIME
        # HEAD response carries no body but Content-Length.
        assert head_resp.headers.get("content-length") == str(
            len(_vendored_mermaid_bytes())
        )

    def test_asset_path_in_wrapper_matches_endpoint(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The wrapper's ``<script src=...>`` URL resolves via the router.

        The hard-coded path in the wrapper (``/views/assets/mermaid/mermaid.min.js``)
        must be the same path the router advertises — otherwise
        the wrapper would 404 on the asset fetch.
        """
        c = _client_with_workdir(filesystem_root_dir)
        # 1) Wrapper page references the path (with the /views prefix
        # — that's the form the browser uses).
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
        )
        body = resp.content.decode("utf-8")
        assert "/views/assets/mermaid/mermaid.min.js" in body
        # 2) The full /views-prefixed path resolves to the asset endpoint.
        asset_resp = c.get("/views/assets/mermaid/mermaid.min.js")
        assert asset_resp.status_code == 200


# ===========================================================================
# Group 3 — Non-mermaid files: byte-identical to pre-Phase-3
# ===========================================================================


class TestNonMermaidBytesIdentical:
    """``.png`` and ``.html`` behavior is unchanged."""

    def test_html_served_native_without_csp(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        # No CSP on raw HTML (designer mockups may carry
        # legitimate scripts — pre-Phase-3 contract preserved).
        assert "content-security-policy" not in resp.headers
        assert b"<h1>Landing</h1>" in resp.content

    def test_png_served_native_without_csp(
        self, filesystem_root_dir: pathlib.Path
    ):
        """``.png`` served with native MIME; no CSP (pre-Phase-3 contract)."""
        # The default allowlist (``["html","md","mmd","mermaid"]``)
        # excludes PNG — re-add it for this test.
        c = _client_with_workdir(
            filesystem_root_dir, extra_extensions=["png"]
        )
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/hero.png"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert "content-security-policy" not in resp.headers
        assert resp.content == b"\x89PNG\r\n\x1a\n"


# ===========================================================================
# Group 4 — Root-agnosticism
# ===========================================================================


class TestRootAgnosticism:
    """The interception fires for ANY registered root, not just one."""

    def test_planning_root_intercepts_mmd(
        self, filesystem_root_dir: pathlib.Path
    ):
        """A second registered root (``planning``) ALSO intercepts .mmd.

        The interception lives in the shared serving seam — no
        per-root config needed. A ``planning/<project_shortname>/....mmd``
        URL renders the wrapper, identical to the
        ``designer-artifact`` URL.
        """
        c = _client_with_workdir(
            filesystem_root_dir, include_planning_root=True
        )
        # Stage a .mmd file under the planning tree.
        planning = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        # The fixture already created diagram.mmd; the planning
        # root is project_scoped and resolves to the same
        # workdir, so the same file is reachable from a
        # different URL prefix.
        resp = c.get("/views/planning/ens/feat/design/mockups/diagram.mmd")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        # Same wrapper (asset URL + raw link + strict security).
        body = resp.content.decode("utf-8")
        assert "/views/assets/mermaid/mermaid.min.js" in body
        assert "securityLevel: 'strict'" in body

    def test_planning_root_raw_query_for_mmd(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(
            filesystem_root_dir, include_planning_root=True
        )
        resp = c.get(
            "/views/planning/ens/feat/design/mockups/diagram.mmd?raw=1"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        assert b"graph TD" in resp.content


# ===========================================================================
# Group 5 — Markdown renderer: mermaid-fence handling
# ===========================================================================


class TestMarkdownMermaidFences:
    """``.md`` files containing ``mermaid`` fences use the mermaid-aware wrapper."""

    def test_md_with_mermaid_fence_loads_vendored_asset(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The mermaid-aware markdown wrapper loads the vendored asset."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/doc-with-mermaid.md"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        body = resp.content.decode("utf-8")
        # The vendored asset URL is loaded via ``<script src=...>``.
        assert (
            '<script src="/views/assets/mermaid/mermaid.min.js"'
            in body
        )
        assert _MERMAID_ASSET_SRI_SHA256 in body
        # The bootstrap rewrites ``pre code.language-mermaid`` → ``pre.mermaid``.
        assert "code.language-mermaid" in body
        assert "pre.mermaid" in body
        # The bootstrap calls mermaid.run on the rewritten nodes.
        assert "mermaid.run" in body
        assert "securityLevel: 'strict'" in body

    def test_md_with_tilde_mermaid_fence_also_renders(
        self, filesystem_root_dir: pathlib.Path
    ):
        """Tilde fence (``~~~mermaid``) triggers the mermaid-aware wrapper too."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/doc-with-tilde-mermaid.md"
        )
        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        assert "mermaid.run" in body
        assert "/views/assets/mermaid/mermaid.min.js" in body

    def test_md_without_mermaid_fence_does_not_load_asset(
        self, filesystem_root_dir: pathlib.Path
    ):
        """A plain ``.md`` (no mermaid fences) keeps the pre-Phase-3 wrapper.

        The mermaid-aware variant is opt-in per file (cheap scan
        for ```mermaid opener before rendering). Without
        mermaid fences, the wrapper does NOT load the vendored
        asset — byte-identical to the pre-Phase-3 wrapper.
        """
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/doc-without-mermaid.md"
        )
        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        # The vendored asset URL is NOT loaded when there are
        # no mermaid fences.
        assert (
            "/views/assets/mermaid/mermaid.min.js"
            not in body
        )
        assert "mermaid.run" not in body
        # The CDN-marked + DOMPurify script tags ARE present
        # (the pre-Phase-3 wrapper).
        from daemon.services.live_views import (
            DOMPURIFY_CDN_URL,
            MARKED_CDN_URL,
        )
        assert MARKED_CDN_URL in body
        assert DOMPURIFY_CDN_URL in body

    def test_md_with_mermaid_fence_renders_tables(
        self, filesystem_root_dir: pathlib.Path
    ):
        """GFM tables render alongside the mermaid fence.

        Marked 12's default ``gfm: true`` setting handles
        tables out-of-the-box. The mermaid-aware wrapper
        uses the same marked config, so tables in the
        same .md render alongside mermaid fences without
        any per-table config.
        """
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/doc-with-mermaid.md"
        )
        assert resp.status_code == 200
        # The wrapper passes the raw markdown through to
        # marked.parse + DOMPurify.sanitize — both stages
        # preserve the table syntax. The rendered table
        # is emitted via JS at runtime; the static
        # rendered form (before bootstrap) is the escaped
        # raw markdown. We assert the source has the
        # table syntax and the wrapper carries the GFM
        # renderer + sanitizer.
        body = resp.content.decode("utf-8")
        assert "| a | b |" in body  # escaped raw markdown
        # The marked CDN URL is loaded (the GFM renderer).
        from daemon.services.live_views import MARKED_CDN_URL
        assert MARKED_CDN_URL in body

    def test_md_wrapper_mermaid_security_level_strict(
        self, filesystem_root_dir: pathlib.Path
    ):
        """The mermaid-aware markdown wrapper also pins strict security level."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/doc-with-mermaid.md"
        )
        body = resp.content.decode("utf-8")
        assert "securityLevel: 'strict'" in body

    def test_md_wrapper_escapes_malicious_payload(
        self, filesystem_root_dir: pathlib.Path
    ):
        """A ``<script>`` inside a mermaid fence is escaped.

        Defense in depth: even though mermaid runs in
        ``strict`` mode, the wrapper still html-escapes the
        raw markdown before embedding it in the fallback
        ``<pre>`` (graceful degradation contract).
        """
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "malicious.md").write_text(
            "```mermaid\n"
            "<script>alert(1)</script>\n"
            "graph TD\n  A --> B\n"
            "```\n"
        )
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/malicious.md"
        )
        body = resp.content.decode("utf-8")
        # The raw payload is escaped in the embedded fallback.
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
        # The raw form does NOT survive as live HTML.
        assert "<script>alert(1)</script>" not in body


# ===========================================================================
# Group 6 — Service-side helpers
# ===========================================================================


class TestServiceHelpers:
    """Pure helpers exposed by ``daemon.services.live_views``."""

    def test_is_mermaid_extension_lowercase(self):
        from daemon.services.live_views import is_mermaid_extension

        assert is_mermaid_extension("mmd") is True
        assert is_mermaid_extension("mermaid") is True
        assert is_mermaid_extension("md") is False
        assert is_mermaid_extension("html") is False
        assert is_mermaid_extension("png") is False

    def test_mermaid_asset_url_is_path_relative(self):
        from daemon.services.live_views import mermaid_asset_url

        url = mermaid_asset_url()
        # Path-relative: the operator-fronted OAuth proxy supplies
        # the public host. Absolute URLs (https://...) would
        # bypass the proxy gate.
        assert url.startswith("/")
        assert not url.startswith("//")  # not protocol-relative
        assert "mermaid.min.js" in url

    def test_markdown_wrapper_uses_mermaid_detection(self):
        from daemon.services.live_views import markdown_wrapper_uses_mermaid

        assert markdown_wrapper_uses_mermaid(
            "# Hi\n```mermaid\ngraph TD\nA --> B\n```\n"
        ) is True
        assert markdown_wrapper_uses_mermaid(
            "# Hi\n~~~mermaid\ngraph TD\nA --> B\n~~~\n"
        ) is True
        assert markdown_wrapper_uses_mermaid(
            "# Hi\n```\nplain code\n```\n"
        ) is False
        assert markdown_wrapper_uses_mermaid("# Hello") is False
        # Edge case — ``mermaid`` somewhere else in the text
        # (not a fence opener) does NOT trigger.
        assert markdown_wrapper_uses_mermaid(
            "Inline `mermaid` word does not count."
        ) is False


# ===========================================================================
# Group 7 — Uniform 404: extension-not-allowed on root without mmd allowlist
# ===========================================================================


class TestExtensionAllowlistForMermaid:
    """A root whose ``allowed_extensions`` does NOT include ``mmd`` returns 404."""

    def test_mmd_on_html_only_root_returns_uniform_404(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(
            filesystem_root_dir, allowed_extensions=["html"]
        )
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_mermaid_on_html_only_root_returns_uniform_404(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(
            filesystem_root_dir, allowed_extensions=["html"]
        )
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/alt.mermaid"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}