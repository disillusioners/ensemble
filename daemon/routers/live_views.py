"""Live-view HTTP route family (Phase 1 + Phase 2).

GET/HEAD ``/views/<root-name>/<relative-path>`` for any registered
root. The router is the **only** HTTP surface for the subsystem;
``view_link`` is the only agent-tool minting surface.

Order-of-registration is load-bearing: the router is mounted on
the daemon ``app`` (NOT on ``api_router``) at the ``/views`` prefix
so it is captured BEFORE the SPA catch-all at ``daemon/api.py:3111``
(Starlette first-match-wins). Mirrors the ``/livez`` and
``/readyz`` precedent (``daemon/api.py:3048``).

SECURITY MODEL (architect ruling, 2026-10-07):

* Read-only: GET/HEAD only. There are no POST / DELETE / LIST
  routes. The whole family is the public-by-obscurity file-serving
  shape (``conventions.md``).
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
    from daemon.services.live_views import LiveViewsService

logger = logging.getLogger(__name__)


# Per-response hardening headers — mirrors the tmp-images set
# (``daemon/routers/tmp_images.py:419-432``) so HTML / SVG / JS
# served from a registered root cannot be MIME-sniffed to an
# XSS vector by a browser.
_HARDENING_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=60",
}


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
        render_markdown_wrapper,
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


def _resolve_service(request: Request) -> "LiveViewsService | None":
    """Return the per-app ``LiveViewsService`` or None if not wired.

    The service is installed by the lifespan into
    ``app.state.live_views_service``. A request that arrives
    before the lifespan ran (probe-style early traffic) gets
    ``None`` here, which collapses to the uniform 404 — never
    a 500 / stack trace / "service unavailable" message.
    """
    return getattr(request.app.state, "live_views_service", None)


def build_router() -> APIRouter:
    """Build the ``/views`` router.

    The router does NOT capture a service at build time — it
    pulls the service from ``app.state`` at request time, so
    the lifespan can wire the real service (with the per-app
    ``TmpImageStore`` + project-workdir resolvers) AFTER
    ``create_app()`` returns and the service becomes available
    for the first request.

    The router accepts GET and HEAD only. There is no POST /
    DELETE / LIST surface; the read-only contract is enforced
    at the FastAPI layer (no @router.post declaration).
    """
    from daemon.services.live_views import (
        PathNotFoundError,
        RootNotFoundError,
        TraversalError,
    )

    router = APIRouter(prefix="/views", tags=["live_views"])

    async def _resolve(
        request: Request, root_name: str, rel_path: str
    ) -> Response:
        service = _resolve_service(request)
        if service is None or not service.enabled():
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
            new_csp_nonce,
            render_markdown_wrapper,
        )

        if is_markdown_content_type(resolved.content_type):
            try:
                file_bytes = _fd_read(resolved.on_disk_path, MAX_SERVED_BYTES)
                if file_bytes is None:
                    return _uniform_404()
                markdown_text = file_bytes.decode(
                    "utf-8", errors="replace"
                )
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
