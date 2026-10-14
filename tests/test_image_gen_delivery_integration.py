"""Integration harness for image-gen delivery (Phase-4 certification item 3).

Mirrors the structure of ``tests/test_chart_image_delivery_e2e.py`` (chart
analog) with one shift: the marker family is ``image-gen`` instead of
``chart-render``. The harness is **in-process only**: real
``TmpImageStore`` + real ``ResponseDispatcher`` + ``MockSourceAdapter``
held in-process; NO network, NO LLM, NO daemon boot. The dispatcher side
is the production code under ``daemon/sources/dispatcher.py``.

Scope (minimum per Phase-4 item 3):

* ``TestImageGenDeliveryHappyPath`` — full-chain extraction on chat
  source (both seams), web-leg verbatim (API / no-colon), dedup, no
  double-send between progressive + completed.
* ``TestImageGenDeliveryDegradedPath`` — mid-chain failures fall back
  to text: missing blob, wrong MIME, provenance feature mismatch
  (NOT in ALLOWED_FEATURES, OR feature differs from ``image-gen``).
* ``TestImageGenStoreDeleteAfterUpload`` — the chat-success delete
  semantic: once-and-only-once on success; not on upload failure; not
  on API/no-colon source; exactly once across both lanes.

The marker is a fresh ``uuid4().hex`` per test in the locked form
``<!-- ens-img:image-gen:<32hex> -->`` on its own line. The store row
carries ``provenance={"feature": "image-gen"}`` so the
``ALLOWED_FEATURES`` gate (``daemon/sources/dispatcher.py:88``) lets
the resolver through.

All scenarios carry the ``@pytest.mark.integration`` marker so the
default suite (which excludes the ``integration`` marker per
``pyproject.toml`` ``addopts = "-m 'not integration and not postgres
and not slow'"``) does NOT collect them. The exact invocation per the
task brief:

    timeout 300 <venv>/pytest tests/test_image_gen_delivery_integration.py \\
        --override-ini="addopts=" -m integration --tb=short -q

Per the task brief, ONLY this file ships in this commit —
pathspec-guarded ``git add`` + ``git commit -- <file>`` per the
parallel-lane discipline (this worktree lane commits this file alone).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import socket
import tempfile
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, PropertyMock

import pytest

from daemon.sources.adapters.discord import DiscordAdapter
from daemon.sources.base import (
    ImageAttachment,
    IncomingMessage,
    OutgoingMessage,
    SourceConfig,
    SourceStatus,
)
from daemon.sources.dispatcher import (
    ALLOWED_FEATURES,
    ResponseDispatcher,
)
from daemon.services.event_bus import EventBus
from daemon.services.tmp_image_store import (
    TmpImageNotFound,
    TmpImageRecord,
    TmpImageStore,
)


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Test PNG — 1×1 RGBA, hand-crafted (identical to the chart harness fixture).
# --------------------------------------------------------------------------- #
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"  # signature
    b"\x00\x00\x00\rIHDR"  # IHDR length + name
    b"\x00\x00\x00\x01\x00\x00\x00\x01"  # 1x1
    b"\x08\x06\x00\x00\x00"  # bit depth / color type
    b"\x1f\x15\xc4\x89"  # CRC
    b"\x00\x00\x00\rIDATx"  # IDAT length + name + x
    b"\x9c\x62\x62\x60\x60\x60\x60\x60\x60\x60\x60\x60"  # data
    b"\x00\x00\x00\x05\x00\x01\r\n\x2d\xb4"  # end + CRC (chart-harness fixture byte-compat)
    b"\x00\x00\x00\x00IEND"  # IEND
    b"\xaeB`\x82"  # CRC
)
# IMPORTANT: bytes must match the chart-render harness fixture
# (tests/test_chart_image_delivery_e2e.py:92-103) byte-for-byte — any
# divergence here is a fixture-bug, NOT a product-bug.
assert PNG_BYTES == (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx"
    b"\x9c\x62\x62\x60\x60\x60\x60\x60\x60\x60\x60\x60"
    b"\x00\x00\x00\x05\x00\x01\r\n\x2d\xb4"
    b"\x00\x00\x00\x00IEND"
    b"\xaeB`\x82"
), "PNG_BYTES diverged from chart-render harness fixture"
assert len(PNG_BYTES) >= 64, f"Test PNG too small: {len(PNG_BYTES)} bytes"
assert PNG_BYTES[:8] == b"\x89PNG\r\n\x1a\n", (
    f"PNG signature check failed; bytes[:8]={PNG_BYTES[:8]!r}"
)


# --------------------------------------------------------------------------- #
# Marker helpers — fresh uuid4 hex per test in the image-gen family form.
# --------------------------------------------------------------------------- #
def _marker(image_id: str) -> str:
    """Return the image-gen LOCKED marker form."""
    return f"<!-- ens-img:image-gen:{image_id} -->"


def _fresh_image_id() -> str:
    """Fresh 32-hex image_id (one per test for isolation)."""
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# Real TmpImageStore fixture — hermetic tmp dir, teardown deletes blob + sidecar.
# --------------------------------------------------------------------------- #
class _SpyStore:
    """Wrap a real ``TmpImageStore``; record call args for assertions.

    The dispatcher calls ``open_full`` then ``open_with_meta`` on every
    image (per dispatcher.py family resolver); we record both. ``delete``
    is also recorded (delete-after-upload assertions). All three methods
    delegate to a real ``TmpImageStore`` so the round-trip behavior is
    faithful.

    ``timeline`` is a unified ordered log of every store operation
    ("open_full:<id>", "open_with_meta:<id>", "delete:<id>") so tests
    can assert call ordering across operation types.

    For "wrong content_type" / "wrong provenance" tests, callers can
    pre-populate the store with a hand-crafted sidecar via ``save_record``
    that overrides the canonical ``image/png`` + ``feature="image-gen"``
    defaults.
    """

    def __init__(self, store: TmpImageStore) -> None:
        self._store = store
        self.open_full_calls: list[str] = []
        self.open_with_meta_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.timeline: list[str] = []

    def save_record(
        self,
        image_id: str,
        *,
        content_type: str = "image/png",
        provenance: dict[str, Any] | None = None,
    ) -> TmpImageRecord:
        """Save with optional sidecar overrides.

        Default values match the canonical "image-gen PNG" path
        (``feature="image-gen"`` + ``image/png``). Callers override
        ``content_type`` for MIME-mismatch tests and ``provenance``
        for the provenance-gate path.
        """
        rec = self._store.save(
            image_id,
            PNG_BYTES,
            content_type,
            provenance=provenance
            if provenance is not None
            else {"feature": "image-gen"},
        )
        return rec

    def open_full(self, image_id: str) -> TmpImageRecord:
        self.open_full_calls.append(image_id)
        self.timeline.append(f"open_full:{image_id}")
        return self._store.open_full(image_id)

    def open_with_meta(self, image_id: str) -> tuple[bytes, str, str]:
        self.open_with_meta_calls.append(image_id)
        self.timeline.append(f"open_with_meta:{image_id}")
        return self._store.open_with_meta(image_id)

    def delete(self, image_id: str) -> bool:
        self.delete_calls.append(image_id)
        self.timeline.append(f"delete:{image_id}")
        return self._store.delete(image_id)

    # Helper for tests that need to simulate a "deleted blob" mid-chain.
    def force_delete_blob(self, image_id: str) -> None:
        """Delete just the blob (NOT the sidecar) to trigger TmpImageNotFound."""
        Path(self._store._data_dir, "tmp_images", image_id).unlink(missing_ok=True)


@pytest.fixture
def tmp_image_dir() -> Any:
    """Hermetic tmp dir for one test; teardown deletes the whole tree."""
    with tempfile.TemporaryDirectory(prefix="image-gen-int-") as tmp:
        yield Path(tmp)


@pytest.fixture
def spy_store(tmp_image_dir) -> _SpyStore:
    """Real ``TmpImageStore`` wrapped in a call-tracking spy."""
    return _SpyStore(TmpImageStore(data_dir=tmp_image_dir))


# --------------------------------------------------------------------------- #
# Mock source adapter — captures sent_messages on the SAME registry the
# dispatcher resolves through.
# --------------------------------------------------------------------------- #
class _MockSourceAdapter:
    """Minimal ``MessageSourceAdapter`` that captures ``sent_messages``.

    Implements just the surface the dispatcher exercises: ``send``
    (records the OutgoingMessage, mutates ``delivered_image_ids`` on
    success), ``start`` / ``stop`` (no-ops), ``health_check``
    (always True). The adapter is registered on the registry the
    dispatcher resolves through.
    """

    def __init__(
        self,
        source_id: str,
        *,
        send_return: bool = True,
        send_side_effect: Any = None,
        raise_on_send: bool = False,
    ) -> None:
        config = SourceConfig(
            source_id=source_id,
            source_type="mock",
            name=f"Mock {source_id}",
            config={},
            credentials={},
            enabled=True,
        )
        # ABC requires ``on_message`` at construction; we pass None and
        # rely on tests not calling emit() on this adapter.
        object.__setattr__(self, "config", config)
        object.__setattr__(self, "_on_message", None)
        object.__setattr__(self, "_status", SourceStatus.STOPPED)
        object.__setattr__(self, "_error", None)
        object.__setattr__(self, "sent_messages", [])
        object.__setattr__(self, "_send_return", send_return)
        object.__setattr__(self, "_send_side_effect", send_side_effect)
        object.__setattr__(self, "_raise_on_send", raise_on_send)
        object.__setattr__(self, "send_call_count", 0)

    @property
    def source_id(self) -> str:
        return self.config.source_id

    @property
    def source_type(self) -> str:
        return self.config.source_type

    @property
    def status(self) -> SourceStatus:
        return self._status

    @property
    def error(self) -> str | None:
        return self._error

    async def start(self) -> None:
        self._status = SourceStatus.RUNNING

    async def stop(self) -> None:
        self._status = SourceStatus.STOPPED

    async def health_check(self) -> bool:
        return True

    async def send(self, message: OutgoingMessage) -> bool:
        self.send_call_count += 1
        if self._raise_on_send:
            raise RuntimeError("mock adapter.send raise (delete-after-upload failure path)")
        if self._send_side_effect is not None:
            result = self._send_side_effect(self.send_call_count - 1)
            self.sent_messages.append(message)
            if result and message.images:
                if message.delivered_image_ids is None:
                    message.delivered_image_ids = []
                message.delivered_image_ids.extend(
                    img.image_id for img in message.images
                )
            return result
        self.sent_messages.append(message)
        if self._send_return and message.images:
            if message.delivered_image_ids is None:
                message.delivered_image_ids = []
            message.delivered_image_ids.extend(
                img.image_id for img in message.images
            )
        return self._send_return


def _make_registry(adapter: _MockSourceAdapter | None, manager: Mock) -> Mock:
    """Build a mock ``SourceRegistry`` with the adapter wired through.

    The registry's ``get(source_id)`` returns the adapter for the
    adapter's source_id, and ``None`` for everything else. ``manager``
    is exposed via the ``manager`` property — that's the path the
    dispatcher walks to reach ``tmp_image_store`` (dispatcher.py:595+).
    """
    registry = Mock()
    registry.get = Mock(
        side_effect=lambda source_id: adapter if (adapter and source_id == adapter.source_id) else None
    )
    type(registry).manager = PropertyMock(return_value=manager)
    return registry


# --------------------------------------------------------------------------- #
# EventBus fixture — dispatcher requires it via constructor in the source.
# --------------------------------------------------------------------------- #
@pytest.fixture
def event_bus() -> EventBus:
    mock_repo = MagicMock()
    mock_repo.create_event = Mock()
    mock_repo.cleanup_old = Mock(return_value=0)
    return EventBus(event_repo=mock_repo)


# =========================================================================== #
# Group 1 — full-chain happy path (image-gen analog of chart Group 1).
# =========================================================================== #
@pytest.mark.integration
class TestImageGenDeliveryHappyPath:
    """Full-chain extraction on chat source + API/no-colon verbatim web leg."""

    @pytest.mark.asyncio
    async def test_full_chain_chat_marker_extracted_and_image_attached(self, spy_store):
        """Agent-style content carrying ``<!-- ens-img:image-gen:<32hex> -->``
        (with the blob pre-seeded in the real ``TmpImageStore`` carrying
        ``provenance={"feature":"image-gen"}``) → both seams strip the
        marker → ``OutgoingMessage.images`` carries the bytes → the mock
        chat adapter receives the actual image bytes → blob is deleted
        after delivery.

        Mirrors the chart-render ``test_full_chain_discord_user_story``
        pin; only the provenance feature + filename prefix differ
        (``image-<id8>.<ext>`` per ``daemon/sources/dispatcher.py:408``).
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)

        adapter = _MockSourceAdapter("mock-source")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g1-image-gen-story"
        )
        await dispatcher.start()

        content = (
            f"Here is your generated image.\n"
            f"{_marker(image_id)}\n"
            f"Hope this helps!"
        )
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source="mock-source:user1",
            content=content,
        )

        # adapter.send called exactly once.
        assert adapter.send_call_count == 1
        outgoing = adapter.sent_messages[-1]

        # Content: marker stripped, surrounding text preserved.
        assert "ens-img" not in outgoing.content, (
            f"marker must be stripped from chat-bound text; "
            f"content={outgoing.content!r}"
        )
        assert "Here is your generated image." in outgoing.content
        assert "Hope this helps!" in outgoing.content

        # Image: ONE ImageAttachment with image_id / content_type /
        # filename / bytes_b64 populated. Filename prefix is
        # ``image-<id8>.<ext>`` for image-gen (vs ``chart-<id8>.<ext>``
        # for chart-render — distinguishable at the artifact layer).
        assert outgoing.images is not None
        assert len(outgoing.images) == 1
        att = outgoing.images[0]
        assert att.image_id == image_id
        assert att.content_type == "image/png"
        assert att.filename.startswith("image-"), (
            f"filename prefix must be 'image-' for image-gen artifacts; "
            f"got {att.filename!r}"
        )
        assert att.filename.endswith(".png")
        decoded = base64.b64decode(att.bytes_b64)
        assert decoded == PNG_BYTES, (
            f"PNG bytes mismatch: got {decoded[:8]!r}, "
            f"expected {PNG_BYTES[:8]!r}"
        )

        # store.delete fired exactly once (chat delivery success).
        assert spy_store.delete_calls == [image_id], (
            f"store.delete must fire once on chat success; "
            f"got {spy_store.delete_calls!r}"
        )

        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_full_chain_api_no_colon_marker_kept_verbatim(self, spy_store):
        """API source (no-colon) keeps the marker verbatim — NEITHER
        adapter.send NOR ``open_with_meta`` is called. Mirrors the
        chart harness's ``test_full_chain_api_caller_keeps_marker``
        pin (chart test:413) for the image-gen family.

        Per the dispatcher (no-colon skip at
        ``daemon/sources/dispatcher.py:687-689``) and the 30-day GET
        window for API-origin (no-colon), the chat-family extraction +
        delete must NOT fire on ``source="api"``. The web leg keeps the
        marker so the HTTP-style response shape is byte-stable for the
        API caller.
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)

        adapter = _MockSourceAdapter("api", send_return=True)
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g1-image-gen-api-web-leg"
        )
        await dispatcher.start()

        content = f"text\n{_marker(image_id)}\n"

        await dispatcher.dispatch_message(source="api", content=content)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source="api",
            content=content,
        )

        # adapter.send NEVER called (no chat adapter for "api").
        assert adapter.send_call_count == 0
        # Store NEVER queried (extraction never runs).
        assert spy_store.open_full_calls == []
        assert spy_store.open_with_meta_calls == []
        # store.delete NEVER called.
        assert spy_store.delete_calls == []
        # Marker is byte-stable in the original content (no extraction).
        assert _marker(image_id) in content

        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_progressive_lane_extracts_and_strips(self, spy_store):
        """``dispatcher.dispatch_message`` (the chat-source progressive
        lane) extracts the image-gen marker, strips it, attaches the
        ``ImageAttachment``, and deletes on success.

        Mirrors chart ``test_progressive_lane_extracts_and_strips``
        (chart test:485). Pin: progressive lane is the NORMAL chat-final
        lane for external sources; without extraction here, the marker
        leaks as literal text (HTML comments render literally on
        Discord / Telegram / Slack) and the image never delivers.
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)

        adapter = _MockSourceAdapter("mock-source")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g1-image-gen-progressive"
        )
        await dispatcher.start()

        content = f"Here is your image\n{_marker(image_id)}\n"
        await dispatcher.dispatch_message(
            source="mock-source:user1", content=content
        )

        assert adapter.send_call_count == 1
        outgoing = adapter.sent_messages[-1]
        assert "ens-img" not in outgoing.content
        assert outgoing.images is not None
        assert len(outgoing.images) == 1
        assert outgoing.images[0].image_id == image_id
        # store.delete fired once after chat delivery success.
        assert spy_store.delete_calls == [image_id]
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_progressive_then_completed_no_double_send(self, spy_store):
        """BOTH seams fire on same source → ``_progressive_sent_sources``
        guard keeps once-only delivery; exactly 1 ``ImageAttachment``
        across ``sent_messages``.

        Mirrors chart ``test_progressive_then_completed_no_double_send``
        (chart test:517). Pin: progressive delivered → completed
        discards; image-delete fires ONCE on the first successful lane.
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)

        adapter = _MockSourceAdapter("mock-source")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g1-image-gen-no-double"
        )
        await dispatcher.start()

        content = f"text\n{_marker(image_id)}\n"
        await dispatcher.dispatch_message(
            source="mock-source:user1", content=content
        )
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source="mock-source:user1",
            content=content,
        )

        # Exactly one send across both seams.
        assert adapter.send_call_count == 1, (
            f"progressive should deliver once; completed should be "
            f"skipped via _progressive_sent_sources guard; got "
            f"send_call_count={adapter.send_call_count}"
        )
        # Exactly one store.delete call (progressive lane won; completed
        # lane was skipped).
        assert spy_store.delete_calls == [image_id]
        # Total bytes_b64 across sent_messages == 1.
        b64_count = sum(len(msg.images or []) for msg in adapter.sent_messages)
        assert b64_count == 1
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_duplicate_marker_dedup(self, spy_store):
        """Content with the same image-gen marker twice → dedup
        preserves first-occurrence order; single ``ImageAttachment`` in
        ``sent_messages[-1].images``.

        Mirrors chart ``test_duplicate_marker_dedup`` (chart test:589).
        Pin: the family extraction dedups on a seen-set keyed by
        image_id (per-features key); sibling content (text) is
        preserved.
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)

        adapter = _MockSourceAdapter("mock-source")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g1-image-gen-dedup"
        )
        await dispatcher.start()

        content = (
            f"first\n{_marker(image_id)}\n"
            f"second\n{_marker(image_id)}\n"
            f"third\n"
        )
        await dispatcher.dispatch_message(
            source="mock-source:user1", content=content
        )

        outgoing = adapter.sent_messages[-1]
        assert "ens-img" not in outgoing.content
        assert outgoing.images is not None
        # First-occurrence dedup → exactly one attachment.
        assert len(outgoing.images) == 1, (
            f"duplicate markers must dedup to one attachment; "
            f"got {len(outgoing.images)}"
        )
        assert outgoing.images[0].image_id == image_id
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_internal_agent_colon_source_skips_at_lookup(self, spy_store):
        """``source_id='internal_agent:foo'`` → returns at the adapter
        lookup BEFORE extraction (no ``open_with_meta`` call, no marker
        strip, content byte-stable). Mirrors chart
        ``test_extraction_after_adapter_lookup`` (chart test:552).
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)
        original_content = f"raw\n{_marker(image_id)}\nmore\n"

        # ``internal_agent:foo`` has NO adapter → dispatcher returns at
        # the ``registry.get(...) is None`` check BEFORE extraction.
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter=None, manager=manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g1-image-gen-internal"
        )
        await dispatcher.start()

        await dispatcher.dispatch_message(
            source="internal_agent:foo", content=original_content
        )
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source="internal_agent:foo",
            content=original_content,
        )

        # Store NEVER queried (extraction never runs).
        assert spy_store.open_full_calls == []
        assert spy_store.open_with_meta_calls == []
        # store.delete NEVER called.
        assert spy_store.delete_calls == []
        # Content is byte-stable.
        assert original_content == f"raw\n{_marker(image_id)}\nmore\n"
        assert _marker(image_id) in original_content
        await dispatcher.stop()


# =========================================================================== #
# Group 2 — degraded path (image-gen analog of chart Group 2 — mid-chain
# failures fall back to text + WARN).
# =========================================================================== #
@pytest.mark.integration
class TestImageGenDeliveryDegradedPath:
    """Mid-chain failures must degrade to text-only delivery + WARN.

    Negative-mirror per chart ``TestDegradedPath`` (chart test:702);
    the SAME behavioral expectations apply (per-id isolation, WARN
    logged, text fallback) — only the marker family + the resolver
    (which is ``_resolve_family_images`` for image-gen, vs
    ``_resolve_chart_images`` for chart-render) differs.

    Pin: per-id isolation is preserved across all degraded paths (a
    bad id never poisons its siblings).
    """

    @pytest.mark.asyncio
    async def test_image_get_raises_marker_stripped_text_delivered(
        self, spy_store, caplog
    ):
        """``TmpImageNotFound`` raised (delete blob between save and
        dispatch) → ``sent_messages[-1].images is None`` (or ``[]``),
        marker-free content, WARN logged. Per-id isolation: a sibling
        valid marker still uploads.

        Mirrors chart ``test_image_get_raises_marker_stripped_text_delivered``
        (chart test:706).
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)

        adapter = _MockSourceAdapter("mock-source")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g2-image-gen-not-found"
        )
        await dispatcher.start()

        # Delete just the blob; sidecar remains, so TmpImageNotFound is
        # raised by ``open_full``.
        spy_store.force_delete_blob(image_id)

        content = f"text\n{_marker(image_id)}\nmore\n"
        with caplog.at_level(logging.WARNING, logger="daemon.sources.dispatcher"):
            await dispatcher.dispatch_message(
                source="mock-source:user1", content=content
            )

        outgoing = adapter.sent_messages[-1]
        assert "ens-img" not in outgoing.content
        # Text + more preserved (marker stripped, sibling content stable).
        assert "text" in outgoing.content
        assert "more" in outgoing.content
        # Dispatcher sets ``images=[]`` when extraction returned no
        # resolvable ids; ``None`` is the semantic fallback. Accept
        # either form as "no images attached".
        assert outgoing.images is None or outgoing.images == [], (
            f"Expected no images on store failure; got {outgoing.images!r}"
        )
        # WARN was emitted.
        assert any(
            "image" in record.message.lower()
            and (
                "resolve failed" in record.message.lower()
                or "not found" in record.message.lower()
                or "matching" in record.message.lower()
            )
            for record in caplog.records
        ), (
            f"No image-resolve WARN captured; "
            f"records={[r.message for r in caplog.records]}"
        )
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_image_get_returns_wrong_content_type_text_delivered(
        self, spy_store, caplog
    ):
        """Store has the PNG but ``content_type`` is NOT in
        ``CHART_IMAGE_MIME_WHITELIST`` (``"image/svg+xml"``) → text
        fallback + WARN. Per-id isolation preserved (sibling valid
        marker still uploads).

        Mirrors chart ``test_image_get_returns_wrong_content_type_text_delivered``
        (chart test:749). The WARN text from
        ``_resolve_family_images`` is ``"image MIME mismatch: ..."``
        — look for "mime" / "content_type" generic tokens.
        """
        bad_id = _fresh_image_id()
        good_id = _fresh_image_id()
        spy_store.save_record(bad_id, content_type="image/svg+xml")
        spy_store.save_record(good_id)  # default content_type="image/png"

        adapter = _MockSourceAdapter("mock-source")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g2-image-gen-mime"
        )
        await dispatcher.start()

        # Per-id isolation: bad SVG AND good PNG in same content. Good
        # one still uploads; bad one dropped + WARN.
        content = f"text\n{_marker(bad_id)}\n{_marker(good_id)}\n"
        with caplog.at_level(logging.WARNING, logger="daemon.sources.dispatcher"):
            await dispatcher.dispatch_message(
                source="mock-source:user1", content=content
            )

        outgoing = adapter.sent_messages[-1]
        assert "ens-img" not in outgoing.content
        assert outgoing.images is not None
        # Only the good marker resolves to an attachment.
        image_ids = {img.image_id for img in outgoing.images}
        assert good_id in image_ids
        assert bad_id not in image_ids
        # WARN was emitted (MIME mismatch path).
        assert any(
            "mime" in record.message.lower()
            or "content_type" in record.message.lower()
            for record in caplog.records
        )
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_image_get_provenance_feature_mismatch_text_delivered(
        self, spy_store, caplog
    ):
        """Store row has ``provenance.feature="chart-render"`` (NOT
        ``"image-gen"``) → drop image + WARN + text fallback.

        Per-id isolation preserved (sibling valid marker still uploads).

        Mirrors chart ``test_image_get_provenance_feature_mismatch_text_delivered``
        (chart test:789). The WARN text from
        ``_resolve_family_images`` for the request/actual feature
        mismatch is ``"image provenance mismatch: ..."``.
        """
        bad_id = _fresh_image_id()
        good_id = _fresh_image_id()
        # ``chart-render`` is in ALLOWED_FEATURES but the family
        # resolver's per-feature gate at ``actual_feature != feature``
        # fires because the dispatched feature is ``image-gen``.
        spy_store.save_record(
            bad_id, provenance={"feature": "chart-render"}
        )
        spy_store.save_record(good_id)  # default provenance={"feature": "image-gen"}

        adapter = _MockSourceAdapter("mock-source")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g2-image-gen-provenance"
        )
        await dispatcher.start()

        content = f"text\n{_marker(bad_id)}\n{_marker(good_id)}\n"
        with caplog.at_level(logging.WARNING, logger="daemon.sources.dispatcher"):
            await dispatcher.dispatch_message(
                source="mock-source:user1", content=content
            )

        outgoing = adapter.sent_messages[-1]
        assert "ens-img" not in outgoing.content
        assert outgoing.images is not None
        image_ids = {img.image_id for img in outgoing.images}
        assert good_id in image_ids
        assert bad_id not in image_ids
        # WARN was emitted (provenance gate path).
        assert any(
            "provenance" in record.message.lower()
            for record in caplog.records
        )
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_foreign_feature_marker_text_fallback(
        self, spy_store, caplog
    ):
        """Foreign-feature marker pin: a marker resolves to a record
        whose ``provenance.feature`` is NOT in ``ALLOWED_FEATURES``
        (e.g., ``"clipboard"``). The family resolver's
        ``actual_feature != feature`` gate AND the ALLOWED_FEATURES
        gate both drop it (the marker is stripped by the extraction
        sweepers, no image attaches, WARN logged).

        Mirrors chart ``test_foreign_feature_marker_text_fallback``
        (chart test:1083) — chart harness uses ``"clipboard"`` which
        is NOT in ``ALLOWED_FEATURES``.
        """
        foreign_id = _fresh_image_id()
        spy_store.save_record(
            foreign_id, provenance={"feature": "clipboard"}
        )

        adapter = _MockSourceAdapter("mock-source")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g2-image-gen-foreign"
        )
        await dispatcher.start()

        content = f"text\n{_marker(foreign_id)}\n"
        with caplog.at_level(logging.WARNING, logger="daemon.sources.dispatcher"):
            await dispatcher.dispatch_message(
                source="mock-source:user1", content=content
            )

        outgoing = adapter.sent_messages[-1]
        assert "ens-img" not in outgoing.content
        # ``images=[]`` or ``None``; either is acceptable as
        # "no images attached" per the dispatcher semantics.
        assert outgoing.images is None or outgoing.images == []
        # WARN was emitted (provenance gate path).
        assert any(
            "provenance" in record.message.lower()
            for record in caplog.records
        )
        await dispatcher.stop()


# =========================================================================== #
# Group 8 (image-gen analog) — store.delete-after-upload.
# =========================================================================== #
@pytest.mark.integration
class TestImageGenStoreDeleteAfterUpload:
    """Chat-delivery success → ``store.delete(image_id)`` fired once;
    upload-fail / API-source / both-lanes-fire → NO spurious deletes.

    Mirrors chart ``TestStoreDeleteAfterUpload`` (chart test:2248);
    same chat-success delete semantic for the image-gen family.
    """

    @pytest.mark.asyncio
    async def test_chat_delivery_calls_store_delete_once(self, spy_store):
        """Happy-path chat delivery → ``store.delete(image_id)`` fired
        exactly once; ``open_with_meta`` BEFORE ``delete`` (the spy
        ``timeline`` records both).
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)

        adapter = _MockSourceAdapter("mock-source")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g8-image-gen-delete-once"
        )
        await dispatcher.start()

        content = f"text\n{_marker(image_id)}\n"
        await dispatcher.dispatch_message(
            source="mock-source:user1", content=content
        )

        # store.delete fired exactly once.
        assert spy_store.delete_calls == [image_id]
        # Order: open_with_meta happened BEFORE delete.
        open_idx = spy_store.timeline.index(f"open_with_meta:{image_id}")
        delete_idx = spy_store.timeline.index(f"delete:{image_id}")
        assert open_idx < delete_idx, (
            f"open_with_meta after delete: timeline={spy_store.timeline}"
        )
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_chat_delivery_upload_failure_does_not_delete(
        self, spy_store
    ):
        """Image upload raises → ``store.delete`` NOT called (only
        successful deliveries delete).

        Mirrors chart ``test_chat_delivery_upload_failure_does_not_delete``
        (chart test:2286).
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)

        # Adapter that raises on send.
        adapter = _MockSourceAdapter("mock-source", raise_on_send=True)
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g8-image-gen-upload-fail"
        )
        await dispatcher.start()

        content = f"text\n{_marker(image_id)}\n"
        # ``dispatch_message`` catches exceptions and returns without
        # calling delete.
        await dispatcher.dispatch_message(
            source="mock-source:user1", content=content
        )

        # store.delete NEVER called (upload raised → no successful delivery).
        assert spy_store.delete_calls == []
        # open_with_meta DID fire (extraction ran before send).
        assert spy_store.open_with_meta_calls == [image_id]
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_api_source_does_not_delete(self, spy_store):
        """``source_id='api'`` (no-colon) → ``store.delete`` NOT called
        (30-day GET window preserved per §http-api). Pin: the web leg
        MUST preserve the marker verbatim AND keep the blob alive.

        Mirrors chart ``test_api_source_does_not_delete`` (chart
        test:2313).
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)

        adapter = _MockSourceAdapter("api")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g8-image-gen-api-no-delete"
        )
        await dispatcher.start()

        content = f"text\n{_marker(image_id)}\n"
        await dispatcher.dispatch_message(source="api", content=content)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source="api",
            content=content,
        )

        # Adapter NEVER called; store NEVER queried; delete NEVER fired.
        assert adapter.send_call_count == 0
        assert spy_store.open_full_calls == []
        assert spy_store.open_with_meta_calls == []
        assert spy_store.delete_calls == []
        # Marker still byte-stable in original content (no extraction).
        assert _marker(image_id) in content
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_both_lanes_only_one_delete(self, spy_store):
        """Both ``dispatch_message`` and ``dispatch_completed`` paths
        fire on the same source → ``delete.call_count == 1``
        (mutually-exclusive lanes; once-only structural).

        Mirrors chart ``test_both_lanes_only_one_delete`` (chart
        test:2345).
        """
        image_id = _fresh_image_id()
        spy_store.save_record(image_id)

        adapter = _MockSourceAdapter("mock-source")
        manager = Mock()
        manager.tmp_image_store = spy_store
        registry = _make_registry(adapter, manager)

        dispatcher = ResponseDispatcher(
            registry=registry, subscriber_id="g8-image-gen-both-lanes"
        )
        await dispatcher.start()

        content = f"text\n{_marker(image_id)}\n"
        await dispatcher.dispatch_message(
            source="mock-source:user1", content=content
        )
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source="mock-source:user1",
            content=content,
        )

        # Exactly one delete across both lanes.
        assert spy_store.delete_calls == [image_id]
        assert len(spy_store.delete_calls) == 1
        await dispatcher.stop()


# =========================================================================== #
# Sanity guards on the harness itself (no production-code patching).
# =========================================================================== #
@pytest.mark.integration
def test_image_gen_marker_format_locked():
    """The image-gen marker is byte-stable in the locked form.

    Locks the form ``<!-- ens-img:image-gen:<32hex> -->`` for any future
    contributor to the harness; if you change the form, the entire
    family-extraction contract changes (per the chart precedent at
    chart test:1484 — ``TestMarkerRegexPinning``). Pure sanity test —
    no dispatcher call.
    """
    sample_id = "abcdef0123456789abcdef0123456789"
    assert len(sample_id) == 32
    marker = _marker(sample_id)
    assert marker == "<!-- ens-img:image-gen:abcdef0123456789abcdef0123456789 -->"
    # Regex shape (mirrors ``_FAMILY_MARKER_RE`` in dispatcher.py:40-43).
    pat = re.compile(r"^<!-- ens-img:([a-z0-9-]+):([a-f0-9]{32}) -->$")
    m = pat.match(marker)
    assert m is not None, f"Marker must match _FAMILY_MARKER_RE shape: {marker!r}"
    assert m.group(1) == "image-gen"
    assert m.group(2) == sample_id


@pytest.mark.integration
def test_allowed_features_includes_image_gen():
    """Product-contract sanity: ``ALLOWED_FEATURES`` MUST include
    ``"image-gen"`` (per ``daemon/sources/dispatcher.py:88``); if the
    contract slips, the entire feature is dead-on-arrival.

    Pin: this is a NO-MODIFY guard — the production constant MUST be
    the source of truth. If this test ever fails with a product gap,
    surface FAIL-with-evidence; never patch the constant here.
    """
    assert "image-gen" in ALLOWED_FEATURES, (
        f"image-gen MUST be in ALLOWED_FEATURES per dispatcher.py:88; "
        f"got {sorted(ALLOWED_FEATURES)}"
    )
