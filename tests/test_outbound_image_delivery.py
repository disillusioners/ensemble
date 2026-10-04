"""End-to-end tests for Phase B chart-image delivery (Phase B.5 tasks #40, #41, #42).

Uses a ``MockSourceAdapter`` + a fake ``tmp_image_store`` populated with a
synthetic 1×1 PNG. The dispatcher routes the message through the mock
adapter; the assertions cover:
  * BOTH-seam extraction (progressive + completed)
  * Image arrives (OutgoingMessage.images populated) + text marker-stripped
  * store.delete fired exactly once after successful chat delivery
  * API-source (no-colon) keeps marker verbatim + open_with_meta NOT called
"""

from __future__ import annotations

import base64
import logging
import os
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from daemon.sources.base import (
    ImageAttachment,
    OutgoingMessage,
    SourceConfig,
)
from daemon.sources.dispatcher import ResponseDispatcher
from daemon.services.event_bus import EventBus
from daemon.services.tmp_image_store import TmpImageRecord


# A minimal 1×1 PNG (76 bytes) — small enough for tests but well-formed
# enough to round-trip through base64. The actual contents don't matter for
# these tests (we only verify the bytes-roundtrip property).
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"  # signature
    b"\x00\x00\x00\rIHDR"  # IHDR length + name
    b"\x00\x00\x00\x01\x00\x00\x00\x01"  # 1x1
    b"\x08\x06\x00\x00\x00"  # bit depth / color type
    b"\x1f\x15\xc4\x89"  # CRC
    b"\x00\x00\x00\rIDATx"  # IDAT length + name + x
    b"\x9c\x62\x62\x60\x60\x60\x60\x60\x60\x60\x60\x60"  # data
    b"\x00\x00\x00\x05\x00\x01\x0d\x0a\x2d\xb4"  # end + CRC
    b"\x00\x00\x00\x00IEND"  # IEND
    b"\xaeB`\x82"  # CRC
)
assert len(PNG_BYTES) >= 64, f"Test PNG too small: {len(PNG_BYTES)} bytes"


@pytest.fixture
def mock_image_id() -> str:
    """A 32-hex image_id (regex-validated by the dispatcher)."""
    return "0" * 32


@pytest.fixture
def fake_tmp_image_store(mock_image_id):
    """Fake ``tmp_image_store`` carrying a tiny PNG with chart-render provenance.

    Exposes:
      * open_full(image_id) → TmpImageRecord (provenance + metadata only)
      * open_with_meta(image_id) → (bytes, content_type, sha256_hex)
      * delete(image_id) → bool (records calls for assertions)
    """
    record = TmpImageRecord(
        image_id=mock_image_id,
        content_type="image/png",
        size_bytes=len(PNG_BYTES),
        uploaded_at="2026-10-04T00:00:00+00:00",
        sha256_hex="0" * 64,
        provenance={"feature": "chart-render"},
        retention_class="normal",
    )

    class _FakeStore:
        def __init__(self):
            self.delete_calls: list[str] = []
            self.open_full_calls: list[str] = []
            self.open_with_meta_calls: list[str] = []

        def open_full(self, image_id):
            self.open_full_calls.append(image_id)
            if image_id != mock_image_id:
                raise KeyError(f"unknown image_id: {image_id}")
            return record

        def open_with_meta(self, image_id):
            self.open_with_meta_calls.append(image_id)
            if image_id != mock_image_id:
                raise KeyError(f"unknown image_id: {image_id}")
            return PNG_BYTES, "image/png", "0" * 64

        def delete(self, image_id):
            self.delete_calls.append(image_id)
            return True

    return _FakeStore()


@pytest.fixture
def event_bus():
    mock_repo = MagicMock()
    mock_repo.create_event = Mock()
    mock_repo.cleanup_old = Mock(return_value=0)
    return EventBus(event_repo=mock_repo)


class TestE2EChartImageDelivery:
    """End-to-end via mock source + fake tmp_image_store."""

    @pytest.mark.asyncio
    async def test_progressive_lane_e2e_marker_extracted_and_stripped(
        self, fake_tmp_image_store, mock_image_id
    ):
        """Phase B Task #40: dispatch_message extracts + strips + uploads."""
        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=True)
        adapter.sent_messages: list[OutgoingMessage] = []

        manager = Mock()
        manager.tmp_image_store = fake_tmp_image_store

        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="e2e-test")
        await dispatcher.start()

        content = (
            f"Here is your chart\n"
            f"<!-- ens-img:chart-render:{mock_image_id} -->\n"
            f"hope you like it"
        )
        await dispatcher.dispatch_message(
            source="discord:user1", content=content
        )

        # adapter.send called with marker-stripped content + populated images.
        assert adapter.send.await_count == 1
        outgoing = adapter.send.await_args.args[0]
        assert "ens-img" not in outgoing.content
        assert "Here is your chart" in outgoing.content
        assert outgoing.images is not None
        assert len(outgoing.images) == 1
        assert outgoing.images[0].image_id == mock_image_id

        # Bytes decode to the same PNG we put in the store.
        decoded = base64.b64decode(outgoing.images[0].bytes_b64)
        assert decoded == PNG_BYTES

        # store.delete fired once (amendment #22).
        assert fake_tmp_image_store.delete_calls == [mock_image_id]

        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_completed_lane_e2e_marker_extracted_and_stripped(
        self, fake_tmp_image_store, mock_image_id
    ):
        """Phase B Task #40: dispatch_completed extracts + strips + uploads
        (progressive adapter-False path; completed delivers with extraction)."""
        adapter = AsyncMock()
        # Progressive returns False (forces completed lane to fire).
        adapter.send = AsyncMock(side_effect=[False, True])

        manager = Mock()
        manager.tmp_image_store = fake_tmp_image_store

        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="e2e-test")
        await dispatcher.start()

        source = "discord:user1"
        content = (
            f"Here is your chart\n"
            f"<!-- ens-img:chart-render:{mock_image_id} -->\n"
            f"hope you like it"
        )

        await dispatcher.dispatch_message(source=source, content=content)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source=source,
            content=content,
        )

        # adapter.send called twice (progressive False, completed True).
        assert adapter.send.await_count == 2

        # The completed-lane call has the extracted image + populated images.
        completed_call = adapter.send.await_args_list[-1].args[0]
        assert "ens-img" not in completed_call.content
        assert completed_call.images is not None
        assert len(completed_call.images) == 1
        assert completed_call.images[0].image_id == mock_image_id

        # store.delete fired EXACTLY ONCE — only the completed lane delivered.
        assert fake_tmp_image_store.delete_calls == [mock_image_id]

        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_api_source_e2e_keeps_marker_no_fetch(self, fake_tmp_image_store):
        """Phase B Task #42: API-source (no-colon) keeps marker verbatim +
        store.open_full / open_with_meta NEVER called."""
        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=True)

        manager = Mock()
        manager.tmp_image_store = fake_tmp_image_store

        registry = Mock()
        registry.get = Mock(return_value=adapter)  # never called
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="e2e-test")
        await dispatcher.start()

        marker = "0" * 32
        content = f"text\n<!-- ens-img:chart-render:{marker} -->\n"

        # Source has NO colon — api internal source, marker preserved verbatim.
        await dispatcher.dispatch_message(source="api", content=content)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source="api",
            content=content,
        )

        # adapter.send NEVER called (no chat adapter for "api").
        adapter.send.assert_not_called()
        # Store NEVER queried (extraction never runs).
        assert fake_tmp_image_store.open_full_calls == []
        assert fake_tmp_image_store.open_with_meta_calls == []
        # store.delete NEVER called.
        assert fake_tmp_image_store.delete_calls == []

        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_store_delete_only_after_success(
        self, fake_tmp_image_store, mock_image_id
    ):
        """Phase B Task #41: store.delete only on success."""
        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=False)  # FAIL

        manager = Mock()
        manager.tmp_image_store = fake_tmp_image_store

        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="e2e-test")
        await dispatcher.start()

        content = f"text\n<!-- ens-img:chart-render:{mock_image_id} -->\n"
        await dispatcher.dispatch_message(source="discord:user1", content=content)

        # Failed send → delete NOT fired.
        assert fake_tmp_image_store.delete_calls == []
        # But open_full / open_with_meta DID fire (we DID extract before send).
        assert fake_tmp_image_store.open_full_calls == [mock_image_id]
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_progressive_then_completed_e2e_one_send_one_delete(
        self, fake_tmp_image_store, mock_image_id
    ):
        """Phase B Task #27 e2e: progressive delivered → completed discards;
        exactly ONE send + ONE delete."""
        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=True)

        manager = Mock()
        manager.tmp_image_store = fake_tmp_image_store

        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="e2e-test")
        await dispatcher.start()

        source = "discord:user1"
        content = (
            f"text\n"
            f"<!-- ens-img:chart-render:{mock_image_id} -->\n"
            f"more"
        )
        await dispatcher.dispatch_message(source=source, content=content)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source=source,
            content=content,
        )

        # Exactly one adapter.send call across both seams.
        assert adapter.send.await_count == 1
        # Exactly one store.delete call.
        assert fake_tmp_image_store.delete_calls == [mock_image_id]
        await dispatcher.stop()


class TestImageAttachmentBytesTransport:
    """Pin the bytes-resolution shape (Task #30a companion — load-bearing)."""

    def test_image_attachment_decodes_byte_equal_to_stored(self):
        """The bytes embedded in ImageAttachment must decode byte-equal to the
        raw PNG stored in the source store."""
        att = ImageAttachment(
            image_id="0" * 32,
            content_type="image/png",
            filename="chart-00000000.png",
            size_bytes=len(PNG_BYTES),
            bytes_b64=base64.b64encode(PNG_BYTES).decode("ascii"),
        )
        decoded = base64.b64decode(att.bytes_b64)
        assert decoded == PNG_BYTES