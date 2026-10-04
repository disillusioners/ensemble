# Architecture Recommendation — chart-image-delivery

**Date:** 2026-10-04 05:50 UTC
**Commission:** `chart-image-delivery` (branch `feature/chart-image-delivery`, base `latest` @ `cf8efbef`)
**Author:** architect (controller) — synthesized from dispatched analysis; no direct design work
**Inputs (all read-only analysts):**
- Council `architect-council-chart-image-transport` — governor `bf921a19-0017-4a10-8470-20f9bc3d826d`, 2 councilors (worker@agentic, worker@coding), skill `data-flow-design` — transport contract (Q1 channel / Q2 extraction seam / Q3 upload ownership), Round-0 verdicts + Round-1 cross-examination
- Worker `architect-worker-render-durability` — `33047ef0-ab9b-45fd-acce-49d682a4ee23`, skill `resilience-design` — render-pipeline durability (focus 4)
- Worker `architect-worker-surface-security` — `7fb23ec1-6c54-4aa0-b004-53ca3bf29d29`, skill `security-design` — new-surface threat model (focus 5)

**Status:** COMPLETE — 3/3 reports in, skills confirmed loaded on all lanes, council dissent recorded (§8). No gaps.

---

## 0. Verdict

Ship the plan with **one structural correction** and a **hardening package**. The structural correction: the plan places marker extraction on `dispatch_completed` ONLY — but for external chat sources the final AI message is normally delivered by the **progressive lane** (`dispatch_message`), and `dispatch_completed` then discards via the `progressive_sent_sources` guard. As planned, the feature is **dead-on-arrival for the exact user story it exists to serve** (a Discord user would see the marker as literal text and never receive the PNG) — while mocked-dispatcher tests pass green. Extraction must run at **both seams**. Everything else survives adjudication: keep the in-band marker (Option A) hardened; dispatcher owns byte resolution; adapters own platform ladders; shared text-floor invariant; two mandatory circuit-breaker guardrails; durable toolchain via deploy-step pre-warm + flock-guarded self-heal; security pins on log redaction, provenance gating, and render sandboxing.

---

## 1. 🔴 Dominant finding — the extraction lane (resolves Focus 2 definitively)

**Finding (council, verified twice with citations, independently corroborated):** For an external chat source, the final AI message is delivered via the **progressive lane** — in-loop when `language_check` is OFF, buffered and re-dispatched post-loop when ON (`daemon/services/instance_messaging.py:4506-4566`, `:4815-4834`; `dispatch_source` stamped from `message_source` at `:3102`) — and `dispatch_completed` then discards via `progressive_sent_sources` (`daemon/sources/dispatcher.py:125-128`; guard added on send-success `:261-264`, **not** on adapter-False `:265-266`). Verified against `graph.py:4496` / `:4620-4631` / `:12460-12461` (both graph branches, `language_check_active` default). Corroborated by (a) the pre-commission explorer report (two dispatch sites: progressive `instance_messaging.py:3053-3128`, final `message_processing_pipeline.py:720-795`) and (b) the dissenting council lane's **own** once-only test design ("dispatch_message first sends; dispatch_completed returns at 127").

**Consequence if uncorrected:** extraction never runs in production for chat sources; the marker leaks as visible literal text (decisions.md:95's "renders as nothing" claim is **false for all three clients** — none hide HTML comments); no image is ever uploaded; tests stay green.

### The pin (implementers pin this verbatim)

> `extract_chart_images` runs in `dispatch_message` AND `dispatch_completed` as the LAST content transformation before `OutgoingMessage` construction — **after** the no-colon skip (`dispatcher.py:132-134` / `:209-211`), after source validation, after the internal-report skip, and **after** the adapter lookup (`:158-165` / `:234-240`). API-origin (no-colon) sources return at the skip and never reach extraction: marker verbatim in content, `open_with_meta` never called. Internal colon-sources (`internal_agent:*`) return at the adapter lookup before extraction.

**Implications the implementer must ship:**
- **Invert plan pin #6** ("progressive-path untouched") and rewrite the phaseB-plan.md:610 acceptance that pins "marker NOT extracted" in `dispatch_message` — that test as written **locks the marker-leak bug**. Replace with `test_progressive_lane_extracts_and_strips`.
- Populate `images` at **both** construction sites (`dispatcher.py:170` AND `:243`); never at `registry.py:980`.
- Once-only is preserved structurally: one `OutgoingMessage` per lane per message; the `progressive_sent_sources` guard keeps exact semantics (progressive delivered → completed discards; progressive adapter-False → completed delivers with extraction).
- The decisions.md:321 prose contradiction (skip-before-extraction vs injected-between-119-and-132) resolves trivially once the lane fact is known — fix by append-only addendum; the before/after-skip ordering debate was never the real risk. Also correct decisions.md:95's invisibility claim.
- Empty-after-strip content with images present is valid: adapters must not early-return on empty content when an image is attached (Discord `adapter.py:1570-1574` needs `and not file`).

---

## 2. Focus-area decisions

### Focus 1 — Marker transport channel: **DECISION: Option A (in-band marker only), hardened**

**Rationale:** The council's decisive argument is the correlation crux: a content-blind out-of-band sidecar cannot know which stashed ids belong to *this* final response — the parent may call `generate_chart` and deliberately not include the chart — so Option B either over-delivers (wrong images) or starves the one case it existed to fix; Option C's fallback inherits B's unsolvable suppression rule *and* the fallback lane (`dispatch_message` fallback, `dispatcher.py:189-198`) carries no `instance_id`, making a per-instance sidecar unreachable without a hot-path signature change. The in-band marker is self-correlating (position in content = intent to deliver), stateless, and preserves §http-api by construction (no-colon skip; no new content channel exists to violate the pin). Both council lanes agreed B is structurally unsolvable at the delivery seam. Five-axis comparison: §4.

**Hardening package (adopted):**
1. **Id-dedupe preserving first-occurrence order** at extraction (duplicate-marker failure mode, flagged by both lanes independently).
2. **Per-id resolution isolation** — one bad (hallucinated) id must not kill sibling images: `open` failure → WARN, continue.
3. **Optional strip-only near-miss sweeper** — a NEW, clearly-labeled secondary pattern (e.g. `^\s*<!--\s*ens-img:chart-render:[^>\n]{0,64}-->\s*$`) that strips but never extracts and never matches the locked form. Fixes the visible-junk-line cosmetic failure (comments render literally on all three clients). **The locked marker regex stays byte-stable** — no relaxation in v1 (dissent #2 rejected; see §8).
4. The planned mitigation stack (Phase A chart-skill paragraph + Phase C 21-agent "do not strip" guidance + malformed-marker tolerance + absent-marker→text-only) stays as-is.

**Merged failure-mode table (in-band marker, guided):**

| # | Failure | P | Detection | Outcome |
|---|---------|---|-----------|---------|
| 1 | Parent strips/omits marker | 3–8% | no match | Text-only Mermaid = pre-feature floor. Tolerable. |
| 2 | Mutated id / indentation / trailing space | 2–5% | regex miss, residue left | Visible junk line — sweeper fixes cosmetics |
| 3 | Duplicated marker | 1–3% | duplicate ids | Clean with dedupe (regression pin) |
| 4 | Hallucinated well-formed id | 1–4% | store lookup raises | WARN + per-id isolation; good ids still deliver |
| 5 | Code-fencing | low harm | `^…$` MULTILINE still matches an unindented fenced line (marker is its own line) | Indented fence only → same as #2 |
| 6 | Real stale id (earlier turn / other conversation) | 1–3% | none under A | Wrong/old image attached — **accepted residual**, ledgered; revisit if strip-rate >~10% post-Phase C or on user report |

### Focus 2 — Dispatcher extraction seam: **DECISION: see §1 — both seams, after skip + adapter lookup**

The rule in §1 is the canonical resolution of the plan-overview §Reconciliations #1 inconsistency. What each party sees:
- **API-origin (no-colon) instances:** marker verbatim in content; extraction never runs (return at the skip); `store.open_with_meta` never called. Pinned by `test_full_chain_api_caller_keeps_marker`, which must assert BOTH `adapter.send` NOT called AND a spy on `store.open_with_meta` NOT called, plus an e2e twin asserting the marker byte-for-byte in the HTTP response.
- **Progressive dispatch (`message_type="text"`):** MUST extract + strip + attach (this is the normal chat-final lane per §1) — the plan's "untouched as defense in depth" framing is inverted into "extract at both seams; once-only is structural".
- **Internal colon-sources (`internal_agent:*`, `system:*`):** return at the adapter lookup before extraction — no fetch, no strip.

### Focus 3 — Upload ownership: **DECISION: dispatcher resolves, adapter delivers, shared invariants; shared breaker + two mandatory guardrails**

**Rationale:** The dispatcher is the only component that sees content + source-type + the store seam — so it owns everything source-agnostic (extraction, byte fetch, MIME whitelist, `ImageAttachment` construction), giving a single fetch site, one `store-None` handling point, and keeping adapters free of manager coupling. Only the adapter knows its platform ladder (D4: source-agnostic dispatcher) — so it owns limits, API choice, retries, fallback. The text-floor and ordering invariants are shared because both sides must uphold them.

| Concern | Owner |
|---|---|
| Marker extraction + strip, byte fetch (`open_full` via `asyncio.to_thread`), MIME whitelist, provenance gate, `ImageAttachment` construction, `OutgoingMessage.images` at `:170`/`:243` | **Dispatcher** |
| Platform size ladders + constants, API choice/kwargs, retry/fallback ladder, capability detection, upload execution (INSIDE per-chat/per-channel locks) | **Adapter** |
| Text-floor invariant (text always delivered), ordering (marker order → attachment order; never reorder; never silently drop), logging discipline (`image_id[:8]` + size + content_type only; **bytes never in any log**) | **Shared** |

**Breaker policy — MANDATORY guardrails (unanimous):**
1. **Slack `missing_scope` must NOT trip any breaker.** Verified defect: `_call_slack_api` records failure on ANY `ok=false` (`slack/adapter.py:340-343`) — a workspace without `files:write` would open the *text* breaker after 5 chart sends. Fix: classify before `record_failure` + cached per-token capability flag → zero API calls + WARN-once-per-channel thereafter.
2. **Telegram multipart must classify 4xx as non-transient, no record** (mirror Discord `:1537-1547`) — systematically-rejected images exert zero breaker pressure; transport errors keep retry + record (a real outage should trip it, and text would fail too).

**Shared vs separate upload breaker:** shared + the two guardrails. Verified: `record_success` resets the consecutive counter (`circuit_breaker.py:20-33` / `:82-90`), so the cliff scenario (5 upload failures → 60s text silence) requires the text fallback to also fail 5 consecutive times — i.e. a real outage where the breaker *should* be open. Discord stays unchanged (upload 5xx counts, 429/4xx excluded — existing pins preserved). A separate per-adapter `_upload_circuit_breaker` is a **compatible belt-and-braces** implementer choice layered on top without contract change (dissent #3, recorded).

**Per-platform failure ladders:**
- **Discord** — chunk-1 (text + file) is an **atomic first unit**; on failure, one text-only retry of that unit (file never re-attempted on later chunks); success → continue chunks 2..N; retry-fail → `return False` (today's total-failure semantics). `file`/`files` are mutually exclusive in discord-py 2.7.1 — use `files=[…]` for N images.
- **Telegram** — multipart 4xx: no retry, fall to text; transport error: 3× exponential backoff then text; >50MB / MIME-miss: skip image, WARN, deliver text. Caption >1024: truncated caption on the photo **plus** full-text follow-up `sendMessage` (text floor).
- **Slack** — first `missing_scope`: set capability flag + WARN-once + text; flagged thereafter: zero API calls + text; other errors: per `_safe_api_call`, then text. Primary pattern: single `files_upload_v2(channel_id, filename, content, initial_comment)` — the upload-then-`postMessage(file=)` sketch pattern is suspect (undocumented kwarg + double-render risk).
- **All rungs:** WARN with `image_id[:8]` / size / content_type; multi-image = marker order preserved, **no silent drops** (every dropped image WARNs — `images[0]`-only behavior contradicts Phase D's own multi-chart e2e).

**Structural logging pin (merges security F1):** `ImageAttachment.bytes_b64 = field(repr=False)` + redacting `__repr__`; declare `OutgoingMessage.images` transport-only — never persisted, never logged whole.

**Doc-drift flags for verify-at-impl (D4):** discord.py 2.7.1 `File(fp, filename)` + `send(content=, file=/files=)` stable, 8MB non-boosted default stands; Telegram `sendPhoto` ≤10MB + w+h≤10000 + caption ≤1024, `sendDocument` ≤50MB, field names `photo`/`document`; slack-sdk 3.42.0 `files_upload_v2` accepts `content` as bytes, multiple files supported.

### Focus 4 — Render durability: **DECISION: hybrid install executor + 4-signal readiness probe + taxonomy-driven degradation**

1. **Install home/executor — HYBRID.** Skill home stays `agents/charter/skills-template/install-mermaid-cli.md` (charter-owned, D1); **ari/commissioner pre-warms as a deploy step** (mirrors install-opendesign's deploy-step execution); charter self-heals on cold-detect behind an advisory `flock` (10s timeout, `$HOME/.cache/charter/mermaid-install.lock`) — a multi-minute chromium download must not ride inside `generate_chart`'s invoke-and-wait budget, and concurrent charters must not race the install. Lock-contended → degrade now + log `install_in_progress_other`. Cold-detect mid-turn writes `$HOME/.cache/charter/mermaid-pending-install` (async queue marker) and degrades. **Inline install is allowed ONLY when** (a) `cold_misses_in_session < 2` AND (b) chromium partially present (resumable) AND (c) hard 60s cap. HONEST-STOP prereqs: `curl`, `git`, `jq`, `~/.nvm/nvm.sh` — any absent → `HARD_BLOCKER`, never auto-install.
2. **Warm/cold detection — 4-signal config-file probe; drop `command -v mmdc`.** Non-interactive bash never sources `~/.bashrc`, so `command -v` gives a *permanent false-cold* even when the install exists. The probe requires ALL of: (1) `~/.config/charter-mermaid-puppeteer.json` exists + valid JSON; (2) `mmdcPath` absolute + executable; (3) `puppeteerConfig.executablePath` absolute + executable **probed at probe time, not trusted from config-write time** (survives chromium moves); (4) pinned `mermaidCli` version matches. Probe lives in the install skill (`## READINESS_PROBE`) + an extracted `install-mermaid-cli.lib.sh` sourced by `workflow.md` Step 5 — single source of truth, fits Phase A's agent-prompt-only fence (pure bash + jq).
3. **Degraded path — taxonomy (charter render skill / §degradation-v2 append):** syntax errors keep the 3-attempt retry budget (syntax ONLY); render-side failures never retry. Cold → pre-warm expected / async queue; env-class mmdc errors (ENOENT/sandbox/EACCES) → one retry after sandbox check, else degrade; timeout 124 → kill, no retry; `TmpImageStoreFull` / store-not-initialized → degrade, no retry (cap won't free mid-session); other `image_save` errors → one retry, else degrade. Every rung: no marker, text-only Mermaid, log line.
4. **Version/drift + offline:** pins mermaid-cli@12 / Node 24 (frozen, no auto-upgrade) / puppeteer-managed chromium; partial installs re-enter the idempotent install (probe-failure = repair trigger); offline → `--prefer-offline` from npm cache, else `HARD_BLOCKER offline`; never fetch-and-execute from arbitrary URLs (tsc-typosquat hygiene).
5. **Retire `npx -y` (merges security F5):** current `workflow.md:109/:141` invoke `npx -y @mermaid-js/mermaid-cli` — unpinned remote fetch-and-execute on every render. After D1 install, invoke the installed global `mmdc` by absolute path only; worst case `npx --no-install`; delete the `npx -y` instruction when Phase A lands.
6. 🟢 **Store-capacity pre-check (optional):** before render-to-store, `image_list(feature="chart-render")` size sum > 80% of cap → skip persist, deliver text-only with `image_store_full` log (avoids a wasted render).

### Focus 5 — Security: **DECISION: no 🔴 findings; adopt pins F1/F2/F4/F5; re-open deferred item (c) narrowed; residuals accepted**

- **F1 (logging, 🟡):** bytes never in logs — structural: `field(repr=False)` + redacting `__repr__`; log `image_id[:8]` + size + content_type at dispatcher AND all three adapters (merged into Focus 3's shared invariants).
- **F2 (cross-namespace id confusion, 🟡):** resolution is feature-blind today — a forged/echoed marker referencing a *clipboard* or *designer* id would upload that image. Cheap dispatcher-side gate: resolve via `store.open_full(image_id)` (`tmp_image_store.py:480-494`) and **require `provenance.feature == "chart-render"`** before upload — mismatch → resolution failure → text fallback. Optional 24h freshness window (render→dispatch is seconds) kills stale replay. Severity bounded (id = 128-bit capability; never-minted ids fail clean at `TmpImageNotFound`), but the gate is ~5 lines.
- **F3 (unauth GET + 30-day retention, 🟡 noted-residual):** acceptable residual consistent with daemon-wide norms (assistant text persists indefinitely in PG; the PNG is a 30-day id-gated duplicate — strictly smaller exposure class). **Recommended amendment:** re-open deferred item (c) *narrowed to the chat path* — after a successful adapter upload (`send()` returned True, in whichever lane delivered), `store.delete(image_id)`; the GET window collapses from 30 days to zero for chat-delivered charts; API-origin keeps the 30-day GET per §http-api. Mutually-exclusive lane semantics make double-delete impossible.
- **F4 (mmdc sandboxing/DoS, 🟡):** `securityLevel` is overridable **from inside diagram text** (`%%{init}%%` directives / frontmatter) — pin `-c '{"securityLevel":"strict","htmlLabels":false}'` in the §capture invocation; pre-render sanitizer strips/rejects init/config directives + frontmatter securityLevel keys from the `.mmd`; wrap render in `ulimit -v` (~2GB) alongside `timeout 60` (wall-clock bound does not bound memory); sandbox policy: never root, `--no-sandbox` only if launch fails and say so in the log (accepted residual on single-user host). Rasterization-to-PNG itself neutralizes script/link vectors at the output stage.
- **F5 (`npx -y`, 🟡):** retired by Focus 4.5 above.
- **F7 (cosmetic):** amend decisions.md:95 rationale — comments render literally on Discord/Telegram/Slack; the near-miss sweeper (Focus 1.3) is the cosmetic fix.
- **F8/F9/F10/F11 (🟢):** credential scoping minimal + fail-soft (add Discord 403 WARN-once parity with Slack `missing_scope`); in-process fetch pin verified intact (no rogue HTTP path; `SourceRegistry.manager` is the single clean seam); fallback semantics benign; temp-file hygiene sound (extend the mktemp trap to the `.svg` artifact).

**Residual risks accepted:** unauth `/api/tmp_images` (daemon-wide norm; router header already pins it first-in-line for future auth); learned-id replay (capability parity with GET; shrunk by F2); `--no-sandbox` chromium if required; 30-day GET window for API-delivered charts; `image_id[:8]` in WARN logs.

### Focus 6 — Sequencing & instance reuse: **DECISION: A ∥ C → B → D CONFIRMED (with scope deltas)**

- **A → B dependency holds** (B consumes the marker contract; the contract itself is unchanged — LOCKED regex untouched; A's deliverables grow by the probe/install-skill detail and the §capture security pins).
- **A ∥ C shared-file coupling** (`agents/_prompt_system/innate-skills/chart/skill.md`): land as one coordinated commit or A-then-C; reviewer verifies `_BUSY_STRING` byte-pin (skill.md:74 / tests/test_chart_tools.py:296) and Wedged-Charter Recovery byte-identical — unchanged from plan.
- **B scope grows** (both-seam extraction, breaker guardrails, both construction sites, adapter empty-content fix) — still one restart + one promote; no dependency inversion.
- **D test matrix must be updated** (see amendments #21) — notably the inverted pin #6 and the real-astream-lane requirement for the full-chain e2e; the 23-pin audit list inherits the changes. Run the audit pre-baseline on clean `latest` BEFORE merge (plan risk #1) — unchanged.
- **Instance budget:** chains are sequential within each phase; A-dev ∥ C-dev = 2 concurrent + staggered reviewers/tester ≤ 3. Holds.

---

## 3. Plan amendments the implementers MUST apply

| # | Phase | Amendment |
|---|-------|-----------|
| 1 | B 🔴 | Extract at **both** `dispatch_message` AND `dispatch_completed`, last transform before `OutgoingMessage`, after no-colon skip + adapter lookup (§1 pin, verbatim). Populate `images` at `dispatcher.py:170` AND `:243`; never `registry.py:980`. |
| 2 | B | Id-dedupe preserving first-occurrence order at extraction. |
| 3 | B | Per-id resolution isolation (bad id → WARN, siblings deliver). |
| 4 | B | Slack: classify errors BEFORE `record_failure`; `missing_scope` → cached per-token capability flag, WARN-once-per-channel, zero API calls once flagged (fixes `slack/adapter.py:340-343` ok=false recording defect). |
| 5 | B | Telegram multipart: 4xx non-transient, no record (mirror Discord `:1537-1547`); transport errors 3× backoff then text. |
| 6 | B | Discord: chunk-1 text+file = atomic first unit; one text-only retry; file never re-attempted on later chunks; `files=[…]` for N (file/files mutually exclusive in 2.7.1). |
| 7 | B | Multi-image: marker order preserved, no silent drops, WARN-on-drop. |
| 8 | B | Telegram caption >1024 / Slack `initial_comment`: truncated on-image + full-text follow-up send (text floor). |
| 9 | B | Slack upload = single `files_uploadV2(channel_id, filename, content, initial_comment)`; NOT upload-then-`postMessage(file=)`. |
| 10 | B | Uploads execute INSIDE per-chat/per-channel locks (Telegram + Slack sketch correction — ordering hazard). |
| 11 | B | Empty-after-strip content valid when images attached (Discord `adapter.py:1570-1574` `and not file`). |
| 12 | B | `ImageAttachment.bytes_b64 = field(repr=False)` + redacting `__repr__`; logging contract: `image_id[:8]` + size + content_type only, dispatcher + all adapters. |
| 13 | B | Resolution via `store.open_full` + `provenance.feature == "chart-render"` gate (mismatch → text fallback); optional 24h freshness window. |
| 14 | A/B | Near-miss strip-only sweeper (secondary, clearly-labeled pattern; never extracts; LOCKED regex untouched). |
| 15 | A | §capture addendum: `-c '{"securityLevel":"strict","htmlLabels":false}'`; pre-render directive/frontmatter sanitizer; `ulimit -v` ~2GB + `timeout 60`; sandbox policy note (never root; `--no-sandbox` only on launch failure, logged). |
| 16 | A | Retire `npx -y` from `workflow.md:109/:141` — installed global `mmdc` by absolute path; worst case `npx --no-install`. |
| 17 | A | 4-signal READINESS_PROBE (config-file based) replaces `command -v mmdc` pre-flight; lives in install skill + `install-mermaid-cli.lib.sh`; `workflow.md` Step 5 sources the lib. |
| 18 | A | Hybrid executor: ari/commissioner deploy-step pre-warm + charter flock-guarded self-heal + `mermaid-pending-install` async queue + HONEST-STOP prereqs (curl/git/jq/nvm.sh) + inline-install gating (cold_misses<2 ∧ partial chromium ∧ 60s cap). One-line reminder in `.agents/shared/context.md`. |
| 19 | A | `rule.md`: Must = emit marker only after valid `image_save` result; Never = retry render-side failures (syntax-only 3-attempt budget preserved). Optional 🟢 store-capacity pre-check. |
| 20 | decisions.md | Append-only addenda (do NOT amend locked §marker): §phase-b addendum (both-seam rule; :321 prose fix; :95 invisibility correction; logging contract; provenance gate); §degradation-v2-toolchain (Focus 4 taxonomy). |
| 21 | D | Test matrix: `test_progressive_lane_extracts_and_strips` (replaces the marker-NOT-extracted acceptance); `test_progressive_then_completed_no_double_send`; `test_extraction_after_adapter_lookup` (internal_agent:* → no fetch); `test_duplicate_marker_dedup`; empty-content-with-images; API-keeps-marker must also spy `open_with_meta` NOT called + e2e byte-for-byte twin; foreign-feature id → text fallback (F2); full-chain Discord e2e must exercise the **real astream lane**, not a mocked dispatcher seam; 23-pin audit list updated for inverted pin #6. |
| 22 | B (or follow-up) | Re-open deferred item (c) narrowed to chat path: `store.delete(image_id)` after successful upload in the delivering lane; API path keeps 30-day GET. **Recommend ADOPT** (≈3 lines + 1 test). |

---

## 4. Approach comparison (Focus 1 — channel; council five-axis verdict)

| Axis | A — in-band marker | B — out-of-band sidecar | C — hybrid |
|------|--------------------|------------------------|------------|
| **Complexity** | **Lowest** — one regex, stateless | Highest — stash + turn-scoping + `dispatch_message` signature change + suppression logic | A + most of B's machinery |
| **Scalability** | **Stateless; store reads only for matched ids** | Per-instance stash growth needs TTL/eviction | B's fallback-path costs |
| **Maintainability** | **One pinned contract; failures observable in content** | Truth split across two channels | Two channels + precedence rules; most test surface |
| **Risk** | LLM-behavioral only (strip → graceful text; stale-id → wrong image, accepted + ledgered); dominant technical risk eliminated by the §1 lane fix | Systematic wrong-image; API callers lose PNG refs or pin violated | A's residuals + B's wrong-image tail at lower probability |
| **Cost** | **Minimal** | ~150–300 LOC + new seams | Slightly under B, well over A |
| **Recommendation** | **✅ ADOPT (hardened)** | Reject — correlation unsolvable at the delivery seam | Reject — inherits B's suppression problem; fallback lane carries no `instance_id` |

---

## 5. Risks

- 🔴 **Plan-as-written ships the feature dead for chat sources** (extraction on the wrong lane; §1). Mitigation: amendment #1 + the rewritten acceptance test + the real-astream-lane e2e (#21). This is the one amendment that is non-negotiable.
- 🟡 Slack capability-miss poisoning the text breaker (verified defect) — mitigation #4.
- 🟡 `securityLevel` override from inside diagram text + unbounded render memory — mitigation #15.
- 🟡 Permanent false-cold toolchain from `command -v` probe in non-interactive bash (silent text-only forever) — mitigation #17.
- 🟡 Stale-real-id wrong-image delivery (accepted residual, 1–3%) — ledgered; mint-ledger recorded as sound design debt needing an instance-carrying seam first.
- 🟢 `npx -y` fetch-and-execute adjacency — mitigation #16.
- 🟢 Payload-in-logs — structural redaction #12.
- 🟢 bytes/latency of base64 transport (~67–400KB encoded) — negligible; logging ruled out anyway.

## 6. Decisions pending (leader/user)

1. **Amendment #22 (delete-after-chat-upload):** adopt in Phase B (recommended) or keep deferred. Trade-off: 30-day unauth GET window vs ~3 LOC + 1 test.
2. **Accepted residual — stale-id wrong image:** confirm acceptance with ledger + revisit trigger (strip-rate >10% post-Phase C or user report).
3. **ari pre-warm responsibility (amendment #18):** requires the deploy-step expectation to live in ari's commissioning workflow / `.agents/shared/context.md` — an agent-prompt touch outside this commission's file list; leader to ratify the ownership.

## 7. Open questions / unverified (verify at implementation)

- Telegram limiter actual rate: explorer cited 30 msg/s token bucket; leader context cited 30msg/30s — does not change the ownership decision; verify at impl.
- `TmpImageStoreFull` cap configurability (1 GiB default; not pinned in decisions.md §D3) — affects the optional capacity pre-check only.
- `nvm which 24` vs `nvm-exec` idiom ambiguity (decisions.md:18 vs install-opendesign.md:859) — probe should accept either.
- Inline-install 60s cap is heuristic — log actual install time on first success; re-tune (`pinned.observedInstallSec`).
- mmdc 12 render vs frontend mermaid (ngx-markdown) version drift — cosmetic parity only.

## 8. Gaps & dissent

**Gaps: none** — 3/3 reports complete, all skills confirmed loaded, all evidence-cited.

**Council dissent (coding lane, preserved; adjudication stated):**
1. *dispatch_completed-only extraction* — overruled: contradicted by the twice-verified caller-trace, by the lane's own once-only test design, and by its own Q1 premise; would ship the feature dead-on-arrival. Its strongest sub-point (API-origin keeps marker verbatim) is adopted in the rule.
2. *Mint ledger + regex relaxation (C-lite)* — rejected for v1 (fallback-lane check site can't close the normal case; delivery seam lacks `instance_id`; relaxation conflicts with the LOCKED pin). Recorded as sound design debt if stale-id protection is later wanted.
3. *Separate per-adapter upload breaker* — not primary (verified reset semantics + guardrails cover the poison cases at lower complexity); compatible belt-and-braces implementer choice.
4. *Telegram/Slack first-image-only v1* — rejected: contradicts Phase D's multi-chart e2e; sequential in-order sends with WARN-on-drop are the v1 bar.

Process note: the coding lane re-affirmed Round-0 positions without engaging the Round-1 counter-evidence; the Q2 ruling is therefore weighted by verification depth (agentic lane re-verified with citations) and internal corroboration (explorer report + the lane's own test design). Confidence: **normal multi-source**.
