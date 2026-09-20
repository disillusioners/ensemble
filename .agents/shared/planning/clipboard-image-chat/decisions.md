# Decision Register: clipboard-image-chat

Date: 2026-09-19
Author: plan-worker-decisions (Worker) — feeds ARCHITECT consultation
Status: Architect-ruled 2026-09-19 — amendments applied. Ruling source of truth: `architecture-recommendation.md` (same dir); its rulings supersede this register where marked.
Scope: THIS FILE ONLY (`decisions.md`). Phase plans (1–6) and plan-overview are owned by other workers.
Branch: `feature/clipboard-image-chat` @ `307db932` (verified: HEAD of the branch is the chat-source live-injection merge).

## Amendment log (post-architect, 2026-09-19)

Rulings applied in place from `architecture-recommendation.md` §8/#21–#24 + §11 risk deltas:
- **#21 Q-f:** vision policy RULED **FAIL-FAST** — 400 at POST when `image_refs` non-empty AND `not config.llm.model_vision`; check at TOP of the conversion hook; `ErrorResponse` shape = existing gate (`messages.py:222-231`), message names `image_refs` + `OPENAI_MODEL_VISION`. Phase-2's fail-open test expectation ARCHIVED as superseded (superseded-by amendment #7; replaced by the 5 fail-fast cases, rec §2). Two-analyst convergence + weighted scores noted (fail-fast 4.28 vs fail-open 2.85).
- **#22 Q-a:** Reversibility paragraph rewritten — the "branch revert" claim was factually wrong (FE and daemon deploy independently, S5). Rollback = **deploy the prior FE bundle** (deploy-time event); old-FE-bundle-against-new-daemon remains the real coexistence lever (sibling field).
- **#23 Q-b:** ordering-inversion accepted-risk note added (both legs; cross-source/multi-tab bypass FE block-send; low likelihood, low blast radius; follow-up candidates RECORDED NOT BUILT).
- **#24 §6:** all six architect-input items marked ANSWERED with one-line outcomes + pointers into the recommendation.
- **Consistency edits:** §4 R2/R3 updated, R15–R19 added, cut-items note added (600s deadline / conversion-mode knob / cleanup kill-switch / POST-level guard); §5 wave structure + probe promotion + phase-1 growth; header status; Q-c/Q-h3 debug gating + 1 GiB store cap + guard-rails (conventions line #25 applied by planner/leader); Q-d ruling noted; Q-e kill-switch DELETED + interval pinned 3600s.

### Round 2 (2026-09-19 — post-review REJECTED → C1/C2 rulings; rulings supersede round-1 ONLY where stated)

Ruling source: `architecture-recommendation.md` §"Post-review rulings (round 2)". Applied in place:
- **#33 S3 clarified:** agent-input exclusion is BY SIGNATURE — `_build_message_content` carries only `images`; the `image_refs` kwarg is never threaded to it (guarantee now testable, A1). The round-1 contradiction between S3 and the old phase-2 Task 7 overload is RESOLVED explicitly in favor of the two-channel ruling (Task 7 rewritten per amendment #26; M2 overload+prefix-strip rejected).
- **#34 h4 + R13 REWRITTEN:** h4 is IN v1 as **h4-S1** (narrow additive `set_injection` kwarg, byte-identical-when-absent per the `InstanceManager.set_injection` conditional-add block at `manager.py:2784-2797`; drain-site metadata-only stamp; POST-time echo stamp; 4 consumer call-sites verified; no facade seam at `set_injection`). R13 amended: 202 display gap CLOSED in v1 by h4-S1; phase-5 merge pin remains independently load-bearing (echo `images: null` clobber predates and survives the serializer union). BONUS recorded: h4-S1 closes the pre-existing legacy 202 images-drop. Rejected alternatives + weights recorded (S3 strand trap, S2 reload loss, S4 re-scope 4.10 vs S1 4.45, decisive axis Maintainability) + the documented ESCAPE (fall back to S4, never widen S1).
- **#35 §6 item 6 updated:** h4 marked ANSWERED "in v1 as task h4-S1"; round-1's §6.6 ruling explicitly marked SUPERSEDED.
- **Two-channel summary block added (§2.1)** — agent channel (`images` kwarg ONLY) vs display channel (`additional_kwargs['image_refs']` on BOTH legs) vs read side (`serialize_message` union, legacy blocks unchanged — reviewer (ii)); reviewer (i) chat-source no-exclusion (safe by construction, A8 + guard comment at `base.py:25`); facade-forwarding note (amendment #27 makes Task 10 REQUIRED — round-1 contingent framing superseded; `set_injection` has NO facade seam).
- **#41 merge-gate checklist added (§2.2, canonical copy)** — grep all §2 contract-family tokens across all phase files, diff vs §2 + round-2 rulings; Task 3 regex accepts all three input forms; `grep "tmp-images\|image_b64\|ref_id"` must return zero hits; each phase file's exit criterion references this checklist.
- **O1 env normalization:** `SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS` (+ field `tmp_image_cleanup_retention_days`) everywhere; interval knob aligned to the same cleanup family (`SERVICES_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS`) — matching already-normalized phase3-plan.md.
- **O5 anchor nits:** `_INJECTION_TTL_SECONDS` at `manager.py:2732` (comment block `:2715-2732`); echo mint `str(uuid.uuid4())` at `messages.py:454`; SPA catch-all returns 404 JSON for api/ws/vscode-prefixed paths (`api.py:2619-2624`), not index.html — recorded in §0 "Round-2 anchor corrections".
- **§5 round-2 DAG note:** wave shape unchanged; optional 2a/2b split (2a conversion+durable-leg → 2b h4-S1 202-leg, serial, ≤3 envelope); h4-S1 NOT in phase-1 window (depends on conversion seam minting canonical refs); probe stays in phase-1 window and now also verifies the `quick` alias resolves to a real model (review iii — `_resolve_model_override` keyword handling unverified).
- **Polish pass (cycle-2 review APPROVED-WITH-NOTES, mechanical):** (1) `set_injection` anchor family converted to function-name cites (`InstanceManager.set_injection` signature `:2734-2740` + conditional-add `:2784-2797`) with git-verified dual-pin stable across `307db932`/`5f453b93`/HEAD `0b504b5e`; the reviewer-claimed `:2560/:2362` anchors are git-DISPROVEN at every checked commit and were NOT written (§0 polish-pass anchor rule records this). (2) Archived §6 h4 question self-labels SUPERSEDED inline. (3) R4 absolute replaced with the three named surviving hazard shapes (S4-fallback / union regression / text-only echo paths). (4) Authorized exception E4c: `architecture-recommendation.md` ruling-block anchor citation converted to function-name + dual-pin — citation tokens ONLY.

---

## 0. Anchor Verification Notes (read this first)

Every file:line anchor in this register was **read and verified on 2026-09-19** against the working tree. Three anchors circulating in the task brief / prior docs are STALE — corrected anchors are used throughout:

| Claimed anchor | Actual (verified) | What is there |
|---|---|---|
| `manager.py:2379-2382` (set_injection) | **`InstanceManager.set_injection`** — signature `daemon/manager.py:2734-2740`, conditional-add block `:2784-2797` (dual-pin verified STABLE at baseline `307db932`, `5f453b93`, and HEAD `0b504b5e` — the definition has not moved) | `set_injection(self, instance_id, content, source=None, echo_id=None)` — **no `images` param** (defect confirmed, anchor moved). The stale `:2379-2382` is now the `command_dispatcher` property docstring; the stale citation also appears in `.agents/shared/planning/message-display-latency/architecture-recommendation.md:21` (written 2026-08-30, pre-move). |
| `graph.py:342-351` (has_images scan) | **`daemon/graph.py:7032-7053`** | The per-invocation vision-routing scan (`has_images` over message content blocks, `use_vision_model` selection at `:7047`). `:342-351` is a tool-pairing helper docstring. |
| `sse.service.ts:473-476` (path) | **`frontend/src/app/services/sse.service.ts:473-476`** (under `services/`, not `sse/`) | `mapToMessage` images filter — `img.startsWith('data:image/')` at `:475`. Single-prefix, fail-closed. |

Additionally verified and load-bearing below:

- Discord live consumer: `daemon/sources/adapters/discord/adapter.py:1044-1105` — MIME-filtered `image_urls` (`:1049-1055`), `IncomingMessage(images=image_urls or None)` at `:1105`. **Note: these are HTTPS CDN URLs, not data URIs** — the persisted `MessageQueue.images` JSONB is ALREADY heterogeneous in production.
- `MessageQueue.images` JSONB: `daemon/repositories/message_queue/models.py:94-98` (comment says "base64 data URIs" — comment is already inaccurate for Discord rows).
- 202-injection images-drop: `daemon/routers/messages.py:458` — `set_injection(instance_id, message.content, echo_id=echo_id)`; `message.images` discarded.
- Vision gate: `daemon/routers/messages.py:222-231` — `if message.images and not manager.config.llm.model_vision: 400`.
- Invoke semaphore: `daemon/utils.py:557-569` — cap `max(1, WORKER_POOL_SIZE - 1)` at `:566`.
- Conversion template: `daemon/tools/image_tools.py:498-523` — `invoke_agent_and_wait(agent_id="image-reader", images=[data_uri], timeout=300.0)` (`:513-523`, timeout literal at `:521`).
- `invoke_agent_and_wait`: `daemon/utils.py:588-597` — `images` param `:595`, forwarded to `enqueue_message` (docstring `:615-619`, forward at `:681`).
- SPA catch-all: `daemon/api.py:2612` (`@app.get("/{path:path}")`, api/ws/vscode prefix skip at `:2619-2620`). Route-order precedent: the `/vscode` mount had to be MOVED before the catch-all (`daemon/api.py:1318-1330` comment explains mount-appends-to-end shadowing).
- Sweep precedents: `daemon/config.py:1474-1533` (orphan-watcher interval + grace knobs, `SERVICES_*` env, fail-fast at boot) and `:1550-1558` (job-lock sweep interval). Sweep service shape: `daemon/services/job_lock_sweep.py` (ALWAYS-ON, no kill-switch, boot-wired, shutdown-mirrored at `daemon/api.py:1669-1682`).
- `MessageCreate` validator: `daemon/models/message.py:8` (`_BASE64_IMAGE_PATTERN`), `:26-63` (≤3, strict data-URI regex, 10MB base64-estimated). **The regex would 422 every new-form ref** — resolution in §2/§Q-2.
- Facade chain: `daemon/manager.py:6795-6801` (`enqueue_message` facade, `images` at `:6801`) → `daemon/services/instance_messaging.py:1977` (durable path: `_prepare_enqueued_message` writes MessageQueue + Task in ONE transaction, then notify — the strand-safe shape); HumanMessage construction `_build_message_content(message, images)` at `instance_messaging.py:4042`; `_build_message_content` at `daemon/manager.py:165-177` / `instance_messaging.py:113`.
- Vision LLM construction: `daemon/graph.py:8093-8105` — `if model_vision:` → vision chat model, else standard model for ALL calls. `model_vision` is daemon-GLOBAL (`daemon/manager.py:461` flows `config.llm.model_vision` into every spawned graph, including image-reader).
- **Runtime config suspicion:** `.env:21` / `.env.prod:25` carry `OPENAI_MODEL_VISION=vision`. No model-alias table resolves the literal `"vision"` (grep of `daemon/config.py` / `daemon/constants.py`: none). Unless this deployment maps it provider-side, the value is a PLACEHOLDER — see Risk R2.

**Round-2 anchor corrections (O5):** `_INJECTION_TTL_SECONDS = 3600` sits at `daemon/manager.py:2732` (the RAM-FIFO threading-contract comment block is `:2715-2732`; the `InstanceManager.set_injection` signature is `:2734-2740`; the byte-identical conditional-add pattern is `:2784-2797`). The POST-time echo_id is minted as `echo_id = str(uuid.uuid4())` at `daemon/routers/messages.py:454` (call-site `:458`; echo emit `:486-504`). The SPA catch-all (`daemon/api.py:2612`) returns **404 JSON** for unmatched `api`/`ws`/`vscode`-prefixed paths (`:2619-2624`) — it is NOT an index.html fallback for those prefixes; SPA serving applies to non-prefixed unmatched paths only. All four injection-lane `set_injection` consumer call-sites re-verified: `messages.py:458`, `daemon/tools/instance.py:3166`, `daemon/sources/registry.py:1029`, `daemon/tools/job_queue.py:2471` — none pass an `image_refs` kwarg today.
**Polish-pass anchor rule (cycle-2 review):** cite `InstanceManager.set_injection` BY NAME — its line-number cites are demonstrably drift-prone (the original `:2379-2382` was stale; a later reviewer claim of `:2560 @HEAD 5f453b93 / :2362 @baseline 307db932` is DISPROVEN by git at every checked commit — `git show <rev>:daemon/manager.py | grep -n "def set_injection"` returns `2734` at baseline `307db932`, at `5f453b93`, and at HEAD `0b504b5e`). Dual-pin only with those verified numbers.
- FE: `message-input.component.ts` — paste `:404`, `processFiles` `:442-476` (data-URI conversion `:464`, client caps `:445`/`:457`), drop `:492-500`; `api.service.ts:310-314` (POST body `{content, images}`); thumbnails `chat-interface.html:193-195` (`@if (message.role === 'user' && message.images?.length)`, `track $index`, **no click handler today** — the popup viewer is genuinely new); SSE whitelist `sse.service.ts:474-476`; merge spread `message-merge.util.ts:160` (`{ ...result[idx], ...msg }` — see Risk R4 for the `images: null` clobber hazard); `serialize_message` emits `"images": images` unconditionally (`daemon/utils.py:213`) — a text-only drained message serializes `images: null`.

---

## 1. SETTLED Decisions (record, do not reopen)

### S1. Existing multimodal data-URI path STAYS
**Decision:** The legacy `images: [data:image/...;base64,...]` transport remains fully functional for all existing consumers.
**Rationale + evidence:** Discord adapter is a LIVE consumer of the durable images path with HTTPS attachment URLs (`discord/adapter.py:1049-1105`, `:1105`); legacy web FE sends data URIs (`api.service.ts:310-314`); the PAUSED auto-resume path threads `message.images` end-to-end (`messages.py:313` `images=message.images if is_target else None`, `:346` fallback enqueue). The new flow is ADDITIVE (sibling field, §Q-2), never a replacement of the wire field.

### S2. NO DB migration — `MessageQueue.images` JSONB suffices
**Decision:** Ref persistence overloads the existing `images` JSONB list (`message_queue/models.py:94-98`); no new table, no new column, no migration in v1.
**Rationale:** The column is an untyped `list[str]` that ALREADY carries three heterogeneous forms in production (web data URIs, Discord HTTPS URLs, and now ref URLs). A dedicated `image_refs` column would buy type clarity at the cost of a migration + dual-read display logic for zero v1 capability. Reversibility is excellent: a later additive `image_refs` column + backfill is a routine migration if lifecycle tracking (per-ref expiry states) is ever needed. (Also the backend phase-2 plan's recommendation — recorded as endorsed.)

### S3. Converted TEXT is the agent-facing and persisted message; main chat model NOT switched to vision
**Decision:** The agent receives text-only content (original user text + converted image descriptions). The main instance's model is never swapped to a vision model by this feature.
**Rationale + evidence (CLARIFIED per round-2 amendment #33 — the guarantee is BY SIGNATURE, testable):** agent-input exclusion is **BY SIGNATURE**: `_build_message_content` (twins at `manager.py:165-177` / `instance_messaging.py:113-128`, called at `:4042`) carries only the `images` kwarg — the `image_refs` kwarg is **never threaded to it** (round-2 C1 ruling (a): separation by signature, NOT by prefix detection). With `images=None` on ref-sends, the HumanMessage content is a PLAIN STRING — the `has_images` scan (`graph.py:7032-7053`) finds no `image_url` blocks, `use_vision_model` stays False (`:7047`), and the standard model serves the turn (testable as A1: content is `str`, invocation logs `call_type="STANDARD"`). Two reinforcing facts: (a) ref URLs are localhost-relative and would be UNFETCHABLE by a hosted provider API even if they leaked as image_url blocks; (b) the requirement (verbatim #2) is "the TEXT is what the agent receives and what persists as the message". The `tmpimg://`-era idea of letting refs ride into the model is dead by construction.
**Round-1 contradiction RESOLVED (explicit):** round-1's S3 ("refs never enter the agent channel") contradicted the old phase-2 Task 7, which overloaded `enqueue_message_job(..., images=message.image_refs)` — that overload WOULD have routed refs down the agent channel (content blocks → `has_images=True` → with fail-fast guaranteeing `model_vision` set, the MAIN agent routes to vision on every ref-send, violating requirement #2). Round-2's two-channel ruling (C1) resolves the contradiction IN FAVOR OF signature separation: Task 7 is REWRITTEN (amendment #26) to pass two parallel fields (`images=` + `image_refs=`), and M2 (overload + prefix-strip at the twins) is REJECTED for silent semantic drift (a future edit re-leaks refs through the `if images:` truthy check) and fragile prefix heuristics (the canonical prefix already moved once — `tmpimg://` demotion).

### S4. Tmp-image dir + 30-day auto-clear is acceptable; image loss after cleanup is OK
**Decision:** Files live in `data/tmp_images/{id}` (phase-1 store) and are deleted after the retention window. Text descriptions persist in checkpoints/message rows; only pixels expire.
**Rationale:** Requirement verbatim (#5) accepts this. Consequence the architect should note: `GET /api/tmp_images/{id}` becomes 404 after cleanup → FE must render an onerror placeholder (phase 6) — this is a DESIGNED degradation, not a bug, and it is why the phase-6-before-phase-3-activation ordering edge exists (§5).

### S5. Branch + activation model
**Decision:** All work lands on `feature/clipboard-image-chat` (based @ `307db932`). Daemon activation = rebuild + restart; FE activation = `npm build` (project convention #3). The FE bundle and daemon MUST both be deployed before the feature is user-visible end-to-end; partial deploys degrade per §5 notes.

### S6. (from dispatcher data) Source-side (Discord/Telegram/Slack) adoption is OUT of scope v1
**Decision:** Chat sources keep their existing transport (data URIs / HTTPS URLs). Coexistence with the new ref flow is automatic — sources never produce refs, and the ref validator (§Q-2) rejects non-ref shapes, so no source can accidentally mint a ref.
**Rationale:** Sources route through `IncomingMessage → message_queue → Task` without touching `MessageCreate`; the ref form only exists on the API request surface. Backend phase-2 Task 11 already audits the chat-source text-only injection invariant (`sources/registry.py:78-94`) — unchanged by this feature.

---

## 2. CANONICAL WIRE CONTRACT (cross-lane arbitration — resolving the phase1↔phase4/5 mismatch)

The two plan lanes diverged. Backend phase-1 pinned `POST /api/tmp_images` (underscore), 32-hex ids, `tmpimg://<id>` canonical ref, batch body, response items `{image_id, ref, content_type, size_bytes}`, GET parse-tolerant. FE phase-4/5 planned `/api/tmp-images` (hyphen), per-image body `{filename, content_type, image_b64}`, response `{ref_url, ref_id}`, ref prefix `'/tmp-images/'`, plus a `DELETE /api/tmp-images/{ref_id}` that **backend phase-1 does not implement** (verified: no DELETE in phase1-plan.md).

**THE CANONICAL CONTRACT (pick this; losing sides rename mechanically):**

| Element | Canonical form | Notes |
|---|---|---|
| Upload | `POST /api/tmp_images` — body `{ "images": [{ filename, content_type, data_base64 }, ...] }`, ≤3 per call | Backend shape AS-WRITTEN (batch). FE adapter sends **batch-of-1** and unwraps `uploads[0]` — this preserves FE's per-chip upload/abort lifecycle with ZERO backend change. |
| Upload response | `{ "uploads": [{ "image_id": "<32-hex>", "ref_url": "/api/tmp_images/<32-hex>", "content_type", "size_bytes" }] }` | Field rename `ref` → `ref_url` on the backend (one line + contract docstring + test). `ref_url` is the canonical emitted ref. |
| Serving | `GET /api/tmp_images/{image_id}` — raw bytes, stored MIME, `Cache-Control: private, max-age=3600`, weak ETag | Backend AS-WRITTEN. MUST be registered in `create_app` BEFORE the SPA catch-all (`daemon/api.py:2612`); first-match-wins + the `/vscode` shadowing precedent (`api.py:1318-1330`) make late registration a real defect, not a style nit. Parse-tolerant of `tmpimg://<id>` and bare `<id>` input (kept, harmless). |
| Delete | `DELETE /api/tmp_images/{image_id}` → 204, idempotent (204/404 on missing) | **REQUIRED BACKEND ADDITION** — store `.delete()` already exists (phase-1 Task 2); only the route + test are missing. FE phase-4 Task 7 (eager release on chip remove) 404s noisily without it and orphaned uploads accumulate to the 30-day sweep. Owner: backend phase-1 (smallest delta) or phase-3. |
| Canonical ref string | **`/api/tmp_images/{image_id}`** (relative, same-origin HTTP URL) | See rationale below. FE `TMP_IMAGE_REF_PREFIX` constant value changes `'/tmp-images/'` → `'/api/tmp_images/'`. |
| `tmpimg://<id>` scheme | DEMOTED to accepted-input alias on GET/validators ONLY. **Never emitted, never persisted.** | Parse-tolerance stays (backend already built + tested it); the scheme is no longer canonical. |
| Message-request field | `image_refs: list[str]` (NEW sibling field on `MessageCreate`, XOR with `images`) — entries: 32-hex ids (bare) or `/api/tmp_images/<id>` or `tmpimg://<id>` accepted; canonical persisted form = ref_url | Backend phase-2 Task 3/Task 5 as-written, with ONE amendment: the persisted display form written into `MessageQueue.images` must be the **ref_url string**, not `tmpimg://`. |
| Body key for image data | `data_base64` | Backend name wins; FE Task 4 renames `image_b64` → `data_base64`. |
| id format | 32-hex lowercase uuid4 (`^[a-f0-9]{32}$`), path-traversal safe | Backend AS-WRITTEN (phase-1 Task 5 regex). |

**Why URL form over `tmpimg://` as the canonical ref (the one non-mechanical call):**
1. The persisted/display ref must be a valid `<img [src]>` value — `chat-interface.html:193-195` renders `message.images` items DIRECTLY; `tmpimg://` is not fetchable by a browser and would force a translation layer into the SSE pipeline, the GET/refetch path, and any future export consumer.
2. The SSE whitelist (phase-5) is prefix-scoped by design — a URL prefix (`/api/tmp_images/`) is naturally scoppable; a scheme alias is not renderable after passing the filter.
3. The design direction asked for "stable image id/**URL** refs".
4. The endpoint path is now canonical and stable, so the decoupling benefit of a custom scheme is moot. Parse-tolerance retains migration slack if the path ever moves.

**Mechanical rename list (losing sides):**
- FE phase-4 Task 4: endpoint path `/api/tmp-images` → `/api/tmp_images`; body `image_b64` → `data_base64`; wrap in batch-of-1 `{images:[...]}`; unwrap `uploads[0]`; map `ref_url`.
- FE phase-5 Task 1: `TMP_IMAGE_REF_PREFIX = '/api/tmp_images/'`; Task 2 spec fixtures `'/tmp-images/abc-123.png'` → `'/api/tmp_images/<32hex>'`; identity-grep pins updated.
- Backend phase-1 Task 4: response field `ref` → `ref_url` (plus the contract docstring + integration test). Backend phase-2 Task 5: persist ref_url form into `MessageQueue.images`.
- Backend phase-1: ADD `DELETE /api/tmp_images/{image_id}` (route + idempotency test).

### §2.1 Round-2 two-channel picture (C1 — supersedes any single-channel reading)

```text
AGENT channel (content blocks → vision routing):   images kwarg ONLY — refs NEVER enter
DISPLAY channel (checkpoint sidecar → serialize):  additional_kwargs['image_refs'] on BOTH legs
  durable leg: enqueue_message_job(image_refs=…) → row column (audit) + _build_graph_input kwargs stamp
  202 leg:     set_injection(image_refs=…) → drain-site kwargs stamp (+ POST-time echo stamp)
  read side:   serialize_message unions kwargs refs into wire `images` (legacy blocks unchanged)
```

- **Agent channel:** `images` kwarg only → `_build_message_content` content blocks → vision routing. Refs NEVER enter (S3 signature separation, A1).
- **Display channel:** `additional_kwargs['image_refs']` on BOTH legs — durable: `enqueue_message_job(image_refs=…)` → MessageQueue row column (durable audit, `:1688`, S2 no-migration stance preserved at the row level) + `_build_graph_input` additive merge (`instance_messaging.py:409-532`, alongside the proven `_stamped_additional_kwargs` stamps `:382-406`; MessageTapSlot id-invariant satisfied — `:528-532` already passes `id=message_id`); 202: `set_injection(image_refs=…)` → drain-site stamp (`graph.py:6647-6673`, METADATA ONLY — `langchain_openai` does not serialize `additional_kwargs` to the wire) + POST-time echo stamp (`messages.py:486-491`). Persists via the checkpoint (mechanism proven by `source`/`context_kind`/`injected_message` round-tripping today, `utils.py:218-266`).
- **Read side:** `serialize_message` (`utils.py:113-137`) extends to UNION `additional_kwargs['image_refs']` into the wire `images` field — ONE serializer extension serves both legs. **Reviewer question (ii) RULED: legacy display surface untouched by the union** — Discord HTTPS URLs and legacy data URIs surface through the same `images` field exactly as today; refs add alongside.
- **Reviewer question (i) RULED — chat-source registry needs NO exclusion:** `IncomingMessage` (`daemon/sources/base.py:20-28`) has NO `image_refs` field — sources cannot mint refs, safe by construction; the existing `msg.images` predicate (`registry.py:94-95`) covers the only image-bearing field. Pinned by static field-absence assertion test (A8, folded into phase-2 Task 11's audit) + a one-line guard comment at `sources/base.py:25` ("Sources must NOT add `image_refs` — refs are POST-only").
- **Facade-forwarding (amendment #27 — SUPERSEDES the round-1 framing):** the phase-2 Task 10 facade work is now **REQUIRED, not contingent** — `image_refs` threads the 5-function chain (router → `InstanceManager.enqueue_message` `:6795-6808` AND `enqueue_message_job` `:6893-6905` → `InstanceMessagingService.enqueue_message` `:1977+` AND `enqueue_message_job` `:2151+` → `_prepare_enqueued_message` `:1527-1541`/row `:1688` AND `_process_message_with_tracking` `:6944-6956`/`:7009`); guard tests mirror the `work_id_required` 5-test pattern for BOTH facade methods + a real-dispatch integration test (A6/A7). Any round-1 reading that the facade guards only matter if the async-pipeline is revived is dead. Note the ASYMMETRY: `set_injection` (202 leg) lives DIRECTLY on `InstanceManager` — no facade seam there; verify at implementation and document in the PR.

### §2.2 Merge-gate contract checklist (canonical copy — round-2 amendment #41)

Planner-mechanical, closes reviewer critical #3 (contract-rename propagation). At merge gate:

1. `grep` every §2 contract-family token across **ALL six phase files** (and plan-overview): `/api/tmp_images`, `image_refs`, `data_base64`, `ref_url`, `TMP_IMAGE_REF_PREFIX`.
2. Diff every hit against §2 + the round-2 rulings — any deviating form (old `/api/tmp-images` path, `tmpimg://` as canonical/emitted form, `data_base64`/`ref_url` naming drift, FE prefix-constant drift) blocks the merge.
3. Includes phase-2 Task 3's `image_refs` regex: must ACCEPT all three canonical input forms (bare `<32hex>`, `tmpimg://<32hex>`, `/api/tmp_images/<32hex>`) while emitting only the URL form.
4. `grep "tmp-images\|image_b64\|ref_id"` across all phase files must return **ZERO hits**.
5. Each phase file's exit criterion references THIS checklist (the checklist is the single canonical copy; per-phase copies would drift).

---

## 3. OPEN ARCHITECTURE QUESTIONS (the core deliverable)

Format per question: Context → Options → Trade-offs → **Recommended default** → Reversibility → What the phase plans assume meanwhile.

---

### Q-a. Web-source input strategy — full switch vs coexistence knob

**Context.** The design direction says all web image inputs (paste + picker + drag-drop) route through the SAME upload-first flow, flagged as "coexistence knob vs full switch". Meanwhile the API surface must keep serving legacy data-URI sends (S1: Discord is live; S4-resume threads `message.images`; old FE builds exist).

**Options.**
1. **Full switch, FE-side only:** the web composer's three input paths all convert to upload-first refs. No runtime knob. API-level coexistence preserved via the sibling `image_refs` field (XOR with `images`).
2. **Coexistence knob:** a runtime flag (env / config) letting the web composer still send data URIs (e.g., rollback lever if upload infra misbehaves).
3. **Full switch everywhere:** also migrate sources (already excluded — S6).

**Trade-offs.** Option 1 gives the FE one code path (one upload pipeline, one error surface, one test matrix) and honors the design direction verbatim. Option 2 adds a knob whose only use is "escape hatch if the new endpoint regresses" — but the identical escape already exists: legacy data-URI code remains IN the FE bundle-able path only if kept alive, which doubles the FE maintenance surface permanently for a one-week risk window. The daemon-side coexistence (sibling field) is the real safety net: the OLD FE build keeps working against the NEW daemon indefinitely. **Interplay with the vision gate (`messages.py:222-231`):** ref-sends skip the gate naturally — the hook clears `images` before the gate line (phase-2 Task 5 documents the ordering + a regression comment), so a ref-send is never 400'd for missing `model_vision` at the gate (but see Q-f for conversion-time vision policy). Data-URI sends keep the gate unchanged. **Interplay with Discord:** none — different surface, S6.

**Recommended default: Option 1 (full switch on the web composer, no knob).**
**Reversibility (REWRITTEN per amendment #22 — the earlier "branch revert" claim was factually wrong):** FE and daemon deploy independently (S5), so a source-branch revert is not the rollback story. Rollback = **deploy the prior FE bundle** — a deploy-time event, executed at the deploy surface, not a git operation. The durable coexistence lever is unchanged: the daemon's sibling `image_refs` field keeps the OLD FE bundle working against the NEW daemon indefinitely, so no user is stranded while a rollback (or forward-fix) ships.
**Phase plans assume:** FE phase-4 builds upload-first into the composer (all three input paths via `processFiles`); backend phase-2 assumes coexistence at the API layer. Consistent — no plan churn.

---

### Q-b. Conversion placement — synchronous in POST vs pre-dispatch pipeline step

**Context.** WHERE does `ref → image-reader → text` run? The conversion is an agent turn (`invoke_agent_and_wait`, `daemon/utils.py:588`) — real latency (typically 5–20s per image, timeout-bound at 300s per the `explain_image` template, `image_tools.py:521`).

**Options.**
1. **Synchronous in `POST /api/instances/{id}/messages`** — a pre-dispatch hook in the router (backend phase-2 Task 4-5: after empty-content validation `~:215-220`, BEFORE the vision gate `:222-231`).
2. **Pre-dispatch pipeline step** — inside the durable dispatch path (`_process_message_with_tracking`, before `_build_message_content` at `instance_messaging.py:4042`), POST returns 202 immediately, conversion runs on the worker before the agent turn.
3. **At the drain site** — convert inside the injection drain (`graph.py`) for the 202 leg only.

**Trade-offs.**
- *HTTP latency:* Option 1 blocks the POST for Σ(per-image conversion) — worst case 300s×3 = 900s (phase-2's own Risk 3: browsers/LBs may abort earlier). Option 2 returns 202 in ms; conversion latency hides behind the provisional bubble + POST-time echo (message-display-latency machinery already emits `user_message` at POST time on the 202 leg, `messages.py:471-504`).
- *Two-leg coverage (the decisive constraint the phase plans under-state):* user messages reach the agent through TWO legs — the durable enqueue leg (IDLE/PAUSED/200) AND the 202 RAM-injection leg (RUNNING + live graph, `messages.py:442-458` → `set_injection`, RAM-only FIFO). Option 2's hook inside `_process_message_with_tracking` covers ONLY the durable leg; the 202 leg bypasses it entirely (`set_injection` takes `content` straight into RAM). Option 2 therefore needs a SECOND seam (drain-site conversion) to avoid injecting raw refs into a live turn — two seams, two failure surfaces, two test matrices. Option 1's router placement runs BEFORE the status branch, covering both legs at ONE seam. This is why backend phase-2 rejected the pipeline ("REJECTED for v1", phase-2-plan.md §rejected) — though note their published strand-trap rationale is the weaker argument (a hook inside the existing task execution mints NO new rows; the strand trap only bites row-minting designs); the two-seam argument is the strong one.
- *Worker-pool coupling:* every conversion consumes an invoke-semaphore lane (`max(1, WORKER_POOL_SIZE-1)` = 4 with default pool 5, `daemon/utils.py:566`) AND spawns an image-reader child that itself needs a worker lane. Option 1 does this inside the HTTP request (caller waits; no queue interaction). Option 2/3 do it on a worker — under pool saturation, conversions queue behind other work and the user's turn start becomes unpredictable. Option 1 keeps the coupling caller-visible and bounded.
- *Ordering guarantee:* Option 1 gives "text ready before agent turn" absolutely (the enqueue/injection happens after the hook returns). Option 3 gives it per-leg but with the conversion racing the turn start.
- *Failure surface:* identical (placeholder fallback, Q-f) in all options.
- *FE provisional UX:* Option 1 = the FE send-button spinner covers the conversion ("this may take a minute" — phase-2's mitigation). Option 2 = smoother (202 fast) but requires the drain-site second seam to be built anyway.

**Recommended default: Option 1 (synchronous in POST, single router seam before the vision gate) — RATIFIED (rec §4).** Hardening as ruled: per-image timeout 90s converter-owned (Q-f — the "inherited 300s" reduction is now the pinned value, not an option); the 600s per-POST deadline is DROPPED (dead code — see Q-f/§4 cut list); FE-side `timeout(300_000)` added instead (amendment #16).
**Reversibility:** moderate — migrating to Option 2 later means adding the drain-site seam and deleting the router hook; the converter service (phase-2 Task 2 `TmpImageConverter`) is placement-agnostic, so the expensive part survives.
**Phase plans assume:** Option 1 (backend phase-2 Task 4-5, rejected-alternatives section). FE phase-4 assumes send-time upload + a blocking-ish POST — consistent.

**ORDERING-INVERSION — ACCEPTED RISK v1 (ruled, rec §4 / amendment #23):** while an image message converts (≤270s), a later text-only message can overtake it on BOTH legs (durable enqueue AND RAM injection) — the message that arrives later is seen by the agent first. Cross-source interleaving (e.g. a Discord message to the same instance) and multi-tab users bypass FE mitigation entirely; phase-4's block-send covers single-user-single-tab ONLY. Likelihood low; blast radius low (one out-of-order pair, NOT data loss). Follow-up candidates RECORDED, NOT BUILT: (a) per-tab pendingPostLock via `storage` event; (b) daemon-side per-instance POST serialization.

**Cut by ruling (no-knob family):** the `ENSEMBLE_IMAGE_CONVERSION_MODE` env knob (phase-2 Task 6) is **DELETED** — full-switch means no runtime conversion-mode toggle, daemon-side either (rec §6.5 corollary). A POST-level conversion concurrency guard is also ruled OUT as over-engineering — the invoke semaphore (cap 4, `daemon/utils.py:566`) already IS one; document the cap in the converter docstring (rec §5.5).

---

### Q-c. Upload API contract

**Context.** Canonicalized in §2 above (endpoint, body, response, ref form, id format, DELETE addition). This question records the REMAINING contract decisions beyond the rename arbitration.

**Sub-decisions:**
- **No multipart.** JSON base64 body — no multipart endpoint exists anywhere in the daemon today; the design direction explicitly chose JSON-base64 for style consistency. 10MB raw ≈ 13.7MB base64 wire — acceptable for a self-hosted single-user tool.
- **Caps enforcement location.** THREE layers, deliberately redundant: (1) FE client pre-checks — `message-input.component.ts:445` (≤3) and `:457` (10MB raw `file.size`) for instant UX; (2) upload endpoint — 413/422 on decoded size >10MB raw bytes and batch >3 (NOTE: raw-decoded check here is CLEANER than the legacy data-URI path's base64-estimation math at `message.py:50-61`; keep both, they are different paths); (3) `MessageCreate.image_refs` validator — ≤3 entries + shape regex (phase-2 Task 3). The ≤3-per-MESSAGE cap lives at layer 3 (and stays ≤3-per-UPLOAD at layer 2); FE keeps its chip-strip cap.
- **Auth posture (RULED, rec §3/§6):** the daemon API has NO authentication layer anywhere (`/messages`, `/work`, everything — same posture; verified zero auth Depends in `daemon/routers/`). The upload + serving endpoints inherit: **public-by-obscurity, ratified for v1 WITH mandatory guard-rails** — (1) route docstring block on the tmp_images router: "PUBLIC-BY-OBSCURITY — ids are identifiers, not secrets; if any daemon endpoint gains auth, this MUST be the first file-serving route to gain it; not precedent for auth-free serving of sensitive content"; (2) one line in `.agents/shared/conventions.md` (applied by planner/leader, amendment #25); (3) proxy tripwire: if this daemon is ever reverse-proxied publicly, gate `/api/tmp_images/` at the proxy FIRST (bind default `0.0.0.0`, `config.py:517`; CORS `allow_origins=["*"]`, `api.py:2421-2425`). **Debug listing GET (phase-1 Task 6) — RULED: KEPT but GATED** behind `ENSEMBLE_TMP_IMAGE_DEBUG_LISTING`, default OFF, returns **404 when disabled** so the route does not advertise itself; returns only `{count, oldest_mtime}` (no id leak). **Store growth — RULED (new):** `tmp_image_store_max_bytes` default 1 GiB (`SERVICES_TMP_IMAGE_STORE_MAX_BYTES`); walkdir sum at upload; exceed → `507` + rate-limited WARNING — ~10 LOC against the feature's only unbounded resource. A scoped-token scheme remains OUT of scope v1.
- **Path-traversal safety.** Strict `^[a-f0-9]{32}$` on the id path segment → 404 otherwise (phase-1 Task 5 covers `../etc/passwd` shapes); store resolves ids against a fixed dir; no user-controlled filename components reach the filesystem (filenames are metadata only).

**Recommended default:** as stated (JSON base64; 3-layer caps; public-by-obscurity with the flag; strict hex-id guard).
**Reversibility:** caps and auth can tighten later without wire changes; the endpoint path is the expensive-to-change element — now canonical (§2).
**Phase plans assume:** backend phase-1 Tasks 4-6 + FE phase-4 Task 4, modulo the §2 renames and the DELETE addition.

---

### Q-d. Ref persistence format in `MessageQueue.images`

**Context.** The display column must serve THREE eras of rows: legacy data URIs (web), Discord HTTPS URLs (production TODAY — `discord/adapter.py:1105`), and new ref URLs. The FE renders whatever is in `message.images` (`chat-interface.html:193-195`); the SSE whitelist filters (`sse.service.ts:474-476`); the merge util spreads (`message-merge.util.ts:160`).

**Options.**
1. **Overload with URL refs (canonical):** new rows carry `/api/tmp_images/{id}` strings in `images` alongside whatever else is there. No migration (S2). Display handles all three forms — they are all valid `<img [src]>` values.
2. **New `image_refs` column + migration:** rejected — see S2.
3. **Data URIs in the column (upload + re-inline):** would require the daemon to re-encode refs to data URIs at persistence — reintroduces the exact payload bloat the feature removes, and breaks ref-expiry semantics (a cleaned-up file can never 404 visibly). Rejected.

**Trade-offs / latent defect to surface:** the SSE whitelist today accepts ONLY `data:image/` — which means **Discord image URLs are silently dropped from the live SSE leg today** (they survive GET /messages refetch, where no filter runs — a leg-dependent display inconsistency that predates this feature). Phase-5's two-prefix accept (`data:image/` + `/api/tmp_images/`) does NOT fix the Discord leg. A third predicate (`https://cdn.discordapp.com/`-scoped, or scheme-level `https://` with a host allowlist) would — but the whitelist's fail-closed purpose is precisely to prevent arbitrary URL injection into `<img src>`, so widening it is a security-posture decision, not a freebie. **Recommended default:** phase-5 ships the two-prefix accept ONLY; the Discord-SSE gap is recorded as a KNOWN pre-existing limitation and a follow-up candidate (owner: architect's call, zero coupling to this feature's critical path). No thumbnail/SSE regression for old rows: `data:image/` prefix is retained verbatim; the merge util needs the R4 pin (below) so server `images: null` cannot clobber FE-local ref arrays.

**Recommended default:** Option 1, mixed-form tolerance as described. **RULED (rec §6.5): out of scope v1 CONFIRMED** — the whitelist stays fail-closed two-prefix (`data:image/` + `/api/tmp_images/`); scheme-widening to `https://` is explicitly OUT (tracking-pixel + internal-network-probe vectors via `<img src>`); the correct future Discord fix is a HOST allowlist (e.g. `cdn.discordapp.com`), recorded as follow-up; phase-5 pins a one-line comment stating exactly this.
**Reversibility:** additive; a future migration can normalize in place.
**Phase plans assume:** phase-5 model-parity notes already declare the mixed form ("MIX of legacy data URIs and new refs") — consistent; backend phase-2 Task 5 needs the §2 amendment (persist ref_url form).

---

### Q-e. Cleanup / retention design

**Context.** Requirement: auto-clear after 30 days; loss acceptable (S4). The codebase has a strong precedent shape: ALWAYS-ON sweep services with a `ServicesConfig` interval knob, fail-fast-at-boot range validation, boot-time cleanup + periodic tick, and a shutdown mirror (`job_lock_sweep.py` docstring; `config.py:1474-1533`, `:1550-1558`; `api.py:1669-1682`).

**Design (recommended default):**
- **Knobs (ServicesConfig, `SERVICES_*` env pattern):**
  - `tmp_image_cleanup_retention_days: int = 30` (ge=1) — fail-fast at boot like the sweep precedents (field + env name normalized round-2 / O1, matching phase3-plan: env override `SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS`).
  - `tmp_image_cleanup_interval_seconds: int = 3600` (ge=1) — hourly tick; the scan is a directory `stat` walk (no DB), cheap even at 60s if triage demands (field + env normalized round-2 / O1 to the cleanup family: env override `SERVICES_TMP_IMAGE_CLEANUP_INTERVAL_SECONDS`). **RULED: interval PINNED at 3600s/hourly** (rec §7 — phase-3-plan said 86400; hourly wins: deletion latency ≤ retention + interval, scan is cheap).
- **ALWAYS-ON, no kill-switch — RULED: the proposed `tmp_image_cleanup_enabled` kill-switch is DELETED** (rec §7 / amendment #15; phase-3-plan Task 2). Per the project owner's HARD POLICY codified in `job_lock_sweep.py` ("no kill-switch env var", full stop) — its unique failure mode is silent permanent storage growth when toggled by accident. The retention DAYS knob IS the operator's lever (set it huge to effectively disable expiry). Internal constructor param allowed for unit tests only; hardcoded ON at the boot anchor.
- **Age basis: filesystem mtime.** The store is deliberately dir-only (no DB table — S2's simplicity argument extends here); files are write-once at upload, so mtime == upload time. Known fragility (mtime preserved by copies/rsync) is irrelevant on a single-node self-hosted deployment. A content-addressed scheme (dedupe) was floated in phase-1 but ids are uuid4 — dedupe is NOT v1; duplicates simply age out independently.
- **Boot-time sweep once + periodic tick** — cleans the restart-window accumulation, mirroring the dependency-bus startup/periodic split (`config.py:1483-1487` describes the same shape for watchers).
- **Shutdown mirror:** graceful `stop()` with WARNING-on-failure inside the lifespan shutdown, EXACTLY the `api.py:1669-1682` shape (stop the sweep BEFORE manager shutdown so an in-flight unlink finishes before the process dies). Also required: the sweep must tolerate files vanishing mid-scan (idempotent unlink, `FileNotFoundError` swallowed) — concurrent FE DELETE (§2) + sweep race is expected traffic, not an error.
- **Eager DELETE interplay:** FE chip-remove DELETE (§2) and the sweep target the same unlink; both are idempotent; no locking needed (worst case: double-unlink, second is a no-op).

**Reversibility:** retention semantics are data-destructive by design (S4) — the reversibility question is moot for expired files; the SERVICE itself is removable without residue.
**Phase plans assume:** phase-3 owns the service (store + knobs + boot/shutdown wiring). Consistent with the above; phase-3 should add the idempotent-unlink race test.

---

### Q-f. Conversion failure policy

**Context.** Conversion can fail: image-reader spawn failure, vision LLM error, 300s timeout, corrupt bytes. The placeholder convention `[Image attached — description unavailable]` is already pinned in the design direction and phase-2 tests (Task 8: forced `asyncio.TimeoutError` still enqueues with fallback text; WARNING log; no exception propagates to the client).

**Sub-decisions (recommended defaults):**
- **Placeholder text:** YES — per image, injected into the agent-facing content prefix (phase-2 Task 4d shape: `[Image N: description]` / `[Image N: description unavailable]`). The user's original text is preserved AFTER the prefix.
- **Retry:** NONE in v1 (fail straight to placeholder). Rationale: a retry doubles the worst-case POST latency for a marginal recovery rate; the image STILL DISPLAYS (ref persists — requirement #3/#5) so the human sees what the model missed and can re-ask in a follow-up turn. A "convert-on-demand" retry tool for the agent is a possible future extension, not v1.
- **Does the ref still display when conversion failed?** YES — non-negotiable. Display refs and conversion outcomes are independent (refs persist in `MessageQueue.images` regardless; only the agent-facing text degrades). This preserves requirement #3 under all failure modes.
- **Timeout budget — RULED (rec §5, amendments #8/#16):** per-image **90s RATIFIED** as a converter-owned constant `TMP_IMAGE_CONVERSION_TIMEOUT_S = 90.0` in `daemon/services/tmp_image_converter.py` — do NOT reuse the `image_tools.py:521` 300s literal (tool-context budget ≠ HTTP-context budget; 90s ≈ 5× the 5–20s typical; bounds a 3-image POST at **270s** + overhead). The **600s per-POST deadline is DROPPED** (dead code: sequential worst 270s can never trip it — untestable, misleading; if a POST cap is ever wanted, derive it `3 × per_image + margin`, never hardcode; phase-2 Risk 3d/"Task 4b" deleted). **FE-side timeout now REQUIRED (phase-4):** verified nothing bounds the FE wait in prod today — plain `http.post` (`api.service.ts:307-328`), no interceptor, no proxy, uvicorn sets no request-processing timeout; amendment = rxjs `timeout(300_000)` on the messages POST, typed send-failure on timeout ("may still have been delivered"), **NEVER auto-retry** (the server completes the handler regardless — retry duplicates the message). Client disconnect = accept-and-document (Starlette/uvicorn does NOT cancel the handler; optional cheap mitigation: `request.is_disconnected()` check BETWEEN per-image conversions → placeholders for the remainder, early lane release, INFO log).
- **Vision-unconfigured policy — RULED: FAIL-FAST (rec §2, amendment #7; supersedes this register's earlier "recommended default" framing):** `POST /api/instances/{id}/messages` returns **400** when `image_refs` is non-empty AND `not manager.config.llm.model_vision` — same `ErrorResponse` shape as the existing gate (`daemon/routers/messages.py:222-231`), message adjusted to name `image_refs` + `OPENAI_MODEL_VISION`. The check sits at the **TOP of the conversion hook** (before any conversion work — refuse early, spend zero conversion budget on a doomed request). Decisive evidence (unchanged, now ratified): (1) API-law precedent — the existing gate already 400s data-URI image sends without `model_vision`; the ref path silently weakening it is a convention inversion; (2) a 200-with-placeholders response systematically lies to the agent about every image — the feature's core promise (requirement #2) dies silently; (3) use-time fail-fast catches both UNSET and SET-BUT-BOGUS configs (`OPENAI_MODEL_VISION=vision` resolves through no alias table — R2), which truthiness/boot-WARN checks cannot. **Weighted scores (W-A, two independent analysts converged without coordination — the strongest signal in the run): fail-fast 4.28 vs fail-open 2.85 vs falsy-only-hybrid 2.95.**
  - **ARCHIVED (superseded test expectation):** backend phase-2 Task 5's integration-test line "`POST with image_refs=[1] and model_vision UNSET succeeds (200/202)`" encoded fail-open by accident and is **SUPERSEDED-BY amendment #7** — replaced, BEFORE tests are written, by the 5 fail-fast cases in rec §2: (1) unset + ref → 400 (gate-shaped error naming `image_refs`/`OPENAI_MODEL_VISION`); (2) set + forced timeout STRING → 200/202, placeholder prefix, WARNING, refs persist; (3) set + spawn failure (error-string path) → same as (2), distinct WARNING marker; (4) data-URI path byte-identical (unset → 400, set → 200/202); (5) `images` AND `image_refs` both non-empty → 422 (XOR). The fail-fast check complements (does not replace) the h6 vision probe.
**Reversibility:** policy flip is a router-branch + test change — cheap (though it is now RULED, not merely recommended).
**Phase plans assume (post-ruling):** placeholder + no-retry stand; per-image timeout is now 90s converter-owned (phase-2 Task 2 amended — was 300s); fail-open is DEAD — phase-2 Task 5's test is rewritten to the 5 fail-fast cases BEFORE tests are written (amendment #7); phase-2 Tasks 2/8 additionally get the error-STRING collapse (see R17).

---

### Q-g. Multi-image (≤3) conversion

**Context.** A message may carry ≤3 refs. Each conversion = one `invoke_agent_and_wait` lane (`invoke_semaphore` cap = 4 with pool 5, `daemon/utils.py:566`) + one spawned image-reader child instance (which itself consumes a worker lane to run).

**Options.** (1) Sequential; (2) parallel (`asyncio.gather`); (3) parallel with bounded concurrency (2).

**Trade-offs.** Parallel-3 needs 3 invoke lanes + 3 child worker lanes = 6 lanes against a 5-lane pool → children queue unpredictably; latency becomes a function of unrelated pool load, worst-case WORSE than sequential under saturation. Sequential is bounded (3×t), trivially testable (phase-2 Task 9 pins sequential semaphore acquisition with a saturation test), and the per-image failure isolation is natural (one `ok=False` does not abort siblings — `ConvertedImage.ok` flag, phase-2 Task 2). Parallel-2 is a marginal optimization that adds gather/cancel complexity for ~33% best-case latency win on an already-rare 3-image path.

**Recommended default: SEQUENTIAL, per-image failure isolation.** Latency budget: typical 15–60s for 3 images; worst 3×90s=270s under Q-f's reduced timeout (was 900s at 300s). FE communicates the wait (Q-b).
**Reversibility:** converter-internal; parallelize later without wire changes.
**Phase plans assume:** sequential (phase-2 Task 9) — consistent.

---

### Q-h. Additional questions surfaced by this analysis

**h1. Compaction interplay — SETTLED BY ANALYSIS (no architect time needed).** Converted messages are plain-string content: no `context_kind`, no multimodal blocks, no `image_url` — trivially safe under every compaction rung (threshold counting, selectability, CLE backstop; Core Architecture). Legacy data-URI rows in history remain multimodal — UNCHANGED existing behavior, already survived by the compaction ladder in production. The one watch-item: converted DESCRIPTION text grows message size modestly (a few hundred chars/image) — noise, not a rung risk.

**h2. Concurrent uploads / duplicate content.** ids are uuid4-128bit — collision is negligible (no scan-safety concern for the hex-id filesystem layout). Duplicate CONTENT creates duplicate files (no dedupe v1 — content-addressing rejected with §2); an orphaned duplicate ages out via the sweep. FE chip identity prevents accidental double-upload of the same chip; a deliberate double-paste produces two files, one orphaned — acceptable, self-healing.

**h3. Serving-endpoint auth — PUBLIC-BY-OBSCURITY, RULED with guard-rails (rec §6).** No daemon API auth exists (verified: no HTTPBearer/APIKey/auth Depends in `daemon/routers/`); the new endpoints inherit that posture. The ONLY new exposure vs `/messages` is unauthenticated READ of stored pixels by anyone with network reach + an id. ids are 128-bit random but appear in `MessageQueue.images`, SSE payloads, checkpoints, and logs — they are identifiers, not secrets. Verdict: consistent with the deployment's threat model (self-hosted, single operator); the architect flag below is now ANSWERED with binding guard-rails: (1) route docstring PUBLIC-BY-OBSCURITY block (this MUST be the first file-serving route to gain auth if any daemon endpoint ever does); (2) `.agents/shared/conventions.md` line (applied by planner/leader, amendment #25); (3) CORS `*`/bind `0.0.0.0` posture tripwire recorded as R19 — reverse-proxy-to-public flips the ruling to "gate at proxy". Debug listing GET: KEPT but gated default-off → 404 when disabled (see Q-c). **Original flag (resolved): do not let this silently become precedent for auth-free file serving of anything sensitive later — the guard-rails are the enforcement.**

**h4. The 202-leg display gap — RULED IN V1 as h4-S1 (round-2 C2, amendment #31; supersedes round-1's "accept v1" stance).** On the 202/RUNNING leg, the POST-time `user_message` echo carried NO images and the injected HumanMessage carried none — the pre-existing 202 images-drop defect (`messages.py:458` passes only `content`; `serialize_message` emits `images: null` for text-only messages, `utils.py:213`). **Ruling: narrow additive `set_injection` kwarg — `set_injection(instance_id, content, source=None, echo_id=None, image_refs=None)`:**
- Signature gains the kwarg (`InstanceManager.set_injection`, `daemon/manager.py:2734-2740` — dual-pin stable across `307db932`/`5f453b93`/HEAD `0b504b5e`); the RAM-FIFO entry adds `image_refs` ONLY when non-None — byte-identical-when-absent, mirroring the proven `source`/`echo_id` conditional-add pattern at `manager.py:2784-2797` (entry-contract context: threading comment block `:2715-2732`, `_INJECTION_TTL_SECONDS` at `:2732`).
- Drain site (`graph.py:6647-6673`, same conditional-add pattern) stamps `additional_kwargs["image_refs"]` on the injected HumanMessage — METADATA ONLY, never content blocks (agent channel stays text-only; `langchain_openai` does not serialize `additional_kwargs` to the wire). Survives the turn commit via the return-carried message list, then surfaces through the SAME `serialize_message` union as the durable leg (§2.1) — one serializer extension serves both legs.
- Router `messages.py:458` passes `image_refs=message.image_refs`; POST-time echo (`:486-491`, echo minted `:454`) stamps `additional_kwargs={"image_refs": [...]}` so the optimistic echo carries refs immediately.
- **All 4 injection-lane consumer classes verified call-site by call-site** (`messages.py:458`; `daemon/tools/instance.py:3166`; `daemon/sources/registry.py:1029`; `daemon/tools/job_queue.py:2471`) — none pass the kwarg → byte-identical entries (A9, mirroring `tests/test_injection_slot.py:255-280`). `set_injection` lives directly on `InstanceManager` — **no facade seam**; verify at implementation and document in the PR.
- **BONUS (recorded):** h4-S1 also closes the pre-existing legacy 202 images-drop defect — today `messages.py:458` drops `message.images` for legacy data-URI sends; the kwarg change amortizes across two bugs.
- **Rejected alternatives + weights (rec round-2 C2):** S3 (POST-time MessageQueue row for the 202 leg) REJECTED — wedge-class V strand trap (row-without-Task+notify; `child_reports.py:3136-3247`, `message_processing_pipeline.py:611-660`; `_prepare_enqueued_message` is the only strand-safe shape) and strictly more work than S1. S2 (202-body-only) REJECTED — doesn't survive reload. S4 (re-scope to live-session-only) REJECTED — user weights thumbnails CORE and paste-mid-turn is a COMMON path; **weighted 4.45 (S1) vs 4.10 (S4), decisive axis Maintainability** (S1 rides two live precedents); the margin is Δ0.35 — what makes it unambiguous is the green byte-identical-when-absent test precedent + the legacy-defect closure.
- **ESCAPE HATCH (documented, honest):** if implementation discovers the drain-site stamp violates a live-turn invariant the analysts missed → **fall back to S4 (re-scope), never widen S1.**
- **FE merge pin stays MANDATORY and independently load-bearing:** the echo's `images: null` clobber hazard at `message-merge.util.ts:160` PREDATES and SURVIVES the serializer union (the pin covers the SSE live-echo leg regardless of h4-S1; A11 re-ratified). FE phase-5's "BE fix lands, flagged cross-lane" assumption is now TRUE — the BE fix (h4-S1) lands in v1.

**h5. `images: null` merge-clobber — promoted to a REQUIRED phase-5 task (from R4 analysis).** See h4 + R4. The pin must cover BOTH SSE re-emit and GET-refetch copies. This is the difference between "thumbnail appears after drain" (h4's accepted gap) and "thumbnail NEVER appears" (the defect if unpinned).

**h6. Vision-config runtime probe — cheap, early, mandatory (from R2 analysis; PROMOTED per rec §6 h6/§9).** Run ONE manual probe: submit a small test image through the real path (upload → POST with ref → observe image-reader turn + description quality). Verifies end-to-end that `model_vision` resolves to a REAL vision-capable model in PROD (the `OPENAI_MODEL_VISION=vision` value is unverified and suspicious — no alias table resolves it). Cost: minutes. **Owner: PROMOTED to the phase-1 window (phase-2 Task 0 amended) — run at contract-freeze time so the wave-2 go/no-go lands before dispatch** (see §5). Skip = ship a converter that may hallucinate "descriptions" from a non-vision model. If typical latency proves >60s in the probe, revisit the 90s budget upward before phase-2 tests freeze (rec §12 — the converter-owned-constant mechanism is the durable ruling; the number is a first estimate).

**h7. `image_refs` XOR `images` — confirmed good, with one note.** The XOR validator (phase-2 Task 3) prevents mixed sends and protects the resume path from double-carrying (`messages.py:313/346` pass `message.images` through — with `images=None` on ref-sends, resume threads text correctly). Note for the architect: XOR means a message cannot carry BOTH a legacy data URI and a ref — fine for the FE (never mixes) and sources (never send refs); if a future consumer needs mixing, the validator loosens additively.

---

## 4. CONSOLIDATED RISK REGISTER

| # | Risk | Severity | Likelihood | Mitigation | Owning phase |
|---|------|----------|------------|------------|--------------|
| R1 | **Conversion failure handling** — image-reader spawn/LLM/timeout failure degrades agent-facing text | Medium | Medium | Placeholder text per image; no retry v1; ref STILL displays (Q-f); WARNING log; no client-visible exception (phase-2 Task 8 test) | Phase 2 |
| R2 | **model_vision RUNTIME CONFIG UNVERIFIED** — conversion depends on daemon-global vision routing (`graph.py:8093-8105`); `.env` value `vision` resolves through NO alias table; if bogus/unset, image-reader is blind → garbage descriptions or errors | **High — unchanged 🔴 until the probe runs** | Medium | **Probe PROMOTED to the phase-1 window** (rec §6 h6/§9 — run at contract-freeze time so the {2,3,4}-wave go/no-go lands BEFORE dispatch); fail-fast now RULED (Q-f) so breakage is LOUD at use time, not silent placeholder-lies | Phase 1 window (probe), Phase 2 (consumer) |
| R3 | **POST latency vs pipeline placement** — sync-in-POST blocks for Σ conversions; **worst case now 270s** (90s/img ratified; 600s deadline DROPPED — dead code); browsers/proxies may abort earlier | Medium | Medium | 90s/img converter-owned constant (Q-f); FE `timeout(300_000)` + typed send-failure + NEVER auto-retry (amendment #16); spinner/copy state "converting images, this may take a minute" (amendment #17); optional `request.is_disconnected()` early-exit between images (§5.4); sequential conversion bounds the tail (Q-g) | Phase 2 + FE Phase 4 |
| R4 | **SSE/merge seams** — whitelist drops non-`data:image/` refs (`sse.service.ts:474-76`); merge spread clobbers FE-local refs with server `images: null` (`message-merge.util.ts:160` + `serialize_message` at `daemon/utils.py:213` emits null) → without the pin, the thumbnail is lost on the SSE echo leg in the surviving hazard shapes: **(i) S4-fallback** (if h4-S1 hits a live-turn invariant and we re-scope per the documented escape), **(ii) union regression** (`serialize_message` dropping the kwargs union), **(iii) text-only echo paths** (a consumer that never stamps refs) — the pin is the mitigation for ALL THREE on the SSE echo leg | **High** | High (if unpinned) | Phase-5: two-prefix whitelist (prefix-scoped, fail-closed otherwise) + **MANDATORY merge pin** treating null/absent server images as keep-local (h4/h5); cross-seam spec: ref survives `mapToMessage` → `mergeMessagesById` end-to-end | Phase 5 |
| R5 | **SPA catch-all route order** — new GET/DELETE routes registered after `@app.get("/{path:path}")` (`daemon/api.py:2612`) are shadowed (first-match-wins; `/vscode` precedent `api.py:1318-1330`) | High | Low (plan already mandates) | Register router BEFORE the catch-all in `create_app`; integration test hits `/api/tmp_images/{id}` through the real app | Phase 1 |
| R6 | **10MB/≤3 caps consistency FE↔daemon** — FE checks raw `file.size` (`message-input:445,457`); legacy daemon path estimates from base64 (`message.py:50-61`); upload endpoint must check RAW decoded bytes | Medium | Low | Three-layer caps (Q-c); integration tests: 11MB → 422, 4 images → 422 (phase-1 Task 4); FE spec pins the same numbers | Phase 1 + Phase 4 |
| R7 | **Cleanup service lifecycle** — missing shutdown mirror orphans in-flight unlinks; missing boot sweep leaks restart-window files | Low | Medium | Mirror `api.py:1669-1682` shape exactly; boot-time sweep + periodic tick (Q-e); idempotent unlink race test (DELETE ∥ sweep) | Phase 3 |
| R8 | **Regression to the existing data-URI path** — Discord (live consumer, HTTPS URLs `adapter.py:1105`), legacy web sends, PAUSED-resume threading (`messages.py:313,346`) | **High** | Low | Additive design: existing `MessageCreate.images` validator UNTOUCHED (§Q-2 resolution); hook is a no-op when `image_refs` empty; XOR prevents cross-contamination; guard tests: data-URI POST byte-identical pre/post, resume path covered | Phase 1 + 2 (guards), verified per-phase |
| R9 | **message_queue strand trap** — direct row-minting without paired PENDING Task + notify strands forever (wedge-class V; `child_reports.py:3136-3247`, `message_processing_pipeline.py:611-660`) | **High** | Low (design avoids) | Sync-in-POST mints NO rows (conversion is pre-enqueue); durable path unchanged (single-tx row+Task at `instance_messaging.py:1977-2010`); the async-pipeline alternative that minted rows is REJECTED (Q-b); phase-2 must not add row writes | Phase 2 (constraint) |
| R10 | **Deployment ordering: cleanup vs FE onerror** — if phase-3's 30-day sweep activates before phase-6's broken-image placeholder ships, cleaned files render broken-image icons | Medium | Medium (window is real: cleanup only bites files ≥30 days old, but the DEBUG/dev knobs could shorten it) | **Hard sequencing edge: phase-6 merges before-or-with phase-3 activation** (§5); keep retention ≥30d at activation | Phase 3 + Phase 6 |
| R11 | **Wire-contract drift between lanes** — the §2 mismatch (path/ref-form/response/DELETE) ships unaligned | **High** | Was certain — now resolved | §2 canonical contract + mechanical rename list; rename completion is a merge-gate checklist item for BOTH lanes | Both lanes (this file arbitrates) |
| R12 | **`MessageCreate.images` validator 422s every ref-send** (FE-flagged hard cross-lane dep) | High | Was certain — now resolved | RESOLVED by design: sibling `image_refs` field with its OWN validator (phase-2 Task 3); existing data-URI validator untouched. Verification item: confirm phase-2's Task 3 landed in the plan (it has — line 159) and survives implementation | Phase 2 (verify) |
| R13 | **202 display gap — CLOSED in v1 by h4-S1** (round-2 C2 / W-E amendment 1): narrow additive `set_injection(image_refs=)` kwarg + drain-site additional_kwargs stamp + POST-time echo stamp close the gap on the live 202 leg (h4, rewritten). **The phase-5 merge pin remains independently load-bearing** — the echo `images: null` clobber hazard at `message-merge.util.ts:160` predates and survives the serializer union (it covers the SSE live-echo leg regardless of h4-S1; A11). BONUS: h4-S1 also closes the pre-existing legacy 202 images-drop (`messages.py:458`). Residual: escape hatch to S4 re-scope if the drain-site stamp violates a live-turn invariant — never widen S1 | ~~Medium~~ Residual Low | Was certain — now closed by design | h4-S1 (phase-2, new task) + merge pin (phase-5, unchanged) | Phase 2 (h4-S1) + Phase 5 (pin) |
| R14 | **Discord-SSE display gap (pre-existing)** — SSE whitelist drops Discord HTTPS URLs on the live leg today; phase-5's two-prefix accept does not fix it | Low | Certain (pre-existing) | OUT of scope v1 — RULED (rec §6.5): whitelist stays fail-closed two-prefix; `https://` explicitly OUT (tracking-pixel + internal-network-probe vectors via `<img src>`); future fix = HOST allowlist (e.g. `cdn.discordapp.com`), follow-up; phase-5 pins a one-line comment | Architect (follow-up triage) |
| R15 | **GET serving missing `X-Content-Type-Options: nosniff` / `Content-Disposition`** — sniff-to-`image/svg+xml`/`text/html` on direct navigation = same-origin XSS into the SPA (the daemon serves the FE itself) | 🟡 → 🟢 with amendment #1 | Medium | AMENDED (rec §3, "most consequential gap found"): add `X-Content-Type-Options: nosniff` + `Content-Disposition: inline; filename="<image_id>"` (precedent `daemon/routers/vscode_proxy.py:296-298`); allowlist trimmed to png/jpeg/gif/webp — `bmp`/`tiff` DROPPED with explicit rejection message naming the type (svg excluded: stored-XSS vector) | Phase 1 |
| R16 | **FE unbounded wait in prod** — plain `http.post` (`api.service.ts:307-328`), no HttpClient timeout/interceptor anywhere, no proxy (daemon serves the SPA), uvicorn sets no request-processing timeout: NOTHING bounds the FE wait today | 🟡 → 🟢 with amendment #16 | Medium | rxjs `timeout(300_000)` on the messages POST (300s > 270s worst case); typed send-failure ("may still have been delivered"); **NEVER auto-retry** — the server completes the handler regardless, retry duplicates the message | Phase 4 |
| R17 | **Error-STRING-as-description bug class** — `invoke_agent_and_wait` NEVER raises on timeout/agent-error; it RETURNS the string `"Error: Agent timed out…"` / `"Error: Agent failed…"` (`daemon/utils.py:695-705`); a plain try/except ships error text as image descriptions | 🟡 → 🟢 with amendment #9 | Medium (certain without the fix) | Converter collapse mirrors `image_tools.py:530-541`: `ok = bool(result) and not str(result).startswith("Error")` + None check; store read INSIDE the per-image try (vanished-file case); Task 8 fault injection forces the timeout STRING path (exception case kept) | Phase 2 |
| R18 | **Ordering inversion under sync-in-POST** — a later text-only message overtakes a converting image message on BOTH legs (cross-source interleaving e.g. Discord→same instance; multi-tab); FE block-send covers single-user-single-tab ONLY | 🟡 ACCEPTED-RISK v1 (amendment #13) | Low | Accepted: blast radius = one out-of-order pair, NOT data loss. Follow-ups RECORDED NOT BUILT: per-tab pendingPostLock via `storage` event; daemon-side per-instance POST serialization. phase-4 pins block-send scope (amendment #18) | Phase 2 (row) + Phase 4 (scope pin) |
| R19 | **CORS `allow_origins=["*"]` (`daemon/api.py:2421-2425`) + default bind `0.0.0.0` (`daemon/config.py:517`)** — LAN-reachable by default, public if reverse-proxied; global posture, NOT this feature | 🟡 recorded (global, out of v1 scope) | n/a (pre-existing) | Flagged for the LEADER, not a phase: if this daemon is ever reverse-proxied to the public internet, gate `/api/tmp_images/` at the proxy FIRST (rec §6/§11 tripwire); a scoped-token scheme stays out of scope v1 | Leader (not a phase) |

**Cut by architect ruling (rec §10 over-engineered list) — do NOT (re)introduce in any phase:**
- ~~600s per-POST deadline~~ — dead code (sequential worst 270s can never trip it); untestable, misleading. If a POST cap is ever wanted, derive it (`3 × per_image + margin`), never hardcode.
- ~~`ENSEMBLE_IMAGE_CONVERSION_MODE` env knob~~ — no-knob ruling (full switch, Q-a/Q-b).
- ~~`tmp_image_cleanup_enabled` kill-switch~~ — owner HARD POLICY violation (Q-e); retention-days knob is the operator lever.
- ~~POST-level conversion concurrency guard~~ — the invoke semaphore (cap 4, `daemon/utils.py:566`) already IS one; document the cap in the converter docstring.
- ~~Rate-limit framework / per-IP throttle / memory-amplification guard~~ — out of scope for self-hosted single-operator (W-C).

---

## 5. SEQUENCING MAP (six phases — dependency graph + critical path)

**Phases:** 1 = tmp store + upload/serve/delete endpoints; 2 = conversion (converter + router hook + vision policy); 3 = cleanup/retention service; 4 = FE paste/picker/drag upload-first; 5 = FE SSE whitelist + merge pin + ref constants; 6 = FE popup viewer + onerror placeholder.

```text
                 ┌──────────────┐
                 │  phase 1     │  store + POST/GET/DELETE + route-order + contract docstring
                 │ (backend)    │
                 └──┬───────┬───┘
        hard: store │       │ hard: endpoint + ref_url contract frozen
            +caps   │       │
                   ▼       ▼
          ┌────────────┐   ┌────────────┐
          │  phase 2   │   │  phase 4   │  FE upload-first composer (paste/picker/drag)
          │ conversion │   │  (FE)      │
          └─────┬──────┘   └─────┬──────┘
                │                │ hard: refs in flight on the live wire
                │ soft: ref      ▼
                │ prefix   ┌────────────┐
                │ constant │  phase 5   │  SSE two-prefix whitelist + MERGE PIN (R4) + constants
                └─────────►│  (FE)      │
                           └─────┬──────┘
                                 ▼
                           ┌────────────┐
                           │  phase 6   │  popup viewer + onerror placeholder
                           │  (FE)      │
                           └─────┬──────┘
                                 │ DEPLOYMENT EDGE (R10): before-or-with ▼
          ┌────────────┐   ┌──────────────┐
          │  phase 3   │──►│ phase-3 ACT. │  cleanup service code anytime after 1;
          │ cleanup    │   │ gated on 6   │  ACTIVATION (rebuild+restart with sweep live) gated on 6
          └────────────┘   └──────────────┘
```

**Hard dependencies:**
- **1 → 2** (converter reads via `TmpImageStore`; caps + id format frozen).
- **1 → 4** (endpoint path/body/response per §2; FE Task 4 is the only path-sensitive FE task).
- **4 → 5** (whitelist has nothing to accept until refs flow).
- **5 → 6** (viewer builds on rendered, surviving thumbnails).
- **6 ⇢ 3-ACTIVATION** (R10 deployment edge: onerror placeholder must exist before any file can realistically expire out from under a rendered bubble).

**Soft / contract couplings:**
- **2 → 5:** the ref prefix constant + persisted ref form (§2) must agree across the daemon persistence and the FE whitelist — a one-line agreement, but a silent 404s-everything mismatch if drifted.
- **2 → 4:** FE upload UX assumes conversion semantics (send-time wait, per Q-b/f) — messaging copy only.

**Parallelizable:** {2, 3, 4} all start once phase-1's contract is frozen (§2 IS the freeze — both lanes rename per the list). Phase-3 CODE has no FE dependency; only its ACTIVATION gates on 6. Backend lane (1→2) and FE lane (4→5→6) are otherwise fully independent.

**Critical path: 1 → 4 → 5 → 6**, with 2 joining at the 5-seam (a full-feature E2E needs 2 landed before the final integration gate, but the FE chain is the longest serial spine). Phase-1 is the sole global blocker — front-load its contract freeze (this file, §2).

**Architect wave-structure adjustments (rec §9 — DAG verified sound, fits the 3-instance budget):**
- **Vision probe (h6) PROMOTED into the phase-1 window** — manual, minutes-cheap; run it at contract-freeze time so the escalation/go-no-go decision for wave 2's centerpiece (conversion) lands BEFORE dispatching {2,3,4}, not after. This is the cheapest possible de-risk of the wave.
- **Wave structure:** wave 1 = phase 1 (1 instance); **wave 2 = {2, 3, 4} = exactly 3 instances — at cap, no overflow**; wave 3 = phase 5; wave 4 = phase 6.
- **Phase 1 GREW** (amendments #1–6: nosniff/Content-Disposition headers, allowlist trim, 1 GiB store cap, gated debug listing, DELETE route, guard-rail docstring) — still inside one instance's scope, no re-partition; **phase 1 is now unambiguously the largest backend phase; keep its route-order integration test as the merge gate.**
- The **6⇢3-ACTIVATION deployment edge stands** with the §7 activation-checklist line: BEFORE rebuild+restart with the sweep live, record the oldest mtime in `data/tmp_images` (debug GET, flag on); if any file is ≥ retention_days — or `SERVICES_TMP_IMAGE_CLEANUP_RETENTION_DAYS` < 30 — deploy phase-6 FE dist FIRST; verify the FE dist hash post-deploy (daemon rebuild does NOT cover FE dist).

**Round-2 DAG note (rec round-2, "Round-2 DAG note"):** wave shape UNCHANGED — {2, 3, 4} after phase-1 freeze, ≤3 concurrent, still fits. Phase 2 GREW again (kwarg chain + h4-S1 + serializer union); if dispatch sizing demands, an **optional 2a/2b split**: 2a = conversion + durable-leg channels (hook, kwarg chain, serializer union, facade tests) → 2b = h4-S1 (202-leg), with 2b SERIAL after 2a but still inside the wave envelope ({2a|2b, 3, 4} ≤ 3). **h4-S1 is NOT in the phase-1 window** — it depends on the conversion seam minting canonical refs. The vision probe STAYS promoted to the phase-1 window per round-1 §9.1, and per review question (iii) it now ALSO verifies that `agents/image-reader/meta.json`'s `llm_model:"quick"` resolves to a REAL model name: assert the image-reader spawn log shows a resolved model, NOT the literal `quick` (the watcher resolver maps `quick` → `config.llm.model_keywords` at `graph.py:8551-8599`, but image-reader resolves at spawn via `_resolve_model_override` whose keyword handling is UNVERIFIED — verbatim `quick` in the log = fail probe + escalate, same posture as R2).

**Sequencing risks to watch:** (a) if phase-1's DELETE endpoint slips, FE Task 7 degrades to no-op-on-404 (acceptable, noisy — fix before merge gate); (b) if phase-2's vision probe (h6) FAILS, phases 4-6 still proceed (display-only works) but the feature's core is blocked — escalate immediately rather than at the integration gate.

---

## 6. Questions Requiring CALLER/ARCHITECT Input (condensed)

**ALL SIX ITEMS — ANSWERED 2026-09-19 by the architect (amendment #24).** Ruling source: `architecture-recommendation.md` (same dir). Outcomes + pointers:

1. **ANSWERED — §2 contract RATIFIED with 5 security amendments** (rec §3): `X-Content-Type-Options: nosniff` + `Content-Disposition: inline` on GET (most consequential gap), allowlist trimmed to png/jpeg/gif/webp, debug listing gated default-off, 1 GiB store cap, PUBLIC-BY-OBSCURITY guard-rail docstring. The rename list + route-order merge gate stand unchanged.
2. **ANSWERED — Q-f vision policy: FAIL-FAST** (rec §2): 400 at POST, check at TOP of the conversion hook, gate-shaped ErrorResponse naming `image_refs`/`OPENAI_MODEL_VISION`; 5 replacement test cases pinned BEFORE phase-2 tests are written; weighted scores fail-fast 4.28 vs fail-open 2.85, two analysts converged independently.
3. **ANSWERED — Q-f timeout: 90s/image converter-owned constant; 600s per-POST deadline DROPPED (dead code); FE `timeout(300_000)` REQUIRED** (rec §5). The number is revisitable if the phase-1-window probe shows typical latency >60s; the mechanism (converter-owned constant, never the tool literal) is the durable ruling.
4. **ANSWERED — serving auth: public-by-obscurity for v1 WITH guard-rails** (rec §6): route docstring block, `.agents/shared/conventions.md` line (applied by planner/leader, amendment #25), debug listing KEPT but gated (404 when disabled); CORS `*` + bind `0.0.0.0` posture tripwire recorded as R19.
5. **ANSWERED — Discord-SSE gap: OUT of scope v1, confirmed** (rec §6.5): whitelist stays fail-closed two-prefix; scheme-widening to `https://` explicitly OUT (tracking-pixel + internal-probe vectors); future fix = host allowlist, follow-up.
6. **ANSWERED — UPDATED by round-2: h4 pulled INTO v1 as task h4-S1** (round-2 ruling C2, amendment #31). **Round-1's §6.6 ruling ("top follow-up, NOT v1") is SUPERSEDED.** New outcome: narrow additive `set_injection(image_refs=)` kwarg (byte-identical-when-absent, 4 consumer classes verified, no facade seam) + drain-site additional_kwargs stamp + POST-time echo stamp; also closes the pre-existing legacy 202 images-drop (`messages.py:458`); phase-5 merge pin stays independently load-bearing (A11); escape hatch = S4 re-scope, never widen S1. See rewritten h4 + R13.

Original questions (preserved for the record):

1. **Ratify §2 canonical contract** (underscore path, ref_url canonical form, `tmpimg://` demotion, batch-of-1 FE adapter, DELETE addition) — both lanes rename mechanically per the list.
2. **Q-f vision policy:** fail-fast at POST (recommended) vs fail-open-with-placeholders (phase-2's accidental default) — must be pinned BEFORE phase-2's tests are written.
3. **Q-f timeout number:** ratify per-image 90s + optional 600s per-POST deadline (mechanism matters more than the number).
4. **Q-h3 serving auth posture:** confirm public-by-obscurity is acceptable for v1 (recommended; consistent with the whole API), and decide the fate of the debug listing GET.
5. **Q-d Discord-SSE gap:** confirm out-of-scope v1 + whitelist stays fail-closed two-prefix.
6. **h4 follow-up:** thread `images` through `set_injection` (closes the pre-existing 202 drop defect) — confirm as the top follow-up candidate, NOT v1 scope. **[SUPERSEDED — round-2 ruling C2 pulled h4 INTO v1 as task h4-S1; see ANSWERED item 6 above + rewritten h4/R13. Do NOT act on this archived line.]**
