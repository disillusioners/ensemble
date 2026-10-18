"""Live-view HTTP route family (Phase 1 + Phase 2 + Phase 3).

GET/HEAD ``/views/<root-name>/<relative-path>`` for any registered
root. The router is the **only** HTTP surface for the subsystem;
``view_link`` is the only agent-tool minting surface.

Phase 2 of live-view-passcode (this file, 2026-10-17) extends the
family with the public lane:

* GET/HEAD/POST ``/views/public/<root>/<rel>`` — the ONLY POST on
  the entire ``/views`` surface (the verify endpoint — same-URL
  form POST, architecture-recommendation.md D6:78).
* GET ``/views/public/assets/mermaid/mermaid.min.js`` — gate-free
  asset alias (architecture-recommendation.md D11:130).

The auth gate for the public lane is the daemon-side passcode +
cookie check; the canonical fronting (Envoy / oauth2-proxy /
in-house OIDC) exempts ``/views/public`` specifically so the
lane is reachable without credentials. Direct lane behavior is
unchanged (requirement 5 — byte-identical to pre-feature).

Order-of-registration is load-bearing: the router is mounted on
the daemon ``app`` (NOT on ``api_router``) at the ``/views`` prefix
so it is captured BEFORE the SPA catch-all at ``daemon/api.py:3111``
(Starlette first-match-wins). Mirrors the ``/livez`` and
``/readyz`` precedent (``daemon/api.py:3048``).

SECURITY MODEL (architect ruling, 2026-10-07; P2 addition
2026-10-17):

* Direct lane: GET/HEAD only — read-only. There are no POST /
  DELETE / LIST routes on ``/views/<root>/<rel>``. The whole
  family is the public-by-obscurity file-serving shape
  (``conventions.md``).
* Public lane (``/views/public/<root>/<rel>``): GET/HEAD/POST
  (POST exists ONLY on the verify path; the form action is
  the same URL — architecture-recommendation.md D6:78). One
  POST endpoint on the entire ``/views`` surface; the
  method-surface test (T2.11) pins the matrix.
* Uniform 404 envelope on every miss (unknown root, unknown path,
  traversal attempt, extension-not-allowed, torn sidecar) — the
  error code is identical, the body has no path or root
  information, and the log line keeps diagnostics on the server
  side.
* ``X-Content-Type-Options: nosniff`` on every response — the
  tmp-images precedent at
  ``daemon/routers/tmp_images.py:430``; the same browser-sniff
  risk exists for HTML / SVG / JS that an agent artifact could
  contain.
* No auth at the daemon. Edge guard is documented in
  ``docs/runbooks/live-views.md``.

Content-aware rendering (Phase 2, live here): ``.md`` files are
NOT served as a raw ``text/markdown`` blob — the router wraps them
in an HTML viewer page (pinned-CDN renderer + sanitizer + CSP
nonce) via ``render_markdown_wrapper``. Non-markdown content stays
a byte-for-byte passthrough.

Phase 3 — live-view mermaid rendering (this file's changes):

* ``.mmd`` / ``.mermaid`` requests WITHOUT ``?raw=1`` are wrapped
  in an HTML viewer page that loads the vendored mermaid.js
  asset (served from ``/views/assets/mermaid/mermaid.min.js``),
  initializes ``mermaid`` with ``securityLevel: 'strict'``, and
  renders the diagram client-side. The raw source is fetched via
  ``?raw=1`` — file content NEVER touches the wrapper HTML/JS
  string interpolation surface.

* ``?raw=1`` on ``.mmd`` / ``.mermaid`` returns the raw bytes
  with sane MIME (``text/plain; charset=utf-8``) — identical to
  the pre-Pinkage raw-serving behavior.

* ``.md`` files containing ``mermaid`` fenced code blocks now
  render those fences as actual diagrams (via the same vendored
  asset + ``mermaid.run``). The ``.md`` path WITHOUT mermaid
  fences keeps the existing CDN-marked wrapper byte-identical.

* The vendored asset lives at
  ``daemon/services/live_views/assets/mermaid/mermaid.min.js``
  (loaded via the canonical source-relative ``__file__``-based
  path so PyInstaller's data-files extraction keeps the file in
  place when the binary runs in ``_MEIPASS``). The asset endpoint
  at ``/views/assets/mermaid/mermaid.min.js`` is mounted on the
  same router and rides the same auth/OAuth-proxy gate (the path
  is under the ``/views`` prefix which the middleware in front
  of the daemon gates on — the gate is untouched by this change).

TOCTOU CONTAINMENT (REWORK 2026-10-07, M4): the service does a
``realpath``-based containment check + a stat-based size cap on
the resolved path. Between the service resolve and the router
read, a window exists where the final-component file could be
swapped for a symlink, or grown past the cap. The router
mitigates both with an fd-based read: ``os.open`` with
``O_NOFOLLOW`` (the symlink-swap attempt is rejected by the
open call, not by a post-read comparison) → ``os.fstat`` (the
cap is re-checked against the fstat, not the prior stat) →
``os.read`` (we read up to the fstat size, capping any
intervening growth). The same shape is used in
``daemon/routers/tmp_images.py`` for the sidecar-MIME
substrate.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

if TYPE_CHECKING:
    from daemon.services.live_views import LiveViewsService, ResolvedTarget

logger = logging.getLogger(__name__)


# Per-response hardening headers — mirrors the tmp-images set
# (``daemon/routers/tmp_images.py:419-432``) so HTML / SVG / JS
# served from a registered root cannot be MIME-sniffed to an
# XSS vector by a browser.
_HARDENING_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=60",
}


# Asset-endpoint response headers: the shared hardening set plus
# a lock-down document CSP of ``default-src 'none'``. The bundle
# is consumed via ``<script src=...>`` (fetch, not navigation) —
# but if a browser ever renders the URL as a top-level document
# (direct navigation, hotlink into an <iframe>), the CSP strips
# it of every capability: no scripts, no frames, no forms, no
# connections. Defense in depth only; it does not affect the
# legitimate ``<script>`` consumption path and rides on the 200,
# 304, and 500 asset responses alike.
_ASSET_HEADERS: dict[str, str] = {
    **_HARDENING_HEADERS,
    "Content-Security-Policy": "default-src 'none'",
}


# Mermaid asset endpoint metadata. The single-file IIFE bundle
# at ``daemon/services/live_views/assets/mermaid/mermaid.min.js``
# is served at ``/views/assets/mermaid/mermaid.min.js``. The
# path is hard-coded so the wrapper template can render a
# stable ``<script src=…>`` URL across wrapper types
# (``.mmd``/``.mermaid`` wrapper AND ``.md`` mermaid-fence
# wrapper). The route shape lives in ``build_router`` below.
_MERMAID_ASSET_URL_PATH = "/assets/mermaid/mermaid.min.js"


def _render_markdown_response(
    file_bytes: bytes, rel_path: str, nonce: str | None = None
) -> Response:
    """Render a ``.md`` file as the HTML wrapper page.

    Phase 2 of the live-view subsystem: instead of serving the
    raw markdown as ``text/markdown`` (which browsers render as
    raw text and which never shows up as a rendered page), the
    router wraps the markdown in an HTML viewer page that loads
    a pinned-version CDN markdown renderer (``marked``) and a
    sanitizer (``DOMPurify``) with strict SRI + CSP. The
    wrapper degrades gracefully to readable raw text when JS
    is off / CDN is unreachable — the raw markdown lives in a
    ``<pre>`` in the page until the bootstrap rewrites it.

    The hardening headers (nosniff + cache-control) ride on the
    response alongside the CSP (which is built by the service-
    side ``render_markdown_wrapper`` to keep the template
    consistent with the service's other surface). The CSP
    blocks inline-script execution from any source other than
    the per-request nonced bootstrap — defense in depth with
    the SRI hashes on the CDN tags.

    The raw ``.html`` family (designer mockups, etc.) is NOT
    affected — they continue to be served with the basic
    ``_HARDENING_HEADERS`` set and no CSP, per the architect
    ruling that legitimate HTML may include scripts.
    """
    from daemon.services.live_views import (
        markdown_wrapper_uses_mermaid,
        render_markdown_wrapper,
        render_markdown_wrapper_with_mermaid,
    )

    # The file bytes were already size-capped (32 MiB) by
    # the service and re-capped at read time by ``_fd_read``
    # (``os.fstat`` → re-check). We pass them straight through
    # to the wrapper — utf-8 decode failure (rare; valid
    # markdown is text) collapses to a uniform response.
    try:
        markdown_text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # Fall back to a lossy decode so the wrapper still
        # renders something readable (the byte sequence
        # probably contains binary garbage — operator can
        # spot the issue from the daemon log).
        markdown_text = file_bytes.decode("utf-8", errors="replace")

    # Phase 3: when the markdown contains ``mermaid`` fenced
    # code blocks, ship the mermaid-aware variant (loads the
    # vendored mermaid asset + bootstraps ``mermaid.run``
    # after DOMPurify). When the markdown has NO mermaid
    # fences, ship the original CDN-marked variant BYTE-
    # IDENTICAL to the pre-Pinkage render (no CSP change, no
    # SRI change, no vendored asset fetch on the client).
    if markdown_wrapper_uses_mermaid(markdown_text):
        body, headers = render_markdown_wrapper_with_mermaid(
            rel_path=rel_path,
            markdown_text=markdown_text,
            nonce=nonce,
        )
    else:
        body, headers = render_markdown_wrapper(
            rel_path=rel_path, markdown_text=markdown_text, nonce=nonce
        )
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        status_code=200,
        headers={
            **headers,
            "Content-Length": str(len(body.encode("utf-8"))),
        },
    )


def _render_mermaid_response(
    rel_path: str, nonce: str | None = None, *, public: bool = False
) -> Response:
    """Render the ``.mmd`` / ``.mermaid`` wrapper page.

    The wrapper fetches the raw source from the SAME URL +
    ``?raw=1`` (so the file's content never enters the
    HTML/JS string interpolation surface). The vendored
    mermaid.js asset is loaded via a stable
    ``/views/assets/mermaid/mermaid.min.js`` URL (or the
    public-lane mirror ``/views/public/assets/...`` when
    ``public=True``; T2.10) with the SRI hash pinned.

    Returns the wrapper HTML response (text/html) with the
    mermaid-strict CSP (NO CDN script-src, NO unsafe-inline
    for scripts) and the standard hardening headers. The
    raw source file is NOT opened here — the wrapper does
    that itself via the ``?raw=1`` URL the wrapper carries.
    """
    from daemon.services.live_views import render_mermaid_wrapper

    body, headers = render_mermaid_wrapper(
        rel_path=rel_path, nonce=nonce, public=public
    )
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        status_code=200,
        headers={
            **headers,
            "Content-Length": str(len(body.encode("utf-8"))),
        },
    )


def _render_mermaid_wrapper_body(
    rel_path: str, nonce: str | None = None, *, public: bool = False
) -> bytes:
    """Return the wrapper HTML bytes for HEAD Content-Length measurement.

    The HEAD handler must report a Content-Length that
    matches the GET body — the wrapper page size is a
    function of the rel_path (not the file bytes), so the
    HEAD path can render the wrapper independently of any
    file read. This mirrors the existing MD path's HEAD
    contract.
    """
    from daemon.services.live_views import render_mermaid_wrapper

    body, _headers = render_mermaid_wrapper(
        rel_path=rel_path, nonce=nonce, public=public
    )
    return body.encode("utf-8")


def _wants_raw_query(request: Request) -> bool:
    """Return True iff the inbound request asks for raw bytes.

    The router IS the canonical ``raw`` param handler — there is
    no service-level ``wants_raw_query``. Semantics are
    presence-based and pinned by tests: ANY non-empty value
    counts as raw (``?raw=1``, ``?raw=0``, ``?raw=TRUE`` all
    return raw bytes — clients are not trusted to send a
    canonical ``1``), while the parameter absent OR empty
    (``?raw=``) returns the wrapper page.
    """
    raw = request.query_params.get("raw")
    return raw is not None and raw != ""


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Evaluate ``If-None-Match`` against our strong ETag (RFC 7232).

    ``*`` matches any current representation. Otherwise the
    header is a comma-separated candidate list; each candidate
    may carry a ``W/`` weakness prefix (we serve a strong ETag —
    a weak candidate still compares equal on its opaque value,
    which is the comparison browsers actually perform) and is
    quote-stripped before the exact match. No header → no match
    (full 200 response).
    """
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    ours = etag.strip('"')
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate[:2] in ("W/", "w/"):
            candidate = candidate[2:]
        if candidate.strip('"') == ours:
            return True
    return False


def _serve_mermaid_asset(request: Request) -> Response:
    """Return the vendored ``mermaid.min.js`` bytes with JS content-type.

    W2+W3 (fix pack 2026-10-17): the asset record is resolved via
    the memoised :func:`_vendored_mermaid_asset` loader — bytes
    are read ONCE and hashed ONCE per ``(st_mtime_ns, st_size)``
    file version instead of on every request (the prior shape
    re-read + re-hashed the ~5.2 MiB bundle twice per request).
    The response carries a STRONG ``ETag`` derived from that
    single hash pass; a request whose ``If-None-Match`` matches
    gets ``304 Not Modified`` (both GET and HEAD) so repeat
    browsers skip the 5.2 MiB body — that revalidation, not an
    ``immutable``/long ``max-age`` (the URL carries no version
    segment, so a re-vendor must not strand browsers), is the
    caching mechanism. Fail-closed posture unchanged: a missing
    or hash-mismatched asset returns the 500 envelope rather
    than serving a different script.
    """
    from daemon.services.live_views import (
        _MERMAID_ASSET_PUBLIC_MIME,
        _vendored_mermaid_asset,
    )

    try:
        record = _vendored_mermaid_asset()
    except (FileNotFoundError, RuntimeError) as exc:
        # Asset missing or hash mismatch — fail closed with a
        # 500 envelope. The wrapper page's bootstrap script
        # treats a non-200 asset fetch as a render failure
        # (graceful degradation shows the raw text); an
        # operator-facing 500 here is preferable to a
        # silently-empty asset response.
        logger.error(
            "[LiveViews] vendored mermaid asset unavailable: %s",
            exc,
        )
        return JSONResponse(
            status_code=500,
            content={"error": "asset unavailable"},
            headers=_ASSET_HEADERS,
        )
    if _etag_matches(request.headers.get("if-none-match"), record.etag):
        return Response(
            status_code=304,
            headers={
                **_ASSET_HEADERS,
                "ETag": record.etag,
            },
        )
    return Response(
        content=record.data,
        media_type=_MERMAID_ASSET_PUBLIC_MIME,
        status_code=200,
        headers={
            **_ASSET_HEADERS,
            "ETag": record.etag,
            "Content-Length": str(len(record.data)),
        },
    )


def _fd_read(path: "os.PathLike[str] | str", max_bytes: int) -> bytes | None:
    """Read ``path`` via ``os.open`` + ``O_NOFOLLOW`` + ``os.fstat``.

    REWORK 2026-10-07 (M4): the prior ``Path.read_bytes()`` form
    opened the file a SECOND time, after the service had already
    resolved + stat'd it. The window between the two opens was
    the TOCTOU race:

    * final-component symlink swap — the service's
      ``realpath`` containment check at ``live_views.py:527-534``
      ran against the ORIGINAL (regular-file) path; a swap
      immediately before the second open turns the file into a
      symlink, and ``read_bytes`` would follow it.
    * size-cap race — a file that grew past the cap between the
      service's stat and the second open would be served at
      the new (over-cap) size.

    The fd-based form closes both:

    * ``O_NOFOLLOW`` refuses to follow a final-component
      symlink at OPEN time (kernel-level); the open fails
      with ``ELOOP`` and we return ``None`` (uniform 404).
    * ``fstat`` after open reports the CURRENT size; we
      re-check against the cap and read up to that
      (capped) size — any intervening growth above the
      fstat is truncated to the cap, not served.

    Returns ``None`` on any open / fstat / over-cap / read
    error. The router collapses ``None`` to the uniform
    404 — never a stack trace, never a path-disclosure
    log line at WARNING. The same shape is used in
    ``daemon/routers/tmp_images.py`` for the sidecar-MIME
    substrate.
    """
    try:
        # ``O_NOFOLLOW`` is the load-bearing flag. A symlink
        # final component triggers ``ELOOP`` here, not at a
        # post-read check.
        fd = os.open(
            os.fspath(path),
            os.O_RDONLY | os.O_NOFOLLOW,
        )
    except OSError:
        return None
    try:
        try:
            st = os.fstat(fd)
        except OSError:
            return None
        size = st.st_size
        if size > max_bytes:
            return None
        # Read in a loop until we have ``size`` bytes or hit
        # EOF (``b""``). Single-shot ``os.read(fd, size)`` is
        # not guaranteed to return the full file on every
        # kernel / fs — short reads DO happen (large files,
        # pipes, some network filesystems), and returning
        # truncated bytes would silently ship a 404-corrupt
        # file to the browser. The cap is ``size`` itself
        # (the fstat-confirmed current size), so an
        # intervening growth above the cap never escapes;
        # the loop terminates either at EOF (clean) or
        # after reading exactly ``size`` bytes (cap).
        try:
            chunks: list[bytes] = []
            remaining = size
            while remaining > 0:
                buf = os.read(fd, remaining)
                if not buf:
                    # EOF before we hit ``size`` — the file
                    # was truncated under us (rare; only
                    # possible when another writer is
                    # racing). Return what we have; the
                    # router treats ``None`` as the only
                    # hard-fail signal and a short read
                    # is still bytes the caller can serve.
                    break
                chunks.append(buf)
                remaining -= len(buf)
            return b"".join(chunks)
        except OSError:
            return None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _uniform_404() -> Response:
    """Return a uniform 404 — same body, same code, no path disclosure.

    Used for every miss (unknown root, traversal, not-found,
    extension-not-allowed, store-not-wired, subsystem-off). The
    body intentionally echoes nothing about which case fired;
    a probing client cannot distinguish "root exists but path
    missing" from "root disabled" from "traversal rejected".
    """
    return JSONResponse(
        status_code=404,
        content={"error": "view not found"},
        headers=_HARDENING_HEADERS,
    )


def _public_csp_nonce() -> str:
    """Return a fresh CSP nonce for the public-lane pages.

    Wraps :func:`daemon.services.view_passcode_pages.new_csp_nonce`
    (the canonical source of truth — keeps the nonce contract
    in the pages module). Import is local to avoid a top-of-
    file import cycle in tests that mock the page module.
    """
    from daemon.services.view_passcode_pages import new_csp_nonce

    return new_csp_nonce()


def _clear_grant_cookie_header(path: str) -> str:
    """Build the ``Set-Cookie`` value that clears the grant cookie.

    T2.5 / D12 — the validation chain's failure contract is
    "interstitial + cookie clear" (architecture-recommendation.md
    D6:76: ``InvalidToken`` → interstitial + cookie clear). A
    stale / tampered / expired cookie must not linger in the
    browser: the clear rides ``Max-Age=0`` on the SAME
    exact-path scope the cookie was set with — RFC 6265 §5.3
    (step 11) deletes a stored cookie only when name + domain
    + PATH all match, so the clear's ``Path`` must be the
    exact string the set used (:func:`_grant_cookie_path`,
    queryless). A ``Path=/`` clear does NOT reach a cookie
    stored at a deeper path.
    """
    from daemon.services.view_passcode_service import GRANT_COOKIE_NAME

    return (
        f"{GRANT_COOKIE_NAME}=; "
        f"Path={path}; "
        "HttpOnly; Secure; SameSite=Lax; "
        "Max-Age=0"
    )


def _clear_grant_cookie_root_header() -> str:
    """Belt-and-suspenders ``Path=/`` clear variant.

    Covers only a cookie stored at the root scope (RFC 6265
    §5.3 name+domain+PATH equality — a root-scoped delete
    never reaches a deeper-path cookie). The exact-path
    variant (:func:`_clear_grant_cookie_header`) is the
    authoritative delete; this one is hygiene for any
    ``Path=/``-shaped grant cookie that a legacy client
    stored before the exact-scope mint existed. Emitted as a
    SECOND ``Set-Cookie`` header alongside the exact-path
    clear.
    """
    from daemon.services.view_passcode_service import GRANT_COOKIE_NAME

    return (
        f"{GRANT_COOKIE_NAME}=; "
        "Path=/; "
        "HttpOnly; Secure; SameSite=Lax; "
        "Max-Age=0"
    )


def _render_interstitial_page(
    request: Request, root_name: str, rel_path: str
) -> Response:
    """Render the passcode-entry interstitial (no cookie path).

    When a grant cookie was PRESENTED but failed validation
    (decrypt / claims / generation / exp), the interstitial
    rides the cookie-clear ``Set-Cookie`` (T2.5 — "interstitial
    + cookie clear"; the stale token must not survive in the
    browser). A request with NO cookie renders the plain
    interstitial (nothing to clear).
    """
    from daemon.services.view_passcode_pages import (
        page_headers,
        render_interstitial,
    )

    nonce = _public_csp_nonce()
    form_action = _request_public_url_path(
        request, root_name, rel_path
    )
    body = render_interstitial(
        csp_nonce=nonce,
        root_name=root_name,
        rel_path=rel_path,
        form_action=form_action,
    )
    headers = page_headers(csp_nonce=nonce)
    if request.cookies.get("view_grant"):
        # A cookie was presented but the gate rejected it —
        # clear it (the failed-validation contract). RFC
        # 6265 §5.3 deletes a stored cookie only on name +
        # domain + PATH equality, so the authoritative
        # clear rides the SAME exact queryless path the
        # mint used (:func:`_grant_cookie_path`). A
        # ``Path=/`` clear never reaches a deeper-path
        # cookie — it is emitted as a SECOND header purely
        # as hygiene for any root-scoped grant cookie a
        # legacy client stored.
        cookie_path = _grant_cookie_path(root_name, rel_path)
        response = Response(
            content=body,
            status_code=200,
            headers=headers,
        )
        response.headers.append(
            "Set-Cookie", _clear_grant_cookie_header(cookie_path)
        )
        response.headers.append(
            "Set-Cookie", _clear_grant_cookie_root_header()
        )
        return response
    return Response(
        content=body,
        status_code=200,
        headers=headers,
    )


def _render_failure_page(
    request: Request, root_name: str, rel_path: str
) -> Response:
    """Render the interstitial with a generic error after a wrong code."""
    from daemon.services.view_passcode_pages import (
        page_headers,
        render_failure,
    )

    nonce = _public_csp_nonce()
    form_action = _request_public_url_path(
        request, root_name, rel_path
    )
    body = render_failure(
        csp_nonce=nonce,
        root_name=root_name,
        rel_path=rel_path,
        form_action=form_action,
    )
    headers = page_headers(csp_nonce=nonce)
    return Response(
        content=body,
        status_code=200,
        headers=headers,
    )


def _render_locked_page(request: Request) -> Response:
    """Render the locked-notice page (HTTP 200, no content / no form)."""
    from daemon.services.view_passcode_pages import (
        page_headers,
        render_locked,
    )

    nonce = _public_csp_nonce()
    body = render_locked(csp_nonce=nonce)
    headers = page_headers(csp_nonce=nonce)
    return Response(
        content=body,
        status_code=200,
        headers=headers,
    )


def _render_rate_limited_page(request: Request) -> Response:
    """Render the 429 page (per-IP bucket exceeded)."""
    from daemon.services.view_passcode_pages import (
        page_headers,
        render_rate_limited,
    )

    nonce = _public_csp_nonce()
    body = render_rate_limited(csp_nonce=nonce)
    headers = page_headers(csp_nonce=nonce)
    return Response(
        content=body,
        status_code=429,
        headers=headers,
    )


def _render_secure_context_page(
    request: Request, *, csp_nonce: str
) -> Response:
    """Render the explicit "secure-context required" page."""
    from daemon.services.view_passcode_pages import (
        page_headers,
        render_secure_context_required,
    )

    body = render_secure_context_required(csp_nonce=csp_nonce)
    headers = page_headers(csp_nonce=csp_nonce)
    return Response(
        content=body,
        status_code=200,
        headers=headers,
    )


def _client_ip_for_bucket(request: Request) -> str:
    """Return the rightmost XFF entry else the socket peer.

    Architecture-recommendation.md D7:83-86 (rightmost-XFF
    dominates every topology; leftmost is spoofable behind a
    reverse proxy). The XFF parse is SINGLE-HOMED in
    :func:`daemon.services.view_passcode_service.
    _rightmost_xff_ip` (comma-split, whitespace-trimmed,
    empty/invalid entries skipped) — the limiter keys on its
    output and the service-level pins hold it; this wrapper
    contributes only the request-scope glue: the socket-peer
    fallback (the Starlette ``client`` scope entry) when no
    XFF header survives the parse, and ``"unknown"`` when
    neither is present (the limiter's malformed-IP guard
    collapses to "do not consume a token"; the public lane
    never bricks on a missing socket peer).
    """
    from daemon.services.view_passcode_service import (
        _rightmost_xff_ip,
    )

    rightmost = _rightmost_xff_ip(request.headers)
    if rightmost:
        return rightmost
    scope_client = request.scope.get("client")
    if scope_client and isinstance(scope_client, tuple):
        host = scope_client[0]
        if host:
            return host
    return "unknown"


def _same_host_origin_check(request: Request) -> bool:
    """Cheap CSRF check — same-host Origin / Referer when present.

    Architecture-recommendation.md D6:77 (CSRF defense-in-depth).
    The check ONLY fires when the header is present (some
    clients omit it; a missing header is not a CSRF block).
    The check is a string ``startswith`` against the request
    host — same-host requests always pass; cross-host
    requests fail with a 403 generic envelope.

    Returns ``True`` when the header is absent (the binding
    posture: same-host check when header present).
    Returns ``False`` only on a confirmed cross-host hit.
    """
    headers = request.headers
    host = headers.get("host")
    if not host:
        # The Host header is mandatory on HTTP/1.1; absent
        # only on degenerate traffic. Skip the check rather
        # than 403 — the binding posture is permissive on
        # missing headers (ADR D6:77 — applies when present).
        return True
    expected = host.strip().lower()
    for header_name in ("origin", "referer"):
        raw = headers.get(header_name)
        if not raw:
            continue
        # Strip the scheme + authority and compare the
        # remaining host against the request Host.
        value = raw.strip().lower()
        # Origin / Referer can be ``https://host:port/path``;
        # we only care about the host segment.
        if value.startswith("https://"):
            value = value[len("https://"):]
        elif value.startswith("http://"):
            value = value[len("http://"):]
        # Take the first path segment end or query start or
        # end-of-string.
        for terminator in ("/", "?", "#"):
            if terminator in value:
                value = value.split(terminator, 1)[0]
                break
        if value and value != expected:
            return False
    return True


def _is_https_request(request: Request) -> bool:
    """Return True iff the inbound request is HTTPS.

    Architecture-recommendation.md D6:79 (plain-HTTP edge —
    Secure blocks cookie storage on direct http://LAN:9797).
    Reads the standard ``X-Forwarded-Proto`` header (set by
    Envoy / oauth2-proxy); falls back to the URL scheme
    (rarely the right answer behind a TLS-terminating proxy).
    """
    forwarded_proto = request.headers.get(
        "x-forwarded-proto"
    ) or request.headers.get("X-Forwarded-Proto")
    if forwarded_proto:
        return forwarded_proto.strip().lower() == "https"
    # ``request.url.scheme`` is the URL Starlette parsed —
    # direct HTTPS hits carry ``https`` here. Behind a
    # proxy the X-Forwarded-Proto header is the signal.
    try:
        return request.url.scheme == "https"
    except Exception:
        return False


def _grant_cookie_path(root_name: str, rel_path: str) -> str:
    """Return the EXACT queryless public URL path for cookie ``Path`` scoping.

    Architecture-recommendation.md D6:73 (Path = the exact
    public view URL — tightest scope, NOT the
    ``/views/public`` prefix) + D6:76 ("cookie Path matching
    is query-insensitive"). RFC 6265 §5.1.4 path-matching
    compares the attribute against the request PATH only —
    the query string is never part of the match. A ``Path``
    attribute carrying ``?raw=1`` (or any query) therefore
    matches NO request path ever: the minted cookie could
    never be sent back, and every post-303 GET would
    re-render the interstitial — an infinite loop for
    query-bearing entry URLs. The query lives only in the
    303 ``Location`` and the form action
    (:func:`_request_public_url_path`), never here.
    """
    return f"/views/public/{root_name}/{rel_path}"


def _request_public_url_path(
    request: Request, root_name: str, rel_path: str
) -> str:
    """Return the full public view URL — 303 ``Location`` + form action.

    Carries any ``?raw=1`` (or utm / tracking) query string
    verbatim: the same-URL POST contract (architecture-
    recommendation.md D6:78) round-trips whatever URL the
    customer is on. NOT for cookie ``Path`` scoping — the
    Set-Cookie Path uses :func:`_grant_cookie_path`
    (queryless; RFC 6265 §5.1.4 path-matching excludes the
    query, so a query-bearing Path attribute can never match
    a subsequent request).
    """
    base = f"/views/public/{root_name}/{rel_path}"
    raw_query = request.url.query
    if raw_query:
        return f"{base}?{raw_query}"
    return base


def _build_grant_cookie_value(
    *, token: bytes, path: str, ttl_seconds: int
) -> str:
    """Build the ``Set-Cookie`` value for the grant cookie.

    Flags (architecture-recommendation.md D6:75): ``HttpOnly``,
    ``Secure``, ``SameSite=Lax``, ``Max-Age=ttl_seconds``. Path
    = the exact QUERYLESS public view path (tightest scope;
    ADR D6:73; RFC 6265 §5.1.4 — path-matching excludes the
    query, see :func:`_grant_cookie_path`). The cookie NAME is
    ``view_grant`` (the canonical identifier from the
    grant-cookie codec module).
    """
    from daemon.services.view_passcode_service import (
        GRANT_COOKIE_NAME,
    )

    # URL-safe base64 string form — Fernet produces
    # URL-safe characters; ASCII-encoded bytestrings decode
    # cleanly. The browser stores the token verbatim.
    token_str = token.decode("ascii")
    return (
        f"{GRANT_COOKIE_NAME}={token_str}; "
        f"Path={path}; "
        f"HttpOnly; Secure; SameSite=Lax; "
        f"Max-Age={ttl_seconds}"
    )


def _resolve_service(request: Request) -> "LiveViewsService | None":
    """Return the per-app ``LiveViewsService`` or None if not wired.

    The service is installed by the lifespan into
    ``app.state.live_views_service``. A request that arrives
    before the lifespan ran (probe-style early traffic) gets
    ``None`` here, which collapses to the uniform 404 — never
    a 500 / stack trace / "service unavailable" message.
    """
    return getattr(request.app.state, "live_views_service", None)


def _resolve_passcode_service(request: Request):
    """Return the per-app ``ViewPasscodeService`` or None.

    The service is installed by the lifespan into
    ``app.state.view_passcode_service``. A request that arrives
    before the lifespan ran (probe-style early traffic / the
    subsystem was disabled at config load) gets ``None`` here,
    which collapses to the uniform 404 — never a 500 / stack
    trace / "service unavailable" message (mirrors
    :func:`_resolve_service`).
    """
    # Local import — the live-views router is the load-bearing
    # consumer; the service is a peer module, and the local
    # import keeps the router importable even if the passcode
    # service module is ever refactored (mirrors the other
    # service imports in this file).
    from daemon.services.view_passcode_service import ViewPasscodeService

    svc = getattr(request.app.state, "view_passcode_service", None)
    if svc is None or not isinstance(svc, ViewPasscodeService):
        return None
    return svc


def _row_locked(row) -> bool:
    """Return ``row.locked`` regardless of row-shape.

    P1's SQLAlchemy-backed store returns a SQLModel row with
    attribute access (``.locked``); the test suite's
    in-memory store returns a plain dict (``row["locked"]``).
    Both shapes flow through the same router code — this
    helper is the single access seam.
    """
    if row is None:
        return False
    if isinstance(row, dict):
        return bool(row.get("locked", False))
    return bool(getattr(row, "locked", False))


def _row_generation(row) -> int:
    """Return ``row.generation`` regardless of row-shape."""
    if row is None:
        return 0
    if isinstance(row, dict):
        return int(row.get("generation", 0))
    return int(getattr(row, "generation", 0))


def _row_wrapped_passcode(row) -> bytes:
    """Return ``row.wrapped_passcode`` regardless of row-shape."""
    if row is None:
        return b""
    if isinstance(row, dict):
        return bytes(row.get("wrapped_passcode", b""))
    return bytes(getattr(row, "wrapped_passcode", b""))


def _resolve_verify_rate_limiter(request: Request):
    """Return the per-app ``VerifyRateLimiter`` or None.

    The limiter is installed alongside the passcode service at
    lifespan time. Missing limiter → the verify handler
    SKIPS the bucket entirely (``if limiter is not None and
    not limiter.check(ip)`` — the POST proceeds ungated by
    the bucket; the durable strike lock remains the
    authoritative control). This is a deliberate fail-open
    on the limiter-only seam (architecture-recommendation.md
    V6 — a limiter misconfiguration must not brick the
    lane); the UNIFORM-404 fail-closed posture applies to
    the PASSCODE SERVICE seam (``_resolve_passcode_service``
    → None → uniform 404), not here. Lifespan guarantees
    wiring, so a missing limiter in production = degraded
    throttling, never a 404 and never a crash.
    """
    from daemon.services.view_passcode_service import VerifyRateLimiter

    limiter = getattr(request.app.state, "verify_rate_limiter", None)
    if limiter is None or not isinstance(limiter, VerifyRateLimiter):
        return None
    return limiter


def _serve_resolved_target(
    request: Request,
    resolved: "ResolvedTarget",
    rel_path: str,
    *,
    public: bool = False,
) -> Response:
    """Serve a ``ResolvedTarget`` for either lane.

    P2 of live-view-passcode (T2.10): the content-serving
    pipeline is shared between the direct lane and the public
    lane — the gate sits BEFORE this helper. The ONLY
    difference is the ``public=True`` flag, which selects the
    public-lane asset URL for the ``.mmd`` / ``.mermaid``
    wrapper pages (the vendored bundle is served at BOTH
    ``/views/assets/...`` and ``/views/public/assets/...``
    via the gate-free asset alias — the wrapper emits the
    appropriate path so the asset fetch rides the public-lane
    exemption in BOTH deployment states).

    All other rendering (tmp-images blob read, ``?raw=1``
    passthrough, ``.md`` content-aware rendering, ``.mmd``
    ``.mermaid`` wrapper) is identical to the direct lane —
    the public lane rides the same path byte-for-byte once
    the gate is past (architecture-recommendation.md
    §Attack surface :185).
    """
    from daemon.services.live_views import (
        MAX_SERVED_BYTES,
        extension_for,
        is_mermaid_extension,
        is_markdown_content_type,
        new_csp_nonce,
    )

    if resolved.root_type == "tmp_images":
        service = _resolve_service(request)
        open_result = (
            service.open_tmp_image(rel_path.strip())
            if service is not None
            else None
        )
        if open_result is None:
            return _uniform_404()
        data, content_type, _sha = open_result
        return Response(
            content=data,
            media_type=content_type,
            status_code=200,
            headers={
                **_HARDENING_HEADERS,
                "Content-Length": str(len(data)),
            },
        )

    # ``.mmd`` / ``.mermaid`` wrapper interception (T2.10
    # public asset mirror): the wrapper page uses the
    # public-lane asset URL when ``public=True`` (the
    # vendored bundle is mirrored under ``/views/public/assets/``,
    # the same gate-free alias route serves it).
    if is_mermaid_extension(extension_for(rel_path)) and not _wants_raw_query(
        request
    ):
        return _render_mermaid_response(
            rel_path, nonce=new_csp_nonce(), public=public
        )

    # Filesystem / project_scoped: serve the bytes via the
    # same fd-read pipeline as the direct lane (TOCTOU
    # containment — see module docstring).
    file_bytes = _fd_read(resolved.on_disk_path, MAX_SERVED_BYTES)
    if file_bytes is None:
        return _uniform_404()

    if is_markdown_content_type(resolved.content_type):
        return _render_markdown_response(
            file_bytes, rel_path, nonce=new_csp_nonce()
        )

    return Response(
        content=file_bytes,
        media_type=resolved.content_type,
        status_code=200,
        headers={
            **_HARDENING_HEADERS,
            "Content-Length": str(len(file_bytes)),
        },
    )


def build_router() -> APIRouter:
    """Build the ``/views`` router.

    The router does NOT capture a service at build time — it
    pulls the service from ``app.state`` at request time, so
    the lifespan can wire the real service (with the per-app
    ``TmpImageStore`` + project-workdir resolvers) AFTER
    ``create_app()`` returns and the service becomes available
    for the first request.

    Method surface (P2 of live-view-passcode):

    * Direct lane ``/views/{root}/{rel}``: GET/HEAD only — no
      POST/DELETE/LIST. The read-only contract is enforced at
      the FastAPI layer.
    * Public lane ``/views/public/{root}/{rel}``: GET/HEAD/POST.
      The POST exists ONLY on the public verify path (T2.11
      method-surface test pins the matrix — any other ``/views``
      path × POST → 405/404 uniform).
    * Public asset alias ``/views/public/assets/mermaid/...``:
      GET (and HEAD) only — gate-free, asset-header lockdown.

    **ROUTE-DECLARATION ORDER IS LOAD-BEARING** (architecture-
    recommendation.md D1:26): the public-lane sub-routes are
    declared BEFORE the parametrized
    ``/{root_name}/{rel_path:path}`` handlers — Starlette
    first-match-wins. A naive ordering would let the
    parametrized GET/HEAD match ``/views/public/...`` as
    ``root_name="public", rel_path="..."``, which is the
    reserved-name collision (T2.2). The defensive mirror in
    :func:`_resolve` closes the case where order drifts, but
    the right answer is structural: declare first, match
    first.
    """
    from daemon.services.live_views import (
        PathNotFoundError,
        RootNotFoundError,
        TraversalError,
    )

    router = APIRouter(prefix="/views", tags=["live_views"])

    @router.get(_MERMAID_ASSET_URL_PATH)
    async def get_mermaid_asset(request: Request) -> Response:
        """Serve the vendored mermaid.js bundle.

        Hard-coded path (``/views/assets/mermaid/mermaid.min.js``).
        Lives on the same router as ``/{root_name}/{rel_path:path}``
        so it rides the same auth/OAuth-proxy gate (the
        ``/views`` prefix sits behind the operator-fronted
        proxy per ``docs/runbooks/live-views.md``). Mounted
        BEFORE the catch-all root route below — Starlette
        first-match-wins ordering is load-bearing here.

        The vendored bytes are SHA-256-pinned at vendoring time
        and re-verified per file version via the memoised
        loader (:func:`_vendored_mermaid_asset` — re-read +
        re-hash only when ``(st_mtime_ns, st_size)`` changes);
        a corrupted or missing on-disk asset returns 500
        instead of silently shipping a different bundle to the
        browser. Responses carry a strong ``ETag``; a matching
        ``If-None-Match`` short-circuits to ``304 Not Modified``.
        """
        return _serve_mermaid_asset(request)

    @router.head(_MERMAID_ASSET_URL_PATH)
    async def head_mermaid_asset(request: Request) -> Response:
        """HEAD support for the vendored mermaid.js asset.

        Mirrors the GET handler — same hardening headers, same
        ``ETag``, same ``304 Not Modified`` contract on a
        matching ``If-None-Match``, and on 200 the same body
        length (the memoised record is resolved on HEAD too so
        a corrupted asset is detected on HEAD just as it would
        be on GET). Body bytes are discarded after the
        Content-Length measurement.
        """
        from daemon.services.live_views import (
            _MERMAID_ASSET_PUBLIC_MIME,
            _vendored_mermaid_asset,
        )
        try:
            record = _vendored_mermaid_asset()
        except (FileNotFoundError, RuntimeError):
            return Response(
                status_code=500,
                headers=_ASSET_HEADERS,
            )
        if _etag_matches(request.headers.get("if-none-match"), record.etag):
            return Response(
                status_code=304,
                headers={
                    **_ASSET_HEADERS,
                    "ETag": record.etag,
                },
            )
        return Response(
            status_code=200,
            headers={
                **_ASSET_HEADERS,
                "ETag": record.etag,
                "Content-Type": _MERMAID_ASSET_PUBLIC_MIME,
                "Content-Length": str(len(record.data)),
            },
        )

    async def _resolve(
        request: Request, root_name: str, rel_path: str
    ) -> Response:
        service = _resolve_service(request)
        if service is None or not service.enabled():
            return _uniform_404()
        # Defensive layer 3 (architecture-recommendation.md
        # D1:29): the generic (non-public) handler additionally
        # 404s ``root_name == "public"`` even if a future drift
        # let one slip past the registry validator. Reserved-
        # name guard mirroring :func:`_public_resolve`.
        from daemon.services.live_views import is_reserved_root_name

        if is_reserved_root_name(root_name):
            return _uniform_404()
        try:
            # REWORK 2026-10-07 (M2): every registered root is
            # now anonymous-resolvable (designer-artifact moved
            # from filesystem-against-calling-instance to
            # project_scoped, so the project is named in the URL,
            # not pulled from ``request.state.instance_id``). The
            # per-instance workdir argument is still accepted on
            # the service signature for OPERATOR-supplied
            # filesystem roots that opt into instance workdir
            # resolution; the seeded three roots do not consult
            # it. We pass ``None`` here — nothing in the daemon
            # ever set ``request.state.instance_id`` anyway, so
            # the prior wiring was a permanent 404 for every
            # anonymous browser hit.
            resolved = service.resolve_for_instance(
                root_name,
                rel_path,
                calling_instance_id=None,
            )
        except (RootNotFoundError, TraversalError, PathNotFoundError):
            return _uniform_404()
        return resolved

    # ─────────────────────────────────────────────────────────
    # Public passcode lane (P2 of live-view-passcode)
    #
    # ROUTE-DECLARATION ORDER IS LOAD-BEARING
    # (architecture-recommendation.md D1:26): every public
    # route below MUST be declared BEFORE the parametrized
    # ``/{root_name}/{rel_path:path}`` handlers (the HEAD
    # @router.head at :678 and the GET @router.get at :802).
    # Starlette first-match-wins would otherwise route
    # ``/views/public/foo/bar`` through the parametrized
    # handlers with ``root_name="public", rel_path="foo/bar"``
    # — the reserved-name collision (T2.2). The defensive
    # mirror in :func:`_resolve` (``root_name == "public"``
    # collapse) closes the case where order drifts, but the
    # right answer is structural: declare first, match first.
    # ─────────────────────────────────────────────────────────

    # Public asset alias — ``/views/public/assets/mermaid/...``
    # (architecture-recommendation.md D11:130). Gate-FREE — no
    # passcode check, no grant cookie required. The bundle is
    # vendored + lockdown-headered (``_ASSET_HEADERS``) so
    # serving it unauthenticated is inert (no scripts, no
    # forms, no connections, no frames; the existing
    # ``default-src 'none'`` CSP makes it a dead document if
    # rendered directly). Do NOT widen the exemption to
    # ``/views/assets`` (the same locked header set applies
    # there; the gating exemption is exact-prefix on
    # ``/views/public``).
    @router.get("/public/assets/mermaid/mermaid.min.js")
    async def get_public_mermaid_asset(request: Request) -> Response:
        """Public-lane asset alias — gate-free vendored bundle.

        Same vendored bytes as the direct-lane
        ``/views/assets/mermaid/mermaid.min.js`` alias (T2.10);
        served with the SAME ``_ASSET_HEADERS`` lockdown
        (``default-src 'none'`` + nosniff). Public-lane wrapper
        pages (the public ``.mmd`` / ``.mermaid`` viewer)
        reference this mirrored path so the asset fetch rides
        the public-lane exemption in BOTH deployment states
        (architecture-recommendation.md §📌 Deployment Note).
        """
        return _serve_mermaid_asset(request)

    @router.head("/public/assets/mermaid/mermaid.min.js")
    async def head_public_mermaid_asset(request: Request) -> Response:
        """HEAD mirror of :func:`get_public_mermaid_asset`."""
        # Reuse the same logic — the memoised
        # :func:`_vendored_mermaid_asset` record resolves on
        # HEAD too, so a corrupted asset is detected on HEAD
        # just as it would be on GET.
        from daemon.services.live_views import (
            _MERMAID_ASSET_PUBLIC_MIME,
            _vendored_mermaid_asset,
        )
        try:
            record = _vendored_mermaid_asset()
        except (FileNotFoundError, RuntimeError):
            return Response(
                status_code=500,
                headers=_ASSET_HEADERS,
            )
        if _etag_matches(
            request.headers.get("if-none-match"), record.etag
        ):
            return Response(
                status_code=304,
                headers={
                    **_ASSET_HEADERS,
                    "ETag": record.etag,
                },
            )
        return Response(
            status_code=200,
            headers={
                **_ASSET_HEADERS,
                "ETag": record.etag,
                "Content-Type": _MERMAID_ASSET_PUBLIC_MIME,
                "Content-Length": str(len(record.data)),
            },
        )

    async def _public_resolve(
        request: Request, root_name: str, rel_path: str
    ) -> Response | None:
        """Resolve ``(root, rel)`` for the public lane.

        Returns the same :class:`ResolvedTarget` envelope the
        direct lane returns on success, or a uniform 404
        Response on any miss. ``None`` distinguishes "no row
        minted yet" (the verify-handler path collapses to
        uniform 404; the GET path collapses to a renderable
        404 too — the gate never reveals whether a row exists
        on a never-minted path).

        Gate order (architecture-recommendation.md D2:36 +
        §Attack surface :186; cookie FIRST per D5:68 —
        "Valid grant cookies persist through lockout —
        lockout gates the entry lane, not established
        grants", the reviewer-upheld deviation from the
        original 4/5 numbering):

        1. well-formed root / rel
        2. root configured + enabled
        3. passcode row exists (any miss → uniform 404; NO
           row is created for unminted paths — ADR T7
           write-amplification pin)
        4. grant cookie valid? (valid → resolved envelope
           for serving; invalid/absent → fall through)
        5. locked? (returns a locked Response, not None —
           only reached when no valid grant was presented,
           because a valid grant short-circuits at #4)
        """
        from daemon.services.live_views import (
            PathNotFoundError,
            RootNotFoundError,
            TraversalError,
            is_reserved_root_name,
            is_well_formed_rel_path,
            is_well_formed_root_name,
        )

        if not is_well_formed_root_name(root_name):
            return _uniform_404()
        # Reserved-name mirror (architecture-recommendation.md
        # D1:27 — three-layer guard; the route-side mirror
        # collapses reserved-name misses to uniform 404 even
        # if a future config drift let one slip past the
        # registry validator).
        if is_reserved_root_name(root_name):
            return _uniform_404()
        if not is_well_formed_rel_path(rel_path):
            return _uniform_404()
        service = _resolve_service(request)
        if service is None or not service.enabled():
            return _uniform_404()
        # Passcode row existence check — gate #3. ANY miss
        # here is uniform 404 (the row existence is NEVER
        # disclosed to the client; no leaked signal even to a
        # holder of the URL).
        passcode_service = _resolve_passcode_service(request)
        if passcode_service is None:
            return _uniform_404()
        row = passcode_service.get_row(root_name, rel_path)
        if row is None:
            return _uniform_404()
        # Grant cookie check — gate #4 — runs BEFORE the
        # locked check (architecture-recommendation.md
        # D5:68: "Valid grant cookies persist through
        # lockout — lockout gates the entry lane, not
        # established grants"). The cookie is the
        # higher-trust signal: an established grant
        # represents a customer who already passed the gate;
        # a lockout is for the entry lane (POST verify /
        # first-time GET).
        cipher = getattr(request.app.state, "view_passcode_cipher", None)
        if cipher is not None:
            cookie_token = request.cookies.get("view_grant")
            if cookie_token:
                from daemon.services.view_passcode_service import (
                    parse_grant_cookie,
                )
                try:
                    parse_grant_cookie(
                        cipher=cipher,
                        token=cookie_token,
                        expected_root=root_name,
                        expected_rel=rel_path,
                        expected_generation=_row_generation(row),
                    )
                    # Valid grant → resolve the file content
                    # via the SAME pipeline the direct lane
                    # uses.
                    try:
                        return service.resolve_for_instance(
                            root_name,
                            rel_path,
                            calling_instance_id=None,
                        )
                    except (
                        RootNotFoundError,
                        TraversalError,
                        PathNotFoundError,
                    ):
                        return _uniform_404()
                except Exception:
                    # Cookie failure (decrypt / claims /
                    # exp / generation mismatch) — fall
                    # through to the locked check + the
                    # interstitial. Per-request cookie
                    # failures are LOG-ONLY here (no audit
                    # row, write-amplification pin;
                    # architecture-recommendation.md D9:115).
                    pass
        # Locked short-circuit — gate #5. A valid grant
        # would have served above; with no valid grant,
        # the locked view renders the dedicated
        # locked-notice page (HTTP 200, "ask for a fresh
        # link", architecture-recommendation.md V3). No
        # compare, no increment on this path.
        if _row_locked(row):
            return _render_locked_page(request)
        # No valid grant cookie → render the interstitial.
        return None

    async def _public_get(
        request: Request, root_name: str, rel_path: str
    ) -> Response:
        """GET handler for ``/views/public/{root}/{rel}``.

        Gate order (architecture-recommendation.md §Attack
        surface :186 + D2:36; cookie first per D5:68):
        well-formed → root configured → row exists → cookie
        valid? → locked? → interstitial. The CONTENT SERVING
        path reuses the EXISTING ``_resolve`` pipeline
        byte-for-byte, only entered post-grant.
        """
        resolved = await _public_resolve(
            request, root_name, rel_path
        )
        if isinstance(resolved, Response):
            return resolved
        if resolved is None:
            # No valid grant cookie / unminted / locked →
            # render the interstitial. The locked path is
            # caught in ``_public_resolve`` (returns a
            # locked-page Response, which is
            # ``isinstance(resolved, Response)``).
            return _render_interstitial_page(
                request, root_name, rel_path
            )
        # Post-grant: ``resolved`` is a ``ResolvedTarget``;
        # dispatch through the shared serving helper so the
        # public lane gets the same ``.md`` / ``.mmd`` /
        # ``?raw=1`` treatment as the direct lane, with the
        # public asset URL emitted for the mermaid wrappers
        # (T2.10).
        return _serve_resolved_target(
            request, resolved, rel_path, public=True
        )

    async def _public_head(
        request: Request, root_name: str, rel_path: str
    ) -> Response:
        """HEAD mirror of :func:`_public_get`.

        HEAD on the public lane never serves content (no
        Content-Length for an interstitial page is meaningful,
        and the ``Content-Length`` of the gated view depends
        on the gate state which the HEAD request doesn't
        carry). The HEAD contract: 200 + ``Content-Length: 0``
        when the gate would serve (so a probe sees the URL is
        reachable), 200 + ``Content-Length: 0`` on the
        interstitial path (so a probe sees the URL is
        reachable too — uniformity is the discipline).
        Locked views: 200 + the locked-page body bytes
        (matches GET).
        """
        resolved = await _public_resolve(
            request, root_name, rel_path
        )
        if isinstance(resolved, Response):
            return resolved
        # No valid grant → interstitial; HEAD returns the
        # headers-only shape with ``Content-Length: 0`` (the
        # body is computed on GET; the HEAD contract is the
        # reachable-or-not signal, not the body).
        return Response(
            status_code=200,
            headers={
                **_HARDENING_HEADERS,
                "Content-Length": "0",
            },
        )

    async def _public_post(
        request: Request, root_name: str, rel_path: str
    ) -> Response:
        """POST handler — verify customer-entered passcode.

        Ordered per architecture-recommendation.md §Attack
        surface :186: per-IP bucket (429, fail-open) →
        unminted → uniform 404 (NO row created) → locked
        short-circuit (locked page; no compare, no increment)
        → constant-time compare → success: grant cookie + 303
        See Other (PRG) back to the same URL; failure:
        re-render interstitial with a generic error.

        Logging hygiene (architecture-recommendation.md
        D11:131): NEVER log the submitted passcode, grant
        tokens, or ciphertexts; wrong-passcode log lines
        carry the counter snapshot only; the strike count is
        NEVER disclosed in any response body pre-lock
        (architecture-recommendation.md D2:40 — never aid
        multi-IP grinding).

        CSRF (architecture-recommendation.md D6:77): the
        cheap same-host Origin/Referer check, applied when
        the header is present. Mismatch → 403 generic.
        """
        from daemon.services.view_passcode_service import (
            VerifyOutcome,
            issue_grant_cookie,
            parse_grant_cookie,
        )

        # Service presence — fail-closed (uniform 404) on
        # misconfigured lifespan / disabled subsystem.
        passcode_service = _resolve_passcode_service(request)
        if passcode_service is None:
            return _uniform_404()

        # Per-IP bucket — gate #1 (ADR §Attack surface :186).
        # The bucket throttles the lane BEFORE any row lookup
        # or compare; an over-budget IP gets the 429 page
        # WITHOUT touching the strike counter (binding T5
        # interplay pin a).
        limiter = _resolve_verify_rate_limiter(request)
        ip = _client_ip_for_bucket(request)
        if limiter is not None and not limiter.check(ip):
            return _render_rate_limited_page(request)

        # Row lookup — gate #2 (unminted → uniform 404, NO
        # row created; architecture-recommendation.md D2:36).
        row = passcode_service.get_row(root_name, rel_path)
        if row is None:
            return _uniform_404()

        # Locked short-circuit — gate #3. No compare, no
        # increment (architecture-recommendation.md D5:65).
        if _row_locked(row):
            return _render_locked_page(request)

        # CSRF — gate #4 (architecture-recommendation.md
        # D6:77). Only applied when the header is present
        # (a missing Origin / Referer is NOT a CSRF block —
        # some clients omit them; the binding posture is
        # "same-host check when header present").
        if not _same_host_origin_check(request):
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden"},
                headers=_HARDENING_HEADERS,
            )

        # Read the customer-entered passcode from the form
        # body. The form action = same URL (architecture-
        # recommendation.md D6:78); the field name is
        # ``code`` (the interstitial template emits it).
        try:
            form = await request.form()
        except Exception:
            # A malformed multipart body (not a form) —
            # generic failure path, no body leak.
            return _render_failure_page(
                request, root_name, rel_path
            )
        presented = form.get("code")
        if not isinstance(presented, str):
            # Missing / non-string — generic failure. The
            # verify() method raises TypeError on non-str
            # input; we map to the failure page BEFORE the
            # call so the strike counter is never touched
            # on a malformed form (the binding posture:
            # only a real compare attempt consumes a
            # strike).
            return _render_failure_page(
                request, root_name, rel_path
            )

        # Constant-time compare — gate #5.
        try:
            outcome = passcode_service.verify(row, presented)
        except TypeError:
            # Non-str ``presented`` (defensive; the
            # ``isinstance`` check above already collapses
            # this). Map to the failure page WITHOUT a
            # strike increment — the binding posture is
            # "only a real compare attempt counts".
            return _render_failure_page(
                request, root_name, rel_path
            )
        except Exception:
            # Rotation-fail / cipher unavailable / any
            # decryption exception — the canonical
            # primitive is "regeneration required". NEVER
            # a 500; render the interstitial with the
            # generic error so the customer can retry once
            # the owner has refreshed.
            logger.warning(
                "[LiveViews] public verify rotation-fail "
                "for root=%s rel=%s",
                root_name,
                rel_path,
            )
            return _render_failure_page(
                request, root_name, rel_path
            )

        if outcome == VerifyOutcome.OK:
            # Success — issue the grant cookie + 303 See
            # Other (PRG; the customer is sent back to the
            # same URL via GET, which then short-circuits
            # through the cookie gate to the served view).
            cipher = getattr(
                request.app.state, "view_passcode_cipher", None
            )
            ttl_seconds = getattr(
                request.app.state,
                "view_passcode_grant_ttl_seconds",
                86400,
            )
            if cipher is None:
                # Cipher missing → fail-closed. The cookie
                # cannot be issued safely; collapse to
                # uniform 404.
                return _uniform_404()
            token = issue_grant_cookie(
                cipher=cipher,
                root_name=root_name,
                rel_path=rel_path,
                generation=_row_generation(row),
                ttl_seconds=int(ttl_seconds),
            )
            # Audit row — successful verify emits ``grant``
            # (architecture-recommendation.md D9:115 —
            # the closed action set).
            try:
                passcode_service.append_audit(
                    root_name,
                    rel_path,
                    _row_generation(row),
                    action="grant",
                    actor=None,
                    ip=ip,
                )
            except Exception:
                # Audit write failure is LOG-ONLY; never
                # break the customer flow on an audit
                # hiccup.
                logger.warning(
                    "[LiveViews] grant audit append failed "
                    "for root=%s rel=%s",
                    root_name,
                    rel_path,
                )
            # Cookie Path = the EXACT queryless public view
            # path (tightest scope; architecture-
            # recommendation.md D6:73 — NOT the
            # ``/views/public`` prefix). RFC 6265 §5.1.4
            # path-matching compares the Path attribute
            # against the request PATH only — the query is
            # never part of the match (ADR D6:76 "cookie
            # Path matching is query-insensitive"). A query-
            # bearing Path attribute would match NO request:
            # correct passcode → 303 → cookie never sent →
            # infinite interstitial loop for query-bearing
            # entry URLs (?raw=1, utm). The query lives ONLY
            # in the 303 ``Location`` below and the form
            # action.
            cookie_path = _grant_cookie_path(root_name, rel_path)
            # On plain HTTP the ``Secure`` flag blocks
            # cookie storage on the browser
            # (architecture-recommendation.md D6:79).
            # Detect and render the explicit
            # secure-context message — never a redirect
            # loop.
            if not _is_https_request(request):
                return _render_secure_context_page(
                    request, csp_nonce=_public_csp_nonce()
                )
            response = Response(
                status_code=303,
                headers={
                    **_HARDENING_HEADERS,
                    # Location = the FULL view URL the
                    # customer just POSTed from — query
                    # included verbatim (same-URL POST →
                    # PRG; D6:78). NOT the cookie path.
                    "Location": _request_public_url_path(
                        request, root_name, rel_path
                    ),
                    "Set-Cookie": _build_grant_cookie_value(
                        token=token,
                        path=cookie_path,
                        ttl_seconds=int(ttl_seconds),
                    ),
                },
            )
            # Belt-and-suspenders: the cookie WAS set with
            # ``Secure`` (see ``_build_grant_cookie_value``)
            # so a plain-HTTP request never reaches the
            # success branch — the explicit
            # secure-context page above is the only
            # plain-HTTP outcome. If a test ever injects
            # a non-secure request through the success
            # path, the ``Secure`` flag still denies the
            # cookie storage on the browser.
            return response

        # Wrong code — gate #6. Increment the strike
        # counter (atomic in the service; the lockout
        # audit row is emitted in the SAME transaction
        # on the 10th strike). The bucket AND the strike
        # both fire (binding T5 interplay pin c).
        try:
            attempts, locked_now = passcode_service.record_failure(
                root_name,
                rel_path,
                ip=ip,
                actor=None,
            )
        except Exception:
            attempts, locked_now = -1, False
        # Audit row — wrong verify emits ``deny``
        # (architecture-recommendation.md D9:115).
        try:
            passcode_service.append_audit(
                root_name,
                rel_path,
                _row_generation(row),
                action="deny",
                actor=None,
                ip=ip,
            )
        except Exception:
            logger.warning(
                "[LiveViews] deny audit append failed for "
                "root=%s rel=%s",
                root_name,
                rel_path,
            )
        # LOG: counter snapshot only — NEVER the
        # submitted passcode, NEVER the ciphertext
        # (architecture-recommendation.md D11:131).
        logger.info(
            "[LiveViews] public verify denied for "
            "root=%s rel=%s attempts=%s",
            root_name,
            rel_path,
            attempts if attempts >= 0 else "n/a",
        )
        # If the strike just flipped the lock, render the
        # locked page instead of the generic failure
        # (architecture-recommendation.md V3 — HTTP 200
        # with notice; architect sign-off D2/R11). The
        # strike counter + lock audit row is the durable
        # signal; the response body never discloses the
        # count (architecture-recommendation.md D2:40).
        if locked_now:
            return _render_locked_page(request)
        return _render_failure_page(
            request, root_name, rel_path
        )

    @router.get("/public/{root_name}/{rel_path:path}")
    async def get_public_view(
        root_name: str, rel_path: str, request: Request
    ) -> Response:
        return await _public_get(request, root_name, rel_path)

    @router.head("/public/{root_name}/{rel_path:path}")
    async def head_public_view(
        root_name: str, rel_path: str, request: Request
    ) -> Response:
        return await _public_head(request, root_name, rel_path)

    @router.post("/public/{root_name}/{rel_path:path}")
    async def post_public_verify(
        root_name: str, rel_path: str, request: Request
    ) -> Response:
        return await _public_post(request, root_name, rel_path)

    @router.head("/{root_name}/{rel_path:path}")
    async def head_view(
        root_name: str, rel_path: str, request: Request
    ) -> Response:
        resolved = await _resolve(request, root_name, rel_path)
        if isinstance(resolved, Response):
            return resolved
        # Filesystem / project_scoped: stat already happened in
        # the service. tmp_images: REWORK 2026-10-07 (m4) — the
        # service's ``_resolve_tmp_images`` now uses
        # ``stat_with_meta`` (no blob read at resolve time),
        # so the HEAD handler can return content_type + size
        # WITHOUT re-opening the store. The ``Content-Length``
        # is the fstat'd size; the blob is read on the GET
        # path only.
        if resolved.root_type == "tmp_images":
            return Response(
                status_code=200,
                headers={
                    **_HARDENING_HEADERS,
                    "Content-Type": resolved.content_type,
                    "Content-Length": str(resolved.size_bytes),
                },
            )
        # Phase 3: ``.mmd`` / ``.mermaid`` (case-insensitive on
        # the URL — the extension is already lowercased by
        # ``extension_for`` before reaching here) WITHOUT
        # ``?raw=1`` returns the wrapper page. The wrapper
        # content is independent of the file bytes (the
        # wrapper fetches the source itself via ``?raw=1``),
        # so the HEAD Content-Length matches the GET body.
        from daemon.services.live_views import (
            extension_for,
            is_mermaid_extension,
            new_csp_nonce,
        )

        if is_mermaid_extension(extension_for(rel_path)) and not _wants_raw_query(
            request
        ):
            return Response(
                status_code=200,
                headers={
                    **_HARDENING_HEADERS,
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Length": str(
                        len(
                            _render_mermaid_wrapper_body(
                                rel_path, nonce=new_csp_nonce()
                            )
                        )
                    ),
                },
            )
        # Phase 2: ``.md`` files wrap into a per-response HTML
        # page whose size depends on the wrapper template +
        # markdown content. The HEAD handler must return a
        # Content-Length that matches the GET body — we render
        # the wrapper once (with a stable test-friendly
        # nonce) just to measure it, then throw the body
        # away. The cost is the same fd-read + utf-8 decode the
        # GET path does; for a HEAD request this is wasted
        # work but keeps the contract consistent.
        from daemon.services.live_views import (
            MAX_SERVED_BYTES,
            is_markdown_content_type,
            render_markdown_wrapper,
            render_markdown_wrapper_with_mermaid,
            markdown_wrapper_uses_mermaid,
        )

        if is_markdown_content_type(resolved.content_type):
            try:
                file_bytes = _fd_read(resolved.on_disk_path, MAX_SERVED_BYTES)
                if file_bytes is None:
                    return _uniform_404()
                markdown_text = file_bytes.decode(
                    "utf-8", errors="replace"
                )
                if markdown_wrapper_uses_mermaid(markdown_text):
                    body, headers = render_markdown_wrapper_with_mermaid(
                        rel_path=rel_path,
                        markdown_text=markdown_text,
                        nonce=new_csp_nonce(),
                    )
                else:
                    body, headers = render_markdown_wrapper(
                        rel_path=rel_path,
                        markdown_text=markdown_text,
                        nonce=new_csp_nonce(),
                    )
                return Response(
                    status_code=200,
                    headers={
                        **_HARDENING_HEADERS,
                        **{k: v for k, v in headers.items() if k != "X-Content-Type-Options" and k != "Cache-Control"},
                        "Content-Type": "text/html; charset=utf-8",
                        "Content-Length": str(
                            len(body.encode("utf-8"))
                        ),
                    },
                )
            except Exception:
                # The HEAD path is best-effort: if the
                # wrapper render blows up for any reason we
                # collapse to the headers-only response so
                # the client sees a consistent shape.
                return Response(
                    status_code=200,
                    headers={
                        **_HARDENING_HEADERS,
                        "Content-Type": "text/html; charset=utf-8",
                        "Content-Length": "0",
                    },
                )
        return Response(
            status_code=200,
            headers={
                **_HARDENING_HEADERS,
                "Content-Type": resolved.content_type,
                "Content-Length": str(resolved.size_bytes),
            },
        )

    @router.get("/{root_name}/{rel_path:path}")
    async def get_view(
        root_name: str, rel_path: str, request: Request
    ) -> Response:
        resolved = await _resolve(request, root_name, rel_path)
        if isinstance(resolved, Response):
            return resolved

        if resolved.root_type == "tmp_images":
            # REWORK 2026-10-07 (m4): the public
            # ``LiveViewsService.open_tmp_image`` is the
            # seam — the router no longer reaches into the
            # service's private ``_tmp_image_store``. The
            # service catches store errors and returns None;
            # we collapse None to the uniform 404.
            service = _resolve_service(request)
            open_result = (
                service.open_tmp_image(rel_path.strip())
                if service is not None
                else None
            )
            if open_result is None:
                return _uniform_404()
            data, content_type, _sha = open_result
            return Response(
                content=data,
                media_type=content_type,
                status_code=200,
                headers={
                    **_HARDENING_HEADERS,
                    "Content-Length": str(len(data)),
                },
            )

        # Phase 3: ``.mmd`` / ``.mermaid`` interception. The
        # interception runs BEFORE the file-read path so the
        # wrapper page is returned without ever opening the
        # file (the wrapper fetches the source itself via
        # ``?raw=1``). ``?raw=1`` short-circuits to the
        # raw-byte path below (extension-typed MIME, identical
        # to the pre-Pinkage raw serving). Case-insensitive
        # — the extension is already lowercased by
        # ``extension_for`` before reaching here.
        from daemon.services.live_views import (
            extension_for,
            is_mermaid_extension,
            new_csp_nonce,
        )

        if is_mermaid_extension(extension_for(rel_path)) and not _wants_raw_query(
            request
        ):
            return _render_mermaid_response(
                rel_path, nonce=new_csp_nonce()
            )

        # Filesystem / project_scoped: serve the bytes. REWORK
        # 2026-10-07 (M4): fd-based read with ``O_NOFOLLOW`` →
        # ``fstat`` → size-cap re-check → ``os.read`` (capped to
        # the fstat size). Closes the final-component symlink-
        # swap race AND the size-cap race that the prior
        # ``Path.read_bytes()`` shape exposed (the second open
        # could see a different file than the service's resolve
        # + stat + size-check saw).
        from daemon.services.live_views import (
            MAX_SERVED_BYTES,
            is_markdown_content_type,
            new_csp_nonce,
        )

        file_bytes = _fd_read(resolved.on_disk_path, MAX_SERVED_BYTES)
        if file_bytes is None:
            # Vanishingly rare (concurrent swap, concurrent
            # unlink, race that the kernel rejected at
            # ``O_NOFOLLOW`` open time, etc.) — uniform 404, no
            # stack trace leak.
            return _uniform_404()

        # Phase 2: content-aware rendering. ``.md`` files get
        # the HTML wrapper (renderer + sanitizer via pinned CDN,
        # CSP nonce, SRI integrity) instead of the raw
        # ``text/markdown`` blob (browsers display raw markdown
        # as plain text — useless for a /views URL a human is
        # supposed to click in chat). Other text types stay
        # native — ``text/plain`` and ``text/html`` are already
        # useful in a browser; image types keep their MIME.
        # Phase 3: ``.md`` files with ``mermaid`` fences get
        # the mermaid-aware variant (loads the vendored
        # asset + bootstraps ``mermaid.run``); files without
        # fences keep the CDN-marked wrapper byte-identical.
        if is_markdown_content_type(resolved.content_type):
            return _render_markdown_response(
                file_bytes, rel_path, nonce=new_csp_nonce()
            )

        return Response(
            content=file_bytes,
            media_type=resolved.content_type,
            status_code=200,
            headers={
                **_HARDENING_HEADERS,
                "Content-Length": str(len(file_bytes)),
            },
        )

    return router
