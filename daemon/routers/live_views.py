"""Live-view HTTP route family (Phase 1).

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

The router does NOT do content conversion (no markdown→HTML, no
image resize). Phase 1 is the primitive; FE WebView + on-the-fly
conversion is Phase 2 (not in this slice).

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
        # Read in one shot when the file fits; bounded by
        # ``size`` so an intervening growth above the cap
        # never escapes.
        try:
            return os.read(fd, size)
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
        # the service. tmp_images: re-read the sidecar (the
        # service resolved the size; the head only needs type +
        # length).
        if resolved.root_type == "tmp_images":
            service = _resolve_service(request)
            from daemon.services.tmp_image_store import TmpImageNotFound
            try:
                _data, content_type, _sha = service._tmp_image_store.open_with_meta(
                    rel_path.strip()
                )
            except (TmpImageNotFound, Exception):
                return _uniform_404()
            return Response(
                status_code=200,
                headers={
                    **_HARDENING_HEADERS,
                    "Content-Type": content_type,
                    "Content-Length": str(resolved.size_bytes),
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
            service = _resolve_service(request)
            from daemon.services.tmp_image_store import TmpImageNotFound
            try:
                data, content_type, _sha = service._tmp_image_store.open_with_meta(
                    rel_path.strip()
                )
            except (TmpImageNotFound, Exception):
                return _uniform_404()
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
        from daemon.services.live_views import _MAX_SERVED_BYTES

        file_bytes = _fd_read(resolved.on_disk_path, _MAX_SERVED_BYTES)
        if file_bytes is None:
            # Vanishingly rare (concurrent swap, concurrent
            # unlink, race that the kernel rejected at
            # ``O_NOFOLLOW`` open time, etc.) — uniform 404, no
            # stack trace leak.
            return _uniform_404()
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
