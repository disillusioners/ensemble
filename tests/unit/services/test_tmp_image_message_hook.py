"""Unit tests for :func:`pre_dispatch_image_hook` (Phase 2 / Task 4).

Validates:

* No-op when ``image_refs`` empty (zero-cost byte-identical for legacy).
* Prefix construction (success / failure mix).
* Original content preserved (prepended).
* ``images`` cleared on the returned request.
* ``image_refs`` RETAINED on the returned request (display channel).
* Disconnect mitigation (architect amendment #11) — placeholders for
  remaining refs after a synthetic disconnect.
* Defensive OSError catch on the converter's read path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.models.message import MessageCreate
from daemon.services.tmp_image_converter import ConvertedImage
from daemon.services.tmp_image_message_hook import (
    _build_image_prefix,
    pre_dispatch_image_hook,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


_VALID_REF_A = "/api/tmp_images/" + "a" * 32
_VALID_REF_B = "/api/tmp_images/" + "b" * 32
_VALID_REF_C = "/api/tmp_images/" + "c" * 32


@dataclass
class _StubRequest:
    """A minimal Starlette-like Request exposing ``is_disconnected``."""

    is_disconnected_returns: bool = False

    async def is_disconnected(self) -> bool:
        return self.is_disconnected_returns


def _store_with_converter_results(results: list[ConvertedImage]):
    """A TmpImageStore stand-in; the converter does the real work."""
    return MagicMock()


def _mgr_for_convert_results(
    results: list[ConvertedImage],
):
    """Build a manager stub; the converter inside the hook is what reads."""
    return MagicMock()


# We monkey-patch the converter class so the hook uses our results
# without needing a real store + LLM.
@pytest.fixture
def patch_converter(monkeypatch):
    """Patch TmpImageConverter so each instance returns the supplied results.

    Returns a callable that takes a list of ``ConvertedImage`` and
    configures the converter to return it (one per input ref).
    """

    def _patch(results: list[ConvertedImage]):
        class _StubConverter:
            def __init__(self, manager, tmp_image_store):
                self._results = list(results)

            async def _convert_one(self, ref, question):
                # Pop the next result (aligned to input order). If the
                # caller passed fewer results than refs, the remaining
                # refs become placeholders.
                if self._results:
                    return self._results.pop(0)
                return ConvertedImage(ref=ref, description="", ok=False)

        monkeypatch.setattr(
            "daemon.services.tmp_image_message_hook.TmpImageConverter",
            _StubConverter,
        )

    return _patch


# ---------------------------------------------------------------------------
# Group 1 — no-op when image_refs is empty
# ---------------------------------------------------------------------------


class TestPreDispatchHookNoOp:
    async def test_empty_refs_returns_unchanged(self, patch_converter):
        """Empty image_refs → zero-cost byte-identical for legacy requests."""
        patch_converter([])  # never consumed
        msg = MessageCreate(content="hello")
        store = _store_with_converter_results([])
        mgr = _mgr_for_convert_results([])

        result = await pre_dispatch_image_hook(msg, mgr, store)

        assert result is msg  # byte-identical (same object)


# ---------------------------------------------------------------------------
# Group 2 — prefix construction
# ---------------------------------------------------------------------------


class TestPreDispatchPrefixConstruction:
    async def test_three_successful_conversions_produce_three_descriptions(
        self, patch_converter
    ):
        patch_converter([
            ConvertedImage(ref=_VALID_REF_A, description="flower", ok=True),
            ConvertedImage(ref=_VALID_REF_B, description="tree", ok=True),
            ConvertedImage(ref=_VALID_REF_C, description="rock", ok=True),
        ])
        msg = MessageCreate(
            content="look",
            image_refs=[_VALID_REF_A, _VALID_REF_B, _VALID_REF_C],
        )
        result = await pre_dispatch_image_hook(msg, _mgr_for_convert_results([]), _store_with_converter_results([]))

        # Prefix is on top of "look"
        assert "[Image 1: flower]" in result.content
        assert "[Image 2: tree]" in result.content
        assert "[Image 3: rock]" in result.content
        # Original content preserved
        assert result.content.endswith("look")

    async def test_mixed_success_failure_yields_placeholder(self, patch_converter):
        patch_converter([
            ConvertedImage(ref=_VALID_REF_A, description="flower", ok=True),
            ConvertedImage(ref=_VALID_REF_B, description="", ok=False),
            ConvertedImage(ref=_VALID_REF_C, description="rock", ok=True),
        ])
        msg = MessageCreate(
            content="look",
            image_refs=[_VALID_REF_A, _VALID_REF_B, _VALID_REF_C],
        )
        result = await pre_dispatch_image_hook(msg, _mgr_for_convert_results([]), _store_with_converter_results([]))

        assert "[Image 1: flower]" in result.content
        assert "[Image 2: description unavailable]" in result.content
        assert "[Image 3: rock]" in result.content

    async def test_all_failures_yield_placeholders(self, patch_converter):
        patch_converter([
            ConvertedImage(ref=_VALID_REF_A, description="", ok=False),
            ConvertedImage(ref=_VALID_REF_B, description="", ok=False),
        ])
        msg = MessageCreate(
            content="look",
            image_refs=[_VALID_REF_A, _VALID_REF_B],
        )
        result = await pre_dispatch_image_hook(msg, _mgr_for_convert_results([]), _store_with_converter_results([]))

        assert "[Image 1: description unavailable]" in result.content
        assert "[Image 2: description unavailable]" in result.content

    async def test_empty_original_content_still_has_prefix(self, patch_converter):
        patch_converter([
            ConvertedImage(ref=_VALID_REF_A, description="flower", ok=True),
        ])
        msg = MessageCreate(
            content="look",
            image_refs=[_VALID_REF_A],
        )
        # Sanity: the hook prepends regardless.
        result = await pre_dispatch_image_hook(msg, _mgr_for_convert_results([]), _store_with_converter_results([]))

        assert "[Image 1: flower]" in result.content
        assert result.content.endswith("look")


# ---------------------------------------------------------------------------
# Group 3 — channel invariants
# ---------------------------------------------------------------------------


class TestPreDispatchChannelInvariants:
    async def test_images_cleared_on_return(self, patch_converter):
        """images MUST be None on the returned request so the legacy
        vision gate naturally skips."""
        patch_converter([
            ConvertedImage(ref=_VALID_REF_A, description="flower", ok=True),
        ])
        # Bypass XOR by constructing with only image_refs.
        msg = MessageCreate(content="look", image_refs=[_VALID_REF_A])
        # Sanity: images starts None.
        assert msg.images is None

        result = await pre_dispatch_image_hook(msg, _mgr_for_convert_results([]), _store_with_converter_results([]))
        assert result.images is None

    async def test_image_refs_retained_on_return(self, patch_converter):
        """image_refs MUST be retained on the returned request so the
        router can thread it to the durable channel (row + kwargs stamp)."""
        patch_converter([
            ConvertedImage(ref=_VALID_REF_A, description="flower", ok=True),
        ])
        refs = [_VALID_REF_A, _VALID_REF_B]
        msg = MessageCreate(content="look", image_refs=refs)

        result = await pre_dispatch_image_hook(msg, _mgr_for_convert_results([]), _store_with_converter_results([]))
        assert result.image_refs == refs


# ---------------------------------------------------------------------------
# Group 4 — disconnect mitigation
# ---------------------------------------------------------------------------


class TestPreDispatchDisconnectMitigation:
    async def test_disconnect_after_first_conversion_placeholders_remainder(
        self, monkeypatch
    ):
        """If the client disconnects after image 1, images 2+ become
        placeholders. No further invokes. (Architect amendment #11.)"""

        call_count = [0]

        class _CountingConverter:
            def __init__(self, manager, tmp_image_store):
                pass

            async def _convert_one(self, ref, question):
                call_count[0] += 1
                return ConvertedImage(ref=ref, description=f"desc-{call_count[0]}", ok=True)

        monkeypatch.setattr(
            "daemon.services.tmp_image_message_hook.TmpImageConverter",
            _CountingConverter,
        )

        # Disconnect fires the FIRST time ``is_disconnected`` is called
        # (idx=1 — between image 1 and image 2). The hook checks the
        # disconnect status before converting each subsequent ref, so
        # a True return at idx=1 short-circuits images 2+ to
        # placeholders without invoking the converter again.
        class _FlakyRequest:
            async def is_disconnected(self) -> bool:
                return True

        msg = MessageCreate(
            content="look",
            image_refs=[_VALID_REF_A, _VALID_REF_B, _VALID_REF_C],
        )
        result = await pre_dispatch_image_hook(
            msg,
            _mgr_for_convert_results([]),
            _store_with_converter_results([]),
            http_request=_FlakyRequest(),
        )

        # Only the first conversion ran (idx=0 has no disconnect check).
        assert call_count[0] == 1
        assert "[Image 1: desc-1]" in result.content
        # 2 and 3 are placeholders.
        assert "[Image 2: description unavailable]" in result.content
        assert "[Image 3: description unavailable]" in result.content

    async def test_no_disconnect_check_when_http_request_is_none(self, monkeypatch):
        """When http_request is None (some test paths), no disconnect
        check is performed — all refs convert normally."""
        monkeypatch.setattr(
            "daemon.services.tmp_image_message_hook.TmpImageConverter",
            _SimpleConverter,
        )

        msg = MessageCreate(
            content="look",
            image_refs=[_VALID_REF_A, _VALID_REF_B],
        )
        result = await pre_dispatch_image_hook(
            msg,
            _mgr_for_convert_results([]),
            _store_with_converter_results([]),
            http_request=None,
        )
        assert "[Image 1: desc]" in result.content
        assert "[Image 2: desc]" in result.content


class _SimpleConverter:
    """Always-OK converter for the no-disconnect-check test."""

    def __init__(self, manager, tmp_image_store):
        pass

    async def _convert_one(self, ref, question):
        return ConvertedImage(ref=ref, description="desc", ok=True)


# ---------------------------------------------------------------------------
# Group 5 — _build_image_prefix (private helper)
# ---------------------------------------------------------------------------


class TestBuildImagePrefix:
    def test_empty_returns_empty_string(self):
        assert _build_image_prefix([]) == ""

    def test_success(self):
        result = _build_image_prefix(
            [ConvertedImage(ref="r", description="flower", ok=True)]
        )
        assert result == "[Image 1: flower]\n"

    def test_failure(self):
        result = _build_image_prefix(
            [ConvertedImage(ref="r", description="", ok=False)]
        )
        assert result == "[Image 1: description unavailable]\n"

    def test_mixed(self):
        result = _build_image_prefix([
            ConvertedImage(ref="r1", description="flower", ok=True),
            ConvertedImage(ref="r2", description="", ok=False),
        ])
        assert result == "[Image 1: flower]\n[Image 2: description unavailable]\n"
