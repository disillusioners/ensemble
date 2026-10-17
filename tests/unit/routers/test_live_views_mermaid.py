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
import os
import pathlib
from urllib.parse import quote

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

        Style posture is pinned here too: ``style-src`` MUST
        carry ``'unsafe-inline'`` and MUST NOT carry a nonce.
        Per CSP3, when a directive contains both ``'nonce-X'``
        and ``'unsafe-inline'`` browsers IGNORE ``'unsafe-inline'``
        and require nonce matches — that posture would silently
        black-fill every mermaid-rendered SVG (the runtime
        applies inline ``style="..."`` attrs to SVG nodes
        post-load with no element-level nonce).
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
        # Style-src allows inline styles (mermaid runtime applies
        # them post-load) and EXPLICITLY does not carry a nonce —
        # the nonce + unsafe-inline combination would, per CSP3,
        # be downgraded to "nonce-only" and black-fill SVGs.
        style_src_match = re.search(r"style-src[^;]*", csp)
        assert style_src_match is not None, "CSP must include a style-src directive"
        style_src = style_src_match.group(0)
        assert "'unsafe-inline'" in style_src, (
            f"style-src must allow 'unsafe-inline' (mermaid runtime "
            f"applies inline style attrs); got: {style_src!r}"
        )
        assert "'nonce-" not in style_src, (
            f"style-src must NOT carry a nonce (CSP3 makes nonce + "
            f"unsafe-inline downgrade to nonce-only, blocking mermaid "
            f"inline styles); got: {style_src!r}"
        )

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

# ===========================================================================
# Group 6 — Fix-pack W2+W3: ETag / If-None-Match 304 / memoisation
# ===========================================================================

_MMD_URL = "/views/designer-artifact/ens/feat/design/mockups/diagram.mmd"
_ASSET_URL = "/views/assets/mermaid/mermaid.min.js"


def _mockup_dir(workdir: pathlib.Path) -> pathlib.Path:
    """Return the mockup directory inside the ``filesystem_root_dir`` fixture."""
    return (
        workdir
        / ".agents"
        / "shared"
        / "planning"
        / "feat"
        / "design"
        / "mockups"
    )


class TestAssetEtagAndConditionalGet:
    """Strong ETag + ``If-None-Match → 304`` on BOTH GET and HEAD (W2+W3).

    The ETag/304 revalidation is the caching mechanism — the URL
    carries no version segment, so ``Cache-Control`` deliberately
    stays ``private, max-age=60`` (a same-URL re-vendor must not
    strand browsers past revalidation; no ``immutable``, no long
    max-age).
    """

    def test_asset_get_carries_strong_etag(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(_ASSET_URL)
        assert resp.status_code == 200
        etag = resp.headers.get("etag")
        assert etag is not None
        # Strong form: quoted opaque value, no ``W/`` prefix.
        assert etag.startswith('"') and etag.endswith('"')
        # Derived from the served bytes' sha256 (single hash pass).
        assert etag == '"%s"' % hashlib.sha256(resp.content).hexdigest()

    def test_asset_if_none_match_304_on_get(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        first = c.get(_ASSET_URL)
        etag = first.headers["etag"]
        second = c.get(_ASSET_URL, headers={"if-none-match": etag})
        assert second.status_code == 304
        assert second.content == b""
        # ETag echoes on the 304 so the client can keep revalidating.
        assert second.headers.get("etag") == etag
        # Revalidation contract headers ride on 304.
        assert second.headers.get("cache-control") == "private, max-age=60"
        assert second.headers.get("x-content-type-options") == "nosniff"

    def test_asset_if_none_match_304_on_head(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        etag = c.get(_ASSET_URL).headers["etag"]
        head = c.head(_ASSET_URL, headers={"if-none-match": etag})
        assert head.status_code == 304
        assert head.headers.get("etag") == etag

    def test_asset_if_none_match_star_matches(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(_ASSET_URL, headers={"if-none-match": "*"})
        assert resp.status_code == 304

    def test_asset_if_none_match_candidate_list_with_weak_prefix(
        self, filesystem_root_dir: pathlib.Path
    ):
        """A candidate LIST (with a ``W/``-prefixed stale entry) matches."""
        c = _client_with_workdir(filesystem_root_dir)
        etag = c.get(_ASSET_URL).headers["etag"]
        resp = c.get(
            _ASSET_URL,
            headers={"if-none-match": f'W/"stale", {etag}'},
        )
        assert resp.status_code == 304

    def test_asset_if_none_match_mismatch_returns_full_200(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        first = c.get(_ASSET_URL)
        resp = c.get(_ASSET_URL, headers={"if-none-match": '"deadbeef"'})
        assert resp.status_code == 200
        assert resp.content == first.content
        assert resp.headers.get("etag") == first.headers["etag"]

    def test_asset_head_parity_content_length(
        self, filesystem_root_dir: pathlib.Path
    ):
        """HEAD parity on the ASSET: same status + Content-Length as GET."""
        c = _client_with_workdir(filesystem_root_dir)
        get_resp = c.get(_ASSET_URL)
        head_resp = c.head(_ASSET_URL)
        assert head_resp.status_code == 200
        assert (
            head_resp.headers["content-length"]
            == get_resp.headers["content-length"]
        )

    def test_asset_carries_lockdown_document_csp(
        self, filesystem_root_dir: pathlib.Path
    ):
        """Optional fix-pack hardening: ``default-src 'none'`` on the asset.

        The bundle is consumed via ``<script src=...>`` (fetch, not
        navigation); if a browser ever renders the URL as a document,
        the CSP strips every capability. Rides on GET and HEAD alike.
        """
        c = _client_with_workdir(filesystem_root_dir)
        for resp in (c.get(_ASSET_URL), c.head(_ASSET_URL)):
            assert (
                resp.headers.get("content-security-policy")
                == "default-src 'none'"
            )

    def test_asset_cache_control_stays_moderate(
        self, filesystem_root_dir: pathlib.Path
    ):
        """No ``immutable`` / long max-age — ETag revalidation is the mechanism."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(_ASSET_URL)
        cache_control = resp.headers["cache-control"]
        assert cache_control == "private, max-age=60"
        assert "immutable" not in cache_control


class TestAssetMemoisation:
    """W2+W3 memoisation: read + hash ONCE per ``(mtime_ns, size)`` version.

    Fixtures are mktemp-fenced (``tmp_path``) — a byte-identical copy
    of the vendored asset with a DISTINCTIVE stat identity (epoch-1
    ns mtimes) wired in via ``monkeypatch``, so the copy's key can
    never collide with the real asset's key. The cache slot is
    reset per test and restored by monkeypatch afterwards.
    """

    @pytest.fixture
    def asset_copy(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> pathlib.Path:
        from daemon.services import live_views as lv

        src = lv._vendored_mermaid_asset_path()
        dst = tmp_path / "mermaid.min.js"
        dst.write_bytes(src.read_bytes())
        # Distinctive stat identity: cannot collide with the real
        # asset's key even though the bytes (and thus the size) match.
        os.utime(dst, ns=(1_000_000_000, 1_000_000_000))
        monkeypatch.setattr(
            lv, "_vendored_mermaid_asset_path", lambda: dst
        )
        monkeypatch.setattr(lv, "_MERMAID_ASSET_CACHE", None)
        return dst

    @staticmethod
    def _count_reads(
        monkeypatch: pytest.MonkeyPatch, target: pathlib.Path
    ) -> dict[str, int]:
        from pathlib import Path

        counter = {"n": 0}
        orig = Path.read_bytes

        def counting(self: Path) -> bytes:
            if self == target:
                counter["n"] += 1
            return orig(self)

        monkeypatch.setattr(Path, "read_bytes", counting)
        return counter

    def test_cached_serve_after_first_verify(
        self,
        asset_copy: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Second resolve on an unchanged file version does NOT re-read."""
        from daemon.services import live_views as lv

        counter = self._count_reads(monkeypatch, asset_copy)
        r1 = lv._vendored_mermaid_asset()
        assert counter["n"] == 1
        r2 = lv._vendored_mermaid_asset()
        assert counter["n"] == 1  # memoisation hit — no re-read, no re-hash
        assert r2.data == r1.data
        assert r2.etag == r1.etag
        assert r2.sri == lv._MERMAID_ASSET_SRI_SHA256

    def test_reverify_when_mtime_key_changes(
        self,
        asset_copy: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A touch (mtime change, bytes identical) triggers re-verification."""
        from daemon.services import live_views as lv

        counter = self._count_reads(monkeypatch, asset_copy)
        lv._vendored_mermaid_asset()
        assert counter["n"] == 1
        os.utime(asset_copy, ns=(2_000_000_000, 2_000_000_000))
        r2 = lv._vendored_mermaid_asset()
        assert counter["n"] == 2  # key changed → re-read + re-verify
        assert r2.sha256_hex == hashlib.sha256(asset_copy.read_bytes()).hexdigest()

    def test_size_key_change_reverified_fail_closed_and_not_cached(
        self,
        asset_copy: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Byte change (size key change) → re-verify → fail closed, no cache.

        Any byte drift breaks the pin, so a size-key change on the
        vendored asset exercises BOTH the re-verify trigger and the
        fail-closed contract: RuntimeError, and the failing version
        is never memoised.
        """
        from daemon.services import live_views as lv

        counter = self._count_reads(monkeypatch, asset_copy)
        lv._vendored_mermaid_asset()
        assert counter["n"] == 1
        with open(asset_copy, "ab") as fh:
            fh.write(b"tampered")
        os.utime(asset_copy, ns=(3_000_000_000, 3_000_000_000))
        key_before = lv._MERMAID_ASSET_CACHE[0]
        with pytest.raises(RuntimeError, match="hash mismatch"):
            lv._vendored_mermaid_asset()
        # The failing version is never cached: the slot still holds
        # the record keyed by the OLD (clean) stat identity — which
        # can never match the tampered file's new identity, so the
        # tampered bytes are re-verified (and fail closed) on every
        # subsequent request.
        assert lv._MERMAID_ASSET_CACHE is not None
        assert lv._MERMAID_ASSET_CACHE[0] == key_before
        assert lv._MERMAID_ASSET_CACHE[0] != (
            3_000_000_000,
            asset_copy.stat().st_size,
        )

    def test_verify_shim_forces_verification(
        self,
        asset_copy: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """The named ``_verify_vendored_mermaid_sha256`` seam still verifies."""
        from daemon.services import live_views as lv

        counter = self._count_reads(monkeypatch, asset_copy)
        lv._verify_vendored_mermaid_sha256()  # cold cache → full pass
        assert counter["n"] == 1
        lv._verify_vendored_mermaid_sha256()  # memoised → still verified
        assert counter["n"] == 1

    def test_tampered_asset_serves_500_envelope(
        self,
        asset_copy: pathlib.Path,
        filesystem_root_dir: pathlib.Path,
    ):
        """End-to-end fail-closed: tampered asset → 500, never 200 bytes."""
        c = _client_with_workdir(filesystem_root_dir)
        with open(asset_copy, "ab") as fh:
            fh.write(b"tampered")
        os.utime(asset_copy, ns=(3_000_000_000, 3_000_000_000))
        resp = c.get(_ASSET_URL)
        assert resp.status_code == 500
        assert resp.json() == {"error": "asset unavailable"}
        # The lock-down CSP rides on the fail-closed envelope too.
        assert (
            resp.headers.get("content-security-policy")
            == "default-src 'none'"
        )


# ===========================================================================
# Group 7 — Fix-pack pins: raw-param semantics, hostile filenames, edges
# ===========================================================================


class TestRawParamSemantics:
    """Pin the ACTUAL ``raw`` semantics: presence-based, any non-empty value.

    The router is the canonical ``raw`` handler (there is no
    service-level ``wants_raw_query``): ``raw=1``, ``raw=0``,
    ``raw=TRUE`` — any non-empty value — return raw bytes; the
    parameter absent or EMPTY returns the wrapper page.
    """

    @pytest.mark.parametrize("query", ["raw=1", "raw=0", "raw=TRUE", "raw=yes"])
    def test_any_non_empty_raw_value_returns_raw_bytes(
        self, filesystem_root_dir: pathlib.Path, query: str
    ):
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(f"{_MMD_URL}?{query}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        assert b"graph TD" in resp.content
        assert b"<html" not in resp.content

    def test_empty_raw_value_returns_wrapper(
        self, filesystem_root_dir: pathlib.Path
    ):
        """``?raw=`` (empty value) is NOT raw — the wrapper ships."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(f"{_MMD_URL}?raw=")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert "/views/assets/mermaid/mermaid.min.js" in resp.text


class TestWrapperHeadParity:
    """HEAD parity on the WRAPPER URL specifically (fix-pack test (a))."""

    def test_wrapper_head_matches_get_content_length(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        get_resp = c.get(_MMD_URL)
        head_resp = c.head(_MMD_URL)
        assert head_resp.status_code == 200
        assert head_resp.headers["content-type"] == "text/html; charset=utf-8"
        # HEAD Content-Length must equal the GET body length — the
        # wrapper renders the same page for the measurement.
        assert head_resp.headers["content-length"] == str(
            len(get_resp.content)
        )
        # Hardening headers ride on the HEAD response too.
        assert head_resp.headers.get("x-content-type-options") == "nosniff"

    def test_wrapper_head_parity_with_mermaid_extension(
        self, filesystem_root_dir: pathlib.Path
    ):
        """Same parity contract for the ``.mermaid`` spelling."""
        c = _client_with_workdir(filesystem_root_dir)
        url = "/views/designer-artifact/ens/feat/design/mockups/alt.mermaid"
        get_resp = c.get(url)
        head_resp = c.head(url)
        assert head_resp.status_code == 200
        assert head_resp.headers["content-length"] == str(
            len(get_resp.content)
        )


class TestHostileFilenamePins:
    """Hostile-filename regression pins on the mermaid surface."""

    def test_traversal_with_mermaid_extension_uniform_404(
        self, filesystem_root_dir: pathlib.Path
    ):
        """``..%2f..`` + a mermaid extension gets the uniform 404 —
        the interception must NEVER fire for a hostile path."""
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get("/views/designer-artifact/ens/..%2f..%2fetc%2fpasswd.mmd")
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}
        # Uniform envelope: no path / root disclosure.
        assert b"passwd" not in resp.content

    def test_double_encoded_traversal_with_mermaid_extension_uniform_404(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/%2e%2e%2f%2e%2e%2fetc%2fpasswd.mmd"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_hostile_angle_bracket_filename_escaped_in_wrapper(
        self, filesystem_root_dir: pathlib.Path
    ):
        """A ``<img onerror>`` XSS filename is served (it exists) but
        HTML-escaped in the wrapper title — the raw tag form must
        never appear in the page. (Slash-free payload: filenames
        cannot carry ``/``.)"""
        hostile = "<img src=x onerror=alert(1)>.mmd"
        (_mockup_dir(filesystem_root_dir) / hostile).write_text(
            "graph TD\n  A --> B\n"
        )
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/"
            + quote(hostile)
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        body = resp.content.decode("utf-8")
        # The raw tag form must not appear anywhere in the page.
        assert "<img src=x" not in body
        assert "onerror=alert(1)>" not in body
        # The escaped form does (title + status line).
        assert "&lt;img src=x onerror=alert(1)&gt;" in body


class TestEdgeProbes:
    """Double extensions + percent-encoded paths."""

    def test_double_extension_html_mmd_gets_wrapper(
        self, filesystem_root_dir: pathlib.Path
    ):
        """``.html.mmd`` — LAST extension wins → mermaid wrapper."""
        (_mockup_dir(filesystem_root_dir) / "page.html.mmd").write_text(
            "graph TD\n  A --> B\n"
        )
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/page.html.mmd"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert "/views/assets/mermaid/mermaid.min.js" in resp.text

    def test_double_extension_mmd_html_served_raw(
        self, filesystem_root_dir: pathlib.Path
    ):
        """``.mmd.html`` — LAST extension wins → native HTML, NO wrapper."""
        (_mockup_dir(filesystem_root_dir) / "page.mmd.html").write_text(
            "<h1>not a diagram</h1>"
        )
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/page.mmd.html"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert b"not a diagram" in resp.content
        # No wrapper interception: no vendored asset reference.
        assert "mermaid.min.js" not in resp.text

    def test_percent_encoded_filename_resolves_to_wrapper(
        self, filesystem_root_dir: pathlib.Path
    ):
        """A percent-encoded (space) filename resolves to the wrapper."""
        (_mockup_dir(filesystem_root_dir) / "diagram v2.mmd").write_text(
            "graph TD\n  A --> B\n"
        )
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/diagram%20v2.mmd"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert "/views/assets/mermaid/mermaid.min.js" in resp.text

    def test_percent_encoded_filename_raw_query_returns_bytes(
        self, filesystem_root_dir: pathlib.Path
    ):
        """Percent-encoded + ``?raw=1`` → raw bytes for the SAME file."""
        (_mockup_dir(filesystem_root_dir) / "diagram v2.mmd").write_text(
            "graph TD\n  A --> B\n"
        )
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/"
            "diagram%20v2.mmd?raw=1"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/plain; charset=utf-8"
        assert b"graph TD" in resp.content
