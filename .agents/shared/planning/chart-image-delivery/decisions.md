# Decisions — chart-image-delivery

**Status:** R5 state — Phase A locked sections (with reviewer/approver-ordered in-place corrections) + Phase B/D append-only addenda through approver iteration 003. Authored at Phase A planning. Phases B and D consume this. Do not amend light-heartedly; marker regex and capture contract are LOCKING contracts for downstream phases.
**Author:** planner[v2] via plan-creation worker
**Date:** 2026-10-04
**Companion:** `phaseA-plan.md` (same directory)

---

## D1 — self-install (verbatim from user directive)

Charter MAY self-install its render toolchain following the `designer/install-opendesign` skill pattern (`agents/worker/skills-template/install-opendesign.md` is the template; job f9946b1d lineage): idempotent, fenced — **NO Docker, NO system packages**, Node/nvm user-space detection+bootstrap. Craft an equivalent install procedure/skill for mermaid-cli/puppeteer.

**Operationalization:**

- New file: `agents/charter/skills-template/install-mermaid-cli.md`.
- Fence set (verbatim-equivalent to `install-opendesign.md:796`): no Docker; no `apt`; no system Node touch; nvm user-space bootstrap only.
- nvm install Node 24 via `nvm install 24`; capture absolute path via `NVM_NODE="$(nvm which 24)"` (same idiom as `install-opendesign.md:859`).
- Install `mmdc` globally under the nvm-managed Node (`npm i -g @mermaid-js/mermaid-cli@12`).
- Discover puppeteer's chromium at `~/.cache/puppeteer/chrome/`; record the absolute path in `~/.config/charter-mermaid-puppeteer.json` so the render script can read it without re-discovering.
- **Non-interactive safety:** the install skill ships a `nvm source + resolve` helper because `~/.bashrc:121-123` is interactive-only (verified spot-check) and daemon-spawned bash would otherwise see system Node v22 (not v24).
- Idempotent — re-running on a fully-working host is verify-only.
- Verify section — `mmdc --version` returns `12.0.0`; a minimal `flowchart TD\n  A-->B` renders to BOTH `out.svg` and `out.png`; `file out.png` reports `PNG image data`.
- Reversal section — `npm rm -g @mermaid-js/mermaid-cli`, `nvm uninstall 24`, `rm -rf ~/.cache/puppeteer` (charter-owned).
- HONEST-STOP on hard blockers (no curl, no git) — report and stop; do NOT apt-install.

**Trigger in workflow.md Step 5:** pre-flight check `command -v mmdc || [ -f ~/.config/charter-mermaid-puppeteer.json ]`; on miss, invoke `install-mermaid-cli` skill via standard skill-execution path; on success, re-check and proceed.

> **[SUPERSEDED R3]** — the `command -v mmdc` pre-flight above is superseded by arch-rec §3 amendment #17: non-interactive bash never sources `~/.bashrc`, so `command -v` gives a permanent false-cold even when the install exists. The 4-signal READINESS_PROBE (`install-mermaid-cli.lib.sh`, sourced by `workflow.md` Step 5) replaces it — see `phaseA-plan.md` Components §4.

---

## D2 — render FINAL (verbatim from user directive)

LOCAL headless render. External render services (kroki.io etc.) OFF the table — no data-egress concern to raise.

**Operationalization:**

- mmdc + puppeteer + chromium all run local (user-space after D1 install).
- No outbound HTTP to any third-party render service. No `https://mermaid.ink/...`, no kroki.io, no `https://quickchart.io/...`.
- The install skill (D1) sets up the render stack; no other network egress is used.
- Charter's `bash` process is what executes mmdc; no separate service.

---

## D3 — storage (verbatim from user directive + verified semantics)

Rendered images go to ensemble `tmp_images` substrate (image module) — TTL cache + auto-clean via periodic sweep. **Provenance `feature="chart-render"`, `retention_class="normal"`** unless real justification. Verify actual sweep semantics in code and cite.

**Verified semantics (spot-checks cited):**

| Knob | Value | Source |
|------|-------|--------|
| Sweep cadence | Hourly (`3600 s`) | `daemon/services/tmp_image_cleanup_service.py:74` `DEFAULT_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS = 3600` |
| Retention window | 30 days | `:80` `DEFAULT_TMP_IMAGE_CLEANUP_RETENTION_DAYS = 30` |
| Kill-switch | NONE (always-on) | `:13-25` "ALWAYS ON (no kill-switch)" |
| Pair semantics | Blob+sidecar deleted together; `FileNotFoundError` swallowed; orphans reaped by their own mtime | `:37-42` |
| Protected exempt | YES — only literal `"protected"` exempts | `:470-498` `_is_protected` |
| Cap on total bytes | 1 GiB default; per-save walk-and-sum | `daemon/services/tmp_image_store.py:140-148` + `:179-198` walk |
| `protected` cap behavior | Counts toward cap; exhaustion raises `TmpImageStoreFull`, no silent protected-eviction | `daemon/services/tmp_image_store.py:107` (architect amendment #3) |
| Valid retention classes | `frozenset({"normal", "protected"})` | `daemon/services/tmp_image_store.py:89-91` |

**Operationalization:**

- `image_save` parameters from charter: `content_b64=<bytes>`, `content_type="image/png"`, `feature="chart-render"`, `retention_class="normal"`, `source_agent=None` (auto-stamped by tool — `image_tools.py:712-715`).
- 30-day retention is right for chat attachments; no justification for `protected`.
- No sweep-config change. No service change. No new HTTP route.
- Failure modes: `TmpImageStoreFull` (cap exhausted), store-not-initialized, O_EXCL collision. All surface as `"Error: ..."` strings from `image_save`; charter falls through to the degradation branch (text-only Mermaid delivery, no marker).

---

## D4 — per-platform native delivery (verbatim from user directive)

Per-platform NATIVE image delivery — no generic hack. Internal image-reference transport stays generic/source-agnostic (marker/attachment ref through response path); final delivery is per-adapter native: Discord attachment/embed (bot API/webhook file upload), Telegram sendPhoto (+size/type constraints), Slack file upload API (files.uploadV2 or current equivalent). Handle per-platform limits (file size, MIME, rate limits) + graceful text fallback where a source can't deliver. Verify against CURRENT platform API docs during implementation — don't guess endpoints.

**Operationalization (Phase A's slice — marker contract; Phase B owns the rest):**

- Phase A defines ONLY the **internal transport** (the marker). The per-adapter native delivery is Phase B's scope.
- The marker is source-agnostic — it does NOT encode Discord / Slack / Telegram specifics (no attachment id, no channel id, no file size hint). The image_id is the only payload.
- Phase B adapters MUST consult CURRENT platform API docs at implementation time per the directive. Phase A's plan does not pre-specify which API surface each adapter uses — that's Phase B's decision per the user directive.
- Per-platform size headroom: with `mmdc -w 1200 -s 2` (see §capture), expected PNG is 50–300 KB for typical Mermaid output, well below Discord 8 MB / Telegram 10 MB / Slack 5 MB initial-upload (current equivalents may differ — Phase B verifies at implementation). If a chart exceeds per-platform limits, the adapter degrades to a text-only delivery (or a downscaled variant — Phase B decision).

---

## §marker — canonical image-reference marker

**This is the LOCKING contract Phase B consumes. Do not amend light-heartedly.**

### Syntax

```text
<!-- ens-img:chart-render:<image_id> -->
```

| Field | Value | Rationale |
|-------|-------|-----------|
| Prefix | `<!-- ens-img:` | HTML comment. **Marker is NOT invisible on chat surfaces** — HTML comments render LITERALLY on Discord, Slack, and Telegram (verified by architect analysis; `architecture-recommendation.md §0` + §5-F7). The near-miss strip-only sweeper (`architecture-recommendation.md §3 amendment #14`, secondary pattern) handles cosmetic junk-line cleanup if an LLM emits a slightly-mutated marker. LLM prompts tell the agent to pass it through unmodified so it survives byte-for-byte until dispatch-side strip. |
| Feature tag | `chart-render` | Distinguishes chart PNGs from other tmp_image sources (clipboard, screenshot, designer output). Phase B dispatcher keys off this; provenance gate (`architecture-recommendation.md §3 amendment #13`) requires `feature == "chart-render"` before upload — kills cross-namespace id confusion. |
| Image id | `<image_id>` | 32-hex (`[a-f0-9]{32}`), uuid4. Enforced at the router layer (`daemon/routers/tmp_images.py` regex — confirmed) + `image_save` returns the same shape. |
| Suffix | ` -->` | Close the comment. |

### Regex (byte-stable — Phase B uses this exact pattern)

```regex
^<!-- ens-img:chart-render:[a-f0-9]{32} -->$
```

### Lifecycle

1. Charter validates Mermaid + renders PNG + calls `image_save` → gets `{"image_id": "abc..."}`.
2. Charter appends `<!-- ens-img:chart-render:abc... -->` on its own line, AFTER the explanation prose, in its assistant turn.
3. `generate_chart` returns the result verbatim (`daemon/tools/chart_tools.py:525` — load-bearing property; do NOT change).
4. Parent agent receives the result; the chart innate skill (`agents/_prompt_system/innate-skills/chart/skill.md`) is updated to instruct: "do not strip the trailing `<!-- ens-img:... -->` marker; paste the result directly."
5. Parent agent's final response includes the marker.
6. Chat-source dispatcher (Phase B) extracts the marker via the regex above, strips it from the visible message text, and uploads the PNG via the per-adapter native API.
7. Markers that do not match the regex (typo, missing tag, extra whitespace) are ignored — text delivery is unaffected.

### HTTP-API caller behavior (non-chat)

The marker is preserved in the assistant text content for all callers. Clients that want the PNG parse the marker and call `GET /api/tmp_images/<id>` directly. Clients that don't know the marker see the validated Mermaid text plus one line of HTML comment at the bottom — the comment renders as nothing in Markdown previews, and the Mermaid block is the substantive content. **No separate content channel is invented** — this preserves the verbatim-passthrough property of `generate_chart` (`chart_tools.py:525`).

### What charter does NOT do (in Phase A)

- Does NOT upload the PNG — Phase B's job, per platform.
- Does NOT strip the marker from text — Phase B strips.
- Does NOT emit a URL — marker is a reference, not a link.
- Does NOT retry on render/persist failure — degrades to text-only.

---

## §capture — mmdc render contract

**Charter's render step uses exactly these flags. The flag set is the input to Phase B's degradation ladder.**

### Required mmdc invocation

```text
mmdc \
  -i <input.mmd> \
  -o <output.png> \
  -t default \
  -b white \
  -w 1200 \
  -s 2 \
  --puppeteerConfigFile <cfg.json> \
  --quiet
```

| Flag | Value | Why |
|------|-------|-----|
| `-t` | `default` | Mermaid default theme; matches what most chat previews render. |
| `-b` | `white` | Opaque background — transparent PNGs look bad in dark-mode chat UIs. White is the safe default; per-platform theming is Phase B's job. |
| `-w` | `1200` | Width in px. Fits Discord embed preview (≤ 800, scales) and Slack unfurl width; scales for Telegram. |
| `-s` | `2` | Scale factor (2x retina) — keeps text crisp on high-DPI screens. |
| `--puppeteerConfigFile` | `<cfg.json>` | JSON `{"executablePath": "<absolute-chromium-path>"}` written inline before the render. Path resolved from `~/.config/charter-mermaid-puppeteer.json` (created by D1 install skill). |
| `--quiet` | (flag) | Suppress mmdc's progress chatter; the validation log line is the source of truth. |

### Timeout

The render step is wrapped in `timeout 60` (60-second budget). Puppeteer cold start is ~5–10s on warm cache; 60s leaves headroom without exploding the validation window.

### Expected size

Typical Mermaid output at these flags: **50–300 KB PNG**. This is well under Discord 8 MB / Telegram 10 MB / Slack 5 MB initial-upload limits. **Phase B verifies current limits at implementation time** per the user directive.

### Retry discipline

The existing **3-attempt syntax-retry budget** (charter `rule.md:14`) is preserved for **syntax errors only**. Render-side failures (puppeteer timeout, mmdc non-zero exit, file-system errors, image_save failure) do NOT retry — they fall through to the degradation branch (text-only Mermaid delivery, no marker).

### mktemp hygiene

Every temp file uses `mktemp /tmp/charter_XXXXXX.{mmd,png,cfg}`; defensive `trap 'rm -f "$TMPFILE" "$TMPPNG" "$TMPPNG.cfg"' EXIT` to avoid leaks on early-exit. The render step's output PNG is consumed by the subsequent `image_save` call; on success the PNG is removed AFTER `image_save` returns.

### mktemp / cleanup on failure

If mmdc fails or `image_save` fails, the temp PNG is removed before returning. The Mermaid text in the assistant turn is unaffected.

---

## §degradation — failure modes

Chart delivery NEVER breaks because of image-render problems. Every failure mode below degrades to text-only Mermaid delivery, with no marker emitted.

| Failure | Detection | Degradation |
|---------|-----------|-------------|
| mmdc not installed | `command -v mmdc` empty, no `~/.config/charter-mermaid-puppeteer.json` | Trigger D1 install skill; if install fails, return Mermaid text only with `⚠️ Validation skipped` warning (existing rule.md:19). |
| mmdc non-zero exit (syntax) | `RENDER_EXIT != 0` AND output empty | Existing 3-attempt syntax-retry budget kicks in. On exhaustion, return Mermaid text with `⚠️ Validation skipped` (rule.md:19). No marker. |
| mmdc non-zero exit (puppeteer / chromium) | `RENDER_EXIT != 0` AND stdout contains puppeteer/chromium error | NO retry — degrade immediately. Return Mermaid text only (validation passed; render failed). No marker. |
| mmdc timeout (60s) | `timeout` returns 124 | NO retry — degrade. Return Mermaid text only. No marker. |
| `image_save` returns "Error: ..." | String starts with `"Error:"` | NO retry — degrade. Return Mermaid text only. No marker. |
| `image_save` returns valid JSON but no `image_id` | JSON missing the key | Log the anomaly, degrade. Return Mermaid text only. No marker. |
| Charter emits marker with invented `image_id` (LLM error) | LLM went off-script | Not detected at emit time, but Phase B extraction fails the regex match and ignores the marker. Text delivery unaffected. Test #5 covers byte-stability. |

---

## §http-api — non-chat caller behavior

For HTTP-API calls (e.g. `POST /api/messages` from tester, web UI direct, programmatic clients):

- The marker is in the assistant text content.
- The PNG is at `GET /api/tmp_images/<image_id>` (the same route the chat source dispatcher uses).
- The response is the agent's `result.content` — verbatim passthrough. No additional structure is added in Phase A.
- The client may parse the marker (regex in §marker) and fetch the PNG, or ignore it.
- The marker never reaches chat END-USERS as visible text — the dispatcher strips it before adapter delivery (HTML comments render LITERALLY on Discord/Telegram/Slack; see §phase-b-r2-addendum-2). HTTP-API callers DO receive the marker line in the response body, where it is parseable and inert in Markdown renderers that hide HTML comments. [wording sharpened R3, approver iter-002]

---

## §restart-promote — Phase A component matrix

| File | Daemon restart? | Promote? |
|------|-----------------|----------|
| `agents/charter/workflow.md` | NO (picked up at next charter spawn) | NO |
| `agents/charter/rule.md` | NO | NO |
| `agents/charter/soul.md` | NO | NO |
| `agents/charter/meta.json` | NO (picked up at next spawn) | NO |
| `agents/charter/skills-template/install-mermaid-cli.md` | NO (skill_seed_service consumes on next spawn) | NO |
| `agents/_prompt_system/innate-skills/chart/skill.md` | NO (innate skill loaded on next instance spawn) | NO |
| **Phase A total** | **NO** | **NO** |

Phase A is a planning artifact + agent-prompt change only. **No `daemon/` code, no migration, no restart, no promote.** Phase B (dispatcher + adapter changes) is the one that requires a promote — that matrix is Phase B's plan, not this one.

---

## §phase-b-handoff — what Phase B consumes from this record

1. The marker regex (§marker) — Phase B's dispatcher extracts via this exact pattern.
2. The capture flags (§capture) — Phase B's degradation ladder can assume `mmdc -t default -b white -w 1200 -s 2` produces 50–300 KB PNG.
3. The `feature="chart-render"` provenance tag — Phase B may use it to filter `image_list` calls.
4. The `retention_class="normal"` semantics — Phase B does not need to override; sweep handles it.
5. The HTTP route `GET /api/tmp_images/<image_id>` (already implemented; Phase A does not change it).
6. The `content_type="image/png"` shape — Phase B's adapter native upload APIs accept this.
7. The degradation contract (§degradation) — Phase B's failure modes are separate (platform upload failure); the marker-still-there-but-image-gone case is Phase B's text fallback.

---

## §open-questions — items deferred to later phases

1. Should charter emit the marker also when the chart is a `NEEDS MORE INFO` result? **No** — no diagram to render; workflow.md Step 2 short-circuits. (Implicit; restated here for Phase B.)
2. Should charter add a `page` provenance value (e.g. `page="mermaid-render"`)? **No** — `feature="chart-render"` is enough.
3. PNG vs SVG for delivery? **PNG** — most chat UIs don't render SVG as an inline image, and Discord/Slack/Telegram attachment APIs prefer PNG/JPG. SVG stays internal as a validation artifact.
4. Should the install skill also be added to the `worker` agent's `skills-template/`? **Out of scope for Phase A.** Phase C/D decision.
5. Does the marker need a TTL field (e.g. `<!-- ens-img:chart-render:<id>;ttl=...;-->`) for chat sources with strict lifetime guarantees? **No for Phase A** — the 30-day sweep is the contract; the HTTP route is the access path. If a chat source has stricter lifetime, Phase B can add an early `image_delete` call after successful upload.

---

## §phase-b-image-access — in-process TmpImageStore access path

**Decision:** Phase B's dispatcher resolves image bytes via direct in-process access (`manager.tmp_image_store.open_with_meta(image_id)`), **NOT** an HTTP self-call to `GET /api/tmp_images/<image_id>`. **[API-name superseded R3]** — resolution now uses `store.open_full(image_id)` (returns the full `TmpImageRecord` so the provenance gate can read `record.provenance.feature`) per `§phase-b-r2-addendum-4`; the `open_with_meta` references below predate that amendment.

**Rationale:**

- `TmpImageStore.open_with_meta` is the documented sync read API at `daemon/services/tmp_image_store.py:436-455` — returns `(bytes, content_type, sha256_hex)`. No need to round-trip through HTTP.
- `manager.tmp_image_store` is a public read-only property at `daemon/manager.py:2544-2546` — returns the shared store injected at lifespan boot.
- The store is also exposed on `app.state.tmp_image_store` (lifespan wiring) and reachable via `SourceRegistry._manager`. To avoid private-attr reach across modules, Phase B adds a 3-line `@property manager` on `SourceRegistry` (~line 236) as a clean public seam.
- HTTP self-call would add latency + risk the request reaching a separate worker process; in-process is strictly better.
- Wrapped in `asyncio.to_thread` (sync → async) — sync open is ~1ms for 50–300 KB; no thread-pool exhaustion under per-user send-lock LRU bound (`MAX_SEND_LOCKS=10000`).

**Failure mode:** `store is None` (test contexts where InstanceManager is constructed without the store) → text fallback; marker stripped; WARN log. Production wiring verified at lifespan boot — the store is always present.

**Caller code path:**

```
ResponseDispatcher._registry (SourceRegistry)
  → .manager (new @property)
  → manager.tmp_image_store (public property)
  → store.open_with_meta(image_id)  # sync, wrapped in to_thread
```

---

## §phase-b-outgoing-extension — OutgoingMessage.images field

**Decision:** Extend `OutgoingMessage` (daemon/sources/base.py:52-60) with a single defaulted `images: list[ImageAttachment] | None = None` field. Backward-compatible.

**Shape:**

```python
@dataclass(frozen=True)
class ImageAttachment:
    image_id: str            # 32-hex (regex-validated at extraction)
    content_type: str        # e.g. "image/png"
    filename: str            # e.g. "chart-abc12345.png"
    bytes_b64: str           # base64 of the PNG bytes
```

Base64 (vs `bytes`) keeps the field JSON-serializable for logging + avoids BytesIO pickling across the adapter boundary. Adapters decode per their platform's upload API. ~67–400 KB encoded for 50–300 KB source — negligible.

**Construction-site impact:** zero for existing callers — all 3 sites (`dispatcher.py:170`, `:243`, `registry.py:980`) pass kwargs by name; the field auto-defaults to `None`. **[CORRECTED R3, approver iteration-001 blocking #1 — both-seam]** The dispatcher populates `images` at BOTH construction sites (`dispatcher.py:170` AND `:243`) per `§phase-b-r2-addendum-1`; `registry.py:980` (/new confirmation) keeps `images=None` (that path never carries a marker).

---

## §phase-b-marker-extraction — extraction in BOTH dispatch seams (arch-rec §1)

**Decision:** [CORRECTED R3, approver iteration-001 — see the BOTH SEAMS paragraph at :323] Marker extraction runs in **BOTH** `dispatch_message` AND `dispatch_completed` as the last content transformation before `OutgoingMessage` construction (`§phase-b-r2-addendum-1`, NON-NEGOTIABLE).

**Why:**

1. Phase A's contract: charter emits the marker in its **own assistant turn** → parent receives the result via `generate_chart()` → parent's final AIMessage contains the marker. and for external chat sources that final message is delivered by the PROGRESSIVE lane (instance_messaging.py:4506-4566, :4815-4834), hence extraction at both seams. [CORRECTED R3, approver iteration-001]
2. `MessageProcessingPipeline._dispatch_completed` (`daemon/services/message_processing_pipeline.py:720-800`) is the completed-lane call site — extraction runs there AND at the `dispatch_message` construction site (`dispatcher.py:243`). [CORRECTED R3]
3. `InstanceMessagingService._process_message_with_tracking:4824` calls `dispatch_message` for the "deferred final" — but the dispatcher's progressive_sent_sources guard (`daemon/sources/dispatcher.py:124-128`) ensures `dispatch_completed` is skipped if progressive already sent for the same source. So the marker-stripping + image-upload happens exactly once.

**Regex (byte-stable, from §marker):**

```python
_MARKER_RE = re.compile(
    r"^<!-- ens-img:chart-render:([a-f0-9]{32}) -->$",
    re.MULTILINE,
)
```

**Helper:**

```python
def extract_chart_images(content: str) -> tuple[str, list[str]]:
    """Strip marker lines; return (stripped_content, [image_id, ...]).
    Malformed markers (typo, extra whitespace, invented id) are left
    in content untouched — text delivery unaffected.
    """
```

**Malformed markers are LEFT IN content** as defense against LLM-emitted garbage — but the **near-miss strip-only sweeper** (`architecture-recommendation.md §3 amendment #14`, secondary pattern `^\s*<!--\s*ens-img:chart-render:[^>\n]{0,64}-->\s*$`) catches the visible-mutated cases (indented, extra whitespace, mutated id) for cosmetic strip-only. Phase A §degradation #5 covers the well-formed-but-hallucinated-id case contractually: "Charter emits marker with invented image_id (LLM error) → LLM went off-script → Not detected at emit time, but Phase B LOCKED-regex extraction fails the match and ignores the marker. Text delivery unaffected."

**HTTP-API safety — CORRECTED (arch-rec §1, R2 in-place fix per reviewer order):** the no-colon skip at `dispatcher.py:132-134` (in `dispatch_completed`) / `:209-211` (in `dispatch_message`) runs BEFORE the extraction injection point. The extraction+resolution lives AFTER the adapter lookup (`dispatcher.py:158-165` / `:234-240`), NOT between line 119 and line 132 (the prior plan's contradictory placement — the prose in this section was incorrect and conflicted with itself; the reviewer-ordered in-place fix resolves it to the both-seam/after-skip rule). HTTP-API sources (`"api"` and similar internal no-colon identifiers) short-circuit at the no-colon skip and never reach extraction. Internal colon-sources (`internal_agent:*`) return at the adapter lookup before extraction. Phase A's §http-api contract (marker stays in API responses) is preserved by construction.

**BOTH SEAMS (arch-rec §1 pin, verbatim, non-negotiable):** `extract_chart_images` runs in `dispatch_message` AND `dispatch_completed` as the LAST content transformation before `OutgoingMessage` construction. Once-only is preserved structurally: one `OutgoingMessage` per lane per message; the `_progressive_sent_sources` guard (`dispatcher.py:124-128`) keeps exact semantics (progressive delivered → completed discards; progressive adapter-False → completed delivers with extraction). For external chat sources, the final AI message is delivered via the progressive lane (`instance_messaging.py:4506-4566`, `:4815-4834`); without both-seam extraction, the marker leaks as visible literal text and the image never delivers.

---

## §phase-b-degradation — uniform text-fallback ladder

**Decision:** Every Phase B chart-image path degrades to text-only delivery on any failure, with the marker stripped from the visible text and the Mermaid block intact. NO downscale-on-oversize in Phase B (no PIL dependency; 50–300 KB typical per §capture; out-of-band edge cases go to text fallback).

**Per-adapter ladder:**

| Platform | Step 1 (upload) | Step 2 (fallback) | Step 3 (final) | Always |
|----------|----------------|--------------------|----------------|--------|
| Discord | `target.send(content, file=discord.File)` chunk 0 | retry text-only (chunk 0, no file) | return False (existing failure path) | text content (markers stripped by dispatcher) carries on; warn-log at each retry |
| Telegram | `sendPhoto` if ≤10 MB, else `sendDocument` if ≤50 MB | (none — single multipart call) | fall through to text-send code (markers stripped) | token bucket + per-chat lock preserved |
| Slack | `files.upload_v2` (verify method at impl) | if `missing_scope`/`files:write`/channel-error → warn-once per channel | fall through to text-send code | per-channel lock + circuit-breaker preserved |

**Logging:** every rung logs at WARN (single line, includes image_id[:8] for traceability). Slack `missing_scope` is logged **once per channel** (not per send) to avoid log floods — the dispatcher's per-user LRU lock + WARN-once-per-key pattern.

**No degradation swallows text delivery.** Text delivery is the universal floor; image upload is best-effort enhancement.

---

## §phase-b-slack-scope — files:write scope + USER ACTION ITEM

**Decision:** Ship Slack image upload behind capability detection; ship `docs/sources/slack-setup.md` scope table update with one new row (`files:write`); flag USER ACTION ITEM: operator must grant the scope in their Slack app config + reinstall.

**Why:**

- `docs/sources/slack-setup.md:35-48` scope manifest lists `files:read` but NOT `files:write` (verified spot-check). Upload needs `files:write` per Slack docs.
- Without the scope, `files.uploadV2` returns `missing_scope` — Phase B detects this string (case-insensitive) and degrades to text-only delivery with a WARN-once log.
- Phase B does NOT add a runtime OAuth re-scope flow; that's a separate ticket (operator-side work).
- Operators running without the scope see zero Slack image delivery but text delivery is unaffected.

**USER ACTION ITEM (commit body + decisions.md):**

> Slack chart-image delivery requires the `files:write` OAuth scope. Until granted in your Slack app's Bot Token Scopes + app reinstalled, Slack text-only delivery with WARN log on each chart render. Grant + reinstall to enable.

---

## §phase-b-platform-limits — daemon/constants.py additions

**Decision:** Add 4 size constants + 1 MIME whitelist to `daemon/constants.py`. Values verified against Phase A §capture (50–300 KB typical) — all well within limits.

```python
DISCORD_FILE_MAX_BYTES: int = 8 * 1024 * 1024      # 8 MB  (Discord bot upload limit)
TELEGRAM_PHOTO_MAX_BYTES: int = 10 * 1024 * 1024   # 10 MB (Telegram sendPhoto)
TELEGRAM_DOCUMENT_MAX_BYTES: int = 50 * 1024 * 1024 # 50 MB (Telegram sendDocument)
SLACK_FILE_MAX_BYTES: int = 1024 * 1024 * 1024     # 1 GB  (Slack upload)
# R4 verify-at-impl (per D4): Discord non-boosted default has moved 8MB->10MB across API
# generations; Slack limits are workspace-dependent (1GB = ceiling, not a doc claim);
# INITIAL_COMMENT_MAX=4000 (Slack initial_comment soft limit) also lands here per phaseB Task #6.
CHART_IMAGE_MIME_WHITELIST: frozenset[str] = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
})
```

**Re-verify at impl time against CURRENT platform docs.** Verified values + doc URLs recorded in commit body. (Phase A §capture notes the typical 50–300 KB fits everywhere — limits are headroom for edge cases.)

---

## §phase-b-source-hint-deferred — out of scope, Phase C's call

**Decision:** **DEFER.** Phase B does not implement a `source_hint` system-context injection (e.g., `[SYSTEM CONTEXT: source=discord]` visible to the agent).

**Rationale:**

1. Phase C plans the agent-prompt wording ("when in doubt about the source, prefer `generate_chart()`"). If Phase C's wording is robust enough (over-deliver-when-uncertain default), `source_hint` is unnecessary.
2. Phase B is the DISPATCH/delivery layer. It does not need to know whether the agent knew it was on a chat source. It extracts the marker and uploads regardless.
3. If a future ticket finds `source_hint` valuable, it's a context-injection concern (prompt-side), not an adapter concern. Phase C is the right home.
4. Scope discipline: Phase B already touches dispatcher + 3 adapters + constants + tests; adding prompt-side injection is scope creep.

**Recorded for Phase C's reference.** Phase C may decide to add it; if so, that's Phase C's implementation. Phase B does not block Phase C and does not regress either way.

---

## §phase-b-telegram-4096-deferred — pre-existing defect, not Phase B's job

**Decision:** **DEFER.** Telegram text chunking for >4096-char messages (pre-existing defect — not in scope of chart-image-delivery) is deferred to a separate ticket.

**Why:**

1. The defect is unrelated to chart-image delivery: any >4096-char Telegram text message drops server-side today.
2. Phase A's `<!-- ens-img:chart-render:<id> -->` marker is on its own line and short (~50 chars). The Mermaid code block CAN exceed 4096 chars for very complex diagrams — but this is an existing limitation, not a new one introduced by Phase B.
3. Phase B's text-fallback path INHERITS this limitation: if `message.content` (with marker stripped) is >4096 chars and image upload succeeded, Telegram will silently drop the text. **However**, the image was delivered — the primary chart-render content survives. The text fallback (when image fails) suffers the same drop. This is acceptable for Phase B's scope.
4. Cleaning this up is a small adjacent hardening (~30 lines in `daemon/sources/adapters/telegram.py:262` `send()` to add chunking) — separate ticket. **Recorded for the project backlog.**

---

## §phase-b-doc-verification — platform API docs to check at impl

**TASK list (recorded for the developer):**

| Platform | Doc URL | CHECK |
|----------|---------|-------|
| Discord | https://discordpy.readthedocs.io/en/stable/api.html#discord.File | `discord.File(fp, filename)` signature; `TextChannel.send(content=, file=, ...)` kwarg semantics |
| Telegram | https://core.telegram.org/bots/api#sendphoto | sendPhoto max photo size (10 MB), accepted MIME, caption limit (1024), parse_mode behavior |
| Telegram | https://core.telegram.org/bots/api#senddocument | sendDocument max size (50 MB), accepted MIME, caption semantics |
| Slack | https://api.slack.com/methods/files.uploadV2 | files.uploadV2 kwarg signature (filename/content/channel_id), return shape |
| Slack | https://api.slack.com/methods/chat.postMessage | chat.postMessage `file` kwarg semantics (file_share to a previously-uploaded file) |

**Rule:** do not hardcode unverified endpoint details in the plan. Verify at impl time; record findings in commit body. This avoids drift between plan and code.

---

## §phase-b-restart-promote — Phase B component matrix

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

**Phase B is daemon code.** Restart required; promote required. Phase D (cross-cutting) consolidates the version + release-notes matrix.

---

**End Phase B decisions.** Phase A's locked sections (§marker, §capture, §degradation, §http-api) are unchanged. Phase B's records (§phase-b-*) are append-only — they do not amend Phase A's contract.

---

# §phase-b addendum — R2 corrections per architecture-recommendation.md (2026-10-04)

**Status:** APPENDED at Phase B R2 revision loop. Companion to `phaseB-plan.md`. Records reviewer-ordered in-place corrections and the R2 adoption of architect recommendations. Phase A's locked sections (§marker, §capture, §degradation, §http-api) are NOT amended. Phase B's prior §phase-b-* sections are NOT deleted (prior records remain visible; this addendum records corrections and adoptions, and supersedes only the contradicted details explicitly identified).

**Author:** planner[v2] via plan-creation worker
**Date:** 2026-10-04 (R2)

**Precedence:** `architecture-recommendation.md` §3 governs over phase-plan prose on conflict.

---

## §phase-b-r2-addendum-1 — BOTH-SEAM extraction (arch-rec §1, NON-NEGOTIABLE)

**Supersedes:** §phase-b-marker-extraction's framing of "extraction in dispatch_completed ONLY" (R2 in-place correction at decisions.md:321) **and** §phase-b-outgoing-extension's "Construction-site impact" single-seam claim (R3 in-place corrections at decisions.md:286 and :292-297, per approver iteration-001 blocking #1).

**Decision:** `extract_chart_images` runs in `dispatch_message` AND `dispatch_completed` as the LAST content transformation before `OutgoingMessage` construction — after the no-colon skip (`dispatcher.py:132-134` / `:209-211`), after source validation, after the internal-report skip, and after the adapter lookup (`dispatcher.py:158-165` / `:234-240`).

**Pin (verbatim from arch-rec §1):**

> `extract_chart_images` runs in `dispatch_message` AND `dispatch_completed` as the LAST content transformation before `OutgoingMessage` construction — **after** the no-colon skip (`dispatcher.py:132-134` / `:209-211`), after source validation, after the internal-report skip, and **after** the adapter lookup (`:158-165` / `:234-240`). API-origin (no-colon) sources return at the skip and never reach extraction: marker verbatim in content, `open_with_meta` never called. Internal colon-sources (`internal_agent:*`) return at the adapter lookup before extraction.

**Implications:**
- `OutgoingMessage.images` populated at **BOTH** construction sites (`dispatcher.py:170` AND `:243`); NEVER at `registry.py:980`.
- Once-only is preserved structurally: one `OutgoingMessage` per lane per message; `_progressive_sent_sources` guard (`dispatcher.py:124-128`) keeps exact semantics (progressive delivered → completed discards; progressive adapter-False → completed delivers with extraction).
- The previously-pinned acceptance test "marker NOT extracted in dispatch_message" (Phase B plan §acceptance-criteria line 1073, "defense in depth") was REPLACED with `test_progressive_lane_extracts_and_strips` + `test_progressive_then_completed_no_double_send` (Phase B plan Tasks #26-27). The prior test, as written, would have pinned the marker-leak defect.

**Why:** Verified twice by the council, independently corroborated by `instance_messaging.py:4506-4566` + `:4815-4834`, and by the dissenting lane's own once-only test design. If uncorrected: extraction never runs in production for chat sources; the marker leaks as visible literal text (HTML comments render literally on Discord/Telegram/Slack — see §phase-b-r2-addendum-2); no image is ever uploaded; tests stay green.

---

## §phase-b-r2-addendum-2 — invisibility claim correction (decisions.md:95)

**Supersedes:** The "Renders as nothing in Markdown/Discord/Slack/Telegram" claim at decisions.md:95 (R2 in-place correction per reviewer order).

**Decision:** HTML comments render LITERALLY on Discord, Telegram, and Slack. The marker is NOT invisible. Architect-verified by `architecture-recommendation.md §0` ("decisions.md:95's 'renders as nothing' claim is **false for all three clients** — none hide HTML comments") and §5-F7.

**Mitigation:** The **near-miss strip-only sweeper** (`architecture-recommendation.md §3 amendment #14`, secondary pattern `^\s*<!--\s*ens-img:chart-render:[^>\n]{0,64}-->\s*$`) catches the visible-mutated cases for cosmetic junk-line cleanup. The LOCKED marker regex stays byte-stable — no relaxation in v1. Charter-emitted well-formed markers survive byte-for-byte until dispatch-side strip in the normal flow; near-miss catches LLM-emitted mutated markers.

---

## §phase-b-r2-addendum-3 — logging contract (arch-rec §3 amendment #12)

**Decision:** `ImageAttachment.bytes_b64 = field(repr=False)` + redacting `__repr__`. Declare `OutgoingMessage.images` transport-only — NEVER persisted, NEVER logged whole.

**Logging format:** `image_id[:8]` + `size_bytes` + `content_type` only, at dispatcher AND all three adapters. Bytes NEVER in any log line.

**Helper:** `_log_image_metadata(image_id, size_bytes, content_type)` defined in dispatcher.py + mirrored in `daemon/sources/adapters/{discord,telegram,slack}/*.py`. Used at every site that handles `ImageAttachment`.

---

## §phase-b-r2-addendum-4 — provenance gate (arch-rec §3 amendment #13)

**Decision:** Resolution via `store.open_full(image_id)` (`daemon/services/tmp_image_store.py:480-494`) — NOT `open_with_meta`. Before upload, dispatcher requires `record.provenance.feature == "chart-render"`. Mismatch → drop the image, WARN, text fallback.

**Rationale:** F2 cross-namespace id confusion — a forged/echoed marker referencing a clipboard or designer id would upload that image. The gate is ~5 lines and kills the confusion. Severity bounded (id = 128-bit capability; never-minted ids fail clean at `TmpImageNotFound`).

**Optional 24h freshness window:** Phase D may add (`uploaded_at < now - 24h` → drop); render→dispatch is seconds, so the window protects against replay without false-positives in the normal flow.

---

## §phase-b-r2-addendum-5 — Slack capability classification (arch-rec §3 amendment #4, MANDATORY guardrail)

**Supersedes:** Slack plan-as-written (text fallback only, no breaker guardrail).

**Verified defect:** `_call_slack_api:326-329` records `record_failure` on ANY `ok=false` (arch-rec cites `:340-343` — line drift, defect real). Five `missing_scope` sends would open the text breaker.

**Decision:**
1. Add `SlackCapabilityError` exception class (`daemon/sources/adapters/slack/adapter.py`).
2. In `_call_slack_api`, classify `missing_scope`/`not_in_channel`/`channel_not_found`/`is_archived` BEFORE `record_failure` → raise `SlackCapabilityError` (no breaker record).
3. Add `self._slack_capability_flags: set[str]` per-adapter-instance (in-memory; resets on daemon restart). Once `"files_upload_v2"` is added, `send()` short-circuits BEFORE the API call (zero API calls).
4. WARN-once-per-channel for `missing_scope` with "USER ACTION REQUIRED: grant files:write scope in Slack app config" text. Subsequent sends do NOT re-WARN.
5. Test: 5 `missing_scope` sends → only 1 actual API call; `consecutive_failures == 0` throughout.

---

## §phase-b-r2-addendum-6 — Telegram multipart 4xx non-transient (arch-rec §3 amendment #5, MANDATORY guardrail)

**Supersedes:** Telegram plan-as-written (no classification; transport + 4xx both `record_failure`).

**Decision:** In `_api_call_multipart`, classify `400 <= error_code < 500` as non-transient — raise `TelegramAPIError` WITHOUT calling `record_failure` (mirror Discord `:1537-1547`). Transport errors (`aiohttp.ClientError`, 5xx) keep 3× exponential backoff + `record_failure` (existing behavior preserved for outages).

**Rationale:** Systematically-rejected images (e.g., `Bad Request: photo_invalid_dimensions`) must NOT poison the text breaker. A real outage (5xx) DOES trip it — and text would fail too, which is correct.

---

## §phase-b-r2-addendum-7 — Slack single-call upload (arch-rec §3 amendment #9)

**Supersedes:** The plan-as-written upload-then-`postMessage(file=)` sketch (~line 805-810 of original phaseB-plan.md).

**Decision:** Upload is a SINGLE `files_upload_v2(channel_id, filename, content, initial_comment)` call (slack-sdk 3.42.0 AsyncWebClient; verify method signature at impl). DELETE the `chat.postMessage(file=)` follow-up for the file itself — `file=` is an undocumented kwarg with double-render risk.

**`initial_comment` length limit:** Slack `initial_comment` has a limit (~4000 chars; verify at impl). Truncated `initial_comment` on the file PLUS a follow-up `chat.postMessage(text=full_content[MAX:])` (text floor; arch-rec §3 amendment #8).

---

## §phase-b-r2-addendum-8 — uploads INSIDE per-chat/per-channel locks (arch-rec §3 amendment #10)

**Decision:** Image upload must be inside the existing per-chat/per-channel LRU lock. Telegram restructure: image upload + text-send BOTH happen inside `async with lock:`. Slack restructure: upload + postMessage (if any) BOTH inside `async with lock:`.

**Rationale:** Without the lock, chat-A image-A starts uploading, chat-A text-A interleaves, chat-A image-B (from another send) interleaves → ordering hazard.

---

## §phase-b-r2-addendum-9 — ADOPTED — store.delete after successful chat delivery (arch-rec §3 amendment #22)

**Supersedes:** Prior decisions.md §open-questions #5 ("No for Phase A"; leader-adjudicated this round as ADOPT).

**Decision:** After a successful `adapter.send(outgoing)` in the delivering lane (progressive OR completed), `store.delete(image_id)` collapses the unauth GET window from 30 days to zero for chat-delivered charts. API-origin (no `:`) keeps the 30-day GET per Phase A §http-api. Mutually-exclusive lanes (api skips both seams; chat gets one lane) make double-delete impossible.

**Trigger:** ~3 LOC + 1 test. Test: successful send → `store.delete` called; failed send → NOT called; API-source → NOT called; both-lanes-fail-fast scenario → assert `delete.call_count == 1` total.

**Deferred-ledger move:** Phase D reviser moves the prior §open-questions #5 row from the deferred ledger to the adopted ledger.

---

## §phase-b-r2-addendum-10 — multi-image discipline (arch-rec §3 amendment #7)

**Decision:** Multi-image is v1-supported. Marker order preserved (deterministic dispatch through `extract_chart_images` + the construction `images=[…]` list). NO silent drops — every dropped image WARNs with `image_id[:8]` + size + content_type. Discord `file=` / `files=` are mutually exclusive in discord-py 2.7.1 — use `files=[…]` for N>1. Telegram sequential sendPhoto/sendDocument per image. Slack: single `files_upload_v2` with N files (verify SDK signature at impl).

**Dissolved question:** Phase B plan OQ#5 ("One image per message vs many?") is RESOLVED by this amendment.

---

## §phase-b-r2-addendum-11 — empty-content-with-images Discord guard (arch-rec §3 amendment #11)

**Decision:** Extend the existing `if not content:` early-return at `daemon/sources/adapters/discord/adapter.py:1575-1577` to `if not content and not message.images:`. Without this, marker strip can leave empty content + images attached, and the adapter would early-return without ever sending.

**Architect-mandated change:** 1-line fix with a regression test (`test_discord_empty_content_with_images`).

---

## §phase-b-r2-addendum-12 — per-id isolation (arch-rec §3 amendment #3)

**Decision:** One bad (hallucinated / not-found) id MUST NOT kill sibling images. In `_resolve_chart_images`, each `open_full` call is wrapped in `try/except`; on failure, WARN with `image_id[:8]` + exception, continue with the rest. Returned list contains the successfully-resolved siblings.

---

## §phase-b-r2-addendum-13 — per-id dedupe first-occurrence order (arch-rec §3 amendment #2)

**Decision:** In `extract_chart_images`, dedupe `image_ids` while preserving first-occurrence order. Implementation: `seen: set[str] = set()` + skip-on-second-occurrence. A duplicate marker in the same content (rare LLM behavior) does NOT re-deliver the same image.

---

## §phase-b-r2-addendum-14 — near-miss strip-only sweeper (arch-rec §3 amendment #14)

**Decision:** Add a NEW, clearly-labeled secondary pattern (NOT the locked regex) that STRIPS malformed markers but NEVER EXTRACTS (R4 comment correction: the pattern CAN textually overlap the locked form at the pattern level — operationally safe ONLY because the locked-regex extraction pass runs FIRST; swept lines are exactly those the locked regex did not match). Pattern: `^\s*<!--\s*ens-img:chart-render:[^>\n]{0,64}-->\s*$`. Cosmetic junk-line cleanup for slightly-mutated markers (indented, extra whitespace, mutated id). LOCKED regex stays byte-stable.

---

## §phase-b-r2-addendum-15 — Phase B doc-text fix (slack-setup.md line refs)

**Correction:** The plan-as-written cited `docs/sources/slack-setup.md:35-82` for the YAML manifest + scopes table. Actual refs (verified spot-check):
- YAML manifest fenced block: `:17-61` (yaml opener at `:17`, closer at `:61`)
- YAML `scopes:` subsection: `:35-48`
- Scopes table: `:69-82` (header at `:69`, last row at `:82`)

Phase B plan §7 (Slack component) and Task #43 (B.6 docs) use the corrected refs.

---

## §phase-b-r2-addendum-16 — Phase B firm decisions (closing OQ)

- **Telegram `parse_mode=None` for image captions** (closes OQ#7 from prior plan): FIRM DECISION. Mermaid `<` characters break HTML parse_mode. Default `parse_mode=None` (plain text) is safe.
- **Per-arch-rec §6 Focus 6 — Sequencing & instance reuse:** A ∥ C → B → D CONFIRMED. B scope grew (both-seam + breaker guardrails + both construction sites + Discord atomic-first-unit + capability cached flag) but is still one restart + one promote. No dependency inversion.

---

# Phase D decisions — chart-image-delivery (cross-cutting consolidation + release)

## §degradation-v2-toolchain — render-failure taxonomy (arch-rec §3 amendment #20, added R5)

**Decision:** Charter-side degradation taxonomy per arch-rec Focus 4.3 — syntax errors keep the 3-attempt retry budget (SYNTAX ONLY); render-side failures never retry. Cold → pre-warm expected / async queue marker; env-class mmdc errors (ENOENT/sandbox/EACCES) → one retry after sandbox check, else degrade; timeout 124 → kill, no retry; `TmpImageStoreFull` / store-not-initialized → degrade, no retry (cap won't free mid-session); other `image_save` errors → one retry, else degrade. Every rung: no marker, text-only Mermaid, log line. Where this conflicts with §degradation's earlier charter-side retry prose, amendment #19's never-retry rule is operative (architecture-recommendation.md §3 precedence). Recorded here because the R2 fold-in missed this addendum (iter-003 tracking note).

**Status:** APPENDED at Phase D planning. Companion to `phaseD-plan.md` (same directory). Phase A's locked sections (§marker, §capture, §degradation, §http-api) and Phase B's locked §phase-b-* sections are unchanged. This record is append-only.

**Author:** planner[v2] via plan-creation worker
**Date:** 2026-10-04

---

## §phase-d-test-matrix — consolidated regression pin audit (24 pins — R2 update)

**Decision:** Phase D consolidates the full do-not-break list across Phases A, B, C into ONE executable audit — both as a pytest module (`tests/test_chart_image_delivery_audit.py`) and as a standalone bash wrapper (`tools/audit-chart-image-delivery.sh`). **24 pins** (R2: +1 = pin #24 Ari pre-warm; pin #6 INVERTED per arch-rec §1 + §3 amendment #21). Pins split into PRESERVATION (pre-existing invariants that already hold on clean `latest`) and FEATURE (assert post-merge state). Exit code 0 iff all pass. Runnable in CI before merge AND by operators post-deploy.

**Rationale:**

- The per-phase plans each carry their own do-not-break list (Phase A's `_BUSY_STRING` × 3 sites + `_PAUSED_STRING` × 3 sites + marker byte-stability; Phase B's dispatcher skip rules + BOTH-seam extraction + API-source-keep-marker + chunk ordering + circuit-breaker; Phase C's `_BUSY_STRING`/`_PAUSED_STRING` byte-pins + Wedged-Charter unchanged + 20-coverage). Without consolidation, a regression in any one of them slips until the next feature merge.
- The pattern source is `scripts/upgrade/ledger_check.py` (per ADR-021 N=3 cycle discipline) — a single executable gate for "is the contract still intact".
- Phase A tests already verify their own per-phase pins (e.g., `test_chart_tools.py:540` pins `_BUSY_STRING`). Phase D's audit is the CROSS-CUTTING version — it catches pins that span multiple files / phases.
- **R2 INVERSION (pin #6):** the prior framing "progressive path does NOT call extract_chart_images" PINNED THE DOA DEFECT (extraction only on `dispatch_completed`, which `progressive_sent_sources` discards for chat sources — see arch-rec §0/§1). Pin #6 now asserts BOTH-seam extraction (arch-rec §3 amendment #21 verbatim). The prior test "marker NOT extracted in dispatch_message" would have locked the marker-leak bug.
- **R2 ADD (pin #24):** per arch-rec §3 amendment #18 + §6 pending #3, A/R2 must pre-warm the toolchain as a deploy step. Pin asserts the one-line reminder is present in `.agents/shared/context.md` AND `agents/ari/workflow.md` (content-addressable, NOT line numbers).

**Pin catalog (24 pins — full list lives in `phaseD-plan.md` Components §2):**

| Pin group | Pins | Class | Source of truth |
|-----------|------|-------|-----------------|
| Phase A — busy/paused string pins | 1 (`_BUSY_STRING` 3 sites), 2 (`_PAUSED_STRING` 3 sites) | PRESERVATION | `daemon/tools/chart_tools.py:_BUSY_MSG` (:55) + `:222` paused-literal; `tests/test_chart_tools.py:_BUSY_STRING` (:296) + `_PAUSED_STRING` (:297-298); `agents/_prompt_system/innate-skills/chart/skill.md` (:62, :68 busy; :74 paused) |
| Phase A — marker contract | 3 (regex byte-stable in `decisions.md` §marker — the LOCKED form; NOT the near-miss sweeper), 4 (`chart_tools.py:525` verbatim passthrough) | PRESERVATION | `decisions.md:103-104`, `daemon/tools/chart_tools.py:525` |
| Phase B — dispatcher | 5 (no-colon skip before extraction — applies to BOTH seams), 6 **INVERTED** (BOTH-seam extraction: `dispatch_message` AND `dispatch_completed`, last transform, after no-colon skip + adapter lookup; `images` populated at `dispatcher.py:170` AND `:243`; never at `registry.py:980`), 7 (`OutgoingMessage.images` defaulted) | 5 PRESERVATION / 6+7 FEATURE | `daemon/sources/dispatcher.py:132-134` + `:209-211` (no-colon); `:170`+`:243` (construction); `daemon/sources/base.py:52-60` |
| Phase B — adapters | 8 (Discord `file=None` + `files=[…]` for N>1), 9 (Discord 2000-char chunking), 10 (Telegram multipart 3-retry transport + 4xx-non-transient classification), 11 (Telegram sendPhoto/sendDocument ladder), 12 (Slack `files_upload_v2` single-call + `SlackCapabilityError` + capability-flag zero-API-calls), 13 (Slack `BLOCKS_CONTENT_THRESHOLD` 400 chars) | 9 + 13 PRESERVATION / 8, 10-12 FEATURE | `daemon/sources/adapters/{discord,telegram,slack}/*` |
| Phase B — constants | 14 (4 size constants + `CHART_IMAGE_MIME_WHITELIST`) | FEATURE | `daemon/constants.py` |
| Phase A — meta | 15 (charter `tools.allow` includes `"image"`), 16 (charter `meta.json` version `1.2.0`) | FEATURE | `agents/charter/meta.json` |
| Phase C — coverage | 17 (**20** chart-capable agents reference "Chat Delivery"), 18 (no `.md` path tokens), 19 (`_BUSY_STRING` skill.md byte-identical), 20 ("Wedged-Charter Recovery" unchanged) | ALL FEATURE | `agents/_prompt_system/innate-skills/chart/skill.md` + 20 `agents/<name>/rule.md` (or tools_note/soul/workflow for the canonical-home per agent) |
| Phase B — docs | 21 (`docs/sources/slack-setup.md` carries `files:write` in YAML manifest `:17-61` AND scopes table `:69-82` per §phase-b-r2-addendum-15) | FEATURE | `docs/sources/slack-setup.md` |
| Phase B — image seam | 22 (`daemon/manager.py` `tmp_image_store` property still public) | PRESERVATION | `daemon/manager.py` |
| Phase B — e2e infra | 23 (`tests/e2e/mock_source_server.py` captures the whole `OutgoingMessage`) | PRESERVATION | `tests/e2e/mock_source_server.py:99-110` |
| Phase A — pre-warm | 24 (R2 ADD — `.agents/shared/context.md` + `agents/ari/workflow.md` carry the deploy-step pre-warm reminder per arch-rec §3 amendment #18) | FEATURE | content-grep in both files |

**Pin class split (per R5 fix — the pre-baseline gate depends on this):**

- **PRESERVATION** (pins 1, 2, 3, 4, 5, 9, 13, 22, 23 — ~9 pins): pre-existing invariants that already hold on clean `latest` BEFORE any feature merge. Run them in the **D.0 pre-baseline gate task** (`tools/audit-chart-image-delivery.sh --class preservation`) on clean `latest` before A/B/C merge; if any fail, escalate as **pre-existing breakage** (open a fix ticket; do NOT log as feature regression).
- **FEATURE** (pins 6, 7, 8, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 24 = 15 pins — R5: #10 wholly FEATURE, matching phaseD): assert post-merge state. These CANNOT pass on pre-merge `latest`; they are the gate at release cut.

**Audit-script + e2e-test run order (per `phaseD-plan.md` Tasks D.0 + D.1):**

1. **D.0 pre-baseline:** `tools/audit-chart-image-delivery.sh --class preservation` against clean pre-merge `latest`. Exits 0 → PRESERVATION pins hold (any failure = pre-existing breakage).
2. **D.1.a** `tests/test_chart_image_delivery_audit.py` — 24 pin tests, one per row (PRESERVATION + FEATURE).
3. **D.1.b** `tools/audit-chart-image-delivery.sh` (default `--class all`) — bash wrapper invoking the audit pytest + a couple of static greps for the marker regex + the prompt-audit checklist (Phase C's grep).
4. **D.1.c** `tests/test_chart_image_delivery_e2e.py` — **8** e2e test groups (full-chain happy + api-keep-marker + amendment #21 list; degraded image_get + wrong-MIME + provenance-mismatch; multi-chart + concurrent ordering; multi-source isolation + foreign-feature; installer hygiene; out-of-scope SHA tripwire; **REAL-ASTREAM-LANE per R8 + arch-rec §3 amendment #21**; **store.delete-after-upload per R2 adopted + arch-rec §3 amendment #22**).

---

## §phase-d-version-bump — version bump decision

**Decision:** Bump `0.16.12 → 0.16.13` at release cut. Mirror in BOTH `daemon/__init__.py` and `pyproject.toml` (verified at `daemon/__init__.py:3` `__version__ = "0.16.12"` and `pyproject.toml:3` `version = "0.16.12"` — the two homes of the version constant must move together).

**Rationale:**

- **Semver minor.** New feature on the existing minor line. No breaking change; no API change; no config flip. The `0.16.x` release line uses staged releases + promote ceremonies (per `upgrade_policy`); minor bumps are the standard cadence.
- **NOT patch.** A patch bump signals a bug fix; chart-image-delivery is a feature.
- **NOT major.** No breaking change to module, daemon API, config shape, or prompt contract.

**Timing — release cut only:**

- The version bump is performed at release cut (Phase D.6), NOT in Phase D's own planning/execution commits. Phase D plans the bump; the release commit performs it.
- The release cut coordinates via the project's standard release process (per `upgrade_policy.ratified_by_user` + `upgrade_workflow_preference` effective from v0.16.8: prefer speed, slim safety steps; full backup only when explicitly requested by user).
- Collision risk with another feature also bumping to `0.16.13` is mitigated by the release coordinator (open question #4 in `phaseD-plan.md`).

**CHANGELOG entry timing:** the entry from `docs/changelog-pending/chart-image-delivery.md` slices into `CHANGELOG.md` `[Unreleased]` at release cut, NOT in Phase D's own commits. Keep a Changelog format (verified at `CHANGELOG.md:1-5` — "The format is based on Keep a Changelog"; entry style at `:32-65` `[0.14.2] — 2026-09-25`).

**Verification before release cut:**

```bash
# Phase D implementer (release cut):
grep -n '__version__' daemon/__init__.py        # expect: __version__ = "0.16.13"
grep -n '^version' pyproject.toml                # expect: version = "0.16.13"
grep -n 'chart-image-delivery' CHANGELOG.md      # expect: ≥1 hit under [Unreleased]
```

---

## §phase-d-release-report — release report template (R2 update)

**Decision:** The Phase D implementer produces `.agents/shared/planning/chart-image-delivery/release-report.md` (the FILLED-IN template) with **nine** enumerated sections per `phaseD-plan.md` Components §6 (R2 added the adopted-items ledger + deferred+residual sections; R3 renumbered the map to nine contiguous sections §1-§9 — canonical: 1 What shipped, 2 Test evidence, 3 USER ACTION ITEM, 4 Restart/promote matrix, 5 Rollback notes, 6 Adopted-items ledger, 7 Deferred-items ledger + accepted residual, 8 Real-platform smoke evidence, 9 Sign-off):

1. **What shipped** — per-phase deliverable list (Phase A's **10** files [9 code/prompt + decisions.md verify/append], Phase B's 7 daemon files + slack-setup.md row, Phase C's **20** cardinal references + chart skill section). File lists verbatim. [R5, iter-003 blocking #1]
2. **Test evidence** — per-suite green counts (every suite named in `phaseD-plan.md` §Test Strategy). Counts from CI output, not just "✓".
3. **USER ACTION ITEM — Slack `files:write` scope** — verbatim wording per `phaseD-plan.md` Components §6 §3 (the 5-step operator procedure + the text-only fallback note).
4. **Restart/promote matrix** — verbatim table from `phaseD-plan.md` §restart-promote (the four rows: Phase A = no restart, Phase B = restart + promote, Phase C = no restart, docs/tests = n/a).
5. **Rollback notes** — Phase B is the risky surface; describes the graceful degradation (text-only Mermaid delivery) if reverted; Phase A revert is the inverse (charter stops emitting the marker; chart-render PNGs in `tmp_images` unaffected; sweep reaps them on schedule); Phase C revert: agents may stop cross-referencing Chat Delivery for chat-source turns; over-deliver-when-uncertain default still applies.
6. **Adopted-items ledger (R2 NEW)** — see §phase-d-adopted-items below.
7. **Deferred-items ledger (R2 reduced from 5 to 4)** — see §phase-d-deferred-items below. Plus the ACCEPTED RESIDUAL stale-real-id (~1-3%, ledgered per arch-rec §4 merge-table focus #6 + §6 pending #2; revisit trigger = strip-rate >10% post-Phase C or user report).
8. **Real-platform smoke evidence** — Discord (always-on): screenshot of the PNG in the channel + the marker-stripped explanation; Slack (gated): text-only delivery + WARN log line until scope granted; Telegram (gated): sendPhoto delivery with the PNG inline + caption (Telegram `parse_mode=None` per `decisions.md` §phase-b-r2-addendum-16).
9. **Sign-off** — project owner + developer/tester who ran Phase D; date; release tag.

**Reviewer (project owner per the project's release-review chain):** cross-checks every section's evidence before signing off on the release cut.

---

## §phase-d-adopted-items — adopted-items ledger (R2 NEW)

**Decision:** Items promoted out of the deferred ledger during the commission (R2 fold-in). The release report §6 carries this row to make the promotion explicit. Currently ONE adopted item; future Phase D-style consolidation phases may add to it (append-only).

| # | Item | One-line summary | Origin |
|---|------|------------------|--------|
| 1 | **`store.delete(image_id)` after successful chat delivery** | R2 promotes the prior §open-questions #5 deferred item to ADOPTED. After a successful `adapter.send(outgoing)` in the delivering lane (progressive OR completed), `store.delete(image_id)` collapses the unauth GET window from 30 days to zero for chat-delivered charts. API-origin (no `:`) keeps the 30-day GET per Phase A §http-api. Mutually-exclusive lanes (api skips both seams; chat gets one lane) make double-delete impossible | arch-rec §3 amendment #22 + `decisions.md` §phase-b-r2-addendum-9 (Phase B task + test landed) |

**Trigger (~3 LOC + 1 test):** Successful send → `store.delete` called; failed send → NOT called; API-source → NOT called; both-lanes-fail-fast scenario → `delete.call_count == 1` total.

**Coverage:** Phase D's Group 8 in `tests/test_chart_image_delivery_e2e.py` covers the four test cases (chat-delivery success → 1 delete; upload-fail → 0 delete; API → 0 delete; both-lanes-fire → 1 total delete).

---

## §phase-d-deferred-items — deferred-items ledger (R2 reduced to 4 + 1 accepted residual)

**Decision:** **Four** items deferred to post-release work (R2 reduced from 5 — `image_delete` moved to §phase-d-adopted-items). None block the chart-image-delivery release. **Plus one ACCEPTED RESIDUAL** (NOT a deferred engineering item — sound design debt needing an instance-carrying seam first).

| # | Item | One-line summary | Owner / ticket |
|---|------|------------------|----------------|
| 1 | Telegram 4096-char text chunking | Pre-existing defect (`daemon/sources/adapters/telegram.py:262` `send()` lacks chunking for >4096-char text); unrelated to chart-image-delivery but inherited; ~30 lines of hardening; Phase B's text-fallback path may drop very large Mermaid blocks at Telegram | New ticket — "Telegram text chunking" |
| 2 | `source_hint` system-context injection | Phase C's open question #1: a system-context field that tells the agent "this turn arrived via Discord/Slack/Telegram" so the "Chat Delivery" wording is unambiguous. Phase C's over-deliver-when-uncertain default is sufficient for v1; deferred if a future chat-source-aware agent prompt wants explicit confirmation | New ticket — "source_hint injection for chart-aware agents" (prompt-side, not adapter-side) |
| 3 | Real-platform e2e credentials | Always operator-dependent. The release manual smoke is operator-driven; Discord (always-on), Slack (gated on `files:write`), Telegram (gated on real bot token). Phase D's release report documents the gating; the smoke evidence may be partial if the operator hasn't yet granted Slack scope / provisioned a Telegram bot | No ticket — operator workflow |
| 4 | HTTP-API structured image-ref response | Phase A §http-api currently: marker verbatim only (clients parse and fetch). Future: a structured payload `{content, images: [{image_id, content_type, ...}]}` for HTTP-API callers that don't want to parse the marker. Backward-compatible addition (the marker still works). Defer until a client expresses the want | New ticket — "HTTP-API structured image-ref response" (backward-compatible additive) |

### ACCEPTED RESIDUAL (R2 NEW — not a deferred engineering item):

| # | Item | One-line summary | Revisit trigger |
|---|------|------------------|-----------------|
| R | **Stale-real-id wrong-image delivery (~1-3%)** | A real stale `image_id` from an earlier turn or another conversation attaches the wrong/old image. Inherent to in-band marker Option A. Mint-ledger-recorded sound design debt needing an instance-carrying seam first | **strip-rate >10% post-Phase C or user report** (per arch-rec §6 pending #2 ratified; arch-rec §5 risk "Stale-real-id wrong image delivery — ledgered"; arch-rec §4 merge-table focus #6 verbatim "Real stale id (earlier turn / other conversation) … 1-3% … accepted residual, ledgered; revisit if strip-rate >~10% post-Phase C or on user report") |

**Why these are NOT Phase D's job:**

- Phase D's mandate is "consolidate the three lanes for release". New feature work is OUT of scope (per `phaseD-plan.md` §Out of Scope).
- The four items above each touch either: (a) an adjacent adapter hardening (Telegram chunking); (b) a context-injection concern (source_hint); (c) an operator workflow (credentials); (d) a future API surface (HTTP-API structured payload). All are intentionally DEFERRED, not IN SCOPE.
- The accepted residual is a SOUND-DESIGN artifact of in-band marker Option A — solving it requires a structural change (instance-carrying sidecar; Option B territory; arch-rec §4 verified the structural unsolvability at the delivery seam). Phase D records it; it is NOT in the deferred-engineering ledger.

**The deferred-items ledger is append-only.** Future Phase D-style consolidation phases (for unrelated features) do NOT inherit these items — each feature's deferred ledger is its own. The project backlog (`docs/stability-backlog.md`) may cross-link them.

---

## §phase-d-doc-verification — docs surface verification

**Decision:** Phase D verifies whether any docs surface BEYOND Phase B's `docs/sources/slack-setup.md` requires a chart-image-delivery mention. Spot-check per `phaseD-plan.md` Task 5.

**Candidate docs spot-checked:**

| Doc | Mentions chart? | Mentions image/attachment? | Requires update? |
|-----|-----------------|---------------------------|-------------------|
| `docs/setup.md` | (search) | (search) | conditional on spot-check |
| `docs/sources/discord-setup.md` | (search) | (search) | conditional on spot-check |
| `docs/pluggable-sources-architecture.md` | (search) | (search) | conditional on spot-check |
| `docs/architecture.md` | (search) | (search) | conditional on spot-check |
| `docs/agents.md` | (search) | (search) | conditional on spot-check |
| `docs/agent-prompt-writing-guide.md` | (search) | (search) | conditional on spot-check (Phase C audited this; if Phase A/B/C changed it, file an issue — out of Phase D scope) |

**Default plan:** NO additional doc updates beyond Phase B's `slack-setup.md` row + the CHANGELOG entry. The architecture / pluggable-sources / setup / agents docs are architecture-level (not feature-level) and adding a chart-image-delivery mention would be scope creep.

**Verification step** (Phase D.2 Task 5): the Phase D implementer runs a grep + visual scan; if any doc requires a real update, it's listed here in this section (append-only); otherwise the only doc change is `slack-setup.md` (Phase B's row).

---

## §phase-d-restart-promote — consolidated restart/promote matrix

**Decision:** The FINAL restart/promote matrix for the chart-image-delivery release. THIS section (§phase-d-restart-promote) is the CANONICAL matrix; the release report §4 reproduces it verbatim (the 6-row table here consolidates the plan's 4-row matrix plus the docs + release-cut rows). [R5 placement clarification]

| Phase | Files | Daemon restart? | Promote? |
|-------|-------|-----------------|----------|
| A (10 files) | `agents/charter/workflow.md`, `agents/charter/rule.md`, `agents/charter/soul.md`, `agents/charter/meta.json`, `agents/charter/skills-template/install-mermaid-cli.md`, `agents/charter/skills-template/install-mermaid-cli.lib.sh`, `agents/_prompt_system/innate-skills/chart/skill.md` (paragraph addition), `.agents/shared/context.md` (pre-warm one-liner), `agents/ari/workflow.md` (commissioning note), `decisions.md` (verify/append only) | NO (agent-prompt + bash lib only; picked up at next instance spawn / lib sourced per-render) | NO |
| B | `daemon/sources/base.py`, `daemon/sources/registry.py`, `daemon/sources/dispatcher.py`, `daemon/sources/adapters/discord/adapter.py`, `daemon/sources/adapters/slack/adapter.py`, `daemon/sources/adapters/telegram.py`, `daemon/constants.py` | **YES** | **YES** |
| C | `agents/_prompt_system/innate-skills/chart/skill.md` (Chat Delivery section + table row), **20** agent canonical-home files (`agents/*/{rule,soul,tools_note,workflow}.md`) | NO (agent-prompt only) | NO |
| D (10 files) | `tests/test_chart_image_delivery_e2e.py` (NEW), `tests/test_chart_image_delivery_audit.py` (NEW), `tools/audit-chart-image-delivery.sh` (NEW), `docs/changelog-pending/chart-image-delivery.md` (NEW), `.agents/shared/planning/chart-image-delivery/release-report-template.md` (NEW), `.agents/shared/planning/chart-image-delivery/release-report.md` (NEW — the FILLED, COMMITTED report; iter-003 blocking #6), `decisions.md` (APPEND §phase-d-*) | n/a (test/docs/planning only) | n/a |
| docs | `docs/sources/slack-setup.md` (Phase B's row) | no (doc only) | no |
| Release cut (Phase D.6) | `daemon/__init__.py` (`__version__` bump), `pyproject.toml` (`version` bump), `CHANGELOG.md` (`[Unreleased]` entry slice) | YES (release cut) | YES (per promote ceremony) |

**Total restart/promote cost:** ONE restart + ONE promote (driven by Phase B; coordinated with the release cut's version bump + CHANGELOG entry per Phase D.6). The release cut follows the project's upgrade workflow preference (per shared meta-kv `upgrade_workflow_preference`, effective from v0.16.8: prefer speed, slim safety steps; full backup only when explicitly requested by user).

**Operator post-deploy verification:** `tools/audit-chart-image-delivery.sh` against the live daemon; the live rung's `daemon.__version__` reports `0.16.13`; Discord manual smoke confirms the PNG attachment in the channel.

---

## §phase-d-implementation-dispatch — implementation shape

**Decision:** One tester (the same tester context that ran Phase A/B/C verification) + one reviewer (the project owner per the project's release-review chain). No developer for Phase D's own code (the consolidation is verifier-centric; Phase D writes tests + audit script + release report + decisions.md append, not feature code).

**Sequencing (per `phaseD-plan.md` Tasks D.1–D.5):**

1. D.1 — Audit-script + e2e scaffolding (Tasks 1-4): pytest module + bash wrapper + e2e tests + audit-script catches-a-known-regression sanity.
2. D.2 — Docs surface verification (Tasks 5-7): spot-check the 5 candidate docs; confirm Phase B's `slack-setup.md` row; verify `agent-prompt-writing-guide.md` unchanged.
3. D.3 — Version + CHANGELOG (Tasks 8-10): write the pending CHANGELOG slice; record the version-bump decision (this section); stage the bump locally (do NOT commit).
4. D.4 — Release report + decisions append (Tasks 11-13): write the template; APPEND §phase-d-* sections to decisions.md; fill in the report after A+B+C+D merge.
5. D.5 — Final regression + sign-off (Tasks 14-18): full regression run; manual Discord smoke; SHA tripwire.
6. D.6 — Release cut (separate from Phase D's own commits): version bump + CHANGELOG entry + tag + post-promote verification.

**Reviewer (project owner) cross-checks** at the release-report review:

1. All 24 audit pins green.
2. All **8** e2e groups green (including the **REAL-ASTREAM-LANE** Group 7 per arch-rec §1 dominant finding + arch-rec §3 amendment #21, and the **store.delete-after-upload** Group 8 per arch-rec §3 amendment #22 / `decisions.md` §phase-b-r2-addendum-9 R2 adopted).
3. SHA tripwire green (Phase A/B/C files unchanged).
4. USER ACTION ITEM wording verbatim.
5. Restart/promote matrix verbatim.
6. Deferred-items ledger complete.
7. Version-bump decision recorded.

**Why no developer for Phase D:** Phase D's own code (the audit + e2e + report + decisions append) is verifier-centric — it does NOT introduce new feature surface. The release-cut developer (per D.6) flips the version + slices the CHANGELOG; that's the standard release process, not a Phase D development task. If a regression is discovered mid-Phase-D that requires amending a sealed file (Phase A/B/C's files), Phase D's tripwire catches it and the discovery is escalated to the planner — Phase D's implementer does NOT silently amend.

---

## §phase-d-deferred-items-ledger — supplementary notes (cross-references) (R2 update)

This section is a cross-reference index for the deferred-items ledger (§phase-d-deferred-items above) and the adopted-items ledger (§phase-d-adopted-items). No new content; just pointers to where each item is discussed in detail:

**Deferred:**

- **Telegram 4096-char text chunking** — Phase B §phase-b-telegram-4096-deferred (Phase B's lock; content-addressed — line refs removed, R5).
- **`source_hint` system-context injection** — Phase C open question #1 (Phase C's lock) + Phase B §phase-b-source-hint-deferred (Phase B's deferral); both content-addressed (line refs removed, R5).
- **Real-platform e2e credentials** — operator workflow; no project-ticket home.
- **HTTP-API structured image-ref response** — Phase A §http-api (current marker-only contract; content-addressed, R5).

**Adopted (R2 NEW — promoted out of the prior deferred ledger):**

- **`store.delete(image_id)` after successful chat delivery** — R2 ADOPTED. Origin: Phase A §open-questions #5 at `decisions.md:240` (Phase A's prior deferral, now superseded); arch-rec §3 amendment #22 (Recommend ADOPT); `decisions.md` §phase-b-r2-addendum-9 (Phase B's lock with task + test landed); §phase-d-adopted-items above (Phase D's ledger).

**Accepted residual (R2 NEW — NOT a deferred engineering item):**

- **Stale-real-id wrong-image delivery (~1-3%)** — arch-rec §4 merge-table focus #6 verbatim "Real stale id (earlier turn / other conversation) … 1-3% … accepted residual, ledgered; revisit if strip-rate >~10% post-Phase C or on user report"; arch-rec §5 risk "Stale-real-id wrong image delivery — ledgered"; arch-rec §6 pending #2 (leader/user ratification). Mint-ledger-recorded sound design debt needing an instance-carrying seam first; revisit trigger per the verbatim arch-rec text. §phase-d-deferred-items above carries the entry.

---

## §phase-d-r2-fold-in — R2 fold-in record (revision loop 2)

**Decision:** This section records the R2 fold-in applied to Phase D's plan + decisions on 2026-10-04 (revision loop 2). All other Phase D decisions (R1) remain valid; R2 amendments are listed below for traceability.

**Amendments folded in (R2):**

| # | Amendment | Source | Phase D action |
|---|-----------|--------|-----------------|
| R1 (D-side) | Invert pin #6 + update test matrix per arch-rec §3 amendment #21 | arch-rec §0/§1 + §3 amendment #21 + §5 risk | Pin #6 in `§phase-d-test-matrix` inverted; e2e Group 1 expanded with `test_progressive_lane_extracts_and_strips` + `test_progressive_then_completed_no_double_send` + `test_extraction_after_adapter_lookup` + `test_duplicate_marker_dedup` + empty-content-with-images; `test_image_get_provenance_feature_mismatch_text_delivered` added to Group 2; `test_foreign_feature_marker_text_fallback` added to Group 4; `test_install_mermaid_cli_4_signal_readiness_probe_in_skill` added to Group 5; `test_full_chain_api_caller_keeps_marker` enhanced with resolution-accessor spy (`open_full`/`open_with_meta` — assert ZERO store calls; R5 correction: post-addendum-4 the record accessor is `open_full`) + e2e byte-for-byte twin |
| R2 | `store.delete` ADOPTED | arch-rec §3 amendment #22 + `decisions.md` §phase-b-r2-addendum-9 | Moved `image_delete-after-upload` row out of `§phase-d-deferred-items` into new `§phase-d-adopted-items` (R2 NEW); release report §6 carries the adopted ledger row; e2e Group 8 added with 4 test cases (`test_chat_delivery_calls_store_delete_once` + `test_chat_delivery_upload_failure_does_not_delete` + `test_api_source_does_not_delete` + `test_both_lanes_only_one_delete`) |
| R5 | Split pin catalog into PRESERVATION / FEATURE; add D.0 pre-baseline gate task | Pre-BR5 fix for the unsatisfiable pre-baseline acceptance | `§phase-d-test-matrix` table gains a Class column + split rows; new `D.0 pre-baseline gate` task (`tools/audit-chart-image-delivery.sh --class preservation`) added; audit-script spec accepts `--class preservation\|feature\|all`; acceptance criteria §Cross-cutting regression gains D.0 line + 24-pin counts |
| R6 | Pin #24 — Ari pre-warm reminder | arch-rec §3 amendment #18 + §6 pending #3 | Pin #24 added to `§phase-d-test-matrix` (FEATURE class; content-addressable grep in `.agents/shared/context.md` AND `agents/ari/workflow.md`); e2e Group 5 installer hygiene unchanged (this is a pin-only addition) |
| R8 | Add real-astream-lane e2e | arch-rec §0/§1 dominant finding + §3 amendment #21 | New e2e Group 7 (REAL-ASTREAM-LANE) added with 4 tests (`test_astream_discord_user_receives_png` + `test_astream_progressive_lane_marker_extracted` + `test_astream_progressive_lane_failure_routes_to_completed` + `test_astream_internal_agent_source_no_extract`); harness via existing `tests/e2e/` daemon-infrastructure |
| OPT-21→20 | Update 21 to 20 EVERYWHERE (per Phase C R2 reviewer correction) | `phaseC-plan.md:75-81` verified list | Narrative lines 14, 30, 44, 145, 185, 270 in phaseD-plan.md updated to "20"; risk-1 + risk-3 wording in decisions.md §phase-d-deferred-items updated; pin #17 catalog updated; test counts (Phase C coverage) updated; SHA tripwire (Group 6) updated; Component §6 §1 updated |
| OPT-busy/paused | Correct _BUSY/_PAUSED_STRING pin labels (verified sites: busy = chart_tools.py:55 + test_chart_tools.py:296 + skill.md:62,68 narrative; paused = chart_tools.py:222 literal + test_chart_tools.py:298 + skill.md:74 narrative) | Verified spot-check 2026-10-04 | Pin #1 (busy) and #2 (paused) re-labeled to content-addressable grep descriptions; pin #19 updated to specify call-site (skill.md) rather than `skill.md:74` (which is the paused site, not busy) |
| OPT-stale-real-id | Add stale-real-id residual (~1-3%, revisit trigger strip-rate >10% post-Phase C or user report) | arch-rec §4 merge-table focus #6 + §5 risk + §6 pending #2 | New row R in `§phase-d-deferred-items`; cross-ref added to `§phase-d-deferred-items-ledger`; risk #13 added to phaseD-plan.md Risks table |
| GLOBAL-precedence | Add precedence clause to phaseD-plan.md header | Project-wide convention adopted in R2 (per phaseC-plan.md:9 + phaseB-plan.md R2) | Header updated: `> **Precedence:** \`architecture-recommendation.md\` §3 governs over phase-plan prose on conflict.` |
| GLOBAL-status | Update Status to "Draft R2 — Revision loop 2 (R1-R9 fold-in)" | Project-wide convention | phaseD-plan.md header Status updated |

**Append-only discipline preserved:** Phase A's locked §marker / §capture / §degradation / §http-api / §open-questions and Phase B's locked §phase-b-* + §phase-b-r2-addendum-* sections are unchanged. Only the §phase-d-* sections appended after R1 were edited in R2.

**End Phase D decisions.** Phase A and Phase B sections remain locked; Phase D records (R1 + R2 fold-in) are append-only.

---

# §phase-b-impl-record — implementation record (Phase B dispatch 2026-10-04)

**Status:** APPENDED at Phase B implementation dispatch. Companion to `phaseB-plan.md`. Records the coder's faithfulness audit + the test surface as shipped + Phase D follow-ups flagged at commit time. Phase A's locked sections (§marker, §capture, §degradation, §http-api) and Phase B's prior §phase-b-* + §phase-b-r2-addendum-* sections are NOT amended.

**Author:** coder (working-lead)
**Date:** 2026-10-04 (Phase B implementation)

**Precedence:** `architecture-recommendation.md` §3 governs on conflict (no conflicts observed — all amendments applied).

---

## §phase-b-impl-1 — implementation summary

| Component | Plan ref | Commit | Notes |
|-----------|----------|--------|-------|
| `OutgoingMessage.images` + `ImageAttachment` | B.1 task #1 | 2f014f2e | `bytes_b64 = field(repr=False)` + redacting `__repr__`; frozen dataclass; defaulted field. |
| `SourceRegistry.manager` property | B.1 task #2 | 2f014f2e | 3-line `@property` returning `_manager`. |
| `extract_chart_images` + `_MARKER_RE` + `_NEAR_MISS_RE` + `_resolve_chart_images` + `_log_image_metadata` | B.1 tasks #3/#7 | 2f014f2e | LOCKED regex byte-stable; near-miss strip-only sweeper; per-id dedupe + isolation + provenance gate. |
| BOTH-seam extraction injection | B.1 tasks #4-#5 | 2f014f2e | After adapter lookup at `:158-165` / `:234-240`, BEFORE OutgoingMessage construction at `:170` / `:243`. `store.delete` after success in delivering lane. |
| Platform limit constants | B.1 task #6 | 2f014f2e | 8 MB / 10 MB / 50 MB / 1 GB / 4000-char / image MIME whitelist. |
| Discord native upload | B.2 tasks #8-#12 | 43a998e9 | empty-content-with-images guard; `file`+`files` kwargs; atomic-first-unit; text-only retry of chunk 1; multi-image order + no silent drop. |
| Telegram native upload | B.3 tasks #13-#18 | c8dfccca | `_api_call_multipart` + 4xx non-transient (`_TelegramNonTransientAPIError`); sendPhoto/sendDocument ladder; >50 MB / MIME-miss skip; caption>1024 follow-up; parse_mode=None; upload inside per-chat lock. |
| Slack native upload | B.4 tasks #19-#24, #46 | 476578d8 | single `files_upload_v2`; `SlackCapabilityError` + classify-before-record; per-token capability flag with pre-check zero-API short-circuit; WARN-once-per-channel; initial_comment truncation + follow-up; upload inside per-channel lock. |
| Tests (extract + BOTH-seam + adapter + e2e) | B.5 tasks #25-#42, #47 | 870f0914 | 60 new tests across 5 files; all green. |
| slack-setup.md scope update | B.6 task #43 | 870f0914 | `files:write` row in YAML + table (USER ACTION REQUIRED). |
| Oversize-image regression tests (Slack + Discord) | M1/M3 review-cleanups (3c32da1f) | 3c32da1f | +2 new tests; oversize image → WARN + skip that image; text floor preserved. |
| Telegram multi-image mixed-success order test | F7 council-review (this commit) | <this-commit> | +1 new test; 3 images, 2nd fails (4xx non-transient); order preserved + F5 delivered_image_ids. |
| Slack capability real-chain tests (F2/F4/F6) + F1/F3/F5/F8/F9/F11/F12 fixes | F1-F12 council-review | <this-commit> | Retired/rerouted 3 bypassing capability tests through the real `acquire_and_execute` chain; F6 added real assertions (was vacuous); F4 added global-scope test. |

**Commits (in order on `feature/chart-image-delivery`):**
```
2f014f2e feat(chart-image-delivery): Phase B.1 dispatcher extraction at both seams
43a998e9 feat(chart-image-delivery): Phase B.2 Discord native upload
c8dfccca feat(chart-image-delivery): Phase B.3 Telegram native upload
476578d8 feat(chart-image-delivery): Phase B.4 Slack native upload
870f0914 feat(chart-image-delivery): Phase B.5+B.6 tests + slack-setup doc
```

---

## §phase-b-impl-2 — doc-verification findings

**Discord (#8):** `discord.py 2.7.1` `discord.File(fp, filename)` accepts a `BinaryIO`-like fp and a filename; `files=[...]` is the multi-file variant. Verified `file=` and `files=` are MUTUALLY EXCLUSIVE in discord-py 2.7.1 — discord.py raises `ValueError: Cannot mix file and files keyword arguments`. Plan adopts `files=[...]` for N-image sends per amendment #6.

**Telegram (#13):** sendPhoto (≤10 MB), sendDocument (≤50 MB), image/png+jpeg for photo and any MIME for document. Caption limit 1024 chars. Verified at https://core.telegram.org/bots/api#sendphoto and https://core.telegram.org/bots/api#senddocument.

**Slack (#19):** `slack-sdk 3.42.0` `AsyncWebClient.files_upload_v2` kwargs: `channel_id`, `filename`, `content`, `initial_comment`. NO `chat.postMessage(file=)` follow-up for the file itself (the sketch in earlier drafts was deleted per amendment #9). Verified at https://api.slack.com/methods/files.uploadV2.

---

## §phase-b-impl-3 — anchor-drift report (per task constraint #6)

No anchor drift encountered. All anchors (`transfer_state` line numbers) matched reality in the actual files:
* `dispatcher.py:132-134` (no-colon skip) ✓
* `dispatcher.py:158-165` (adapter lookup `dispatch_completed`) ✓
* `dispatcher.py:209-211` (no-colon skip `dispatch_message`) ✓
* `dispatcher.py:234-240` (adapter lookup `dispatch_message`) ✓
* `dispatcher.py:170` (OutgoingMessage construction `dispatch_completed`) ✓
* `dispatcher.py:243` (OutgoingMessage construction `dispatch_message`) ✓
* `dispatcher.py:980` (registry.py /new construction — NEVER populated) ✓
* `discord/adapter.py:1554` (send method) ✓
* `discord/adapter.py:1575-1577` (empty-content guard) ✓ — extended to `and not message.images` per amendment #11
* `telegram.py:148-197` (_api_call) ✓
* `slack/adapter.py:380` (send method) ✓
* `slack/adapter.py:340-378` (_safe_api_call) ✓ — `SlackCapabilityError` propagation added BEFORE generic handler per amendment #4
* `manager.py:2545` (tmp_image_store property) ✓
* `tmp_image_store.py:480-494` (open_full record) ✓
* `tmp_image_store.py:436-455` (open_with_meta bytes) ✓

---

## §phase-b-impl-4 — backward-compat verification

All existing 376 tests in the touched suites still pass (78 dispatcher + 196 Discord + 45 Telegram + 114 Slack + 6 e2e = 439 after our work; pre-existing 376 + 63 new = 439). Per-finding breakdown of the 63 new tests:
- Phase B.5+B.6 (870f0914): 60 new tests across 5 files
- Phase A review-cleanups M1/M3 (3c32da1f): 2 new tests (Slack + Discord oversize-image regression)
- F7 council-review (this commit): 1 new test (Telegram multi-image mixed-success order)

Single test updated:
* `tests/test_discord_adapter.py::test_send_strips_llm_tags_by_default` reads `call_args.kwargs['content']` instead of `call_args.args[0]` — required by the new kwarg-only `_send_single_chunk` signature (Phase B.2 file=/files= support).

No `daemon/tools/chart_tools.py` changes (Phase A locked; passthrough at chart_tools.py:525 verbatim). No `agents/` changes (Phase C closed). No migration / new HTTP route / new daemon service.

F2 council-review: the three Slack capability tests in `tests/test_slack_adapter.py` (`TestSlackCapabilityClassifiedBeforeRecord.test_missing_scope_raises_capability_error`, `TestSlackCapabilityFlagZeroApiCalls.test_capability_flag_short_circuits_subsequent_sends`, `TestSlackWarnOncePerChannel.test_warn_once_global_across_channels`) were MODIFIED in-place to stub `_do_api_call` instead of `_safe_api_call` — the real `SlackTieredRateLimiter.acquire_and_execute` chain now runs end-to-end. F1's `SlackCapabilityError: raise` guard is exercised: without the F1 fix, the rate-limiter catch-all would swallow the capability error, the breaker would record_failure on a config problem, and these tests would FAIL. Verified by reverting F1 against the F2-rerouted tests.

---

## §phase-b-impl-5 — Phase D follow-ups

1. **Slack USER ACTION ITEM (operator side):** operator must grant `files:write` scope in their Slack app config + reinstall. Until granted, capability detection in `send()` degrades to text-only delivery + one-time WARN log per channel. Doc surface updated in `docs/sources/slack-setup.md` (YAML manifest `:35-48` AND scopes table `:69-82`); the live-workspace token grant is an operator action item, not a code change.

2. **Real-platform smoke evidence** — Phase D's commission: Discord (always-on, since existing token has `files:write` semantics on Discord by default), Telegram (needs user to grant a real bot + chat), Slack (gated on operator scope grant).

3. **Multi-chart e2e test (real-astream lane)** — Phase D's task. Phase B ships the dispatcher-side logic; the multi-image end-to-end test that exercises the real astream lane (not a mocked dispatcher seam) is Phase D's per architecture-recommendation.md §3 amendment #21.

4. **Telegram 4096-char text chunking (DEFERRED)** — pre-existing defect; Phase B's text floor delivers text > 1024 via caption truncation + sendMessage follow-up, but text > 4096 (Telegram's per-message limit) is not chunked. Out of Phase B's scope; recorded in §phase-b-telegram-4096-deferred.

5. **`source_hint` system-context injection** — deferred to Phase C (per §phase-b-source-hint-deferred).

6. **`OutgoingMessage.images` field persistence audit** — Phase D should verify no row-level DB serializes `images` accidentally (transport-only invariant per amendment #12; `bytes_b64` is `field(repr=False)` for repr/dataclasses-asdict suppression, but `asdict()` still includes the value — callers MUST NOT persist the full message).

7. **Slack capability flag reset on token rotation** — flag is per-adapter-instance lifetime (in-memory); token rotation typically requires daemon restart, which resets the flag. Documented at risk #23 in phaseB-plan.md.

