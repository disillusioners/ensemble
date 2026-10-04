"""Response dispatcher for routing agent responses back to external sources.

The dispatcher receives completed message events directly from the manager
and routes responses to external sources (Telegram, Discord, etc.) using
per-user ordering locks for guaranteed delivery ordering.
"""

from __future__ import annotations

import asyncio
import base64 as _base64
import logging
import re
from collections import OrderedDict
from typing import TYPE_CHECKING

from .base import ImageAttachment, OutgoingMessage

if TYPE_CHECKING:
    from .registry import SourceRegistry

logger = logging.getLogger(__name__)


# Phase B: chart-image marker extraction (LOCKED regex from decisions.md §marker).
# Byte-stable — DO NOT modify without an R5 architectural review.
_MARKER_RE = re.compile(
    r"^<!-- ens-img:chart-render:([a-f0-9]{32}) -->$",
    re.MULTILINE,
)

# Near-miss strip-only sweeper (architecture-recommendation.md §3 amendment #14).
# Secondary pattern that STRIPS but NEVER EXTRACTS and never matches the locked
# form. Fixes the cosmetic junk-line failure mode where HTML comments render
# literally on Discord / Telegram / Slack (decisions.md §phase-b-r2-addendum-2).
# The LOCKED marker regex above stays byte-stable — no relaxation in v1.
_NEAR_MISS_RE = re.compile(
    r"^\s*<!--\s*ens-img:chart-render:[^>\n]{0,64}-->\s*$",
    re.MULTILINE,
)


def _log_image_metadata(image_id: str, size_bytes: int | None, content_type: str | None) -> None:
    """Standardized image metadata log — bytes never included.

    (architecture-recommendation.md §3 amendment #12). Logs
    ``image_id[:8]`` + ``size_bytes`` + ``content_type`` at the
    dispatcher AND mirrored helpers in all three chat adapters. The
    base64 bytes payload is NEVER in any log line.
    """
    short = image_id[:8]
    if size_bytes is not None and content_type is not None:
        logger.debug(
            f"chart-image resolved: image_id={short}... size={size_bytes} "
            f"content_type={content_type}"
        )
    else:
        logger.debug(f"chart-image reference: image_id={short}...")


def extract_chart_images(content: str) -> tuple[str, list[str]]:
    """Strip marker lines; return ``(stripped_content, [image_id, ...])``.

    Per decisions.md §marker + architecture-recommendation.md §3 amendments
    #2 / #3 / #14:

    * LOCKED regex matches → extract (de-dupe, first-occurrence order).
    * Near-miss sweeper pattern → strip only (never extract).
    * Other content → preserved untouched.
    * Malformed markers (typo, extra whitespace, invented id) NOT matched
      by the LOCKED regex are caught by the near-miss sweeper for
      strip-only cosmetic cleanup.

    The dispatcher calls this in ``dispatch_message`` AND
    ``dispatch_completed`` as the LAST content transformation before
    ``OutgoingMessage`` construction — see architecture-recommendation.md §1
    pin, verbatim.
    """
    image_ids: list[str] = []
    seen: set[str] = set()  # amendment #2: per-id dedupe
    # First pass: extract via LOCKED regex. Subsequent occurrences of the same
    # id are stripped but not re-added (amendment #2).
    pending_lines: list[str] = []
    for line in content.splitlines():
        m = _MARKER_RE.match(line)
        if m:
            image_id = m.group(1)
            if image_id not in seen:
                seen.add(image_id)
                image_ids.append(image_id)
            continue
        pending_lines.append(line)

    # Second pass: near-miss sweeper strips only — does NOT add to image_ids.
    # (The pattern can textually overlap the locked form at the regex level;
    # safe ONLY because the locked-regex extraction pass ran FIRST — swept
    # lines are exactly those the locked regex did not match.)
    swept: list[str] = []
    for line in pending_lines:
        if _NEAR_MISS_RE.match(line):
            continue
        swept.append(line)

    return "\n".join(swept), image_ids


async def _resolve_chart_images(
    image_ids: list[str],
    store: "TmpImageStore | None",
) -> list[ImageAttachment]:
    """Resolve ``image_ids`` → ``list[ImageAttachment]`` via in-process store.

    Per-id isolation (architecture-recommendation.md §3 amendment #3):
    one bad id → WARN + continue; siblings still deliver.

    Provenance gate (architecture-recommendation.md §3 amendment #13):
    ``record.provenance.feature`` MUST == ``"chart-render"``; mismatch →
    drop the image (text fallback), WARN. Kills cross-namespace id
    confusion (a forged clipboard/designer id forged as chart-render
    cannot upload).

    The record is read via ``store.open_full`` (provenance + metadata
    only — verified TmpImageRecord has no ``.blob`` field), and the
    bytes are read via the sibling ``store.open_with_meta`` accessor.
    Both reads run on a worker thread (``asyncio.to_thread``) so the
    event loop is not blocked by the disk read.
    """
    if not image_ids or store is None:
        return []
    resolved: list[ImageAttachment] = []
    for image_id in image_ids:
        try:
            record = await asyncio.to_thread(store.open_full, image_id)
            # Provenance gate (amendment #13) — default-deny: missing provenance
            # or wrong feature → drop + WARN.
            provenance = record.provenance or {}
            if provenance.get("feature") != "chart-render":
                _log_image_metadata(image_id, record.size_bytes, record.content_type)
                logger.warning(
                    f"chart-image provenance mismatch: image_id={image_id[:8]}... "
                    f"feature={provenance.get('feature')!r} (expected 'chart-render'); "
                    f"skipping (text fallback)"
                )
                continue
            blob_bytes, _ctype_read, _sha = await asyncio.to_thread(
                store.open_with_meta, image_id
            )
            ext = (record.content_type or "image/png").split("/")[-1] or "bin"
            resolved.append(ImageAttachment(
                image_id=image_id,
                content_type=record.content_type or "image/png",
                filename=f"chart-{image_id[:8]}.{ext}",
                size_bytes=record.size_bytes,
                bytes_b64=_base64.b64encode(blob_bytes).decode("ascii"),
            ))
            _log_image_metadata(image_id, record.size_bytes, record.content_type)
        except Exception as e:
            # Per-id isolation: bad id → WARN, siblings continue.
            logger.warning(
                f"chart-image resolve failed image_id={image_id[:8]}...: {e}; "
                f"continuing with siblings"
            )
    return resolved


class ResponseDispatcher:
    """Dispatches completed agent responses to appropriate message sources.
    
    Receives completed message events directly from the manager via
    `dispatch_completed()` and routes responses to external sources.
    Uses per-user ordering locks to guarantee delivery ordering.
    """
    
    MAX_SEND_LOCKS = 10000  # Class constant for LRU eviction
    
    def __init__(
        self,
        registry: "SourceRegistry" | None = None,
        subscriber_id: str = "response_dispatcher"
    ) -> None:
        """Initialize the response dispatcher.
        
        Args:
            registry: SourceRegistry to get adapters from.
            subscriber_id: Unique identifier for this dispatcher instance.
        """
        self._registry: "SourceRegistry" | None = registry
        self._subscriber_id = subscriber_id
        
        self._running: bool = False
        
        self._send_locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._locks_guard = asyncio.Lock()
        
        # Track sources that received progressive messages to avoid duplicate delivery
        self._progressive_sent_sources: set[str] = set()
        
        logger.info(f"ResponseDispatcher initialized with subscriber_id={subscriber_id}")
    
    async def start(self) -> None:
        """Start the dispatcher asynchronously."""
        if self._running:
            logger.warning("ResponseDispatcher already running")
            return
        
        logger.info("Starting ResponseDispatcher")
        self._running = True
    
    async def stop(self, timeout: float = 30.0) -> None:
        """Stop the dispatcher gracefully.
        
        Args:
            timeout: Maximum seconds to wait for graceful shutdown.
        """
        if not self._running:
            return
        
        logger.info("Stopping ResponseDispatcher")
        self._running = False
        
        # Clear send locks
        async with self._locks_guard:
            self._send_locks.clear()
            self._progressive_sent_sources.clear()
    
    async def dispatch_completed(
        self,
        instance_id: str,
        message_id: str,
        source: str,
        content: str,
        message_type: str = "text",
        metadata: dict | None = None,
        reply_to_id: str | None = None,
    ) -> None:
        """Dispatch a completed message to the appropriate external source.
        
        This is the main entry point called by the manager when a message
        completes processing. It routes the response to the correct adapter.
        
        Args:
            instance_id: The instance that processed the message.
            message_id: The message ID that completed.
            source: The source identifier (format: "source_id:external_user_id").
            content: The response content to send.
            message_type: Type of message (text, image, etc.).
            metadata: Optional metadata to include.
            reply_to_id: Optional message ID to reply to.
        """
        logger.debug(f"[DISPATCH] dispatch_completed called: source={source}, content_length={len(content) if content else 0}")
        
        if not self._running:
            logger.debug("ResponseDispatcher not running, skipping dispatch")
            return
        
        if self._registry is None:
            logger.debug("No source registry configured, skipping dispatch")
            return
        
        # Skip empty content to avoid duplicate/empty sends (progressive may have sent)
        if not content or not content.strip():
            logger.debug(f"Skipping empty content for source={source}")
            return
        
        # Skip if source already received progressive messages (last message was already sent)
        logger.debug(f"[DISPATCH] progressive_sent_sources check: source={source}, in_set={source in self._progressive_sent_sources}")
        if source in self._progressive_sent_sources:
            logger.debug(f"Skipping dispatch_completed for source={source} (progressive delivery already sent)")
            self._progressive_sent_sources.discard(source)
            return
        
        # Parse source as "source_id:external_user_id"
        # Sources without ":" are internal (e.g., "api") and don't need routing
        if ":" not in source:
            logger.debug(f"Skipping internal source (no routing needed): {source}")
            return
        
        source_id, external_user_id = source.split(":", 1)
        
        # Validate source_id format
        if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', source_id):
            logger.warning(f"Invalid source_id format: {source_id}")
            return
        
        # Validate external_user_id length
        if len(external_user_id) > 256:
            logger.warning(f"external_user_id too long: {len(external_user_id)}")
            return
        
        logger.debug(f"Dispatching completed message for source={source_id}, user={external_user_id}")
        
        # Skip adapter lookup for internal sources (not external adapters)
        # C1 fix: Only skip internal_report and internal_error_report, NOT internal_agent
        if source_id in ("internal_report", "internal_error_report"):
            logger.debug("[DISPATCH] SKIPPED: no adapter or source is internal")
            logger.debug(f"Skipping internal report source (no adapter needed): {source_id}")
            return
        
        # Get adapter from registry
        adapter = self._registry.get(source_id)
        if adapter is None:
            logger.debug("[DISPATCH] SKIPPED: no adapter or source is internal")
            if source_id.startswith("internal_"):
                logger.debug(f"No adapter needed for internal source: {source_id}")
            else:
                logger.debug(f"No adapter found for source_id={source_id}")
            return

        logger.debug(f"[DISPATCH] sending to adapter: source={source}, adapter_type={type(adapter).__name__}")

        # Phase B: chart-image extraction + in-process bytes resolution.
        # arch-rec §1 pin (verbatim): LAST content transformation before
        # OutgoingMessage, AFTER adapter lookup. API-origin (no-colon)
        # returned at the no-colon skip and never reaches here. Internal
        # colon-sources (internal_report / internal_error_report) returned
        # at the internal-report skip; internal_agent:* returned at the
        # adapter-lookup miss. Both seams (progressive + completed) carry
        # the same transformation — once-only is preserved structurally
        # by `_progressive_sent_sources`.
        stripped_content, image_ids = extract_chart_images(content)
        images: list[ImageAttachment] | None = None
        if image_ids:
            manager = getattr(self._registry, "manager", None)
            store = getattr(manager, "tmp_image_store", None) if manager else None
            images = await _resolve_chart_images(image_ids, store)
        content = stripped_content  # markers always stripped from chat-bound text

        # Create OutgoingMessage (populates images at THIS construction site)
        outgoing = OutgoingMessage(
            external_user_id=external_user_id,
            content=content,
            source_id=source_id,
            metadata=metadata or {},
            message_type=message_type,
            reply_to_id=reply_to_id,
            images=images,
        )

        # Send with per-user lock for ordering
        send_lock = await self._get_send_lock(external_user_id)

        async with send_lock:
            success = await adapter.send(outgoing)
            if success:
                logger.debug(f"Sent response to user {external_user_id} via {source_id}")
                # Phase B amendment #22: chat-delivered → 0-day GET window.
                # API-origin (no-colon) keeps the 30-day GET per Phase A
                # §http-api. Mutually-exclusive lanes make double-delete
                # impossible (progressive delivered → completed discards via
                # `_progressive_sent_sources`; progressive adapter-False →
                # completed delivers + deletes here).
                if images:
                    for img in images:
                        try:
                            await asyncio.to_thread(store.delete, img.image_id)
                            logger.debug(
                                f"chart-image deleted after chat delivery: "
                                f"image_id={img.image_id[:8]}..."
                            )
                        except Exception as e:
                            logger.warning(
                                f"chart-image post-delivery delete failed: "
                                f"image_id={img.image_id[:8]}...: {e}"
                            )
            else:
                logger.warning(f"Failed to send response to user {external_user_id} via {source_id}")
    
    async def dispatch_message(self, source: str, content: str) -> None:
        """Send an intermediate message during execution (progressive delivery).
        
        Routes messages to external sources during agent execution, as opposed
        to dispatch_completed which sends the final response.
        
        Args:
            source: The source identifier (format: "source_id:external_user_id").
            content: The message content to send.
        """
        if not self._running:
            logger.debug("ResponseDispatcher not running, skipping dispatch")
            return
        
        if self._registry is None:
            logger.debug("No source registry configured, skipping dispatch")
            return
        
        # Parse source as "source_id:external_user_id"
        # Sources without ":" are internal (e.g., "api") and don't need routing
        if ":" not in source:
            logger.debug(f"Skipping internal source (no routing needed): {source}")
            return
        
        source_id, external_user_id = source.split(":", 1)
        
        # Validate source_id format
        if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', source_id):
            logger.warning(f"Invalid source_id format: {source_id}")
            return
        
        # Validate external_user_id length
        if len(external_user_id) > 256:
            logger.warning(f"external_user_id too long: {len(external_user_id)}")
            return
        
        logger.debug(f"Dispatching progressive message for source={source_id}, user={external_user_id}")
        
        # Skip adapter lookup for internal sources (not external adapters)
        # C1 fix: Only skip internal_report and internal_error_report, NOT internal_agent
        if source_id in ("internal_report", "internal_error_report"):
            logger.debug(f"Skipping internal report source (no adapter needed): {source_id}")
            return
        
        # Get adapter from registry
        adapter = self._registry.get(source_id)
        if adapter is None:
            if source_id.startswith("internal_"):
                logger.debug(f"No adapter needed for internal source: {source_id}")
            else:
                logger.debug(f"No adapter found for source_id={source_id}")
            return

        # Phase B: chart-image extraction + in-process bytes resolution.
        # arch-rec §1 pin (verbatim): BOTH seams extract. The progressive
        # lane is the normal chat-final lane for external sources; without
        # extraction here, the marker leaks as literal text (HTML comments
        # render literally on Discord / Telegram / Slack) and the image
        # never delivers — the feature is dead-on-arrival for the exact
        # user story it exists to serve.
        stripped_content, image_ids = extract_chart_images(content)
        images: list[ImageAttachment] | None = None
        if image_ids:
            manager = getattr(self._registry, "manager", None)
            store = getattr(manager, "tmp_image_store", None) if manager else None
            images = await _resolve_chart_images(image_ids, store)
        content = stripped_content

        # Create OutgoingMessage (populates images at THIS construction site)
        outgoing = OutgoingMessage(
            external_user_id=external_user_id,
            content=content,
            source_id=source_id,
            metadata={},
            message_type="text",
            reply_to_id=None,
            images=images,
        )

        # Send with per-user lock for ordering
        send_lock = await self._get_send_lock(external_user_id)

        async with send_lock:
            try:
                success = await adapter.send(outgoing)
            except Exception as e:
                logger.warning(f"Progressive dispatch failed for source {source}: {e}")
                return
            if success:
                logger.debug(f"Sent progressive message to user {external_user_id} via {source_id}")
                # Phase B amendment #22: chat-delivered → 0-day GET window.
                if images:
                    for img in images:
                        try:
                            await asyncio.to_thread(store.delete, img.image_id)
                            logger.debug(
                                f"chart-image deleted after progressive delivery: "
                                f"image_id={img.image_id[:8]}..."
                            )
                        except Exception as e:
                            logger.warning(
                                f"chart-image post-delivery delete failed: "
                                f"image_id={img.image_id[:8]}...: {e}"
                            )
                # Track this source so dispatch_completed won't send again
                self._progressive_sent_sources.add(source)
            else:
                logger.warning(f"Failed to send progressive message to user {external_user_id} via {source_id}")
    
    async def _get_send_lock(self, external_user_id: str) -> asyncio.Lock:
        """Get or create a send lock for a specific user.
        
        Uses double-check locking pattern for thread-safe lock creation.
        Implements LRU eviction to prevent memory leaks.
        
        Args:
            external_user_id: The external user ID to get lock for.
            
        Returns:
            asyncio.Lock for this user's send operations.
        """
        async with self._locks_guard:
            if external_user_id in self._send_locks:
                # Move to end (most recently used)
                self._send_locks.move_to_end(external_user_id)
                return self._send_locks[external_user_id]
            
            # Evict oldest if at capacity
            if len(self._send_locks) >= self.MAX_SEND_LOCKS:
                oldest_id, _ = self._send_locks.popitem(last=False)
                logger.debug(f"Evicted send lock for inactive user: {oldest_id}")
            
            lock = asyncio.Lock()
            self._send_locks[external_user_id] = lock
            return lock
    
    async def _handle_event(self, event: dict) -> None:
        """Process a completed event by sending response to source.
        
        NOTE: Disabled pending redesign. This was previously called by the event
        loop but is now a no-op.
        
        Args:
            event: The event dict (not used)
        """
        # No-op - dispatcher is disabled pending redesign
        pass
