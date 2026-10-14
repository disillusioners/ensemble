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

from daemon.constants import CHART_IMAGE_MIME_WHITELIST

if TYPE_CHECKING:
    from .registry import SourceRegistry

logger = logging.getLogger(__name__)


# Phase B: chart-image marker extraction (LOCKED regex from decisions.md §marker).
# Byte-stable — DO NOT modify without an R5 architectural review.
_MARKER_RE = re.compile(
    r"^<!-- ens-img:chart-render:([a-f0-9]{32}) -->$",
    re.MULTILINE,
)

# Phase 2: marker-family regex (decisions.md §D2 + phase2-plan.md §Marker Family
# Design). Mirrors the locked→sweeper structure (architecture-recommendation.md
# §3 AM13): this pass runs ONLY on lines the LOCKED pass did NOT claim, so
# chart-render markers stay byte-compatible (F12 invariant extended, not
# replaced). Extracts ``(feature, image_id)`` pairs; ``feature`` is a lowercase-
# kebab token matching ``[a-z0-9-]+`` (D2 grammar).
_FAMILY_MARKER_RE = re.compile(
    r"^<!-- ens-img:([a-z0-9-]+):([a-f0-9]{32}) -->$",
    re.MULTILINE,
)

# Near-miss strip-only sweeper — GENERALIZED for the marker family
# (architecture-recommendation.md §3 amendment #14 + phase2-plan.md §3 pass #3).
# Secondary pattern that STRIPS but NEVER EXTRACTS. Fixes the cosmetic junk-line
# failure mode where HTML comments render literally on Discord / Telegram / Slack
# (decisions.md §phase-b-r2-addendum-2).
#
# INVARIANT (F12/AM13 — drift-proof prose form, no specific line numbers):
#   ``extract_image_markers`` runs the LOCKED extraction pass FIRST
#   (adds to ``chart_ids``, deduped + first-occurrence order), then the
#   FAMILY extraction pass on the LINES the locked pass did NOT match
#   (also deduped + first-occurrence order, keyed by feature), and only
#   THEN applies this near-miss sweeper to the LINES neither pass claimed.
#   The sweeper therefore can only STRIP junk; it can NEVER mint a new
#   ``feature_ids`` entry — the locked + family passes would already have
#   claimed any real marker.
#
#   [AM13 — load-bearing] the generalized sweeper TEXTUALLY MATCHES well-formed
#   family markers (32-hex ⊂ ``[^>\n]{0,64}``). Safety exists ONLY because the
#   sweeper runs LAST on lines the locked + family passes did not claim. If a
#   future contributor re-orders the passes or runs the sweeper over the raw
#   input, the cosmetic-strip purpose is preserved but well-formed markers get
#   stripped-and-lost silently (chart/image loss). Keep the order. The
#   correctness proof is the sweeper-last-on-residual unit test (NOT optional
#   documentation; see phase2-plan.md §Reviewer implementation-time notes).
#
#   The chart-render byte-compat path uses its OWN chart-only near-miss
#   regex (see ``_CHART_NEAR_MISS_RE`` below) so the chart pipeline
#   stays byte-identical for chart-render inputs.
#
# The LOCKED marker regex above stays byte-stable — no relaxation in v1.
_NEAR_MISS_RE = re.compile(
    r"^\s*<!--\s*ens-img:[a-z0-9-]+:[^>\n]{0,64}-->\s*$",
    re.MULTILINE,
)

# Chart-only near-miss sweeper — hoisted to module level (tidier-polish M8)
# so both call sites (``extract_chart_images`` pass 2 and
# ``extract_image_markers`` pass 1.5) share a single source of truth.
# Stays chart-only on purpose: the generalized ``_NEAR_MISS_RE`` would
# textually match well-formed image-gen markers (AM13) and would silently
# strip-and-lose them if used at either site. Same pattern string + flags
# as the prior inline literals — no behavior change.
_CHART_NEAR_MISS_RE = re.compile(
    r"^\s*<!--\s*ens-img:chart-render:[^>\n]{0,64}-->\s*$",
    re.MULTILINE,
)

# Provenance gate (phase2-plan.md §Marker Family Design; architecture-
# recommendation.md §3 amendment #13 extended to feature-family). The set of
# features whose images may be DELIVERED to chat; future features land here
# additively. Strip-but-not-deliver on unknown features is fail-closed (the
# marker is removed from chat text by the extraction sweepers above, but the
# image is NEVER handed to the adapter). Mirrors the design decision that
# cross-namespace id confusion (a forged id advertised under the wrong feature)
# cannot upload.
ALLOWED_FEATURES: frozenset[str] = frozenset({"chart-render", "image-gen"})


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

    Note (phase2-plan.md): the chart-only near-miss regex is the
    module-level ``_CHART_NEAR_MISS_RE`` so chart-render byte-compat is
    preserved; the GENERALIZED ``_NEAR_MISS_RE`` would textually match
    well-formed image-gen markers (AM13) and would silently strip-and-
    lose them if used here.
    """
    # Phase 2: chart-only near-miss — references the module-level
    # ``_CHART_NEAR_MISS_RE`` (tidier-polish M8 hoist). The generalized
    # ``_NEAR_MISS_RE`` is the FAMILY form used by ``extract_image_markers``
    # AFTER the family pass has consumed well-formed markers; using it
    # here would consume image-gen lines before ``extract_image_markers``
    # can extract them.
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
        if _CHART_NEAR_MISS_RE.match(line):
            continue
        swept.append(line)

    return "\n".join(swept), image_ids


def extract_image_markers(content: str) -> tuple[str, dict[str, list[str]]]:
    """Strip marker lines; return ``(stripped_content, {feature: [image_id, ...]})``.

    Phase 2 family extraction (decisions.md §D2 + phase2-plan.md §Marker
    Family Design). Three passes, order preserved (F12/AM13 invariant):

    1. **LOCKED pass** (byte-stable regex ``_MARKER_RE``) → extracts
       ``feature='chart-render'`` ids (dedupe, first-occurrence order).
       Implemented INLINE (not delegated to the legacy chart-only function)
       so the audit pin at
       ``tests/test_chart_image_delivery_audit.py::test_pin_05_*`` keeps
       anchoring only at the seam call sites. Semantics are identical to
       the legacy chart-only function for chart-render inputs.
    2. **FAMILY pass** (``_FAMILY_MARKER_RE``) → applied ONLY to lines the
       locked pass did NOT claim (i.e., the residual from pass 1) →
       extracts arbitrary feature ids (dedupe, first-occurrence order,
       keyed by feature).
    3. **Near-miss sweeper** (GENERALIZED ``_NEAR_MISS_RE``) → applied
       ONLY to lines neither pass claimed → strips junk (NEVER extracts).

    Returns a dict mapping ``feature → [image_id, ...]`` covering ALL
    extracted features (including ``chart-render``). The chart-render ids
    list mirrors what ``extract_chart_images`` returns; consumers that only
    need chart-render can call that function directly for byte-compat.

    The [AM13] invariant comment above ``_NEAR_MISS_RE`` documents the
    load-bearing pass-order requirement; the sweeper-last-on-residual unit
    test pins it.
    """
    feature_ids: dict[str, list[str]] = {}
    seen: set[str] = set()

    # Pass 1: LOCKED chart pass — inline replica of ``extract_chart_images``
    # pass 1 (the LOCKED regex + dedup + first-occurrence). Inlined here
    # so the audit-pin regex (which matches the literal seam call form)
    # anchors only at the seam sites.
    _chart_pending: list[str] = []
    _chart_ids: list[str] = []
    for line in content.splitlines():
        m = _MARKER_RE.match(line)
        if m:
            image_id = m.group(1)
            if image_id not in seen:
                seen.add(image_id)
                _chart_ids.append(image_id)
            continue
        _chart_pending.append(line)
    if _chart_ids:
        feature_ids["chart-render"] = _chart_ids

    # Pass 1.5: chart-only near-miss sweep — strips chart-render near-miss
    # lines from the pending list (mirrors ``extract_chart_images`` pass 2;
    # references the module-level ``_CHART_NEAR_MISS_RE`` (tidier-polish M8
    # hoist) so the GENERALIZED ``_NEAR_MISS_RE`` does not textually match
    # and strip well-formed family markers below).
    _after_chart_sweep: list[str] = []
    for line in _chart_pending:
        if _CHART_NEAR_MISS_RE.match(line):
            continue
        _after_chart_sweep.append(line)
    residual = "\n".join(_after_chart_sweep)

    # Pass 2: family pass on residual — extracts non-chart-render markers.
    pending_after_family: list[str] = []
    for line in residual.splitlines():
        m = _FAMILY_MARKER_RE.match(line)
        if m:
            feature = m.group(1)
            image_id = m.group(2)
            if image_id not in seen:
                seen.add(image_id)
                feature_ids.setdefault(feature, []).append(image_id)
            continue
        pending_after_family.append(line)

    # Pass 3: generalized near-miss sweeper on further residual — strips
    # junk only (textually matches well-formed family markers; safe because
    # they were already consumed by passes 1-2).
    swept: list[str] = []
    for line in pending_after_family:
        if _NEAR_MISS_RE.match(line):
            continue
        swept.append(line)

    return "\n".join(swept), feature_ids


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
            # Source-agnostic MIME gate (architecture-recommendation.md §3
            # amendment #3 — dispatcher owns MIME whitelist; the per-adapter
            # ladder enforces platform-specific limits separately). A MIME
            # mismatch here is a STORE/PROVENANCE defect, not a chat-source
            # concern — drop + WARN, deliver text fallback for siblings.
            stored_content_type = record.content_type or "image/png"
            if stored_content_type not in CHART_IMAGE_MIME_WHITELIST:
                _log_image_metadata(image_id, record.size_bytes, stored_content_type)
                logger.warning(
                    f"chart-image MIME mismatch: image_id={image_id[:8]}... "
                    f"content_type={stored_content_type!r} not in "
                    f"{sorted(CHART_IMAGE_MIME_WHITELIST)}; skipping "
                    f"(text fallback)"
                )
                continue
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


async def _resolve_family_images(
    feature: str,
    image_ids: list[str],
    store: "TmpImageStore | None",
) -> list[ImageAttachment]:
    """Family resolver — ``feature ∈ ALLOWED_FEATURES`` (default-deny else).

    Phase 2 sibling of ``_resolve_chart_images`` (architecture-recommendation.md
    §3 amendment #13 extended + phase2-plan.md §Marker Family Design
    Provenance gate). Identical per-id isolation + MIME gate semantics; the
    only differences from the chart-only resolver are:

    * The provenance gate checks ``feature in ALLOWED_FEATURES`` instead of
      the literal ``"chart-render"`` string. This is the fail-closed gate for
      future features (a marker advertising a not-yet-approved feature is
      stripped-but-not-delivered).
    * The filename prefix is ``image-<id8>.<ext>`` (the chart resolver uses
      ``chart-<id8>.<ext>``) so adapters / downstream consumers can tell
      chart-render vs image-gen artifacts apart at the artifact layer.

    Callers MUST pass a feature name present in ``ALLOWED_FEATURES``; the
    resolver does NOT add the chart-render entrypoint (``_resolve_chart_images``
    handles chart-render for byte-compat with the existing test corpus).
    """
    if not image_ids or store is None:
        return []
    if feature not in ALLOWED_FEATURES:
        logger.warning(
            f"image-family resolve refused: feature={feature!r} not in "
            f"{sorted(ALLOWED_FEATURES)}; skipping (text fallback)"
        )
        return []
    resolved: list[ImageAttachment] = []
    for image_id in image_ids:
        try:
            record = await asyncio.to_thread(store.open_full, image_id)
            # Provenance gate (architecture-recommendation.md §3 amendment #13
            # extended + phase2-plan.md §Marker Family Design): the record's
            # provenance.feature MUST equal the requested feature AND the
            # feature MUST be in ALLOWED_FEATURES — otherwise drop + WARN
            # (strip-but-not-deliver; the marker line has already been
            # removed from chat-bound text by the extraction sweepers).
            provenance = record.provenance or {}
            actual_feature = provenance.get("feature")
            if actual_feature != feature:
                _log_image_metadata(image_id, record.size_bytes, record.content_type)
                logger.warning(
                    f"image provenance mismatch: image_id={image_id[:8]}... "
                    f"requested_feature={feature!r} actual_feature={actual_feature!r} "
                    f"(allowed={sorted(ALLOWED_FEATURES)}); skipping (text fallback)"
                )
                continue
            blob_bytes, _ctype_read, _sha = await asyncio.to_thread(
                store.open_with_meta, image_id
            )
            # Source-agnostic MIME gate (mirrors ``_resolve_chart_images``).
            stored_content_type = record.content_type or "image/png"
            if stored_content_type not in CHART_IMAGE_MIME_WHITELIST:
                _log_image_metadata(image_id, record.size_bytes, stored_content_type)
                logger.warning(
                    f"image MIME mismatch: image_id={image_id[:8]}... "
                    f"content_type={stored_content_type!r} not in "
                    f"{sorted(CHART_IMAGE_MIME_WHITELIST)}; skipping "
                    f"(text fallback)"
                )
                continue
            ext = (record.content_type or "image/png").split("/")[-1] or "bin"
            resolved.append(ImageAttachment(
                image_id=image_id,
                content_type=record.content_type or "image/png",
                filename=f"image-{image_id[:8]}.{ext}",
                size_bytes=record.size_bytes,
                bytes_b64=_base64.b64encode(blob_bytes).decode("ascii"),
            ))
            _log_image_metadata(image_id, record.size_bytes, record.content_type)
        except Exception as e:
            # Per-id isolation (mirrors ``_resolve_chart_images``): bad id →
            # WARN, siblings continue.
            logger.warning(
                f"image resolve failed feature={feature!r} "
                f"image_id={image_id[:8]}...: {e}; continuing with siblings"
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

        # Phase B/2: marker-family extraction + in-process bytes resolution.
        # arch-rec §1 pin (verbatim): LAST content transformation before
        # OutgoingMessage, AFTER adapter lookup. API-origin (no-colon)
        # returned at the no-colon skip and never reaches here. Internal
        # colon-sources (internal_report / internal_error_report) returned
        # at the internal-report skip; internal_agent:* returned at the
        # adapter-lookup miss. Both seams (progressive + completed) carry
        # the same transformation — once-only is preserved structurally
        # by `_progressive_sent_sources`. Phase 2 generalization: the
        # extraction now handles the full feature family (chart-render +
        # image-gen); the seam delegates the chart-only sweep via
        # the call below. The seam itself still invokes
        # ``extract_chart_images`` first (pin #5 byte-compat audit
        # anchor — chart-render extraction is the documented LAST
        # content transformation before OutgoingMessage, verbatim) and
        # then ``extract_image_markers`` on the chart-stripped
        # residual to extract non-chart features.
        chart_stripped, chart_ids = extract_chart_images(content)
        stripped_content, feature_ids = extract_image_markers(chart_stripped)
        # `feature_ids` does NOT carry chart-render in this seam (the chart
        # pass already ran first on `content` and consumed every chart-render
        # marker, leaving the residual fed to `extract_image_markers` empty
        # of chart lines); the `!= "chart-render"` filter is kept as
        # belt-and-braces against a future caller that bypasses the
        # chart-first seam order. Route non-chart features through the
        # family resolver.
        non_chart_features = {
            f: ids for f, ids in feature_ids.items() if f != "chart-render"
        }
        images: list[ImageAttachment] | None = None
        if chart_ids or non_chart_features:
            manager = getattr(self._registry, "manager", None)
            store = getattr(manager, "tmp_image_store", None) if manager else None
            images = []
            if chart_ids:
                # Byte-compat path: chart-render resolution stays through
                # ``_resolve_chart_images`` (its tests pin chart-render-
                # specific provenance + per-id isolation semantics).
                images.extend(await _resolve_chart_images(chart_ids, store))
            for feature, ids in non_chart_features.items():
                # Family path: ALLOWED_FEATURES gate (fail-closed for
                # unknown features; chart-render routed above).
                images.extend(
                    await _resolve_family_images(feature, ids, store)
                )
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
                #
                # F5 (council-review): only delete images the adapter
                # actually delivered. Adapters that don't populate
                # ``outgoing.delivered_image_ids`` (None) fall back to
                # deleting every input image — backward-compat. Adapters
                # that DO populate it delete only the delivered subset;
                # decode-failed / oversize / MIME-miss / capability-
                # short-circuit images stay in the store for retry.
                if images:
                    delivered = getattr(outgoing, "delivered_image_ids", None)
                    if delivered is None:
                        ids_to_delete = [img.image_id for img in images]
                    else:
                        ids_to_delete = [
                            img.image_id for img in images
                            if img.image_id in delivered
                        ]
                    for img_id in ids_to_delete:
                        try:
                            await asyncio.to_thread(store.delete, img_id)
                            logger.debug(
                                f"chart-image deleted after chat delivery: "
                                f"image_id={img_id[:8]}..."
                            )
                        except Exception as e:
                            logger.warning(
                                f"chart-image post-delivery delete failed: "
                                f"image_id={img_id[:8]}...: {e}"
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

        # Phase B/2: marker-family extraction + in-process bytes resolution.
        # arch-rec §1 pin (verbatim): BOTH seams extract. The progressive
        # lane is the normal chat-final lane for external sources; without
        # extraction here, the marker leaks as literal text (HTML comments
        # render literally on Discord / Telegram / Slack) and the image
        # never delivers — the feature is dead-on-arrival for the exact
        # user story it exists to serve. Phase 2: extraction is now
        # feature-family aware (chart-render + image-gen); the seam
        # delegates the chart-only sweep via the call below. The seam
        # itself still invokes ``extract_chart_images`` first (pin #5
        # byte-compat audit anchor) and then ``extract_image_markers`` on
        # the chart-stripped residual to extract non-chart features.
        chart_stripped, chart_ids = extract_chart_images(content)
        stripped_content, feature_ids = extract_image_markers(chart_stripped)
        non_chart_features = {
            f: ids for f, ids in feature_ids.items() if f != "chart-render"
        }
        images: list[ImageAttachment] | None = None
        if chart_ids or non_chart_features:
            manager = getattr(self._registry, "manager", None)
            store = getattr(manager, "tmp_image_store", None) if manager else None
            images = []
            if chart_ids:
                images.extend(await _resolve_chart_images(chart_ids, store))
            for feature, ids in non_chart_features.items():
                images.extend(
                    await _resolve_family_images(feature, ids, store)
                )
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
                # F5 (council-review): only delete images the adapter
                # actually delivered. ``outgoing.delivered_image_ids`` is
                # populated by the adapter during ``send()`` on successful
                # per-image delivery; ``None`` → backward-compat fallback
                # to deleting every input image.
                if images:
                    delivered = getattr(outgoing, "delivered_image_ids", None)
                    if delivered is None:
                        ids_to_delete = [img.image_id for img in images]
                    else:
                        ids_to_delete = [
                            img.image_id for img in images
                            if img.image_id in delivered
                        ]
                    for img_id in ids_to_delete:
                        try:
                            await asyncio.to_thread(store.delete, img_id)
                            logger.debug(
                                f"chart-image deleted after progressive delivery: "
                                f"image_id={img_id[:8]}..."
                            )
                        except Exception as e:
                            logger.warning(
                                f"chart-image post-delivery delete failed: "
                                f"image_id={img_id[:8]}...: {e}"
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
