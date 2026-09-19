"""Unit tests for :class:`TmpImageConverter` (Phase 2 / Task 1).

Validates:

* Happy-path conversion (image-reader returns a description string).
* Vanished-file case (TmpImageNotFound) — graceful placeholder.
* Defensive OSError catch — graceful placeholder.
* Error-STRING collapse (architect amendment #9): ``invoke_agent_and_wait``
  returns ``"Error: ..."`` strings on timeout / agent error — the
  converter MUST collapse via ``not str(result).startswith("Error")``.
* None / empty-string result — graceful placeholder.
* Default question is the legacy ``explain_image`` default.
* Empty input returns ``[]``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.tmp_image_converter import (
    DEFAULT_CONVERSION_QUESTION,
    TMP_IMAGE_CONVERSION_TIMEOUT_S,
    ConvertedImage,
    TmpImageConverter,
)
from daemon.services.tmp_image_store import (
    TmpImageNotFound,
    TmpImageStore,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _store_with_bytes(image_id: str, content: bytes, content_type: str):
    """Build a TmpImageStore stand-in with one stored image."""
    store = TmpImageStore.__new__(TmpImageStore)
    store._data_dir = None  # not used — open() is mocked below
    store.open = MagicMock(return_value=(content, content_type))
    return store


def _store_vanished():
    """Build a TmpImageStore stand-in whose open() raises TmpImageNotFound."""
    store = TmpImageStore.__new__(TmpImageStore)
    store._data_dir = None
    store.open = MagicMock(side_effect=TmpImageNotFound("not in store"))
    return store


def _store_oserror():
    """Build a TmpImageStore stand-in whose open() raises OSError."""
    store = TmpImageStore.__new__(TmpImageStore)
    store._data_dir = None
    store.open = MagicMock(side_effect=OSError("disk error"))
    return store


def _manager_with_invoke(result):
    """Build a manager stand-in whose invoke_agent_and_wait returns ``result``."""
    mgr = MagicMock()
    mgr._patch_invoke = None  # placeholder — set on the module below
    return mgr


# We monkey-patch the converter's call to invoke_agent_and_wait so we
# don't need a real LLM. The cleanest seam is to patch the module
# attribute that the converter imported.
@pytest.fixture
def patch_invoke(monkeypatch):
    """Return a function that overrides the converter's invoke call."""

    def _patch(result):
        async def _stub(*args, **kwargs):
            return result

        monkeypatch.setattr(
            "daemon.services.tmp_image_converter.invoke_agent_and_wait",
            _stub,
        )

    return _patch


# ---------------------------------------------------------------------------
# Group 1 — module constants
# ---------------------------------------------------------------------------


class TestConverterConstants:
    def test_timeout_is_90s(self):
        # Architect amendment #8 — converter-owned budget, NOT the
        # image_tools.py:521 300s literal.
        assert TMP_IMAGE_CONVERSION_TIMEOUT_S == 90.0

    def test_default_question_matches_explain_image(self):
        # Same default as the proven explain_image tool.
        assert DEFAULT_CONVERSION_QUESTION == "Describe this image in detail."


# ---------------------------------------------------------------------------
# Group 2 — happy path
# ---------------------------------------------------------------------------


class TestConverterHappyPath:
    async def test_single_ref_returns_description(self, patch_invoke):
        store = _store_with_bytes("a" * 32, b"hi", "image/png")
        mgr = _manager_with_invoke("a yellow flower")
        patch_invoke("a yellow flower")

        converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
        refs = [f"/api/tmp_images/{'a' * 32}"]
        results = await converter.convert_refs(refs)

        assert len(results) == 1
        r = results[0]
        assert isinstance(r, ConvertedImage)
        assert r.ref == refs[0]
        assert r.description == "a yellow flower"
        assert r.ok is True

    async def test_three_refs_all_succeed(self, patch_invoke):
        store = _store_with_bytes("a" * 32, b"hi", "image/png")
        mgr = _manager_with_invoke("desc")
        patch_invoke("desc")

        converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
        refs = [
            f"/api/tmp_images/{'a' * 32}",
            f"/api/tmp_images/{'b' * 32}",
            f"/api/tmp_images/{'c' * 32}",
        ]
        results = await converter.convert_refs(refs)

        assert len(results) == 3
        assert all(r.ok for r in results)
        assert all(r.description == "desc" for r in results)

    async def test_input_order_preserved_on_mixed_results(self, patch_invoke):
        """Mixed ok + failure: returned list aligns 1:1 with input refs."""
        # 3 refs, 2 ok + 1 vanished.
        ok_store = _store_with_bytes("a" * 32, b"hi", "image/png")
        # The middle ref opens OK; the first 2 also OK; the 3rd vanished.
        call_log = []

        def _open(image_id: str):
            call_log.append(image_id)
            if image_id == "c" * 32:
                raise TmpImageNotFound("vanished")
            return (b"hi", "image/png")

        ok_store.open = MagicMock(side_effect=_open)
        mgr = _manager_with_invoke("desc")
        patch_invoke("desc")

        converter = TmpImageConverter(manager=mgr, tmp_image_store=ok_store)
        refs = [
            f"/api/tmp_images/{'a' * 32}",
            f"/api/tmp_images/{'b' * 32}",
            f"/api/tmp_images/{'c' * 32}",
        ]
        results = await converter.convert_refs(refs)

        assert len(results) == 3
        assert results[0].ok is True
        assert results[1].ok is True
        assert results[2].ok is False
        assert results[2].description == ""


# ---------------------------------------------------------------------------
# Group 3 — failure paths (architect amendment #9)
# ---------------------------------------------------------------------------


class TestConverterFailurePaths:
    async def test_vanished_file_returns_placeholder(self, patch_invoke):
        store = _store_vanished()
        mgr = _manager_with_invoke("never reached")  # should NOT be invoked
        patch_invoke("never reached")

        converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
        refs = [f"/api/tmp_images/{'a' * 32}"]
        results = await converter.convert_refs(refs)

        assert len(results) == 1
        assert results[0].ok is False
        assert results[0].description == ""

    async def test_oserror_returns_placeholder(self, patch_invoke):
        store = _store_oserror()
        mgr = _manager_with_invoke("never reached")
        patch_invoke("never reached")

        converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
        results = await converter.convert_refs([f"/api/tmp_images/{'a' * 32}"])

        assert results[0].ok is False
        assert results[0].description == ""

    async def test_timeout_string_returns_placeholder(self, patch_invoke):
        """Architect amendment #9: invoke_agent_and_wait returns
        ``"Error: Agent timed out..."`` STRING — must collapse."""
        store = _store_with_bytes("a" * 32, b"hi", "image/png")
        mgr = _manager_with_invoke(
            "Error: Agent timed out after 90s. Instance abc... may still be running."
        )
        patch_invoke(
            "Error: Agent timed out after 90s. Instance abc... may still be running."
        )

        converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
        results = await converter.convert_refs([f"/api/tmp_images/{'a' * 32}"])

        assert results[0].ok is False
        assert results[0].description == ""

    async def test_agent_error_string_returns_placeholder(self, patch_invoke):
        """Architect amendment #9: invoke_agent_and_wait returns
        ``"Error: Agent failed..."`` STRING — must collapse."""
        store = _store_with_bytes("a" * 32, b"hi", "image/png")
        mgr = _manager_with_invoke("Error: Agent failed. spawn refused")
        patch_invoke("Error: Agent failed. spawn refused")

        converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
        results = await converter.convert_refs([f"/api/tmp_images/{'a' * 32}"])

        assert results[0].ok is False
        assert results[0].description == ""

    async def test_none_result_returns_placeholder(self, patch_invoke):
        store = _store_with_bytes("a" * 32, b"hi", "image/png")
        mgr = _manager_with_invoke(None)
        patch_invoke(None)

        converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
        results = await converter.convert_refs([f"/api/tmp_images/{'a' * 32}"])

        assert results[0].ok is False
        assert results[0].description == ""

    async def test_empty_string_returns_placeholder(self, patch_invoke):
        store = _store_with_bytes("a" * 32, b"hi", "image/png")
        mgr = _manager_with_invoke("")
        patch_invoke("")

        converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
        results = await converter.convert_refs([f"/api/tmp_images/{'a' * 32}"])

        assert results[0].ok is False
        assert results[0].description == ""

    async def test_unexpected_exception_returns_placeholder(self, patch_invoke):
        """Last-resort guard: any exception collapses to placeholder,
        never propagates to the caller (per-image one-shot)."""

        async def _boom(*args, **kwargs):
            raise RuntimeError("unexpected boom")

        import daemon.services.tmp_image_converter as conv_mod

        # Override the import the converter used.
        original = conv_mod.invoke_agent_and_wait
        conv_mod.invoke_agent_and_wait = _boom
        try:
            store = _store_with_bytes("a" * 32, b"hi", "image/png")
            mgr = _manager_with_invoke("never reached")
            converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
            results = await converter.convert_refs([f"/api/tmp_images/{'a' * 32}"])
        finally:
            conv_mod.invoke_agent_and_wait = original

        assert results[0].ok is False
        assert results[0].description == ""


# ---------------------------------------------------------------------------
# Group 4 — empty / edge inputs
# ---------------------------------------------------------------------------


class TestConverterEmptyAndEdgeInputs:
    async def test_empty_refs_returns_empty_list(self, patch_invoke):
        store = _store_with_bytes("a" * 32, b"hi", "image/png")
        mgr = _manager_with_invoke("never reached")
        patch_invoke("never reached")

        converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
        result = await converter.convert_refs([])
        assert result == []

    async def test_malformed_ref_treated_as_failure(self, patch_invoke):
        """Defensive belt: a malformed ref (not matching the validator's
        regex) is treated as a failure by the converter."""
        store = _store_with_bytes("a" * 32, b"hi", "image/png")
        mgr = _manager_with_invoke("never reached")
        patch_invoke("never reached")

        converter = TmpImageConverter(manager=mgr, tmp_image_store=store)
        results = await converter.convert_refs(["not-a-valid-ref"])

        assert len(results) == 1
        assert results[0].ok is False
        assert results[0].ref == "not-a-valid-ref"
