"""Path→data-URI bridge — substrate image_id → ``data:`` URI list.

P2-WP3 implementation of the bridge design documented in
``.agents/shared/planning/designer-agent/implementation-plan/bridge-design.md``.
The doc is the contract (per PD-11 — deviations are defects, reported
not silently absorbed).

Layering (Option B per bridge-design §3):
    ``daemon/services/tmp_image_bridge.py`` imports
    ``daemon.services.tmp_image_store``; ``daemon.tools.compare_tools``
    imports the bridge; no reverse dependency.

The five binding constraints (per bridge-design §2):

1. **Daemon-side execution.** The bridge reads substrate blobs from
   ``data_dir/tmp_images`` via ``TmpImageStore.open_full`` and never
   exposes raw paths or bytes to agent-visible surfaces.

2. **MIME from sidecar (never extension guessing).** The
   ``data:`` URI prefix is built from ``TmpImageRecord.content_type``
   as recorded in the sidecar. Magic-byte sniffing is a
   sidecar-MISSING fallback only; the recovery sets
   ``mime_recovered: true`` on the result.

3. **404 is authoritative.** Missing blob / missing or torn sidecar
   → per-position ``{"error": "unavailable_path", ...}`` envelope.
   No optimistic caching, no retry storm.

4. **Output contract + per-image size guard.** Ordered list
   position N ↔ input position N; ``size_bytes > 20 MiB``
   → ``{"error": "image_too_large", ...}`` envelope. Size check
   fires BEFORE base64 encoding (no partial work).

5. **Provenance passthrough.** The bridge attaches
   ``TmpImageRecord.provenance`` to the result verbatim — never
   synthesizes it, never infers from filenames.

Acceptance criteria (per bridge-design §6) are pinned in
``tests/test_tmp_image_bridge.py``.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.services.tmp_image_store import TmpImageStore

logger = logging.getLogger(__name__)

# Default per-image size guard (per bridge-design §2 row 4).
# 20 MiB pre-base64. Base64 inflation is ~4/3 (15 MiB → ~20 MiB
# base64); the store cap is 1 GiB total, so 20 MiB per image caps
# request size without starving the store.
DEFAULT_SIZE_LIMIT_BYTES: int = 20 * 1024 * 1024  # 21,474,840

# Recognised magic-byte signatures for the §2 row 2 fallback path.
# The fallback fires only when the sidecar MIME is empty or unparseable;
# anything unrecognised routes to ``unavailable_path`` (NOT a recovery
# opportunity — a blob without a recognised signature is unauthenticated
# as to MIME).
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # RIFF + WEBP at offset 8 — checked separately
    (b"BM", "image/bmp"),
)


def _sniff_mime(blob: bytes) -> str | None:
    """Recover a MIME from the first bytes of a blob (sidecar-MISSING fallback).

    Only invoked when the sidecar is present but ``content_type`` is
    empty or unparseable. Returns ``None`` when the signature is not
    recognised — the bridge then surfaces ``unavailable_path`` (per
    bridge-design §2 row 2 contract #3).
    """
    if not blob:
        return None
    head = blob[:8]
    for sig, mime in _MAGIC_SIGNATURES:
        if head.startswith(sig):
            # WEBP needs the bytes at offset 8 to confirm — the RIFF
            # prefix alone is ambiguous (RIFF/WAVE is also valid).
            if mime == "image/webp":
                if len(blob) >= 12 and blob[8:12] == b"WEBP":
                    return mime
                continue
            return mime
    return None


def _resolve_single(
    store: "TmpImageStore",
    image_id: str,
    *,
    size_limit_bytes: int,
) -> dict[str, Any]:
    """Resolve one substrate id → per-image result dict.

    The shape is the bridge-design §2 row 5 contract: a position-N
    dict carrying ``image_id``, ``content_type``, ``data_uri``,
    ``size_bytes``, ``provenance``, ``retention_class`` (with
    ``mime_recovered`` flag on the fallback path).

    On any failure mode the dict carries the per-position error
    envelope (``error`` key) — the bridge NEVER raises to the caller.

    Order preservation (A1) is the caller's responsibility: the
    caller walks the input list position-by-position and stitches the
    per-image results back into the output list in the same order.
    """
    # Lazy import — the store module is large and only needed when
    # the bridge actually fires. Same pattern as
    # ``daemon/tools/image_tools.py``.
    from daemon.services.tmp_image_store import TmpImageNotFound

    # Constraint row 4 contract #1: cheap pre-check on size_bytes
    # from the sidecar BEFORE base64. Avoids wasted CPU on a
    # pathological capture that would have been rejected anyway.
    try:
        record = store.open_full(image_id)
    except TmpImageNotFound as exc:
        # Constraint row 3: 404 → structured per-position error.
        # ``reason`` mirrors the four store-side failure modes that
        # ``open()`` documents (blob_missing / sidecar_missing /
        # sidecar_unreadable / sidecar_torn_json). The store's
        # ``TmpImageNotFound`` message carries the underlying
        # reason; surface it verbatim so the comparator can audit.
        reason = "blob_missing"
        msg = str(exc)
        lower = msg.lower()
        if "sidecar" in lower:
            if "unreadable" in lower or "invalid json" in lower:
                reason = "sidecar_unreadable"
            elif "missing" in lower:
                reason = "sidecar_missing"
            else:
                reason = "sidecar_torn_json"
        return {
            "error": "unavailable_path",
            "image_id": image_id,
            "reason": reason,
            "message": msg,
        }

    # Constraint row 4 contract #2: per-image size guard fires
    # BEFORE any base64 work. ``size_bytes`` comes from the sidecar
    # only — no need to read the blob to check.
    if record.size_bytes > size_limit_bytes:
        return {
            "error": "image_too_large",
            "image_id": image_id,
            "size_bytes": record.size_bytes,
            "size_limit_bytes": size_limit_bytes,
            "message": (
                f"image exceeds per-image guard "
                f"({size_limit_bytes} bytes); capture likely misconfigured"
            ),
        }

    # Read the blob bytes for base64 + MIME fallback. This is the
    # only place the bridge touches the filesystem.
    try:
        blob = (store.dir / record.image_id).read_bytes()
    except OSError as exc:
        # Path-traversal defenses aside, the blob can still vanish
        # in a FE DELETE ∥ sweep race; surface a clean miss the same
        # way ``image_get`` does.
        return {
            "error": "unavailable_path",
            "image_id": image_id,
            "reason": "blob_missing",
            "message": f"tmp-image blob unreadable: {record.image_id} ({exc})",
        }

    # Constraint row 2 — MIME resolution order:
    #   1. sidecar present + content_type non-empty + parseable → use verbatim.
    #   2. sidecar present + content_type empty / malformed → magic-byte
    #      fallback (emit ``mime_recovered: true``).
    #   3. sidecar missing / unreadable / torn → 404 path (handled above).
    mime_recovered = False
    content_type = (record.content_type or "").strip()
    if not content_type:
        recovered = _sniff_mime(blob)
        if recovered is None:
            return {
                "error": "unavailable_path",
                "image_id": image_id,
                "reason": "unrecognized_mime",
                "message": (
                    f"sidecar content_type empty and magic-byte "
                    f"signature not recognised for {record.image_id}"
                ),
            }
        content_type = recovered
        mime_recovered = True

    # Constraint row 4 contract #3: base64-encode the blob bytes;
    # emit ``"data:<content_type>;base64,<...>"``.
    encoded = base64.b64encode(blob).decode("ascii")
    data_uri = f"data:{content_type};base64,{encoded}"

    # Constraint row 5 — provenance passthrough (byte-for-byte).
    # ``None`` propagates as ``None`` (A6).
    result: dict[str, Any] = {
        "image_id": record.image_id,
        "content_type": content_type,
        "data_uri": data_uri,
        "size_bytes": record.size_bytes,
        "provenance": record.provenance,
        "retention_class": record.retention_class,
    }
    if mime_recovered:
        # Opt-in flag — auditors / callers can detect a sniffed MIME.
        result["mime_recovered"] = True
    return result


def resolve_data_uris(
    image_ids: list[str],
    *,
    store: "TmpImageStore",
    size_limit_bytes: int = DEFAULT_SIZE_LIMIT_BYTES,
) -> list[dict[str, Any]]:
    """Resolve a list of substrate image_ids → ordered list of ``data:`` URIs.

    Per-image result preserves input order position-for-position
    (bridge-design A1) — the caller walks the input list and
    stitches the per-image results back into the output list in the
    same order.

    The bridge is pure read-shaping — no writes, no caching
    (bridge-design A8 — every call re-resolves through
    ``store.open_full``), no retry on 404 (A9), no agent-side file
    I/O (A10 — daemon-side only).

    Args:
        image_ids: Ordered list of substrate ids (32-hex) to resolve.
        store: The shared ``TmpImageStore`` instance the bridge reads
            through. The caller is responsible for wiring (typically
            ``manager.tmp_image_store`` from the lifespan).
        size_limit_bytes: Per-image size guard (pre-base64). Defaults
            to ``DEFAULT_SIZE_LIMIT_BYTES`` (20 MiB per bridge-design
            §2 row 4).

    Returns:
        Ordered list of per-image result dicts. Each entry is
        EITHER a success row carrying ``image_id``,
        ``content_type``, ``data_uri``, ``size_bytes``,
        ``provenance``, ``retention_class`` (and ``mime_recovered``
        when the fallback path fired),
        OR a structured-error row carrying ``error`` (one of
        ``"unavailable_path"``, ``"image_too_large"``,
        ``"unrecognized_mime"``) plus the matching diagnostic
        fields.

    Raises:
        Never raises. Per-position failures surface as
        ``{"error": "unavailable_path", ...}`` envelopes the
        caller (the comparator facade) surfaces verbatim.
    """
    if not isinstance(image_ids, list):
        # Bridge-design contract #4 / facade-never-raises — defensively
        # coerce to empty list rather than raising. The facade
        # catches TypeError upstream and surfaces its own envelope.
        try:
            image_ids = list(image_ids)
        except Exception:
            return []
    out: list[dict[str, Any]] = []
    for image_id in image_ids:
        if not isinstance(image_id, str) or not image_id:
            # Skip invalid position silently (caller-side validation
            # gates this; the bridge is read-only and never invents
            # an image_id).
            out.append(
                {
                    "error": "unavailable_path",
                    "image_id": str(image_id),
                    "reason": "invalid_id",
                    "message": (
                        "image_id must be a non-empty string "
                        "(substrate 32-hex id)"
                    ),
                }
            )
            continue
        out.append(
            _resolve_single(
                store,
                image_id,
                size_limit_bytes=size_limit_bytes,
            )
        )
    return out
