# Phase 2: Image→text conversion + persistence + vision-gate coexistence

> **Backend — Phase 2 of 6** for `feature/clipboard-image-chat`.
> Phase owner: `plan-worker-backend` (this file).
> Branch baseline: `feature/clipboard-image-chat` @ `307db932` (planning-only).
> Depends on: phase 1 (tmp-image store + endpoints).
> Cross-references: phase 3 (retention), phase 4 (FE upload), phase 5 (FE display),
> phase 6 (FE viewer + grace).

## Amendment log (architect rulings 2026-09-19)

Applied in this pass (per `.agents/shared/planning/clipboard-image-chat/architecture-recommendation.md` §8):

- **#7** 🔴 — Task 5 test rewritten: REPLACED fail-open assertion with the architect's 5 EXACT fail-fast cases (architect §2). Must land before tests are written.
- **#8** — Task 2 timeout: added `TMP_IMAGE_CONVERSION_TIMEOUT_S = 90.0` (converter-owned constant, NOT the tool's 300s literal — tool-context budget ≠ HTTP-context budget). Deleted "300s matches image_tools.py:521" claim.
- **#9** — Task 2 error-STRING collapse: `ok = bool(result) and not str(result).startswith("Error")` + None check (mirror `image_tools.py:530-541`; `invoke_agent_and_wait` NEVER raises — returns `"Error: …"` strings per `utils.py:695-705`). Store read INSIDE per-image try (+ unit case for vanished file).
- **#10** — Task 8 fault injection forces the timeout STRING path (exception case kept).
- **#11** — Task 4: optional `await request.is_disconnected()` early-exit between per-image conversions → convert remaining to placeholders, release invoke lane early, INFO log on disconnect. Document "client disconnect = accept-and-document" (Starlette does not cancel the handler; retry duplicates).
- **#12** — Task 6: DELETED `ENSEMBLE_IMAGE_CONVERSION_MODE` env knob (no-knob ruling, daemon-side).
- **#13** — Risks: ADDED ordering-inversion row (sync-in-POST: later text-only B overtakes converting A on both legs; cross-source/multi-tab bypass FE block-send; accepted-risk v1, low×low). DELETED Risk 3d / Task 4b (600s per-POST deadline — dead code: 3×90=270s < 600s can never fire). Invoke-semaphore 4-cap documented in converter docstring (§5.5 — the semaphore IS the concurrency guard; no POST-level guard).
- **#14** — Task 0 (vision probe) PROMOTED to the phase-1 window (architect §6 h6). Manual, minutes-cheap; runs at phase-1 contract-freeze time; failure escalates BEFORE the {2,3,4} wave dispatch. Phase-1 plan now owns the probe; phase-2 Task 0 retains only the conversion-time fail-fast check.

### Round 2 (architect post-review rulings 2026-09-19)

Applied in this pass (per `architecture-recommendation.md` §"Post-review rulings"):

- **#26 (C1) — Task 7 REWRITE.** Signature-separated channels: router calls `manager.enqueue_message_job(..., images=message.images, image_refs=message.image_refs)`. `_build_message_content` (`manager.py:165-180`, `instance_messaging.py:113-128`) carries ONLY `images` — structurally cannot see refs. The old "overload images=" instruction is DELETED. This fixes the conflation that would route every ref-send to vision via `has_images=True` at `graph.py:7032-7051`.
- **#27 (C1) — Task 10 PROMOTED to REQUIRED.** Full facade-forwarding for `image_refs` across the 5-function chain: `InstanceManager.enqueue_message` (`:6795-6808`) + `enqueue_message_job` (`:6893-6905`) → `InstanceMessagingService.enqueue_message` (`:1977+`) + `enqueue_message_job` (`:2151+`) → `_prepare_enqueued_message` (`:1527-1541`, row `:1688`) → `_process_message_with_tracking` (`:6944-6956`, `:7009`). 5-test guard pattern × BOTH facade methods + real-dispatch integration test mirror.
- **#28 (C1) — Implementation mapping block.** `_build_graph_input` (`instance_messaging.py:409-532`) merges `{"image_refs": [...]}` into `HumanMessage.additional_kwargs` (additive-only when non-empty; pattern from `_stamped_additional_kwargs` at `:382-406`); row's `images` column = `images=(image_refs if image_refs is not None else images)` (audit; NOT display source). Fallback if LIST-typed additional_kwargs doesn't round-trip: amend stamp to JSON string (A1/A3 end-to-end prove either way).
- **#29 (C1) — `serialize_message` union extension.** `utils.py:113-137` + `:252+` reads `additional_kwargs['image_refs']`, UNIONs into wire `images` (single field; legacy `image_url` blocks surface unchanged — reviewer question (ii) ruled legacy display untouched). Identity-grep pin that `image_refs` appears verbatim in that block + comment citing this ruling (serializer-simplification hazard).
- **#30 — PAUSED/resume branch.** `messages.py:313` + `:342` forward `message.image_refs` alongside `images` (XOR guarantees at most one non-empty).
- **#31 (C2 — pulled INTO v1) — NEW TASK h4-S1: `set_injection` image_refs kwarg.** `manager.py:2734-2740` gains `image_refs=None` kwarg; RAM-FIFO entry adds `image_refs` ONLY when non-None (byte-identical-when-absent, mirroring source/echo_id at `:2784-2797`). Drain site `graph.py:6647-6673` stamps `additional_kwargs["image_refs"]` on the injected HumanMessage (METADATA ONLY, never content blocks; agent channel stays text-only per `langchain_openai` not serializing `additional_kwargs` to the wire at `:6657-6661`). Router `messages.py:458` passes `image_refs=message.image_refs`; POST-time echo `:486-491` stamps `additional_kwargs={"image_refs": [...]}`. Byte-identical-when-absent tests for all 4 consumer classes (`messages.py:458`; `tools/instance.py:3166`; `sources/registry.py:1029`; `tools/job_queue.py:2471`) mirror `tests/test_injection_slot.py:255-280`. `set_injection` lives directly on `InstanceManager` (NO facade seam — verify in PR). **Bonus**: closes pre-existing 202 images-drop defect for LEGACY data-URI sends (`messages.py:458` today drops `message.images`). Documented escape: if drain-site stamp violates live-turn invariant, fall back to S4 (re-scope), never widen S1.
- **#32 — S3 REJECTED.** POST-time MessageQueue row for 202 leg is wedge-class V (row-without-Task+notify strands forever; `child_reports.py:3136-3247`, `message_processing_pipeline.py:611-660`). `_prepare_enqueued_message:1977` is the only strand-safe shape (atomic `MessageQueue + Task + Event` in one transaction). S3 strictly more work than S1 (h4-S1 above) and rejected.
- **#36 (phase1) — Probe checklist line.** Image-reader `meta.json` declares `llm_model: "quick"`. Watcher resolver `graph.py:8551-8599` maps `quick` → `model_keywords` (operator-pinned via `OPENAI_MODEL_KEYWORDS`) with `""` fallback, but image-reader resolves at spawn via a separate `_resolve_model_override` path whose keyword handling is UNVERIFIED. Probe must verify the image-reader spawn log shows a RESOLVED model name (e.g. `gpt-4o-mini`), NOT the literal `quick`. Verbatim `quick` = FAIL probe + escalate R2-style (same posture as model_vision probe). Added in phase-1 plan.
- **#38 — Sources must NOT add `image_refs`.** One-line guard comment at `daemon/sources/base.py:25` ("Sources must NOT add `image_refs` — refs are POST-only") + static field-absence assertion test (A8) in phase-2 Task 11's audit. `IncomingMessage` has NO `image_refs` field — safe by construction; `registry.py:94-95` `msg.images` predicate covers the only image-bearing field.
- **C3 (regex) — Task 3 ref validator.** Must ACCEPT all 3 §2 canonical forms: bare 32-hex, `tmpimg://<32hex>`, and `/api/tmp_images/<32hex>`. Old regex `^(tmpimg://)?[a-f0-9]{32}$` REJECTS the canonical URL form.
- **Test freeze list (phase-2 gate) — A1–A11 + residuals** added verbatim.
- **O2 — Reword collapse-guard precedent claim.** `image_tools.py:530-541` is a None-check ONLY (`if result is None: return "Error: ..."`); the `startswith("Error")` guard is NEW with no precedent. Mirror-citation kept as "pattern inspiration", not precedent.
- **O4 — Open Q tail.** "RULED FAIL-FAST (amendment #7) — superseded" appended to Open Question #4.
- **O5 — Anchor nits verified.** messages.py:454 = `str(uuid.uuid4())` (echo_id mint) ✓; `_BASE64_IMAGE_PATTERN` at message.py:8 ✓; `_INJECTION_TTL_SECONDS` at manager.py:2732 ✓; SPA catch-all returns `404 JSON` for `/api`/`/ws`/`/vscode` prefixes, `index.html` for non-API paths ✓.
- **O6 — DELETE∥sweep race test annotated PHASE-3-DEFERRED** (phase-1 Task 4b references it; sweep doesn't exist until phase 3).

### Polish pass (cycle-2 review APPROVED-WITH-NOTES; rulings final; no re-review)

- (2) Stale round-1 "202-gap out of scope" bullet STRIKEN at :126-143 — false mechanism + rejected-S3 option contradicted Task 16 / amendment #31 / test A10. Replaced with a 1-line pointer to Task 16 (h4-S1 closes the gap in v1).
- (3) Anchor drift-stability fixes — Component #6 now cites FUNCTION NAME (`InstanceManager.set_injection` signature + conditional-add block) instead of `daemon/manager.py:2734-2808`; Component #12 marks `daemon/tools/instance.py:3166` as HEAD-only (drifted from baseline `307db932` ≈ `:3124`) with a function-name alternative; test strategy entry updates to match. Drift-prone line numbers replaced with structural anchors that survive HEAD churn.
- (4) Objective #3 reworded to audit wording — `MessageQueue.images` row is **audit** (not the display read source; display reads via checkpoint kwargs + serializer union, Component #9b). FE phase-5 reads rendered refs via GET /messages post-union.
- (5) Task 12's pointer updated from "Task 15" → "Task 16" (h4-S1 is the live acceptance; tests A9 + A10).
- (7) Task 11 (chat-source audit) — implementation touchpoint added: `daemon/sources/registry.py:78-82` "text-only" comment goes STALE post-h4-S1 (the injection lane gains `additional_kwargs["image_refs"]` metadata). Phase-2 implementer MUST update that comment when Task 16 lands (clarify "text-only on agent channel; image_refs is display-metadata via additional_kwargs"). Code-review checklist item added.

---

## Objective

Add an **image-to-text conversion seam** to the `POST /instances/{id}/messages`
flow that:

1. Accepts a list of `tmpimg://<id>` refs in the request (in addition to
   the existing data-URI `images` list — coexistence, not displacement).
2. **Before the existing vision gate at `daemon/routers/messages.py:222-231`**,
   fetches each ref via the phase-1 GET endpoint (internal, in-process —
   no HTTP roundtrip), and invokes the **proven** `explain_image` tool
   (`daemon/tools/image_tools.py:498-532`) to convert each image into
   a text description using the daemon-global `model_vision` routing
   (`daemon/graph.py:8092-8105`).
3. **Persists the URL refs** in `MessageQueue.images` (JSONB column) for
   **audit** — the row carries refs alongside the message text but is NOT
   the display read source (display reads via the checkpoint kwargs
   stamp + `serialize_message` union, see Components #9b). Phase 5 (FE)
   reads the rendered refs via GET /messages (post-union) for thumbnail
   rendering.
4. Delivers the **TEXT description** as the agent-facing message content
   — the chat model is **NOT** switched to vision; the main agent receives
   plain text.
5. **Coexists** with the existing data-URI multimodal path (Discord,
   Telegram, Slack — e.g. `daemon/sources/adapters/discord/adapter.py:1049-1105`)
   and with the existing `set_injection` text-only path
   (`daemon/manager.py:2734-2808`).
6. Falls back **gracefully** when conversion fails (timeout, error) —
   the message STILL delivers with a `[Image attached — description unavailable]`
   placeholder so users never see a silent drop.

The chat-source invariant (per RAG finding #3) is preserved:
**messages carrying unknown metadata OR images always ride the durable queue** —
images can never inject mid-turn through `set_injection` today
(`daemon/sources/registry.py:78-94` — "No images" predicate in the
text-only injection gate).

---

## Scope

### In scope

- New request field on `MessageCreate` (`daemon/models/message.py:11-72`):
  `image_refs: list[str] | None = None` (list of `tmpimg://<id>` or bare
  `<id>`; ≤3 per request; mirrored on the existing `images` count cap).
- A conversion pipeline that runs **BEFORE** the existing vision gate
  (`daemon/routers/messages.py:222-231`) — specifically between the
  empty-content validation (~`:210-220`) and the slash-command intercept
  (`daemon/routers/messages.py:233+`), so the slash-command path is
  untouched and the gate still rejects **data-URI** images when
  `model_vision` is unset.
- Persistent storage of refs in `MessageQueue.images` (the existing
  JSONB column, observed at `daemon/routers/messages.py:558` —
  `images=message.images` flow). The new field threads the same column;
  schema is **unchanged**.
- **Two coexistence paths**:
  - **Path A (existing data-URI)**: `message.images` non-empty →
    existing vision-gate path is unchanged (`daemon/routers/messages.py:222-231`
    still validates `model_vision` is configured). **Do not displace.**
  - **Path B (new ref-based)**: `message.image_refs` non-empty →
    conversion seam runs → description prepended to message content →
    `message.images` is forced to empty for downstream consumers →
    no `model_vision` requirement at the router level (the conversion
    internally uses the daemon-global vision routing at
    `daemon/graph.py:8092-8105`, which is configured independently).
  - Both paths may coexist in the same request **only if they don't
    overlap** (caller picks one — `image_refs` XOR `images`).
- **Graceful fallback**: if `explain_image` returns `"Error: ..."` (the
  STRING path, `daemon/utils.py:695, :701`) or `None` (`daemon/tools/image_tools.py:530-531`) — log a
  WARNING, substitute placeholder text, message STILL enqueues. The
  converter wrapper (Task 2 amendment #9) collapses via `ok = result
  is not None and bool(result) and not str(result).startswith("Error")`
  so error text never ships as a description.
- **Worker-pool safety**: each `explain_image` call invokes
  `invoke_agent_and_wait` (`daemon/utils.py:588-599`, timeout = `TMP_IMAGE_CONVERSION_TIMEOUT_S = 90.0`,
  Task 2 amendment #8) which uses the invoke semaphore
  (`daemon/utils.py:560-569`, cap = `max(1, WORKER_POOL_SIZE - 1) = 4` with default pool).
  Per-image conversion is serialized through this semaphore, so a
  multi-image POST cannot deadlock the worker pool. The invoke semaphore
  IS the per-POST concurrency guard (architect §5.5 — no additional
  POST-level guard needed). Placement: sync-in-POST, fixed (architect
  §4 ratification; no env knob per amendment #12).

### Out of scope (flagged for follow-up)

- ~~**The 202-injection images-drop defect** — the round-1 narrative (false mechanism + rejected-S3 option) is superseded by Task 16 (h4-S1, amendment #31): `set_injection(..., image_refs=...)` + drain-site kwargs stamp + serializer union closes BOTH the pre-existing 202 images-drop defect for legacy data-URI sends AND the 202 display gap for ref-sends. Live acceptance = A9 + A10 tests. **Pointer to Task 16 only — keep this bullet as a 1-line historical stub.**~~ Replaced with: **see Task 16 (h4-S1)** — closes the gap in v1.
- **The existing `MessageCreate.images` field validator** —
  unchanged. The data-URI pattern (`daemon/models/message.py:8`,
  `validate_images` at `:26-63`) stays strict. The new `image_refs`
  validator is **separate** (ref format only, no base64 size math).
- **Discord/Telegram/Slack source-side adoption of the new flow** —
  sources today send **HTTPS URLs** in `IncomingMessage.images`
  (`daemon/sources/adapters/discord/adapter.py:1105`). They do NOT
  use data-URI or `image_refs`. The phase 1 GET endpoint CAN serve
  these URLs through the chat-source path, but doing so requires
  a per-source adapter change to download the URL → write to
  `TmpImageStore` → emit a ref. **Out of scope for v1** —
  flag for a future "source-side image unification" effort. The
  coexistence requirement here is **only**: do not break the
  existing data-URI path that the chat-source live-injection gate
  bypasses (`daemon/sources/registry.py:78-94` excludes them from
  injection anyway).
- **Multimodal `HumanMessage` construction** — today's vision-support
  flow builds a multimodal content block list
  (`.agents/shared/planning/vision-support/decisions.md DEC-002`).
  The new flow does NOT build multimodal blocks — the chat agent
  receives plain text. Vision-routing recommendations F1/F2
  (`daemon/graph.py:7032-7051` — has_images scan, current location;
  the prior-art plan cites `:342-351` from 2026-05-30 when the
  function lived elsewhere) remain **untouched**. No call site
  change is needed because the new flow's `message.images` is
  always empty.

---

## Components (file:line anchors — verified)

| # | Component | Anchor | Notes |
|---|---|---|---|
| 1 | Existing request model (`MessageCreate`) | `daemon/models/message.py:11-72` | Add sibling field `image_refs: list[str] \| None = None` with its own validator (≤3, **C3-fixed regex**: `^((tmpimg://)\|(/api/tmp_images/))?[a-f0-9]{32}$` — accepts bare 32-hex + `tmpimg://<32hex>` + canonical URL `/api/tmp_images/<32hex>`). Mutually exclusive with `images` (XOR validated in `model_validator`). |
| 2 | Existing vision gate (NOT a target — must skip on ref path) | `daemon/routers/messages.py:222-231` | When `message.image_refs` is non-empty (and `message.images` is None), the gate is **skipped** for the ref path. When `message.images` is non-empty, the gate is enforced as today. Document this skip in a one-line comment to prevent future regressions. |
| 3 | `explain_image` tool (PROVEN conversion seam) | `daemon/tools/image_tools.py:498-532` | Calls `invoke_agent_and_wait(manager, agent_id="image-reader", message=question, images=[data_uri], timeout=300, return_instance_id=True)` at `:513-523`. Returns "Error: ..." string on failure (`:530-531`). **This is the seam to reuse** — no new agent, no new tool. |
| 4 | `invoke_agent_and_wait` invoke semaphore | `daemon/utils.py:560-599` | Cap = `max(1, WORKER_POOL_SIZE - 1)` (`:566`); `WORKER_POOL_SIZE` is the daemon-wide constant from `daemon/constants.py:54`; with `WORKER_POOL_SIZE=5`, the invoke semaphore cap = **4** (architect §5.5 — the semaphore IS the per-POST concurrency guard). The conversion's per-image call uses this lane; per-image timeout is `TMP_IMAGE_CONVERSION_TIMEOUT_S = 90.0` (Task 2, NOT the `image_tools.py:521` 300s literal). Multi-image POST waits 90s × N images at worst (single-image calls are sequential within one POST). |
| 5 | Existing `model_vision` routing (used by `explain_image` internally) | `daemon/graph.py:8092-8105` (`build_instance_llms`); `:7032-7051` (`has_images` scan, agent_node routing) | The conversion calls `image-reader` agent which itself routes through `model_vision` if configured. **Risk**: `model_vision` is rarely configured (per `vision-routing-recommendations/plan.md` F6 — only deployments that explicitly set `OPENAI_MODEL_VISION`). **In CI / test**, `model_vision` is typically unset → conversion may fall back to the standard model. This is acceptable (the standard model can describe images if it has vision, e.g. `gpt-4o`); test with both modes. |
| 6 | `set_injection` text-only gate (chat-source invariant) + round-2 `image_refs` kwarg | `daemon/manager.py:2734-2808` (signature + conditional-add pattern); `_INJECTION_TTL_SECONDS` at `:2732`; `daemon/sources/registry.py:94-95` (`if msg.images: return False` predicate — NO `image_refs` field needed since `IncomingMessage` does not carry one) | **202-injection path is text-only by design for the AGENT channel.** Round-2 amendment #31 (C2 — h4-S1 pulled INTO v1) widens this: `set_injection(..., image_refs=None)` adds `image_refs` ONLY when non-None (byte-identical-when-absent, mirror source/echo_id at `:2784-2797`). Drain site `graph.py:6647-6673` stamps `additional_kwargs["image_refs"]` METADATA ONLY (never content blocks; agent channel stays text-only). All 4 injection-lane consumers verified byte-identical-when-absent (Task 16 / A9). |
| 7 | `manager.enqueue_message` / `enqueue_message_job` | `daemon/manager.py:6795-6891` (`enqueue_message`), `:6893-6905` (`enqueue_message_job`) | Both accept `images: list[str] \| None = None`. Round-2 amendment #27 adds `image_refs: list[str] \| None = None` (keyword-only) to BOTH methods + the service layer + `_prepare_enqueued_message` + `_process_message_with_tracking` (kwarg forwarded at `:7009`). Facade-forwarding discipline per blueprint Core Architecture §Facade-Forwarding Discipline (known bug class). |
| 7b | **InstanceMessagingService** (round-2 amendment #27 chain) | `daemon/services/instance_messaging.py:1977-1990` (`enqueue_message`), `:2151-2163` (`enqueue_message_job`), `:1527-1541` (`_prepare_enqueued_message` signature), `:1688` (row write `images=images`), `:409-532` (`_build_graph_input` kwargs stamp), `:382-406` (`_stamped_additional_kwargs` precedent for the additive-only merge pattern), `:528-532` (HumanMessage construction with `additional_kwargs=stamped_kwargs`) | Service-layer + prepare-layer both gain `image_refs=None` kwarg. **Row**: `images=(image_refs if image_refs is not None else images)` at `:1688` (audit; NOT display source). **Checkpoint kwargs stamp** (`:528-532`): `additional_kwargs={**stamped_kwargs, "image_refs": [...]}` when non-empty (additive-only — preserves byte-identical-when-absent for every existing caller). |
| 8 | Resume path (PAUSED → RUNNING) — round-2 amendment #30 | `daemon/routers/messages.py:313` (`resume_processing_job(..., images=message.images if is_target else None)`); `:342` (`enqueue_message(..., images=message.images)`) | Both call sites gain `image_refs=message.image_refs` (keyword-only, default None) alongside `images`. XOR (Task 3 validator) guarantees at most one non-empty at any call site; legacy byte-identical when `image_refs=None`. |
| 8b | **POST-time echo (202 leg)** — round-2 amendment #31 | `daemon/routers/messages.py:486-491` (`post_echo_msg = HumanMessage(content=..., id=entry.get("echo_id"))`) | Round-2 amendment #31: when the FIFO entry carries `image_refs`, stamp `additional_kwargs={"image_refs": [...]}` on `post_echo_msg` so the optimistic SSE echo carries refs immediately. Same conditional-add pattern as source/echo_id at `manager.py:2784-2797`. |
| 9 | MessageQueue.images column (display persistence) | `daemon/routers/messages.py:558` (`images=message.images` — existing thread); JSONB column on `MessageQueue` model | Round-2 amendment #28: `_prepare_enqueued_message:1688` writes `images=(image_refs if image_refs is not None else images)` — row is durable audit; NOT the display read source (display reads via checkpoint kwargs → serializer union). |
| 9b | **Display read channel — checkpoint sidecar + serializer union (round-2 amendments #28 + #29)** | `daemon/services/instance_messaging.py:409-532` (`_build_graph_input` kwargs stamp); `daemon/utils.py:113-137` (`serialize_message` images extraction); `daemon/utils.py:252+` (`additional_kwargs` stamps — `source` / `context_kind` / `injected_message` precedent) | R1 (round-2): durable leg survives via `additional_kwargs["image_refs"]` round-tripping through LangGraph checkpoint (precedent: source/context_kind/injected_message at `utils.py:218-266`). Serializer extends to UNION `additional_kwargs['image_refs']` into the wire `images` field (single field; legacy `image_url` blocks surface unchanged — reviewer question (ii) ruled legacy display untouched). Identity-grep pin: `image_refs` appears verbatim in the serializer block. |
| 10 | Discord adapter coexistence (data-URI is the OTHER path) | `daemon/sources/adapters/discord/adapter.py:1049-1105` (`images=image_urls or None` at `:1105`) | This adapter feeds `IncomingMessage.images` with **HTTPS URLs** — neither base64 data-URI nor ref. It is a parallel ingestion path; the router never sees it directly. **Coexistence is automatic**: the adapter's path flows through `IncomingMessage → message_queue → Task → agent turn` and the agent turn routes via the existing `has_images` scan at `daemon/graph.py:7032-7051`. **No change required.** |
| 11 | **Chat-source safe-by-construction (round-2 amendment #38)** | `daemon/sources/base.py:25` (`IncomingMessage.images: list[str] \| None = None` — NO `image_refs` field); `daemon/sources/registry.py:94-95` (`if msg.images: return False` predicate) | Sources structurally CANNOT mint refs — the `IncomingMessage` dataclass has no `image_refs` field. Static field-absence test (A8) is the regression pin. The existing `msg.images` predicate covers the only image-bearing field; no exclusion needed for `image_refs` because it's not in the type. Guard comment at `base.py:25` documents the invariant. |
| 12 | **All 4 injection-lane consumer classes (round-2 amendment #31, A9)** | `daemon/routers/messages.py:458` (user-API); `daemon/tools/instance.py:3166` (agent-tool `send_message` — **HEAD-only cite**; line drifted from baseline `307db932` where the analogous call site is ~`:3124`; implementer must locate the live `manager.set_injection(instance_id, message, source=...)` call inside `send_message`'s RUNNING branch — function-name cite preferred); `daemon/sources/registry.py:1029` (chat-source); `daemon/tools/job_queue.py:2471` (job_inject) | None pass the new `image_refs` kwarg → byte-identical entries (mirror `tests/test_injection_slot.py:255-280`). Test A9 verifies all 4 produce identical entries when kwarg is absent. User-API site (`:458`) gains `image_refs=message.image_refs` in v1 (Task 16). |
| 13 | **`_resolve_watcher_model` keyword handling (round-2 amendment #36 probe)** | `daemon/graph.py:8551-8599` (`_resolve_watcher_model` — maps `"quick"` → `config.llm.model_keywords` with `""` fallback) | Image-reader `meta.json` declares `llm_model: "quick"`. Watcher resolver handles the keyword, but image-reader resolves at spawn via a separate `_resolve_model_override` path whose keyword handling is UNVERIFIED. Phase-1 probe (amendment #36) verifies the spawn log shows a RESOLVED model name (not the literal `quick`). Verbatim `quick` = FAIL probe + escalate R2-style. |

---

## Tasks (ordered, with acceptance criteria)

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | **🔴 Verify daemon-global `model_vision` config status in this deployment (architect amendment #14 — probe PROMOTED to phase-1 window; this task retains only the conversion-time fail-fast check).** The manual probe itself is owned by phase 1 (runs at phase-1 contract-freeze time; minutes-cheap; failure escalates BEFORE the {2,3,4} wave dispatch — see phase-1 plan "Cross-phase note"). This task's responsibility narrows to: confirm phase-1's probe result is in the gate-evidence note; if probe failed, the router-seam fail-fast at Task 5 (400 when `model_vision` unset) is what surfaces the breakage loudly to the user instead of silently shipping placeholder text. Read `daemon/config.py` (`LLMConfig.model_vision` field, `OPENAI_MODEL_VISION` env var) and grep the latest prod logs for `[Graph] Vision model configured: <name>` (success) vs `[Graph] No vision model configured` (fallback). | phase 1 (probe result) | Phase-1 probe result captured in gate evidence; conversion-time fail-fast (Task 5) covers the prod-misconfig case at use time. |
| 2 | Add `TmpImageConverter` in a new file (`daemon/services/tmp_image_converter.py`). At module top: `TMP_IMAGE_CONVERSION_TIMEOUT_S: float = 90.0` (architect amendment #8 — **converter-owned constant, NOT the `image_tools.py:521` 300s literal**; tool-context budget ≠ HTTP-context budget; 90s ≈ 5× typical 5–20s; bounds 3-image POST at 270s + overhead). Pass `timeout=TMP_IMAGE_CONVERSION_TIMEOUT_S` to `invoke_agent_and_wait(...)`. Constructor takes `manager: InstanceManager` and `tmp_image_store: TmpImageStore`. Single public method: `async def convert_refs(refs: list[str], request: Request \| None = None) -> list[ConvertedImage]` where `ConvertedImage = (ref: str, description: str, ok: bool)`. Implementation: per image, **store read INSIDE the per-image try** (architect amendment #9): (a) `try: bytes, mime = await tmp_image_store.open(image_id)` → on `FileNotFoundError` return `(ref, "", ok=False)` immediately (vanished-file case — unit test required); (b) build data URI `data:<mime>;base64,<b64>`; (c) call `await explain_image(...)` template (`daemon/tools/image_tools.py:498-532`) via a thin wrapper that takes a data URI directly. **🔴 Error-STRING collapse (architect amendment #9 — must-fix bug class):** `invoke_agent_and_wait` NEVER raises on timeout/agent-error — it returns the STRING `"Error: Agent timed out…"` (utils.py:695) or `"Error: Agent failed…"` (utils.py:701). The wrapper MUST mirror `image_tools.py:530-541`: `ok = result is not None and bool(result) and not str(result).startswith("Error")`; if `not ok`, return `(ref, "", ok=False)` so the fallback placeholder lands in the agent-facing prefix instead of `"Error: Agent failed…"` shipped as a description. (d) Optional disconnect check (architect amendment #11, see Task 4). **Invoke semaphore cap = 4** (`daemon/utils.py:566`: `max(1, WORKER_POOL_SIZE - 1) = max(1, 5-1) = 4`); document this in the converter docstring — the semaphore IS the per-POST concurrency guard (no additional POST-level guard needed per architect §5.5). **O2 reword (round-2):** the citation of `image_tools.py:530-541` is **pattern inspiration, not precedent** — that block is a None-check ONLY (`if result is None: return "Error: image-reader agent timed out or failed…"`), not an error-STRING `startswith("Error")` guard. The `startswith("Error")` collapse is NEW with no precedent; the mirror-citation documents the shape, not a copied guard. | Phase 1 Task 5 | Unit test: happy path (1 image → description); failure path via injected `"Error: Agent timed out after 90s…"` STRING return → `ok=False`, description=""; **vanished-file case** (file deleted between Task 7's ref persistence and converter invocation) → `ok=False`, no exception leak; empty ref list → empty result. |
| 3 | Add a sibling request field `image_refs` on `daemon/models/message.py:11-72`. **🔴 C3 regex fix (round 2)**: validator regex MUST accept all 3 §2 canonical input forms: bare 32-hex `^[a-f0-9]{32}$`, `tmpimg://` alias `^tmpimg://[a-f0-9]{32}$`, and canonical URL `^/api/tmp_images/[a-f0-9]{32}$` → combined pattern `^((tmpimg://)|(/api/tmp_images/))?[a-f0-9]{32}$`. The round-1 regex `^(tmpimg://)?[a-f0-9]{32}$` REJECTED the canonical `/api/tmp_images/<32hex>` form — fixed. ≤3 entries; XOR with `images` (model_validator raises ValueError if both non-empty); empty list coerced to None. | Task 1 | Unit test: 3 valid refs accepted (one of each canonical form); 4 refs rejected; mixed valid/invalid rejected with index; `images` + `image_refs` both non-empty rejected with clear error message; **canonical URL form (`/api/tmp_images/<32hex>`) accepted (regression pin)**. |
| 4 | Add a new module-level function (suggested: `daemon/services/tmp_image_message_hook.py`) `async def pre_dispatch_image_hook(request: MessageCreate, manager, tmp_image_store, http_request: Request \| None = None) -> MessageCreate` that: (a) if `request.image_refs` is empty, returns the request unchanged; (b) calls `TmpImageConverter.convert_refs(refs, request=http_request)`; (c) **optional cheap disconnect mitigation (architect amendment #11)** — BETWEEN per-image conversions, if `http_request is not None and await http_request.is_disconnected()`: convert remaining refs to placeholders (skip conversion), release the invoke lane early, INFO log `"[TmpImageConversion] client disconnected mid-conversion; <remaining> images → placeholder, message will still enqueue"`. **Client disconnect = accept-and-document** (architect §5.4 ruling): Starlette/uvicorn does NOT cancel a running handler on disconnect — the conversion completes and the message enqueues regardless; an aborted FE shows failure; a user retry **duplicates** the message. `asyncio.shield` is meaningless (nothing to shield). Document this in the docstring; do not promise cancellation; (d) builds a `content` prefix from successful conversions (e.g. `[Image 1: <description>]\n[Image 2: <description>]\n`) and a fallback prefix from failures (`[Image 1: description unavailable]\n`); (e) prepends to `request.content` (preserving any existing text); (f) sets `request.images = None` and keeps `request.image_refs` as-is (for downstream display persistence in Task 5). | Tasks 2, 3 | Unit test: 3 successful → prefix has 3 descriptions; 1 success + 2 failures → prefix has 1 description + 2 unavailable placeholders; existing text is preserved after the prefix; `images` cleared. **Disconnect test**: mock `Request.is_disconnected` to return `True` after image 1 → images 2 and 3 become placeholders; INFO log line present; conversion semaphore is released (assert via mock that no further `invoke_agent_and_wait` calls happen). |
| 5 | Wire the hook into `daemon/routers/messages.py` **BEFORE** the vision gate at `:222-231`. Specifically: insert after the empty-content validation (~`:215-220`) and before the vision gate. **🔴 Fail-fast at the TOP of the conversion hook (architect amendment #7 — must land BEFORE tests are written):** when `message.image_refs` is non-empty AND `not manager.config.llm.model_vision`, raise `HTTPException(status_code=400, detail=ErrorResponse(code=ErrorCodes.INVALID_REQUEST, message="image_refs provided but model_vision is not configured. Set OPENAI_MODEL_VISION environment variable or model_vision in config.yaml.").model_dump())` — same `ErrorResponse` shape as the existing gate at `:222-231`, message adjusted to name `image_refs` + `OPENAI_MODEL_VISION`. The fail-fast check sits at the **TOP** of the hook (refuse early, spend zero conversion budget on a doomed request). The hook awaits the conversion; the request body's `content` and `images` are mutated in place. **Skip the legacy vision gate when `message.image_refs` was non-empty** (the hook has already cleared `images`; gate logic at `:222-231` checks `if message.images` and naturally skips — but add an explicit one-line comment to prevent future regressions). | Tasks 1, 3, 4 | **🔴 EXACT phase-2 test assertions (architect §2 — 5 cases):** (1) `model_vision` unset + `image_refs=[ref]` → **400**; `ErrorResponse` shape matches gate at `:222-231`; message references `image_refs`/`OPENAI_MODEL_VISION`. (2) `model_vision` set + per-image timeout STRING return (mock returns `"Error: Agent timed out after 90s…"` per architect §5.6) → 200/202; agent-facing prefix `[Image 1: description unavailable]`; WARNING logged; refs persist in `MessageQueue.images`. (3) `model_vision` set + image-reader spawn failure STRING path (mock returns `"Error: Agent failed…"`) → same as (2), distinct WARNING marker. (4) **Data-URI path unchanged byte-identical**: `images=[data_uri]` + `model_vision` unset → 400 (existing gate); `images=[data_uri]` + `model_vision` set → 200/202. (5) `images` AND `image_refs` both non-empty → 422 (XOR validator, Task 3). Mark this test as REQUIRED-PRESERVE in code-review checklist. |
| 6 | ~~**Decision — Placement**: explicit architect-owned decision (escalated to `decisions.md`). Two options, ONE recommended default. See "Placement analysis" § below. The phase plan implements the **RECOMMENDED** path (synchronous in POST) but flags the alternative in `decisions.md` for sign-off.~~ **🔴 DELETED (architect amendment #12 — no-knob ruling):** the `ENSEMBLE_IMAGE_CONVERSION_MODE` env knob is REMOVED. Placement is **SYNC-IN-POST, fixed** (architect §4 ratification; two-leg coverage verified — durable enqueue leg + 202 RAM-injection leg both pass through the router seam; pipeline alternative needs a SECOND drain-site seam and was rejected). The "Placement analysis" § below is retained as a **decision record** (why sync-in-POST won, what the alternative would have required) but the env knob is gone — daemon-side no-knob ruling. To flip the default, change the router call site (one line) and update tests; no env resolver. | none | The router seam is hard-coded to call `await pre_dispatch_image_hook(...)` synchronously. Unit tests assert the call is awaited before the response. |
| 7 | **🔴 REWRITE (round-2 amendment #26 — C1 two-channel design).** Persist the original ref list into **BOTH channels** (router seam): router calls `result = await manager.enqueue_message_job(instance_id=..., message=prepended_content, source="api", images=message.images, image_refs=message.image_refs, queue_id=message.queue_id)`. Two-parallel-fields docstring on `enqueue_message_job`: "`images` → agent channel ONLY (content blocks, vision routing — NEVER refs); `image_refs` → display channel ONLY (checkpoint sidecar via `additional_kwargs['image_refs']` — NEVER content blocks); XOR on input guarantees at most one non-empty (Task 3 validator)." `_build_message_content` (`manager.py:165-180`, `instance_messaging.py:113-128`) carries ONLY `images` — it **structurally cannot see refs**, which is the round-2 guarantee that vision routing NEVER fires on ref-sends. The round-1 "overload `images=message.image_refs`" instruction is **DELETED** (it would have routed every ref-send to vision via `has_images=True` at `graph.py:7032-7051` — the conflation round-2 fixed). | Tasks 4, 5, 9, 12, 13, h4-S1 | **A2 test (test freeze list):** POST `image_refs=[a,b,c]` → GET /messages returns the 3 canonical ref URLs in `images` field (via the serializer union — Task 13). **A1 test:** POST `image_refs=[r]` → checkpointed `HumanMessage.content` is `str` (no `image_url` blocks); `additional_kwargs['image_refs']` carries refs; agent LLM logs `call_type="STANDARD" / use_vision_model=False` at `graph.py:7032-7053`. **A3 test:** MessageQueue.images row == canonical ref URLs for that message_id (audit; NOT display read source). **A4 test:** legacy data-URI send → content IS block list, `additional_kwargs['image_refs']` absent, wire `images` = data URI, vision gate unchanged. **A5 test:** both fields non-empty → 422 (XOR). **Byte-identical-when-absent** for callers not passing `image_refs`. |
| 8 | **Graceful fallback verification (architect amendment #10 — must cover the STRING path, not only exceptions):** write unit tests that (a) force `explain_image` to return the STRING `"Error: Agent timed out after 90s. Instance abc... may still be running."` (`utils.py:695-697` literal) — the wrapper must collapse this via the `not str(result).startswith("Error")` guard (Task 2 amendment #9), the message STILL enqueues with `[Image attached — description unavailable]` placeholder, WARNING logged. (b) Force the STRING `"Error: Agent failed. <reason>"` (`utils.py:701` literal) — distinct WARNING marker (e.g. prefix `[TmpImageConversion] image-reader agent failed: ...`). (c) Force an actual `asyncio.TimeoutError` exception (legacy shape; kept for backward-compat) — message STILL enqueues, WARNING logged. The exception case is **not** the primary defense (architect §5.6 — `invoke_agent_and_wait` does not raise in practice); the STRING collapse is. | Tasks 2, 4 | All three tests pass; WARNING log emitted with the right marker per case; no exception propagates to the HTTP client; agent-facing prefix is `[Image 1: description unavailable]` in all three cases. |
| 9 | **Worker-pool coupling verification**: write a test that POSTs 3 images while the worker pool is saturated (mock `invoke_agent_and_wait` to acquire the invoke semaphore and hold it). The conversion MUST NOT deadlock; the third image waits for the second (sequential acquisition). | Tasks 2, 4 | Test passes; semaphore acquisition is sequential; pool survives (no crash). |
| 10 | **🔴 REQUIRED (round-2 amendment #27 — was contingent in round-1).** Full facade-forwarding for the new `image_refs` kwarg across the 5-function chain (C1 ruling a): router `messages.py:554` call → `InstanceManager.enqueue_message_job` (`daemon/manager.py:6893-6905`) → `InstanceManager.enqueue_message` (`daemon/manager.py:6795-6808`, internal callers) → `InstanceMessagingService.enqueue_message` (`daemon/services/instance_messaging.py:1977-1990`) AND `enqueue_message_job` (`daemon/services/instance_messaging.py:2151-2163`) → `_prepare_enqueued_message` (`daemon/services/instance_messaging.py:1527-1541`, row write at `:1688`) → `_process_message_with_tracking` (`daemon/manager.py:6944-6956`, kwarg forwarded at `:7009`). **Each layer adds `image_refs: list[str] \| None = None` (keyword-only, default None — preserves byte-identical-when-absent contract for every existing call site).** Guard tests mirror `tests/unit/test_manager_enqueue_message_work_id_required.py` 5-test pattern (kwarg forwarded, default None, keyword-only, default behavior unchanged, empty list treated as None) for **BOTH** facade methods (`enqueue_message` AND `enqueue_message_job`) + a real-dispatch integration test mirroring `tests/integration/test_job_driven_enqueue_work_id_facade.py` proving the kwarg survives router → facade → service → `_prepare_enqueued_message` → row + checkpoint kwargs stamp. Cite the facade-forwarding discipline (Core Architecture §Facade-Forwarding Discipline — known bug class slips past AsyncMock + `inspect.getsource` substring assertions). | Phase 2 design (always-on; no longer contingent) | **A6 test (test freeze list):** 5-test pattern × 2 facade methods. **A7 test:** real-dispatch integration test — `image_refs` survives the full kwarg chain. If a kwarg is missing at any layer, the test fails with a clear message naming the dropped layer. |
| 11 | **Chat-source coexistence audit + Sources MUST NOT add `image_refs` (round-2 amendment #38).** Confirm `daemon/sources/registry.py:94-95` `msg.images` predicate is unchanged (covers the only image-bearing field on `IncomingMessage`). Add one-line guard comment at `daemon/sources/base.py:25`: `# Sources must NOT add `image_refs` — refs are POST-only`. **Static field-absence assertion test (A8)**: `dataclasses.fields(IncomingMessage)` does NOT include `image_refs` (catches accidental addition by a future contributor — safe by construction). **🟡 Implementation touchpoint for Task 16 (h4-S1) — phase-2 implementer MUST update the stale "text-only" comment at `daemon/sources/registry.py:78-82`** when the round-2 amendment #31 lands (the injection lane now carries `additional_kwargs["image_refs"]` metadata in addition to text content; comment claims "set_injection is text-only" which is true for the AGENT channel but stale for the display-metadata channel). Update the comment to clarify "set_injection is text-only on the agent channel; image_refs (if present) is display-metadata via additional_kwargs". | none | A8 test: dataclass fields enumeration does NOT contain `image_refs`; comment at `base.py:25` present; **`registry.py:78-82` comment update lands with the Task 16 PR** (verified by code-review checklist item: "registry.py text-only comment reconciled with h4-S1 display metadata"). |
| 12 | **202-injection display gap acknowledgment (round-2 supersedes):** ~~add a docstring in the phase-overview (NOT code) noting that the 202 path's display refs are NOT shown until the agent turn drains~~. **🔴 SUPERSEDED by Task 16 (h4-S1)** which actually closes the gap AND the pre-existing 202 images-drop defect (round-2 amendment #31 — `set_injection` kwarg + drain-site stamp + POST-time echo). This task is kept as a 1-line historical pointer only; the live acceptance is Task 16's tests (A9 + A10). | n/a | n/a (superseded; pointer to Task 16) |
| 13 | **Implementation mapping — kwarg chain + kwargs stamp (round-2 amendment #28).** Code-level mapping for the `image_refs` thread (mirrors amendment #27's facade layer, but covers the DATA PATH into the checkpoint + row): (a) **Row column**: `_prepare_enqueued_message` (`daemon/services/instance_messaging.py:1527-1541`) writes `images=(image_refs if image_refs is not None else images)` at `:1688` — row = durable audit, NOT the display read source. (b) **Checkpoint kwargs stamp**: extend `_build_graph_input` (`daemon/services/instance_messaging.py:409-532`) to merge `{"image_refs": [...]}` into the `HumanMessage.additional_kwargs` (additive-only when non-empty — pattern from `_stamped_additional_kwargs` at `:382-406`; preserves byte-identical-when-absent). At `:528-532` the existing `HumanMessage(content=..., id=message_id, additional_kwargs=stamped_kwargs)` gains `additional_kwargs={**stamped_kwargs, "image_refs": [...]}` when `image_refs` non-empty. (c) **Documented fallback**: if LIST-typed `additional_kwargs` values don't round-trip the LangGraph checkpoint, amend the stamp to a JSON string — the kwargs→row-join fallback is **REJECTED** (R2 analysis — N extra reads per page, two sources of truth, SSE echo path structurally cannot use it at `instance_messaging.py:4025-4051`). A1/A3 end-to-end assertions (test freeze list) prove either way. (d) **Provenance stamps precedent**: `source` / `context_kind` / `injected_message` round-trip through checkpoint today (`utils.py:218-266` reads them back). | Task 10 | Unit test: kwargs stamp adds `image_refs` only when non-empty; round-trip through `serialize_message` (Task 14) surfaces them. Integration test: enqueue with `image_refs=[r]` → `HumanMessage` checkpoint has `additional_kwargs["image_refs"] == [r]`; row `images` column == `[r]` (audit). |
| 14 | **`serialize_message` union extension (round-2 amendment #29).** Extend `daemon/utils.py` `serialize_message` (`:113-137` extracts images; `:252+` reads `additional_kwargs` stamps) to UNION `additional_kwargs.get("image_refs")` into the wire `images` field (single field; legacy `image_url` blocks surface unchanged — reviewer question (ii) ruled legacy display untouched). Identity-grep pin that `image_refs` appears VERBATIM in that block (catches a future "simplification" that drops the union — the serializer-simplification hazard). Comment in code cites this round-2 ruling. | Task 13 | **A2 / A4 / A10 tests:** POST `image_refs=[r1,r2]` (durable leg + 202 leg) → GET /messages shows refs in `images`. POST data-URI → `images` = data URI (unchanged). Identity-grep test: `grep -n "image_refs" daemon/utils.py` returns the union line (regression pin against "simplification"). |
| 15 | **PAUSED/resume branch threading (round-2 amendment #30).** Update `daemon/routers/messages.py:313` (`resume_processing_job(..., images=message.images if is_target else None)`) AND `:342` (`enqueue_message(..., images=message.images)`) to thread `image_refs=message.image_refs` alongside `images`. XOR (Task 3 validator) guarantees at most one non-empty at any call site; legacy byte-identical when `image_refs=None`. | Tasks 3, 10 | Integration test: PAUSED cascade resume with `image_refs=[r]` (target) → row carries refs (Task 13 audit); non-target children receive no refs. Legacy data-URI path threads `images=message.images` byte-identical. |
| 16 | **🔴 h4-S1 — `set_injection` `image_refs` kwarg + drain-site stamp + POST-time echo + tests (round-2 amendment #31 — C2 h4 pulled INTO v1).** (a) `manager.py:2734-2740` `set_injection(...)` signature gains `image_refs: list[str] \| None = None` (keyword-only, default None). FIFO entry adds `image_refs` ONLY when non-None (byte-identical-when-absent — mirror source/echo_id conditional add at `:2784-2797`; new conditional add AFTER the `echo_id` block). (b) Drain site `graph.py:6647-6673` stamps `additional_kwargs["image_refs"]` on the injected HumanMessage — METADATA ONLY, never content blocks (agent channel stays text-only; `langchain_openai` does not serialize `additional_kwargs` to the wire at `:6657-6661`). Use the same `extra_kwargs: dict[str, Any] = {"injected_message": True}` shape, conditionally add `"image_refs": entry["image_refs"]` only when the entry carries it. (c) Router `messages.py:458` passes `image_refs=message.image_refs`; POST-time echo `:486-491` builds `post_echo_msg = HumanMessage(content=..., id=entry.get("echo_id"))` and adds `additional_kwargs={"image_refs": [...]}` when entry has refs. (d) Verify `set_injection` has NO facade seam — it lives directly on `InstanceManager` (not behind `InstanceMessagingService`); document in the PR. (e) **Byte-identical-when-absent tests** for all 4 consumer classes: `messages.py:458` (user-API), `tools/instance.py:3166` (agent-tool `send_message`), `sources/registry.py:1029` (chat-source), `tools/job_queue.py:2471` (job_inject). Mirror the conditional-add pattern from `tests/test_injection_slot.py:250-280`. (f) **Bonus acceptance (closes pre-existing 202 images-drop defect)**: legacy data-URI send on RUNNING target → 202 → drain → `additional_kwargs['images']` carries the data URI (the prior bug was `messages.py:458` dropping `message.images` entirely — now both `images` and `image_refs` thread through if non-None). (g) **Documented escape**: if drain-site stamp violates a live-turn invariant the analysts missed, fall back to S4 (re-scope to live-session-only); never widen S1. | Tasks 10, 13 | **A9 test (byte-identical × 4 consumers):** all 4 call sites produce identical entries without `image_refs` kwarg (mirror `tests/test_injection_slot.py:255-280`). **A10 test:** 202 leg end-to-end — RUNNING target + POST `image_refs=[r1,r2]` → drain → GET /messages carries refs in `images` (via Task 14 union). **Bonus test:** legacy data-URI on RUNNING target → 202 → drain → images persist (pre-existing defect closure). |

---

## Placement analysis (architect decision)

> **Status**: **DECIDED — sync-in-POST** (architect §4 ratification, two-leg coverage verified: durable enqueue via `_process_message_with_tracking` at `daemon/instance_messaging.py:2585` + content build `:4042`, AND 202 RAM-injection via `set_injection` at `daemon/routers/messages.py:442-458` — both pass through the router seam; pipeline alternative needs a SECOND drain-site seam → two seams, two failure surfaces, two test matrices). The architect ruled **no env knob** (architect §8.12 — `ENSEMBLE_IMAGE_CONVERSION_MODE` is deleted, see amendment log). To flip the default, change the router call site (one line, hardcode `await pre_dispatch_image_hook(...)`) and update tests; no env resolver. This section is retained as a **decision record** for future maintainers.

### Option A — Synchronous in POST (RATIFIED)

**Flow**: User sends image → POST /messages → **fail-fast check** (`image_refs` non-empty AND `not model_vision` → 400) → `pre_dispatch_image_hook` awaits conversion (per-image 90s cap, invoke-semaphore 4) → description prepended to content → enqueue_message_job → 200/202 → done.

| Aspect | Effect |
|---|---|
| **HTTP latency** | +90s worst-case per image (architect §5 ratification — `TMP_IMAGE_CONVERSION_TIMEOUT_S = 90.0`). 3-image POST could take 270s wall-clock + overhead. **Mitigation**: gate to ≤3 images (phase 1 cap); reject early in tests by stubbing the explain_image call; FE phase-4 adds rxjs `timeout(300_000)` on the messages POST + typed send-failure on timeout + NEVER auto-retry (architect amendment to phase 4 — server completes the handler regardless, retry duplicates the message). |
| **Worker-pool invoke-lane coupling** | Each image blocks one invoke semaphore slot (`max(1, WORKER_POOL_SIZE - 1)` from `daemon/utils.py:566`). With `WORKER_POOL_SIZE=5`, max 4 simultaneous image conversions across the daemon. **The semaphore IS the per-POST concurrency guard** (architect §5.5 — no additional POST-level guard needed; budget-as-queue-wait under saturation degrades to placeholder + delivery, graceful by construction). Document the 4-cap in the converter docstring. |
| **FE provisional UX** | User sees a spinner / "converting images, this may take a minute" until the 200/202 arrives (architect phase-4 amendment). Simple, predictable; never auto-retry on timeout. |
| **Ordering** | Text is ready BEFORE the agent turn starts — the agent reads `[Image 1: <desc>]\nUser: <text>`. **Accepted-risk**: a later text-only message B may overtake A on both legs (see Risk #8 below). |
| **Failure surface** | If conversion times out (STRING path), user sees "[Image 1: description unavailable]" in the bubble immediately. No async error to surface later. Client disconnect = message STILL enqueues (architect §5.4 — Starlette doesn't cancel; retry duplicates). |
| **Implementation** | Reuses the existing `explain_image` tool with zero new agent/tool code. |
| **Strand-trap risk** | None — no `message_queue` rows written outside the standard `enqueue_message_job` flow. |

### Option B — Pre-dispatch pipeline step (REJECTED for v1, RATIFIED-rejection)

**Flow**: User sends image → POST /messages → refs persisted as a `message_queue` row + PENDING Task (in one transaction) → 202 immediately → background dispatcher claims the Task → calls explain_image (per-image 90s) → updates the message row with the description + a follow-up Task to wake the agent. **REJECTED** by architect §4 for v1 because:

- **Two-seam coverage failure**: durable enqueue leg + 202 RAM-injection leg both bypass a pipeline hook (only the router seam covers both — see Decision row above). Pipeline alternative needs a SECOND drain-site seam → two seams, two failure surfaces, two test matrices. The architect ruled this Worse than sync-in-POST's loud latency.
- **Strand-trap risk**: a missed `Task` row strands the message forever (wedge-class V — `daemon/services/child_reports.py:3136-3247`, `message_processing_pipeline.py:611-660`). The pre-dispatch path needs the same row+Task+notify invariant that production message enqueue already enforces. If the conversion hangs, the message sits unprocessed for up to 90s × N images per conversion attempt — visible as a stalled chat bubble.
- **Display ordering**: the user sees the image bubble appear immediately (refs in `message_queue.images`), but the **text** description appears later (after conversion). The agent may also start its turn before the description is ready, leading to "the agent didn't see the image" UX failures.
- **Worker-pool coupling**: more invasive — needs a new dispatcher service, new task type, new failure-recovery path.
- **Implementation**: requires a new pre-dispatch service, new Task variant, new row update path. Larger surface area for defects.

### What would change if architect flips the default (decision-record only — not a planned path)

If a future architect reverses the ruling and selects Option B, phase 2 changes as follows:

- Tasks 4, 5: instead of awaiting conversion in the router, **enqueue first, return 202 immediately**. A new pre-dispatch service (modeled on phase 3's cleanup service — interval-driven, claim a row, convert, update row) takes ownership.
- Task 8: graceful fallback becomes "agent may see the original ref → fetches via GET endpoint → explains itself" — a different pattern, requires FE support.
- Tasks 9, 10: tests rewrite for the pre-dispatch flow.
- **Out-of-scope changes**: the FE's "image attached — description unavailable" UX moves from "shown immediately on conversion failure" to "shown only after pre-dispatch gives up". Worse UX.

The flip requires a NEW architect ruling + new plan-overview entry + breaking the no-knob commitment. Documented here only for traceability; no current path implements it.

---

## §rejected (round-2 amendment #32 — S3 POST-time row for 202 leg)

**S3 — POST-time MessageQueue row for the 202 leg:** REJECTED. The architectural alternative considered was "at POST time for the 202 (RAM-injection) leg, write a `MessageQueue` row alongside the FIFO entry so the FE can query the row immediately for display refs." Rejected because:

- **Wedge-class V strand trap** (the same class that bit `daemon/services/child_reports.py:3136-3247`): a `MessageQueue` row written WITHOUT a paired `Task` row in the same transaction + notify-after-commit strands forever — delivery is task-driven only (`daemon/services/message_processing_pipeline.py:611-660`); the legacy counter at `finalizer_counts_as_pending` parks the root at `WAITING_CHILDREN` indefinitely. The strand signature: `'has N pending messages, status=WAITING_CHILDREN (deprecated)'` WHILE gate logs `decision=allowed`. **Repair self-surgery is structurally refused** (`ens_db_repair_execute` cannot target `message_queue`; R14 rejects `'now()'` token). Sanctioned fix is code-level, not DB-level.
- **Only strand-safe shape:** `_prepare_enqueued_message:1977` writes the atomic `MessageQueue + Task + Event` trio in one transaction. Adding a partial-row path for the 202 leg would need to either (a) duplicate that trio (two writes per POST, race-prone), or (b) write the row at POST time WITHOUT the Task (wedge trap).
- **Strictly more work than S1 (h4-S1, Task 16)**: S1 piggybacks on existing infra (`set_injection` kwarg + drain-site stamp + serializer union). S3 needs a new partial-row invariant + new failure-recovery path + new test matrix.
- **Bonus rejection reason**: S3 doesn't survive reload either (separate "S2 — 202-body-only" rejection noted in round-1 §6 — would need to refetch via SSE which defeats the optimistic-bubble UX).

S1 (h4-S1, Task 16) is the ratified path. If S1's drain-site stamp violates a live-turn invariant the analysts missed, the documented escape is S4 (re-scope to live-session-only), NOT widening S1, and certainly NOT falling back to S3.

---

## Test freeze list (phase-2 gate — pin BEFORE tests are written)

> Per round-2 architect ruling: this table is the **gate**. Every test in `tests/unit/` and `tests/integration/` for phase-2 must pin against these assertions; same-PR test updates for each are REQUIRED. The freeze list supersedes any round-1 task-level test description where they conflict.

| ID | Assertion | Pinned by |
|---|---|---|
| **A1** | **Req-#2 structural guarantee**: ref-send → checkpoint HumanMessage content is `str` (no `image_url` blocks); `additional_kwargs['image_refs']` carries canonical ref URLs; agent LLM invocation logs `call_type="STANDARD"` (`use_vision_model=False`) at `graph.py:7032-7053`. | Tasks 7, 13 |
| **A2** | **Durable-leg display** (CORRECTED Task 7 test): POST `image_refs=[a,b,c]` → GET /messages returns the 3 canonical ref URLs in `images` (via serializer union — Task 14). | Tasks 7, 14 |
| **A3** | **Row persistence**: `MessageQueue.images` == the 3 canonical ref URLs for that `message_id` (audit; NOT display read source). | Tasks 7, 13 |
| **A4** | **Legacy byte-identical**: data-URI send → content IS a block list; `additional_kwargs['image_refs']` absent; wire `images` = the data URI; vision gate behavior at `messages.py:222-231` unchanged. | Tasks 7, 14 |
| **A5** | **XOR**: both `images` and `image_refs` non-empty → 422 (Task 3 validator). | Task 3 |
| **A6** | **Facade-forwarding**: 5-test pattern × BOTH facade methods (`image_refs` kwarg forwarded, default None, keyword-only, default behavior unchanged, empty list treated as None) — `enqueue_message` AND `enqueue_message_job` mirror `tests/unit/test_manager_enqueue_message_work_id_required.py`. | Task 10 |
| **A7** | **Real-dispatch facade integration**: `image_refs` kwarg survives router → facade → service → `_prepare_enqueued_message` → row + checkpoint kwargs stamp. Mirror `tests/integration/test_job_driven_enqueue_work_id_facade.py`. | Task 10 |
| **A8** | **Chat-source safe-by-construction**: `dataclasses.fields(IncomingMessage)` does NOT include `image_refs` (static guard; `registry.py:94-95` `msg.images` predicate covers the only image-bearing field). Guard comment at `daemon/sources/base.py:25`. | Task 11 |
| **A9** | **202-leg byte-identical-when-absent** × 4 consumer classes: `messages.py:458` (user-API), `tools/instance.py:3166` (agent-tool), `sources/registry.py:1029` (chat-source), `tools/job_queue.py:2471` (job_inject). Mirror `tests/test_injection_slot.py:255-280`. | Task 16 |
| **A10** | **202-leg display parity end-to-end**: RUNNING target + POST `image_refs=[r1,r2]` → drain → GET /messages carries refs in `images` (the round-2 amendment #40 reload-smoke test). | Task 16 |
| **A11** | **Phase-5 merge pin unchanged**: existing spec, re-ratified (FE phase-5 plan). | (out of scope; cross-phase pin) |

**Unverified residuals (honest, round-2):**

1. **LangGraph checkpoint round-trip of LIST-typed `additional_kwargs` values** is precedent-inferred (`source` / `context_kind` / `injected_message` are strings; `utils.py:218-266` reads them back, but list-typed values are not proven). **A1/A3 end-to-end assertions are the proof.** If the round-trip fails, the fallback is to amend the kwargs stamp to a JSON string (Task 13 (c) — documented). The **kwargs→row-join fallback is REJECTED** (R2 analysis — extra reads per page, two sources of truth, SSE echo path cannot use it at `instance_messaging.py:4025-4051`).
2. **`_resolve_model_override` keyword handling for `"quick"`** is UNVERIFIED for image-reader's spawn path (architect §6 review iii). The watcher resolver at `graph.py:8551-8599` maps `quick` → `config.llm.model_keywords` with `""` fallback, but image-reader resolves at spawn via a separate path. Phase-1 probe (amendment #36) is the de-risk: assert image-reader spawn log shows a RESOLVED model name (e.g. `gpt-4o-mini`), NOT the literal `quick`. If the probe sees `quick` verbatim, **fail the probe and escalate R2-style BEFORE phase-2 dispatch**.
3. **Hardcoded 3600s sweep interval vs hour boundary**: not directly relevant to phase-2 but listed for completeness; phase-3 owns the sweep timing.

---

## Dependencies

### Backward (depends on)

- **Phase 1** (tmp-image store + GET endpoint) — Task 2's `TmpImageConverter`
  reads bytes via `TmpImageStore.open(image_id)`; no HTTP roundtrip.
- **Existing `explain_image` tool** — `daemon/tools/image_tools.py:498-532`
  is the proven conversion seam.

### Forward (is depended on by)

- **Phase 3** (retention) is independent — phase 3 reaps by mtime; phase 2
  does not change the mtime semantics.
- **FE phase 4** (paste + upload) sends `image_refs` in `POST /messages`;
  the response shape (200/202 + message_id) is unchanged.
- **FE phase 5** (transcript merge) reads the `images` field of each
  message via GET /messages — now populated with `tmpimg://<id>` refs;
  the FE renders thumbnails via GET /api/tmp_images/<ref>.

### Cross-cutting

- **Activated via rebuild + restart**.
- **No DB migration** — the `images` column already exists; this phase
  overloads its semantic meaning (data-URI OR refs).
- **Tool authorization unchanged** — `explain_image` is already in the
  worker's tool allowlist (verify in a Task; not cited in this plan).

---

## Test strategy

> **The round-2 Test Freeze List (§"Test freeze list" above) is the GATE.** Every unit + integration test below maps to one or more A1–A11 IDs. Tests must be pinned **BEFORE** they are written (per architect ruling). Same-PR test updates are REQUIRED for any spec change.

### Unit

| Test | File | Verifies | Freeze-list ID |
|---|---|---|---|
| `test_tmp_image_model_image_refs.py` | `tests/unit/models/` | New field validators; XOR with `images`; **C3-fixed regex accepts all 3 canonical forms** (bare 32-hex, `tmpimg://<32hex>`, `/api/tmp_images/<32hex>`) | A5 |
| `test_tmp_image_converter.py` | `tests/unit/services/` | `convert_refs` happy path; failure path (forced STRING return per `utils.py:695/701` + legacy exception); empty input; **vanished-file case** (file deleted between ref persistence and converter invocation) | n/a (covered by Task 8) |
| `test_pre_dispatch_image_hook.py` | `tests/unit/services/` | Prefix construction (success/failure mix); text preservation; `images` cleared; `image_refs` retained for display; **disconnect mitigation** (mock `Request.is_disconnected` → placeholders for remaining) | n/a |
| `test_messages_router_ref_path.py` | `tests/unit/routers/` | Vision gate skipped when `image_refs` non-empty; vision gate still enforces for `images` non-empty; XOR enforced; **fail-fast 400** when `model_vision` unset + `image_refs` non-empty | A4 |
| `test_manager_enqueue_message_image_refs.py` (round-2 #27 — mirrors `test_manager_enqueue_message_work_id_required.py`) | `tests/unit/` | 5-test pattern × BOTH facade methods (`enqueue_message` AND `enqueue_message_job`): kwarg forwarded, default None, keyword-only, default behavior unchanged, empty list treated as None | A6 |
| `test_set_injection_image_refs.py` (round-2 #31 — mirrors `tests/test_injection_slot.py:255-280`) | `tests/` | Byte-identical-when-absent for all 4 consumer classes (`messages.py:458`, `tools/instance.py:3166` HEAD-only [or function-name cite for live call site], `sources/registry.py:1029`, `tools/job_queue.py:2471`); conditional-add block on `InstanceManager.set_injection` entry mirrors the proven source/echo_id pattern | A9 |
| `test_serialize_message_image_refs_union.py` (round-2 #29) | `tests/unit/utils/` | Identity-grep pin: `image_refs` appears verbatim in the union block; A4 (legacy blocks surface unchanged) + A2 (refs surface via union) | A2, A4 |
| `test_incoming_message_no_image_refs_field.py` (round-2 #38, A8) | `tests/unit/sources/` | `dataclasses.fields(IncomingMessage)` does NOT contain `image_refs` | A8 |

### Integration

| Test | File | Verifies | Freeze-list ID |
|---|---|---|---|
| `test_messages_ref_path_e2e.py` | `tests/integration/` | End-to-end: upload PNG via phase-1 POST → POST /messages with ref → agent turn receives a `HumanMessage` whose content STARTS with `[Image 1: <description>]\n`; **A1 — content is `str`, no `image_url` blocks; `additional_kwargs['image_refs']` carries refs; agent LLM logs `call_type="STANDARD"` (use_vision_model=False)** | A1 |
| `test_messages_ref_path_injection_e2e.py` (round-2 amendment #31, A10) | `tests/integration/` | RUNNING target + POST with `image_refs` → 202 + drain → GET /messages carries refs in `images` (via serializer union — h4-S1 closes the 202 display gap); **reload smoke** (amendment #40): post-reload `images` field is non-null | A10 |
| `test_messages_ref_path_fallback.py` | `tests/integration/` | Forced `explain_image` STRING return (`utils.py:695/701`) → message STILL enqueues with placeholder; WARNING log; refs persist (A3 row assertion) | A3 |
| `test_messages_ref_path_worker_pool.py` | `tests/integration/` | 3-image POST under saturated worker pool → serial semaphore acquisition, no deadlock | n/a |
| `test_job_driven_enqueue_image_refs_facade.py` (round-2 #27 — mirrors `tests/integration/test_job_driven_enqueue_work_id_facade.py`) | `tests/integration/` | Real-dispatch: `image_refs` kwarg survives router → `enqueue_message_job` facade → service → `_prepare_enqueued_message` → row + checkpoint kwargs stamp | A7 |
| `test_messages_ref_path_resume.py` (round-2 #30) | `tests/integration/` | PAUSED cascade resume with `image_refs` (target) → row carries refs (A3); non-target children receive no refs; legacy data-URI threads byte-identical | A2, A4 |

### Facade-forwarding discipline (round-2 amendment #27)

- **REQUIRED, not contingent.** `image_refs` kwarg threads router → `InstanceManager.enqueue_message` (`:6795-6808`) AND `enqueue_message_job` (`:6893-6905`) → `InstanceMessagingService.enqueue_message` (`:1977+`) AND `enqueue_message_job` (`:2151+`) → `_prepare_enqueued_message` (`:1527-1541`, row `:1688`) → `_process_message_with_tracking` (`:6944-6956`, forwarded at `:7009`).
- 5-test guard pattern × BOTH facade methods (`enqueue_message` AND `enqueue_message_job`) + real-dispatch integration test.
- Cite the facade-forwarding discipline (blueprint Core Architecture §Facade-Forwarding Discipline — known bug class slips past AsyncMock + `inspect.getsource` substring assertions).

### Web-automation e2e (tester will run later)

The tester will need:

- A small fixed PNG that produces a deterministic description when `explain_image` is mocked. For real e2e, the description is non-deterministic (LLM output) — Playwright must assert on the **shape** of the prefix (`[Image 1: ...]\n`) and on the `images` field carrying refs after reload (A10 reload-smoke per amendment #40).
- A path to force conversion failure (a deliberately corrupt PNG uploaded via phase-1 POST → the converter raises → fallback text appears).
- **Reload smoke test (amendment #40):** POST `image_refs=[r1,r2]` on RUNNING target (202 leg) → drain → `GET /messages` shows refs in `images` after page-reload; SAME assertion for a legacy data-URI send (pre-existing-defect closure — bonus acceptance criterion).

---

## Phase risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **Facade-forwarding regression** if `image_refs` kwarg is added at the router but not forwarded at any layer of the 5-function chain (round-2 amendment #27 — was contingent in round-1, now REQUIRED) | High (silent kwarg loss; refs vanish mid-chain — agent turns out text-only BUT display surface is empty too; row audit is empty; A1 fails). Bug class slips past unit tests per blueprint Core Architecture §Facade-Forwarding Discipline | Medium | **5-test guard pattern × BOTH facade methods** (`tests/unit/test_manager_enqueue_message_image_refs.py` mirroring `test_manager_enqueue_message_work_id_required.py`) + **real-dispatch integration test** mirroring `tests/integration/test_job_driven_enqueue_work_id_facade.py`. The 5 layers that must forward: `InstanceManager.enqueue_message` (`:6795-6808`) + `enqueue_message_job` (`:6893-6905`) → `InstanceMessagingService.enqueue_message` (`:1977+`) + `enqueue_message_job` (`:2151+`) → `_prepare_enqueued_message` (`:1527-1541`, row `:1688`) → `_process_message_with_tracking` (`:6944-6956`, forwarded at `:7009`). |
| 2 | **`model_vision` config UNVERIFIED in prod** — the conversion relies on `daemon/graph.py:8092-8105` routing to a vision-capable model. If `OPENAI_MODEL_VISION` is unset, `image-reader` fails → conversion returns `"Error: Agent failed…"` for EVERY image → all messages carry placeholder text | High (every chat image fails) | Medium (RAG F6 says most deployments don't set it) | **(a)** Phase-1 probe (Task 1 cross-reference + amendment #36) verifies config status at contract-freeze time; **(b)** **router seam FAIL-FAST at Task 5** — `image_refs` non-empty AND `not model_vision` → **400** with `ErrorResponse` shape matching the existing gate (round-2 amendment #7 — replaces the round-1 graceful-fallback position); **(c)** document the requirement in the OpenAPI schema; **(d)** the legacy fail-open concern is closed by the fail-fast at use time — loud, not silent. |
| 3 | **Conversion timeout cascades** — 3 images × 90s = 270s worst-case HTTP latency. Browsers may abort at 300s (some load balancers at 60s). | Medium (user retries; image drop) | Medium | **(a)** Phase-1 caps to 3 images per POST (existing convention); **(b)** the per-image 90s is the **upper bound**, not expected — typical conversion is 5–20s; **(c)** the FE phase-4 upload spinner must communicate "this may take a minute" + add rxjs `timeout(300_000)` on the messages POST + typed send-failure on timeout + NEVER auto-retry (architect amendments to phase 4 — server completes the handler regardless, retry duplicates the message). **DELETED: 600s per-POST deadline (architect amendment #13)** — dead code: sequential worst case 3×90 = 270s < 600s, so the deadline can never fire before per-image timeouts exhaust. If a POST cap is ever wanted, derive it (`3 × per_image + margin`), never hardcode. |
| 4 | **Existing data-URI path regression** — adding the new `image_refs` field might accidentally relax the vision gate's `images` enforcement | High (silent breaking change for users on data-URI + `model_vision` unset path) | Low (Task 5 explicitly preserves the gate for `images`; **round-2 C1 two-channel design** structurally prevents `image_refs` from reaching `_build_message_content` which carries ONLY `images` at `manager.py:165-180` and `instance_messaging.py:113-128`) | A4 test (legacy byte-identical) + A1 test (agent LLM logs `use_vision_model=False` on ref-send). Mark A1+A4 as REQUIRED-PRESERVE in code-review checklist. |
| 5 | ~~**202-injection display gap** — ref-send on RUNNING target → 202 → text bubble shows immediately; thumbnail appears after turn drains.~~ | (closed) | (closed) | **🔴 CLOSED by round-2 amendment #31 (h4-S1, Task 16).** `set_injection(image_refs=...)` + drain-site kwargs stamp + serializer union (Task 14) → 202-leg display parity end-to-end (A10). **Also closes the pre-existing 202 images-drop defect** (`messages.py:458` previously dropped `message.images` for legacy data-URI sends; the new `image_refs` kwarg pattern includes `images` threading too). Strand-trap risk (wedge-class V) lives in §rejected (S3). |
| 8 | **🔴 NEW (architect amendment #13) — Ordering inversion under sync-in-POST.** While image message A converts (up to 270s for 3 images), a later text-only message B can overtake A on **both legs** (durable enqueue via `_process_message_with_tracking` at `daemon/instance_messaging.py:2585`, content build `:4042`; AND 202 RAM-injection via `set_injection` at `daemon/routers/messages.py:442-458`). B enqueues/injects while A is still converting. | Low (one out-of-order pair, not data loss) | Low (requires exact timing — single burst into the same instance) | **Accepted-risk v1.** FE phase-4's block-send default mitigates **single-user single-tab web ONLY** — cross-source interleaving (e.g. Discord message to the same instance arriving while the user pastes in FE) and multi-tab bypass it. **Follow-up candidates recorded, NOT built:** (a) per-tab `pendingPostLock` via `storage` event to serialize across tabs; (b) daemon-side per-instance POST serialization. Phase 4 plan pins block-send scope as single-user-single-tab (architect amendment #18). The 90s timeout SHRINKS the inversion window vs the 300s literal. |
| 6 | **🔴 NEW (round-2 amendment #38) — Chat-source accidental `image_refs` addition.** A future contributor adds `image_refs` to `IncomingMessage` (`daemon/sources/base.py:25`) thinking it parallels `images`. The chat-source live-injection gate (`registry.py:94-95`) checks `msg.images` but NOT `image_refs` (which doesn't exist yet) → the new field passes through → sources mint refs → invariant breaks. | Medium | Low | **A8 static field-absence test** (`dataclasses.fields(IncomingMessage)` does NOT contain `image_refs`) catches the addition at test time. Guard comment at `base.py:25`: "Sources must NOT add `image_refs` — refs are POST-only". PR review checklist item: "no `image_refs` field on `IncomingMessage`". |
| 7 | **Image→text loses critical visual context** — text descriptions may omit subtle details (e.g. "is this bar chart red or blue?") that the agent needs to make a decision | Medium (agent makes suboptimal choices) | Medium (inherent to image-to-text conversion) | Document the limitation in the user-facing help text ("Image attachments are converted to text descriptions; some visual detail may be lost"). The vision-support data-URI path remains available for callers who need full multimodal context (it requires `model_vision` configured). The two paths coexist by design — callers pick the trade-off they need. |
| 9 | **🔴 NEW (round-2 unverified residual) — LangGraph checkpoint LIST-typed `additional_kwargs` round-trip.** The kwargs stamp at `_build_graph_input:528-532` puts `{"image_refs": [...]}` (list-typed) into `additional_kwargs`. Today only STRING-typed values (`source` / `context_kind` / `injected_message` at `utils.py:218-266`) round-trip through the checkpoint. List-typed is precedent-inferred. | High (refs vanish mid-turn; A1 fails; the `_build_graph_input` stamp would be a silent no-op) | Low (LangGraph's `add_messages` reducer handles list-typed `additional_kwargs` for `tool_call_id`; the analog for image_refs is reasonable but unproven) | **A1/A3 end-to-end assertions are the proof.** If round-trip fails, **fallback**: amend the kwargs stamp to a JSON string (Task 13 (c) — documented). **REJECTED**: kwargs→row-join fallback (R2 analysis — N extra reads per page, two sources of truth, SSE echo path structurally cannot use it at `instance_messaging.py:4025-4051`). |
| 10 | **🔴 NEW (round-2 amendment #36 — unverified residual) — `_resolve_model_override` keyword handling for `"quick"`.** Image-reader `meta.json` declares `llm_model: "quick"`. The watcher resolver (`graph.py:8551-8599`) maps `quick` → `config.llm.model_keywords` with `""` fallback, but image-reader resolves at spawn via a separate `_resolve_model_override` path whose keyword handling is UNVERIFIED. If `OPENAI_MODEL_KEYWORDS` is unset and `_resolve_model_override` doesn't have its own keyword map, image-reader spawn passes the literal `quick` to the OpenAI client → 400/410 from the embedding endpoint or worse, image conversion fails with an opaque model error. | High (every image conversion fails) | Low (most deployments have `OPENAI_MODEL_KEYWORDS` set OR image-reader's resolver has its own quick-map; both are unverifiable today) | **Phase-1 probe (amendment #36) verifies the image-reader spawn log shows a RESOLVED model name (e.g. `gpt-4o-mini`), NOT the literal `quick`.** Verbatim `quick` = FAIL probe + escalate R2-style BEFORE phase-2 dispatch. The probe is the de-risk; without it the {2,3,4} wave's centerpiece is known-dead only at integration gate. |

---

## Open questions (escalated to `decisions.md`)

1. **Placement (synchronous vs pre-dispatch)** — see "Placement analysis"
   § above. Recommended: synchronous in POST. Architect may flip.
2. **Ref semantic overloading on `images` column** — phase 2 stores refs
   in the existing `MessageQueue.images` JSONB column (overloaded with
   data-URI semantic). The FE must distinguish (a) `tmpimg://` prefix →
   GET /api/tmp_images/<id>, (b) `data:image/...` prefix → render
   directly, (c) `https://...` → render directly (Discord adapter).
   Cleaner alternative: add a new `image_refs` JSONB column with a
   migration. **Trade-off**: schema change vs semantic overloading.
   Flagged for `decisions.md`.
3. **Display gap on 202 path** — accept v1 (text-only bubble shows
   immediately; thumbnail after drain) or fix it via `set_injection`
   images kwarg (also fixes the pre-existing defect).
4. **`model_vision` config requirement** — should this feature FAIL FAST
   at boot if `model_vision` is unset and `image_refs` was used in the
   last 24h? Or fail-open per-request with placeholder? Recommended:
   fail-open (consistent with the graceful fallback pattern).
   **🔴 RULED FAIL-FAST (round-2 amendment #7) — superseded.** The fail-fast
   is at the router seam (Task 5): when `image_refs` non-empty AND
   `not model_vision` → **400** with the same `ErrorResponse` shape as
   the existing vision gate (`messages.py:222-231`), message adjusted
   to name `image_refs`/`OPENAI_MODEL_VISION`. The open question is
   closed; no boot-time alarm is needed because the router fails loud
   at use time. Boot-time WARNING can still be added as a future
   operator-UX nicety; not in v1 scope.