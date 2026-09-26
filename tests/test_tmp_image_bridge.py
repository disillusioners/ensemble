"""Tests for ``daemon.services.tmp_image_bridge.resolve_data_uris``.

Pins the five binding constraints (bridge-design §2) and the
acceptance criteria (bridge-design §6) end-to-end:

  C1 / A1: Order preservation — input position N ↔ output position N.
  C2 / A2: MIME from sidecar (verbatim, never extension-guessed).
  C2 / A3: Magic-byte fallback fires only on absent / empty
            sidecar-MIME; ``mime_recovered: true`` flag set.
  C3 / A4: 404 is structured, not silent — missing blob / missing
            sidecar / torn JSON sidecar → per-position error envelope.
  C4 / A5: Size guard is per-image, pre-base64 — ``size_bytes >
            20 MiB`` → ``image_too_large`` envelope.
  C5 / A6: Provenance passthrough (byte-for-byte; ``null`` propagates).
  C5 / A7: Retention class passthrough (no silent eviction).
  C1 / A8: No caching — every call re-fetches via ``store.open_full``.
  C3 / A9: No retry — first ``TmpImageNotFound`` surfaces verbatim.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from daemon.services.tmp_image_bridge import (
    DEFAULT_SIZE_LIMIT_BYTES,
    resolve_data_uris,
)


# ── Test doubles ────────────────────────────────────────────────────────────


class _FakeBlobPath:
    """Path-like stand-in for ``store.dir / image_id`` returns.

    The bridge does ``(store.dir / image_id).read_bytes()`` — a real
    ``pathlib.Path`` works in production but the fake store keeps
    blobs in memory. ``_FakeBlobPath`` is a callable wrapper that
    returns the configured bytes.
    """

    def __init__(self, blobs: dict[str, bytes], image_id: str):
        self._blobs = blobs
        self._image_id = image_id

    def read_bytes(self) -> bytes:
        return self._blobs.get(self._image_id, b"")


class _FakeDir:
    """Stand-in for ``TmpImageStore.dir`` — supports ``__truediv__``."""

    def __init__(self, blobs: dict[str, bytes]):
        self._blobs = blobs

    def __truediv__(self, image_id: str) -> _FakeBlobPath:
        return _FakeBlobPath(self._blobs, image_id)


class _FakeStore:
    """In-memory ``TmpImageStore`` stand-in for bridge tests.

    Mirrors the public surface the bridge reads through:
    ``open_full(image_id) -> TmpImageRecord`` and ``dir`` (the
    filesystem path the bridge reads blob bytes from).
    """

    def __init__(
        self,
        records: dict[str, SimpleNamespace] | None = None,
        blobs: dict[str, bytes] | None = None,
        open_full_error: BaseException | None = None,
        tmp_dir_root: Path | None = None,
    ):
        self.records = records or {}
        self.blobs = blobs or {}
        self._open_full_error = open_full_error
        # The bridge reads blob bytes via ``(store.dir / image_id)``.
        # In production ``store.dir`` is a ``pathlib.Path``; the fake
        # mimics the public surface so the bridge can call it
        # unchanged.
        self._dir = _FakeDir(self.blobs)
        # ``tmp_dir_root`` is unused by the fake but mirrors the
        # real-store ``init()`` parent path; tests that want a real
        # filesystem (rare for the bridge) can wire it here.
        self._tmp_dir_root = tmp_dir_root

    @property
    def dir(self):
        return self._dir

    def open_full(self, image_id: str) -> SimpleNamespace:
        if self._open_full_error is not None:
            raise self._open_full_error
        if image_id not in self.records:
            from daemon.services.tmp_image_store import TmpImageNotFound

            raise TmpImageNotFound(f"tmp image not found: {image_id}")
        return self.records[image_id]


def _tmp_image_record(
    image_id: str = "f3e1c7a9b4d2e8f0a1b2c3d4e5f60718",
    content_type: str = "image/png",
    size_bytes: int = 1024,
    provenance: dict | None = None,
    retention_class: str = "normal",
) -> SimpleNamespace:
    """Build a SimpleNamespace stand-in for ``TmpImageRecord``."""
    return SimpleNamespace(
        image_id=image_id,
        content_type=content_type,
        size_bytes=size_bytes,
        uploaded_at="2026-09-26T00:00:00+00:00",
        sha256_hex="0" * 64,
        provenance=provenance,
        retention_class=retention_class,
    )


# ── Test helpers ────────────────────────────────────────────────────────────


# 1x1 transparent PNG (smallest valid PNG)
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)


# ── A1: Order preservation ──────────────────────────────────────────────────


class TestOrderPreservation:
    """A1 — input position N maps to output position N."""

    def test_three_input_ids_return_in_same_order(self):
        records = {
            "aaaa1111aaaa1111aaaa1111aaaa1111": _tmp_image_record(
                image_id="aaaa1111aaaa1111aaaa1111aaaa1111",
                provenance={"feature": "first"},
            ),
            "bbbb2222bbbb2222bbbb2222bbbb2222": _tmp_image_record(
                image_id="bbbb2222bbbb2222bbbb2222bbbb2222",
                provenance={"feature": "second"},
            ),
            "cccc3333cccc3333cccc3333cccc3333": _tmp_image_record(
                image_id="cccc3333cccc3333cccc3333cccc3333",
                provenance={"feature": "third"},
            ),
        }
        blobs = {k: _PNG_BYTES for k in records}
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            list(records.keys()),
            store=store,
        )

        assert len(out) == 3
        assert out[0]["image_id"] == "aaaa1111aaaa1111aaaa1111aaaa1111"
        assert out[1]["image_id"] == "bbbb2222bbbb2222bbbb2222bbbb2222"
        assert out[2]["image_id"] == "cccc3333cccc3333cccc3333cccc3333"

    def test_partial_failure_preserves_order_with_envelope_in_slot(self):
        """One id 404s — the error envelope fills its position; the
        surrounding entries still match by index."""
        records = {
            "ok_id_aaaaaaaaaaaaaaaaaaaaaaaaaa": _tmp_image_record(
                image_id="ok_id_aaaaaaaaaaaaaaaaaaaaaaaaaa",
            ),
            # The middle id is intentionally absent — the fake
            # store's ``open_full`` raises ``TmpImageNotFound`` and
            # the bridge surfaces a per-position error envelope.
            "ok2_idccccccccccccccccccccccccccccc": _tmp_image_record(
                image_id="ok2_idccccccccccccccccccccccccccccc",
            ),
        }
        blobs = {
            "ok_id_aaaaaaaaaaaaaaaaaaaaaaaaaa": _PNG_BYTES,
            "ok2_idccccccccccccccccccccccccccccc": _PNG_BYTES,
        }
        store = _FakeStore(records=records, blobs=blobs)

        ids = [
            "ok_id_aaaaaaaaaaaaaaaaaaaaaaaaaa",
            "missing_bbbbbbbbbbbbbbbbbbbbbbbbbb",
            "ok2_idccccccccccccccccccccccccccccc",
        ]
        out = resolve_data_uris(ids, store=store)

        assert len(out) == 3
        assert "data_uri" in out[0]
        assert out[1]["error"] == "unavailable_path"
        assert "data_uri" in out[2]


# ── A2 / A3: MIME from sidecar + magic-byte fallback ────────────────────────


class TestMimeResolution:
    """A2/A3 — sidecar MIME verbatim; magic-byte fallback only when missing."""

    def test_sidecar_png_content_type_used_verbatim(self):
        """Sidecar records ``image/png`` → data URI prefix ``data:image/png;...``.

        A2 + A3 pin: a blob with valid sidecar MIME is NEVER magic-sniffed.
        """
        records = {
            "img_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": _tmp_image_record(
                image_id="img_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                content_type="image/png",
            ),
        }
        blobs = {"img_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": _PNG_BYTES}
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            ["img_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], store=store
        )

        assert out[0]["content_type"] == "image/png"
        assert out[0]["data_uri"].startswith("data:image/png;base64,")
        # No fallback flag — the sidecar MIME was authoritative.
        assert "mime_recovered" not in out[0]

    def test_sidecar_jpeg_content_type_used_verbatim(self):
        records = {
            "img_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": _tmp_image_record(
                image_id="img_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                content_type="image/jpeg",
            ),
        }
        # Provide a non-PNG blob to confirm the MIME is NOT sniffed.
        jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"
        blobs = {"img_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": jpeg_bytes}
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            ["img_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"], store=store
        )

        assert out[0]["content_type"] == "image/jpeg"

    def test_empty_sidecar_mime_triggers_magic_byte_fallback(self):
        """Sidecar ``content_type`` is empty → magic-byte sniff fires.

        A3 contract #2: ``mime_recovered: true`` flag emitted so the
        caller can audit.
        """
        records = {
            "img_cccccccccccccccccccccccccccccc": _tmp_image_record(
                image_id="img_cccccccccccccccccccccccccccccc",
                content_type="",
            ),
        }
        blobs = {"img_cccccccccccccccccccccccccccccc": _PNG_BYTES}
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            ["img_cccccccccccccccccccccccccccccc"], store=store
        )

        assert out[0]["content_type"] == "image/png"
        assert out[0]["mime_recovered"] is True

    def test_unrecognised_magic_bytes_route_to_unavailable_path(self):
        """Empty sidecar MIME + unknown signature → unavailable_path envelope.

        A3 contract #3: NOT a recovery opportunity.
        """
        records = {
            "img_dddddddddddddddddddddddddddddd": _tmp_image_record(
                image_id="img_dddddddddddddddddddddddddddddd",
                content_type="",
            ),
        }
        # Bogus bytes that match no signature.
        blobs = {"img_dddddddddddddddddddddddddddddd": b"NOT_AN_IMAGE"}
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            ["img_dddddddddddddddddddddddddddddd"], store=store
        )

        assert out[0]["error"] == "unavailable_path"
        assert out[0]["reason"] == "unrecognized_mime"


# ── A4: 404 is structured ───────────────────────────────────────────────────


class TestNotFoundStructured:
    """A4 — every 404 mode produces a per-position error envelope."""

    def test_missing_blob_surfaces_unavailable_path(self):
        """``TmpImageNotFound`` → ``error: unavailable_path`` envelope."""
        from daemon.services.tmp_image_store import TmpImageNotFound

        store = _FakeStore(
            records={},
            open_full_error=TmpImageNotFound(
                "tmp image not found: missing_id"
            ),
        )

        out = resolve_data_uris(["missing_id"], store=store)

        assert out[0]["error"] == "unavailable_path"
        assert out[0]["image_id"] == "missing_id"
        assert out[0]["reason"] == "blob_missing"

    def test_sidecar_unreadable_routes_to_unreadable_reason(self):
        from daemon.services.tmp_image_store import TmpImageNotFound

        store = _FakeStore(
            records={},
            open_full_error=TmpImageNotFound(
                "tmp image sidecar unreadable: bad_sidecar (invalid json)"
            ),
        )

        out = resolve_data_uris(["bad_sidecar"], store=store)

        assert out[0]["error"] == "unavailable_path"
        assert out[0]["reason"] == "sidecar_unreadable"

    def test_sidecar_missing_routes_to_sidecar_missing_reason(self):
        from daemon.services.tmp_image_store import TmpImageNotFound

        store = _FakeStore(
            records={},
            open_full_error=TmpImageNotFound(
                "tmp image sidecar missing: missing_sidecar"
            ),
        )

        out = resolve_data_uris(["missing_sidecar"], store=store)

        assert out[0]["error"] == "unavailable_path"
        assert out[0]["reason"] == "sidecar_missing"


# ── A5: Size guard is per-image, pre-base64 ─────────────────────────────────


class TestSizeGuard:
    """A5 — size guard fires BEFORE base64 encoding; no partial work."""

    def test_oversized_image_surfaces_image_too_large_envelope(self):
        """``size_bytes > 20 MiB`` → ``image_too_large`` envelope."""
        records = {
            "huge_image_aaaaaaaaaaaaaaaaaaaaaaaa": _tmp_image_record(
                image_id="huge_image_aaaaaaaaaaaaaaaaaaaaaaaa",
                size_bytes=DEFAULT_SIZE_LIMIT_BYTES + 1,
            ),
        }
        blobs: dict[str, bytes] = {}  # No blob — the guard must NOT read it
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            ["huge_image_aaaaaaaaaaaaaaaaaaaaaaaa"], store=store
        )

        assert out[0]["error"] == "image_too_large"
        assert out[0]["size_bytes"] == DEFAULT_SIZE_LIMIT_BYTES + 1
        assert out[0]["size_limit_bytes"] == DEFAULT_SIZE_LIMIT_BYTES

    def test_at_the_limit_image_succeeds(self):
        """``size_bytes == 20 MiB`` exactly → success (boundary inclusive)."""
        records = {
            "at_limit_bbbbbbbbbbbbbbbbbbbbbbbbbb": _tmp_image_record(
                image_id="at_limit_bbbbbbbbbbbbbbbbbbbbbbbbbb",
                size_bytes=DEFAULT_SIZE_LIMIT_BYTES,
            ),
        }
        blobs = {
            "at_limit_bbbbbbbbbbbbbbbbbbbbbbbbbb": b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        }
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            ["at_limit_bbbbbbbbbbbbbbbbbbbbbbbbbb"], store=store
        )

        assert "data_uri" in out[0]


# ── A6 / A7: Provenance + retention class passthrough ───────────────────────


class TestProvenancePassthrough:
    """A6/A7 — provenance + retention_class pass through verbatim."""

    def test_full_provenance_passes_through_verbatim(self):
        provenance = {
            "feature": "checkout-flow",
            "page": "/cart",
            "version": "v1.2.0",
            "source_agent": "playwright-capture-1",
        }
        records = {
            "img_eeeeeeeeeeeeeeeeeeeeeeeeeeeeee": _tmp_image_record(
                image_id="img_eeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                provenance=provenance,
                retention_class="protected",
            ),
        }
        blobs = {"img_eeeeeeeeeeeeeeeeeeeeeeeeeeeeee": _PNG_BYTES}
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            ["img_eeeeeeeeeeeeeeeeeeeeeeeeeeeeee"], store=store
        )

        assert out[0]["provenance"] == provenance
        assert out[0]["retention_class"] == "protected"

    def test_null_provenance_passes_through_as_null(self):
        """Old sidecar with no provenance key → ``None`` propagates as
        ``None`` (A6)."""
        records = {
            "img_ffffffffffffffffffffffffffffff": _tmp_image_record(
                image_id="img_ffffffffffffffffffffffffffffff",
                provenance=None,
                retention_class="normal",
            ),
        }
        blobs = {"img_ffffffffffffffffffffffffffffff": _PNG_BYTES}
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            ["img_ffffffffffffffffffffffffffffff"], store=store
        )

        assert out[0]["provenance"] is None
        assert out[0]["retention_class"] == "normal"


# ── A8: No caching ──────────────────────────────────────────────────────────


class TestNoCaching:
    """A8 — every call re-fetches via ``store.open_full``; no in-memory cache."""

    def test_consecutive_calls_each_invoke_open_full(self):
        """Two consecutive resolve calls → ``open_full`` called twice.

        A8 + A9: no caching, no retry — each call goes through the
        store fresh.
        """
        records = {
            "img_gggggggggggggggggggggggggggggg": _tmp_image_record(
                image_id="img_gggggggggggggggggggggggggggggg",
            ),
        }
        blobs = {"img_gggggggggggggggggggggggggggggg": _PNG_BYTES}
        store = _FakeStore(records=records, blobs=blobs)

        spy = MagicMock(wraps=store.open_full)
        # Inject the spy on the fake store; the bridge calls
        # ``store.open_full`` directly.
        store.open_full = spy  # type: ignore[method-assign]

        # Two consecutive calls — bridge must NOT memoize.
        out1 = resolve_data_uris(
            ["img_gggggggggggggggggggggggggggggg"], store=store
        )
        out2 = resolve_data_uris(
            ["img_gggggggggggggggggggggggggggggg"], store=store
        )

        assert "data_uri" in out1[0]
        assert "data_uri" in out2[0]
        assert spy.call_count == 2


# ── A9: No retry ────────────────────────────────────────────────────────────


class TestNoRetry:
    """A9 — first ``TmpImageNotFound`` surfaces verbatim, no retry loop."""

    def test_first_failure_is_returned_not_retried(self):
        """``open_full`` raises once → the bridge surfaces the
        envelope without re-invoking ``open_full``."""
        from daemon.services.tmp_image_store import TmpImageNotFound

        store = _FakeStore(
            records={},
            open_full_error=TmpImageNotFound("tmp image not found: gone"),
        )

        spy = MagicMock(wraps=store.open_full)
        store.open_full = spy  # type: ignore[method-assign]

        out = resolve_data_uris(["gone"], store=store)

        assert out[0]["error"] == "unavailable_path"
        # ``open_full`` invoked exactly once — no retry.
        assert spy.call_count == 1


# ── Data URI shape ──────────────────────────────────────────────────────────


class TestDataUriShape:
    """The output ``data:`` URI prefix is byte-correct."""

    def test_data_uri_prefix_is_correct(self):
        records = {
            "img_hhhhhhhhhhhhhhhhhhhhhhhhhhhhhh": _tmp_image_record(
                image_id="img_hhhhhhhhhhhhhhhhhhhhhhhhhhhhhh",
                content_type="image/png",
            ),
        }
        blobs = {"img_hhhhhhhhhhhhhhhhhhhhhhhhhhhhhh": _PNG_BYTES}
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            ["img_hhhhhhhhhhhhhhhhhhhhhhhhhhhhhh"], store=store
        )

        # Decode the base64 portion and verify it round-trips.
        prefix = "data:image/png;base64,"
        assert out[0]["data_uri"].startswith(prefix)
        b64_part = out[0]["data_uri"][len(prefix):]
        decoded = base64.b64decode(b64_part)
        assert decoded == _PNG_BYTES

    def test_result_is_json_serializable(self):
        """All per-image result fields are JSON-serializable."""
        records = {
            "img_iiiiiiiiiiiiiiiiiiiiiiiiiiiiiii": _tmp_image_record(
                image_id="img_iiiiiiiiiiiiiiiiiiiiiiiiiiiiiii",
                provenance={"feature": "x", "page": "/y"},
                retention_class="normal",
            ),
        }
        blobs = {"img_iiiiiiiiiiiiiiiiiiiiiiiiiiiiiii": _PNG_BYTES}
        store = _FakeStore(records=records, blobs=blobs)

        out = resolve_data_uris(
            ["img_iiiiiiiiiiiiiiiiiiiiiiiiiiiiiii"], store=store
        )

        # JSON round-trip — every field survives.
        serialized = json.dumps(out[0])
        roundtrip = json.loads(serialized)
        assert roundtrip["image_id"] == "img_iiiiiiiiiiiiiiiiiiiiiiiiiiiiiii"
        assert roundtrip["content_type"] == "image/png"
        assert roundtrip["size_bytes"] == 1024
        assert roundtrip["provenance"] == {"feature": "x", "page": "/y"}
        assert roundtrip["retention_class"] == "normal"
