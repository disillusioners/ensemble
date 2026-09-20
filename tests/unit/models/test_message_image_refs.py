"""Unit tests for the ``image_refs`` field on :class:`MessageCreate`.

Phase 2 / clipboard-image-chat / Task 3 + freeze-list A5.

Validates the C3-fixed regex (round 2) accepts all three canonical
input forms — bare 32-hex, ``tmpimg://<32hex>``, and the canonical
``/api/tmp_images/<32hex>`` URL form — and rejects malformed inputs.
Pins the XOR invariant with the legacy ``images`` field.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daemon.models.message import (
    MessageCreate,
    _IMAGE_REF_PATTERN,
    _TMP_IMAGE_CANONICAL_URL_PREFIX,
    normalize_image_ref_to_canonical_url,
)


_VALID_HEX_32 = "a" * 32


# ---------------------------------------------------------------------------
# Group 1 — all 3 canonical input forms accepted
# ---------------------------------------------------------------------------


class TestImageRefsValidForms:
    """The C3 regex accepts every canonical input form."""

    def test_bare_32_hex_accepted(self):
        m = MessageCreate(content="hi", image_refs=[_VALID_HEX_32])
        assert m.image_refs == [_VALID_HEX_32]

    def test_tmpimg_alias_accepted(self):
        ref = f"tmpimg://{_VALID_HEX_32}"
        m = MessageCreate(content="hi", image_refs=[ref])
        assert m.image_refs == [ref]

    def test_canonical_url_form_accepted(self):
        ref = f"/api/tmp_images/{_VALID_HEX_32}"
        m = MessageCreate(content="hi", image_refs=[ref])
        assert m.image_refs == [ref]

    def test_all_three_forms_in_one_request(self):
        # Mix of all three canonical input forms in a single request
        # is allowed (the validator only checks each entry, not the
        # cross-entry shape).
        refs = [
            _VALID_HEX_32,
            f"tmpimg://{'b' * 32}",
            f"/api/tmp_images/{'c' * 32}",
        ]
        m = MessageCreate(content="hi", image_refs=refs)
        assert m.image_refs == refs


# ---------------------------------------------------------------------------
# Group 2 — rejection paths
# ---------------------------------------------------------------------------


class TestImageRefsRejections:
    """Malformed inputs are rejected with descriptive errors."""

    def test_too_many_refs_rejected(self):
        with pytest.raises(ValidationError) as ei:
            MessageCreate(
                content="hi",
                image_refs=[_VALID_HEX_32, "b" * 32, "c" * 32, "d" * 32],
            )
        assert "Maximum 3 image_refs" in str(ei.value)

    def test_empty_list_coerced_to_none(self):
        # Same convention as images: empty list == absent.
        m = MessageCreate(content="hi", image_refs=[])
        assert m.image_refs is None

    def test_invalid_form_rejected(self):
        with pytest.raises(ValidationError) as ei:
            MessageCreate(content="hi", image_refs=["not-a-valid-ref"])
        assert "Invalid image_ref at index 0" in str(ei.value)

    def test_uppercase_hex_rejected(self):
        # The regex is lowercase-only — uppercase hex would route to
        # the GET path which itself rejects (regex is 32-hex lowercase).
        with pytest.raises(ValidationError) as ei:
            MessageCreate(content="hi", image_refs=["A" * 32])
        assert "Invalid image_ref" in str(ei.value)

    def test_too_short_hex_rejected(self):
        with pytest.raises(ValidationError) as ei:
            MessageCreate(content="hi", image_refs=["a" * 31])
        assert "Invalid image_ref" in str(ei.value)


# ---------------------------------------------------------------------------
# Group 3 — XOR with images (freeze-list A5)
# ---------------------------------------------------------------------------


class TestImageRefsXorWithImages:
    """A request carrying BOTH images and image_refs is rejected."""

    def test_both_non_empty_rejected(self):
        with pytest.raises(ValidationError) as ei:
            MessageCreate(
                content="hi",
                images=["data:image/png;base64,abcdefghij"],
                image_refs=[_VALID_HEX_32],
            )
        assert "at most one image channel" in str(ei.value)

    def test_only_images_allowed(self):
        m = MessageCreate(
            content="hi",
            images=["data:image/png;base64,abcdefghij"],
        )
        assert m.image_refs is None
        assert m.images is not None

    def test_only_image_refs_allowed(self):
        m = MessageCreate(content="hi", image_refs=[_VALID_HEX_32])
        assert m.images is None
        assert m.image_refs == [_VALID_HEX_32]

    def test_neither_field_rejected_as_xor(self):
        # Empty/no-channel is fine — XOR is "at most one".
        m = MessageCreate(content="hi")
        assert m.images is None
        assert m.image_refs is None


# ---------------------------------------------------------------------------
# Group 4 — normalize_image_ref_to_canonical_url
# ---------------------------------------------------------------------------


class TestNormalizeImageRefToCanonicalUrl:
    """All 3 accepted forms normalize to /api/tmp_images/<32hex>."""

    def test_bare_normalized(self):
        assert (
            normalize_image_ref_to_canonical_url(_VALID_HEX_32)
            == f"/api/tmp_images/{_VALID_HEX_32}"
        )

    def test_tmpimg_alias_normalized(self):
        assert (
            normalize_image_ref_to_canonical_url(f"tmpimg://{_VALID_HEX_32}")
            == f"/api/tmp_images/{_VALID_HEX_32}"
        )

    def test_canonical_url_passthrough(self):
        canonical = f"/api/tmp_images/{_VALID_HEX_32}"
        assert (
            normalize_image_ref_to_canonical_url(canonical) == canonical
        )

    def test_canonical_prefix_constant(self):
        # Identity-pin: the canonical prefix MUST be the literal
        # "/api/tmp_images/" — a future "rename to /tmp_images/"
        # would break this and every FE consumer.
        assert _TMP_IMAGE_CANONICAL_URL_PREFIX == "/api/tmp_images/"


# ---------------------------------------------------------------------------
# Group 5 — regex shape (regression pin against future drift)
# ---------------------------------------------------------------------------


class TestImageRefsRegexShape:
    """Pin the C3 regex shape so a future 'simplification' trips the test."""

    def test_pattern_accepts_all_three_forms(self):
        # Direct regex pin (in case the validator is refactored).
        assert _IMAGE_REF_PATTERN.match(_VALID_HEX_32)
        assert _IMAGE_REF_PATTERN.match(f"tmpimg://{_VALID_HEX_32}")
        assert _IMAGE_REF_PATTERN.match(f"/api/tmp_images/{_VALID_HEX_32}")

    def test_pattern_rejects_garbage(self):
        assert not _IMAGE_REF_PATTERN.match("not-a-ref")
        assert not _IMAGE_REF_PATTERN.match("data:image/png;base64,xxx")
        assert not _IMAGE_REF_PATTERN.match("/other/abcdef" + "0" * 25)
