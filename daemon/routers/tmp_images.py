"""TEMP-image upload + serving endpoints.

PUBLIC-BY-OBSCURITY — daemon has no auth layer; ids are identifiers,
not secrets. If any daemon endpoint gains auth, this MUST be the first
file-serving route to gain it. Do not treat this as precedent for
auth-free serving of sensitive content.

----------------------------------------------------------------------
FROZEN API CONTRACT (architect §3 ratification)
----------------------------------------------------------------------

POST /api/tmp_images
    Request:  { "images": [{ "filename", "content_type", "data_base64" }, ...] }
              ≤3 images per request; ≤10MB per image (decoded).
              content_type MUST be in the 4-type allowlist
              (image/png | image/jpeg | image/jpg | image/gif | image/webp).
    Response: 200 { "uploads": [{ "image_id", "ref_url", "content_type",
                                  "size_bytes", "uploaded_at" }, ...] }
              — ref_url is the canonical emitted form
              (``/api/tmp_images/<image_id>``). ``tmpimg://<image_id>``
              is NEVER emitted (input-alias only).
    Errors:
        422 UNPROCESSABLE_ENTITY — FastAPI/pydantic validation envelope
                                  (``{"detail": [{"type": ..., "loc": [...],
                                  "msg": ..., ...}, ...]}``). This is the
                                  wire shape for a malformed request
                                  body — count > 3, per-item bytes >
                                  limit, malformed base64, missing
                                  fields, etc. The 422 path bypasses
                                  the typed ``ErrorResponse`` envelope
                                  below; the FE MUST parse the
                                  ``detail`` array (extract ``msg``)
                                  rather than look for a ``code`` field
                                  that does not exist on this path.
        400 INVALID_REQUEST      — ``ErrorResponse`` envelope
                                  (``{"code": "...", "message": "..."}``)
                                  for explicit allowlist rejections.
                                  Allowlist rejection message is
                                  VERBATIM: "content_type '<t>'
                                  rejected — only png/jpeg/gif/webp
                                  allowed (svg excluded: stored-XSS via
                                  direct navigation)".
        409 CONFLICT             — image_id collision (caller bug — should
                                  not reuse server-minted ids).
        507 INSUFFICIENT_STORAGE — store byte cap exceeded; rate-limited
                                  WARNING (≤1/min).

GET /api/tmp_images/{image_id_or_ref}
    Accepts: bare 32-hex id (the normal form), OR
             ``tmpimg://<32hex>`` after URL-decoding the prefix
             (parse-tolerant input).
    Path-traversal: id MUST match ``^[a-f0-9]{32}$`` AFTER stripping
                    any scheme prefix — anything else returns 404
                    (no body leak, no regex-shape leak).
    Response: 200 with raw bytes + the following headers:
        Content-Type: <stored-mime>
        Content-Length: <exact bytes>
        Cache-Control: private, max-age=3600
        ETag: W/"<sha256-hex[:16]>"
        X-Content-Type-Options: nosniff        (architect amendment #1)
        Content-Disposition: inline; filename="<image_id>"
    Errors:
        304 NOT_MODIFIED — If-None-Match matches stored ETag (no body;
                          carries ETag / Cache-Control / nosniff /
                          Content-Disposition — the 200 header set
                          minus Content-Length and Content-Type
                          (no body ⇒ no entity headers))
        404 NOT_FOUND    — id not in store / malformed id

DELETE /api/tmp_images/{image_id}
    204 No Content, idempotent. Same path-traversal safety as GET.
    FileNotFoundError swallowed silently (FE DELETE ∥ phase-3 sweep
    race = expected traffic per architect §7).
    Errors:
        404 NOT_FOUND — id never existed / malformed id

GET /api/tmp_images
    GATED debug listing (architect amendment #4). Default OFF
    (ENSEMBLE_TMP_IMAGE_DEBUG_LISTING unset / 0) returns 404 — the
    route does not advertise itself to a casual probe. With
    ENSEMBLE_TMP_IMAGE_DEBUG_LISTING=1 returns:
        200 { "count": int, "oldest_mtime": iso8601-or-null }
    No id leak — recon-only exposure.
"""

from __future__ import annotations

import binascii
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response

from daemon.models.tmp_image import (
    TmpImageCleanupStatus,
    TmpImageDebugListingResponse,
    TmpImageUploadBatchResponse,
    TmpImageUploadRequest,
    TmpImageUploadResponse,
    _normalize_content_type,
)
from daemon.models import ErrorCodes, ErrorResponse
from daemon.services.tmp_image_store import (
    TmpImageNotFound,
    TmpImageStoreFull,
    TmpImageStore,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tmp_images", tags=["tmp_images"])

# Canonical id regex. MUST be enforced BEFORE any filesystem call —
# rejects path-traversal shapes including "..", "/", "\", unicode,
# control chars, and any URL-encoded variant. Anything not matching
# this pattern returns 404 (NOT 400 — to avoid leaking the regex shape).
_IMAGE_ID_REGEX = re.compile(r"^[a-f0-9]{32}$")

# Scheme prefix the GET endpoint accepts as an alias (parse-tolerant
# input per architect amendment #5). The actual wire form is the bare
# id — this prefix is only honored so callers passing the ref through
# without stripping the scheme still resolve.
_TMPIMG_SCHEME = "tmpimg://"

# ETag shape: weak, sha256[:16] hex (architect amendment #1).
_ETAG_QUOTE = '"'

# Rate-limited WARNING for over-cap POSTs (architect amendment #3).
# Module-level timestamp so the limiter is shared across requests.
# The limiter is intentionally tiny — a closure on the router would
# suffice, but module-level keeps tests straightforward (monkeypatch
# the timestamp to drive the rate).
_LAST_FULL_WARNING_AT: float = 0.0
_FULL_WARNING_INTERVAL_SECONDS = 60.0


def _reset_full_warning_clock() -> None:
    """Test hook — clear the rate-limit clock between assertions."""
    global _LAST_FULL_WARNING_AT
    _LAST_FULL_WARNING_AT = 0.0


def _maybe_log_full_warning() -> None:
    """Emit a single WARNING per minute when the store is full.

    The cap itself is enforced inside ``TmpImageStore.save``; this
    helper gates the LOG frequency so a misconfigured deployment
    doesn't drown the log with 1 WARNING per upload attempt.
    """
    global _LAST_FULL_WARNING_AT
    now = time.monotonic()
    if now - _LAST_FULL_WARNING_AT < _FULL_WARNING_INTERVAL_SECONDS:
        return
    _LAST_FULL_WARNING_AT = now
    logger.warning(
        "[TmpImages] store full — refusing uploads; check "
        "tmp_image_store_max_bytes / phase-3 retention sweep."
    )


def _get_store(request: Request) -> TmpImageStore:
    """Resolve the per-app TmpImageStore from ``app.state``.

    Returns 503 if the lifespan did not wire the store — a clean
    fail-fast for an incomplete boot (the store is always-on infra).
    """
    store = getattr(request.app.state, "tmp_image_store", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail=ErrorResponse(
                code=ErrorCodes.SERVICE_UNAVAILABLE,
                message="tmp-image store not initialized",
            ).model_dump(),
        )
    return store


def _normalize_image_id(raw: str) -> str | None:
    """Return the canonical 32-hex id, or None on any non-conforming shape.

    Accepts:
        * bare ``<32hex>``
        * ``tmpimg://<32hex>`` (scheme stripped before validation)
        * URL-decoded forms of the above (FastAPI decodes path params
          automatically; we still validate the resulting string).

    Rejects everything else (404 — see router):
        * ``..`` / ``/`` / ``\\``
        * unicode / control chars
        * any URL-encoded variant that doesn't decode to a valid form
    """
    if not raw:
        return None
    candidate = raw
    if candidate.startswith(_TMPIMG_SCHEME):
        candidate = candidate[len(_TMPIMG_SCHEME):]
    if _IMAGE_ID_REGEX.match(candidate) is None:
        return None
    return candidate


# ===========================================================================
# POST /api/tmp_images
# ===========================================================================


@router.post(
    "",
    response_model=TmpImageUploadBatchResponse,
    response_model_exclude_none=False,
)
async def upload_images(
    request: Request,
    payload: TmpImageUploadRequest,
) -> TmpImageUploadBatchResponse:
    """Accept a batch of images (≤3) and persist them to the store.

    Per-image failures abort the whole request — phase 2 assumes a
    successful POST yields valid refs. The router walks each entry in
    order; a 507 / 409 / 500 mid-batch leaves the request rejected and
    any earlier successful writes are rolled back (the store's
    per-image delete is used).
    """
    store = _get_store(request)
    if not payload.images:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message="request body must include at least one image",
            ).model_dump(),
        )

    accepted_ids: list[str] = []
    responses: list[TmpImageUploadResponse] = []
    try:
        for img in payload.images:
            try:
                # Decode-once (phase-1+3 review S3): the batch-level
                # validator already decoded and cached the payload, so
                # this is a cache hit on the validated path. The
                # defensive except stays as a belt-and-braces 400 for
                # any shape that could ever bypass the validators.
                decoded = img.decoded_bytes()
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail=ErrorResponse(
                        code=ErrorCodes.INVALID_REQUEST,
                        message=f"image data_base64 is not valid base64: {exc}",
                    ).model_dump(),
                ) from exc
            image_id = uuid.uuid4().hex
            normalized_ct = _normalize_content_type(img.content_type)
            try:
                record = store.save(image_id, decoded, normalized_ct)
            except TmpImageStoreFull as exc:
                _maybe_log_full_warning()
                raise HTTPException(
                    status_code=507,
                    detail=ErrorResponse(
                        code=ErrorCodes.TMP_IMAGE_STORE_FULL,
                        message="tmp-image store is full",
                        details={"reason": str(exc)},
                    ).model_dump(),
                ) from exc
            except FileExistsError as exc:
                # O_CREAT|O_EXCL collision — vanishingly rare with uuid4,
                # but possible if a client retries with a deterministic id.
                raise HTTPException(
                    status_code=409,
                    detail=ErrorResponse(
                        code=ErrorCodes.TMP_IMAGE_CONFLICT,
                        message=f"image_id collision: {exc}",
                    ).model_dump(),
                ) from exc
            except OSError as exc:
                logger.error(f"[TmpImages] save failed: {exc}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=ErrorResponse(
                        code=ErrorCodes.INTERNAL_ERROR,
                        message=f"tmp-image save failed: {exc}",
                    ).model_dump(),
                ) from exc
            accepted_ids.append(image_id)
            responses.append(
                TmpImageUploadResponse(
                    image_id=record.image_id,
                    ref_url=f"/api/tmp_images/{record.image_id}",
                    content_type=record.content_type,
                    size_bytes=record.size_bytes,
                    uploaded_at=_parse_iso(record.uploaded_at),
                )
            )
    except HTTPException:
        # Roll back any earlier successful saves so a partial POST
        # never leaves the client with refs that don't all resolve.
        for image_id in accepted_ids:
            try:
                store.delete(image_id)
            except Exception:  # noqa: BLE001 — best-effort rollback
                logger.warning(
                    f"[TmpImages] rollback delete failed for {image_id}",
                    exc_info=True,
                )
        raise

    return TmpImageUploadBatchResponse(uploads=responses)


def _parse_iso(value: str) -> datetime:
    """Parse the ISO string the store wrote (with offset) into a datetime.

    Pydantic v2 requires a ``datetime`` instance for ``uploaded_at``;
    ``datetime.fromisoformat`` accepts the ``+00:00`` offset the store
    produces (``now_utc_iso`` returns aware UTC).
    """
    return datetime.fromisoformat(value)


# ===========================================================================
# GET /api/tmp_images/{image_id_or_ref}
# ===========================================================================


@router.get("/{image_id_or_ref:path}")
async def serve_image(image_id_or_ref: str, request: Request) -> Response:
    """Serve a stored image's bytes with hardening headers + ETag.

    The ``:path`` converter is used so that callers may pass a
    URL-encoded ``tmpimg://<32hex>`` form (parse-tolerant input). Any
    other shape — including ``..``, ``/``, ``\\``, unicode, control
    chars — falls through the regex gate and returns 404 without
    leaking the validation rule.
    """
    normalized = _normalize_image_id(image_id_or_ref)
    if normalized is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.TMP_IMAGE_NOT_FOUND,
                message="tmp image not found",
            ).model_dump(),
        )
    store = _get_store(request)
    try:
        data, content_type, sha256_hex = store.open_with_meta(normalized)
    except TmpImageNotFound:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.TMP_IMAGE_NOT_FOUND,
                message="tmp image not found",
            ).model_dump(),
        )

    weak_etag = f"W/{_ETAG_QUOTE}{sha256_hex[:16]}{_ETAG_QUOTE}" if sha256_hex else None
    if weak_etag is not None:
        if_none_match = request.headers.get("if-none-match")
        # Exact single-value compare is DELIBERATE (phase-1+3 review
        # S5): this endpoint issues exactly ONE weak ETag per resource
        # and the frozen contract above defines a single-value
        # If-None-Match. RFC 7232 list / ``*`` forms are out of
        # contract; a non-matching form falls through to a 200, which
        # is the safe direction (re-serve, never wrongly-304).
        if if_none_match is not None and if_none_match.strip() == weak_etag:
            return Response(
                status_code=304,
                headers={
                    "ETag": weak_etag,
                    "Cache-Control": "private, max-age=3600",
                    "X-Content-Type-Options": "nosniff",
                    # Content-Disposition matches the 200 path for
                    # header consistency (phase-1+3 review S1).
                    "Content-Disposition": f'inline; filename="{normalized}"',
                },
            )

    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(data)),
        "Cache-Control": "private, max-age=3600",
        # Hardening headers — architect amendment #1. Without
        # ``nosniff`` a browser can sniff-to-``image/svg+xml`` /
        # ``text/html`` on direct navigation = same-origin XSS into
        # the SPA (the daemon serves the FE itself — same origin,
        # CORS ``*`` at daemon/api.py:2422-2429). The Content-
        # disposition forces an inline image render even on
        # direct-navigation, neutralising the download→rename vector.
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="{normalized}"',
    }
    if weak_etag is not None:
        headers["ETag"] = weak_etag
    return Response(
        content=data,
        media_type=content_type,
        status_code=200,
        headers=headers,
    )


# ===========================================================================
# DELETE /api/tmp_images/{image_id}
# ===========================================================================


@router.delete("/{image_id_or_ref:path}", status_code=204)
async def delete_image(image_id_or_ref: str, request: Request) -> Response:
    """Idempotent delete (architect amendment #6).

    Returns 204 unconditionally for any well-formed id — including ids
    that were already deleted or never existed on disk. Malformed ids
    (failing the regex gate) return 404 because the request was
    structurally invalid, not just missing-on-disk.
    """
    normalized = _normalize_image_id(image_id_or_ref)
    if normalized is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.TMP_IMAGE_NOT_FOUND,
                message="tmp image not found",
            ).model_dump(),
        )
    store = _get_store(request)
    # Per architect amendment #6, the FileNotFoundError path is
    # swallowed silently — FE DELETE ∥ phase-3 sweep race = expected
    # traffic per architect §7.
    store.delete(normalized)
    return Response(status_code=204)


# ===========================================================================
# GET /api/tmp_images (gated debug listing)
# ===========================================================================


@router.get(
    "",
    response_model=TmpImageDebugListingResponse,
    include_in_schema=False,
)
async def debug_listing(request: Request) -> TmpImageDebugListingResponse:
    """GATED health/debug listing (architect amendment #4).

    Default OFF — the route returns 404 when
    ``ENSEMBLE_TMP_IMAGE_DEBUG_LISTING`` is unset or 0. With the env
    flag set to a truthy value, returns ``{count, oldest_mtime}`` only
    — no id leak (ids appear in SSE / checkpoints / logs by design; do
    not amplify the surface).
    """
    raw = os.environ.get("ENSEMBLE_TMP_IMAGE_DEBUG_LISTING", "")
    if raw not in ("1", "true", "TRUE", "True", "yes", "YES"):
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                code=ErrorCodes.TMP_IMAGE_NOT_FOUND,
                message="not found",
            ).model_dump(),
        )
    store = _get_store(request)
    count = store.count()
    oldest = store.oldest_mtime()
    oldest_iso: datetime | None = None
    if oldest is not None:
        oldest_iso = datetime.fromtimestamp(oldest, tz=timezone.utc)
    # Phase-3 cleanup block — getattr-guarded so the endpoint keeps
    # working on boot shapes where the service was never wired
    # (cleanup: null). The service is ALWAYS-ON; the status block
    # carries NO 'enabled' key by construction (architect amendment
    # #15) — the shape is pinned by tests.
    cleanup_service = getattr(request.app.state, "tmp_image_cleanup", None)
    cleanup: TmpImageCleanupStatus | None = None
    if cleanup_service is not None:
        cleanup = TmpImageCleanupStatus(
            interval_seconds=cleanup_service.interval_seconds,
            retention_days=cleanup_service.retention_days,
            last_sweep_at=cleanup_service.last_sweep_at,
            last_sweep_deleted=cleanup_service.last_sweep_deleted,
            last_sweep_error=cleanup_service.last_sweep_error,
        )
    return TmpImageDebugListingResponse(
        count=count, oldest_mtime=oldest_iso, cleanup=cleanup
    )


# ===========================================================================
# Mounting seam — the lifespan includes this router via the api_router
# BEFORE the SPA catch-all (architect risk #1). The router has its own
# prefix ``/tmp_images`` and is mounted under ``/api`` (see daemon/api.py).
# ===========================================================================
