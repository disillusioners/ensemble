"""Reverse proxy application for an unauthenticated local code-server."""

from __future__ import annotations

import asyncio
import gzip
import logging
import re
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.websockets import WebSocketState

from ..config import _resolve_vscode_webview_csp_fix
from ..services.vscode_server_manager import VSCodeServerManager
from ..services.workspace_guard import WorkspaceGuard

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 50 * 1024 * 1024

# W1: Controlled CSP policy — NOT strip-all. Replaces code-server's restrictive CSP
# with our own that allows what VS Code needs for iframe embedding.
# The virtual-host wildcard (``https://*.vscode-resource.vscode-cdn.net``)
# in script-src/style-src/img-src mirrors what fix-vscode-image-preview
# Step 1 appends to the webview HTML's meta-CSP — both policies
# INTERSECT per CSP3 §6.1.5.4, so the proxy MUST permit the wildcard
# here too or the meta-CSP rewrite alone is defeated. The suffix
# ``vscode-resource.vscode-cdn.net`` is owned by Microsoft/VS Code for
# this purpose; the wildcard is narrower than ``https:`` and broader
# than the per-request encoded host (which the browser rejects as an
# invalid source — see the meta-CSP rewrite rationale in
# ``_WEBVIEW_CSP_VIRTUAL_HOST``).
VSCODE_PROXY_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: "
    "https://*.vscode-resource.vscode-cdn.net; "
    "style-src 'self' 'unsafe-inline' https://*.vscode-resource.vscode-cdn.net; "
    "img-src 'self' data: blob: https://*.vscode-resource.vscode-cdn.net; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self' blob:;"
)

# fix-vscode-image-preview Step 1 — meta-CSP rewrite seam.
#
# Path fragment used to identify the webview HTML response (the iframe
# document that hosts extension content). The full URL is
# ``/vscode/stable-<hash>/static/out/vs/workbench/contrib/webview/
# browser/pre/index.html?id=<uuid>&parentId=...&extensionId=...``. We
# match on the path fragment because:
#   * ``extensionId=vscode.media-preview`` is query-string-scoped, not
#     path-scoped, and the workbench may load other webviews (welcome
#     tab, custom editors) with the same path;
#   * matching the canonical path component is version-tolerant (the
#     sha256-hash differs between 4.112.0 and 4.137.0; the file name
#     ``index.html`` does not);
#   * the path is the natural selector for "this is a webview doc,
#     not the workbench shell".
#
# ANCHORED on path-segment boundaries (``/.../webview/browser/pre/
# index.html``), not a bare bytes-substring — review-council follow-up
# 2. A request like ``/vscode/static/xwebview/browser/pre/index.html``
# must NOT match (the ``x`` is mid-segment). The split-then-join test
# below enforces this without depending on URL-parse semantics in
# :mod:`urllib.parse` (the path comes from httpx / FastAPI's raw
# routing already, and a mid-segment substring would slip through a
# naive ``in`` check).
_WEBVIEW_HTML_PATH_FRAGMENT = b"webview/browser/pre/index.html"


def _path_contains_webview_fragment(path_bytes: bytes) -> bool:
    """Return True iff ``/webview/browser/pre/index.html`` appears
    as a sequence of path segments in ``path_bytes``.

    Splits on ``/`` and tests membership of the joined 4-segment
    tuple ``(b"webview", b"browser", b"pre", b"index.html")`` —
    so a request like ``/vscode/xwebview/browser/pre/index.html`` (the
    ``x`` mid-segment) does NOT match, while the canonical
    ``/vscode/<hash>/static/out/vs/workbench/contrib/webview/browser/
    pre/index.html`` does.
    """
    if not path_bytes:
        return False
    target = _WEBVIEW_HTML_PATH_FRAGMENT.split(b"/")
    parts = [p for p in path_bytes.split(b"/") if p]
    n = len(parts)
    m = len(target)
    for i in range(n - m + 1):
        if parts[i : i + m] == target:
            return True
    return False


# CSP source expression appended to ``script-src`` and ``style-src``
# in the webview HTML's meta-CSP. The virtual host
# ``vscode-remote+<encoded-authority>.vscode-resource.vscode-cdn.net``
# encodes the daemon origin (host:port) per request — but CSP source
# expressions have a tight grammar: the literal ``+`` in the hostname
# makes the FULL origin an invalid source (the browser rejects the
# source and falls back to ``default-src 'none'``). We therefore
# match on the constant suffix with a leading-wildcard host pattern
# — the only form the browser accepts. A wildcard in the MIDDLE of
# the hostname (e.g. ``vscode-remote+*.vscode-resource.vscode-cdn.net``)
# is also rejected; only the LEFTMOST wildcard is valid per CSP3
# §6.7.2.4 (Host Source Wildcards). Empirically verified against
# headless Chromium: ``https://*.vscode-resource.vscode-cdn.net`` is
# accepted; the literal-encoded variants are rejected. The 4.137.0
# webview JS performs a runtime equivalent (``cspSource`` rewrite to
# a specific origin) — that works because the parent workbench
# registers the same origin pattern into the SW fetch handler first,
# so the CSP rejection is moot in direct mode. We can't rely on the
# SW here (the proxy serves from a different host family), so the
# suffix-wildcard is the only viable source.
_WEBVIEW_CSP_VIRTUAL_HOST = (
    "https://*.vscode-resource.vscode-cdn.net"
)

# CSP rewrite directives — review-council follow-up CRITICAL 1.
#
# The real meta-CSP captured at .agents/tester/RESULTS/
# 2026-09-12-vscode-image-preview-browser-capture.md:41-44 is
# ``default-src 'none'; script-src 'sha256-…' 'self'; frame-src 'self';
# style-src 'unsafe-inline';`` — NO ``img-src`` / NO ``media-src``.
# Per CSP3 §6.1.5.4, an absent directive falls back to
# ``default-src 'none'``; per §6.7.2.4, the HTTP header CSP and the
# meta-CSP INTERSECT (sources must appear in both). A bare APPEND
# cannot fix this — there is no ``img-src`` directive to append to.
# The fix is INSERT-WHEN-ABSENT semantics for ``img-src`` and
# ``media-src``: when the directive is absent from the meta-CSP, we
# add it; when present, we extend it with the virtual-host wildcard
# (same as the script-src / style-src append).
#
# ``img-src``: image bytes for media-preview (and any other extension
# that constructs an ``<img src="virtual-host">``). Includes ``data:``
# (for the inline-data URI path the extension sometimes uses) and
# ``blob:`` (for the blob-URL workaround the JS layer may use after
# a ``fetch()`` to virtual-host).
#
# ``media-src``: audio + video siblings (``<audio>`` / ``<video>``).
# The 4.137.0 ``vscode.audioPreview`` / ``vscode.videoPreview`` custom
# editors use the same ``asWebviewUri`` flow as the image editor;
# without this directive they hit the same ``default-src 'none'``
# fall-back.
_WEBVIEW_CSP_INSERTIONS: dict[str, str] = {
    # When ``img-src`` is absent in the meta-CSP, insert this value.
    # Includes ``data:`` / ``blob:`` so the extension's two known
    # bypass paths (inline data URI + blob URL after fetch()) keep
    # working; and the virtual-host wildcard so direct
    # ``<img src="https://*.vscode-resource.vscode-cdn.net/...">``
    # is also permitted.
    "img-src": (
        "data: blob: https://*.vscode-resource.vscode-cdn.net"
    ),
    # When ``media-src`` is absent, insert this value (audio/video).
    "media-src": (
        "'self' https://*.vscode-resource.vscode-cdn.net"
    ),
}

# Directives that should be EXTENDED (appended to) rather than
# INSERTED. Each entry maps the directive name to the value to
# APPEND if the origin isn't already present. ``script-src`` and
# ``style-src`` get the virtual-host wildcard appended; the
# ``img-src`` / ``media-src`` insertions are handled separately
# (they go in the ``_WEBVIEW_CSP_INSERTIONS`` table above).
_WEBVIEW_CSP_APPENDS: dict[str, str] = {
    "script-src": _WEBVIEW_CSP_VIRTUAL_HOST,
    "style-src": _WEBVIEW_CSP_VIRTUAL_HOST,
}

# All directives this seam ever touches — used as a sanity set in
# the rewrite helper (catches typos like ``"image-src"``).
_ALL_WEBVIEW_CSP_DIRECTIVES: frozenset[str] = frozenset(
    set(_WEBVIEW_CSP_INSERTIONS) | set(_WEBVIEW_CSP_APPENDS)
)

# Maximum buffered body size for the meta-CSP rewrite seam.
# Webview HTML is small (< 10 KiB based on the 2026-09-12 capture
# evidence); the cap is generous to accommodate future code-server
# growth but bounded to prevent an oversized upstream from forcing
# the proxy into a memory blow-up. 1 MiB is ~100× the observed
# size. TWO distinct paths share this cap:
#
# (a) ``Content-Length`` advertised > cap — common case. We
#     detect this BEFORE consuming (no memory cost) and stream
#     the upstream through verbatim, never touching ``aiter_raw``.
#     Pure streaming passthrough; the browser sees the upstream
#     body, the rewrite seam is skipped (a > 1 MiB webview doc
#     is not the failure mode we care about anyway).
#
# (b) ``Content-Length`` absent or chunked transfer-encoding —
#     we cannot pre-decide, so we consume chunk-by-chunk. If the
#     running total exceeds the cap, we STOP appending to the
#     rewrite buffer but CONTINUE draining into a separate
#     raw-re-serve accumulator. The accumulator ends up holding
#     the FULL original body (prefix + remainder); we raw-re-serve
#     every byte with the original ``Content-Encoding`` preserved
#     so the wire is byte-faithful. Transient memory for a
#     pathological oversize doc is acceptable — truncation is not
#     (a truncated webview doc is a broken page, worse than the
#     skip the cap replaces).
_WEBVIEW_REWRITE_MAX_BODY_BYTES: int = 1 * 1024 * 1024

# Per-process one-shot log: at most one warning per worker per
# undecodable upstream encoding, so a flood of brotli responses does
# not spam the log.
_BROTLI_DECODE_MISSING_LOGGED = False

# Per-process one-shot log: at most one warning per worker per
# oversized body (mid-stream path only — the Content-Length-gate
# path is silent because no memory was spent), so a flood of
# over-cap responses does not spam the log.
_OVERSIZE_BODY_LOGGED = False

# Match a ``<meta http-equiv="Content-Security-Policy" content="...">``
# tag. The value (``[^"]*``) tolerates embedded newlines and quotes
# inside the attribute value as long as they are not literal ``"``
# characters (CSP directives do not embed ``"``).
_META_CSP_RE = re.compile(
    rb'(<meta\s+http-equiv="Content-Security-Policy"\s+content=")([^"]*)(")',
    re.IGNORECASE,
)

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def _proxy_headers(headers: Mapping[str, str], port: int) -> dict[str, str]:
    """Filter request hop-by-hop headers and set the local upstream authority."""
    result = {
        name: value
        for name, value in headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS and name.lower() not in {"host", "origin"}
    }
    result["Host"] = f"127.0.0.1:{port}"
    result["Origin"] = f"http://127.0.0.1:{port}"
    return result


# Outbound ``Accept-Encoding`` for rewrite-eligible requests —
# review-council follow-up ride-along 3. If a future browser /
# client negotiates a non-decodeable encoding (e.g. ``zstd``) the
# rewrite seam would silently skip — the proxy cannot decode the
# upstream response, the consume point drains nothing useful, and
# the meta-CSP rewrite is skipped. Pinning the outbound header to
# the decodable set guarantees that any response we receive IS
# decodeable; non-eligible requests keep the client's value
# unchanged (so e.g. zstd-encoded JS chunks for the workbench
# shell still flow through).
_WEBVIEW_REWRITE_DECODABLE_ENCODINGS: tuple[str, ...] = (
    "gzip", "deflate", "br",
)
_WEBVIEW_REWRITE_ACCEPT_ENCODING: str = "gzip, deflate, br"


def _accept_encoding_for_request(
    path_bytes: bytes, client_accept_encoding: str | None
) -> str | None:
    """Return the outbound ``Accept-Encoding`` for ``path_bytes``.

    For rewrite-eligible paths (those whose URL contains the
    webview index fragment), pin the header to the decodable set
    so the upstream can't accidentally reply with an encoding we
    can't decode (e.g. ``zstd``); for non-eligible paths return
    the client's value unchanged so non-webview traffic flows
    through with whatever the client negotiated.
    """
    if _path_contains_webview_fragment(path_bytes):
        return _WEBVIEW_REWRITE_ACCEPT_ENCODING
    return client_accept_encoding


def _response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Filter response hop-by-hop headers and replace framing-related policies."""
    result = {
        name: value
        for name, value in headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
        and name.lower() not in {"content-security-policy", "x-content-security-policy", "x-frame-options"}
    }
    result["Content-Security-Policy"] = VSCODE_PROXY_CSP
    result["X-Content-Security-Policy"] = VSCODE_PROXY_CSP
    result["X-Frame-Options"] = "SAMEORIGIN"
    return result


def _decode_response_body(
    body: bytes, content_encoding: str | None
) -> bytes | None:
    """Decode a response body using its ``Content-Encoding``.

    Returns the decoded bytes, or ``None`` if the encoding is not
    decodable in this environment (e.g. ``br`` (brotli) is not a
    stdlib codec and the optional ``brotli`` / ``brotlicffi`` package
    is not installed). Recognised: ``identity``, ``gzip`` (stdlib),
    ``deflate`` (stdlib zlib), ``br`` (optional brotli). Any unknown
    encoding also returns ``None`` so the caller can fall back to
    pass-through streaming.

    NOTE: malformed/truncated compressed bytes still raise
    (OSError / zlib.error / brotli.error). The caller
    (:func:`_buffer_and_maybe_rewrite_webview`) catches those and
    falls back to byte-faithful re-serve of the original raw bytes
    — a 500 is never the right answer for a downstream bug.
    """
    if not content_encoding:
        return body
    enc = content_encoding.strip().lower()
    if enc in ("identity", ""):
        return body
    if enc == "gzip":
        return gzip.decompress(body)
    if enc == "deflate":
        import zlib

        return zlib.decompress(body)
    if enc == "br":
        # brotli is a declared runtime dep (pyproject.toml) so this
        # import should always succeed. We still guard to keep the
        # kill-switch path safe if a future dep prune removes it.
        global _BROTLI_DECODE_MISSING_LOGGED
        try:
            import brotli  # type: ignore[import-not-found]
        except ImportError:
            if not _BROTLI_DECODE_MISSING_LOGGED:
                _BROTLI_DECODE_MISSING_LOGGED = True
                logger.warning(
                    "VSCode proxy: skipping webview meta-CSP rewrite for "
                    "brotli-compressed response — 'brotli' package not "
                    "installed. Add `brotli>=1.0` to "
                    "[project].dependencies in pyproject.toml."
                )
            return None
        return brotli.decompress(body)
    # Unknown encoding — pass through to streaming unchanged.
    return None


# Recognised Content-Encoding values that this proxy can decode in
# the meta-CSP rewrite path. Anything outside this set (including the
# empty ``identity`` string and ``identity`` itself) is either a
# no-op decode or an explicit skip — callers must check this BEFORE
# consuming the upstream body to avoid
# ``httpx.Response.aiter_raw`` raising ``StreamConsumed`` on the
# second iteration (the streaming fall-through path).
_DECODEABLE_ENCODINGS: frozenset[str] = frozenset(
    {"", "identity", "gzip", "deflate", "br"}
)


def _can_decode_encoding(encoding: str | None) -> bool:
    """Return True iff the encoding is in the recognised decode set.

    Mirrors :func:`_decode_response_body`'s recognised values. For
    ``br``, additionally requires the ``brotli`` package to be
    importable (declared runtime dep, but kept guard so the
    kill-switch path stays safe if a future dep prune removes it).
    """
    if encoding is None:
        return True
    enc = encoding.strip().lower()
    if enc not in _DECODEABLE_ENCODINGS:
        return False
    if enc == "br":
        try:
            import brotli  # noqa: F401  type: ignore[import-not-found]
        except ImportError:
            return False
    return True


def _raw_byte_faithful_response(
    body: bytes, upstream: httpx.Response
) -> Response:
    """Re-serve the original raw bytes with proxy-default headers.

    Used after we consume the upstream body but decide NOT to rewrite
    (either no meta-CSP to augment, or decode failed on a malformed
    payload). Preserves the original ``Content-Encoding`` so the
    browser's decoder stays in sync with the wire bytes — this is the
    only path that keeps the upstream's pre-encoded bytes intact end
    to end. Recomputes ``Content-Length`` so the new header agrees
    with the actual body length (which equals the buffered body
    length).
    """
    out_headers = _response_headers(upstream.headers)
    out_headers["Content-Length"] = str(len(body))
    return Response(
        content=body,
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=None,
    )


def _augment_csp_directives(
    csp_value: str,
    *,
    insert_when_absent: Mapping[str, str] | None = None,
    append_when_present: Mapping[str, str] | None = None,
) -> str:
    """Augment a meta-CSP with the directives this seam needs.

    Two complementary operations, both required for the fix to
    satisfy CSP3 §6.1.5.4 (absent directive falls back to
    ``default-src 'none'``) and §6.7.2.4 (HTTP header CSP and
    meta-CSP INTERSECT):

    1. **insert_when_absent** — for each ``(directive, value)`` pair,
       if the meta-CSP does NOT contain that directive, append it.
       The real meta-CSP captured in the 2026-09-12 evidence has
       NO ``img-src`` and NO ``media-src``; without insertion, the
       browser falls back to ``default-src 'none'`` and the image
       stays blocked even after the HTTP header CSP is widened
       (header + meta policies INTERSECT — sources must appear in
       both).

    2. **append_when_present** — for each ``(directive, source)``
       pair, if the meta-CSP DOES contain that directive, append
       the source to it (idempotent — already-present sources are
       no-ops). Used to widen the existing ``script-src`` /
       ``style-src`` to include the virtual-host wildcard.

    Tolerant of multi-line values (VS Code splits the meta-CSP
    across lines for readability). Idempotent on repeated calls:
    insertion only fires when the directive is missing, and the
    append is a no-op when the source is already present.

    Both ``insert_when_absent`` and ``append_when_present`` are
    optional (``None`` means "skip this operation"). Callers pass
    :data:`_WEBVIEW_CSP_INSERTIONS` for (1) and
    :data:`_WEBVIEW_CSP_APPENDS` for (2) — see :func:`_rewrite_webview_meta_csp`.
    """
    insert_when_absent = insert_when_absent or {}
    append_when_present = append_when_present or {}

    # No-op fast path — if there's nothing to insert and the
    # append sources are already everywhere they should be, skip
    # the parse. (The append idempotency check is done per
    # directive below; this just skips when there's nothing to do
    # at all.)
    if not insert_when_absent and not append_when_present:
        return csp_value

    pieces = csp_value.split(";")
    out: list[str] = []
    seen_directives: set[str] = set()
    for piece in pieces:
        stripped = piece.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        directive_name = parts[0].lower()
        sources = parts[1].strip() if len(parts) > 1 else ""
        seen_directives.add(directive_name)

        # append_when_present: extend this directive's source list
        # with the configured source (if not already present).
        append_source = append_when_present.get(directive_name)
        if append_source and append_source not in sources:
            if sources:
                sources = f"{sources} {append_source}"
            else:
                sources = append_source
        out.append(
            f"{directive_name} {sources}".strip()
            if sources
            else directive_name
        )

    # insert_when_absent: any directive that was not present in the
    # meta-CSP at all (and is in our config) is appended at the end.
    for directive, value in insert_when_absent.items():
        if directive.lower() not in seen_directives:
            out.append(f"{directive} {value}")

    return "; ".join(out)


def _rewrite_webview_meta_csp(body: bytes) -> tuple[bytes, bool]:
    """Pattern-rewrite the webview HTML's meta-CSP to permit the
    virtual-host origin AND to insert ``img-src`` / ``media-src``
    when absent.

    Two complementary operations (review-council follow-up
    CRITICAL 1):

    * **Insert** ``img-src`` and ``media-src`` when absent — the
      real meta-CSP captured at .agents/tester/RESULTS/2026-09-12-
      vscode-image-preview-browser-capture.md:41-44 has neither
      directive, so without INSERTION the browser falls back to
      ``default-src 'none'`` and the image stays blocked even
      after the HTTP header CSP is widened (CSP3 §6.7.2.4 INTERSECT
      rule).

    * **Extend** ``script-src`` / ``style-src`` to include the
      virtual-host wildcard (the same constant as the HTTP header
      CSP). Without this extension the extension's JS / CSS get
      blocked; the ``img-src`` / ``media-src`` insertions above
      cannot rescue them.

    The strict meta-CSP (``default-src 'none'; script-src
    'sha256-…' 'self'; frame-src 'self'; style-src 'unsafe-inline';``)
    bundled with the webview document blocks extension resources
    fetched from the
    ``https://vscode-remote+<encoded>.vscode-resource.vscode-cdn.net``
    virtual host — most visibly, media-preview's
    ``imagePreview.css`` / ``.js`` and the image bytes themselves.
    We append the EXACT (per-request computed) virtual-host origin
    to ``script-src`` / ``style-src`` so those assets are permitted
    to load through the proxy (the SW intercepts the virtual-host
    requests and serves them via the proven-good
    ``vscode-remote-resource?path=&tkn=`` shape).

    We can't use a wildcard with the literal virtual-host hostname
    — CSP spec only allows wildcards at the LEFTMOST position of
    the hostname, but the literal ``+`` separator in
    ``vscode-remote+...`` blocks that pattern. The wildcarded
    suffix (``https://*.vscode-resource.vscode-cdn.net``) is the
    only viable source — empirically verified against headless
    Chromium. See :data:`_WEBVIEW_CSP_VIRTUAL_HOST` for the full
    rationale.

    Returns ``(rewritten_body, True)`` if at least one meta-CSP tag
    was augmented, or ``(body, False)`` if no meta-CSP tag was
    found (or the rewrite was already applied — idempotent).
    Pattern-only; the ``sha256-…`` hash differs between
    code-server versions (4.112.0 vs 4.137.0) and is not
    hard-coded.
    """
    matches = list(_META_CSP_RE.finditer(body))
    if not matches:
        return body, False

    rewritten = bytearray(body)
    any_rewritten = False
    # Iterate in reverse so offsets stay valid as we splice.
    for m in reversed(matches):
        head, csp_raw, tail = m.group(1), m.group(2), m.group(3)
        try:
            csp_value = csp_raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        new_value = _augment_csp_directives(
            csp_value,
            insert_when_absent=_WEBVIEW_CSP_INSERTIONS,
            append_when_present=_WEBVIEW_CSP_APPENDS,
        )
        if new_value == csp_value:
            continue
        any_rewritten = True
        new_csp_raw = new_value.encode("utf-8")
        new_tag = head + new_csp_raw + tail
        # Splice: replace the entire match span (group 1..3) with the
        # rebuilt tag. Reverse-iteration keeps earlier offsets valid.
        rewritten[m.start():m.end()] = new_tag

    if not any_rewritten:
        return body, False
    return bytes(rewritten), True


def _validate_folder_param(
    query_string: str,
    project_repo,
) -> str:
    """Validate the ``?folder=`` query parameter against known project directories.

    C1: Prevents arbitrary filesystem access via ``?folder=/etc`` etc. by
    confining the folder to a known project's main_directory using
    :meth:`WorkspaceGuard.resolve_strict`.

    - If no ``folder`` param is present, returns the query string unchanged.
    - If ``folder`` is present but ``project_repo`` is ``None``, drops the
      ``folder`` param entirely (fail-closed: no validation possible →
      don't forward user-supplied paths).
    - If ``folder`` is present and matches a known project workdir, returns
      the query string with the resolved (canonicalized) folder value.
    - If ``folder`` is present but matches no known project, raises
      :class:`HTTPException` with status 403.
    """
    if not query_string:
        return query_string

    params = parse_qs(query_string, keep_blank_values=True)
    folder = params.get("folder", [None])[0]
    if not folder:
        # No folder param, pass through unchanged
        return query_string

    if project_repo is None:
        # Fail-closed: can't validate — drop the folder param entirely
        logger.warning(
            "C1: dropping ?folder= because project_repo is unavailable"
        )
        params.pop("folder", None)
        return urlencode(params, doseq=True)

    # Validate the folder against any project's main_directory
    try:
        projects = project_repo.list_projects()
    except Exception as exc:
        logger.warning("C1: folder validation DB read failed: %s", exc)
        params.pop("folder", None)
        return urlencode(params, doseq=True)

    for project in projects:
        main_directory = getattr(project, "main_directory", None)
        if not main_directory:
            continue
        try:
            guard = WorkspaceGuard(main_directory)
        except (ValueError, OSError) as exc:
            # main_directory doesn't exist or is invalid — skip
            logger.debug(
                "WorkspaceGuard init skipped for %s: %s", main_directory, exc
            )
            continue
        resolved, error = guard.resolve_strict(folder)
        if error is None and resolved is not None:
            # Folder is within this project's workdir — valid
            params["folder"] = [str(resolved)]
            return urlencode(params, doseq=True)

    # Folder doesn't match any project — reject
    raise HTTPException(
        status_code=403,
        detail={
            "error": "Invalid folder parameter",
            "detail": "The folder path is not within any known project directory",
        },
    )


async def _buffer_and_maybe_rewrite_webview(
    upstream: httpx.Response,
    request: Request,
) -> Response | None:
    """Buffer the upstream response, attempt the meta-CSP rewrite.

    Returns ``None`` if the response is not eligible (kill-switch OFF,
    not a webview HTML, body unreadable, encoding not decode-able) —
    the caller falls through to the streaming pass-through WITHOUT
    having consumed the upstream body. Returns a :class:`Response`
    with the rewritten bytes when the meta-CSP was successfully
    augmented; the response headers carry the new ``Content-Length``
    and have ``ETag`` / ``Cache-Control`` / original
    ``Content-Encoding`` stripped so the browser cannot pin a stale
    variant of the rewritten doc.

    The ``request`` argument supplies the browser-facing
    host[:port] used to compute the per-request virtual-host origin
    the meta-CSP must permit. The daemon port the browser sees
    determines the encoded host (e.g. ``localhost:8079`` →
    ``localhost-003a8079``); a different port would yield a
    different encoded host that the browser cannot match.

    Stream-consumption discipline: every pre-check that returns
    ``None`` MUST run BEFORE :meth:`httpx.Response.aiter_raw` is
    called. Real httpx raises :class:`httpx.StreamConsumed` on the
    second iteration of a streamed body — verified by inspection of
    ``httpx/Response.aiter_raw`` (sets ``self.is_stream_consumed =
    True`` on first call) — so a fall-through that re-iterates
    after a pre-check consumed the body would 500. We pin this with
    a unit test that uses a real :class:`httpx.Response` with a
    generator-backed body.
    """
    # ── Pre-consume gates (each must return None without consuming
    # the upstream body). ──
    if not _resolve_vscode_webview_csp_fix():
        return None

    # Path gate — review-council follow-up ride-along 2.
    # ANCHORED on path-segment boundaries (see
    # :func:`_path_contains_webview_fragment`), not a bare
    # bytes-substring. ``/vscode/xwebview/browser/pre/index.html``
    # does NOT match — the ``x`` is mid-segment.
    if not _path_contains_webview_fragment(
        upstream.request.url.path.encode()
    ):
        return None

    # Content-Type guard — only text/html gets the rewrite. ``startswith``
    # covers ``text/html; charset=utf-8`` etc.
    content_type = upstream.headers.get("content-type", "")
    if not content_type.lower().startswith("text/html"):
        return None

    # Encoding pre-check — decide BEFORE consuming. If the encoding
    # is unknown OR the package is missing for a recognised
    # encoding (e.g. brotli without the brotli package), return None
    # so the caller streams through the original bytes without ever
    # touching ``upstream.aiter_raw``.
    encoding = upstream.headers.get("content-encoding", "").strip().lower()
    if not _can_decode_encoding(encoding):
        return None

    # ── Consume point — past here we own the body and cannot fall
    # through to a streaming path that re-calls ``aiter_raw``. All
    # post-consume branches must return a concrete Response. ──
    # Buffer the body. Webview HTML is small (< 10 KiB based on the
    # evidence capture); the cap (review-council follow-up
    # ride-along 1) is generous to accommodate future code-server
    # growth but bounded to prevent an oversized upstream from
    # forcing the proxy into a memory blow-up. 1 MiB is ~100× the
    # observed size; the byte-faithful re-serve on oversize is
    # invisible to the browser (no rewrite, original
    # Content-Encoding preserved).
    # Buffer the body, but with TWO distinct size-cap paths
    # (council corrective round on 92784e20):
    #
    # (a) ``Content-Length`` advertised > cap — pre-consume gate.
    #     We refuse to consume at all and return ``None`` so the
    #     caller streams the original bytes through verbatim. Zero
    #     memory cost, no truncation possible (the upstream
    #     connection itself is what carries the body).
    #
    # (b) ``Content-Length`` absent or chunked — we consume
    #     chunk-by-chunk. The prefix up to the cap goes to the
    #     rewrite buffer; everything after the cap (and the cap's
    #     final chunk) goes to a SEPARATE raw-re-serve accumulator.
    #     Both buffers concatenated hold the FULL original body;
    #     we raw-re-serve every byte with the original
    #     ``Content-Encoding`` preserved. Truncation would be a
    #     broken page — worse than the skip the cap replaces.
    advertised_length = upstream.headers.get("content-length")
    if advertised_length is not None:
        try:
            advertised_length_int = int(advertised_length)
        except (TypeError, ValueError):
            advertised_length_int = None
        if (
            advertised_length_int is not None
            and advertised_length_int > _WEBVIEW_REWRITE_MAX_BODY_BYTES
        ):
            # Common case: advertised-too-large. Do NOT consume.
            # Pure streaming passthrough — the caller will iterate
            # ``aiter_raw()`` once, see the full body, and the
            # rewrite seam is skipped.
            return None

    body_chunks: list[bytes] = []
    oversize_chunks: list[bytes] = []
    oversize = False
    async for chunk in upstream.aiter_raw():
        if not oversize:
            # Still under the cap — accumulate to the rewrite
            # buffer. ``total`` is the running prefix size; the
            # moment it crosses the cap, this branch becomes
            # False for the rest of the iteration.
            total = sum(len(c) for c in body_chunks) + len(chunk)
            if total > _WEBVIEW_REWRITE_MAX_BODY_BYTES:
                oversize = True
                # The current chunk straddles the cap boundary;
                # it belongs to the raw-re-serve accumulator
                # (not the rewrite buffer — the rewrite buffer
                # would have to be discarded anyway).
                oversize_chunks.append(chunk)
                # One-shot log for the mid-stream oversize path
                # (the Content-Length-gate path is silent — no
                # memory was spent there).
                global _OVERSIZE_BODY_LOGGED
                if not _OVERSIZE_BODY_LOGGED:
                    _OVERSIZE_BODY_LOGGED = True
                    logger.warning(
                        "VSCode proxy: webview meta-CSP rewrite skipped "
                        "— mid-stream body exceeded %d-byte cap; "
                        "byte-faithful raw re-serve of FULL body",
                        _WEBVIEW_REWRITE_MAX_BODY_BYTES,
                    )
            else:
                body_chunks.append(chunk)
        else:
            # Already over the cap — every subsequent chunk goes
            # to the raw-re-serve accumulator so the eventual
            # re-serve is byte-faithful to the wire (prefix +
            # remainder = FULL original bytes).
            oversize_chunks.append(chunk)

    if oversize:
        # Mid-stream path — we consumed past the cap, but kept
        # accumulating into the raw-re-serve accumulator so the
        # wire is byte-faithful. The rewrite buffer's prefix is
        # discarded; the FULL upstream body goes to the browser
        # via the raw re-serve.
        full_body = b"".join(oversize_chunks)
        return _raw_byte_faithful_response(full_body, upstream)
    raw_body = b"".join(body_chunks)

    # Try to decode. Malformed/truncated compressed bytes raise:
    # ``EOFError`` (gzip — truncated header / missing stream
    # terminator), ``zlib.error`` (deflate), ``brotli.error``
    # (``br``), or any other ``Exception`` from a third-party
    # decoder. We catch the broad ``Exception`` class because the
    # body is a small, well-known artifact (< 10 KiB); anything
    # thrown from a decode call here is a downstream bug, not a
    # caller-side error, and the right answer is never a 500. The
    # catch is documented + log-warned for forensic value.
    try:
        decoded = _decode_response_body(raw_body, encoding or None)
    except Exception as exc:  # noqa: BLE001 — see docstring above.
        logger.warning(
            "VSCode proxy: webview meta-CSP rewrite skipped — "
            "decoding %s body failed (%s: %s); byte-faithful re-serve",
            encoding or "identity", type(exc).__name__, exc,
        )
        return _raw_byte_faithful_response(raw_body, upstream)

    if decoded is None:
        # _can_decode_encoding said yes but _decode_response_body
        # said no — should not happen, but defend anyway. Re-serve
        # raw bytes.
        return _raw_byte_faithful_response(raw_body, upstream)

    rewritten, was_rewritten = _rewrite_webview_meta_csp(decoded)
    if not was_rewritten:
        # No meta-CSP to augment (or already augmented, or the doc
        # has no meta-CSP tag at all). We must NOT fall through to
        # streaming — we already consumed the upstream body and
        # would 500 with ``StreamConsumed`` on the re-iteration.
        # Instead, re-serve the original encoded bytes with the
        # original Content-Encoding (byte-faithful to the wire).
        return _raw_byte_faithful_response(raw_body, upstream)

    # Build the rewritten response. Headers: keep hop-by-hop filtering
    # + framing-policy replacement semantics from
    # ``_response_headers`` (so X-Frame-Options / CSP header still
    # apply), then override the freshness metadata so the browser
    # cannot pin a stale variant.
    out_headers = _response_headers(upstream.headers)
    out_headers.pop("etag", None)
    out_headers.pop("if-none-match", None)
    out_headers["Cache-Control"] = "no-store"
    out_headers["Content-Length"] = str(len(rewritten))
    # The rewritten body is plain text/html — strip the original
    # compression so the new bytes match the recomputed length and
    # the browser decodes them as-is. Recompressing is not worth the
    # cost for a small doc; the proxy is internal.
    out_headers.pop("content-encoding", None)
    return Response(
        content=rewritten,
        status_code=upstream.status_code,
        headers=out_headers,
        media_type="text/html",
    )



async def upstream_to_browser(websocket: WebSocket, upstream: Any) -> None:
    """Forward messages from ``upstream`` (code-server) to ``websocket`` (browser).

    Defense-in-depth against a race: when the browser disconnects while
    code-server is still streaming messages, sending on a closed WS raises
    ``RuntimeError: Unexpected ASGI message 'websocket.send', after sending
    'websocket.close'``. Without a guard this propagates out of the
    TaskGroup and the proxy crashes with an unhandled exception.

    Three layers of defense:

    1. **Pre-send state check** (Fix 3): best-effort — if the browser WS
       is no longer in ``CONNECTED`` state, stop before issuing a write.
    2. **try/except around send** (Fix 1): catches the ``RuntimeError``
       that Starlette raises when the WS is already closed, breaking
       the loop cleanly.
    3. The enclosing ``except*`` (Fix 2) also catches ``RuntimeError`` as
       a final backstop in case either (1) or (2) misses.
    """
    async for message in upstream:
        # (Fix 3) Skip writes if the browser has already disconnected.
        # WebSocketState is imported at module top — if starlette ever
        # stops exporting it, we still fall through to layer 2 below.
        if websocket.client_state != WebSocketState.CONNECTED:
            break
        try:
            # (Fix 1) Wrap the actual write so a stale WS doesn't crash
            # the TaskGroup. We catch RuntimeError specifically and
            # break out — propagating up would crash the proxy.
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(message)
        except RuntimeError:
            # Browser WS closed mid-stream; stop forwarding.
            break


def create_vscode_proxy_app(
    manager: VSCodeServerManager,
    project_repo=None,
) -> FastAPI:
    """Create an unmounted HTTP and WebSocket proxy for code-server.

    Args:
        manager: VS Code server lifecycle manager.
        project_repo: Optional project repository used to validate the
            ``?folder=`` query parameter (C1 security fix). When ``None``,
            the folder param is dropped (fail-closed).

    Returns:
        An independent FastAPI sub-application.
    """
    app = FastAPI(title="VS Code proxy")

    def readiness() -> JSONResponse | None:
        if not manager.is_running():
            return JSONResponse(
                {"detail": "VS Code server is not ready"},
                status_code=503,
                headers={"Retry-After": "1"},
            )
        return None

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def proxy_http(request: Request, path: str):
        """Proxy an HTTP request with a bounded streaming request body."""
        unavailable = readiness()
        if unavailable is not None:
            return unavailable
        port = manager.get_port()
        if port is None:
            return JSONResponse(
                {"detail": "VS Code server is not ready"},
                status_code=503,
                headers={"Retry-After": "1"},
            )

        chunks: list[bytes] = []
        body_size = 0
        async for chunk in request.stream():
            body_size += len(chunk)
            if body_size > MAX_BODY_BYTES:
                return JSONResponse({"detail": "Request body too large"}, status_code=413)
            chunks.append(chunk)

        client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")
        upstream: httpx.Response | None = None
        try:
            target = "/" + path
            if request.url.query:
                # C1: Validate ?folder= before forwarding to code-server
                validated_query = _validate_folder_param(
                    request.url.query, project_repo
                )
                if validated_query:
                    target += f"?{validated_query}"
            # fix-vscode-image-preview ride-along 3 — for
            # rewrite-eligible requests, pin the outbound
            # ``Accept-Encoding`` to the decodable set so the
            # upstream can't reply with an encoding we can't
            # decode (e.g. ``zstd``). Non-eligible requests keep
            # the client's value so non-webview traffic flows
            # through with whatever the client negotiated.
            client_path = ("/" + path).encode()
            upstream_headers = _proxy_headers(request.headers, port)
            pinned_ae = _accept_encoding_for_request(
                client_path,
                upstream_headers.pop("Accept-Encoding", None),
            )
            if pinned_ae is not None:
                upstream_headers["Accept-Encoding"] = pinned_ae
            upstream = await client.send(
                client.build_request(
                    request.method,
                    target,
                    headers=upstream_headers,
                    content=b"".join(chunks),
                ),
                stream=True,
            )

            # fix-vscode-image-preview Step 1 — meta-CSP rewrite
            # seam. The webview HTML response carries a strict
            # meta-CSP (``default-src 'none'; script-src 'sha256-…'
            # 'self'; frame-src 'self'; style-src 'unsafe-inline';``)
            # that blocks extension resources fetched from the
            # ``vscode-remote+<port>.vscode-resource.vscode-cdn.net``
            # virtual host — most visibly, media-preview's
            # ``imagePreview.css``/``.js`` and the image bytes
            # themselves. When the kill-switch is ON AND the response
            # is a webview HTML, buffer + decode + augment the
            # meta-CSP and return the rewritten doc; otherwise fall
            # through to byte-faithful streaming.
            rewritten = await _buffer_and_maybe_rewrite_webview(upstream, request)
            if rewritten is not None:
                await upstream.aclose()
                await client.aclose()
                return rewritten

            async def content() -> AsyncIterator[bytes]:
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                finally:
                    await upstream.aclose()
                    await client.aclose()

            return StreamingResponse(
                content(),
                status_code=upstream.status_code,
                headers=_response_headers(upstream.headers),
                media_type=None,
            )
        except httpx.RequestError as exc:
            # Catch upstream connection failures (RemoteProtocolError,
            # ConnectError, etc.) and return a clean 503 instead of
            # letting the exception bubble up as an ugly HTTP 500.
            # RequestError is the base class for connection-level errors;
            # HTTPStatusError (4xx/5xx from upstream) is deliberately NOT
            # caught here so it is not masked.
            if upstream is not None:
                await upstream.aclose()
            await client.aclose()
            logger.warning(
                "VSCode proxy upstream error: %s: %s",
                type(exc).__name__,
                exc,
            )
            return JSONResponse(
                status_code=503,
                content={"detail": "VS Code server unavailable. It may be restarting."},
                headers={"Retry-After": "5"},
            )

    @app.websocket("/{path:path}")
    async def proxy_websocket(websocket: WebSocket, path: str) -> None:
        """Bridge text and binary WebSocket messages in both directions."""
        unavailable = readiness()
        if unavailable is not None:
            await websocket.close(code=1013, reason="VS Code server is not ready")
            return
        port = manager.get_port()
        if port is None:
            await websocket.close(code=1013, reason="VS Code server is not ready")
            return

        import websockets
        from websockets.typing import Subprotocol

        offered = [
            value.strip()
            for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
            if value.strip()
        ]
        selected = offered[0] if offered else None
        await websocket.accept(subprotocol=selected)
        upstream: Any = None
        try:
            upstream = await websockets.connect(
                f"ws://127.0.0.1:{port}/{path}",
                subprotocols=cast(list[Subprotocol] | None, offered or None),
                close_timeout=2,
                ping_interval=20,
                ping_timeout=20,
                additional_headers={"Host": f"127.0.0.1:{port}"},
            )

            async def browser_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async with asyncio.TaskGroup() as group:
                group.create_task(browser_to_upstream())
                group.create_task(upstream_to_browser(websocket, upstream))
        # (Fix 2) Add RuntimeError so the outer handler swallows the
        # ASGI-after-close crash even if Fixes 1/3 miss an edge case.
        except* (WebSocketDisconnect, ConnectionError, asyncio.CancelledError, RuntimeError) as eg:
            # RuntimeError is expected when sending after the browser WS closes.
            # Log at debug for observability — non-send RuntimeErrors are rare but
            # would be invisible without this.
            for exc in eg.exceptions:
                if isinstance(exc, RuntimeError):
                    logger.debug(f"WebSocket proxy RuntimeError swallowed: {exc}")
        finally:
            if upstream is not None:
                await upstream.close()
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close()

    return app
