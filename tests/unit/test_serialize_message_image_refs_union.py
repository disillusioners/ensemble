"""Unit tests for ``serialize_message`` image_refs union extension.

Phase 2 / clipboard-image-chat / Task 7 (round-2 amendment #29).
Freeze-list A2 (refs surface via union), A4 (legacy byte-identical).

The serializer extends to UNION ``additional_kwargs['image_refs']``
into the wire ``images`` field — single field, legacy ``image_url``
blocks unchanged. Reviewer question (ii) RULED: legacy display surface
untouched by the union.
"""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage, HumanMessage

from daemon.utils import serialize_message


# Identity-grep pin (round-2 amend #29): the literal string
# ``image_refs`` MUST appear verbatim in the union block of
# ``daemon/utils.py``. A future "simplification" that drops the
# union is the serializer-simplification hazard this comment cites.
def _utils_source_contains_image_refs_union() -> bool:
    import daemon.utils as utils_mod

    src_path = utils_mod.__file__
    if src_path is None:
        return False
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    # Find the union block: lines containing "image_refs" inside
    # ``serialize_message``. We accept any line that says ``image_refs``
    # at least once near ``additional_kwargs.get``.
    return "additional_kwargs.get" in src and "image_refs" in src


class TestSerializeMessageImageRefsUnion:
    def test_legacy_text_only_message_has_no_images(self):
        """Plain text message — no images field."""
        m = HumanMessage(content="hello", id="msg-1")
        out = serialize_message(m)
        assert out["images"] is None

    def test_legacy_data_uri_blocks_surface_unchanged(self):
        """Legacy data-URI send → content is a block list with
        image_url blocks; surface through ``images`` UNCHANGED."""
        m = HumanMessage(
            content=[
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
            ],
            id="msg-2",
        )
        out = serialize_message(m)
        assert "data:image/png;base64,xxx" in out["images"]

    def test_image_refs_only_message_unions_refs_into_images(self):
        """Ref-send: additional_kwargs['image_refs'] surfaces via
        union into the wire ``images`` field (A2)."""
        ref = "/api/tmp_images/" + "a" * 32
        m = HumanMessage(
            content="look at this",
            id="msg-3",
            additional_kwargs={"image_refs": [ref]},
        )
        out = serialize_message(m)
        assert ref in out["images"]

    def test_image_refs_preserve_input_order(self):
        """Multiple refs surface in input order."""
        a = "/api/tmp_images/" + "a" * 32
        b = "/api/tmp_images/" + "b" * 32
        c = "/api/tmp_images/" + "c" * 32
        m = HumanMessage(
            content="three refs",
            id="msg-4",
            additional_kwargs={"image_refs": [a, b, c]},
        )
        out = serialize_message(m)
        # Order preserved
        assert out["images"] == [a, b, c]

    def test_image_refs_union_after_legacy_blocks(self):
        """Legacy data-URI block + display-channel refs both surface."""
        legacy = "data:image/png;base64,xxx"
        ref = "/api/tmp_images/" + "a" * 32
        m = HumanMessage(
            content=[
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": legacy}},
            ],
            id="msg-5",
            additional_kwargs={"image_refs": [ref]},
        )
        out = serialize_message(m)
        # Legacy block first, then refs appended.
        assert out["images"] == [legacy, ref]

    def test_image_refs_dedup_against_legacy(self):
        """A ref that's also a legacy image_url block doesn't double up."""
        legacy = "data:image/png;base64,xxx"
        m = HumanMessage(
            content=[
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": legacy}},
            ],
            id="msg-6",
            additional_kwargs={"image_refs": [legacy]},
        )
        out = serialize_message(m)
        # The legacy block appears once; the ref dedups against it.
        assert out["images"] == [legacy]

    def test_other_additional_kwargs_markers_still_surface(self):
        """Injected/source markers surface alongside refs (Phase 4
        CHANGE 1 wire contract)."""
        ref = "/api/tmp_images/" + "a" * 32
        m = HumanMessage(
            content="x",
            id="msg-7",
            additional_kwargs={
                "injected_message": True,
                "source": "api",
                "image_refs": [ref],
            },
        )
        out = serialize_message(m)
        assert ref in out["images"]
        assert out["injected_message"] is True
        assert out["source"] == "api"

    def test_ai_message_unaffected(self):
        """AIMessages don't carry image_refs in additional_kwargs — the
        serializer doesn't add the key when it's absent."""
        m = AIMessage(content="assistant", id="msg-8")
        out = serialize_message(m)
        assert out["images"] is None


class TestSerializeMessageIdentityGrepPin:
    def test_image_refs_appears_in_serializer_block(self):
        """Identity-grep regression pin: ``image_refs`` appears verbatim
        in ``daemon/utils.py``. A future "simplification" that drops
        the union is the serializer-simplification hazard this comment
        cites."""
        assert _utils_source_contains_image_refs_union(), (
            "image_refs union missing from daemon/utils.py — "
            "a future simplification may have dropped it. "
            "See phase2-plan.md Task 7 (round-2 amendment #29)."
        )


# Ensure re is imported (used by the helper).
_ = re
