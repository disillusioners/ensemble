"""Unit tests for the Phase 2 content-aware rendering.

Pins the security posture of the ``.md`` wrapper page:

* Content-Type is ``text/html; charset=utf-8`` (not the raw
  ``text/markdown`` Phase 1 shape).
* Hardening headers ride (nosniff, cache-control).
* ``Content-Security-Policy`` is present and restrictive.
* The wrapper's CDN ``<script>`` tags carry ``integrity`` +
  ``crossorigin`` attrs (SRI enforcement).
* Bootstrap ``<script>`` carries the CSP nonce (NOT
  ``unsafe-inline``).
* Malicious payloads in the markdown (``<script>alert(1)</script>``
  + ``[x](javascript:alert(1))``) appear only in the
  escaped / embedded source — the wrapper page does NOT
  contain them as live executable HTML.
* .html served through the same router keeps its raw MIME
  and does NOT receive the strict CSP (designer mockups
  may carry legitimate scripts).
* Image types continue to ship their native Content-Type.
* 32 MiB cap still enforced on .md reads.
* Traversal rejection still works for .md-shaped paths.

Mirrors the TestClient + stub service pattern in the rest of
``tests/unit/routers/test_live_views_router.py``.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.routers.live_views import build_router
from daemon.services.live_views import (
    DOMPURIFY_CDN_INTEGRITY,
    DOMPURIFY_CDN_URL,
    MARKED_CDN_INTEGRITY,
    MARKED_CDN_URL,
    LiveViewsService,
    new_csp_nonce,
    render_markdown_wrapper,
)


def _service_with_md_allowed(
    workdir: pathlib.Path, *, extra_extensions: list[str] | None = None
) -> LiveViewsService:
    cfg = LiveViewsConfig()
    allowed = ["html", "md"] + (extra_extensions or [])
    cfg.roots["designer-artifact"] = LiveViewsRootConfig(
        type="project_scoped",
        path=".agents/shared/planning",
        required_rel_subpath=["design", "mockups"],
        allowed_extensions=allowed,
    )
    return LiveViewsService(
        config=cfg,
        project_workdir_by_shortname_resolver=lambda shortname: (
            str(workdir) if shortname == "ens" else None
        ),
    )


def _client_with_workdir(workdir: pathlib.Path) -> TestClient:
    service = _service_with_md_allowed(workdir)
    app = FastAPI()
    app.include_router(build_router())
    app.state.live_views_service = service
    return TestClient(app)


@pytest.fixture
def filesystem_root_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A workdir with one HTML + one MD file under mockups."""
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
    return workdir


# ===========================================================================
# Group 1 — Wrapper surface (Content-Type + hardening headers)
# ===========================================================================


class TestMarkdownWrapperSurface:
    """The ``.md`` response is HTML, not raw text/markdown."""

    def test_wrapper_content_type_is_html(
        self, filesystem_root_dir: pathlib.Path
    ):
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "notes.md").write_bytes(b"# Hello\n")
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"

    def test_wrapper_has_nosniff_header(
        self, filesystem_root_dir: pathlib.Path
    ):
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "notes.md").write_bytes(b"# Hello\n")
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        assert resp.headers.get("x-content-type-options") == "nosniff"

    def test_wrapper_has_csp_header(
        self, filesystem_root_dir: pathlib.Path
    ):
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "notes.md").write_bytes(b"# Hello\n")
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        csp = resp.headers.get("content-security-policy")
        assert csp is not None, "CSP header MUST be set on the .md wrapper"
        # Restrictive CSP — no unsafe-eval; the bootstrap script
        # is gated by per-request nonce.
        assert "unsafe-eval" not in csp
        assert "script-src 'unsafe-inline'" not in csp
        # jsdelivr CDN is whitelisted for marked + DOMPurify.
        assert "cdn.jsdelivr.net" in csp
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp


# ===========================================================================
# Group 2 — SRI + CDN attrs on every CDN <script>
# ===========================================================================


class TestMarkdownWrapperSriAndCdn:
    """The CDN ``<script>`` tags carry integrity + crossorigin."""

    def test_marked_script_has_integrity_and_crossorigin(
        self, filesystem_root_dir: pathlib.Path
    ):
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "notes.md").write_bytes(b"# Hello\n")
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        body = resp.content.decode("utf-8")
        assert MARKED_CDN_URL in body
        # The CDN URL is pinned (no unpinned @latest etc.).
        assert "@latest" not in body
        # The SRI integrity hash rides alongside the marked CDN tag.
        assert MARKED_CDN_INTEGRITY in body
        assert 'crossorigin="anonymous"' in body

    def test_dompurify_script_has_integrity_and_crossorigin(
        self, filesystem_root_dir: pathlib.Path
    ):
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "notes.md").write_bytes(b"# Hello\n")
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        body = resp.content.decode("utf-8")
        assert DOMPURIFY_CDN_URL in body
        assert DOMPURIFY_CDN_INTEGRITY in body
        assert 'crossorigin="anonymous"' in body

    def test_inline_bootstrap_script_carries_nonce(
    self, filesystem_root_dir: pathlib.Path
):
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "notes.md").write_bytes(b"# Hello\n")
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        csp = resp.headers.get("content-security-policy")
        # Extract any nonce values from the CSP. The CSP
        # format is ``directive 'self' 'nonce-XXX' ...`` — the
        # nonce sits inside a ``'nonce-... '`` quoted token
        # within any directive. We split directives on ``;``,
        # then look for the ``'nonce-XXX'`` token shape.
        import re

        assert csp is not None
        nonces: list[str] = []
        for directive in csp.split(";"):
            for m in re.finditer(r"'nonce-([^']+)'", directive):
                nonces.append(m.group(1))
        assert nonces, "CSP must include at least one 'nonce-XXX' directive"
        # The inline bootstrap MUST carry each nonce that
        # appears in script-src so the browser accepts it.
        # (A directive like style-src may also use the same
        # nonce — that's fine, the same nonce string on the
        # <style> tag is what matters.)
        body = resp.content.decode("utf-8")
        for nonce in nonces:
            assert f'nonce="{nonce}"' in body


# ===========================================================================
# Group 3 — Payload escaping
# ===========================================================================


class TestMarkdownWrapperPayloadEscaping:
    """A malicious payload in the markdown source must not survive
    into the wrapper page as live, executable HTML.

    Defense layers:

    * The raw markdown is HTML-escaped before being embedded
      in the wrapper's ``<article><pre>`` element (visible
      fallback for NO-JS / CDN-unreachable cases).
    * The bootstrap script reads the wrapper via ``.textContent``
      (auto-decodes the entities back to the original markdown)
      and runs marked.parse + DOMPurify.sanitize before any
      ``innerHTML`` write.

    These tests verify the first layer (escape) and that the
    payload never appears in the live wrapper page as
    executable markup. The structural assertion is: the
    payload only lives in the escaped/embedded source, never
    as a live ``<script>`` tag or live ``onerror=`` attr.
    """

    def _fetch(
        self,
        filesystem_root_dir: pathlib.Path,
        md_text: bytes,
    ) -> tuple[int, str]:
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "payload.md").write_bytes(md_text)
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/payload.md"
        )
        return resp.status_code, resp.content.decode("utf-8")

    def test_script_tag_escaped(
        self, filesystem_root_dir: pathlib.Path
    ):
        # The payload lives only in escaped form in the embedded
        # <pre> fallback. The wrapper page MUST NOT contain the
        # raw ``<script>alert(1)</script>`` sequence (which would
        # be live-executable if it ever appeared in the page
        # outside the escaping boundary).
        _, body = self._fetch(
            filesystem_root_dir,
            b"<script>alert(1)</script>\n",
        )
        # The escaped form (``&lt;script&gt;alert(1)&lt;/script&gt;``)
        # is present in the fallback <pre>.
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
        # The raw form is NOT present in the page.
        assert "<script>alert(1)</script>" not in body

    def test_javascript_uri_escaped(
        self, filesystem_root_dir: pathlib.Path
    ):
        _, body = self._fetch(
            filesystem_root_dir,
            b"[click](javascript:alert(1))\n",
        )
        # Escaped form.
        assert "[click](javascript:alert(1))" in body
        # No live javascript: link survives — the bootstrap runs
        # through DOMPurify which strips javascript: URIs.

    def test_img_onerror_escaped(
        self, filesystem_root_dir: pathlib.Path
    ):
        _, body = self._fetch(
            filesystem_root_dir,
            b'<img src=x onerror="alert(1)">\n',
        )
        # Escaped form.
        assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in body
        # Raw form NOT present.
        assert '<img src=x onerror="alert(1)">' not in body

    def test_bootstrap_script_is_the_only_inline_script(
        self, filesystem_root_dir: pathlib.Path
    ):
        # The wrapper MUST NOT have any inline scripts other than
        # the per-request nonced bootstrap. The CDN <script src>
        # tags are external, not inline.
        _, body = self._fetch(filesystem_root_dir, b"# Hello\n")
        # Count the bootstrap script tag: must contain the word
        # 'article' (the bootstrap calls document.getElementById('rendered'))
        # and the bootstrap is the ONLY inline script.
        # Count inline <script> blocks (without src=).
        import re

        inline_scripts = re.findall(
            r"<script(?![^>]*\bsrc=)[^>]*>", body
        )
        # Exactly ONE inline script (the bootstrap). External
        # CDN scripts have src= and are excluded.
        assert len(inline_scripts) == 1, (
            f"wrapper must have exactly ONE inline script "
            f"(the bootstrap); got {len(inline_scripts)}"
        )
        # The inline script carries the nonce.
        assert "nonce=" in inline_scripts[0]


# ===========================================================================
# Group 4 — Other content types stay native
# ===========================================================================


class TestOtherContentTypesNative:
    """``.html`` and images keep their native MIME + today's headers
    (no CSP, no wrapper).
    """

    def test_html_served_native_without_csp(
        self, filesystem_root_dir: pathlib.Path
    ):
        # The seeded fixture already writes
        # ``<workdir>/.agents/shared/planning/feat/design/mockups/landing.html``.
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        # Hardening headers ride.
        assert resp.headers.get("x-content-type-options") == "nosniff"
        # NO CSP — designer mockups may carry legitimate scripts.
        assert "content-security-policy" not in resp.headers
        # The body is the raw HTML the operator wrote.
        assert b"<h1>Landing</h1>" in resp.content

    def test_image_served_native_without_csp(
        self, filesystem_root_dir: pathlib.Path
    ):
        # Stage a PNG in the mockups subtree (the default
        # allowlist is ["html"]; rebuild with png in it).
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        service = _service_with_md_allowed(filesystem_root_dir, extra_extensions=["png"])
        app = FastAPI()
        app.include_router(build_router())
        app.state.live_views_service = service
        c = TestClient(app)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/hero.png"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        # Hardening headers ride.
        assert resp.headers.get("x-content-type-options") == "nosniff"
        # NO CSP on image MIME (no wrapper).
        assert "content-security-policy" not in resp.headers
        # The body is the raw PNG bytes we wrote.
        assert resp.content == b"\x89PNG\r\n\x1a\n"


# ===========================================================================
# Group 5 — Regression guards
# ===========================================================================


class TestRegressionGuards:
    """Existing protections survive the rendering refactor."""

    def test_traversal_rejected_for_md_path(
        self, filesystem_root_dir: pathlib.Path
    ):
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/../../../etc/passwd.md"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_extension_not_allowed_md_in_html_only_root(
        self, filesystem_root_dir: pathlib.Path
    ):
        # The default allowlist (when we don't add md) is
        # ["html"]. A .md file 404s on a root that only allows
        # html. We don't actually need a .md file on disk —
        # the allowlist gate is upstream of the file read.
        cfg = LiveViewsConfig()
        cfg.roots["designer-artifact"] = LiveViewsRootConfig(
            type="project_scoped",
            path=".agents/shared/planning",
            required_rel_subpath=["design", "mockups"],
            allowed_extensions=["html"],
        )
        service = LiveViewsService(
            config=cfg,
            project_workdir_by_shortname_resolver=lambda shortname: (
                str(filesystem_root_dir) if shortname == "ens" else None
            ),
        )
        app = FastAPI()
        app.include_router(build_router())
        app.state.live_views_service = service
        c = TestClient(app)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/anything.md"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_32_mib_cap_enforced_on_md(
        self, filesystem_root_dir: pathlib.Path, monkeypatch
    ):
        # Build a synthetic 33 MiB "markdown" file in the
        # mockups subtree. The 32 MiB cap is enforced by
        # the service-side ``_resolve_under_root`` and
        # the router-side ``_fd_read`` — either layer
        # collapses to uniform 404.
        from daemon.services.live_views import MAX_SERVED_BYTES

        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        big_md = b"a" * (MAX_SERVED_BYTES + 1)
        (mockup / "big.md").write_bytes(big_md)
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/big.md"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_subsystem_disabled_returns_uniform_404_for_md(
        self, filesystem_root_dir: pathlib.Path
    ):
        cfg = LiveViewsConfig(enabled=False)
        cfg.roots["designer-artifact"] = LiveViewsRootConfig(
            type="project_scoped",
            path=".agents/shared/planning",
            required_rel_subpath=["design", "mockups"],
            allowed_extensions=["html", "md"],
        )
        service = LiveViewsService(
            config=cfg,
            project_workdir_by_shortname_resolver=lambda shortname: (
                str(filesystem_root_dir) if shortname == "ens" else None
            ),
        )
        app = FastAPI()
        app.include_router(build_router())
        app.state.live_views_service = service
        c = TestClient(app)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/anything.md"
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "view not found"}

    def test_head_request_on_md_returns_headers_with_size(
        self, filesystem_root_dir: pathlib.Path
    ):
        # HEAD on a .md file must return a Content-Length that
        # matches the GET body so a HEAD-then-GET pattern works
        # correctly. The HEAD handler renders the wrapper once
        # to size it.
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "notes.md").write_bytes(b"# Hello\n")
        c = _client_with_workdir(filesystem_root_dir)
        head_resp = c.head(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        get_resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        assert head_resp.status_code == 200
        assert head_resp.headers.get("content-type") == "text/html; charset=utf-8"
        assert head_resp.headers.get("content-length") == str(
            len(get_resp.content)
        )


# ===========================================================================
# Group 6 — Phase 2 follow-up: nonce uniqueness + bootstrap pin
# ===========================================================================


class TestCspNonceUniqueness:
    """``new_csp_nonce`` must yield distinct values across calls.

    Phase 2 follow-up — a nonce that repeated would let the
    browser cache-match inline scripts across requests and
    re-evaluate the same CSP scope; the per-request fresh
    nonce is the strict mechanism that closes inline-script
    injection. The function uses ``secrets.token_urlsafe(16)``
    (≈ 128 bits of entropy) so 50 draws must produce 50 unique
    values with overwhelming probability — a regression here
    is a real bug.
    """

    def test_fifty_draws_all_unique(self):
        nonces = {new_csp_nonce() for _ in range(50)}
        assert len(nonces) == 50, (
            f"50 nonce draws produced {len(nonces)} unique values; "
            f"a collision indicates ``secrets.token_urlsafe`` was "
            f"replaced with a lower-entropy source."
        )

    def test_consecutive_requests_get_distinct_nonces_inline(
        self, filesystem_root_dir: pathlib.Path
    ):
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "notes.md").write_bytes(b"# Hello\n")
        c = _client_with_workdir(filesystem_root_dir)
        resp_a = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        resp_b = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        # The CSP header in each response carries the nonce for
        # that specific request — they MUST differ.
        csp_a = resp_a.headers["content-security-policy"]
        csp_b = resp_b.headers["content-security-policy"]
        assert csp_a != csp_b, (
            f"two consecutive requests returned the same CSP "
            f"header — the nonce is not per-request"
        )


class TestBootstrapGuardOrdering:
    """The bootstrap script's guard checks MUST come BEFORE the
    first render call.

    Phase 2 follow-up — fail-closed structural pin. If a future
    refactor moves ``marked.parse(raw)`` (or the DOMPurify
    call) ahead of the availability guards, a missing-marked
    scenario throws ``TypeError: marked.parse is not a function``
    or similar inside the try block — the catch block handles
    it, but the security model is that the guard is explicit,
    not implicit. This test extracts the inline bootstrap
    ``<script>`` body and asserts the textual order of the
    guard tokens vs. the render-call token.
    """

    def _bootstrap_body(self) -> str:
        """Return the inline bootstrap ``<script>`` body (NOT
        the CDN ``<script src=...>`` tags — those are external).

        The wrapper renders ``<script nonce=...>...</script>``
        exactly once for the inline bootstrap; the two CDN
        ``<script src=...>`` tags are external and excluded
        by the regex (``src=`` attribute must be absent). We
        find the inline script's opening tag, then walk forward
        to its matching ``</script>`` to extract the body. The
        CDN tags sit BETWEEN the inline script and the next
        opening ``<script>``, so a non-greedy scan handles the
        first ``</script>`` correctly.
        """
        body, _ = render_markdown_wrapper(
            rel_path="notes.md", markdown_text="# Hello\n"
        )
        import re

        # Find the inline bootstrap opening tag: ``<script``
        # followed by attributes that include ``nonce=`` but
        # NOT ``src=``. Then find the matching ``</script>``.
        # The body is the content between ``>`` and ``</script>``.
        m = re.search(
            r'<script(?![^>]*\bsrc=)[^>]*nonce="[^"]*"[^>]*>',
            body,
        )
        assert m is not None, (
            "could not locate the inline bootstrap <script ... nonce=...> "
            "tag — the wrapper template's inline-script shape changed"
        )
        start = m.end()
        end = body.find("</script>", start)
        assert end > start, (
            "the inline bootstrap <script> tag has no closing "
            "</script> — wrapper template structure regressed"
        )
        return body[start:end]

    def test_marked_defined_guard_precedes_render_call(self):
        body = self._bootstrap_body()
        # Anchor the render call on ``var html = marked.parse``
        # to skip the comment text that mentions ``marked.parse``
        # in prose.
        idx_guard = body.find("typeof marked === 'undefined'")
        idx_render = body.find("var html = marked.parse(raw)")
        assert idx_guard > -1, (
            "bootstrap must contain a ``typeof marked === 'undefined'`` guard"
        )
        assert idx_render > -1, (
            "bootstrap must contain the ``var html = marked.parse(raw)`` call"
        )
        assert idx_guard < idx_render, (
            "the marked-availability guard MUST precede the render call; "
            f"got guard at byte {idx_guard}, render at byte {idx_render}"
        )

    def test_marked_parse_function_guard_precedes_render_call(self):
        """Phase 2 follow-up — the ``typeof marked.parse ===
        'function'`` guard is the tighter shape (a bare
        ``typeof marked`` check passes if marked is a defined
        object but lacks ``parse`` — e.g. an ESM-default
        re-export)."""
        body = self._bootstrap_body()
        idx_guard = body.find("typeof marked.parse !== 'function'")
        idx_render = body.find("var html = marked.parse(raw)")
        assert idx_guard > -1, (
            "bootstrap must contain a ``typeof marked.parse !== "
            "'function'`` guard (the tightened Phase 2 follow-up)"
        )
        assert idx_render > -1, (
            "bootstrap must contain the ``var html = marked.parse(raw)`` call"
        )
        assert idx_guard < idx_render, (
            "the marked.parse function-availability guard MUST precede "
            f"the render call; got guard at {idx_guard}, render at {idx_render}"
        )

    def test_dompurify_defined_guard_precedes_sanitize_call(self):
        body = self._bootstrap_body()
        idx_guard = body.find("typeof DOMPurify === 'undefined'")
        idx_render = body.find("var safe = DOMPurify.sanitize(html")
        assert idx_guard > -1, (
            "bootstrap must contain a ``typeof DOMPurify === 'undefined'`` guard"
        )
        assert idx_render > -1, (
            "bootstrap must contain the ``var safe = DOMPurify.sanitize(html`` call"
        )
        assert idx_guard < idx_render, (
            "the DOMPurify-availability guard MUST precede the sanitize "
            f"call; got guard at {idx_guard}, render at {idx_render}"
        )

    def test_dompurify_sanitize_function_guard_precedes_sanitize_call(self):
        """Phase 2 follow-up — companion to the marked.parse
        guard: the DOMPurify.sanitize function shape is the
        tighter check, matching the same defense pattern."""
        body = self._bootstrap_body()
        idx_guard = body.find(
            "typeof DOMPurify.sanitize !== 'function'"
        )
        idx_render = body.find("var safe = DOMPurify.sanitize(html")
        assert idx_guard > -1, (
            "bootstrap must contain a ``typeof DOMPurify.sanitize !== "
            "'function'`` guard (the tightened Phase 2 follow-up)"
        )
        assert idx_render > -1, (
            "bootstrap must contain the ``var safe = DOMPurify.sanitize(html`` call"
        )
        assert idx_guard < idx_render, (
            "the DOMPurify.sanitize function-availability guard MUST "
            f"precede the sanitize call; got guard at {idx_guard}, render at {idx_render}"
        )


# ===========================================================================
# Group 7 — Phase 2 follow-up: Referrer-Policy on the wrapper only
# ===========================================================================


class TestReferrerPolicyWrapperOnly:
    """The ``.md`` wrapper sets ``Referrer-Policy: no-referrer``.

    Phase 2 follow-up — defense in depth: the wrapper's
    sanitized HTML may carry user-authored ``href=`` values
    that point off-origin; suppressing the Referer prevents
    the destination from learning the artifact path. Raw
    ``.html`` / image / text responses are NOT touched (they
    are operator-curated artifacts whose outbound linking
    behavior we don't presume to override).
    """

    def test_wrapper_sets_referrer_policy_no_referrer(
        self, filesystem_root_dir: pathlib.Path
    ):
        mockup = (
            filesystem_root_dir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        (mockup / "notes.md").write_bytes(b"# Hello\n")
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/notes.md"
        )
        assert resp.status_code == 200
        assert resp.headers.get("referrer-policy") == "no-referrer"

    def test_raw_html_does_not_set_referrer_policy(
        self, filesystem_root_dir: pathlib.Path
    ):
        # The seeded fixture already provides landing.html.
        c = _client_with_workdir(filesystem_root_dir)
        resp = c.get(
            "/views/designer-artifact/ens/feat/design/mockups/landing.html"
        )
        assert resp.status_code == 200
        # Raw HTML is operator-curated — wrapper-only header
        # MUST NOT leak into this response.
        assert "referrer-policy" not in resp.headers