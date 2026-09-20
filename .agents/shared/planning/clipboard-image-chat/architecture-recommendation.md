# Architecture Recommendation: clipboard-image-chat

Date: 2026-09-19
Author: Architect (controller) — aggregation of 3 dispatched analysts
Instance IDs: architect-worker-policy `7144314c` (trade-off-analysis) · architect-worker-resilience `7950b40a` (resilience-design) · architect-worker-security `fd6ae5c4` (security-design)
Mode: Standard Design (Council check: 0–1 of 4 — reversible, additive, single-feature blast radius)
Status: **Complete** — all rulings final; amendment list below is the actionable core

> How to use: every ruling maps to concrete edits in §8 (Consolidated Amendment List). Nothing here
> rewrites the plan; phase files amend in place. Register decisions marked **AMENDED** supersede
> decisions.md as written.

---

## 1. Verdict Table (all 8 ruling questions)

| # | Question | Ruling | Confidence |
|---|----------|--------|------------|
| 1 | Q-f vision-unconfigured policy | **FAIL-FAST** — 400 at POST when `image_refs` non-empty AND `not config.llm.model_vision` | High (two independent analysts converged) |
| 2 | §2 canonical contract | **RATIFIED with 5 security amendments** (nosniff/Content-Disposition, allowlist trim, debug gating, 1GB store cap, guard-rail comment) | High |
| 3 | Conversion placement | **SYNC-IN-POST ratified** (two-leg claim verified in code) + new ordering-inversion risk row (accepted risk v1) | High |
| 4 | GET serving auth | **Public-by-obscurity for v1** + precedent guard-rails (route comment + conventions line). Explicit: ids are identifiers, not secrets | High |
| 5 | Coexistence strategy | **FULL SWITCH, no FE knob**; daemon sibling field is the real escape hatch. decisions.md's "branch revert" rollback claim is **factually wrong** — corrected | High |
| 6 | Timeout budget | **90s/image ratified** (converter-owned constant, NOT the tool's 300s literal); **600s per-POST deadline DROPPED** (dead code); **FE timeout ~300s REQUIRED** (nothing bounds the FE wait today) | High |
| 7 | Cleanup service + R10 | **RATIFIED** (hourly, mtime, retention knob, boot sweep, shutdown mirror) — **kill-switch DELETED** (owner HARD POLICY); interval pinned 3600s; R10 hard edge KEPT with activation-checklist line | High |
| 8 | Open questions (§3/§6) | Ruled individually in §6 | High/Med |

---

## 2. Q-f Ruling — FAIL-FAST (decide-first ruling; pins phase-2 tests)

**Ruling:** `POST /api/instances/{id}/messages` returns **400** when `image_refs` is non-empty AND
`not manager.config.llm.model_vision` — same `ErrorResponse` shape as the existing gate
(`daemon/routers/messages.py:222-231`), message adjusted to name `image_refs` + `OPENAI_MODEL_VISION`.

**Justification (5-axis, from W-A's weighted comparison — fail-fast 4.28 vs fail-open 2.85 vs falsy-only-hybrid 2.95):**

| Option | Complexity | Scalability | Maintainability | Risk | Cost |
|---|---|---|---|---|---|
| A — Fail-fast 400 | Med (one router branch, mirrors existing gate) | Good | **Best** — consistent with API law | **Best** — loud; catches unset AND misconfig at use time | Low |
| B — Fail-open placeholder | Lowest | Good | **Worst** — inverts existing convention; systematically lies to agent on every image | Worst — silent degradation | Lowest |
| C — Falsy-only + boot WARN | Med | Good | Med — two policy sites drift | Bad — set-but-bogus `"vision"` passes truthiness | Med |

**Decisive evidence:**
- API law precedent: `messages.py:222-231` already 400s data-URI image sends without `model_vision`. The ref path silently weakening it is a convention inversion requiring a written override — none exists.
- `daemon/config.py:157` — `model_vision: str | None`; `.env:21`/`.env.prod:25` carry `OPENAI_MODEL_VISION=vision` which resolves through **no alias table** (grep `config.py`+`constants.py`). Truthiness checks CANNOT catch set-but-bogus — only use-time conversion failure can, and fail-open converts that into per-image placeholder lies. This is the most likely prod shape (Risk R2).
- Placeholder preserves display (ref persists) but the agent-facing content systematically misdescribes every image — the feature's core promise (requirement #2) dies silently.
- Hybrid (Option C) rejected: identical lie for the most likely failure shape; boot WARNING is invisible in practice.

**Exact phase-2 test assertions (replaces phase2-plan.md Task 5's fail-open encoding):**
1. `model_vision` unset + `image_refs=[ref]` → **400**, ErrorResponse shape matches gate at `:222-231`, message references `image_refs`/`OPENAI_MODEL_VISION`.
2. `model_vision` set + per-image timeout (force the timeout **string** return, see §5 error-string note) → 200/202; agent-facing prefix `[Image 1: description unavailable]`; WARNING logged; refs persist in `MessageQueue.images`.
3. `model_vision` set + image-reader spawn failure (error-string path) → same as (2), distinct WARNING marker.
4. Data-URI path unchanged: `images=[data_uri]` + unset → 400 (existing gate, byte-identical); `images=[data_uri]` + set → 200/202.
5. `images` AND `image_refs` both non-empty → 422 (XOR validator, Task 3).

**Placement note:** the fail-fast check sits at the TOP of the conversion hook (before any conversion work) — refuse early, spend zero conversion budget on a doomed request. Complements (does not replace) the phase-2 Task 0 probe (h6).

---

## 3. Q2 — Contract Ratification (decisions.md §2) with amendments

**RATIFIED as frozen**, with these amendments (all land in phase 1 unless noted):

| §2 element | Ruling |
|---|---|
| `POST /api/tmp_images` batch body, `data_base64`, ≤3, 32-hex lowercase ids | **Ratified** |
| Response `{uploads:[{image_id, ref_url, content_type, size_bytes}]}` (`ref`→`ref_url` rename) | **Ratified** |
| `GET /api/tmp_images/{image_id}` registered BEFORE SPA catch-all (`api.py:2612`; `/vscode` precedent `:1318-1330`) | **Ratified** — route-order integration test stays mandatory |
| GET response headers | **AMENDED** — add `X-Content-Type-Options: nosniff` + `Content-Disposition: inline; filename="<image_id>"`. Precedent: `daemon/routers/vscode_proxy.py:296-298` already ships this header family on a same-origin byte route. Without nosniff, a sniff-to-`image/svg+xml`/`text/html` on direct navigation = same-origin XSS into the SPA (the daemon serves the FE itself — same origin). **Most consequential gap found.** |
| Content-type allowlist | **AMENDED** — `png/jpg/jpeg/gif/webp` only; **drop `bmp`/`tiff`** (inconsistent `<img>` rendering, marginal value). Explicit rejection message naming the type (forensics): `"content_type '<t>' rejected — only png/jpeg/gif/webp allowed (svg excluded: stored-XSS via direct navigation)"`. NOTE: the allowlist itself was already planned (`phase1-plan.md:37-38`) — the gap was headers, not the list. |
| `DELETE /api/tmp_images/{image_id}` → 204 idempotent | **Ratified** (required addition stands — owner: phase 1) |
| Canonical ref = `/api/tmp_images/{id}` URL; `tmpimg://` input-alias only | **Ratified** |
| `image_refs` sibling field, XOR with `images`, own validator | **Ratified** (h7 analysis holds: protects resume threading at `messages.py:313/346`) |
| Debug listing GET (Task 6) | **AMENDED** — gate behind `ENSEMBLE_TMP_IMAGE_DEBUG_LISTING` (default **off**; returns **404** when disabled so the route doesn't advertise itself). Returns only `{count, oldest_mtime}` — no id leak; recon-only exposure. |
| Store growth (new) | **AMENDED** — add `tmp_image_store_max_bytes` (default 1 GiB, `SERVICES_TMP_IMAGE_STORE_MAX_BYTES`): one walkdir sum at upload; exceed → `507` + WARNING (rate-limited). Prevents unbounded disk fill until sweep; ~10 LOC. |
| Sync conversion inside POST before vision gate | **Ratified** — see §4 |

---

## 4. Q3 — Conversion Placement: SYNC-IN-POST (ratified) + new risk

**Ruling: synchronous in POST** (router hook between empty-content validation ~`:215-220` and vision gate `:222-231`).

**The decisive constraint is verified, not assumed:** user messages reach the agent via TWO legs —
durable enqueue (200) via `_process_message_with_tracking` (`instance_messaging.py:2585`, content build `:4042`) AND
202 RAM-injection via `set_injection` (`messages.py:442-458` → `manager.py:2734-2740`, signature has **no images param** — verified).
A pipeline hook covers Leg A only; the 202 leg bypasses it entirely. The pipeline alternative therefore needs a
SECOND drain-site seam — two seams, two failure surfaces, two test matrices. One router seam covers both legs.
(The plan's published strand-trap rationale was the weaker argument; the two-seam argument is the strong one.)

| Option | Complexity | Scalability | Maintainability | Risk | Cost |
|---|---|---|---|---|---|
| Sync-in-POST | Med | Med (worst 270s request) | **Best** (1 seam, both legs, ordering trivial-ish) | Loud latency, covered by FE UX | Low |
| Pipeline + drain seam | High | Best (202 fast) | Worst (2 seams drift) | Silent two-seam drift | Med |
| Drain-site only | Med | Good | Worst (races turn start) | Race + single-leg | Med |

**NEW RISK the plan must add (accepted-risk v1): ordering inversion under sync-in-POST.**
While image message A converts (up to 270s), a later text-only message B can overtake A on BOTH legs
(B enqueues/injects while A is still converting). FE phase-4's block-send default mitigates single-user
single-tab web ONLY — cross-source interleaving (Discord message to the same instance) and multi-tab bypass it.
Likelihood low, blast radius low (one out-of-order pair, not data loss). **Amendment:** risk row in
phase2-plan.md + accepted-risk note in decisions.md Q-b; follow-up candidates (per-tab pendingPostLock via
storage event, or daemon-side per-instance POST serialization) recorded, not built. phase4-plan.md pins
block-send scope as single-user-single-tab.

---

## 5. Q6 — Timeout Budget & Contention (W-B)

**Rulings:**

1. **Per-image 90s RATIFIED** — as a converter-owned constant
   `TMP_IMAGE_CONVERSION_TIMEOUT_S = 90.0` in `daemon/services/tmp_image_converter.py`, passed to
   `invoke_agent_and_wait(timeout=...)`. **Do NOT reuse the `image_tools.py:521` 300s literal** —
   tool-context budget ≠ HTTP-context budget. (phase2-plan.md Task 2 currently says "300s matches
   image_tools.py:521" — amend.) 90s ≈ 5× the 5–20s typical; bounds 3-image POST at 270s + overhead.
2. **600s per-POST deadline DROPPED** — dead code: sequential worst case 3×90=270s < 600s, so the
   deadline can never fire before per-image timeouts exhaust. Untestable, misleading. If a POST cap is
   ever wanted, derive it (`3 × per_image + margin`), never hardcode. Delete phase2-plan.md Risk 3d/"Task 4b".
3. **FE timeout REQUIRED (phase-4 amendment)** — verified: `api.service.ts:307-328` plain `http.post`,
   zero timeout config, no interceptor anywhere in the FE app; Angular HttpClient has no built-in
   timeout; prod serves the SPA from the daemon itself (no proxy); uvicorn sets no request-processing
   timeout. **Nothing bounds the FE wait in prod today.** Amendment: rxjs `timeout(300_000)` on the
   messages POST (300s > 270s worst case); on timeout surface typed send-failure
   ("may still have been delivered") and **never auto-retry** (server completes the handler regardless
   — retry duplicates the message). Also add the spinner/copy state ("converting images, this may take
   a minute") that phase2-plan.md Risk 3c assigns to phase 4 but phase4-plan.md currently lacks.
4. **Client disconnect = accept-and-document.** Starlette/uvicorn does NOT cancel a running handler on
   disconnect — conversion completes and the message enqueues; an aborted FE shows failure; user retry
   duplicates. `asyncio.shield` is meaningless (nothing to shield). Optional cheap mitigation (phase-2
   Task 4 amendment): `await request.is_disconnected()` check BETWEEN per-image conversions → convert
   remaining to placeholders, release the invoke lane early. INFO log on detected disconnect.
5. **No deadlock; no POST-level guard.** Semaphore holders live on the event loop (router handler), not
   worker threads; children wait only on worker lanes — no circular wait. Budget-as-queue-wait under
   saturation degrades to placeholder + delivery (graceful by construction). The one pathological shape
   (5 workers all inside sync invoke-tools) is pre-existing, timeout-bounded, and the 90s ruling SHRINKS
   it. A daemon-wide conversion guard is over-engineering — the invoke semaphore (cap 4) already is one.
   Document the 4-cap in the converter docstring.
6. **Error-STRING handling — must-fix bug class (phase-2 Tasks 2 & 8).** `invoke_agent_and_wait` NEVER
   raises on timeout/agent-error — it returns the STRING `"Error: Agent timed out…" / "Error: Agent
   failed…"` (`utils.py:695-705`). Task 2's try/except alone ships error text as image descriptions
   (`[Image 1: Error: Agent failed…]` → agent-facing content). Amendment: wrapper mirrors
   `image_tools.py:530-541` collapse — `ok = bool(result) and not str(result).startswith("Error")`,
   plus None check. Task 8's fault injection must force the timeout STRING (keep the exception case too).

---

## 6. Q4 + Q8 — Auth, Coexistence, and the Remaining Open Questions

### Q4 — Serving auth: public-by-obscurity, with guard-rails (W-C)

**Ratified for v1.** Verified posture: zero app-level auth anywhere (`daemon/api.py`, `daemon/routers/`
— no HTTPBearer/APIKey/auth Depends; `instances.py:1697-1722` comment admits "no per-endpoint gates");
CORS `allow_origins=["*"]` at `api.py:2421-2425`; default bind `0.0.0.0` (`config.py:517`) — LAN-reachable
by default, public if reverse-proxied. In this threat model the ONLY new exposure vs `/messages` is
unauthenticated read of stored pixels by anyone holding an id; ids are 128-bit daemon-minted (uuid4) but
ARE identifiers, not secrets (they appear in SSE, checkpoints, logs by design). Consistent with the
deployment's posture; a scoped-token scheme is out of scope v1.

**Guard-rails (mandatory, cheap):**
- Route docstring block on the tmp_images router: "PUBLIC-BY-OBSCURITY — daemon has no auth layer; ids
  are identifiers, not secrets. If any daemon endpoint gains auth, this MUST be the first file-serving
  route to gain it. Do not treat this as precedent for auth-free serving of sensitive content."
- One line in `.agents/shared/conventions.md` (planner/leader applies — outside architect write boundary):
  self-hosted `/api/tmp_images/` refs are public-by-obscurity; future file-serving of sensitive content
  MUST add auth before merge.
- 🟡 note: if this daemon is ever reverse-proxied to the public internet, add auth at the proxy for
  `/api/tmp_images/` FIRST (bind default is 0.0.0.0 + CORS `*` today).

### Q5 — Coexistence: FULL SWITCH, no knob (W-A)

**Ratified: Option 1.** All 3 web inputs (paste+picker+drag) go upload-first; NO runtime knob. Daemon-side
sibling field (XOR) is the real coexistence lever — the OLD FE bundle keeps working against the NEW daemon
indefinitely, so no user is ever stranded. A knob doubles the FE test matrix permanently for a one-week
risk window.

**Correction to decisions.md Q-a:117** — the claim "reverting the FE composer to data-URI sends is a
branch revert" is **wrong**: FE and daemon deploy independently (S5). The rollback story is "deploy the
prior FE bundle" (deploy-time event), not a source branch revert. Rewrite the Reversibility paragraph.

**Corollary (W-B):** phase2-plan.md Task 6's `ENSEMBLE_IMAGE_CONVERSION_MODE` env knob is deleted —
same no-knob ruling, daemon-side.

### Remaining open questions (each ruled)

| Q | Ruling |
|---|---|
| **§6.1 contract ratification** | Ratified with §3 amendments above. Rename list stands; merge-gate checklist item unchanged. |
| **§6.2 Q-f vision policy** | FAIL-FAST (§2 above). Phase-2 Task 5 test rewritten BEFORE tests are written. |
| **§6.3 Q-f timeout number** | 90s/image converter-owned; 600s deadline dropped; FE 300s timeout added (§5). Mechanism > number: the constant must live in the converter, never inherited from the tool literal. |
| **§6.4 h3 auth + debug endpoint** | Public-by-obscurity + guard-rails (above); debug listing KEPT but gated default-off, 404 when disabled (§3). |
| **§6.5 Q-d Discord-SSE gap** | **Out of scope v1 — confirmed.** Whitelist stays fail-closed two-prefix (`data:image/` + `/api/tmp_images/`). Widening analysis (W-C): the two-prefix form is zero-new-attack-surface (daemon-minted ids, exact prefix); `https://` stays OUT — scheme-widening enables tracking-pixel + internal-network-probe vectors via `<img src>`; the correct future fix for Discord is a host allowlist (e.g. `cdn.discordapp.com`), recorded as follow-up. Phase-5 gets a one-line comment pin stating exactly this. |
| **§6.6 h4 set_injection images** | **Confirmed top follow-up, NOT v1.** Verified `manager.py:2734-2740` has no images param; the 202 images-drop at `messages.py:458` predates this feature. The phase-5 merge pin (null/absent server images → keep-local) is LOAD-BEARING for the 202 leg — without it the thumbnail NEVER appears after drain (`serialize_message` emits `images: null` unconditionally, `utils.py:213`). Follow-up also closes the pre-existing legacy drop defect. |
| **Q-g sequential conversion** | **Ratified sequential with per-image failure isolation.** Parallel-3 needs 6 lanes vs a 5-lane pool → children queue unpredictably; sequential is bounded (3×90s), trivially testable, isolates failures. Task 9 saturation test stands. |
| **h1 compaction** | Settled by analysis — converted messages are plain-string content, trivially safe under all compaction rungs. No action. |
| **h2 duplicate uploads** | Settled — uuid4 collision negligible; orphaned duplicates age out via sweep. No action. |
| **h5 merge pin** | Ratified MANDATORY (see h4) — covers BOTH SSE re-emit and GET-refetch copies. |
| **h6 vision probe** | Ratified MANDATORY before phase-2 converter ships. **Adjustment: promote the probe to the phase-1 window** (it is manual, minutes-cheap, and its failure blocks the {2,3,4} wave's centerpiece — run it at contract-freeze time so the escalation decision lands before dispatching phase 2, not after). |

---

## 7. Cleanup Service (Q7) + R10

**Ratified** against the verified precedent (`job_lock_sweep.py` ALWAYS-ON shape; boot `api.py:777-801`;
shutdown `:1669-1682`; `config.py:1550-1568` fail-fast knobs). Three amendments:

1. **🟡 DELETE the `tmp_image_cleanup_enabled` kill-switch** (phase3-plan.md Task 2 adds
   `SERVICES_TMP_IMAGE_CLEANUP_ENABLED`) — contradicts the owner HARD POLICY codified in
   `job_lock_sweep.py` ("no kill-switch env var", full stop) and decisions.md Q-e. The retention-days
   knob IS the operator lever (set huge to effectively disable). Keep an internal constructor param for
   unit tests only; hardcode on at the boot anchor. The kill-switch's unique failure mode is silent
   permanent storage growth when toggled by accident.
2. **🟢 Pin interval 3600s (hourly)** — phase3-plan.md says 86400; register + brief say hourly. Pick
   hourly (deletion latency ≤ retention + interval; scan is a cheap stat-walk).
3. **R10 hard edge KEPT** — phase-6 merges before-or-with phase-3 activation. The 30-day grace makes it
   soft in practice (first sweep can only delete ≥30d-old files, none exist at activation unless the
   feature ran privately or retention was lowered) but the edge is nearly free. Exact activation
   checklist line (phase-3 / plan-overview):

   > BEFORE rebuild+restart with the sweep live: record oldest mtime in `data/tmp_images` (debug GET,
   > enable env flag). If any file is ≥ retention_days old — or `SERVICES_TMP_IMAGE_RETENTION_DAYS` < 30 —
   > deploy phase-6 FE dist FIRST; otherwise phase-6 must land within retention_days of activation.
   > Verify FE dist hash post-deploy (daemon rebuild does NOT cover FE — cbb47e42 note).

Also ratified: idempotent unlink with `FileNotFoundError` swallowed (FE DELETE ∥ sweep race = expected
traffic); missing dir → WARNING + return 0; boot sweep first-tick-immediate (timing vs manager startup
irrelevant — fs-only); per-tick deleted-count structured log.

---

## 8. Consolidated Amendment List (the actionable core — planner applies)

### phase1-plan.md
1. GET response headers (Task 4/:165): **add `X-Content-Type-Options: nosniff` + `Content-Disposition: inline; filename="<id>"`** (vscode_proxy.py:296-298 precedent). ← most consequential
2. Allowlist (:37-38): **drop `bmp`/`tiff`**; add explicit rejection message naming the rejected type.
3. Task 4: **store byte cap** `tmp_image_store_max_bytes` default 1 GiB (`SERVICES_TMP_IMAGE_STORE_MAX_BYTES`); walkdir sum pre-write; 507 + rate-limited WARNING on trip. Test: fill-to-cap → 507.
4. Task 6 debug listing: **gate** `ENSEMBLE_TMP_IMAGE_DEBUG_LISTING` default off → 404 when disabled. Test: 404 default / 200 with flag.
5. Route docstring: PUBLIC-BY-OBSCURITY guard-rail block (§6 text).
6. ADD `DELETE /api/tmp_images/{id}` (already a §2 required addition — keep owner=phase 1).

### phase2-plan.md
7. Task 5 test: **replace fail-open assertion with the 5 fail-fast cases** (§2 above). 🔴 must land before tests are written.
8. Task 2: converter constant `TMP_IMAGE_CONVERSION_TIMEOUT_S = 90.0`; delete "300s matches image_tools.py:521".
9. Task 2: **error-STRING collapse** — `ok = bool(result) and not str(result).startswith("Error")` + None check (mirror image_tools.py:530-541); store read INSIDE the per-image try (+ unit case for vanished file).
10. Task 8: fault-injection forces the timeout STRING path (keep exception case).
11. Task 4: optional `request.is_disconnected()` early-exit between images; disconnect = accept-and-document note.
12. Task 6: **delete `ENSEMBLE_IMAGE_CONVERSION_MODE` knob** (no-knob ruling).
13. Risks: add **ordering-inversion** row (cross-source/multi-tab; accepted v1; follow-up candidates listed). Delete Risk 3d/Task 4b (600s deadline).
14. Task 0 (vision probe): note promotion to phase-1 window (§6 h6).

### phase3-plan.md
15. Task 2: **delete `tmp_image_cleanup_enabled` kill-switch** (internal test param only); pin interval 3600s; add R10 activation-checklist line (§7).

### phase4-plan.md
16. **FE timeout**: rxjs `timeout(300_000)` on messages POST; typed send-failure on timeout; NEVER auto-retry (duplicate hazard).
17. Add spinner/copy state for conversion wait ("converting images, this may take a minute") — assigned by phase2 Risk 3c, currently missing.
18. Pin block-send scope as single-user-single-tab (ordering-inversion mitigation boundary).

### phase5-plan.md
19. Whitelist comment pin: prefix-scoped NOT scheme-based; `https://` explicitly OUT (tracking-pixel/internal-probe vector); Discord gap = pre-existing, fix = host allowlist, out of scope.
20. Merge pin stays MANDATORY and load-bearing (h4/h5) — no change, explicit re-ratification.

### decisions.md
21. Q-f: fail-fast pinned; archive phase-2's superseded test expectation.
22. Q-a:117 Reversibility paragraph rewrite (deploy prior FE bundle, not branch revert).
23. Q-b: add ordering-inversion accepted-risk note.
24. §6: mark all six input items ANSWERED with pointers here.
25. (Leader/planner applies — outside architect write boundary) `.agents/shared/conventions.md`: public-by-obscurity line per §6.

---

## 9. Phase DAG Sanity Check vs 3-Instance Budget

DAG as drawn is **sound and fits**: wave 1 = phase 1 (1 instance); wave 2 = **{2, 3, 4} = exactly 3**
(at cap, no overflow); wave 3 = 5 (1); wave 4 = 6 (1). Critical path 1→4→5→6 with 2 joining at the
5-seam is correct (FE spine is the longest serial chain).

**Adjustments (2):**
1. **Promote phase-2 Task 0 (vision probe) into the phase-1 window** — manual, minutes; if it fails,
   the {2,3,4} wave's centerpiece is known-dead BEFORE dispatch, per the plan's own escalation trigger
   ("escalate immediately, not at the integration gate"). Cheapest possible de-risk of the wave.
2. **Phase-1 grew** (amendments 1–6: headers, cap, gating, DELETE) — still well inside one instance's
   scope; no re-partition needed. Note only: phase 1 is now unambiguously the largest backend phase;
   keep its route-order integration test as the merge gate.

No other DAG changes. The 6⇢3-ACTIVATION deployment edge stands (§7).

---

## 10. Over-engineered / Missing

**Over-engineered (cut):**
- 600s per-POST deadline — dead code (§5.2).
- `ENSEMBLE_IMAGE_CONVERSION_MODE` knob — no-knob ruling (§8.12).
- `tmp_image_cleanup_enabled` kill-switch — owner HARD POLICY violation (§8.15).
- POST-level conversion concurrency guard — semaphore cap 4 already is one (§5.5).
- Rate-limit framework / per-IP throttle / memory-amplification guard — out of scope for self-hosted single-operator (W-C).

**Kept despite looking like over-engineering (with reason):**
- Magic-byte cross-check — already planned, `filetype`-library precedent (image_tools.py:246-261), cheap insurance.
- `tmpimg://` parse-tolerance alias — already built+tested; removal costs more than retention.
- Debug listing endpoint — gated default-off; useful boot-anchor/dev seam.
- 1 GiB store cap — ~10 LOC against the only unbounded resource in the feature.

**Missing (added by this recommendation):**
- `nosniff` + `Content-Disposition` — the real XSS lever (§3).
- FE request timeout + typed failure + no-auto-retry (§5.3).
- Error-STRING collapse in the converter — would ship `"Error: …"` text as descriptions (§5.6).
- Ordering-inversion risk row (§4).
- Store byte cap (§3).
- Guard-rail comment + conventions line against auth-free-serving precedent drift (§6).

---

## 11. Risk Register Deltas (on top of decisions.md §4)

| Delta | Severity | Note |
|---|---|---|
| + nosniff/Content-Disposition missing (phase 1) | 🟡→🟢 with amendment | Same-origin XSS lever under direct navigation |
| + FE unbounded wait in prod (phase 4) | 🟡→🟢 with amendment | No HttpClient/proxy/server timeout exists today |
| + Error-string-as-description bug class (phase 2) | 🟡→🟢 with amendment | Never-raise contract of invoke_agent_and_wait |
| + Ordering inversion cross-source/multi-tab (phase 2) | 🟡 accepted-risk v1 | Follow-up candidates recorded |
| + CORS `*` + bind 0.0.0.0 posture note (global, not this feature) | 🟡 recorded | Flagged for leader; not addressed in v1 scope |
| R2 (model_vision unverified) | unchanged 🔴 until probe | Probe promoted to phase-1 window; fail-fast makes breakage loud, not silent |
| R3 worst-case latency | 270s (was "90–300s") | Deadline dropped; FE timeout matches |

---

## 12. Confidence & Assumptions

**Confidence: High** on all eight rulings. Two independent analysts (trade-off + resilience) converged on
fail-fast without coordination — the strongest signal in this run. Placement, auth, and coexistence
rulings rest on code-verified anchors (two-leg coverage, no-auth posture, independent deploys), not plan
assertions.

**Assumption that would flip a ruling:** if this deployment is (or becomes) reverse-proxied to the public
internet, the auth ruling (Q4) flips from "consistent with posture" to "must gate at proxy" — the
0.0.0.0 bind + CORS `*` posture note in §11 is the tripwire. If typical conversion latency proves
>60s in the Task 0 probe, revisit the 90s per-image budget upward before phase-2 tests freeze (the
mechanism ruling — converter-owned constant — is what matters; the number is a first estimate).


---

## Post-review rulings (round 2, 2026-09-19)

Instance IDs: architect-worker-channels `b40a644b` (structural-design) · architect-worker-h4scope `abc364aa` (trade-off-analysis)
Trigger: plan review REJECTED (Deep-Review council, 2/2) — 3 criticals; C1/C2 ruled here, #3 (contract-rename propagation) is planner-mechanical (merge-gate: grep every §2 contract-family token across ALL phase files — reviewer's checklist addition, adopted).
These rulings supersede round-1 ONLY where stated (h4 scope flips; Task 7 rewritten); everything else in round 1 stands.

### C1 — Two-channel design: RATLED as signature-separated channels + checkpoint-sidecar display

**The defect, confirmed:** phase2 Task 7's `enqueue_message_job(..., images=message.image_refs)` overload sends refs down the AGENT channel — `_build_message_content` twins (`manager.py:165-180`, `instance_messaging.py:113-128`) append `image_url` blocks → `has_images=True` (`graph.py:7032-7053`) → `use_vision_model` fires (`:7047`) → with round-1 fail-fast guaranteeing `model_vision` set, the MAIN agent routes to vision on EVERY ref-send (violates requirement #2). Meanwhile the DISPLAY channel is checkpoint-only (`persistence.py:312/:521` → `serialize_message` `utils.py:113-137` reads image_url blocks; nothing reads `MessageQueue.images`) — so Task 7's integration test ("GET /messages returns refs") was false as specified under EITHER branch of the plan's contradiction.

**Ruling (a) — agent-input channel: M1, NEW `image_refs` kwarg (NOT overload).**
Separation is BY SIGNATURE, not by prefix detection. `images` kwarg → agent channel (content blocks, vision routing). `image_refs` kwarg → display channel. `_build_message_content` carries only `images` — it structurally cannot see refs.
- M2 (overload + prefix-strip at the twins) REJECTED: silent semantic drift (a future edit re-leaks refs through the `if images:` truthy check), twin-drift hazard, fragile prefix heuristic (canonical prefix already moved once — `tmpimg://` demotion).
- Facade-forwarding discipline applies IN FULL (the known bug class): `image_refs` threads router → `InstanceManager.enqueue_message` (`:6795-6808`) AND `enqueue_message_job` (`:6893-6905`) → `InstanceMessagingService.enqueue_message` (`:1977+`) AND `enqueue_message_job` (`:2151+`) → `_prepare_enqueued_message` (`:1527-1541`, row write at `:1688`) AND `_process_message_with_tracking` (`:6944-6956`/`:7009`). Guard tests mirror `test_manager_enqueue_message_work_id_required.py` (5-test pattern, BOTH facade methods) + real-dispatch integration test mirroring `test_job_driven_enqueue_work_id_facade.py`. This converts phase2 Task 10 from contingent to REQUIRED.
- In `_prepare_enqueued_message`, the MessageQueue ROW's `images` column carries the refs (`images=(image_refs if image_refs is not None else images)` at `:1688`) — S2's no-migration stance is preserved at the row level; rows are durable audit (`:1680-1693`). The row is NOT the display read source (below).

**Ruling (b) — display read channel: R1, `additional_kwargs['image_refs']` checkpoint stamp + `serialize_message` union.**
- Durable leg: `_build_graph_input` (`instance_messaging.py:409-532`, alongside the proven `_stamped_additional_kwargs` stamps at `:382-406`) merges `{"image_refs": [...]}` into the HumanMessage's `additional_kwargs` (additive-only, when non-empty). Persists INTO the LangGraph checkpoint — mechanism proven by `source`/`context_kind`/`injected_message` round-tripping today (`utils.py:218-266` reads them back). MessageTapSlot id-invariant satisfied (`_build_graph_input:528-532` already passes `id=message_id`).
- Read side: `serialize_message` (`utils.py:113-137`) extends to UNION `additional_kwargs['image_refs']` into the wire `images` field (single field; FE phase-5 whitelist already accepts both prefixes). Legacy image_url blocks surface through the SAME field, unchanged — **reviewer question (ii) ruled: legacy Discord/data-URI display surface is untouched by the union** (blocks → images stays; refs add alongside).
- R2 (read-time join against MessageQueue by message_id) REJECTED: N extra reads per page, two sources of truth, and the SSE echo path structurally CANNOT use it (echo carries only the LangChain message, `instance_messaging.py:4025-4051`).

**Review question (i) — chat-source registry: NO exclusion needed.** `IncomingMessage` (`sources/base.py:20-28`) has NO `image_refs` field — sources cannot mint refs, safe by construction; the existing `msg.images` predicate (`registry.py:94-95`) covers the only image-bearing field. Pin with a static guard test (A8) + a one-line comment at `sources/base.py:25` ("Sources must NOT add `image_refs` — refs are POST-only").

**Review question (iii) — probe alias: AGREE.** Phase-1 probe must ALSO verify `agents/image-reader/meta.json`'s `llm_model:"quick"` resolves to a REAL model: assert the image-reader spawn log shows a resolved model name, NOT the literal `quick`. Known gap the probe covers: the watcher resolver (`graph.py:8551-8599`) maps `quick` → `config.llm.model_keywords`, but image-reader resolves at spawn via a separate path (`_resolve_model_override`) whose keyword handling is UNVERIFIED — if the probe sees `quick` verbatim, fail the probe and escalate (same posture as R2).

### C2 — h4 pulled INTO v1 as h4-S1 (narrow additive `set_injection` kwarg)

**Ruling: h4-IN-v1**, shape S1 — `set_injection(instance_id, content, source=None, echo_id=None, image_refs=None)`:
- `InstanceManager.set_injection` signature (`daemon/manager.py:2734-2740` — anchor dual-pin verified stable at baseline `307db932`, `5f453b93`, HEAD `0b504b5e`) gains the kwarg; RAM-FIFO entry adds `image_refs` ONLY when non-None (byte-identical-when-absent, mirroring the proven `source`/`echo_id` pattern at `:2784-2797`).
- Drain site (`graph.py:6647-6673`, same conditional-add pattern) stamps `additional_kwargs["image_refs"]` on the injected HumanMessage — METADATA ONLY, never content blocks (agent channel stays text-only; `langchain_openai` does not serialize `additional_kwargs` to the wire, `graph.py:6657-6661`).
- Survives the turn commit via the return-carried message list (the mid-superstep invariant does not affect injected-message absorption), then surfaces through the SAME `serialize_message` union as C1 — one serializer extension serves both legs.
- Router `messages.py:458` passes `image_refs=message.image_refs`; POST-time echo (`:486-491`) stamps `additional_kwargs={"image_refs": [...]}` so the optimistic echo carries refs immediately.
- All 4 injection-lane consumer classes verified call-site by call-site (`messages.py:458`; `tools/instance.py:3166`; `sources/registry.py:1029`; `tools/job_queue.py:2471`) — none pass the kwarg → byte-identical entries. Byte-identical-when-absent tests mirror `tests/test_injection_slot.py:255-280`.
- `set_injection` lives directly on `InstanceManager` (not behind the messaging facade) — no facade-forwarding seam; verify at implementation and document in the PR.
- **Bonus:** closes the pre-existing 202 images-drop defect for LEGACY data-URI sends (`messages.py:458` drops `message.images` today) — the change amortizes across two bugs.
- S3 (POST-time MessageQueue row for the 202 leg) REJECTED — wedge-class V strand trap (row-without-Task+notify; `child_reports.py:3136-3247`, `message_processing_pipeline.py:611-660`; `_prepare_enqueued_message:1977` is the only strand-safe shape) and strictly more work than S1. S2 (202-body-only) REJECTED — doesn't survive reload. S4 (re-scope to live-session-only) REJECTED — user weights thumbnails CORE and paste-mid-turn is a COMMON path; weighted 4.45 (S1) vs 4.10 (S4), decisive axis Maintainability (rides two live precedents).

**Weighting note:** the margin over re-scope is real but not enormous (Δ0.35); what makes it unambiguous is the pattern precedent (byte-identical-when-absent tests already green) + the legacy-defect closure. If implementation discovers the drain-site stamp violates a live-turn invariant the analysts missed, fall back to S4 (re-scope) rather than widening S1 — the escape is documented, cheap, and honest.

### Unified two-channel picture (one design, two legs)

```text
AGENT channel (content blocks → vision routing):   images kwarg ONLY — refs NEVER enter
DISPLAY channel (checkpoint sidecar → serialize):  additional_kwargs['image_refs'] on BOTH legs
  durable leg: enqueue_message_job(image_refs=…) → row column (audit) + _build_graph_input kwargs stamp
  202 leg:     set_injection(image_refs=…) → drain-site kwargs stamp (+ POST-time echo stamp)
  read side:   serialize_message unions kwargs refs into wire `images` (legacy blocks unchanged)
```

### Amendments (continue numbering; #26–#41)

26. **phase2-plan.md Task 7 — REWRITE** (fixes the conflation): router calls `manager.enqueue_message_job(..., images=message.images, image_refs=message.image_refs)`; XOR guarantees at most one non-empty. Delete the "overload" instruction + its docstring change; add two-parallel-fields docstring.
27. **phase2-plan.md Task 10 — REQUIRED, not contingent**: full facade-forwarding for `image_refs` across the 5-function chain (C1 ruling a); guard tests mirror the `work_id_required` 5-test pattern for BOTH facade methods + real-dispatch integration test.
28. **phase2-plan.md — implementation mapping for the kwarg chain** (manager `:6795/:6893`, service `:1977/:2151`, `_prepare_enqueued_message:1527/:1688`, `_process_message_with_tracking:6944/:7009`) + `_build_graph_input` kwargs stamp (`:409-532`, additive-only merge).
29. **phase2-plan.md — `serialize_message` union extension** (`utils.py:113-137` + `:252+`): read `additional_kwargs['image_refs']`, union into wire `images`; identity-grep pin that `image_refs` appears verbatim in that block; comment citing this ruling (serializer-simplification hazard).
30. **phase2-plan.md — PAUSED/resume branch** (`messages.py:313/:342`): forward `message.image_refs` alongside `images` (XOR guarantees one non-empty).
31. **phase2-plan.md — NEW TASK h4-S1** (C2): `set_injection` kwarg + drain-site stamp + POST-time echo stamp + byte-identical-when-absent tests for all 4 consumer classes + end-to-end 202-leg GET test. Verify/document no facade seam at `set_injection`.
32. **phase2-plan.md §rejected — ADD S3 rejection note** (wedge-class V; cite `child_reports.py:3136-3247`, `message_processing_pipeline.py:611-660`, `_prepare_enqueued_message:1977`).
33. **decisions.md S3 — clarify**: "agent-input exclusion is BY SIGNATURE — `_build_message_content` carries only `images`; the `image_refs` kwarg is never threaded to it" (makes the guarantee testable).
34. **decisions.md h4 + R13 — REWRITE**: h4 is IN v1 as h4-S1; amended R13 text per W-E amendment 1 ("R13 — 202 display gap CLOSED in v1 by h4-S1 … phase-5 merge pin remains independently load-bearing").
35. **decisions.md §6 — mark h4 answered** ("in v1 as task h4-S1"); note the round-1 §6 h4 ruling is superseded.
36. **phase1-plan.md — probe checklist ADD one line**: verify image-reader spawn log shows a RESOLVED model name, not the literal `quick`; fail probe + escalate if verbatim (review iii).
37. **phase5-plan.md — NO functional change; two notes**: (a) merge pin #20 STAYS LOAD-BEARING for the SSE live-echo leg (independent of h4-S1 — the echo's `images: null` clobber hazard predates and survives the serializer union); (b) Task 2 spec fixtures updated to the union wire shape when #29 lands (refs arrive in `images`).
38. **daemon/sources/base.py:25 — one-line guard comment** ("Sources must NOT add `image_refs`") + static field-absence assertion test (A8) in phase2 Task 11's audit.
39. **phase4-plan.md — no new work**; note only: 202 response body may now carry the echo stamps — FE optimistic flow unchanged.
40. **Reload smoke test (E2E, replaces phase-5 Task 5 augmentation)**: RUNNING target + POST `image_refs=[r1,r2]` (202 leg) → drain → `GET /messages` shows both refs on `images` (not null) after page-reload; SAME assertion for a legacy data-URI send (pre-existing-defect closure — bonus acceptance criterion).
41. **Merge-gate checklist (planner-mechanical, reviewer critical #3)**: grep every §2 contract-family token (`/api/tmp_images`, `image_refs`, `data_base64`, `ref_url`, `TMP_IMAGE_REF_PREFIX`) across ALL phase files; diff against §2 + these rulings. Includes phase2 Task 3's regex (must ACCEPT the canonical `/api/tmp_images/<32hex>` form).

### Test freeze list (phase-2 gate — pin BEFORE tests are written)

| ID | Assertion |
|---|---|
| A1 | **Req-#2 structural guarantee**: ref-send → checkpoint HumanMessage content is `str` (no `image_url` blocks); `additional_kwargs['image_refs']` carries canonical ref URLs; agent LLM invocation logs `call_type="STANDARD"` (`use_vision_model=False`) |
| A2 | Durable-leg display: POST `image_refs=[a,b,c]` → GET /messages returns the 3 refs in `images` (the CORRECTED Task 7 test) |
| A3 | Row persistence: `MessageQueue.images` == the 3 canonical ref URLs for that message_id |
| A4 | Legacy byte-identical: data-URI send → content IS a block list, `additional_kwargs['image_refs']` absent, wire `images` = the data URI; gate behavior unchanged |
| A5 | XOR: both fields non-empty → 422 |
| A6 | Facade-forwarding: 5-test pattern × both facade methods (`image_refs` kwarg forwarded, default None, keyword-only) |
| A7 | Real-dispatch facade integration: kwarg survives router → facade → service → `_prepare_enqueued_message` → row + checkpoint kwargs stamp |
| A8 | Chat-source safe-by-construction: `IncomingMessage` has NO `image_refs` field (static dataclass-fields assertion) |
| A9 | 202-leg byte-identical-when-absent: all 4 consumer classes produce identical entries without the kwarg (mirror `test_injection_slot.py:255-280`) |
| A10 | 202-leg display parity end-to-end: drain → GET /messages carries refs (see amendment #40) |
| A11 | Phase-5 merge pin unchanged (existing spec, re-ratified) |

**Unverified residuals (honest):** (1) LangGraph checkpoint round-trip of LIST-typed `additional_kwargs` values is precedent-inferred (`source`/`context_kind` are strings) — A1/A3 end-to-end assertions are the proof; (2) `_resolve_model_override` keyword handling for `"quick"` unverified — probe (amendment #36) is the de-risk; (3) if either residual fails, the fallbacks are: kwargs→row-join fallback for (1) is REJECTED (R2 analysis) — instead fall back to amending the stamp to a JSON string; for (2), escalate R2-style before phase-2 dispatch.

### Round-2 DAG note

Wave shape unchanged: {2, 3, 4} after phase-1 freeze, ≤3 concurrent — fits. Phase 2 grew (kwarg chain + h4-S1); if dispatch sizing demands, split as 2a (conversion + durable-leg channels) → 2b (h4-S1 202-leg), with 2b serial after 2a but still inside the wave envelope ({2a|2b, 3, 4} ≤ 3). h4-S1 is NOT in the phase-1 window (it depends on the conversion seam minting canonical refs). Probe (amendment #36) stays promoted to the phase-1 window per round-1 §9.1.
