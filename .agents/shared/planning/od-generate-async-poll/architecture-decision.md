# Architecture Decision: od.generate 120s Read-Window Fix

**Commission:** od-generate-async-poll — "fix the 120s proxy read-window ceiling that kills long OD generation calls"
**Date:** 2026-10-11 · **Revision:** v1.2 — approver iteration-001 REJECTED on one blocking issue (§6.2 max_tokens touch-list incompleteness) + 8 non-blocking folds; all applied in place same day. (v1.1: reviewer APPROVED-WITH-NOTES amendments #1–#6.)
**Status:** DESIGN ONLY — no code changes, no deployment, no promote (all deployment is a separate user-gated step)
**Decision inputs:** Worker A proxy-topology recon (`c75d4af5`, data-flow-design), Worker B ensemble-lane recon (`ceebaed0`, data-flow-design), governor council `5f9b1413` (2 models × `trade-off-analysis`, unanimous verdict), KB explore (layered-ceiling + deferral history), reviewer re-verification of topology claims (2026-10-11), approver iteration-001 verification (2026-10-11)
**Repos (worktrees, branch `feature/od-generate-async-poll` in both):**
- proxy: `/home/nea/Code/opensource-projects/llm-supervisor-proxy-wt-od-generate-async-poll` @ `b2660a6c`
- ensemble: `/home/nea/ensemble-src-wt-od-generate-async-poll` @ `4f70415d2` (= latest)

---

## 0. Decision (one line)

> **RECOMMENDED SHAPE: Option C — consumer-side streaming end-to-end for od.generate.** The od.generate factory switches its single LLM call to `stream:true` and consumes the SSE inline, returning a byte-identical internal envelope. **ZERO proxy code changes** — the proxy's streamed path is already CF-safe in production. Option A (proxy-side async submit→poll) is **PARKED** behind explicit reopen triggers (§5.3). Option B (raise read windows) is **REFUTED** — recorded dead so nobody re-litigates (§5.2).

Council verdict: **unanimous** across both models; decisive axis **Maintainability** (heaviest weight, 25%), reinforced by **Risk**. Reviewer **confirmed** the C design and C-vs-A ranking; approver verified the decision core and all 3 hard constraints honored. Confidence: **HIGH**.

---

## 1. Problem & Evidence — two DISTINCT root causes

| Incident | Symptom | Root cause (verified) |
|---|---|---|
| Oct 9 | od.generate died at **173s cap** | **Ensemble-side** inner per-request timeout `max(120, max_tokens/370)` — `daemon/plugin_subsystem/opendesign/generate.py:965` (recon: `:934` region) — evaluates to exactly **173s at `max_tokens=64000`** (the adapter default, `generate.py:101`). Vision lane is specced for 200000. |
| Oct 10 | **Cloudflare 524** "120-second Proxy Read Timeout" mid-flight | **CF edge** read window (~100–125s, plan-dependent) in front of the proxy kills **buffered** (`stream:false`) responses that emit zero client bytes until the full upstream response completes. |

Both ceilings sit in the same call path; fixing only one leaves the pipeline blocked. The Oct 9 timeout is a budget-arithmetic defect (§6.2), not a transport defect; the Oct 10 524 is the transport defect this decision primarily addresses.

## 2. Verified Topology — proxy side (Worker A + reviewer re-verification)

### 2.1 Timer inventory

**No 120s timer gates the buffered path — the buffered killer is the CF edge.** (v1.0's flat "no 120s timer exists" claim was WRONG as stated: the recon grep pattern `120[ -]*s` cannot match Go's `Duration(120 * time.Second)` literal; the proxy DOES carry one 120s default-ON timer — `IdleTerminationTimeout` — but it is a **streamed-path inter-chunk-gap bound**, not a buffered read window. Corrected per reviewer amendment #1.)

| Timer | Value | Evidence |
|---|---|---|
| `http.Server` ReadTimeout | 30s | `cmd/main.go:302` |
| `http.Server` WriteTimeout | `max(5min, 3×MaxGenerationTime)`, clamp 30min | `cmd/main.go:307-315` |
| `http.Server` IdleTimeout | 300s | `cmd/main.go:317` |
| Upstream `ResponseHeaderTimeout` | 30s | `pkg/proxy/handler.go:154` |
| Upstream `http.Client.Timeout` | 0 (by design, streaming) | `handler.go:144-149` |
| Race-coordinator `StreamDeadline` | default **110s**, env `STREAM_DEADLINE` — **single GLOBAL knob** (no per-model scoping; config declines per-model fields) | `config.go:71`, `race_coordinator.go:403` |
| Race-coordinator `MaxGenerationTime` (MGT) | default **300s**, env `MAX_GENERATION_TIME` | `config.go:72`, `race_coordinator.go:409` |
| **`IdleTerminationTimeout`** (v1.1 #1) | **default 120s, default-ON** (`IdleTerminationEnabled: true`) — enforced **mid-stream on the live-mode winner**: terminates a streamed response whose inter-chunk idle exceeds 120s. Env overrides exist: `IDLE_TERMINATION_ENABLED` / `IDLE_TERMINATION_TIMEOUT` (`config.go:481-489`, v1.2 #9) | `pkg/config/config.go:226-227`; enforcement `pkg/proxy/handler.go:1494-1512`; `IsIdle` requires `statusStreaming` (`race_request.go:298-301`) |

**IdleTerminationTimeout materiality (bounded, but load-bearing for C):** the buffered path bypasses the relay loop entirely (idle termination never fires there); `IsIdle` only evaluates while `statusStreaming`; **healthy streams reset the idle clock on every chunk** — with per-chunk flow and proxy heartbeats, a healthy C-path generation never approaches it. It becomes the binding constraint only if the upstream goes **silent mid-stream >120s** (e.g., long thinking with no token emission). **The Phase-2 MGT raise does NOT lift it** — independent knob (env `IDLE_TERMINATION_TIMEOUT`, §6.6). The Phase-0 probe records max inter-chunk gap (§6.0); risk logged §8.

The killing window for **buffered** calls remains the **Cloudflare edge** (`llm.ensem.dev`, `llm.daoduc.org`, both CF-proxied). The proxy itself documents this at `handler.go:942-947` (`no-transform` header set because "otherwise even frequent SSE heartbeats sit in CF's buffer until CF's 100/180s read timeout triggers (524)").

### 2.2 The asymmetry that decides everything

- **Streamed requests (`stream:true`) are CF-safe BY CONSTRUCTION**: SSE headers at t=0 (`handler.go:948-953`), `: connected\n\n` marker + immediate Flush (`:956-958`), 5s heartbeat goroutine (`heartbeat.go:17,31`; heartbeat token is **`: heartbeat`** — `heartbeat.go:65`, v1.1 #4d), per-chunk Flush (`handler.go:957,1179,1231,1249,1261,1299`). Live mode is the DEFAULT when the buffer header is absent (`handler_functions.go:212-234` — council-verified).
- **Buffered requests (`stream:false` or `X-LLMProxy-Buffer-Response`) write NOTHING to the client until the entire upstream response is buffered** — no heartbeat, no flush (`handler.go:940` gates heartbeat on `rc.isStream`) → CF sees zero bytes for 130–170s → 524.
- **`StreamDeadline` (110s) is a no-forwardable-byte guard, NOT a stream cap** (`race_coordinator.go:391-431, 720-722` — council-verified): once the first byte flows, the deadline fire is a no-op. It survives only as a **time-to-first-token (TTFB) constraint**, gated by a single global knob (§2.1).
- **`stream_options: {"include_usage": true}` is untouched by the proxy** (zero non-test references) and the identical mechanism is production-proven on the main LangChain lane through this same proxy.

### 2.3 State, deployment, auth, config, tests (facts for the parked Option A)

- **No job/async state exists.** sqlc targets only `configs`/`models`/`auth_tokens`/`credentials` (`sqlc.yaml`, `schema.sql:7-65`). pgx/v5 + modernc/sqlite already wired; migrations pattern at `pkg/store/database/migrations/` + `sqlc generate`.
- **k8s `replicaCount: 1`** (`k8s/values.yaml:3`) — in-process job state viable today; **any future multi-replica bump silently breaks it**.
- **Graceful shutdown grace = 5s** (`cmd/main.go:405-429`) — would NOT drain in-flight async jobs; no drain hook exists.
- **Auth**: Bearer / `X-API-Key` / `x-api-key` (`handler.go:276-296`); enforced only when the resolved model is `internal=true` (`requiresInternalAuth`, `handler.go:322-344`).
- **Config**: JSON file + env overlay, and env overrides apply ONLY when `APPLY_ENV_OVERRIDES` is non-empty (`config.go:383-490`). Knob pattern: `STREAM_DEADLINE`, `MAX_GENERATION_TIME` are copyable templates.
- **Tests**: `test/mock_llm.go` (+ `mock_llm_loop.go` with delay behavior) are near-drop-in configurable-delay upstream simulators; heartbeat tests at `pkg/proxy/handler_functions_heartbeat_test.go`.

## 3. Verified Consumer Side — ensemble (Worker B)

Full call path: `sketcher` → tool `od.generate` (`plugin_tool_factory.py:262-299`, `ADAPTER_CLASS_TABLE` `:94-99`) → `OdGenerate.execute_dict` (`generate.py:1379`; **max_tokens default 64000 at FOUR live sites** — `generate.py:128` dataclass default, `generate.py:1403` `raw.get("max_tokens", 64000)` — the OPERATIVE default for the production StructuredTool→execute_dict path, `generate.py:1404-1409` invalid-value fallback, `ports.py:102` Port schema `"default": 64000`; clamp 1..200000 `:1408`, Port schema max 200000 `ports.py:101`) → `execute` (`:1135`) → `_resolve_llm_config` (`:697-742` — reads env directly: `OPENAI_BASE_URL` = `https://llm.ensem.dev/v1`, backup `OPENAI_BASE_URL_BACKUP` = `https://llm.daoduc.org/v1` (**set → failover LIVE**), `OPENAI_MODEL_VISION=vision`, never `OPENAI_MODEL` post-fix) → `_LLM_INVOKER = _invoke_chat_via_facade` (`:1131`, `:1033-1107`) → `invoke_raw_with_failover(factory, raw_cfg, wall_clock_cap_s=420)` (`llm_failover.py:700-859`; ladder ceiling 6 attempts with backup — `llm_failover.py:143-145`; exponential-jitter backoff 31s across 6; raw dict deliberately NOT cleaned — `clean_llm_config` would strip `base_url_backup`) → per attempt `_do_chat_call` (`generate.py:968-1030`): fresh `openai.OpenAI(max_retries=0, default_headers=x-proxy-app/x-proxy-interleaved-thinking, base_url=current_failover_url())` → `client.chat.completions.create(...)` **with NO `stream` kwarg ⇒ wire `stream:false`** (`:1018-1029`).

Extraction reads `choices[0].message.content` / `finish_reason` / `usage` (`:1288-1296`); three completeness gates `_gate_html` (`:535-559` — Gate 2 truncation at `:549-550`): empty_response / truncation (`finish_reason != "stop"`) / missing_artifact_marker.

**The precedent that settles feasibility:** the LangChain main agent lane **already streams by default as the adopted CF-524 fix** — `ThinkingChatOpenAI.default_streaming = True` (`graph.py:3495`), construction chokepoint injects `streaming` + `stream_usage` + watchdog-wrapped http clients (`graph.py:3873-3933, 3974-3984`), rationale verbatim at `graph.py:3857-3868`: *"CF-125s 524 fix… streaming keeps bytes flowing so the connection survives. LangChain's invoke() aggregates the chunks back into the same AIMessage… callers see identical final results."* A 45s stream-stall watchdog (`config.py:176`, `llm_stream_watchdog.py`) force-aborts stalled SSE into the existing timeout retry budget. **Streaming-as-CF-fix is production-proven on the main lane through this same proxy; od.generate's raw path is the remaining `stream:false` holdout for long calls** (`config.py:216-220` documents raw sites as not-streamed by design; the 4 sibling raw-SDK sites — skill/snapshot embedding, skill evolution/search — are short calls).

Threading: the tool is a sync `StructuredTool` → runs on an executor worker; worker pool 5, `invoke_agent_and_wait` semaphore 4 (`constants.py:86`, `utils.py:591-605`), `invoke_agent_and_wait` default wait 300s (`utils.py:630`) — a blocking generation holds one worker under every option including A.

## 4. Options — 5-Axis Comparison (council, unanimous ranking)

| Axis (weight) | A: proxy async submit→poll | B: raise read windows ⛔ | **C: consumer streaming** |
|---|---|---|---|
| Complexity (20%) | 2 — two repos, jobs migration, sweeper, drain, auth decision | 5 (trivial — and worthless) | **4** — one factory; SSE join + terminal-chunk capture |
| Scalability (20%) | 4 — window-agnostic, any future client | — | **4/3** — rides purpose-built path; bounded by pool 5 / semaphore 4; degrades at 10min+ horizons |
| **Maintainability (25%)** | **2** — permanent stateful job lifecycle on the stateless-per-request load-bearing chokepoint | — | **5/4** — zero proxy change; one site; aligns both lanes on ONE adopted pattern |
| Risk (20%) | 2–3 — durable state on proxy; 5s grace kills in-flight jobs; orphan rows; poll storms | 1 — does not fix the failure | **3–4** — mechanism production-proven on main lane; proxy zero-diff = nil regression surface there |
| Cost (15%) | 2 — largest diff both sides + migration | — | **4–5** — one repo, one factory + tests |
| **Total (coding / agentic)** | **2.40 / 2.60** | 2.60 / 3.40 (moot — excluded as refuted) | **4.20 / 3.80** |

*(v1.2 #2: coding-column A total corrected 2.20→2.40 — arithmetic reproduction; C reproduces exactly; ranking unchanged.)*

## 5. The Decision

### 5.1 C NOW — rationale

Decisive axis **Maintainability**: the proxy stays a stateless forwarder; C extends the **already-adopted** main-lane CF-524 streaming fix (`graph.py:3857-3868`) to the one raw-SDK holdout, and can adopt the 45s stream-stall watchdog **via the optional §6.1(a) hardening** — v1.2 #3: NOT "for free"; the watchdog is wired only at the LangChain construction chokepoint (`graph.py:3974-3984`), which od.generate's raw client bypasses today. Reinforced by **Risk**: proxy zero-diff means zero regression surface on the component that fronts ALL ensemble LLM traffic. A's genuine advantages (window-agnosticism, client-death survival, cross-client job surface) **have no consumer today** — sketcher is the sole generation lane, and even under A the sync StructuredTool holds an executor worker through the poll loop.

### 5.2 B is DEAD — record and move on

Two independent grounds (both councilors): (i) the ~120s ceiling killing buffered calls is the **CF edge**, not the proxy — proxy windows are already 5–30min (the one 120s proxy timer, `IdleTerminationTimeout`, is a streamed-path inter-chunk bound, §2.1 — irrelevant to buffered deaths) and the CF plan is not ours to raise; (ii) it holds a long read open, violating the commission's hard constraint ("robust WITHOUT holding reads open").

### 5.3 A PARKED — additive, reopen on ANY of

1. **Vision upstream cannot stream** (`stream:true` rejected) → C is dead outright, A is the only answer. *(This is flip-assumption #1 — hence the Phase 0 probe.)*
2. **A second cross-client long-call need materializes** (fire-and-forget, survive-client-restart, >10min horizons) → A earns its cost.
3. **Routine >10min generations** → connection-held-open becomes the fragile shape; the job model wins.
4. **Any multi-replica proxy plan** re-scores the parked design (in-process job state breaks; Postgres jobs table becomes mandatory).

### 5.4 Flip assumptions for C

1. Vision upstream cannot stream → flip to A.
2. Routine TTFB >110s on 200k generations → mitigate via a **GLOBAL `STREAM_DEADLINE` env raise** (config-only; the knob is single/global — no per-model scoping without a proxy code change, which is out of C's zero-diff scope) — v1.2 #4; flip to A only if TTFB is unknowable/unbounded.
3. (2) and (3) of §5.3 above.

## 6. Implementation Brief

### 6.0 Phase 0 — go/no-go probe (FIRST, cheapest, gates all RELIANCE on C; test authoring may proceed in parallel — v1.1 #6)

One-off verification (dev lane, real upstream): a single `stream:true` chat completion on the **vision** model through the live proxy. **Assertions (v1.1 #3 — same call, zero extra cost):**
- Send with **non-trivial `max_tokens`** (≥8000 — forces a multi-chunk stream) **and `stream_options: {"include_usage": true}`**.
- Assert SSE chunks arrive; **assert the terminal `finish_reason` chunk arrives** (load-bearing for Gate 2, `generate.py:520-522` — truncation detection dies without it) **and the terminal `usage` chunk arrives** with plausible numbers (also proves the proxy passes `include_usage` through — kills the stream_options-400 risk).
- **Record TTFB** (feeds the TTFB>110s / `StreamDeadline` question) **and max inter-chunk gap** (measures the `IdleTerminationTimeout` exposure, §2.1/§8).
- Record results back into this doc (§10 resolution).

### 6.1 Phase 1 — the survival pair (lands as ONE changeset, works against the CURRENT live proxy)

**Ensemble repo only. Proxy repo: zero changes.**

**(a) Streaming factory — `daemon/plugin_subsystem/opendesign/generate.py`**
- In `_do_chat_call` (`:713-762`, create-call at `:755-761`): add `stream=True` + `stream_options={"include_usage": True}`.
- Consume the stream synchronously INSIDE the same factory function (`for chunk in stream`): join `delta.content`; capture last non-null `finish_reason`; capture the terminal usage chunk; optionally accumulate `delta.reasoning_content` (verify emission first — §10; not consumed by extraction today).
- Return a **ChatCompletion-shaped object** (`choices[0].message.content`, `.finish_reason`, `.usage`) — a tiny local dataclass or constructed namespace is fine. **The envelope is the invariant**: `execute()` extraction (`:1035-1063`) and all three `_gate_html` gates stay byte-identical.
- **Non-stream fallback**: if a `stream:true` request is answered with a non-streamed response (wrong/unexpected Content-Type), the consumption path must **classify into the existing retry taxonomy** (transient/unexpected), not crash — test §7.4a.
- **Retry/failover semantics unchanged**: stream consumption happens inside ONE factory attempt; per-attempt URL re-read + facade retry/failover still apply. A mid-stream abort classifies exactly like a request error today (rides the existing retry taxonomy).
- **Optional hardening**: pass a watchdog-wrapped `http_client` to the raw client (reuse `get_or_build_watchdog_clients`, `graph.py:3978`) so stalls surface as `StreamStalledError` → existing timeout budget. Watch the import direction (`plugin_subsystem` → `daemon.graph`); if it drags, import the watchdog module directly (`daemon/services/llm_stream_watchdog.py`) or defer — the proxy's own 5s heartbeats + inner timeout already bound stalls.
- **Short calls**: no special-casing — this factory always streams; short generations stream trivially and the proxy's live mode is its default. Sibling raw-SDK factories are NOT touched (each keeps its own module-level factory; the 3-factory mirror pattern is documented at `generate.py:729-734`).

**(b) Wait-timeout codification — sketcher dispatch sites**
- The designer→sketcher `invoke_agent_and_wait` calls pass an explicit `timeout ≥ 400s` (default 300s at `daemon/utils.py:630` silently trims 130–170s generations; ≥400s is already documented discipline at `agents/designer/workflow.md:117` but unenforced).
- Prefer **explicit per-call-site timeouts** over bumping the global default (contained blast radius — the default is read by every facade caller).

### 6.2 Phase 2 — 🔴 ATOMIC budget-chain reconciliation for the 200k target (one changeset + invariant test)

Any link left behind silently becomes the new killer via `min()` — Oct 9's exact failure family. **v1.1 #2: the chain carries the HA-ladder-room term explicitly.** **v1.2 #1: the max_tokens touch-list is complete at FOUR sites — bumping `generate.py:101` alone silently leaves the production tool path at 64000 → inner timeout stays ≈173s → 200k target unmet while every specified test stays green: the exact Oct 9 failure family this plan exists to kill.**

**The fork (reviewer-required decision, v1.1).** At `max_tokens=200000`, inner attempt = `max(120, 200000/370)` ≈ **540s**. A wall of 600s admits effectively **ONE full attempt** (540s ≈ 90% of budget; the ladder ceiling of 6 attempts at `llm_failover.py:143-145` is nominal-only at that budget). The failure mode is **typed, not silent** (wall-clock stop → typed error, `reraise=True`).

> **DECISION: (a) — single full attempt accepted @200k.** Justification: the outer retry already exists at the right level — the designer→sketcher→critic pipeline iterates ≤3 rounds/page severity-gated, and a typed wall-clock failure surfaces cleanly to designer for re-dispatch, so an in-tool second full attempt duplicates a mechanism the pipeline already owns; fast-fail failover is PRESERVED within the 600s wall (connection-refused / immediate-5xx attempts cost seconds, so primary→backup swap still works — what is sacrificed is only a second FULL 540s attempt); the dominant historical failures were deterministic (timeout arithmetic, CF window), which retry ladders never fix; and option (b)'s occupancy cost is material — a worker held up to ~19min per generation against semaphore 4 worsens the 50-page campaign floor and designer latency. The fork is cheap to reverse: it is a constants/config change, not a structural one.
>
> **Recorded alternative (b)** (flip if production telemetry shows transient mid-generation aborts at 200k): wall ≥ 2×inner + backoff ≈ **1150s**, MGT ≥ **1500s** (WriteTimeout = max(5min, 3×1500s=4500s) → clamps at 30min = 1800s ✓, `cmd/main.go:313-315`), wait ≥ **1210s**. Numbers pre-computed; no design work needed to flip.
>
> **Reconciliation (review closure):** the Phase-3 in-adapter re-attempt makes the worst case ≈2×wall (~1200s) against the documented 660s synchronous-wait floor — a synchronously-waited twice-truncated generation can false-timeout (the async production lane is unaffected; fork (b)'s pre-computed numbers above are the escape). Re-attempt-frequency telemetry is a pre-promote follow-up (anchors `generate.py:694` / `:1327`) — documented only, NOT implemented.

**The chain under decision (a):**

```
inner attempt                     wall_clock_cap_s              invoke_agent_and_wait       proxy MAX_GENERATION_TIME
max(120, 200000/370) ≈ 540s       600s                          ≥ wall + 60s = 660s         ≥ 900s via env
(advisory/vestigial bound          (ONE full attempt +           (strict margin over wall    (WriteTimeout = max(5min, 3×900s
 once 5s heartbeats flow —         fast-fail failover room;      — equality would violate    = 2700s) → EXCEEDS the 30min
 see §7.6)                         typed failure at budget)      the strict-< invariant)     clamp → actual 1800s ✓ still
                                                                                             > wall + overshoot ≈ 1140s;
                                                                                             v1.2 #1 arithmetic fix)
```

**All links move in ONE changeset with a chain-invariant test (§7.6). Touch-list:**
- **max_tokens 64000→200000 at ALL FOUR live sites** (v1.2 #1 blocking fix) — recommended: introduce ONE module-level constant (e.g. `DEFAULT_MAX_TOKENS = 200000`) referenced at every site, plus a no-residual-literal pin:
  1. `ports.py:92` — Port input schema `"default": 64000`. **OPERATIVE for schema-default injection**: the StructuredTool layer injects this default when the agent omits `max_tokens` on the production tool path;
  2. `generate.py:1093` — `raw.get("max_tokens", 64000)` in `execute_dict`. **Operative raw-dict fallback** for the production StructuredTool→`execute_dict` path when the key is absent (per §3 call-path, this is the production entry);
  3. `generate.py:101` — dataclass default (typed-args path);
  4. `generate.py:1096-1099` — invalid-value fallback resets to literal 64000 (clamp-rejection path).
- `generate.py:666` (v1.2 #8 citation fix — was cited `:621`): `wall_clock_cap_s` 420→600.
- Sketcher dispatch timeout ≥660s.
- `agents/designer/workflow.md:117` (v1.1 #6): the documented ≥400s figure becomes chain-VIOLATING once wall=600 — bump in the SAME changeset.
- **The hard-coded 420s test pins at `tests/unit/plugin_subsystem/test_opendesign_b_element.py:418, :1236-1242, :1381`** (v1.2 #8 adds `:1381`) — move into this changeset.
- Proxy env `MAX_GENERATION_TIME=900s` **at deployment time (user-gated — §6.7)**. Note: the MGT raise does NOT lift `IdleTerminationTimeout` (§2.1) — independent knob.

### 6.3 Phase 3 — in-adapter retry-on-truncation (independent hardening, last)

Currently ABSENT (spec-vs-code gap): truncation (`finish_reason == "length"`) burns the full generation then fails typed with no re-attempt. Add exactly ONE bounded in-adapter re-attempt (same prompt) on Gate-2 refusal, inside `execute()` after `_LLM_INVOKER` returns — per the stage1 spec ("exactly 1; more is diminishing returns atop HA retries"). **Test pins the bound: re-attempt count == exactly 1** — a second truncation fails typed, no third attempt (v1.2 #7; §7 item 10).

### 6.4 API contract — NO new endpoints

**C uses the existing `POST /v1/chat/completions` unchanged** except one request kwarg:

- Request: identical body **+ `stream: true` + `stream_options: {"include_usage": true}`** (proxy passes both through untouched — verified).
- Wire response: SSE stream (`text/event-stream`) — headers at t=0, `: connected` + **`: heartbeat`** comment heartbeats every ~5s (SSE-comment tolerant consumption; the OpenAI SDK stream iterator ignores comment lines — pin in test, §7; token verified at `heartbeat.go:65`, v1.1 #4d), `data:` chunks with `delta.content` / final `finish_reason`, terminal `usage` chunk.
- **Consumer-visible contract (od.generate tool): UNCHANGED.** Same request args, same typed result/error envelopes, same three completeness gates, same failover behavior. No job ids, no polling, no TTL/cleanup — there is no job surface (that was A, parked).
- Error semantics: unchanged classification — SSE-level failures (connection reset, stall) classify through the existing `_classify_raw_sdk_exceptions` / retry taxonomy; **in-band SSE error envelopes (e.g., a mid-stream guard fire surfacing as an SSE error event) are mapped by the consumption path into the same taxonomy — the mapping is pinned by the Phase-1 TTFB boundary-pair test (§7.1, v1.2 #5)**; HTTP error statuses arrive as before; a non-streamed reply to a stream request classifies as §6.1(a) non-stream fallback.
- Backward compat (hard invariant): the 4 sibling raw-SDK factories, the LangChain lane, and ALL other clients of the proxy see **byte-identical behavior** — nothing about the proxy changes, and only od.generate's own factory is edited.

### 6.5 Backward-compat story (explicit)

1. Proxy repo: zero code changes in Phase 1; Phase 2's MGT raise is an env value at deploy time, raised (300→900s) not lowered — existing traffic is bounded by per-request timeouts far below 300s, so behavior for every current caller is unchanged; WriteTimeout auto-derives (5–30min clamp unchanged).
2. Ensemble: single-factory diff; `TestOdGenerateFacadeWiring` pins (URL re-read, HA-off fallback, `max_retries=0`, `wall_clock_cap_s`, raw-dict pass-through, BadRequestError→typed envelopes) continue to pass — the fake invoker seam (`_LLM_INVOKER` / `_set_test_hooks`, `generate.py:1176-1207`) insulates all 57 adapter tests from the streaming change (the wall-value pins move atomically with Phase 2, §6.2).
3. Sync endpoints keep working for ALL current ensemble LLM traffic — untouched paths by construction.

### 6.6 Config knobs (complete list)

| Knob | Where | Today | Target | When |
|---|---|---|---|---|
| `stream=True` + `include_usage` | `generate.py` `_do_chat_call` | absent | present | Phase 1 (code) |
| sketcher dispatch wait | designer spawn sites | 300s default | explicit ≥400s (Phase 1) → ≥660s (Phase 2) | Phases 1+2 (code) |
| `max_tokens` default | **four sites** — `ports.py:92`, `generate.py:1093`, `generate.py:101`, `generate.py:1096-1099` | 64000 | 200000 (single shared constant recommended) | Phase 2 (code) |
| `wall_clock_cap_s` | `generate.py:666` | 420 | 600 (decision (a); 1150 if flipped to (b)) | Phase 2 (code) |
| `MAX_GENERATION_TIME` | proxy env (`config.go:72`; env overrides gated on `APPLY_ENV_OVERRIDES` non-empty — verify in helm values before relying) | 300s | ≥900s (≥1500s under (b)) | **Phase 2 deploy (user-gated)** |
| `STREAM_DEADLINE` | proxy env (`config.go:71`) — **single GLOBAL knob** (v1.2 #4) | 110s | keep; GLOBAL raise ONLY if probe shows TTFB >110s (per-model scoping = proxy code change, out of zero-diff scope) | conditional |
| `IdleTerminationTimeout` | proxy env **`IDLE_TERMINATION_TIMEOUT`** / **`IDLE_TERMINATION_ENABLED`** (confirmed, `config.go:481-489` — v1.2 #9) | 120s, ON | keep; raise ONLY if probe shows inter-chunk gaps approaching 120s | conditional |

### 6.7 Deployment gating (out of this commission, by constraint)

Phase 1 needs **no proxy deployment at all** — it works against the current live proxy (streamed path already exists in production). Phase 2's proxy env change rides the normal user-gated deploy lane. No ensemble promote here; the branch carries code for a future promote ceremony.

### 6.8 Close-out note (review closure pass)

`compare_tools.py` dispatch wait 600→660 (`_COMPARATOR_DISPATCH_WAIT_S`) was a plan-plus OUTSIDE §6.2's literal touch-list — the chain invariant demanded it (wall 600 < wait and wait ≥ wall + 60s). Consequence: the `compare_images` semaphore hold widens to 660s. *(Text feeds the merge/changelog note.)*

## 7. Test Strategy

**Core survival proof:** mock upstream with configurable/injectable delays and a STREAMED shape — first chunk ≤5s, then many chunks spanning the pattern (v1.1 #4c: CI runs a **compressed timeline**, e.g. 60×2s chunks ≈ 2min wall — the survival *pattern* is what CI proves; the **literal >120s duration stays in §7.8 real-upstream smoke** pre-promote). Build on the `httpx.MockTransport` real-path pattern (`test_llm_failover_v2.py:326-478`) with a handler emitting SSE chunks on a timer; assert full content + `finish_reason=stop` + usage captured, envelope byte-identical to a buffered fixture. (Council correction stands: a pure delayed-response mock would re-prove the *buffered* death, not streaming survival.)

Additions (council-endorsed + v1.1 #4 + v1.2 folds):
1. **TTFB boundary pair**: first chunk ~100s (passes) vs ~125s (proxy no-forwardable-byte guard fires) — **the ~125s case asserts the guard-fire surfaces as the in-band SSE error envelope AND that the consumption path maps it into the existing retry taxonomy** (v1.2 #5).
2. **include_usage golden**: streamed `usage`/`finish_reason` byte-identical vs buffered fixture; streamed `finish_reason="length"` case asserting Gate 2 (truncation, `generate.py:520-522`) still fires.
3. **Mid-stream abort on primary → backup attempt** (failover semantics preserved through the factory; thread-local URL re-read per attempt).
4. **Fallback + wire-format pins** (v1.1 #4a/4b): (a) **non-stream/wrong-content-type reply to a `stream:true` request → classified into the retry taxonomy, no crash**; (b) **wire-format assert** — the fake client captures `create()` kwargs and the test asserts `stream=True` + `stream_options={"include_usage": True}` are present on the wire.
5. **Heartbeat comment tolerance pin**: the stream iterator tolerates `: heartbeat` / `: connected` comment lines (pin with the REAL proxy token `: heartbeat`, `heartbeat.go:65`; main lane proves it one SDK layer up).
6. **Chain-invariant + defaults test** (Phase 2; v1.1 #5 re-shaped + v1.2 #1b/#6). Asserted constants split by **where they can be asserted from** (v1.2 #6):
   - **Code-assertable from ensemble tests**: `wall_clock_cap_s == 600` (`generate.py:666` import); **max_tokens default resolves to 200000 at every injection site** — (i) instantiate the StructuredTool and invoke with `max_tokens` omitted → effective value 200000 (pins `ports.py:92` schema-default injection, the operative production path); (ii) `execute_dict({})` without the key → 200000 (pins `generate.py:1093`); (iii) dataclass default == 200000 (pins `:101`); (iv) invalid-value fallback (`max_tokens=0`/`"bogus"`) → 200000, not 64000 (pins `:1096-1099`); plus a **no-residual-literal pin** (no `64000` literal remains in `generate.py`/`ports.py`); sketcher dispatch wait constants ≥660s.
   - **Doc-pinned / deploy-gated (proxy-side constants are NOT readable from ensemble tests)**: MGT ≥900s, WriteTimeout derivation, `StreamDeadline` 110s TTFB bound, `IdleTerminationTimeout` 120s inter-chunk bound — asserted at deploy time via helm values review + the proxy repo's own config tests, and behaviorally exercised by §7.1/§7.8.
   - Invariant shape on the code-assertable links: `wall < wait ≤ (doc-pinned MGT + margin)` with **wait ≥ wall + 60s** (strict margin); `inner < wall` demoted to **advisory** (vestigial as a wall bound once 5s heartbeats flow; remains the per-attempt stall bound).
7. **Backward-compat regression**: the existing `TestOdGenerateFacadeWiring` + `TestOdGenerateTimeoutFormula` + `test_generate_model_resolution.py` suites green (wall pins updated atomically in Phase 2, §6.2); 4 sibling raw-SDK factory tests green (nothing changed for them).
8. **Real-upstream smoke before promote**: one real vision-model streamed generation ≥130s through the live proxy — carries the literal >120s duration proof (post-merge, pre-promote ceremony); asserts terminal finish_reason + usage chunks (same shape as Phase 0 probe).
9. *(Conditional)* **Watchdog stall assertion**: fake stream with byte gap >45s → `StreamStalledError` → retry (only if watchdog hardening §6.1(a) adopted).
10. **Phase-3 bound pin** (v1.2 #7): truncation path re-attempts **exactly ONCE** — first `finish_reason="length"` triggers one same-prompt re-attempt; a second truncation fails typed; no third attempt.

## 8. Risks

- 🔴 **Budget-chain min() killer** (§6.2): any unreconciled link silently truncates 200k generations — now explicitly including the **four-site max_tokens default** (v1.2 #1: bumping `generate.py:101` alone leaves the production tool path at 64000 → inner ≈173s while tests stay green — the exact Oct 9 family) and the **HA-ladder-room term** (600s wall at 540s inner = one full attempt; accepted explicitly per decision (a), typed failure, outer retry delegated to the pipeline's ≤3-round loop). One changeset + invariant/defaults test §7.6; Phase 2 is not optional if max_tokens moves.
- 🔴 **Flip-assumption #1**: if the vision upstream rejects `stream:true`, C is dead — Phase 0 probe gates all reliance (authoring may parallel).
- 🟡 **`IdleTerminationTimeout` 120s default-ON** (v1.1 #1): bounds **inter-chunk gaps on the streamed path** — a vision model silent >120s mid-generation would be terminated mid-stream. Healthy streams reset per chunk; buffered path unaffected; **MGT raise does not lift it** — independent knob, env `IDLE_TERMINATION_TIMEOUT` (confirmed, §6.6). Phase-0 probe records max inter-chunk gap; mitigation if measured close to 120s: raise the env knob — config-only.
- 🟡 **Raw-SDK SSE consumption is new code** (no precedent on raw sites) — mitigated: production-proven one SDK layer up (LangChain lane), envelope-invariant design, insulated tests.
- 🟡 **`reasoning_content` over SSE**: verify emission before building the accumulator; else document the parity delta (extraction doesn't consume it today — low impact).
- 🟡 **TTFB >110s** would trip the proxy's no-forwardable-byte guard — measured unlikely (vision models token-stream early); mitigation is a **GLOBAL** `STREAM_DEADLINE` env raise (single knob, affects all streamed routes; per-model scoping = proxy code change, out of zero-diff scope — v1.2 #4).
- 🟡 **Worker occupancy unchanged**: a 170s generation holds one executor worker under every option (pool 5 / semaphore 4) — not a regression; noted for capacity planning (under decision (a), worst-case hold ≈ wall 600s + overshoot, not the ~19min of fork (b)).
- 🟢 **Comment-rot fix while in file** (`generate.py:634-639`): `_OD_FAILOVER_INACTIVE_NOTE` claims `OPENAI_BASE_URL_BACKUP` unset; runtime env HAS it set — failover is live.
- 🟢 **CF plan tier** — immaterial to the C-vs-A ranking (window kills buffered calls regardless; C sidesteps it).

## 9. Sequencing Summary

| Phase | What | Repo(s) | Gate |
|---|---|---|---|
| 0 | Go/no-go streamed probe on vision model via live proxy (expanded assertions §6.0) | none (ops probe) | must PASS before **merge/reliance**; test authoring parallels freely (v1.1 #6) |
| 1 | Streaming factory + explicit ≥400s sketcher waits (one changeset) | ensemble only | compressed-timeline survival test green; wiring pins green; probe PASS |
| 2 | Atomic budget chain for 200k (max_tokens **all four sites**, wall 600 @ `generate.py:666`, wait ≥660, workflow.md:117, 420s test pins ×3 sites, proxy MGT ≥900 env) | ensemble + proxy env (deploy-gated) | chain-invariant + defaults test (§7.6 as split) |
| 3 | One bounded in-adapter retry-on-truncation | ensemble | truncation-path test, **exactly-one bound pinned** (§7.10) |
| — | Option A parked | — | reopen triggers §5.3 |

## 10. Open Questions / User Decisions

1. **Phase 0 probe result — RESOLVED: GO (2026-10-11).** Live `stream:true` vision-model call through the production proxy: all §6.0 assertions PASS — 7996 SSE chunks; terminal `finish_reason` chunk arrived (`length` at max_tokens cap); terminal `usage` chunk arrived (`prompt_tokens: 198 / completion_tokens: 8000`) proving `include_usage` passthrough (no stream_options-400). TTFB 2.92s vs 110s `StreamDeadline`; max inter-chunk gap 2.24s vs 120s `IdleTerminationTimeout`; duration 154.0s (> the ~120s CF window — survived only because streamed); 30 × 5s `: heartbeat` + `: connected` observed. §5.4 flip #1 (vision can't stream) refuted; #2 (STREAM_DEADLINE raise) not triggered. Probe-adjacent: upstream = MiniMax-M3, emits `delta.reasoning_content` then `delta.content` (informs Q4 and the §6.1 SSE join). Evidence: `probe-evidence-phase0.md` (this directory).
2. Confirm `APPLY_ENV_OVERRIDES` is set in the proxy's deployment env before relying on `MAX_GENERATION_TIME` env override at Phase-2 deploy time.
3. ~~Confirm the env override name for `IdleTerminationTimeout`~~ **CLOSED (v1.2 #9)**: overrides exist — `IDLE_TERMINATION_ENABLED` / `IDLE_TERMINATION_TIMEOUT` (`config.go:481-489`).
4. **RESOLVED — probe-adjacent condition SATISFIED (Phase-0 probe, 2026-10-11):** `reasoning_content` SSE emission VERIFIED — upstream vision model is MiniMax-M3; with the `x-proxy-interleaved-thinking` header riding, chunks arrive `delta.reasoning_content` (thinking) then `delta.content` (answer) over the live proxy (evidence: `probe-evidence-phase0.md`, Anomalies 1–2). Accumulator BUILDS per §6.1: join BOTH fields in the SSE consumption; envelope invariant stays strict — `choices[0].message.content` = the `delta.content` join ONLY; `reasoning_content` accumulates separately, NOT consumed by extraction today.
5. Whether `invoke_agent_and_wait` should get a config-backed default bump eventually (vs permanent explicit call-site timeouts).
6. Issue-2 fork (a) stands unless telemetry flips it to (b) — numbers pre-computed in §6.2.

## 11. Evidence Index

- Worker A (proxy): timeout table §2.1; streaming machinery §2.2; state/deploy/auth/config/tests §2.3 — all file:line cited in-repo.
- Worker B (ensemble): call path §3; streaming change surface; submit→poll seam (`_LLM_INVOKER`) + poll-pattern inventory; blast-radius census (LangChain lane sites; 4 raw-SDK siblings); knobs + test patterns.
- Council `5f9b1413`: 5-axis table §4; live-mode default + StreamDeadline-as-TTFB-guard + include_usage-passthrough verifications; budget-chain reconciliation; streaming-shaped mock correction; flip assumptions; unanimous, HIGH confidence.
- Reviewer (2026-10-11): APPROVED-WITH-NOTES — independently re-verified topology against the proxy repo; surfaced `IdleTerminationTimeout` (`config.go:226-227`, `handler.go:1494-1512`, `race_request.go:298-301`) and the HA-ladder-room term; amendments #1–#6 applied as v1.1.
- Approver (2026-10-11, iteration 001): REJECTED on one blocking issue — §6.2 max_tokens touch-list incomplete (verified live 64000 sites at `generate.py:1093`, `:1096-1099`, `ports.py:92`; operative-path analysis confirmed against §3) + 8 non-blocking folds (WriteTimeout arithmetic, §4 total reproduction, watchdog overstatement, global-STREAM_DEADLINE honesty, SSE error-envelope pin, code-assertable vs doc-pinned split, Phase-3 bound pin, citation drift `:621`→`:630` + `:1381`, §10-Q3 closure via `config.go:481-489`); all applied as v1.2. Decision core (C / B-refuted / A-parked) and all 3 hard constraints verified honored.
- Prior art: `.agents/shared/planning/od-generate-agent-lane/architecture-recommendation.md` (2026-10-09) — stage1 lane spec this builds on; deferral history (dev-only 600s MCP knob 2026-10-03; `designer_critic_pipeline.infra_flag` 2026-10-10).

## Appendix — Parked Option A sketch (for when reopen triggers fire)

Seam facts (verified, no design work wasted): routes `POST /v1/async/submit` + `GET /v1/async/jobs/{id}` registering in the `cmd/main.go:282-291` block with in-handler method guards (house style `handler.go:393`); `jobs` table via sqlc migration (`pkg/store/database/migrations/`, queries `GetJob/InsertJob/UpdateJobStatus/DeleteJob`); TTL via soft-delete + `time.Ticker` sweep (no cron infra); shutdown drain hook needed (5s grace kills jobs today); idempotency via `uuid.New()` (dep present); auth decision required (async routes vs `requiresInternalAuth`); in-process job map viable ONLY while `replicaCount: 1` (`k8s/values.yaml:3`) — Postgres jobs table mandatory at multi-replica; ensemble side swaps `_LLM_INVOKER` to submit-leg (reusing `invoke_raw_with_failover` for the POST) + poll loop (patterns: `file_change_monitor._poll_loop`, `wait_exponential_jitter`), poll leg OUTSIDE the facade (thread-local is single-depth, `llm_failover.py:113-115`); poll endpoint itself must emit short responses (never buffered >120s; note `IdleTerminationTimeout` §2.1 is a streamed-path bound and does not affect short poll GETs).
