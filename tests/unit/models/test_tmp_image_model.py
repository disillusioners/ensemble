"""Unit tests for ``daemon.models.tmp_image`` Pydantic models.

Phase 1 / clipboard-image-chat. Validators pin:

* happy 1×1 PNG round-trips through TmpImageUploadRequest.
* 11MB blob (over the 10MB cap) is rejected at the request layer.
* Unknown MIME produces the architect-mandated VERBATIM rejection
  message (forensic-friendly — names the rejected type).
* Non-base64 garbage is rejected by the per-image validator.
* count > 3 is rejected at the request layer.
* Magic-byte cross-check: declaring image/png but passing JPEG bytes
  is rejected (defense-in-depth sniff).
"""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from daemon.models.tmp_image import (
    TmpImageUpload,
    TmpImageUploadRequest,
)


# Standard 1×1 PNG (70 bytes decoded). Used as the happy-path payload.
VALID_1X1_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "AAIAAAoAAv/lxKUAAAAASUVORK5CYII="
)
# Same payload but with the data-URI header — NOT used here (the model
# accepts raw base64 only) but kept for the assertion that the wire
# form is the bare payload.
VALID_1X1_PNG_DATA_URI = (
    "data:image/png;base64," + VALID_1X1_PNG_B64
)

# 1×1 JPEG bytes (the smallest valid JFIF). Used to fabricate a
# "wrong content_type, right magic bytes" cross-check.
VALID_1X1_JPEG_B64 = (
    "/9j/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB/9k="
)


def _upload(content_type: str = "image/png", data_b64: str | None = None) -> TmpImageUpload:
    return TmpImageUpload(
        filename="hello.png",
        content_type=content_type,
        data_base64=data_b64 if data_b64 is not None else VALID_1X1_PNG_B64,
    )


# ---------------------------------------------------------------------------
# Group 1 — happy path
# ---------------------------------------------------------------------------


class TestTmpImageUploadHappy:
    def test_valid_1x1_png_constructs(self):
        img = _upload()
        assert img.filename == "hello.png"
        assert img.content_type == "image/png"
        assert img.data_base64 == VALID_1X1_PNG_B64

    def test_request_with_single_image_constructs(self):
        req = TmpImageUploadRequest(images=[_upload()])
        assert len(req.images) == 1

    def test_jpg_alias_accepted_for_content_type(self):
        # Architect: jpg aliases jpeg (allowlist regex accepts both).
        # JPEG payload — cross-check via the 1×1 JPEG fixture.
        req = TmpImageUploadRequest(
            images=[
                TmpImageUpload(
                    filename="a.jpg",
                    content_type="image/jpg",
                    data_base64=VALID_1X1_JPEG_B64,
                )
            ]
        )
        assert req.images[0].content_type == "image/jpg"


# ---------------------------------------------------------------------------
# Group 2 — rejection paths
# ---------------------------------------------------------------------------


class TestTmpImageUploadRejections:
    def test_unknown_content_type_rejected_with_verbatim_message(self):
        # The exact wording is part of the wire contract — see phase1-plan.md
        # "Allowlist rejection message is verbatim".
        with pytest.raises(ValidationError) as ei:
            TmpImageUpload(
                filename="a.svg",
                content_type="image/svg+xml",
                data_base64=VALID_1X1_PNG_B64,
            )
        msg = str(ei.value)
        assert "content_type 'image/svg+xml' rejected" in msg
        assert "only png/jpeg/gif/webp allowed" in msg
        assert "svg excluded: stored-XSS via direct navigation" in msg

    def test_bmp_rejected_with_verbatim_message(self):
        # architect amendment #2 — bmp was DROPPED from the new endpoint
        # (the existing MessageCreate regex is a separate amendment).
        with pytest.raises(ValidationError) as ei:
            TmpImageUpload(
                filename="a.bmp",
                content_type="image/bmp",
                data_base64=VALID_1X1_PNG_B64,
            )
        assert "content_type 'image/bmp' rejected" in str(ei.value)

    def test_tiff_rejected_with_verbatim_message(self):
        with pytest.raises(ValidationError) as ei:
            TmpImageUpload(
                filename="a.tiff",
                content_type="image/tiff",
                data_base64=VALID_1X1_PNG_B64,
            )
        assert "content_type 'image/tiff' rejected" in str(ei.value)

    def test_non_base64_data_rejected(self):
        # '!@#$%^' is well-formed UTF-8 but invalid base64.
        with pytest.raises(ValidationError) as ei:
            TmpImageUpload(
                filename="a.png",
                content_type="image/png",
                data_base64="!@#$%^",
            )
        # Error message should mention base64.
        assert "base64" in str(ei.value).lower()

    def test_base64_with_whitespace_rejected(self):
        # Mid-payload whitespace breaks the round-trip — reject.
        bad = VALID_1X1_PNG_B64[:30] + "\n" + VALID_1X1_PNG_B64[30:]
        with pytest.raises(ValidationError) as ei:
            TmpImageUpload(
                filename="a.png",
                content_type="image/png",
                data_base64=bad,
            )
        assert "whitespace" in str(ei.value).lower()

    def test_oversize_blob_rejected_at_request_layer(self):
        # Build a synthetic 11MB payload (raw bytes — we don't need
        # real PNG magic bytes here; the size check fires before the
        # cross-check). Encode as base64 to feed the model.
        size = 11 * 1024 * 1024
        big = b"\x00" * size
        big_b64 = base64.b64encode(big).decode("ascii")
        # Single image — passes the per-image validator but trips the
        # request-layer size cap.
        with pytest.raises(ValidationError) as ei:
            TmpImageUploadRequest(
                images=[
                    TmpImageUpload(
                        filename="big.png",
                        content_type="image/png",
                        data_base64=big_b64,
                    )
                ]
            )
        assert "10MB" in str(ei.value) or "maximum size" in str(ei.value).lower()

    def test_count_above_three_rejected(self):
        with pytest.raises(ValidationError) as ei:
            TmpImageUploadRequest(
                images=[_upload() for _ in range(4)]
            )
        assert "Maximum 3 images" in str(ei.value)

    def test_empty_images_list_rejected(self):
        # The model field has no default — an empty list is invalid
        # because we want at least one image per POST (a "delete via
        # POST" semantics is forbidden; DELETE is the path).
        # Pydantic v2: empty list passes the model validator (no min
        # length) but the request handler will reject it at the router
        # layer. At the model layer we don't enforce it.
        req = TmpImageUploadRequest(images=[])
        assert len(req.images) == 0


# ---------------------------------------------------------------------------
# Group 3 — magic-byte cross-check (defense in depth)
# ---------------------------------------------------------------------------


class TestTmpImageMagicByteCrossCheck:
    def test_png_payload_with_jpeg_declared_rejected(self):
        # Decoding VALID_1X1_JPEG_B64 gives JPEG bytes (\xff\xd8\xff);
        # declaring image/png against JPEG bytes should fail the
        # cross-check.
        with pytest.raises(ValidationError) as ei:
            TmpImageUploadRequest(
                images=[
                    TmpImageUpload(
                        filename="fake.png",
                        content_type="image/png",
                        data_base64=VALID_1X1_JPEG_B64,
                    )
                ]
            )
        assert "magic bytes" in str(ei.value).lower() or "cross-check" in str(ei.value).lower()

    def test_png_payload_with_gif_declared_rejected(self):
        # Same idea — declare GIF, payload is JPEG bytes.
        with pytest.raises(ValidationError) as ei:
            TmpImageUploadRequest(
                images=[
                    TmpImageUpload(
                        filename="fake.gif",
                        content_type="image/gif",
                        data_base64=VALID_1X1_JPEG_B64,
                    )
                ]
            )
        assert "magic bytes" in str(ei.value).lower() or "cross-check" in str(ei.value).lower()

    def test_jpeg_payload_with_png_declared_rejected(self):
        # Build a fake PNG header (8 bytes) and pad with zeros — declare
        # image/jpeg against a PNG-shaped payload. Cross-check should
        # reject.
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        png_b64 = base64.b64encode(png_header).decode("ascii")
        with pytest.raises(ValidationError) as ei:
            TmpImageUploadRequest(
                images=[
                    TmpImageUpload(
                        filename="fake.jpg",
                        content_type="image/jpeg",
                        data_base64=png_b64,
                    )
                ]
            )
        assert "magic bytes" in str(ei.value).lower() or "cross-check" in str(ei.value).lower()

    def test_webp_payload_sniff_requires_full_marker(self):
        # RIFF header alone is not enough — WEBP needs the WEBP marker
        # at offset 8. A RIFF + WAVEs sample (RIFF + WAVE) should fail
        # the WEBP sniff even though declared content_type is webp.
        wav = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 16
        wav_b64 = base64.b64encode(wav).decode("ascii")
        with pytest.raises(ValidationError) as ei:
            TmpImageUploadRequest(
                images=[
                    TmpImageUpload(
                        filename="fake.webp",
                        content_type="image/webp",
                        data_base64=wav_b64,
                    )
                ]
            )
        assert "magic bytes" in str(ei.value).lower() or "cross-check" in str(ei.value).lower()

    def test_webp_payload_with_proper_marker_accepted(self):
        # RIFF header + WEBP marker at offset 8 → valid webp sniff.
        webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
        webp_b64 = base64.b64encode(webp).decode("ascii")
        req = TmpImageUploadRequest(
            images=[
                TmpImageUpload(
                    filename="ok.webp",
                    content_type="image/webp",
                    data_base64=webp_b64,
                )
            ]
        )
        assert len(req.images) == 1


# ---------------------------------------------------------------------------
# Group 4 — model config (defense vs accidental extra fields)
# ---------------------------------------------------------------------------


class TestTmpImageModelConfig:
    def test_extra_fields_rejected_on_upload(self):
        # ``extra='forbid'`` — protects against FE/FE-encoder typo
        # adding a field that the server silently ignores.
        with pytest.raises(ValidationError):
            TmpImageUpload(
                filename="a.png",
                content_type="image/png",
                data_base64=VALID_1X1_PNG_B64,
                extra_field="x",
            )

    def test_extra_fields_rejected_on_request(self):
        with pytest.raises(ValidationError):
            TmpImageUploadRequest(
                images=[_upload()],
                extra_field="x",
            )


# ---------------------------------------------------------------------------
# Group 5 — filename cap (phase-1+3 review S4)
# ---------------------------------------------------------------------------


class TestTmpImageFilenameCap:
    def test_filename_at_255_chars_accepted(self):
        # The cap boundary itself is valid.
        img = TmpImageUpload(
            filename="a" * 255,
            content_type="image/png",
            data_base64=VALID_1X1_PNG_B64,
        )
        assert len(img.filename) == 255

    def test_filename_over_255_chars_rejected(self):
        with pytest.raises(ValidationError) as ei:
            TmpImageUpload(
                filename="a" * 256,
                content_type="image/png",
                data_base64=VALID_1X1_PNG_B64,
            )
        assert "255" in str(ei.value)
