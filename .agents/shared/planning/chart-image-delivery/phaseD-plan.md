# Phase D Plan — chart-image-delivery (cross-cutting hardening + version/docs/release)

**Date:** 2026-10-04 (revised R2)
**Author:** planner[v2] via plan-creation worker
**Status:** Draft R3 — Revision loop 3 FINAL (TrueAuto: implementation dispatch follows)
**Feature commission:** `chart-image-delivery` (D1 self-install / D2 local render / D3 tmp_images substrate / D4 per-platform native delivery)
**Branch base:** `latest` @ `cf8efbeff9d932a6d01d7cbb2411836057e7b099` (verified in shared meta-kv)
**Companion artifact:** `.agents/shared/planning/chart-image-delivery/decisions.md` (Phase A & Phase B locked contracts; **APPEND** §phase-d-* sections)
**Output dir:** `.agents/shared/planning/chart-image-delivery/`
**Execution order:** Phase D lands **AFTER** Phases A, B, C are merged to `latest`. Plan written now, executed last.

> **Precedence:** `architecture-recommendation.md` §3 governs over phase-plan prose on conflict.

> **Phase D** of the `chart-image-delivery` commission. Phase A authored the marker contract + render-at-validation capture (agent-prompt only). Phase B owns the chat-dispatcher extraction + per-adapter native upload (daemon code, restart+promote). Phase C owns the agent-guidance cross-reference in 20 chart-capable agents (agent-prompt only; reviewer-corrected from the prior 21-agent count). Phase D **consolidates** the three lanes for release: cross-cutting regression pin audit, full user-story end-to-end test (mock-adapter + real-astream-lane + manual real-platform smoke), test-coverage gap analysis + bridging tests, version bump + CHANGELOG entry, docs surface verification, the final restart/promote matrix, and the implementation release report (with the Slack `files:write` USER ACTION ITEM + adopted/deferred-items ledger).

---

## Objective

After Phases A, B, and C are merged to `latest`, Phase D verifies the **entire chart-image-delivery chain** end-to-end, hardens every cross-cutting regression pin (the do-not-break list across all three phases), bumps the repo version per convention, drafts the CHANGELOG entry, and produces the final implementation release report so the feature is release-ready. The primary acceptance is the **original user-story sentence**: *A Discord user asks the agent for a workflow chart → the agent calls `generate_chart()` → the user receives the rendered PNG as a Discord attachment with the (marker-stripped) explanation text, NOT a wall of Mermaid code.*

**Testable sentence:** *Run the consolidated audit script + the full Phase A/B/C test suite + the new Phase D mock-adapter e2e + the manual Discord real-platform smoke; ALL pass; version is bumped in `daemon/__init__.py` + `pyproject.toml`; CHANGELOG has a Keep-a-Changelog entry under `[Unreleased]`; the release report enumerates the shipped surface, the test evidence, the Slack `files:write` USER ACTION ITEM, the restart/promote matrix, and the deferred-items ledger.*

---

## Scope

### In Scope

1. **Cross-cutting regression pin audit** — one executable checklist (pytest + grep) covering the full do-not-break list from Phase A (`_BUSY_STRING` × 3 call sites — `_BUSY_MSG` const at `chart_tools.py:55` + the skill.md narrative + the test pin; `_PAUSED_STRING` × 3 sites — chart_tools.py:222 + skill.md:74 + tests/test_chart_tools.py:298; marker regex byte-stability; legacy error contract), Phase B (no-colon skip rules, BOTH-seam extraction per arch-rec §1, API-source-keep-marker with resolution-accessor spy (`open_full`/`open_with_meta` — assert ZERO store calls), `images` field defaulted at both `:170`/`:243` constructor sites, chunk ordering, breaker guardrails), and Phase C (`_BUSY_STRING`/`_PAUSED_STRING` byte-pin preserved, no `.md` path tokens, **20**-agent cardinal coverage, Ari pre-warm reminder in `.agents/shared/context.md` + `agents/ari/workflow.md`). **24 pins total**, split into PRESERVATION (pre-existing invariants) and FEATURE (post-merge state); see Tasks D.0 + Components §2.
2. **Full user-story end-to-end** — Phase D's own mock-source e2e test (`tests/test_chart_image_delivery_e2e.py`, NEW) extends Phase B's `tests/test_outbound_image_delivery.py` to assert the **entire chain** (charter stub → parent stub → dispatcher → adapter) with a real (tiny) PNG fixture in a real `TmpImageStore`. Plus a manual real-platform smoke procedure for Discord (always-on); Slack gated on operator-granted `files:write` scope; Telegram gated on a real bot token.
3. **Test-coverage gap analysis + bridging tests** — identify and cover the cases the per-phase suites miss when composed (full-chain integration, multi-chart mixed with text, concurrent chart + text ordering, image_get mid-chain failure, dispatcher unit-of-work under per-user locks, mixed-source conversation). The bridging tests live in `tests/test_chart_image_delivery_e2e.py`.
4. **Version bump + CHANGELOG entry** — version `0.16.12 → 0.16.13` (semver minor: new feature on the existing minor line) in BOTH `daemon/__init__.py` and `pyproject.toml`; CHANGELOG entry under `[Unreleased]` per the repo's Keep-a-Changelog convention (verified via spot-check of existing entries).
5. **Docs consolidation** — verify whether any **other** docs surface needs a chart-image-delivery mention beyond Phase B's `docs/sources/slack-setup.md` row. **Verified spot-check:** `docs/setup.md`, `docs/sources/discord-setup.md`, `docs/pluggable-sources-architecture.md`, `docs/architecture.md`, `docs/agents.md` are the candidate surfaces; only update if a real mention is needed (no architecture/assembly impact → likely zero changes outside `slack-setup.md`). Plan is conditional on the spot-check.
6. **Restart/promote matrix consolidation** — Phase A = no restart (agent-prompt); Phase B = restart + promote (daemon code); Phase C = no restart (agent-prompt); docs/tests = n/a. The final implementation report MUST state this matrix explicitly.
7. **Release report requirements** — template for the implementing agent's final report. Includes: what shipped (per-phase deliverables), test evidence (counts green per suite), the **Slack `files:write` USER ACTION ITEM** with operator steps, the restart/promote matrix, rollback notes (Phase B is the risky surface), and the **deferred-items ledger**.
8. **Implementation dispatch shape** — Phase D's implementer is the **same tester context** that ran A/B/C verification (consolidation benefits from accumulated context). One reviewer for the release report.
9. **Sequencing** — Phase D lands AFTER A+B+C implementation merges; the plan is written now, executed last.

### Out of Scope

- **Any new feature work** — Phase D is consolidation only. New ideas (Telegram 4096-char text chunking, `source_hint` injection, real-platform e2e credentials, HTTP-API structured image-ref response) go to the deferred-items ledger with the responsible future-ticket call-out. The one item that LOOKED new but is ADOPTED in R2: `store.delete(image_id)` after successful chat delivery (arch-rec §3 amendment #22 / `decisions.md` §phase-b-r2-addendum-9) — moved from the deferred ledger to the adopted-ledger row in the release report §6.
- **Adapter upload code changes** — `daemon/sources/adapters/{discord,slack,telegram}/...` is Phase B's sealed surface. Phase D does NOT touch.
- **Agent prompt edits** — `agents/_prompt_system/innate-skills/chart/skill.md` and the **20** chart-capable agent files are Phase C's sealed surface. Phase D's audit SCRIPT runs against them.
- **Charter workflow / install-skill** — `agents/charter/*` is sealed by Phase A. Phase D does NOT touch.
- **CHANGELOG entry for any other feature** — only chart-image-delivery goes into this `[Unreleased]` slice. Other in-flight features land their own CHANGELOG entries via their own Phase D (or equivalent).
- **Active resolution of deferred items** — Phase D records the deferred-items ledger; it does NOT solve them.

### Files Touched (exact list)

| File | Change class | Restart? |
|------|--------------|----------|
| `tests/test_chart_image_delivery_e2e.py` (NEW) | Full-chain mock e2e + degraded-path e2e + multi-chart e2e + concurrent ordering | n/a (test reload) |
| `tests/test_chart_image_delivery_audit.py` (NEW) | Cross-cutting regression-pin audit (pytest-runnable) | n/a |
| `tools/audit-chart-image-delivery.sh` (NEW) | Standalone audit script (executable before merge) | n/a |
| `docs/changelog-pending/chart-image-delivery.md` (NEW) | Draft CHANGELOG section (slice into CHANGELOG.md at release) | n/a |
| `CHANGELOG.md` | One bullet-block added under `[Unreleased]` at release cut | n/a |
| `daemon/__init__.py` | `__version__ = "0.16.12"` → `__version__ = "0.16.13"` | yes (release cut) |
| `pyproject.toml` | `version = "0.16.12"` → `version = "0.16.13"` | yes (release cut) |
| `.agents/shared/planning/chart-image-delivery/release-report-template.md` (NEW) | Template for the implementation report | n/a |
| `.agents/shared/planning/chart-image-delivery/release-report.md` (NEW — the FILLED report; iter-003 blocking #6) | Committed release report: test evidence, smoke evidence, sign-off | n/a |
| `.agents/shared/planning/chart-image-delivery/decisions.md` | APPEND §phase-d-* sections (test matrix consolidation, version bump decision, release-report template, deferred-items ledger) | n/a |

**NO `agents/`, NO `agents/_prompt_system/`, NO `daemon/sources/adapters/`, NO `daemon/tools/chart_tools.py`, NO migration, NO new HTTP route, NO new daemon service.**

---

## Components (exact files + sections)

### 1. `tests/test_chart_image_delivery_e2e.py` (NEW) — full-chain mock e2e + bridging tests

This file consolidates Phase D's end-to-end coverage and bridges the cases the per-phase suites miss when composed. **Eight** test groups (R8 added Group 7 — real-astream-lane e2e; R1 added the amendment #21 test list inside Groups 1/2/4); each is a real pytest test, not a skip-stub.

**Group 1 — full-chain happy path (the user story, dispatch_completed unit-layer):**
- `test_full_chain_discord_user_story` — pre-populate a real `TmpImageStore` with a 1×1 PNG (89 bytes, fixture); stub a charter that returns a fixed Mermaid block + valid marker; stub a parent agent that forwards the chart result to a Discord-source `OutgoingMessage`; run `dispatcher.dispatch_completed(source_id="mock-source", ...)`; assert `sent_messages[-1].images` is `[[ImageAttachment(... image_id, content_type="image/png", filename, bytes_b64)]]` and `sent_messages[-1].content` is marker-free.
- `test_full_chain_api_caller_keeps_marker` — same chain but `source_id="api"` (no colon) → assert NO dispatch happens AND `store.open_with_meta` was NEVER called (per arch-rec §3 amendment #21 — pin must assert BOTH `adapter.send` skip AND `open_with_meta` skip), plus an e2e twin asserting the marker byte-for-byte in the HTTP response.
- `test_progressive_lane_extracts_and_strips` (R1 amendment #21) — drives `dispatcher.dispatch_message(source_id="mock-source", ...)` directly; assert the same image attaches (this is the NORMAL chat-source delivery lane per arch-rec §1, not "defense in depth"). **Replaces the prior "marker NOT extracted in dispatch_message" pin (now inverted to pin #6 in the audit catalog).**
- `test_progressive_then_completed_no_double_send` (R1 amendment #21) — fire BOTH seams in sequence against the same source; assert `_progressive_sent_sources` guard keeps exact semantics (one `OutgoingMessage` per lane per message; once-only delivery; bytes-b64 count == 1 in `sent_messages`).
- `test_extraction_after_adapter_lookup` (R1 amendment #21) — `source_id="internal_agent:foo"`; assert returns at the adapter lookup BEFORE extraction (no `open_with_meta` call, no marker strip, content byte-stable).
- `test_duplicate_marker_dedup` (R1 amendment #21) — content with the same marker twice; assert dedup preserves first-occurrence order; single `ImageAttachment` in `sent_messages[-1].images`.
- `empty-content-with-images Discord guard` (R1 amendment #11) — content stripped to empty + image attached; drive `DiscordAdapter.send()`; assert the message is NOT early-returned; file attached.

**Group 2 — degraded path (image_get fails mid-chain):**
- `test_image_get_raises_marker_stripped_text_delivered` — pre-populate the store, then `tmp_image_store.open_full` raises `TmpImageNotFound` (delete the file between save and dispatch); assert `sent_messages[-1].images is None`, `sent_messages[-1].content` is marker-free, and a WARN log was emitted (per Phase B §phase-b-degradation).
- `test_image_get_returns_wrong_content_type_text_delivered` — store has the PNG but the stored `content_type` is not in `CHART_IMAGE_MIME_WHITELIST` (set to `"image/svg+xml"` for the test); assert text fallback + WARN log.
- `test_image_get_provenance_feature_mismatch_text_delivered` (R1 amendment #13 / F2) — store row has `provenance.feature="designer-output"` (not `"chart-render"`); assert the dispatcher drops the image, WARN, text fallback. Per-id isolation preserved (a sibling valid marker in the same content still uploads).
- `test_image_get_optional_24h_freshness_window` (R1 amendment #13 optional) — store row older than 24h; assert text fallback. Phase D treats this as optional-featured; flag in test parametrize so CI skips if the window-knob is not implemented.

**Group 3 — multi-chart + text ordering under per-user locks:**
- `test_multiple_charts_in_one_conversation` — single chat session, three chart requests in sequence; assert each produces its own attachment; markers correctly scoped (no cross-attribution); marker order preserved in dispatch.
- `test_concurrent_chart_and_text_messages_preserve_order` — interleaved: text, chart, text, chart (per-user LRU lock preserved per Phase B §phase-b-degradation lock discipline + amendment #10 lock-inside-locks); assert final `sent_messages` order is text, chart, text, chart (no reordering, no dropped messages).

**Group 4 — multi-source isolation:**
- `test_different_chat_sources_independent` — Discord and Slack sources interleaved; assert each source's `sent_messages` only carries its own attachments (no cross-leak via shared `OutgoingMessage.images`).
- `test_marker_only_in_chat_dispatch_path` — concurrent dispatch from `api` and a chat source in the same session; the chat-source dispatch extracts the marker; the API-source dispatch keeps it.
- `test_foreign_feature_marker_text_fallback` (R1 amendment #13 / F2) — content carries a well-formed marker whose `image_id` resolves to a non-chart-render feature (clipboard / designer); assert text fallback + WARN; no upload.

**Group 5 — installer/skill hygiene:**
- `test_install_mermaid_cli_skill_frontmatter_version_present` — assert `agents/charter/skills-template/install-mermaid-cli.md` has `version: 1.0.0` (or whatever Phase A ships) in the frontmatter; no missing-version regression.
- `test_install_mermaid_cli_4_signal_readiness_probe_in_skill` (R1 amendment #17) — assert the install skill contains a `READINESS_PROBE` section referencing all 4 signals (config-file existence, mmdcPath executable, puppeteer chromium probed at probe time, version match) — content-addressable, no line numbers.

**Group 6 — out-of-scope guard (Phase D itself):**
- `test_phase_d_does_not_modify_sealed_artifacts` — **baseline the expected SHA256s against the A/B/C MERGE STATE via `git show <merge-commit>:<path> | sha256sum`** (recorded fixture at test-authoring time; NOT a setup-time snapshot — same-run snapshots only catch same-run mutation and false-green across Phase D's commits between runs, iter-003 blocking #5). The test asserts the CURRENT worktree SHAs of every sealed file (Phase A's 10 files, Phase B's 7 daemon files, Phase C's 20 agent files + the chart skill) equal the git-derived baselines; re-run at Task 18 catches cross-commit drift.

**Group 7 — REAL-ASTREAM-LANE end-to-end (R8 + arch-rec §3 amendment #21):**

This is the test that exercises the original user-story delivery path end-to-end. Mock-dispatcher tests hide the lane defect that the original spec pinned; this group drives the REAL astream lane so the user story (Discord user asks → rendered PNG in channel) is actually exercised, not just stubbed.

- `test_astream_discord_user_receives_png` — spawn a real `InstanceMessagingService` against the staging `dispatch_source` path (`daemon/services/instance_messaging.py:4506-4566` + `:4815-4834`); the service receives a synthetic `IncomingMessage` from a Discord source that asks for a workflow chart; the agent calls `generate_chart()` (real tool path with a stubbed charter); assert that the astream output emits a progressive dispatch (not the `dispatch_completed` fallback) AND that the eventual `adapter.send` call carries the rendered PNG with the marker stripped. Asserts the lane path (`instance_messaging.py:3102` `dispatch_source` stamping + the post-loop or in-loop dispatch) is what delivers the message in production — not `dispatch_completed`.
- `test_astream_progressive_lane_marker_extracted` — same harness; assert the marker is stripped from the astream-emitted text AND the eventual `sent_messages[-1].content` is marker-free AND `sent_messages[-1].images` is populated.
- `test_astream_progressive_lane_failure_routes_to_completed` — astream progressive fails (mock adapter-False); assert the message is delivered via `dispatch_completed` (not silently dropped); the dispatched message still carries the image (amendment #1, `_progressive_sent_sources` guard `:265-266` semantics).
- `test_astream_internal_agent_source_no_extract` — same harness with `source_id="internal_agent:foo"`; assert no extraction happens at the astream progressive seam (return at adapter lookup); content byte-stable; `open_with_meta` NOT called.

**Group 8 — store.delete-after-upload (R2 adopted, formerly deferred #3):**
- `test_chat_delivery_calls_store_delete_once` — happy-path chat delivery; assert `store.delete(image_id)` was called exactly once; assert `OpenWithMeta` was called BEFORE `delete`.
- `test_chat_delivery_upload_failure_does_not_delete` — image upload raises; assert `store.delete` was NOT called (only successful deliveries delete).
- `test_api_source_does_not_delete` — `source_id="api"` (no colon); assert `store.delete` was NOT called (30-day GET window preserved per §http-api).
- `test_both_lanes_only_one_delete` — both `dispatch_message` and `dispatch_completed` paths fire on the same source; assert `delete.call_count == 1` (mutually-exclusive lanes; once-only structural).

**Test fixture strategy:** a real `TmpImageStore` instance constructed via the existing `tests/e2e/mock_source_server.py` patterns; a real `SourceRegistry` with `Manager.tmp_image_store` injected. The PNG is a hand-crafted 1×1 RGBA (verified via `file out.png` reports `PNG image data`). The marker is hard-coded with a fresh uuid4 hex per test. Image cleanup in a pytest fixture's `teardown` deletes both blob and sidecar (per `daemon/services/tmp_image_cleanup_service.py:37-42` pair semantics). Mock-source registration shape (R4/N5, now explicit): construct `MockSourceAdapter` and register it on the SAME `SourceRegistry` instance the dispatcher under test resolves through (mirror `tests/e2e/mock_source_server.py` wiring), with `manager.tmp_image_store` injected — no longer implicit. For Group 7 (real-astream), use the existing `tests/e2e/` daemon-harness infrastructure (per `tests/e2e/test_e2e_workflows.py` patterns) — NOT a hand-rolled harness.

### 2. `tests/test_chart_image_delivery_audit.py` (NEW) — regression-pin audit (pytest-runnable)

A single pytest module whose tests are themselves the audit. Each test asserts one pin. The module is also runnable standalone via `tools/audit-chart-image-delivery.sh`.

**Pin catalog = 24 pins, content-addressable (not line numbers) — split into PRESERVATION (pre-existing invariants already green on clean `latest`) and FEATURE (assert post-merge state).** Pin #6 is INVERTED per arch-rec §3 amendment #21: extraction must run at BOTH seams (`dispatch_message` AND `dispatch_completed`); the previously-pinned "marker NOT extracted in dispatch_message" would have locked the marker-leak defect.

| # | Pin | Class | Source of truth |
|---|-----|-------|-----------------|
| 1 | `_BUSY_STRING` text byte-stable at `daemon/tools/chart_tools.py:_BUSY_MSG` + `tests/test_chart_tools.py:_BUSY_STRING` + `agents/_prompt_system/innate-skills/chart/skill.md` (3 call sites under the busy-body section) | PRESERVATION | content-grep for `"Error: Charter busy; pass fresh=True for parallel charts."` |
| 2 | `_PAUSED_STRING` byte-stable — **the source literal is SPLIT across `chart_tools.py:222-223`** (`"Error: Charter is paused; resume it or pass fresh=True for a " + "new charter."` — verified in the worktree): grep the TWO FRAGMENTS separately in chart_tools (`:222` fragment `Error: Charter is paused; resume it or pass fresh=True for a` + `:223` fragment `new charter.`); grep the FULL concatenated value in `tests/test_chart_tools.py` (`_PAUSED_STRING`) + `skill.md` paused-callout prose | PRESERVATION | two-fragment grep in chart_tools + full-string `grep -F` in tests + skill.md — a single full-string grep -F on chart_tools.py is GUARANTEED 0 hits (the split) [R5, iter-003 blocking #3] |
| 3 | `<!-- ens-img:chart-render:[a-f0-9]{32} -->` regex byte-stable in `decisions.md` §marker (the LOCKED form; NOT the near-miss sweeper pattern from §phase-b-r2-addendum-14) | PRESERVATION | content-grep for the regex string literal in `decisions.md` |
| 4 | `chart_tools.py:525` verbatim passthrough (`return result`) unchanged — anchor on the UNIQUE ADJACENCY: the `return result` at `:525` immediately precedes the `generate_chart._full_doc_ = ` assignment at `:527` (verified in the worktree; `verify_text_in_code` does NOT exist in the repo) | PRESERVATION | `sed -n '525p' daemon/tools/chart_tools.py` matches `return result` AND the next non-blank line matches `generate_chart._full_doc_` (merge-stable: neither Phase A nor Phase B touches chart_tools.py) [R5, iter-003 blocking #4] |
| 5 | `daemon/sources/dispatcher.py` no-colon skip runs BEFORE `extract_chart_images` (both seams) | PRESERVATION (the skip is pre-existing) | content-grep for the no-colon check + the `if ":" not in source_id` short-circuit |
| 6 | **INVERTED** — `extract_chart_images` runs at BOTH seams (`dispatch_message` AND `dispatch_completed`) as the LAST content transformation before `OutgoingMessage` construction — AFTER the no-colon skip, AFTER source validation, AFTER the internal-report skip, AFTER the adapter lookup. `OutgoingMessage.images` populated at `dispatcher.py:170` AND `:243`; NEVER at `registry.py:980` | FEATURE (post-merge) | grep for `extract_chart_images` call sites at both `dispatch_message` and `dispatch_completed` functions; assert `images=...` populated at both construction sites |
| 7 | `OutgoingMessage.images` field defaults to `None` in `daemon/sources/base.py` (`ImageAttachment` dataclass exists) | FEATURE | content-grep for `images: list[ImageAttachment] \| None = None` |
| 8 | Discord `_send_single_chunk` accepts `file=None` kwarg (chunk-1 atomic unit on success; file never re-attempted on later chunks; `files=[…]` for N>1) | FEATURE | grep for the `file` kwarg + the `files=` switch in chunk-1 logic |
| 9 | Discord 2000-char chunking preserved | PRESERVATION | content-grep for the 2000-char chunker in discord adapter |
| 10 | Telegram `_api_call_multipart` 3-retry (transport error) + 4xx-non-transient classification (no breaker record); circuit-breaker | FEATURE (new multipart code; the retry discipline mirrors the existing `_api_call` but the classifier is post-merge) | content-grep for the 4xx classifier + the retry loop |
| 11 | Telegram `sendPhoto` (≤10 MB) / `sendDocument` (≤50 MB) ladder | FEATURE | content-grep for the size-threshold branch |
| 12 | Slack single-call `files_upload_v2(channel_id, filename, content, initial_comment)` (NOT upload-then-postMessage(file=)); capability flag in `self._slack_capability_flags`; classify `missing_scope` BEFORE `record_failure`; WARN-once-per-channel | FEATURE | content-grep for `files_upload_v2` + `SlackCapabilityError` + the `_slack_capability_flags` set |
| 13 | Slack `BLOCKS_CONTENT_THRESHOLD` (400 chars) preserved | PRESERVATION | content-grep for the threshold constant |
| 14 | `daemon/constants.py` exports `DISCORD_FILE_MAX_BYTES=8MB`, `TELEGRAM_PHOTO_MAX_BYTES=10MB`, `TELEGRAM_DOCUMENT_MAX_BYTES=50MB`, `SLACK_FILE_MAX_BYTES=1GB`, `CHART_IMAGE_MIME_WHITELIST` (4 MIMEs) | FEATURE | content-grep for the 4 constants + the frozenset |
| 15 | `agents/charter/meta.json` `tools.allow` contains `"image"` | FEATURE | content-grep for `"image"` in tools.allow array |
| 16 | `agents/charter/meta.json` `version` is `1.2.0` | FEATURE | content-grep for `"version": "1.2.0"` |
| 17 | **20** chart-capable agents reference "Chat Delivery" in canonical home (R2 corrected; verified Phase C list — charter itself NOT in this set: approver, approver[v2], architect, ari, coder, developer, developer[v2], devops, doc-writer, governor, leader, maintenancer, planner, planner[v2], project-manager, reviewer, reviewer[v2], tidier, tidier[v2], wanderer) | FEATURE | grep `agents/<name>/*.md` (one of rule/soul/tools_note/workflow) for "Chat Delivery" in each of the 20 |
| 18 | No `.md` path tokens introduced in `agents/*/{rule,soul,tools_note,workflow}.md` new content | FEATURE | content-grep for `.md` regex in the Phase C diff |
| 19 | `_BUSY_STRING` at skill.md call site byte-identical to `tests/test_chart_tools.py:_BUSY_STRING` | FEATURE | content-grep; byte-equality assertion |
| 20 | `agents/_prompt_system/innate-skills/chart/skill.md` "Wedged-Charter Recovery" section unchanged | FEATURE | content-grep for the section heading + body byte-equality |
| 21 | `docs/sources/slack-setup.md` scope manifest + scopes table contain `files:write` (YAML manifest fenced `:17-61`; scopes table `:69-82` per §phase-b-r2-addendum-15) | FEATURE | content-grep for `files:write` in both YAML manifest + scopes table |
| 22 | `daemon/manager.py` `tmp_image_store` property still public | PRESERVATION | content-grep for the `@property tmp_image_store` accessor |
| 23 | `tests/e2e/mock_source_server.py` `MockSourceAdapter.send()` captures the whole `OutgoingMessage` (no field-stripping) | PRESERVATION | content-grep for `self.sent_messages.append(message)` |
| 24 | **Ari pre-warm reminder present post-merge**: a one-line note in `.agents/shared/context.md` AND a commissioning-workflow note in `agents/ari/workflow.md` (content-addressable grep, NOT line numbers; per arch-rec §3 amendment #18 + §6 pending #3 ratified) | FEATURE | content-grep for the deploy-step pre-warm language in both files |

**Pin classes** (per R5 fix):

- **PRESERVATION** (pins 1, 2, 3, 4, 5, 9, 13, 22, 23 = 9 pins — R3: #10 moved wholly to FEATURE, #13 added here; 9+15=24) — pre-existing invariants that already hold on clean `latest` BEFORE any feature merge. Run them in the new **D.0 pre-baseline gate task** (see Tasks §D.0) on clean `latest` before A/B/C merge; if any fail, escalate as pre-existing breakage (open a fix ticket), NOT as feature regression.
- **FEATURE** (pins 6, 7, 8, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 24 = 15 pins) — assert post-merge state. These CANNOT pass on pre-merge `latest`; they are the gate at release cut.

Each pin is a `def test_pin_NN_*():` function. Failure stops the run; CI gate at "all 24 pass" plus the full Phase A/B/C suites.

### 3. `tools/audit-chart-image-delivery.sh` (NEW) — standalone audit script

A bash script that runs the same 24 pins (PRESERVATION + FEATURE) via grep/pytest invocations, with a coloured pass/fail summary grouped by pin class. Exit code 0 iff all pass. Executable by CI before merge AND by operators post-deploy. Pattern source: `scripts/upgrade/ledger_check.py` (per ADR-021 N=3 cycle discipline). Located at `tools/audit-chart-image-delivery.sh` — does NOT touch `scripts/upgrade/` (which is the live-rung gate, different concern).

**The script honours `--class preservation|feature|all` (default `all`)** so the pre-baseline D.0 task can run only the PRESERVATION subset on clean `latest` (per R5). It also accepts `--release-pin FILES…` (deferred-pin selector) for the OQ4 backstop case.

### 4. `docs/changelog-pending/chart-image-delivery.md` (NEW) — draft CHANGELOG section

A standalone file under `docs/changelog-pending/` containing the EXACT text to be sliced into `CHANGELOG.md` `[Unreleased]` at release cut. Keep a Changelog format (verified at `CHANGELOG.md:1-5` — "The format is based on Keep a Changelog"). Single `### Added` block with one feature entry:

```markdown
- **Chart-image delivery** (`feature/chart-image-delivery`, merge `TBD`). Rendered Mermaid diagrams are now delivered as native image attachments in chat-source dispatches (Discord attachments, Telegram `sendPhoto`/`sendDocument`, Slack `files.uploadV2`) instead of Mermaid code blocks. Charter (the Mermaid agent) renders to PNG at validation time and emits a source-agnostic `<!-- ens-img:chart-render:<id> -->` reference marker; the chat-source dispatcher extracts the marker, resolves the PNG via `TmpImageStore.open_with_meta`, and uploads via the per-adapter native API. HTTP-API callers see the marker verbatim and may fetch the PNG via `GET /api/tmp_images/<id>`. Internal transport (marker + tmp_images substrate) is unchanged; degraded delivery is text-only with the Mermaid block intact. **Operator action required for Slack delivery:** grant the `files:write` OAuth scope in your Slack app config and reinstall the app; until granted, Slack text-only delivery with WARN-once log per channel. See `docs/sources/slack-setup.md` scope table and `.agents/shared/planning/chart-image-delivery/release-report.md` for the full operator runbook.
```

The release cut copies this block into `CHANGELOG.md` under `[Unreleased]` — keeping the change isolated to release-cut time (not Phase D's planning/execution window).

### 5. `daemon/__init__.py` + `pyproject.toml` — version bump

Both files currently at `0.16.12` (verified: `daemon/__init__.py:3` `__version__ = "0.16.12"` and `pyproject.toml:3` `version = "0.16.12"`). Bump to `0.16.13`. Rationale: minor bump per semver (new feature on the existing minor line; no breaking change). The bump happens at release cut, NOT in the Phase D planning/execution window — Phase D plans the bump, the release commit performs it. (If the release process requires the bump in the same commit as the feature merge, Phase D's implementer may flip the version at the end of the merge window.)

### 6. `.agents/shared/planning/chart-image-delivery/release-report-template.md` (NEW)

The template the Phase D implementer fills in for the final release report. Sections (each enumerated with the expected content):

1. **What shipped** — enumerate per-phase: Phase A (charter + install skill + chart skill wording), Phase B (dispatcher + 3 adapters + constants + slack-setup.md), Phase C (chart skill section + 20 cardinal references). File lists verbatim.
2. **Test evidence** — per-suite counts green: `tests/test_chart_tools.py`, `tests/test_chart_tools_reuse_integration.py`, `tests/test_chart_tools_legacy_error_contract.py`, `tests/test_charter_render_capture.py` (Phase A new), `tests/test_sources_dispatcher.py`, `tests/test_discord_adapter.py`, `tests/test_telegram_adapter.py`, `tests/test_slack_adapter.py`, `tests/test_outbound_image_delivery.py` (Phase B new), `tests/test_chart_image_delivery_e2e.py` + `tests/test_chart_image_delivery_audit.py` (Phase D new), `tools/audit-chart-image-delivery.sh` (Phase D new shell).
3. **USER ACTION ITEM — Slack `files:write` scope** — verbatim: *"To enable Slack chart-image delivery, grant the `files:write` OAuth scope in your Slack app's Bot Token Scopes and reinstall the app. Steps: 1) Open your Slack app config at api.slack.com/apps; 2) OAuth & Permissions → Bot Token Scopes → add `files:write`; 3) Save; 4) Reinstall the app to your workspace; 5) Restart the daemon if needed. Until granted, Slack chart-image delivery is text-only with a WARN-once log per channel. See `docs/sources/slack-setup.md`."*
4. **Restart/promote matrix** — verbatim table from §restart-promote (below).
5. **Rollback notes** — Phase B is the risky surface. Revert the Phase B commit(s); chart-render capability degrades gracefully to text-only (charter still emits the Mermaid block, just no marker; HTTP-API marker disappears; chat users see the code block). Phase A revert is the inverse: charter stops emitting the marker; chart-image delivery stops working for new charts; existing chart-render PNGs in `tmp_images` are unaffected (TTL sweep reaps them on schedule). Phase C revert: agents may stop cross-referencing Chat Delivery for chat-source turns; over-deliver-when-uncertain default still applies; no functional break.
6. **Adopted-items ledger** (R2 addition — was the deferred ledger pre-R2) — items promoted out of the deferred ledger during the commission. R2 fold-in promotes `store.delete(image_id)` after successful chat delivery (arch-rec §3 amendment #22 / `decisions.md` §phase-b-r2-addendum-9). The new ledger row: ~3 LOC + 1 test; API-origin keeps the 30-day GET per §http-api; mutually-exclusive lanes (api skips both seams; chat gets one lane) make double-delete impossible. Phase D's Group 8 in `tests/test_chart_image_delivery_e2e.py` covers the four test cases (chat-delivery success → 1 delete; upload-fail → 0 delete; API → 0 delete; both-lanes-fire → 1 total delete).
7. **Deferred-items ledger** — items after Phase D ship, each with owning future-ticket placeholders. **Four items** (R2 correction — moved image_delete to §7 adopted): (a) Telegram 4096-char text chunking (pre-existing defect; new ticket — `daemon/sources/adapters/telegram.py:262` `send()` lacks chunking for >4096-char text); (b) `source_hint` system-context injection (Phase C's open question #1; prompt-side, not adapter-side, deferred to Phase C's over-deliver-when-uncertain wording being sufficient); (c) real-platform e2e credentials (always operator-dependent); (d) HTTP-API structured image-ref response (Phase A §http-api currently marker-only; backward-compatible additive structured payload `{content, images: […]}`; new ticket). **Plus one ACCEPTED RESIDUAL** (not a deferred item — sound design debt needing an instance-carrying seam first): stale-real-id wrong-image delivery at ~1-3% (real stale id from an earlier turn/conversation attaches the wrong/old image; inherent to in-band marker Option A per arch-rec §4 merge table focus #6 + §6 pending #2); revisit trigger = strip-rate >10% post-Phase C or user report — OPERATOR-ACTIONABLE (R4/N7): surfaced via the release-report §7 residual note + WARN-log telemetry (`image_id[:8]` + size + content_type per amendment #12), so strip-rate is computable from logs with no code changes.
8. **Real-platform smoke evidence** — Discord (always-on): screenshot of the PNG in the channel + the marker-stripped explanation; Slack (gated): text-only delivery + WARN log line until scope granted; Telegram (gated): sendPhoto delivery with the PNG inline + caption (Telegram `parse_mode=None` per `decisions.md` §phase-b-r2-addendum-16).
9. **Sign-off** — project owner + the developer/tester who ran Phase D; date; release tag (mirrors `decisions.md` §phase-d-release-report item 9). [R3: section map renumbered to nine contiguous sections]

### 7. `decisions.md` — APPEND §phase-d-* sections (R2 update)

Five new sections, all append-only (never amend Phase A's locked §marker / §capture / §degradation / §http-api, never amend Phase B's §phase-b-* or §phase-b-r2-addendum-*):

- **§test matrix consolidation** — the 24 pins (PRESERVATION + FEATURE classes; pin #6 INVERTED; pin #24 added) + 8 e2e test groups (including real-astream Group 7 and store.delete Group 8).
- **§version bump decision** — rationale (`0.16.12 → 0.16.13`, minor per semver; mirror in `daemon/__init__.py` + `pyproject.toml`; performed at release cut, not at Phase D's planning/execution window).
- **§release-report template** — pointer to `release-report-template.md` plus the 9 sections enumerated (R2 added §7 adopted-ledger and §8 accepted-residual stale-real-id).
- **§adopted-items ledger** (R2 NEW) — `store.delete(image_id)` after successful chat delivery (arch-rec §3 amendment #22 / §phase-b-r2-addendum-9; promoted from prior §open-questions #5).
- **§deferred-items ledger** — the 4 remaining items with one-line summaries and owning future-ticket placeholders + the ACCEPTED RESIDUAL stale-real-id (~1-3%, revisit trigger >10% strip-rate post-Phase C or user report).

---

## Tasks (ordered, each with a verification step)

### D.0 — Pre-baseline gate (R5 + R3 fix: AUTHOR-THEN-RUN — the script must exist before the gate runs)

| # | Task | Depends on | Acceptance (verification) |
|---|------|------------|---------------------------|
| 0a | **Author** `tools/audit-chart-image-delivery.sh` + the PRESERVATION subset of `tests/test_chart_image_delivery_audit.py` (pins 1, 2, 3, 4, 5, 9, 13, 22, 23 = 9 pre-existing-invariant pins) AGAINST THE PRE-MERGE BASE — a clean checkout/worktree of the recorded base `cf8efbef` + planning commit `f4bf1557` (the commission worktree qualifies) — NOT a post-merge tree | none | Script + preservation tests exist and collect cleanly on the pre-merge base |
| 0b | **Run** the preservation gate on that pre-merge base BEFORE A/B/C implementation merges: `tools/audit-chart-image-delivery.sh --class preservation` exits 0 | 0a | Script exits 0 against the pre-merge base; any PRESERVATION pin failure escalates as **pre-existing breakage** (open a fix ticket; do NOT log as feature regression) — the FEATURE pins (6, 7, 8, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 24 = 15 pins) will of course fail pre-merge and that is expected |
| 0c | After A+B+C merge, run the FULL audit (`--class all`, all 24 pins) | 0b, A+B+C merged | All 24 pins green on the merged tree |

### D.1 — Audit-script + e2e scaffolding

| # | Task | Depends on | Acceptance (verification) |
|---|------|------------|---------------------------|
| 1 | Write `tests/test_chart_image_delivery_audit.py` with the **24** pins (PRESERVATION + FEATURE classes; pin #6 INVERTED per arch-rec §3 amendment #21; pin #24 added per amendment #18; content-addressable greps, NOT line numbers) | 0a (authoring extends the pre-merge module; the all-green run requires A+B+C merged) | `pytest tests/test_chart_image_delivery_audit.py -v` runs all 24 tests; all pass against the merged tree |
| 2 | Write `tools/audit-chart-image-delivery.sh` — bash wrapper that invokes the 24 pin tests + a couple of static greps for the marker regex; supports `--class preservation\|feature\|all` selector (default `all`); supports `--release-pin FILES…` for the OQ4 deferred-pin backstop | 1 | `./tools/audit-chart-image-delivery.sh` exits 0 against merged tree; `./tools/audit-chart-image-delivery.sh --class preservation` exits 0 against pre-merge `latest` (D.0 gate) |
| 3 | Write `tests/test_chart_image_delivery_e2e.py` with the **8** test groups (Group 7 marked `@pytest.mark.integration` + `@pytest.mark.timeout(300)` — run via `python -m pytest tests/test_chart_image_delivery_e2e.py -v --override-ini="addopts=" -m integration`, per tests/test_settings_api.py:19 precedent, iter-003 blocking #7) (full-chain happy + api-keep-marker + amendment #21 list; degraded image_get + wrong-MIME + provenance-mismatch; multi-chart + concurrent ordering; multi-source; installer hygiene; out-of-scope SHA tripwire; **REAL-ASTREAM-LANE per R8**; **store.delete-after-upload per R2 adopted**) | 1 | `pytest tests/test_chart_image_delivery_e2e.py -v` runs all tests; all pass after Phase A/B/C merge |
| 4 | Verify the audit script catches a known regression (sanity) — temporarily edit `daemon/tools/chart_tools.py:_BUSY_MSG` to drop the period; re-run `--class preservation`; assert exit non-zero with a clear failure message for pin #1; revert | 1, 2 | Audit script catches the deliberate break; revert restores green |

### D.2 — Docs surface verification

| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 5 | Spot-check candidate docs: `docs/setup.md`, `docs/sources/discord-setup.md`, `docs/pluggable-sources-architecture.md`, `docs/architecture.md`, `docs/agents.md` — search for "chart", "image", "attachment" to find any that currently mention but would now be wrong (e.g., "all responses are text-only") | none | Recorded in `release-report-template.md` §1; if any doc requires a real update, it's listed in `decisions.md` §phase-d-doc-verification; otherwise the only doc change is Phase B's `slack-setup.md` row |
| 6 | Confirm Phase B's `docs/sources/slack-setup.md` scope manifest + scopes table carry `files:write` (Phase B owns this; Phase D verifies) | A+B+C merged | `grep -n 'files:write' docs/sources/slack-setup.md` returns ≥2 hits (one in YAML manifest, one in scopes table) |
| 7 | Verify `docs/agent-prompt-writing-guide.md` does not need a chart-image-delivery mention (Phase D does NOT change it — verify nothing contradicts the Phase C edits) | A+B+C merged | `git diff` against `docs/agent-prompt-writing-guide.md` is empty post-A/B/C merge; if anything changed, file an issue (out of Phase D scope) |

### D.3 — Version + CHANGELOG

| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 8 | Write `docs/changelog-pending/chart-image-delivery.md` with the exact Keep-a-Changelog `### Added` block (see Components §4) | 1, 2, 3 | File exists; format matches existing `CHANGELOG.md:32-65` (`[0.14.2] — 2026-09-25`) style; verifies against the project's Keep-a-Changelog convention |
| 9 | Record the version bump decision in `decisions.md` §phase-d-version-bump (rationale: minor for new feature; mirror in both files; performed at release cut) | none | decisions.md has the new section appended |
| 10 | Stage the version bump: prepare a single-line change to `daemon/__init__.py:3` (`0.16.12` → `0.16.13`) and `pyproject.toml:3` (same); DO NOT commit yet — release-cut-only per the project upgrade policy (v0.16.x release line with staged releases + promote ceremonies per the upgrade workflow preference: prefer speed, slim safety steps) | 9 | `git diff` shows the two one-line changes locally; commit message drafted for release cut |

### D.4 — Release report + decisions append

| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 11 | Write `.agents/shared/planning/chart-image-delivery/release-report-template.md` with the 9 sections enumerated in Components §6 (what shipped, test evidence, USER ACTION ITEM, restart/promote matrix, rollback notes, adopted-items ledger, deferred-items ledger + accepted residual, real-platform smoke evidence, sign-off) | 1–7 | File exists; sections enumerated; USER ACTION ITEM wording is verbatim per the brief; restart/promote matrix matches the consolidation in §restart-promote (below) |
| 12 | Append §phase-d-* to `decisions.md` (4 sections: test matrix consolidation, version bump decision, release-report template, deferred-items ledger) | 11 | decisions.md has the new sections appended; Phase A and Phase B locked sections unchanged (verified via `git diff` on the locked sections) |
| 13 | Fill in the release report — the Phase D implementer replaces the template's `[...]` placeholders with the actual evidence: per-suite test counts, real-platform smoke screenshots, the final commit hashes for A/B/C/D | 11, A+B+C+D merged | `release-report.md` complete; signed off by the reviewer |

### D.5 — Final regression + sign-off

| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 14 | Full regression run — every suite named in release-report §2 + the audit script + the e2e tests in STANDARD collection, PLUS the explicit integration lane for Group 7 (`--override-ini="addopts=" -m integration`) — both legs green (iter-003 blocking #7) | 1–13 | Exit 0 on BOTH legs; per-leg counts recorded in the release report |
| 15 | Manual Discord real-platform smoke — submit a `generate_chart` request via a real Discord channel against the staging daemon; capture the PNG attachment + the marker-stripped explanation as evidence in `release-report.md` §8. **Contingency (R4/N6):** if the real-channel smoke is blocked (staging env/credentials), record the blocker, run the Group 7 astream e2e as the INTERIM gate, and HOLD the release cut until a real-channel smoke completes — the user story must not ship on mock-only evidence | 14 | Screenshot in the release report; PNG is the actual `mmdc` output, not a fixture; contingency (if invoked) documented with the blocker + interim gate evidence |
| 16 | Manual Slack real-platform smoke (gated) — IF the operator has granted `files:write` scope, run the same flow on Slack; otherwise record the text-only delivery + WARN-once log as evidence (the gating IS the user action item — the report makes this explicit) | 14 | Either: PNG in Slack channel + file ref visible in the post, OR text-only delivery with the documented WARN-once log line + scope-not-granted note in the report |
| 17 | Manual Telegram real-platform smoke (gated) — IF the operator has a real bot token + chat_id, run the same flow on Telegram; otherwise record the gating as deferred evidence | 14 | Either: sendPhoto delivery with PNG + caption, OR deferred-evidence note in the report |
| 18 | Out-of-scope SHA tripwire — run the Phase D test group's tripwire; verify Phase A/B/C files unchanged by Phase D commits; verify Phase D commits only touch Phase D's own files | 1, 13 | Tripwire green; `git log --stat` shows Phase D commits touch only the **10** files in §Components (incl. the filled `release-report.md` — iter-003 blocking #6) |

### D.6 — Release cut (separate from Phase D's own commits)

| # | Task | Depends on | Acceptance |
|---|------|------------|------------|
| 19 | Release cut: commit the version bump (`daemon/__init__.py` + `pyproject.toml`) + the CHANGELOG entry slice (from `docs/changelog-pending/chart-image-delivery.md` into `CHANGELOG.md` `[Unreleased]`); tag the release per the project's release workflow | 18 | New tag exists; CHANGELOG `[Unreleased]` carries the chart-image-delivery block; version constants match |
| 20 | Promote per the project's upgrade policy — the v0.16.x line uses staged releases + promote ceremonies (per project upgrade workflow preference, effective from v0.16.8 onward: prefer speed, slim safety steps; full backup only when explicitly requested by user) | 19 | Promote succeeds; live rung carries v0.16.13; post-promote `tools/audit-chart-image-delivery.sh` exits 0 against the live daemon |

---

## Dependencies

### Upstream (LOCKED — Phase D cannot run until these merge)

- **Phase A implementation merged to `latest`** — `agents/charter/workflow.md`, `agents/charter/rule.md`, `agents/charter/soul.md`, `agents/charter/meta.json`, `agents/charter/skills-template/install-mermaid-cli.md` (NEW), `agents/_prompt_system/innate-skills/chart/skill.md`.
- **Phase B implementation merged to `latest`** — `daemon/sources/base.py`, `daemon/sources/registry.py`, `daemon/sources/dispatcher.py`, `daemon/sources/adapters/discord/adapter.py`, `daemon/sources/adapters/slack/adapter.py`, `daemon/sources/adapters/telegram.py`, `daemon/constants.py`, `docs/sources/slack-setup.md`.
- **Phase C implementation merged to `latest`** — `agents/_prompt_system/innate-skills/chart/skill.md` "Chat Delivery" section + **20** agent cardinal references (across `agents/*/{rule,soul,tools_note,workflow}.md`).
- **All per-phase test suites merged** — `tests/test_charter_render_capture.py` (A), `tests/test_sources_dispatcher.py` (B additions), `tests/test_discord_adapter.py` (B additions), `tests/test_telegram_adapter.py` (B additions), `tests/test_slack_adapter.py` (B additions), `tests/test_outbound_image_delivery.py` (B NEW).

### Downstream

- **Release cut + promote** — Phase D's audit + e2e tests gate the release; the version bump + CHANGELOG entry land at release cut (not in Phase D's own commits).
- **Operator post-deploy verification** — `tools/audit-chart-image-delivery.sh` against the live daemon; Discord manual smoke.

### Cross-Phase Coupling (consolidation only — Phase D does NOT touch the other phases' code)

| | Phase A | Phase B | Phase C |
|---|---|---|---|
| **Phase D** | verifies + audits (no edits) | verifies + audits (no edits) | verifies + audits (no edits) |
| **Phase D test surface** | adds full-chain e2e + 24-pin audit | adds degraded-path e2e + dispatcher integration assertion + real-astream-lane + store.delete | adds 20-coverage assertion + `_BUSY_STRING` byte-pin + Wedged-Charter unchanged assertion |

---

## Test Strategy

| Layer | Pattern | File |
|-------|---------|------|
| Cross-cutting pin audit | 24 individual pytest tests, each asserting one pin (PRESERVATION + FEATURE classes; pin #6 inverted per arch-rec §3 amendment #21; pin #24 added per amendment #18) | `tests/test_chart_image_delivery_audit.py` (NEW) |
| Cross-cutting pin audit (shell) | bash wrapper invoking the pytest module + a couple of static greps; `--class preservation|feature|all` selector (D.0 gate uses `preservation`) | `tools/audit-chart-image-delivery.sh` (NEW) |
| Full-chain e2e | mock source adapter + real `TmpImageStore` (1×1 PNG fixture) + real `SourceRegistry` + real `Dispatcher` + real `MockSourceAdapter` → assert `sent_messages[-1].images` populated and content marker-free | `tests/test_chart_image_delivery_e2e.py` (NEW, Group 1) |
| API-source e2e | mock source with `source_id="api"` (no colon) → assert no dispatch + marker preserved + `open_with_meta` spy NOT called | `tests/test_chart_image_delivery_e2e.py` (NEW, Group 1) |
| Real-astream-lane e2e | real `InstanceMessagingService` against `instance_messaging.py:4506-4566` + `:4815-4834` path with a synthetic Discord `IncomingMessage` → assert astream progressive delivers the PNG (NOT the `dispatch_completed` stub) | `tests/test_chart_image_delivery_e2e.py` (NEW, Group 7) |
| Degraded-path e2e | store populated, then `open_full` raises / wrong-MIME / provenance-mismatch / stale-id → assert text fallback + WARN | `tests/test_chart_image_delivery_e2e.py` (NEW, Group 2) |
| Multi-chart + ordering | interleaved text/chart requests; assert order preserved under per-user locks (lock-inside-locks per amendment #10) | `tests/test_chart_image_delivery_e2e.py` (NEW, Group 3) |
| Multi-source isolation | Discord + Slack sources interleaved; assert no cross-leak | `tests/test_chart_image_delivery_e2e.py` (NEW, Group 4) |
| store.delete-after-upload (ADOPTED) | chat delivery success → `store.delete` once; upload-fail → no delete; API-source → no delete; both-lanes-fire → 1 total delete | `tests/test_chart_image_delivery_e2e.py` (NEW, Group 8) |
| Out-of-scope tripwire | SHA256 snapshot of sealed files; assert unchanged after Phase D commits | `tests/test_chart_image_delivery_e2e.py` (NEW, Group 6) |
| Manual real-platform smoke | Discord (always-on), Slack (gated), Telegram (gated) — operator-driven | documented in `release-report-template.md` §8 |

**Mock fixture:** a 1×1 PNG (89 bytes; verified via `file out.png` reports `PNG image data`) saved via a real `TmpImageStore`; `image_id` is a fresh uuid4 hex. Cleanup in pytest fixture's teardown deletes blob + sidecar together (per `tmp_image_cleanup_service.py:37-42` pair semantics).

**Standalone audit:** `tools/audit-chart-image-delivery.sh` is runnable in CI before merge AND by operators post-deploy. Pattern: invoke the audit pytest module + a couple of static greps for the marker regex + the prompt-audit checklist (Phase C's grep). Exit code 0 iff all 24 pins pass (or all PRESERVATION pins pass under `--class preservation`).

---

## Acceptance Criteria (checkboxable)

### Primary user-story (THE gate)

- [ ] **A Discord user asks the agent for a workflow chart → the agent calls `generate_chart()` → the user receives the rendered PNG as a Discord attachment in the same message as the explanation text, NOT a wall of Mermaid code.** Verified via (a) `tests/test_chart_image_delivery_e2e.py::test_full_chain_discord_user_story` (mock-adapter e2e); (b) manual Discord real-platform smoke (`release-report.md` §8 screenshot).

### Cross-cutting regression (ALL GREEN)

- [ ] D.0 pre-baseline gate: `tools/audit-chart-image-delivery.sh --class preservation` exits 0 against clean pre-merge `latest` (no Phase A/B/C commits yet) — confirms PRESERVATION pins hold.
- [ ] `tools/audit-chart-image-delivery.sh` (default `all` class) exits 0 against the merged tree — all 24 pins pass.
- [ ] `pytest tests/test_chart_image_delivery_audit.py -v` — all 24 tests pass.
- [ ] `pytest tests/test_chart_image_delivery_e2e.py -v` — all tests pass (Groups 1-8, including the **REAL-ASTREAM-LANE** Group 7 and the **store.delete-after-upload** Group 8).
- [ ] Phase A's `tests/test_charter_render_capture.py` — 8 cases green.
- [ ] Phase A's `tests/test_chart_tools.py` (1024 lines) — 100% green.
- [ ] Phase A's `tests/test_chart_tools_reuse_integration.py` (1619 lines) — 100% green.
- [ ] Phase A's `tests/test_chart_tools_legacy_error_contract.py` — 100% green.
- [ ] Phase B's `tests/test_outbound_image_delivery.py` (NEW) — 100% green.
- [ ] Phase B's `tests/test_sources_dispatcher.py` — existing + new (extract_chart_images at both seams + dispatch_message progressive + dispatch_completed final + API-source-keep-marker with open_with_meta spy + e2e byte-for-byte twin) — 100% green.
- [ ] Phase B's `tests/test_discord_adapter.py` — existing + new (file-on-chunk0 atomic-unit + files=[…] for N>1 + upload-fail-text-fallback + empty-content-with-images guard) — 100% green.
- [ ] Phase B's `tests/test_telegram_adapter.py` — existing + new (sendPhoto + sendDocument + multipart-4xx-non-transient classification + text-fallback + parse_mode=None for captions) — 100% green.
- [ ] Phase B's `tests/test_slack_adapter.py` — existing + new (files_upload_v2 single-call + SlackCapabilityError + capability-flag zero-API-calls-on-repeat + missing-files:write) — 100% green.

### Out-of-scope tripwire

- [ ] `tests/test_chart_image_delivery_e2e.py::test_phase_d_does_not_modify_sealed_artifacts` green — SHA256 of Phase A's **10** files + Phase B's 7 daemon files + Phase C's **20** agent files + chart skill, baselined against the A/B/C merge state via `git show <merge-commit>:<path>` (NOT setup-time snapshots — iter-003 blocking #5), unchanged across Phase D's commits.

### Version + CHANGELOG

- [ ] `daemon/__init__.py:3` is `"0.16.13"` at release cut.
- [ ] `pyproject.toml:3` is `"0.16.13"` at release cut.
- [ ] `CHANGELOG.md` `[Unreleased]` contains the chart-image-delivery block from `docs/changelog-pending/chart-image-delivery.md` at release cut.

### Docs surface

- [ ] `docs/sources/slack-setup.md` carries `files:write` in BOTH the YAML manifest (line ~46) AND the scopes table (line ~70 area) — Phase B owns; Phase D verifies.
- [ ] No other docs require updates (verified per Task 5 spot-check) OR any required updates are recorded in `decisions.md` §phase-d-doc-verification.

### Restart/promote (consolidated)

- [ ] Release report §4 contains the verbatim restart/promote matrix:
  - Phase A files = NO restart (agent-prompt only; picked up at next instance spawn).
  - Phase B daemon files = YES restart + YES promote (`daemon/sources/base.py`, `daemon/sources/registry.py`, `daemon/sources/dispatcher.py`, `daemon/sources/adapters/{discord,slack,telegram}/*`, `daemon/constants.py`).
  - Phase C files = NO restart (agent-prompt only).
  - docs/tests = n/a.

### USER ACTION ITEM surfaced

- [ ] Release report §3 carries the verbatim Slack `files:write` USER ACTION ITEM with the 5-step operator procedure (api.slack.com/apps → OAuth & Permissions → add scope → save → reinstall → restart daemon if needed) and the gating note (until granted, Slack chart-image delivery is text-only with WARN-once per channel).

### Rollback + deferred items

- [ ] Release report §5 documents Phase B as the risky surface; describes the graceful degradation (text-only Mermaid delivery) if reverted.
- [ ] Release report §6 carries the ADOPTED-items ledger row (`store.delete(image_id)` after successful chat delivery — promoted from prior §open-questions #5 in R2; ~3 LOC + 1 test).
- [ ] Release report §7 carries the **4**-item deferred-items ledger: (a) Telegram 4096-char text chunking; (b) `source_hint` injection (Phase C's open question #1); (c) real-platform e2e credentials; (d) HTTP-API structured image-ref response — PLUS the ACCEPTED RESIDUAL stale-real-id (~1-3%, inherent to in-band marker Option A; revisit trigger = strip-rate >10% post-Phase C or user report).

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Phase D's audit script catches a pin that was actually broken before Phase A/B/C (false positive — pin was already broken on `latest`) | Med | Med | Run the audit script on a clean `latest` BEFORE Phase A/B/C merge; if any pin fails pre-merge, escalate to the test agent + open a fix ticket before Phase D proceeds |
| 2 | The full-chain e2e test's `TmpImageStore` integration is flaky (real store vs. mock-store divergence) | Med | Med | Construct the store via the same factory the daemon uses at lifespan boot (verified via `daemon/manager.py:2545` `tmp_image_store` property + `:433-452` constructor param injected from `app.state.tmp_image_store` at lifespan — replaces the false `daemon/persistence.py:79-89` anchor, R3); run the e2e in a hermetic tmp dir with a fresh UUID per test; teardown deletes both blob and sidecar |
| 3 | Out-of-scope SHA tripwire catches a Phase D commit that legitimately needed to touch a "sealed" file (e.g., a regression in Phase A's `daemon/tools/chart_tools.py` discovered mid-Phase-D) | Med | Low | Tripwire is a WARNING, not a hard gate; escalate to dispatcher; the Phase D implementer does NOT silently amend a sealed file — they surface the discovery to the planner |
| 4 | The real-platform smoke on Discord requires the staging daemon to be running with chart-image-delivery staged + promoted; if not, the smoke fails | Med | Med | Phase D's implementer coordinates with the operator to ensure staging is staged + promoted BEFORE the manual smoke; the staging lane is the project's standard staging lane (verified via shared meta-kv `git.branch = "feature/chart-image-delivery"`) |
| 5 | The Slack USER ACTION ITEM requires operator action that may not happen before the release cut | Med | Med | The release report makes the gating explicit (text-only delivery + WARN-once log) so the feature is shippable without the scope grant; the operator action is a follow-up that ENABLES Slack delivery, not a blocker for the release |
| 6 | `tmp_image_store.open_with_meta` raises an exception type not in the documented set (`TmpImageNotFound`, `FileNotFoundError`, etc.) | Low | Low | Phase B's text-fallback path catches `Exception` broadly; Phase D's Group 2 test covers the `TmpImageNotFound` + wrong-MIME paths; broader exception handling is Phase B's concern |
| 7 | Manual Discord smoke fails because the staging environment does not have mmdc installed (charter's toolchain is missing) | High | Low | The `install-mermaid-cli.md` skill (Phase A) handles this — the manual smoke is preceded by the smoke operator running the install skill once via charter spawn (per the D1 pattern); if mmdc is still missing, the smoke operator files a charter-toolchain issue (out of Phase D scope) |
| 8 | Phase D's e2e test (Group 3 concurrent ordering) is timing-sensitive and flakes under CI load | Med | Med | Use `asyncio.gather` with explicit `await` ordering + the per-user LRU lock as the synchronization primitive (no `sleep`); do NOT add `@pytest.mark.flaky(reruns=2)` (pytest-rerunfailures is ABSENT from pyproject — R5 tracking note); the acceptance is a 10-run green baseline with manual re-run on failure |
| 9 | The release-report-template.md is too prescriptive and Phase D's implementer just fills in checkboxes without thinking | Low | Med | The template is a STRUCTURE for evidence, not a checklist of completion; the reviewer cross-checks every section's evidence (e.g., "test counts green" must list actual test counts from CI output, not just "✓") |
| 10 | The version bump (`0.16.12 → 0.16.13`) collides with another feature also bumping to `0.16.13` | Med | Low | The release cut coordinates version bumps via the project's standard release process (verified via shared meta-kv `upgrade_policy`); Phase D's bump is recorded in `decisions.md` §phase-d-version-bump so the release coordinator sees it |
| 11 | Phase D's audit script + e2e tests pass on the developer's machine but fail on a fresh CI runner (env drift — `~/.nvm/nvm-exec`, `~/.cache/puppeteer`, etc.) | Med | Low | The audit script + e2e tests do NOT depend on mmdc / puppeteer / nvm (they test the delivery chain, not the render chain); the render chain is covered by Phase A's charter-spawn smokes (manual in CI; not Phase D's lane) |
| 12 | **Stale-real-id wrong-image delivery** (accepted residual, ~1-3% per arch-rec §4 merge-table focus #6 + §6 pending #2; inherent to in-band marker Option A — a real stale `image_id` from an earlier turn or another conversation attaches the wrong/old image; mint-ledger-recorded sound design debt needing an instance-carrying seam first) | Med | Med | Documented in release report §7 as ACCEPTED RESIDUAL (NOT a deferred engineering item); revisit trigger = **strip-rate >10% post-Phase C or user report** (per arch-rec §6 pending #2 + decided in §5 risks "Stale-real-id wrong image delivery — ledgered"); Phase D's smoke evidence + post-release telemetry (log: `image_id[:8]` + size + content_type per arch-rec §3 amendment #12) feeds the strip-rate metric; if the trigger fires, open a new commission for an instance-carrying sidecar (Option B's correlation fix becomes tractable once the carrier exists) |
| 13 | Pin #6 inversion / pin #24 (Ari pre-warm) added during R2 fold-in; if a CI-gate script was already wired to the prior 23-pin catalog, the script breaks on the new pin count | Low | Low | D.1 Task #4 (sanity regression) catches the count mismatch on the next CI invocation; the bash wrapper's `--class preservation\|feature\|all` selector makes the gate CI-portable |

---

## Open Questions

1. **Should Phase D's e2e test run as part of the standard CI test run, or as a separate gated job?** — Candidates: include in the standard pytest collection (cheaper to maintain, runs every PR); separate job gated on the `feature/chart-image-delivery` branch (more isolated, doesn't run on unrelated PRs). **Decision needed by:** release coordinator before Phase D implementation starts. **RESOLVED R5 (iter-003 blocking #7):** `pyproject.toml` global `addopts = "-m 'not integration and not postgres'"` DESELECTS integration-marked tests from standard collection — Group 7 marked `@pytest.mark.integration` would silently never run while acceptance shows green. Group 7 KEEPS the mark (real daemon harness, heavy) and gains an EXPLICIT integration-lane leg everywhere acceptance is claimed, per the repo precedent `tests/test_settings_api.py:19`: `python -m pytest tests/test_chart_image_delivery_e2e.py -v --override-ini="addopts=" -m integration`. Group 7 tests also carry per-test `@pytest.mark.timeout(300)` (the global `timeout = 30` is too tight for the real harness). No rerunfailures marker — the plugin is absent from pyproject; a 10-run green baseline is the flake acceptance (manual re-run on failure).

2. **Who reviews the release report?** — Phase D plans "one reviewer for the release report"; the project's standard release-review chain is the project owner (per `upgrade_policy.ratified_by_user` — user-confirmed promote ceremony). **Decision needed by:** release coordinator. Default plan: the project owner reviews the report + the version bump + the CHANGELOG entry at release cut; the developer/tester who ran Phase D confirms the test evidence + the USER ACTION ITEM wording. The "3-factor arm ceremony" (user_confirmed + genuine-user-turn + nonce match) at promote is the final gate.

3. **What is the exact format of the Slack `files:write` USER ACTION ITEM in the release report?** — The brief says "the operator steps: grant scope + reinstall app"; Phase D plans a 5-step procedure. **Decision needed by:** none — Phase D uses the procedure as written; if the operator prefers a different format at review time, the wording is in `release-report-template.md` §3 and is editable.

4. **Should the deferred-items ledger link to existing project tickets, or just be a flat list?** — Phase D plans a flat list (no ticket IDs since the integration doesn't yet plan Phase 2 / post-execution work for these items). **Decision needed by:** release coordinator. Default plan: flat list with one-line summaries; the release coordinator opens the tickets after release and adds IDs to the ledger retroactively.

5. **Does the manual Discord smoke need to run against the production-like staging daemon, or against a dedicated test daemon?** — Phase D plans staging (the project's standard staging lane). **Decision needed by:** release coordinator. Default plan: staging, per the project's standard practice; the staging lane is the `feature/chart-image-delivery` branch (verified via shared meta-kv `git.branch`).

---

## Cross-Reference to Phase A / B / C

- **Phase A** (render-at-validation capture + marker contract): owns the marker regex, the capture flags, the degradation ladder (charter-side), the install skill. Phase D's audit pins #3, #6, #15, #16 + the charter-spawn test in the green-locked pin list; Phase D's e2e Group 1 asserts the marker end-to-end.
- **Phase B** (chat-adapter delivery): owns the dispatcher extraction, the per-adapter upload, the constants, the slack-setup.md scope table. Phase D's audit pins #5, #7-14, #21, #22; Phase D's e2e Groups 1-4 assert the dispatch chain.
- **Phase C** (agent guidance): owns the chart skill's "Chat Delivery" section + the 20 cardinal references. Phase D's audit pins #17, #18, #19, #20; Phase D's e2e Group 5 verifies the install-skill frontmatter.

Phase D is the LAST phase to land. All three upstream phases' per-files MUST be in `latest` before Phase D's implementer runs.

---

## Restart / Promote Note

Phase D is **a consolidation + verification phase**. Its own code changes (the e2e test, the audit script, the audit pytest module, the release report template, the CHANGELOG draft, the decisions.md append) are picked up by the standard test/lake-load mechanism — **no daemon restart is required for Phase D's own commits**.

The restart/promote the feature as a whole requires is determined by the OTHER three phases:

- Phase A files = NO restart (agent-prompt only).
- Phase B daemon files = YES restart + YES promote.
- Phase C files = NO restart (agent-prompt only).
- docs/tests = n/a.

This matrix is the FINAL entry in the release report §4. The release cut + promote are Phase D.6 (release-cut-only), executed per the project's upgrade policy (`upgrade_policy.ratified_by_user` + `upgrade_workflow_preference` effective from v0.16.8: prefer speed, slim safety steps; full backup only when explicitly requested by user).

---

## Implementation Dispatch Shape

- **One tester** (same tester context that ran A/B/C verification — accumulated context benefits the consolidation). Writes `tests/test_chart_image_delivery_e2e.py`, `tests/test_chart_image_delivery_audit.py`, `tools/audit-chart-image-delivery.sh`, `docs/changelog-pending/chart-image-delivery.md`, `release-report-template.md`, appends `decisions.md`.
- **One reviewer** (the project owner per the project's release-review chain). Cross-checks: (a) all **24** audit pins green (PRESERVATION + FEATURE); (b) all **8** e2e groups green — Group 7 evidenced by the EXPLICIT integration-lane output (`--override-ini="addopts=" -m integration`; standard collection alone silently deselects it — iter-003 blocking #7) (including the **REAL-ASTREAM-LANE** Group 7 and the **store.delete-after-upload** Group 8); (c) the SHA tripwire green (Phase A/B/C files unchanged); (d) the USER ACTION ITEM wording verbatim; (e) the restart/promote matrix verbatim; (f) the adopted-ledger + deferred-ledger + accepted-residual complete; (g) the version-bump decision recorded in `decisions.md`.
- **No developer** for Phase D's own code (the consolidation is verifier-centric). The release-cut developer (per D.6) flips the version + slices the CHANGELOG; that's the standard release process, not a Phase D development task.