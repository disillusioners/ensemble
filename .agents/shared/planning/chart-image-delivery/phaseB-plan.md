# Phase B Plan — chart-image-delivery (dispatch + per-adapter native upload)

**Date:** 2026-10-04 (R2 — Revision loop 2: arch-recommendation.md fold-in)
**Author:** planner[v2] via plan-creation worker
**Status:** Draft R2 — Revision loop 2 (R1-R9 fold-in)
**Feature commission:** `chart-image-delivery` (D1 self-install / D2 local render / D3 tmp_images substrate / D4 per-platform native delivery)
**Branch base:** `latest` @ `cf8efbeff9d932a6d01d7cbb2411836057e7b099`
**Companion artifact:** `.agents/shared/planning/chart-image-delivery/decisions.md` (Phase A locked contract — **APPEND** §phase-b-* sections at end per dispatch note; reviewer-ordered in-place corrections at decisions.md:95 and decisions.md:321; new §phase-b addendum records R2 adoption of amendment #22 + both-seam rule + sweeper + logging contract + provenance gate)
**Output dir:** `.agents/shared/planning/chart-image-delivery/`
**Architect pin:** `architecture-recommendation.md §1` (both-seam extraction, verbatim) — non-negotiable.
**Doc-drift pinning:** discord.py 2.7.1 / slack-sdk 3.42.0 / aiohttp multipart — verify at impl per `§phase-b-doc-verification`.

> **Precedence:** `architecture-recommendation.md` §3 governs over phase-plan prose on conflict.

> **Phase B** of the `chart-image-delivery` commission. Phase A defines the **internal transport** (the `<!-- ens-img:chart-render:<id> -->` marker + `image_save` to `tmp_images`). Phase B owns the **delivery layer**: extract the marker at **both** dispatch seams (progressive lane + completed lane — see `architecture-recommendation.md §1`), resolve the PNG via in-process `TmpImageStore.open_full()`, upload natively per chat adapter (Discord / Slack / Telegram), and gracefully degrade on platform-side failure. HTTP-API callers keep the marker verbatim per Phase A §http-api.

---

## Objective

When an agent's final response includes a Phase-A-locked `<!-- ens-img:chart-render:<id> -->` marker on a chat-bound dispatch, the chat-source dispatcher extracts the marker at **both** seams (`dispatch_message` for the normal chat-final lane + `dispatch_completed` as the structural fallback lane — `architecture-recommendation.md §1` pin, verbatim), resolves the PNG via in-process `TmpImageStore.open_full()` with a `provenance.feature == "chart-render"` gate, constructs an `OutgoingMessage.images` payload, and hands it to per-adapter native upload (Discord `files=[discord.File]` / Telegram `sendPhoto` multipart / Slack `files_upload_v2`). HTTP-API sources (no-colon, e.g. `"api"`) skip the dispatch path entirely → marker stays in their response content per Phase A §http-api. Image-resolution or upload failure degrades to text-only delivery with the marker stripped and the Mermaid block intact — never block text. After successful chat delivery, `store.delete(image_id)` collapses the unauth GET window (amendment #22 adopted); API-origin keeps the 30-day GET.

**User-story sentence (testable):** *A Discord user asks the agent for a workflow chart → the agent calls `generate_chart()` → the user receives the rendered PNG as a Discord attachment with the (marker-stripped) explanation text, NOT a wall of Mermaid code or visible literal marker text.*

---

## Scope

### In Scope

1. **`OutgoingMessage` extension** (`daemon/sources/base.py:52-60`) — add a defaulted `images: list[ImageAttachment] | None = None` field via a small frozen `ImageAttachment` dataclass. `ImageAttachment.bytes_b64 = field(repr=False)` + redacting `__repr__` (arch-rec §3 amendment #12; logging contract below). Backward-compatible; existing 3 construction sites (`dispatcher.py:170`, `:243`, `registry.py:980`) keep their existing argument lists — the field auto-defaults to `None` until the dispatcher populates it. **`registry.py:980` is NEVER populated** — `/new` confirmation messages never carry markers.
2. **Dispatcher marker extraction — BOTH seams** (`daemon/sources/dispatcher.py`) — `extract_chart_images` runs in `dispatch_message` AND `dispatch_completed` as the LAST content transformation before `OutgoingMessage` construction — **after** the no-colon skip (`dispatcher.py:132-134` / `:209-211`), after source validation, after the internal-report skip, and **after** the adapter lookup (`:158-165` / `:234-240`). Module-level regex `^<!-- ens-img:chart-render:[a-f0-9]{32} -->$` (locked by Phase A §marker, byte-stable). Per-id dedupe preserves first-occurrence order (arch-rec §3 amendment #2). Per-id resolution isolation: one bad (hallucinated) id → WARN, siblings deliver (arch-rec §3 amendment #3).
3. **In-process image bytes access** — `manager.tmp_image_store.open_full(image_id)` (`daemon/services/tmp_image_store.py:480-494` — returns the full `TmpImageRecord` with provenance). Wrapped in `asyncio.to_thread` for the async dispatcher. **No HTTP self-call** — direct service access path verified at `daemon/manager.py:2544-2546`. **Provenance gate** (`arch-rec §3 amendment #13`): `record.provenance.feature == "chart-render"` required; mismatch → text fallback (kills cross-namespace clipboard/designer id confusion).
4. **Near-miss strip-only sweeper** (`arch-rec §3 amendment #14`) — a NEW, clearly-labeled secondary pattern `^\s*<!--\s*ens-img:chart-render:[^>\n]{0,64}-->\s*$` that STRIPS but NEVER extracts and never matches the locked form. The LOCKED marker regex stays byte-stable. Fixes the cosmetic failure mode where HTML comments render literally on Discord/Telegram/Slack.
5. **Discord native upload** (`daemon/sources/adapters/discord/adapter.py:1554`) — chunk-1 (text + file) is an **atomic first unit** (arch-rec §3 amendment #6): on failure, one text-only retry of that unit (file never re-attempted on later chunks); success → continue chunks 2..N; retry-fail → `return False` (today's total-failure semantics). N-image uses `files=[…]` (`file`/`files` mutually exclusive in discord-py 2.7.1). Empty-content-after-strip with images attached is valid — `if not content and not message.images:` early-return at `:1575-1577` is extended with `and not message.images` (arch-rec §3 amendment #11). Upload failure → retry once text-only → if still fails, full text fallback.
6. **Telegram native upload** (`daemon/sources/adapters/telegram.py:262`) — new `_api_call_multipart` helper (uses `aiohttp.FormData`, mirrors `_api_call`'s 3-retry + exponential backoff + circuit-breaker discipline from `:148-197`). `send()` calls `sendPhoto` (≤10 MB) or `sendDocument` (≤50 MB) based on `len(file_bytes)`. **Multipart 4xx classified non-transient, no record** (arch-rec §3 amendment #5; mirror Discord `:1537-1547`); transport errors keep 3× exponential backoff then text. >50MB / MIME-miss: skip image, WARN, deliver text. Caption >1024: truncated caption on the photo **plus** full-text follow-up `sendMessage` (text floor — arch-rec §3 amendment #8). `parse_mode=None` firm decision for image captions (Mermaid `<` characters break HTML parse_mode). Token bucket (`:296`) + per-chat LRU lock (`:301`) preserved. **Upload executes INSIDE per-chat lock** (arch-rec §3 amendment #10 — ordering hazard).
7. **Slack native upload** (`daemon/sources/adapters/slack/adapter.py:380`) — **single `files_upload_v2(channel_id, filename, content, initial_comment)`** (arch-rec §3 amendment #9 — DELETE the upload-then-`postMessage(file=)` sketch; undocumented kwarg + double-render risk). Capability detection: `missing_scope`/`files:write`/`not_in_channel` errors → **classify BEFORE `record_failure`** (verified defect: `_call_slack_api:326-329` records failure on ANY `ok=false`; arch-rec cites `:340-343` — line drift, defect real). Cached per-token capability flag → **zero API calls once flagged**, WARN-once-per-channel (arch-rec §3 amendment #4). `initial_comment` (caption) >length-limit: truncated on-image + full-text follow-up `chat.postMessage`. **Upload executes INSIDE per-channel lock** (arch-rec §3 amendment #10). USER ACTION ITEM: operator must grant `files:write` scope. Doc-update task: `docs/sources/slack-setup.md` YAML manifest (`:17-61`, `scopes:` block `:35-48`) AND scopes table (`:69-82`) get a `files:write` row.
8. **Degradation ladder (uniform)** — per-arch-rec Focus 3:
   - **Discord** — chunk-1 atomic first unit; one text-only retry; file never re-attempted on later chunks; `files=[…]` for N. `files=[…]` 4xx/429 classified non-transient, no record.
   - **Telegram** — multipart 4xx no retry, fall to text; transport error 3× exponential backoff then text; >50MB / MIME-miss: skip image, WARN, deliver text; caption >1024 truncated + full-text follow-up.
   - **Slack** — first `missing_scope`: set capability flag + WARN-once + text; flagged thereafter: zero API calls + text; other errors per `_safe_api_call` then text. Primary pattern: single `files_upload_v2(channel_id, filename, content, initial_comment)`.
   - **All rungs:** WARN with `image_id[:8]` / size / content_type; multi-image = marker order preserved, NO silent drops (every dropped image WARNs — arch-rec §3 amendment #7).
9. **Constants** (`daemon/constants.py`) — `DISCORD_FILE_MAX_BYTES=8MB`, `TELEGRAM_PHOTO_MAX_BYTES=10MB`, `TELEGRAM_DOCUMENT_MAX_BYTES=50MB`, `SLACK_FILE_MAX_BYTES=1GB`, **`INITIAL_COMMENT_MAX=4000` (Slack `initial_comment` soft limit — used by the §7 sketch, Task #23, and the ACs; previously undefined, defined here per R4)**. Plus `CHART_IMAGE_MIME_WHITELIST = {"image/png", "image/jpeg", "image/gif", "image/webp"}` for defense. *Verify-at-impl (R4, tracking iter-002): Discord's non-boosted bot default has moved 8MB→10MB across API generations and Slack workspace limits vary (1GB is a generous ceiling, not a doc claim) — re-verify per D4 at impl; benign for 50–300KB payloads.*
10. **Store.delete after successful upload** (`arch-rec §3 amendment #22` — ADOPTED) — after a successful `adapter.send()` in the delivering lane (progressive OR completed), `store.delete(image_id)` collapses the unauth GET window from 30 days to zero for chat-delivered charts. API-origin (no-colon, no adapter.send) keeps the 30-day GET per Phase A §http-api. Mutually-exclusive lanes (api skips both seams; chat gets one lane) make double-delete impossible. ~3 LOC + 1 test.
11. **Logging contract** (`arch-rec §3 amendment #12` / Focus 3 shared invariants) — `ImageAttachment.bytes_b64 = field(repr=False)` + redacting `__repr__`; declare `OutgoingMessage.images` transport-only — never persisted, never logged whole. Logging format: `image_id[:8]` + `size_bytes` + `content_type` only, at dispatcher AND all three adapters. Bytes NEVER in any log.
12. **Test strategy** — per-seam unit tests (extraction regex incl. malformed markers; BOTH-seam extraction including progressive lane; API-source keep-marker regression; `test_progressive_then_completed_no_double_send`; id-dedupe; per-id isolation; provenance gate; sweeper) + per-adapter AsyncMock tests (Discord: chunk-1 atomic-first-unit + `files=[…]` + empty-content-with-images; Telegram: sendPhoto + 4xx-non-transient + caption>1024-follow-up + sendDocument ladder + 50MB skip + parse_mode=None; Slack: single `files_upload_v2` + capability-flag-zero-API + WARN-once + `initial_comment` follow-up + upload-inside-lock) + dispatcher integration test + new `tests/test_outbound_image_delivery.py` end-to-end.

### Out of Scope

- Any `agents/` prompt / charter / skill change (Phase A, C).
- Cross-cutting tests / version / docs consolidation (Phase D). Phase D owns the multi-chart e2e test that proves the multi-image flow end-to-end.
- Telegram 4096-char text chunking (pre-existing defect — **DEFERRED to separate ticket**; recorded in §phase-b-telegram-4096-deferred).
- `source_hint` system-context injection for agent channel-awareness (Phase C's lane; recorded in §phase-b-source-hint-deferred).
- Image storage changes (Phase A) — `tmp_images` substrate and its sweep semantics are unchanged.
- New HTTP route, new daemon service, new SQL migration, new env var.
- Slack app / new scope grants — **USER ACTION ITEM** only (operator side); Phase B ships the code behind capability detection + text fallback so it degrades safely until the scope is granted.
- New dependencies — no PIL/Pillow, no aiohttp version bump (multipart via existing `aiohttp.FormData`), no discord-py version bump, no slack-sdk version bump. All file-upload APIs are verified-against-current-docs TASKS, not new installs.
- Image dimension probing / Discord embed enrichment (over-engineering for v1).
- Re-render / downscale-on-oversize (no PIL dependency; defer to Phase D if needed).
- Optional 24h freshness window on `provenance.uploaded_at` — phase D may add; not required for v1 (render→dispatch is seconds).

---

## Files Touched (exact list)

| File | Change class | Restart? |
|------|--------------|----------|
| `daemon/sources/base.py` | +`ImageAttachment` dataclass with `bytes_b64 = field(repr=False)` + redacting `__repr__`; +1 defaulted `images` field on `OutgoingMessage` | yes (daemon restart) |
| `daemon/sources/registry.py` | +`@property manager` (1 line; clean seam for dispatcher image-store access) | yes |
| `daemon/sources/dispatcher.py` | +`_MARKER_RE` + `_NEAR_MISS_RE` regexes + `extract_chart_images()` (per-id dedupe + per-id isolation); inject extraction + image-resolution into **BOTH** `dispatch_message` (`:232-256`) AND `dispatch_completed` (`:158-171`) — AFTER adapter lookup; populate `OutgoingMessage.images` at both construction sites; +`store.delete()` after successful send in delivering lane (amendment #22) | yes |
| `daemon/sources/adapters/discord/adapter.py` | `_send_single_chunk` gains `file=None` kwarg; `send()` attaches `discord.File` to chunk 1 + `files=[…]` for N images; chunk-1 atomic-first-unit; empty-content-with-images guard at `:1575-1577` (`if not content and not message.images`); upload-fail text-only retry; `_log_image_metadata()` helper for logging contract | yes |
| `daemon/sources/adapters/telegram.py` | +`_api_call_multipart()` helper; `send()` calls `sendPhoto`/`sendDocument` ladder; 4xx non-transient classification (mirror Discord `:1537-1547`); >50MB/MIME-miss skip+WARN; caption>1024 + full-text follow-up `sendMessage`; `parse_mode=None` for image captions; upload INSIDE per-chat lock; `_log_image_metadata()` helper | yes |
| `daemon/sources/adapters/slack/adapter.py` | `send()` calls single `files_upload_v2(channel_id, filename, content, initial_comment)` (DELETE upload-then-postMessage sketch); classify `missing_scope` BEFORE `record_failure` (fix defect at `_call_slack_api:326-329`); cached per-token capability flag (zero API calls once flagged) + WARN-once-per-channel; `initial_comment` truncation + full-text follow-up; upload INSIDE per-channel lock; `_log_image_metadata()` helper | yes |
| `daemon/constants.py` | +`DISCORD_FILE_MAX_BYTES`, +`TELEGRAM_PHOTO_MAX_BYTES`, +`TELEGRAM_DOCUMENT_MAX_BYTES`, +`SLACK_FILE_MAX_BYTES`, +`CHART_IMAGE_MIME_WHITELIST` | yes |
| `docs/sources/slack-setup.md` | +1 row in YAML manifest `scopes:` block (`:35-48`) AND scopes table (`:69-82`): `files:write` | no (doc only) |
| `tests/test_sources_dispatcher.py` | +`extract_chart_images` unit tests (valid/invalid/multiline/duplicate/near-miss); +BOTH-seam integration tests (progressive + completed); +API-source-keep-marker-with-`open_full`-NOT-called; +id-dedupe regression; +per-id isolation regression; +provenance gate; +sweeper; +`test_progressive_then_completed_no_double_send` | yes |
| `tests/test_discord_adapter.py` | +chunk-1 atomic-first-unit test; +`files=[…]` N-image test; +empty-content-with-images guard test; +upload-fail text-only retry test; +multi-image order-preserved-no-drop test | yes |
| `tests/test_telegram_adapter.py` | +sendPhoto multipart assertion; +4xx-non-transient classification (no circuit-breaker record); +sendPhoto→sendDocument ladder; +>50MB skip+WARN test; +caption>1024 follow-up `sendMessage` test; +parse_mode=None assertion | yes |
| `tests/test_slack_adapter.py` | +single `files_upload_v2` call assertion (NOT upload-then-postMessage); +`missing_scope` classified BEFORE `record_failure` (consecutive_failures==0); +capability flag zero-API-call test (5 sends → only 1 actual API call); +`initial_comment` truncation + follow-up; +upload INSIDE per-channel lock | yes |
| `tests/test_outbound_image_delivery.py` (NEW) | end-to-end via mock source + mock `tmp_image_store` populated with a tiny PNG: marker → image arrives + stripped text delivered (BOTH seams); +store.delete-after-success; +real-astream-lane twin test for Phase D | yes |

**NO `agents/`, NO `agents/_prompt_system/`, NO `daemon/tools/chart_tools.py`, NO migration, NO new HTTP route, NO new daemon service.**

---

## Components (exact files + sections)

### 1. `daemon/sources/base.py:52-60` — `OutgoingMessage` extension

Insertion at lines 52-53 (just before the existing `OutgoingMessage`):

```python
@dataclass(frozen=True)
class ImageAttachment:
    """Resolved image bytes for native chat-source delivery.

    Transport-only: NEVER persisted, NEVER logged whole. The base64 bytes
    payload is excluded from repr/dataclasses-asdict by field(repr=False)
    + redacting __repr__ (architecture-recommendation.md §3 amendment #12).
    Log only image_id[:8] + size + content_type at dispatcher AND adapters.

    Bytes encoding rationale: no BytesIO across the adapter boundary —
    pickling + cross-task handoff is simpler with a string. Adapters decode
    per their platform's upload API.
    """
    image_id: str            # 32-hex (regex-validated at extraction)
    content_type: str        # e.g. "image/png"
    filename: str            # e.g. "chart-abc12345.png"
    size_bytes: int          # captured at resolution; logs use this
    bytes_b64: str = field(repr=False)  # base64 of the PNG bytes — NEVER LOGGED

    def __repr__(self) -> str:  # redacting — bytes payload never in repr
        return (
            f"ImageAttachment(image_id={self.image_id[:8]}..., "
            f"content_type={self.content_type!r}, "
            f"size_bytes={self.size_bytes}, "
            f"filename={self.filename!r}, "
            f"bytes_b64=<redacted>)"
        )
```

Modify existing `OutgoingMessage` at lines 52-60 (one line added):

```python
@dataclass
class OutgoingMessage:
    """Normalized outgoing message to any source.

    `images` is transport-only: NEVER persisted, NEVER logged whole
    (architecture-recommendation.md §3 amendment #12).
    """
    external_user_id: str
    content: str
    source_id: str
    metadata: dict = field(default_factory=dict)
    message_type: str = "text"
    reply_to_id: str | None = None
    images: list[ImageAttachment] | None = None   # NEW — defaulted, backward-compat
```

Construction-site impact: **none required** — all 3 sites pass kwargs by name and the new field defaults to `None`. Tests continue to construct `OutgoingMessage` without changes. The dispatcher populates `images` only at the **two** `OutgoingMessage` construction sites (`dispatcher.py:170` for `dispatch_completed`, `:243` for `dispatch_message`) — never at `registry.py:980` (/new slash never has a marker).

### 2. `daemon/sources/registry.py` — `manager` property (clean seam)

Add a 3-line property near the existing `get()` method (around line 236):

```python
@property
def manager(self) -> "InstanceManager":
    """Public read-only access to the injected manager reference.

    Used by ResponseDispatcher to reach the shared tmp_image_store for
    Phase B chart-image extraction. Read-only — do not mutate.
    """
    return self._manager
```

Why: `ResponseDispatcher._registry._manager.tmp_image_store` would work but reaches into a private attr across modules. A 3-line public property is the cleanest seam.

### 3. `daemon/sources/dispatcher.py` — marker extraction at BOTH seams (arch-rec §1 pin, verbatim)

**This is the architecturally-critical change.** `extract_chart_images` runs in `dispatch_message` AND `dispatch_completed` as the LAST content transformation before `OutgoingMessage` construction — **after** the no-colon skip (`dispatcher.py:132-134` / `:209-211`), after source validation, after the internal-report skip, and **after** the adapter lookup (`:158-165` / `:234-240`). API-origin (no-colon) sources return at the skip and never reach extraction: marker verbatim in content, `open_full` never called. Internal colon-sources (`internal_agent:*`) return at the adapter lookup before extraction.

**Why both seams?** Per `architecture-recommendation.md §1` (twice-cited by the council, independently corroborated by `instance_messaging.py:4506-4566` + `:4815-4834` and `dispatcher.py:124-128`): for external chat sources, the final AI message is delivered via the **progressive lane** (`dispatch_message`) — in-loop when `language_check` is OFF, buffered + re-dispatched post-loop when ON. `dispatch_completed` then discards via `_progressive_sent_sources`. **If extraction is on `dispatch_completed` only, the feature is dead-on-arrival for the user story it exists to serve.** Once-only is preserved structurally: one `OutgoingMessage` per lane per message; the `_progressive_sent_sources` guard keeps exact semantics (progressive delivered → completed discards; progressive adapter-False → completed delivers with extraction).

Module-level additions (after imports, around line 28):

```python
# Phase B: chart-image marker extraction (locked regex from decisions.md §marker)
import base64 as _base64
from .base import ImageAttachment, OutgoingMessage

# LOCKED regex — byte-stable per Phase A §marker.
_MARKER_RE = re.compile(
    r"^<!-- ens-img:chart-render:([a-f0-9]{32}) -->$",
    re.MULTILINE,
)

# Near-miss strip-only sweeper (architecture-recommendation.md §3 amendment #14).
# NEW, clearly-labeled secondary pattern that STRIPS but NEVER EXTRACTS and
# never matches the locked form. Fixes cosmetic failure mode (HTML comments
# render literally on Discord/Telegram/Slack — see decisions.md:95 invisibility
# correction). LOCKED marker regex stays byte-stable.
_NEAR_MISS_RE = re.compile(
    r"^\s*<!--\s*ens-img:chart-render:[^>\n]{0,64}-->\s*$",
    re.MULTILINE,
)


def _log_image_metadata(image_id: str, size_bytes: int | None, content_type: str | None) -> None:
    """Standardized image metadata line — bytes never included."""
    short = image_id[:8]
    if size_bytes is not None and content_type is not None:
        logger.debug(f"chart-image resolved: image_id={short}... size={size_bytes} content_type={content_type}")
    else:
        logger.debug(f"chart-image reference: image_id={short}...")


def extract_chart_images(content: str) -> tuple[str, list[str]]:
    """Strip marker lines; return (stripped_content, [image_id, ...]).

    Per decisions.md §marker + architecture-recommendation.md §3 amendments #2/#3/#14:
      - LOCKED regex matches → extract (de-dupe preserving first-occurrence order)
      - Near-miss sweeper pattern → strip only (never extract)
      - Other content → preserved untouched
      - Malformed markers (typo, extra whitespace, invented id) NOT matched
        by LOCKED regex are caught by near-miss sweeper for strip-only
        (cosmetic junk-line cleanup)
    """
    image_ids: list[str] = []
    seen: set[str] = set()  # amendment #2: per-id dedupe
    kept: list[str] = []
    # First pass: extract with LOCKED regex, strip near-miss.
    # Iterate to lock replacements when both apply (rare).
    pending_lines: list[str] = []
    for line in content.splitlines():
        m = _MARKER_RE.match(line)
        if m:
            image_id = m.group(1)
            if image_id not in seen:
                seen.add(image_id)
                image_ids.append(image_id)
            # dedupe: subsequent occurrences stripped but not re-extracted
            continue
        pending_lines.append(line)

    # Second pass: near-miss sweeper strips only, does NOT add to image_ids.
    swept: list[str] = []
    for line in pending_lines:
        if _NEAR_MISS_RE.match(line):
            continue  # strip only — never EXTRACTS. (The pattern CAN textually
                      # overlap the locked form at the pattern level; safe ONLY because
                      # the locked-regex extraction pass runs FIRST — swept lines are
                      # exactly those the locked regex did not match. R4 comment fix.)
        swept.append(line)

    return "\n".join(swept), image_ids


async def _resolve_chart_images(
    image_ids: list[str],
    store: "TmpImageStore | None",
) -> list[ImageAttachment]:
    """Resolve image_ids → ImageAttachment list via in-process store.

    Per-id isolation (arch-rec §3 amendment #3): one bad id → WARN + continue.
    Per-id provenance gate (arch-rec §3 amendment #13): record.provenance.feature
    MUST == 'chart-render' (or whatever charter emits via decisions.md D3).
    Mismatch → text fallback (drop from returned list, WARN).
    """
    if not image_ids or store is None:
        return []
    from .base import ImageAttachment
    resolved: list[ImageAttachment] = []
    for image_id in image_ids:
        try:
            record = await asyncio.to_thread(store.open_full, image_id)
            # Provenance gate (amendment #13)
            provenance = record.provenance or {}
            if provenance.get("feature") != "chart-render":
                _log_image_metadata(image_id, record.size_bytes, record.content_type)
                logger.warning(
                    f"chart-image provenance mismatch: image_id={image_id[:8]}... "
                    f"feature={provenance.get('feature')!r} (expected 'chart-render'); "
                    f"skipping (text fallback)"
                )
                continue
            # R4 (approver iter-002): TmpImageRecord has NO `.blob` field (verified) —
            # the prior hasattr hedge encoded EMPTY bytes. Bytes come from the
            # store's documented bytes accessor; `record` (open_full) supplies
            # provenance + metadata only. Test 30a pins this accessor pair.
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
                # blob_bytes from open_with_meta (tmp_image_store.py:436-455);
                # record from open_full (:480-494) — verify shapes at impl (test 30a).
            ))
            _log_image_metadata(image_id, record.size_bytes, record.content_type)
        except Exception as e:
            # Per-id isolation: bad id → WARN, siblings continue
            logger.warning(
                f"chart-image resolve failed image_id={image_id[:8]}...: {e}; "
                f"continuing with siblings"
            )
    return resolved
```

**INJECTION INSIDE `dispatch_completed` (`:84-187`)** — extract+resolve lives between adapter lookup at `:158-165` and OutgoingMessage construction at `:170`:

```python
        # Get adapter from registry
        adapter = self._registry.get(source_id)
        if adapter is None:
            # ... existing skip-and-log block unchanged ...
            return

        logger.debug(f"[DISPATCH] sending to adapter: source={source}, adapter_type={type(adapter).__name__}")

        # Phase B: chart-image extraction + in-process bytes resolution.
        # arch-rec §1 pin: LAST content transformation before OutgoingMessage,
        # AFTER adapter lookup. API-origin (no-colon) returns at :132-134;
        # internal colon-sources return at :152-155/158-165 — never reach here.
        stripped_content, image_ids = extract_chart_images(content)
        images: list[ImageAttachment] | None = None
        if image_ids:
            manager = getattr(self._registry, "manager", None)
            store = getattr(manager, "tmp_image_store", None) if manager else None
            images = await _resolve_chart_images(image_ids, store)
        content = stripped_content  # markers always stripped from chat-bound text

        # Create OutgoingMessage (populates images)
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
                # Phase B amendment #22: chat-delivered → 0. Get API window
                if images:
                    for img in images:
                        try:
                            await asyncio.to_thread(store.delete, img.image_id)
                            _log_image_metadata(img.image_id, img.size_bytes, img.content_type)
                            logger.debug(f"chart-image deleted after chat delivery: image_id={img.image_id[:8]}...")
                        except Exception as e:
                            logger.warning(f"chart-image post-delivery delete failed: image_id={img.image_id[:8]}...: {e}")
            else:
                logger.warning(f"Failed to send response to user {external_user_id} via {source_id}")
```

**INJECTION INSIDE `dispatch_message` (`:189-266`)** — IDENTICAL transformation at the symmetric point (after adapter lookup at `:234-240`, before OutgoingMessage construction at `:243`):

```python
        # Get adapter from registry
        adapter = self._registry.get(source_id)
        if adapter is None:
            # ... existing skip-and-log block unchanged ...
            return

        # Phase B: chart-image extraction + in-process bytes resolution.
        # arch-rec §1 pin: BOTH seams (progressive + completed) — the progressive
        # lane is the normal chat-final lane for external sources; without
        # extraction here, the marker leaks as literal text and the image never
        # delivers (verified by arch-rec §0: "the feature is dead-on-arrival for
        # the exact user story it exists to serve").
        stripped_content, image_ids = extract_chart_images(content)
        images: list[ImageAttachment] | None = None
        if image_ids:
            manager = getattr(self._registry, "manager", None)
            store = getattr(manager, "tmp_image_store", None) if manager else None
            images = await _resolve_chart_images(image_ids, store)
        content = stripped_content

        # Create OutgoingMessage (populates images)
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
                # Phase B amendment #22: chat-delivered → 0. Get API window
                if images:
                    for img in images:
                        try:
                            await asyncio.to_thread(store.delete, img.image_id)
                            logger.debug(f"chart-image deleted after progressive delivery: image_id={img.image_id[:8]}...")
                        except Exception as e:
                            logger.warning(f"chart-image post-delivery delete failed: image_id={img.image_id[:8]}...: {e}")
                # Track this source so dispatch_completed won't send again
                self._progressive_sent_sources.add(source)
            else:
                logger.warning(f"Failed to send progressive message to user {external_user_id} via {source_id}")
```

**Once-only preserved structurally:**
- **Progressive delivered (True returned on `adapter.send`)** → `_progressive_sent_sources.add(source)` at `:264`; on next `dispatch_completed` for same source, `:125-128` returns early → marker-stripped text is what `dispatch_message` delivered; `dispatch_completed` never fires.
- **Progressive adapter-False** → source NOT added to `_progressive_sent_sources`; `dispatch_completed` fires later with the same content, re-extracts (idempotent — markers already gone), re-resolves (cacheable, but for v1 re-resolve), re-attempts. If `dispatch_completed` succeeds, store.delete fires there.

**Why this is HTTP-API-safe:** the no-colon skip at line 132 (`if ":" not in source: return`) is BEFORE the extraction injection point (after `:158` adapter lookup). HTTP-API sources (`"api"` and similar internal no-colon identifiers) short-circuit at `:132` and never reach extraction. Phase A's §http-api contract (marker stays in API responses) is preserved by construction.

**Why internal-report/internal_error_report/internal_agent colon-sources are safe:** they return at `:152-155` (internal_report / internal_error_report skip) or `:158-165` (no adapter for `internal_agent:*`) BEFORE the extraction injection. They never reach extraction either.

**`registry.py:980` construction site (`/new` confirmation):** never populated — `/new` confirmation messages never carry markers. Inert.

### 4. `daemon/constants.py` — platform size limits

Append to the existing constants block (after `:24` `MAX_CHAT_LOCKS`):

```python
# Phase B: chart-image delivery platform limits
DISCORD_FILE_MAX_BYTES: int = 8 * 1024 * 1024   # 8 MB (Discord bot upload limit)
TELEGRAM_PHOTO_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB (Telegram sendPhoto)
TELEGRAM_DOCUMENT_MAX_BYTES: int = 50 * 1024 * 1024  # 50 MB (Telegram sendDocument)
SLACK_FILE_MAX_BYTES: int = 1024 * 1024 * 1024  # 1 GB (Slack upload — generous)
INITIAL_COMMENT_MAX: int = 4000  # Slack initial_comment soft limit (verify at impl per D4)
CHART_IMAGE_MIME_WHITELIST: frozenset[str] = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})
```

Values verified against Phase A §capture (50–300 KB typical; well within all limits). **Re-verify against CURRENT platform docs at impl time** — record the verified values + doc URLs in commit message.

### 5. `daemon/sources/adapters/discord/adapter.py:1554` — Discord native delivery (atomic-first-unit + files=[…] for N)

**Empty-content-with-images guard (arch-rec §3 amendment #11)** — the existing early-return at `:1575-1577` (`if not content: ... return False`) must be extended with `and not message.images`. Without this, marker strip can leave an empty content + images attached, and the adapter would early-return without ever sending.

```python
# :1575-1577 (existing) — EXTEND per amendment #11
if not content and not message.images:
    logger.warning("Discord send: empty content after stripping")
    return False
```

**Atomic-first-unit chunk-1** (arch-rec §3 amendment #6) — chunk-1 (text + file) is an atomic first unit. On failure, ONE text-only retry of that unit. File is NEVER re-attempted on later chunks (a partial file failure = full message failure, don't double-invoke). Success → continue chunks 2..N (text-only). Retry-fail → `return False` (today's total-failure semantics).

**Multi-image N>1** (arch-rec §3 amendment #7) — marker order is preserved (deterministic dispatch through `extract_chart_images` + the construction `images=[…]` list); NO silent drops (every dropped image is WARNed with `image_id[:8]`); `file=` and `files=` are mutually exclusive in discord-py 2.7.1 — use `files=[…]` for N>1.

Module-level helper:

```python
def _log_image_metadata(image_id: str, size_bytes: int | None, content_type: str | None) -> None:
    """Standardized image metadata line — bytes never included (arch-rec §3 amendment #12)."""
    short = image_id[:8]
    if size_bytes is not None and content_type is not None:
        logger.debug(f"discord chart-image: image_id={short}... size={size_bytes} content_type={content_type}")
    else:
        logger.debug(f"discord chart-image: image_id={short}...")
```

Modify `_send_single_chunk` signature (lines 1501-1508) to accept `file` AND `files`:

```python
async def _send_single_chunk(
    self,
    target: Any,
    content: str,
    *,
    reference: Any | None,
    allowed_mentions: Any,
    file: Any | None = None,
    files: list[Any] | None = None,
) -> bool:
    """Send a single chunk to a resolved Discord target.
    ...
    """
    try:
        kwargs: dict[str, Any] = {
            "content": content,
            "reference": reference,
            "allowed_mentions": allowed_mentions,
        }
        if files is not None:
            kwargs["files"] = files   # multi-image: discord-py mutually exclusive with file=
        elif file is not None:
            kwargs["file"] = file     # single-image
        await target.send(**kwargs)
        await self._circuit_breaker.record_success()
        return True
    except Exception as e:
        # ... existing classification unchanged ...
        ...
```

Modify `send()` between line 1624 (after target resolution) and line 1645 (chunked loop):

```python
# Phase B: chart-image native upload (discord.File / discord.files for N)
file_obj: Any | None = None
files_objs: list[Any] | None = None
images = getattr(message, "images", None)
if images:
    try:
        import discord as _discord
        import io as _io
        decoded = [_base64.b64decode(img.bytes_b64) for img in images]
        if len(decoded) == 1:
            file_obj = _discord.File(
                fp=_io.BytesIO(decoded[0]),
                filename=images[0].filename,
            )
        else:
            files_objs = [
                _discord.File(fp=_io.BytesIO(b), filename=img.filename)
                for b, img in zip(decoded, images)
            ]
        for img in images:
            _log_image_metadata(img.image_id, img.size_bytes, img.content_type)
    except Exception as e:
        logger.warning(f"discord.File(s) construction failed: {e}; text only")
        file_obj = None
        files_objs = None
```

Modify the chunked loop (lines 1645-1662) for atomic-first-unit behavior:

```python
chunks = self._split_message(content)
async with lock:
    async with self._send_semaphore:
        sent_count = 0
        for chunk in chunks:
            kwargs = {
                "reference": reference,
                "allowed_mentions": allowed_mentions,
            }
            # Chunk 1 (atomic first unit): attach file/files if present.
            # Chunks 2..N: text-only (file NEVER re-attempted on later chunks).
            if sent_count == 0:
                if files_objs is not None:
                    kwargs["files"] = files_objs
                elif file_obj is not None:
                    kwargs["file"] = file_obj
            ok = await self._send_single_chunk(target, chunk, **kwargs)
            if not ok and sent_count == 0 and (file_obj is not None or files_objs is not None):
                # Phase B amendment #6: ONE text-only retry of the atomic first unit.
                # File/files NEVER re-attempted on later chunks.
                logger.warning(
                    f"discord chunk-1 with file failed; retrying text-only for "
                    f"external_user_id={message.external_user_id}"
                )
                kwargs.pop("file", None)
                kwargs.pop("files", None)
                ok = await self._send_single_chunk(target, chunk, **kwargs)
            if not ok:
                logger.warning(
                    f"Discord send: chunk {sent_count + 1}/{len(chunks)} "
                    f"failed for external_user_id={message.external_user_id}"
                )
                return False
            sent_count += 1
return True
```

Imports at top of file: `import base64 as _base64`, `import io as _io`. Existing per-channel LRU lock + `DiscordSendSemaphore(5)` semantics preserved. Multi-image drops (should never happen, but defensive): each `ImageAttachment` not delivered → WARN with `image_id[:8]` + size + content_type; **never silently drop**.

**Drop discipline (amendment #7):** if `len(decoded) != len(images)` (i.e., `bytes_b64` decode fails for some), log WARN per failed image and continue with the successful subset. If ALL images fail, fall through to text-only. Never silently drop.

### 6. `daemon/sources/adapters/telegram.py:262` — Telegram native delivery (4xx non-transient, >50MB skip, caption follow-up, parse_mode=None firm, upload-inside-lock)

**Multipart 4xx classification (arch-rec §3 amendment #5)** — mirror Discord `:1537-1547`. Multipart 4xx (e.g., `Bad Request: photo_invalid_dimensions`, `PHOTO_INVALID`) is treated as **non-transient**: NO `record_failure` (zero breaker pressure; systematically-rejected images don't poison the text breaker). Transport errors (5xx, network) keep 3× exponential backoff + record.

**>50MB / MIME-miss** — skip image, WARN, deliver text. Multi-image: each image checked independently; size/MIME failures WARN-logged per image and skipped (text continues with remaining images, in order).

**Caption >1024** (arch-rec §3 amendment #8) — Telegram `sendPhoto`/`sendDocument` caption limit is 1024 chars. If the caption exceeds: truncated caption on the image **PLUS** a full-text follow-up `sendMessage` (text floor invariant).

**`parse_mode=None` for image captions — FIRM DECISION** (closes OQ#7 in prior plan). Mermaid ` ``` ` fences contain `<` characters (e.g., `subgraph A[Label]`); `parse_mode="HTML"` would interpret these as HTML tags and corrupt the block. Default `parse_mode=None` keeps the caption as plain text — safe for Mermaid fences, no user-visible encoding artifacts.

**Upload INSIDE per-chat lock** (arch-rec §3 amendment #10) — image upload must be inside the per-chat LRU lock. Otherwise: chat-A image-A starts uploading, chat-A text-A interleaves, chat-A image-B (from another send) interleaves → ordering hazard. Lock the entire send op (including image) under per-chat LRU lock.

Module-level helper:

```python
def _log_image_metadata(image_id: str, size_bytes: int | None, content_type: str | None) -> None:
    """Standardized image metadata line — bytes never included (arch-rec §3 amendment #12)."""
    short = image_id[:8]
    if size_bytes is not None and content_type is not None:
        logger.debug(f"telegram chart-image: image_id={short}... size={size_bytes} content_type={content_type}")
    else:
        logger.debug(f"telegram chart-image: image_id={short}...")
```

Add new helper near `_api_call` (after line 197):

```python
async def _api_call_multipart(
    self,
    method: str,
    *,
    file_bytes: bytes,
    filename: str,
    file_field: str = "photo",
    **params,
) -> dict:
    """Make a Telegram Bot API call with multipart file upload.

    Mirrors _api_call's 3-retry + exponential backoff + circuit-breaker
    discipline for transport errors (network / 5xx); multipart 4xx is
    NON-TRANSIENT — NO record_failure (arch-rec §3 amendment #5; mirror
    Discord :1537-1547). Systematically-rejected images exert zero breaker
    pressure; a real outage (transport / 5xx) DOES trip the breaker.
    """
    if not await self._circuit_breaker.can_execute():
        raise CircuitOpenError(f"Circuit open for Telegram API, method={method}")
    if not self._session:
        raise RuntimeError("Adapter not started - no HTTP session")

    url = self._get_api_url(method)
    form = aiohttp.FormData()
    form.add_field(file_field, file_bytes, filename=filename, content_type="image/png")
    for k, v in params.items():
        if v is not None:
            form.add_field(k, str(v))

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            async with self._session.post(
                url, data=form, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    error_desc = data.get("description", "Unknown error")
                    error_code = data.get("error_code", 0)
                    # Multipart 4xx classification (non-transient, NO breaker record)
                    if 400 <= error_code < 500:
                        raise TelegramAPIError(
                            f"Telegram API {error_code} (non-transient, no breaker): {error_desc}"
                        )
                    raise TelegramAPIError(
                        f"Telegram API error {error_code}: {error_desc}"
                    )
                await self._circuit_breaker.record_success()
                return data.get("result", {})
        except aiohttp.ClientError as e:
            last_error = e
            logger.warning(
                f"Telegram multipart call failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}"
            )
            await self._circuit_breaker.record_failure()
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)
    if last_error is not None:
        raise last_error
    raise RuntimeError("unreachable")
```

In `send()`, restructuring to put image upload INSIDE the per-chat lock (amendment #10). The existing text-send code (`:301+`) becomes `_send_text_only(reply_chat_id, content)` — extracted helper preserves the existing LLM artifact sanitization (chunking helper deferred per §phase-b-telegram-4096-deferred but the structural skeleton remains):

```python
async def send(self, message: OutgoingMessage) -> bool:
    """Send an OutgoingMessage to Telegram.

    Phase B restructure: image upload + text-send BOTH execute inside the
    per-chat LRU lock (arch-rec §3 amendment #10). The lock is acquired
    BEFORE any image processing so that chat-A image-A → chat-A text-A
    ordering is preserved (no interleaving from another send).
    """
    if self._status != SourceStatus.RUNNING:
        logger.warning(f"Cannot send: adapter not running (status={self._status})")
        return False

    reply_chat_id = message.metadata.get("reply_chat_id") if message.metadata else None
    if not reply_chat_id:
        reply_chat_id = message.external_user_id

    self.stop_typing(reply_chat_id)

    if not self._validate_chat_id(reply_chat_id):
        logger.error(f"Invalid Telegram chat_id: {reply_chat_id}")
        return False

    if not await self._circuit_breaker.can_execute():
        logger.warning(f"Circuit open, cannot send to {reply_chat_id}")
        return False

    if not await self._rate_limiter.wait_and_acquire(max_wait=10.0):
        logger.warning(f"Rate limit exceeded, dropping message to {reply_chat_id}")
        return False

    # Per-chat lock — acquired ONCE; image + text both happen inside (amendment #10)
    lock = await self._get_chat_lock(reply_chat_id)
    async with lock:
        # Phase B: chart-image native upload (Telegram sendPhoto/sendDocument)
        images = getattr(message, "images", None)
        delivered_count = 0
        if images:
            chat_id = reply_chat_id
            # parse_mode=None firm decision (Mermaid `<` breaks HTML mode)
            full_caption = message.content if message.content else ""
            caption_truncated = full_caption[:1024] if len(full_caption) > 1024 else full_caption
            caption_remaining = full_caption[1024:] if len(full_caption) > 1024 else ""

            for img in images:
                _log_image_metadata(img.image_id, img.size_bytes, img.content_type)
                try:
                    file_bytes = base64.b64decode(img.bytes_b64)
                except Exception as e:
                    logger.warning(
                        f"telegram image decode failed image_id={img.image_id[:8]}...: {e}; "
                        f"skipping (continuing with text/siblings)"
                    )
                    continue

                # >50MB / MIME-miss skip
                if len(file_bytes) > TELEGRAM_DOCUMENT_MAX_BYTES:
                    logger.warning(
                        f"telegram image too large: image_id={img.image_id[:8]}... "
                        f"size={len(file_bytes)} > {TELEGRAM_DOCUMENT_MAX_BYTES}; skipping"
                    )
                    continue
                if img.content_type not in CHART_IMAGE_MIME_WHITELIST:
                    logger.warning(
                        f"telegram image MIME miss: image_id={img.image_id[:8]}... "
                        f"content_type={img.content_type!r}; skipping"
                    )
                    continue

                if len(file_bytes) <= TELEGRAM_PHOTO_MAX_BYTES:
                    method = "sendPhoto"
                    file_field = "photo"
                else:
                    method = "sendDocument"
                    file_field = "document"

                try:
                    await self._api_call_multipart(
                        method,
                        file_bytes=file_bytes,
                        filename=img.filename,
                        file_field=file_field,
                        chat_id=chat_id,
                        caption=caption_truncated,
                        # parse_mode=None (default) — Mermaid `<` safety
                    )
                    delivered_count += 1
                except TelegramAPIError as e:
                    logger.warning(f"Telegram {method} failed: {e}; skipping image")
                    continue
                except Exception as e:
                    logger.warning(f"Telegram image upload unexpected error: {e}; skipping image")
                    continue

            # TEXT FLOOR GUARD (approver iter-002 blocking #1): if ALL image
            # uploads failed, the FULL content MUST still be delivered as text —
            # including the content <= 1024 case (caption_remaining == "") where
            # neither caption branch fires. Without this guard nothing is sent,
            # return True lies about success, and amendment-#22 store.delete
            # would delete the never-delivered image.
            if delivered_count == 0:
                await self._send_text_only(reply_chat_id, message.content)

            # Caption >1024: full-text follow-up sendMessage (text floor — amendment #8)
            if caption_remaining and delivered_count > 0:
                try:
                    await self._send_text_only(reply_chat_id, caption_remaining)
                except Exception as e:
                    logger.warning(f"telegram caption-follow-up text send failed: {e}")
        else:
            # No images — original text-send code path runs unchanged
            await self._send_text_only(reply_chat_id, message.content)

        return True
```

Existing text-send code (`:301+`) extracted as `_send_text_only(reply_chat_id, content)` private method — preserves LLM artifact sanitization. Multi-image drop discipline (amendment #7): each image that fails → WARN-logged with `image_id[:8]` + size + content_type; **never silently dropped**. If all images fail, text follows (text floor).

Token bucket (`:296`) + per-chat LRU lock (`MAX_CHAT_LOCKS=1000`) preserved.

### 7. `daemon/sources/adapters/slack/adapter.py:380` — Slack native delivery (single files_upload_v2, capability cached flag, classify-before-record, upload-inside-lock)

**Primary pattern (arch-rec §3 amendment #9) — DELETE the upload-then-`postMessage(file=)` sketch.** Upload is a SINGLE `files_upload_v2(channel_id, filename, content, initial_comment)` call (slack-sdk 3.42.0 AsyncWebClient; verify method signature at impl). The `chat.postMessage(file=)` follow-up is removed — `file=` is an undocumented kwarg with double-render risk.

**Capability detection (arch-rec §3 amendment #4)** — `missing_scope`/`files:write`/`not_in_channel` errors must classify BEFORE `record_failure`. Verified defect: `_call_slack_api:326-329` records failure on ANY `ok=false` (arch-rec cites `:340-343` — line drift, defect real; verify at impl). Fix: extract error classification into the call wrapper or `_safe_api_call`; classify `missing_scope`/`files:write`/`not_in_channel` as **non-failure for breaker purposes** (raise a dedicated `SlackCapabilityError` that does NOT pass through `record_failure`); cache per-token capability flag → **zero API calls once flagged**, WARN-once-per-channel thereafter (test asserts: 5 `missing_scope` sends → consecutive_failures == 0, only 1 actual API call).

**Upload INSIDE per-channel lock** (arch-rec §3 amendment #10) — acquire the per-channel lock BEFORE image upload (currently at `:457`); upload + postMessage (if any) BOTH happen inside the lock. Same ordering-hazard rationale as Telegram.

**`initial_comment` length limit (arch-rec §3 amendment #8)** — Slack's `initial_comment` has a limit (~4000 chars). If `message.content` exceeds: truncated `initial_comment` on the file **PLUS** a follow-up `chat.postMessage` with the full text (text floor).

Module-level helper:

```python
def _log_image_metadata(image_id: str, size_bytes: int | None, content_type: str | None) -> None:
    """Standardized image metadata line — bytes never included (arch-rec §3 amendment #12)."""
    short = image_id[:8]
    if size_bytes is not None and content_type is not None:
        logger.debug(f"slack chart-image: image_id={short}... size={size_bytes} content_type={content_type}")
    else:
        logger.debug(f"slack chart-image: image_id={short}...")


class SlackCapabilityError(Exception):
    """Raised when Slack rejects an API call due to missing capability.

    Distinct from generic SlackAPIError so callers can classify without
    touching circuit-breaker state (arch-rec §3 amendment #4).
    """
```

In `send()` (between line 446 DB lookup and line 452 circuit-breaker check; the per-channel lock at `:457` wraps the new image branch):

```python
# Capability flag — per-token, per-method (e.g. files_upload_v2).
# Once flagged, ZERO subsequent API calls until process restart. Per-arch-rec §3
# amendment #4: missing_scope must NOT trip any breaker.
self._slack_capability_flags: set[str] = set()  # add to __init__

# In send(), after DB lookup + BEFORE circuit-breaker check:
# (pre-check first to avoid wasted breaker execution)
flagged_methods = getattr(self, "_slack_capability_flags", set())
if images and "files_upload_v2" in flagged_methods:
    # Capability already known missing — skip API call, deliver text only.
    logger.debug(f"slack capability flag set: skipping files_upload_v2 for channel {channel_id}")
    images = None

if not await self._circuit_breaker.can_execute():
    logger.warning(f"Circuit open, cannot send to channel {channel_id}")
    return False

# Get per-channel lock
lock = await self._get_channel_lock(channel_id)

async with lock:
    try:
        # ... existing text-prep blocks-vs-text branching ... :469-498 ...
        # Phase B: chart-image native upload (Slack single files_upload_v2 — amendment #9)
        images = getattr(message, "images", None)
        image_uploaded = False
        if images and "files_upload_v2" not in flagged_methods:
            img = images[0]
            _log_image_metadata(img.image_id, img.size_bytes, img.content_type)
            try:
                file_bytes = base64.b64decode(img.bytes_b64)
            except Exception as e:
                logger.warning(
                    f"slack image decode failed image_id={img.image_id[:8]}...: {e}; "
                    f"skipping"
                )
                file_bytes = None

            if file_bytes is not None:
                # Slack initial_comment length limit (~4000 chars; verify at impl)
                initial_comment = message.content[:INITIAL_COMMENT_MAX] if message.content else ""
                caption_remaining = (
                    message.content[INITIAL_COMMENT_MAX:]
                    if message.content and len(message.content) > INITIAL_COMMENT_MAX
                    else ""
                )
                try:
                    # SINGLE call — no separate postMessage() follow-up for the file itself.
                    # _safe_api_call returns an (ok, result) TUPLE on its failure contract —
                    # UNPACK it (iter-002 note: calling .get(...) on the tuple is an
                    # AttributeError on every upload; verify exact tuple shape at impl).
                    ok, result = await self._safe_api_call(
                        "files_upload_v2",
                        channel_id=channel_id,
                        filename=img.filename,
                        content=file_bytes,
                        initial_comment=initial_comment,
                    )
                    if ok and result:
                        image_uploaded = True
                    else:
                        logger.warning(
                            f"Slack files_upload_v2 returned non-ok for channel {channel_id}: {result}; text fallback"
                        )
                except SlackCapabilityError as e:
                    # Classify BEFORE record_failure (amendment #4) — set flag, never re-attempt
                    err_str = str(e).lower()
                    if "missing_scope" in err_str or "files:write" in err_str:
                        self._slack_capability_flags.add("files_upload_v2")
                        logger.warning(
                            f"Slack channel {channel_id}: missing files:write scope. "
                            f"USER ACTION REQUIRED: grant files:write scope in Slack app config. "
                            f"Image upload disabled until scope granted; text-only delivery."
                        )
                    elif "not_in_channel" in err_str:
                        logger.warning(
                            f"Slack channel {channel_id}: bot not in channel. "
                            f"Invite the bot to the channel; text-only delivery."
                        )
                    else:
                        logger.warning(f"Slack files_upload_v2 capability error: {e}; text fallback")
                except Exception as e:
                    logger.warning(f"Slack files_upload_v2 failed: {e}; text fallback")

            # Caption >INITIAL_COMMENT_MAX: full-text follow-up chat.postMessage (amendment #8)
            if image_uploaded and caption_remaining:
                try:
                    await self._safe_api_call(
                        "chat.postMessage",
                        channel=channel_id,
                        text=caption_remaining,
                    )
                except Exception as e:
                    logger.warning(f"slack caption-follow-up text post failed: {e}")
        else:
            # No images — existing text-postMessage code path runs unchanged
            pass

        # ... existing chat.postMessage call for text ...
```

**`_safe_api_call` / `_call_slack_api` defect fix (amendment #4).** The verified defect at `_call_slack_api:326-329` records failure on ANY `ok=false`. Fix: in `_call_slack_api`, after parsing `response`, classify `error` string before `record_failure`:

```python
# _call_slack_api (around :326-329) — REPLACE:
if not response.get("ok"):
    error = response.get("error", "unknown_error")
    # Capability errors → raise SlackCapabilityError (no breaker record)
    if error in ("missing_scope", "not_in_channel", "channel_not_found", "is_archived"):
        raise SlackCapabilityError(f"Slack capability error: {error}")
    # Other errors → record_failure (existing behavior)
    await self._circuit_breaker.record_failure()
    raise SlackAPIError(f"Slack API error: {error}")
```

AND in the surrounding `try/except` of `_call_slack_api` (live `adapter.py:344-346`), add **BEFORE** the generic `except Exception` handler (approver iter-002 blocking #2a):

```python
    except SlackCapabilityError:
        raise  # propagate UNCAUGHT — the generic handler calls record_failure()
               # and re-raises as SlackAPIError, re-arming the exact
               # breaker-poison defect this fix targets.
```

**`SlackCapabilityError` propagation (R4, approver iter-002 blocking #2b — corrected mechanism)** — `_safe_api_call` (`:340-378`) must NOT catch `SlackCapabilityError`: the exception propagates UNCAUGHT through `_call_slack_api` (via the `except SlackCapabilityError: raise` placed before its generic handler) AND through `_safe_api_call`, reaching `send()`'s existing `except SlackCapabilityError` handler, which sets the per-token capability flag + WARN-once-per-channel. The `(False, None)` tuple-return failure contract stays reserved for transport/API errors ONLY — a caught-and-returned capability error would make the send()-side isinstance distinguish impossible (the mechanism this iteration rejected). ONE mechanism, end to end.

USER ACTION ITEM: operator must grant `files:write` scope. Doc-update task: `docs/sources/slack-setup.md` YAML manifest (`:17-61`, `scopes:` block `:35-48`) AND scopes table (`:69-82`) get a `files:write` row.

### 8. `docs/sources/slack-setup.md:35-82` — scope table update

USER ACTION ITEM: extend the YAML manifest (lines 35-48) and the scopes table (lines 69-82) with one row:

```yaml
      - files:write   # Phase B: upload chart-render images natively
```

```markdown
| `files:write` | Upload files to channels (Phase B chart-image delivery) |
```

This ships with the code change. Operators must manually grant the scope in their Slack app config + reinstall; until then, capability detection in `send()` degrades to text-only delivery + one-time WARN log per channel.

---

## Tasks (ordered, each with verification)

### Phase B.1 — Outbound extension + extraction at BOTH seams (arch-rec §1)
| # | Task | Depends on | Acceptance (verification) |
|---|------|------------|---------------------------|
| 1 | Add `ImageAttachment` dataclass with `bytes_b64 = field(repr=False)` + redacting `__repr__`; extend `OutgoingMessage.images` (daemon/sources/base.py:52-60) | — | `grep -n "images:" daemon/sources/base.py` shows the new field; existing test imports still resolve; `repr(ImageAttachment(...))` does NOT contain `bytes_b64` payload |
| 2 | Add `manager` property on `SourceRegistry` (daemon/sources/registry.py:~236) | — | `python -c "from daemon.sources.registry import SourceRegistry; ..."` loads cleanly; the property returns the injected manager |
| 3 | Add `extract_chart_images()` (per-id dedupe first-occurrence order — amendment #2) + `_MARKER_RE` + `_NEAR_MISS_RE` (sweeper — amendment #14) + `_resolve_chart_images()` (per-id isolation — amendment #3; provenance gate `feature == "chart-render"` — amendment #13) | 1, 2 | New unit tests for valid/malformed/multiline/duplicate/near-miss pass; per-id isolation test (one bad id → siblings still deliver); provenance gate test (foreign-feature id → text fallback) |
| 4 | Inject extraction+resolution into **BOTH** `dispatch_message` AND `dispatch_completed` as the LAST content transformation before OutgoingMessage, AFTER adapter lookup (arch-rec §1 pin, verbatim) | 1, 2, 3 | Both-seam integration test: feed content with marker to `dispatch_message` → adapter.send called with `outgoing.images` populated and marker-stripped content; same for `dispatch_completed` |
| 5 | Add `store.delete(image_id)` after successful `adapter.send(outgoing)` in the delivering lane (amendment #22 — ADOPTED) | 1, 3, 4 | `test_store_delete_after_success` — successful send → `store.delete` called; failed send → NOT called; API-source → NOT called; double-delete from both lanes impossible (verify by sending same source to both seams in test); delete fires ONLY on genuine delivery success — the Telegram `delivered_count==0` guard (iter-002 blocking #1) closes the false-success path that would delete never-delivered images |
| 6 | Add `DISCORD_FILE_MAX_BYTES` + Telegram + Slack + MIME whitelist + `INITIAL_COMMENT_MAX=4000` to `daemon/constants.py` | — | `python -c "from daemon.constants import ..."` resolves all new constants |
| 7 | Add shared logging helper `_log_image_metadata(image_id, size_bytes, content_type)` in dispatcher.py — bytes never in any log line (amendment #12) | 1 | Helper test: log line contains `image_id[:8]` + size + content_type, NEVER contains base64 payload |

### Phase B.2 — Discord (atomic-first-unit + files=[…] + empty-content guard)
| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 8 | **Verify** current discord.py 2.7.1 `discord.File(fp, filename)` signature against https://discordpy.readthedocs.io/en/stable/api.html#discord.File — record findings in commit message | — | Doc URL + signature snippet in commit body |
| 9 | Extend `if not content:` early-return at `:1575-1577` to `if not content and not message.images:` (amendment #11) | 8 | `test_discord_empty_content_with_images` — content="" + images=[one PNG] → send proceeds; content="" + images=None → early-return |
| 10 | Modify `_send_single_chunk` to accept `file=None` AND `files=None` kwargs (mutually exclusive per discord-py 2.7.1) | 8 | Existing tests pass; new test asserts `kwargs["file"]` set on chunk 1 for single-image; `kwargs["files"]` set on chunk 1 for N-image; chunks 2..N have NEITHER |
| 11 | Modify `send()` to construct `discord.File`/`[discord.File]` + attach to chunk 1 (atomic first unit — amendment #6) + retry text-only ONCE for chunk 1; file NEVER re-attempted on later chunks | 1, 9, 10 | `target.send` asserted with `file=discord.File` for chunk 1 (single-image) or `files=[...]` for N; chunks 2..N text-only; upload-fail test: chunk-1 fail → text-only retry on chunk-1 succeeds; chunk-2 fails (no file) → return False |
| 12 | Multi-image discipline (amendment #7): order preserved, no silent drops, WARN-on-drop | 11 | `test_discord_multi_image` — 3-image test markers; assert all 3 in `files=[...]` in order; missing/corrupt bytes for image #2 → WARN-logged + image #2 dropped from `files=[...]` (NOT silently) + images 1, 3 still delivered |

### Phase B.3 — Telegram (4xx non-transient + >50MB skip + caption follow-up + parse_mode=None firm + upload-inside-lock)
| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 13 | **Verify** current Telegram Bot API `sendPhoto` / `sendDocument` constraints at https://core.telegram.org/bots/api#sendphoto and https://core.telegram.org/bots/api#senddocument — record photo/document size limits + accept-MIME in commit message | — | Doc URLs + verified size/MIME table in commit body |
| 14 | Implement `_api_call_multipart()` helper in telegram.py (mirror retry/breaker semantics for transport errors; multipart 4xx classified non-transient, NO breaker record — amendment #5) | 13 | Helper test: FormData with the photo/document field; 3 retries on `aiohttp.ClientError`; multipart 4xx raises `TelegramAPIError` WITHOUT calling `record_failure` (verify breaker counter == 0); circuit-breaker open raises `CircuitOpenError` |
| 15 | Restructure `send()` so image upload + text-send BOTH execute INSIDE per-chat lock (amendment #10); call `sendPhoto` (≤10 MB) / `sendDocument` (>10 MB) | 1, 6, 14 | sendPhoto test asserts `session.post(data=<FormData with photo field>)`; >10 MB test asserts sendDocument; per-chat-lock test asserts no interleaving (concurrency test with two simultaneous sends to same chat) |
| 16 | >50MB / MIME-miss skip + WARN + deliver text (amendment §3 Focus 3) | 1, 15 | `test_telegram_oversize_skip` — 50MB+1 file → WARN-logged + skipped; MIME-miss test: text content_type → WARN + skipped + text delivered; `test_telegram_all_images_failed_text_floor` — ALL uploads fail + content ≤1024 → full text delivered by the delivered_count==0 guard (iter-002 blocking #1) |
| 17 | Caption >1024: truncated caption on photo + full-text follow-up `sendMessage` (amendment #8) | 15 | `test_telegram_caption_followup` — content with 1500 chars + 1 image → `sendPhoto(caption=content[:1024])` + `sendMessage(text=content[1024:])` |
| 18 | `parse_mode=None` for image captions — FIRM (closes OQ#7; Mermaid `<` breaks HTML mode) | 15 | `test_telegram_parse_mode_none` — image caption has NO `parse_mode` kwarg in `session.post` FormData |

### Phase B.4 — Slack (single files_upload_v2 + capability cached flag + classify-before-record + upload-inside-lock)
| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 19 | **Verify** current slack_sdk 3.42.0 `AsyncWebClient.files_upload_v2` method signature at https://api.slack.com/methods/files.uploadV2 — record kwargs (`channel_id`, `filename`, `content`, `initial_comment`, etc.) in commit message | — | Doc URL + verified kwargs in commit body |
| 20 | Fix defect at `_call_slack_api:326-329` (records failure on ANY `ok=false`): add `SlackCapabilityError` exception; classify `missing_scope` / `not_in_channel` / `channel_not_found` / `is_archived` BEFORE `record_failure` (amendment #4); `except SlackCapabilityError: raise` placed BEFORE the generic handler so it propagates UNCAUGHT (R4 single mechanism) | 19 | `test_slack_capability_classified_before_record` — `missing_scope` ok=false raises `SlackCapabilityError` and DOES NOT call `record_failure`; `consecutive_failures == 0` after the call |
| 21 | Add `_slack_capability_flags: set[str]` to `__init__`; modify `send()` to pre-check flag and skip API call entirely when set (amendment #4 — zero API calls once flagged) | 20 | `test_slack_capability_flag_zero_api_calls` — 5 `missing_scope` sends → only 1 actual API call (the first); flag set after first; subsequent 4 short-circuit before API call; `consecutive_failures == 0` throughout |
| 22 | Modify `send()` to call SINGLE `files_upload_v2(channel_id, filename, content, initial_comment)` (DELETE upload-then-`postMessage(file=)` sketch — amendment #9); upload INSIDE per-channel lock (amendment #10); UNPACK the `_safe_api_call` (ok, result) tuple (R4 — .get on the tuple AttributeErrors every upload); SlackCapabilityError arrives via UNCAUGHT propagation (not a tuple return) | 1, 19, 21 | `test_slack_single_files_upload_v2` — exactly one `_safe_api_call("files_upload_v2", ...)` per send with verified kwargs (channel_id, filename, content, initial_comment); NO `postMessage(file=)` follow-up; per-channel-lock test asserts no interleaving |
| 23 | `initial_comment` length limit: truncated + full-text follow-up `chat.postMessage` (amendment #8) | 22 | `test_slack_initial_comment_followup` — content >INITIAL_COMMENT_MAX → `files_upload_v2(initial_comment=content[:MAX])` + `chat.postMessage(text=content[MAX:])` |
| 24 | WARN-once-per-channel for `missing_scope` — text delivered + USER ACTION ITEM flagged | 21 | `test_slack_warn_once` — 5 sends to same channel with `missing_scope` → exactly ONE WARN log line (not 5); WARN text contains "USER ACTION REQUIRED" + "files:write scope" |

### Phase B.5 — Tests (BOTH-seam regression + amendments)
| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 25 | Unit tests for `extract_chart_images` (valid/malformed/multiline/duplicate/near-miss/empty); per-id dedupe first-occurrence (amendment #2) | 3 | All new tests pass; existing `test_sources_dispatcher.py` tests pass; near-miss strip-only test: matches near-miss pattern → stripped from output, NEVER in returned `image_ids`; valid marker in same input → extracted |
| 26 | **REPLACE prior acceptance ("marker NOT extracted in dispatch_message")** — it pinned the defect. New test: `test_progressive_lane_extracts_and_strips` — feed content with marker to `dispatch_message`, assert `adapter.send(outgoing)` called with `outgoing.images` populated + `outgoing.content` marker-free (arch-rec §1) | 4 | Test passes against the in-place code |
| 27 | `test_progressive_then_completed_no_double_send` — feed content with marker, call `dispatch_message` first (returns True → `_progressive_sent_sources.add(source)`); call `dispatch_completed` second → returns early at `:124-128`; assert adapter.send called EXACTLY ONCE (not twice) | 4 | Test asserts `mock_adapter.send.call_count == 1` across the two calls |
| 28 | Regression test: API-source dispatch (no `:`) keeps marker in content + `open_full` NOT called (defense against future regression in the no-colon skip; arch-rec §1) | 4 | Test asserts: (a) marker preserved in `outgoing.content` for source=`"api"`; (b) spy on `store.open_full` returns `call_count == 0`; (c) `adapter.send` was NOT called (no chat adapter for `"api"`) |
| 29 | Regression test: `internal_agent:*` colon-source returns at adapter lookup before extraction (no fetch, no strip) | 4 | Test asserts: spy on `store.open_full` returns `call_count == 0`; content unchanged |
| 30 | Provenance gate test (amendment #13): stub `store.open_full` to return a `TmpImageRecord` with `provenance.feature="screenshot"` → dispatcher drops the image + text fallback + WARN | 3 | Test asserts `outgoing.images is None` + WARN log + `adapter.send` called with text-only |
| 30a | `test_image_resolution_returns_nonempty_bytes` — companion to #30 (approver-required, load-bearing, R4-aligned): stub the store accessor PAIR — `open_full` → record with `provenance.feature="chart-render"`, `open_with_meta` → NON-EMPTY bytes; assert `ImageAttachment.bytes_b64` decodes byte-equal to the stubbed bytes — pins the resolution path against the VERIFIED TmpImageRecord shape (no `.blob` field exists; the hasattr hedge is removed) | 3 | Test asserts decoded bytes non-empty and byte-equal to the stubbed `open_with_meta` bytes |
| 31 | Discord adapter test: chunk 1 atomic-first-unit + `files=[…]` N-image + empty-content-with-images guard + upload-fail text-only retry | 9, 10, 11, 12 | All 4 sub-cases pass |
| 32 | Discord adapter test: multi-image order-preserved no-drop | 12 | Per amendment #7 — 3-image test asserts all 3 in `files=[...]` in marker order |
| 33 | Telegram adapter test: sendPhoto multipart assertion | 15 | `session.post(data=<FormData with photo>)` + photo field + caption (no parse_mode) |
| 34 | Telegram adapter test: 4xx-non-transient classification (no circuit-breaker record) | 14 | Mock Telegram 400 → assert `circuit_breaker.consecutive_failures == 0`; mock 500 → assert recorded |
| 35 | Telegram adapter test: sendPhoto→sendDocument ladder + >50MB skip + caption>1024 follow-up + parse_mode=None | 15, 16, 17, 18 | All 4 sub-cases pass |
| 36 | Slack adapter test: SINGLE `files_upload_v2` call (NOT upload-then-postMessage) | 22 | Assert exactly one `_safe_api_call("files_upload_v2", ...)`; assert NO `chat.postMessage(file=)` follow-up |
| 37 | Slack adapter test: `missing_scope` classified BEFORE `record_failure` (consecutive_failures==0) | 20 | Mock `missing_scope` ok=false → `record_failure` NOT called; `SlackCapabilityError` raised AND propagates uncaught through both `_call_slack_api` and `_safe_api_call` (R4 single mechanism — no catch-and-return path) |
| 38 | Slack adapter test: capability flag zero-API-call test (5 sends → only 1 actual API call) | 21 | `mock_api_call.call_count == 1` across 5 sends |
| 39 | Slack adapter test: `initial_comment` truncation + follow-up + upload-inside-lock | 22, 23 | Per-channel-lock test asserts no interleaving; follow-up test asserts both calls in order |
| 40 | End-to-end integration test (`tests/test_outbound_image_delivery.py`): mock source + mock `tmp_image_store` populated with a tiny PNG → dispatcher → adapter → assert `sent_messages[-1].images` populated and `sent_messages[-1].content` marker-free | 3, 4, 26 | Test passes via mock e2e (BOTH seams) |
| 41 | End-to-end integration test: `store.delete` after success in delivering lane | 5 | Test asserts `store.delete.call_count == 1` after successful send; `== 0` after failed send |
| 42 | Full-chain API-caller keeps marker (e2e twin) — POST `/api/messages` → assert marker byte-for-byte in `result.content` | 28 | E2E twin test passes; matches the regression test at #28 |

### Phase B.6 — Docs + finalize
| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 43 | Update `docs/sources/slack-setup.md` YAML manifest `scopes:` block (`:35-48`) AND scopes table (`:69-82`) — add `files:write` row with USER ACTION ITEM | 24 | Doc diff visible in PR; YAML valid; operator can re-run setup |
| 44 | Decisions.md APPEND: §phase-b addendum (R2 record per arch-rec §3 amendment #20) | — | decisions.md has new sections appended |
| 45 | Self-review: full grep over changed files for `TODO`, `FIXME`, `XXX`, `pass  # stub`; verify dispatcher's two extraction injection sites are byte-identical except for the `_progressive_sent_sources.add(source)` line (only in `dispatch_message`) | all | Zero hits; no stubs left in shipped code; both-seam code structurally symmetric |

---

## Dependencies

| Direction | Dependency |
|-----------|-----------|
| **Upstream (locked)** | Phase A's `decisions.md` §marker (regex exact), §capture (50–300 KB assumption), §http-api (marker-preserved-for-API), §degradation (charter-side failures only — Phase B owns the platform-side mirror) |
| **Upstream (verified)** | `TmpImageStore.open_full` (`daemon/services/tmp_image_store.py:480-494`) — record read (provenance gate) + `TmpImageStore.open_with_meta` (`:436-455`) — bytes read (bytes, content_type, sha256_hex); `manager.tmp_image_store` property (`daemon/manager.py:2545`) — returns `None` when not injected (test path) |
| **Downstream** | Phase D (cross-cutting tests/version/docs consolidation; picks up Phase B's restart-promote matrix) |
| **Independent** | Phase C (agent-prompt-only); Phase A (already shipped/approved) |

---

## Test Strategy

| Layer | Pattern | File |
|-------|---------|------|
| Pure-function unit | direct call + assert | `tests/test_sources_dispatcher.py::extract_chart_images_*` |
| Adapter unit | `AsyncMock` on inner method (`_send_single_chunk`, `_api_call`, `_safe_api_call`); established pattern at `test_discord_adapter.py:1430-1477`, `test_telegram_adapter.py:173-237`, `test_slack_adapter.py` | new tests appended to existing files |
| Adapter upload-fail | raise-once-then-succeed pattern via side_effect | per-adapter |
| MockSourceAdapter | `tests/e2e/mock_source_server.py:99-110` — `sent_messages.append(message)` already captures entire `OutgoingMessage`; no code change needed (images field auto-captured) | n/a — verify-only |
| Dispatcher integration | mock `tmp_image_store` + mock registry + real dispatcher | new in `test_sources_dispatcher.py` |
| End-to-end | mock source adapter + mock tmp_image_store + dispatcher + adapter chain | NEW `tests/test_outbound_image_delivery.py` |
| Manual smoke | Discord bot user + Slack/Telegram with real credentials | post-merge, gated on USER ACTION ITEM for Slack scope |

Mock fixture: a 1×1 PNG (89 bytes) saved to a fixture tmp_image_store; `image_id` matches `^[a-f0-9]{32}$` so the regex accepts it.

---

## Acceptance Criteria (checkboxable)

### User story (THE primary acceptance)
- [ ] **Discord user story**: user asks for a chart on Discord → agent calls `generate_chart()` → user receives the rendered PNG as a Discord attachment in the same message as the explanation text; **verified via mock-adapter e2e test (BOTH seams — progressive AND completed)** AND manual smoke against a real Discord channel.
- [ ] **Slack user story**: same — verified via mock e2e; **gated on operator granting `files:write` scope** (USER ACTION ITEM); without scope, capability flag set after first attempt, text-only + ONE WARN log total, zero API calls subsequently.
- [ ] **Telegram user story**: same — verified via mock e2e; sized ≤10 MB uses sendPhoto, >10 MB uses sendDocument; beyond 50 MB falls to text (skip + WARN).

### Contract preservation (zero regression)
- [ ] **Marker never visible to chat users** — `outgoing.content` passed to chat adapters has marker stripped; near-miss patterns also stripped (cosmetic junk-line cleanup).
- [ ] **API callers see marker + Mermaid** — HTTP-API sources (no `:`) skip dispatch path; marker stays in `result.content` returned to `POST /api/messages` etc.; e2e twin test asserts byte-for-byte marker presence.
- [ ] **Phase A contract untouched** — `decisions.md` §marker LOCKED regex byte-stable (no relaxation in v1); `chart_tools.py:525` passthrough unchanged.

### Both-seam extraction (arch-rec §1 — non-negotiable)
- [ ] **PROGRESSIVE lane extracts + strips + uploads** — `dispatch_message` calls `adapter.send(outgoing)` with `outgoing.images` populated and `outgoing.content` marker-free. Test: `test_progressive_lane_extracts_and_strips`.
- [ ] **COMPLETED lane extracts + strips + uploads** — `dispatch_completed` calls `adapter.send(outgoing)` with same shape. Test: `test_completed_lane_extracts_and_strips`.
- [ ] **Once-only structural** — `test_progressive_then_completed_no_double_send`: progressive delivered → completed returns early at `:124-128`; `adapter.send.call_count == 1` across both calls.
- [ ] **API-source skips both seams** — `test_full_chain_api_caller_keeps_marker`: marker verbatim in `outgoing.content`, `store.open_full` spy returns `call_count == 0`, `adapter.send` not called.
- [ ] **Internal-report/internal_error_report/incomplete internal_agent:* colon-sources skip at adapter lookup before extraction** — test asserts `store.open_full` spy returns `call_count == 0`.

### Multi-image discipline (arch-rec §3 amendment #7)
- [ ] **Marker order preserved** — `extract_chart_images` returns `image_ids` in first-occurrence order (per-id dedupe).
- [ ] **NO silent drops** — every dropped image WARNs with `image_id[:8]` + size + content_type. Discord `files=[…]` constructed from full image list; missing/corrupt image → WARN + drop from list (not silent). Telegram: per-image failure → WARN + skip; remaining images continue. Slack: per-image failure → WARN + skip; text delivered.
- [ ] **All images delivered in single send** when no failures — Discord `files=[…]` (N); Telegram sequential sendPhoto/sendDocument per image (or upgrade to media-group API in v2); Slack: single files_upload_v2 with N files (verify at impl).

### Provenance gate (arch-rec §3 amendment #13)
- [ ] **`feature == "chart-render"` required** — `store.open_full` returning `feature="screenshot"`/`feature="clipboard"`/anything-else → image dropped, text fallback, WARN. Test: `test_provenance_gate_rejects_foreign_feature`.
- [ ] **Missing provenance → dropped** — `provenance is None` or `provenance.get("feature")` missing → text fallback (default-deny).

### Discord atomic-first-unit (arch-rec §3 amendment #6)
- [ ] **Chunk 1 = atomic first unit** — `file`/`files` attached ONLY on chunk 1; chunks 2..N text-only.
- [ ] **ONE text-only retry of chunk 1 on file-upload failure** — file NEVER re-attempted on later chunks.
- [ ] **Empty-content-with-images is valid** — `if not content and not message.images:` early-return at `:1575-1577` (extending existing guard); content="" + images=[PNG] → send proceeds. Test: `test_discord_empty_content_with_images`.

### Telegram circuit-breaker guardrail (arch-rec §3 amendment #5)
- [ ] **Multipart 4xx classified non-transient** — `_api_call_multipart` does NOT call `record_failure` on 4xx (mirror Discord `:1537-1547`); test asserts `consecutive_failures == 0` after 4xx.
- [ ] **Transport errors keep 3× exponential backoff + record** — `aiohttp.ClientError` → `record_failure` (existing behavior preserved for outages).

### Slack capability classification (arch-rec §3 amendment #4)
- [ ] **`missing_scope` classified BEFORE `record_failure`** — `_call_slack_api:326-329` defect fixed; new `SlackCapabilityError` raised, `record_failure` NOT called, `consecutive_failures == 0`.
- [ ] **Cached per-token capability flag → zero API calls once flagged** — first `missing_scope` adds `"files_upload_v2"` to `_slack_capability_flags`; subsequent sends short-circuit BEFORE the API call. Test: 5 sends → only 1 actual API call.
- [ ] **WARN-once-per-channel** — first `missing_scope` per channel → ONE WARN log line (with "USER ACTION REQUIRED" + "files:write scope" text); subsequent sends do NOT re-WARN.

### Slack single-call upload (arch-rec §3 amendment #9)
- [ ] **SINGLE `files_upload_v2` per send** — NO `chat.postMessage(file=)` follow-up for the file itself. Test: exactly one `_safe_api_call("files_upload_v2", ...)` per send; no `postMessage(file=)` follow-up.
- [ ] **`initial_comment` truncation + follow-up** — content >INITIAL_COMMENT_MAX → `files_upload_v2(initial_comment=content[:MAX])` + `chat.postMessage(text=content[MAX:])`.

### Upload-inside-lock (arch-rec §3 amendment #10)
- [ ] **Telegram image upload INSIDE per-chat LRU lock** — concurrency test: two simultaneous sends to same chat serialize correctly (no image/text interleaving).
- [ ] **Slack image upload INSIDE per-channel lock** — same concurrency test pattern.

### Store.delete after success (arch-rec §3 amendment #22 — ADOPTED)
- [ ] **`store.delete(image_id)` called after successful chat send** — both lanes (progressive + completed) trigger delete on success.
- [ ] **NOT called on send failure** — failed `adapter.send` → no delete (image stays for retry).
- [ ] **NOT called for API-source** — `dispatch_message`/`dispatch_completed` for source=`"api"` never runs (skips at `:132`); no delete fired.
- [ ] **Double-delete impossible** — same source on both seams: one wins (delivers), the other returns early (no delete). Test: 2 sends, `store.delete.call_count == 1` total.

### Logging contract (arch-rec §3 amendment #12)
- [ ] **`ImageAttachment.bytes_b64 = field(repr=False)` + redacting `__repr__`** — `repr(ImageAttachment(...))` does NOT contain the base64 payload.
- [ ] **`OutgoingMessage.images` is transport-only** — NEVER persisted, NEVER logged whole. Logging format: `image_id[:8]` + `size_bytes` + `content_type` only.
- [ ] **Bytes NEVER in any log line** — dispatcher + all three adapters use `_log_image_metadata()` helper; test greps log output for absence of base64 prefixes.

### Near-miss strip-only sweeper (arch-rec §3 amendment #14)
- [ ] **LOCKED regex stays byte-stable** — `^<!-- ens-img:chart-render:[a-f0-9]{32} -->$` unchanged.
- [ ] **NEW sweeper pattern strips but never extracts** — `^\s*<!--\s*ens-img:chart-render:[^>\n]{0,64}-->\s*$` matches malformed/indented markers; removed from content; NOT added to `image_ids`. Test: near-miss input → stripped from output, `image_ids == []`.

### Adapter-semantics preservation
- [ ] Discord 2000-char chunking preserved (`_split_message` + `sent_count` order).
- [ ] Discord per-channel LRU lock + `DiscordSendSemaphore(5)` preserved (no new lock contention).
- [ ] Discord 429-exclusion + 4xx-not-transient rules preserved (no false circuit-breaker open).
- [ ] Telegram token bucket (30/30s) preserved.
- [ ] Telegram per-chat LRU lock (`MAX_CHAT_LOCKS=1000`) preserved.
- [ ] Slack blocks conversion for >400 chars preserved (`BLOCKS_CONTENT_THRESHOLD`).
- [ ] Slack per-channel lock preserved (slack:457).
- [ ] Slack circuit-breaker unchanged (except for the new `SlackCapabilityError` path).

### Failure paths
- [ ] `tmp_image_store.open_full` raises `TmpImageNotFound` → marker stripped, text delivered, WARN log.
- [ ] Per-id isolation: one bad id (e.g., TmpImageNotFound) → WARN + continue; siblings still deliver.
- [ ] Discord `discord.File(s)` construction raises → text-only path, no upload attempted.
- [ ] Discord chunk-1 with `file`/`files` raises → retry text-only ONCE on chunk 1; chunks 2..N skipped on retry-fail.
- [ ] Telegram `sendPhoto` API error (4xx non-transient) → image skipped; remaining images + text delivered.
- [ ] Telegram `sendPhoto` size >10 MB → `sendDocument` automatically.
- [ ] Telegram `sendDocument` size >50 MB → text fallback (rare edge).
- [ ] Telegram >50MB / MIME-miss → WARN + skip; remaining images + text delivered.
- [ ] Telegram ALL image uploads fail (4xx/MIME-miss/oversize — any mix) AND content ≤1024 (`caption_remaining == ""`) — full text STILL delivered via the `delivered_count == 0` guard; `send()` returns success only for the actual text delivery; `store.delete` never fires for undelivered images (approver iter-002 blocking #1).
- [ ] Telegram caption >1024 → truncated on image + full-text follow-up `sendMessage`.
- [ ] Slack `files_upload_v2` missing_scope → text-only + ONE WARN log per channel (not N); capability flag set; zero API calls thereafter.
- [ ] Slack `files.uploadV2` other error → text fallback + WARN.

### Tests
- [ ] All existing `test_sources_dispatcher.py` tests pass (zero regression).
- [ ] All existing `test_discord_adapter.py` tests pass.
- [ ] All existing `test_telegram_adapter.py` tests pass.
- [ ] All existing `test_slack_adapter.py` tests pass.
- [ ] New tests listed in Phase B.5 all pass.
- [ ] End-to-end `test_outbound_image_delivery.py` passes (BOTH seams).
- [ ] Full-chain API-caller keeps marker e2e twin test passes.

### Operational
- [ ] Restart required (daemon code change). Promote required (Phase B = daemon-side per dispatch note).
- [ ] Slack USER ACTION ITEM documented in commit body + decisions.md.
- [ ] Doc-verification TASK findings (TASK #8, #13, #19) recorded in commit body.
- [ ] Precedence: `architecture-recommendation.md` §3 governs over phase-plan prose on conflict.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | discord.py `discord.File` signature drift between 2.7.1 and current docs | Med | Low | TASK #8 verifies at impl; pinned at 2.7.1 (`docs/dependencies.txt`); test asserts the verified kwarg shape |
| 2 | slack_sdk `files_upload_v2` kwargs differ from current Slack API docs | Med | Low | TASK #19 verifies; TASK #20-22 is the explicit extension-or-new-helper decision |
| 3 | Telegram `sendPhoto` rejects 8MB white-bg PNG due to width/height/aspect ratio limits | Low | Low | `sendDocument` fallback; documented 10 KB; verify at impl (TASK #13) |
| 4 | `asyncio.to_thread(store.open_full, image_id)` blocks a worker thread under high concurrency | Low | Low | Sync open is ~1ms for 50-300KB; bounded by per-user send lock (LRU `MAX_SEND_LOCKS=10000`); no thread pool exhaustion |
| 5 | **Extraction placed on the wrong seam (arch-rec §1)** | **High** (DOA for user story) | **Med** (plan-as-written shipped this) | Both seams extract (arch-rec §1 pin, verbatim); test_progressive_lane_extracts_and_strips + test_progressive_then_completed_no_double_send pin the correct behavior |
| 6 | Slack `files:write` scope not granted in production | High (no Slack image delivery) | Med | Capability flag set after first attempt + text fallback + WARN-once (zero API calls thereafter); classify-before-record (no breaker poisoning); USER ACTION ITEM in commit + doc-update task |
| 7 | Slack `files.uploadV2` other capability errors (`not_in_channel`, `channel_not_found`, `is_archived`) trip the breaker | Med | Low | SlackCapabilityError path added to `_call_slack_api`; classified separately from transport failures; no `record_failure` |
| 8 | `OutgoingMessage.images` field breaks existing tests (constructor signature drift) | Med | Low | Field is defaulted; no construction-site code change required; existing tests continue to construct without the field |
| 9 | base64 roundtrip overhead for image bytes | Low | High | 50-300KB ≈ 67-400KB encoded; one-shot decode per adapter call; negligible |
| 10 | HTTP-API source ("api" no-colon) accidentally strips marker | High | Low | No-colon skip at `dispatcher.py:132-134` runs BEFORE extraction injection point (after `:158` adapter lookup); TASK #28 regression test + e2e twin #42 |
| 11 | Internal-report / `internal_agent:*` colon-sources accidentally extracted | Med | Low | Adapter lookup at `:158-165` / `:234-240` returns None for these sources BEFORE extraction; TASK #29 regression test |
| 12 | `source_hint` system-context injection deferred; agent channel-awareness gap remains | Low | Med | Phase C addresses wording; Phase B does NOT regress (extraction is dispatch-side, not agent-side) |
| 13 | Discord per-channel lock blocks concurrent multi-chunk + file upload | Low | Low | Single attachment on chunk 1 + already-serialized chunks through the same lock; no contention change |
| 14 | Discord empty-content-with-images early-return would have blocked legitimate sends | Med | Low (mitigated) | `:1575-1577` guard extended with `and not message.images`; TASK #9 |
| 15 | Telegram 4096-char text chunking still missing (pre-existing defect) | Low | Med | **DEFERRED** to separate ticket (§phase-b-telegram-4096-deferred); not blocking Phase B; Phase B's text-fallback handles oversized mermaid blocks via strip-and-deliver (Telegram will drop the text, but the image was delivered — primary content survives) |
| 16 | Charter emits marker with invented `image_id` (LLM off-script) | Med | Low | Phase B LOCKED regex doesn't match → marker ignored (left in content text-only, or stripped by near-miss sweeper); text delivered unchanged |
| 17 | `manager.tmp_image_store` returns None in test contexts | Low | High | Dispatcher code: `if store is not None` guard; tests pass explicit mock; production wiring verified at lifespan boot |
| 18 | SourceRegistry private `_manager` reach via public `manager` property is a layering violation | Low | Low | 3-line `@property` is a documented seam; alternative (private attr access) was considered and rejected |
| 19 | Bytes leak into logs (security regression) | Med | Low | `ImageAttachment.bytes_b64 = field(repr=False)` + redacting `__repr__`; `_log_image_metadata()` helper at dispatcher + all 3 adapters; logging contract test greps for absence of base64 |
| 20 | Cross-namespace id confusion (clipboard/designer id forged as chart-render) | Med | Low | Provenance gate (amendment #13) — `feature == "chart-render"` required; TASK #30 test |
| 22 | Multi-image silent drop on partial failure (amendment #7) | Med | Low | Per-image WARN-on-drop; never silent; tests assert exact `files=[…]` composition; remaining images + text still delivered |
| 23 | Slack capability flag NOT reset on token rotation/refresh | Med | Low | Flag is per-adapter-instance lifetime (in-memory); token rotation typically requires daemon restart, which resets the flag; document in commit |
| 24 | store.delete after chat delivery races with API caller's GET | Low | Low | API-origin (no `:`) never reaches extraction → no delete fired; chat-delivery races with API GET are mutually exclusive on the same image_id; ~millisecond window where API caller can fetch before delete; acceptable |

---

## Open Questions

1. **Slack files.uploadV2 wiring**: extend `_safe_api_call` or add a sibling `_safe_files_upload`? — **DECIDE at impl time** based on TASK #19 SDK signature verification. Default plan: extend `_safe_api_call` if the call shape is identical; add `_safe_files_upload` if it diverges.
2. **`MessageProcessingPipeline._dispatch_completed` interaction with `InstanceMessagingService._process_message_with_tracking:4824` `dispatch_message`** — **RESOLVED** by arch-rec §1: both fire, once-only is structural. Progressive delivered → completed discards at `:124-128`. No further investigation needed; the test matrix (TASK #26-28) pins the behavior.
3. **Should the dispatcher log marker-extraction events at INFO or DEBUG?** — **DEBUG** (matches existing per-source debug logging at `dispatcher.py:108, 124, 148`). INFO only for hard anomalies (e.g., image_id regex mismatch, provenance gate reject).
4. **Should `ImageAttachment` carry `width`/`height` for embed enrichment?** — **DEFER**. Discord embed with image dimensions is over-engineering for v1; PNG header parse adds code path; the user's chat client auto-sizes the attachment.
5. **One image per message vs many?** — **RESOLVED**: many supported, marker order preserved, no silent drops (arch-rec §3 amendment #7 + §8 dissent #4). N-image Discord uses `files=[…]`; Telegram sequential sendPhoto/sendDocument; Slack single `files_upload_v2` with N files (verify at impl).
6. **End-to-end test against real Discord/Telegram/Slack?** — **DEFERRED** to Phase D (cross-cutting e2e). Phase B covers unit + mock-source e2e; real-platform e2e requires credentials the worker doesn't have.
7. **Telegram `parse_mode="HTML"` safety** — **FIRM DECISION** (closes OQ): `parse_mode=None` for image captions. Mermaid `<` characters break HTML mode; default plain-text caption is safe. Logged in decisions.md §phase-b addendum.
8. **Should Phase B also emit `image_delete` calls after successful upload (per decisions.md §open-questions #5)?** — **ADOPTED** (arch-rec §3 amendment #22). `store.delete(image_id)` after successful `adapter.send()` in the delivering lane; API-path keeps the 30-day GET per §http-api; mutually-exclusive lanes prevent double-delete. Leader-adjudicated; Phase D reviser moves the deferred-ledger row.

---

## Restart / Promote Matrix (Phase B component-level)

| File | Daemon restart? | Promote? |
|------|-----------------|----------|
| `daemon/sources/base.py` | **YES** | **YES** |
| `daemon/sources/registry.py` | **YES** | **YES** |
| `daemon/sources/dispatcher.py` | **YES** | **YES** |
| `daemon/sources/adapters/discord/adapter.py` | **YES** | **YES** |
| `daemon/sources/adapters/slack/adapter.py` | **YES** | **YES** |
| `daemon/sources/adapters/telegram.py` | **YES** | **YES** |
| `daemon/constants.py` | **YES** | **YES** |
| `docs/sources/slack-setup.md` | no (doc only) | no |
| `tests/test_sources_dispatcher.py` | n/a (test reload) | n/a |
| `tests/test_discord_adapter.py` | n/a | n/a |
| `tests/test_telegram_adapter.py` | n/a | n/a |
| `tests/test_slack_adapter.py` | n/a | n/a |
| `tests/test_outbound_image_delivery.py` (NEW) | n/a | n/a |
| **Phase B total** | **YES** (daemon-side) | **YES** |

Implementation dispatch: **one developer/reviewer/tester chain for the whole phase** — components share the delivery-chain context (dispatcher init → adapter.send contract → test fixtures). Independent of Phase A (no overlapping files; Phase A = agent-prompt-only). Independent of Phase C (different files). Phase D consolidates the version + release notes.

---

**End Phase B Plan.**