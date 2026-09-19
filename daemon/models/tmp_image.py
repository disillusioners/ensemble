"""Pydantic models for the tmp-image upload + serve endpoints.

Phase 1 of the clipboard-image-chat feature. Phase 2 owns conversion
(text extraction) and persistence; phase 3 owns retention sweep.

The models here pin:

* The upload payload shape ``TmpImageUpload`` (filename + content_type +
  base64 data). Validators enforce the 4-type allowlist
  (png/jpeg/jpg/gif/webp), well-formed base64, the per-image size cap
  (≤10MB), and the per-request count cap (≤3).
* The response shape ``TmpImageUploadResponse`` with ``ref_url`` as the
  canonical wire field (``/api/tmp_images/<id>`` form). ``tmpimg://<id>``
  is INPUT-ONLY (parse-tolerant on GET) and is NEVER emitted.
* The content_type ↔ magic-byte sniff cross-check (defense in depth).

DELIBERATELY NOT imported / reused from :mod:`daemon.models.message`:

The MessageCreate regex ``_BASE64_IMAGE_PATTERN`` (daemon/models/message.py:8)
includes ``bmp`` and ``tiff`` for the existing 6-type data-URI flow. The
tmp-image endpoint uses a NEW, NARROWER regex (4 types) per architect
amendment #2 — that amendment applies to THIS endpoint only; trimming
the MessageCreate regex is a later, separate amendment on the message
path. Keeping the two regexes independent avoids accidental contract
drift between the two flows.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Magic-byte sniff table. Each entry maps the canonical stored content_type
# to the byte signature we expect at the start of the decoded payload. The
# cross-check runs AFTER base64 decode — a wrong header still passes the
# regex but fails the magic-byte sniff.
#
# ``jpg`` aliases ``jpeg`` — the table key is the STORED (normalized)
# form. Cross-check returns True if the decoded bytes match any signature
# that aliases to the declared type (so declaring ``image/jpg`` against
# JPEG bytes is accepted, and declaring ``image/jpeg`` against JPEG bytes
# is accepted).
_MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/gif": [b"GIF87a", b"GIF89a"],
    "image/webp": [b"RIFF"],  # RIFF + WEBP marker follows; full check below.
}

# JPEG covers BOTH ``image/jpeg`` and ``image/jpg`` (the allowlist regex
# accepts both spellings; storage + sniff normalize to ``image/jpeg``).
_NORMALIZED_CONTENT_TYPE: dict[str, str] = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}


def _normalize_content_type(content_type: str) -> str:
    """Return the canonical stored content_type for an allowlist entry.

    Accepts the four allowlist spellings (incl. ``jpg``→``jpeg`` alias) and
    returns the form used as the GET response's ``Content-Type`` header.
    Unknown types are returned unchanged so the validator's reject path
    can format a useful error message naming the rejected type.
    """
    return _NORMALIZED_CONTENT_TYPE.get(content_type, content_type)


def _magic_byte_matches(content_type: str, decoded: bytes) -> bool:
    """Return True if ``decoded`` starts with the magic bytes for ``content_type``.

    Called only after the regex + size validators have passed, so the
    content_type is in the allowlist. WebP needs a deeper sniff (RIFF
    header + WEBP marker at offset 8) because the RIFF signature is
    shared with other RIFF containers (WAV, AVI).
    """
    if content_type == "image/webp":
        return (
            len(decoded) >= 12
            and decoded.startswith(b"RIFF")
            and decoded[8:12] == b"WEBP"
        )
    signatures = _MAGIC_SIGNATURES.get(content_type, [])
    return any(decoded.startswith(sig) for sig in signatures)


class TmpImageUpload(BaseModel):
    """One image in a transient upload request.

    The base64 payload is stored in the canonical data-URI form so the
    wire contract mirrors :mod:`daemon.models.message` (json-stringifiable
    + no multipart dependency for the test harness).
    """

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., description="Original filename (hint only, not used for storage)")
    content_type: str = Field(..., description="Declared MIME; must be in the 4-type allowlist")
    data_base64: str = Field(..., description="Base64-encoded bytes (raw, NOT a data URI)")

    _MAX_IMAGE_BYTES: ClassVar[int] = 10 * 1024 * 1024  # 10MB per architect amendment
    _MAX_IMAGES_PER_REQUEST: ClassVar[int] = 3

    @field_validator("content_type")
    @classmethod
    def _validate_content_type(cls, v: str) -> str:
        """Reject any content_type outside the 4-type allowlist.

        The rejection message names the rejected type for forensics, per
        architect amendment #2. Verbatim wording is part of the contract
        — see tests/unit/models/test_tmp_image_model.py for the exact
        string-contains pin.
        """
        normalized = _normalize_content_type(v)
        if normalized not in _MAGIC_SIGNATURES:
            raise ValueError(
                f"content_type '{v}' rejected — only png/jpeg/gif/webp allowed "
                f"(svg excluded: stored-XSS via direct navigation)"
            )
        return v

    @field_validator("data_base64")
    @classmethod
    def _validate_data_base64(cls, v: str) -> str:
        """Confirm the base64 payload is well-formed.

        We deliberately do NOT enforce a size cap on the base64 string
        here — the decoded-size check below is the contractually
        meaningful one (the base64 length is a 4/3 upper bound on the
        byte count).
        """
        # Reject whitespace/newlines inside the payload — the data-URI
        # form is line-free by convention and FE encoders are expected
        # to produce one continuous string.
        if any(ch.isspace() for ch in v):
            raise ValueError("data_base64 must not contain whitespace")
        try:
            base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"data_base64 is not valid base64: {exc}") from exc
        return v


class TmpImageUploadRequest(BaseModel):
    """Batch upload request (≤3 images per architect amendment + count cap)."""

    model_config = ConfigDict(extra="forbid")

    images: list[TmpImageUpload] = Field(..., description="Images to upload (≤3 per request)")

    @field_validator("images")
    @classmethod
    def _validate_count_and_payloads(cls, v: list[TmpImageUpload]) -> list[TmpImageUpload]:
        """Enforce the ≤3 images cap, the per-image size cap, and the magic-byte cross-check.

        Pydantic v2 runs the per-field validators FIRST (so each
        TmpImageUpload's ``data_base64`` is already confirmed
        well-formed by the time we get here), then the model-level
        ``field_validator`` runs on the assembled list.
        """
        if len(v) > TmpImageUpload._MAX_IMAGES_PER_REQUEST:
            raise ValueError(
                f"Maximum {TmpImageUpload._MAX_IMAGES_PER_REQUEST} images allowed per request"
            )
        for idx, img in enumerate(v):
            try:
                decoded = base64.b64decode(img.data_base64, validate=True)
            except (binascii.Error, ValueError) as exc:  # pragma: no cover — validator chain
                raise ValueError(f"image[{idx}]: base64 decode failed: {exc}") from exc
            if len(decoded) > TmpImageUpload._MAX_IMAGE_BYTES:
                raise ValueError(
                    f"image[{idx}] exceeds maximum size of 10MB "
                    f"(decoded: {len(decoded) / (1024 * 1024):.1f}MB)"
                )
            normalized = _normalize_content_type(img.content_type)
            if not _magic_byte_matches(normalized, decoded):
                raise ValueError(
                    f"image[{idx}] content_type '{img.content_type}' does not match "
                    f"the decoded magic bytes (defense-in-depth cross-check)"
                )
        return v


class TmpImageUploadResponse(BaseModel):
    """One image in the POST response.

    The ``ref_url`` field is the CANONICAL emitted ref form
    (``/api/tmp_images/<image_id>``). ``tmpimg://<image_id>`` is
    INPUT-ONLY and is never emitted by the router.
    """

    image_id: str = Field(..., description="Server-minted 32-hex id (uuid4 lowercase)")
    ref_url: str = Field(..., description="Canonical wire ref: /api/tmp_images/<image_id>")
    content_type: str = Field(..., description="Normalized stored MIME (image/jpeg, image/png, …)")
    size_bytes: int = Field(..., description="Decoded payload size in bytes")
    uploaded_at: datetime = Field(..., description="ISO-8601 timestamp with offset")


class TmpImageUploadBatchResponse(BaseModel):
    """POST response wrapper — list of uploads in the same order as the request."""

    model_config = ConfigDict(extra="forbid")

    uploads: list[TmpImageUploadResponse] = Field(..., description="One entry per uploaded image")


class TmpImageDebugListingResponse(BaseModel):
    """GATED debug listing shape (ENSEMBLE_TMP_IMAGE_DEBUG_LISTING=1).

    Returns ONLY count + oldest mtime — no id leak, no metadata about
    individual entries. Recon-only exposure per architect amendment #4.
    """

    model_config = ConfigDict(extra="forbid")

    count: int = Field(..., description="Number of images currently in the store")
    oldest_mtime: datetime | None = Field(
        default=None,
        description="mtime of the oldest entry (ISO-8601 with offset); null when store is empty",
    )


# Re-export for routers/services that want the allowlist regex without
# importing the model classes (avoids importing pydantic for the hot
# path validators).
__all__ = [
    "TmpImageUpload",
    "TmpImageUploadRequest",
    "TmpImageUploadResponse",
    "TmpImageUploadBatchResponse",
    "TmpImageDebugListingResponse",
    "_normalize_content_type",
]
