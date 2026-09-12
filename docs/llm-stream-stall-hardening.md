# LLM Stream-Stall Hardening — Investigation Findings & Implementation Plan

| Field | Value |
|---|---|
| **Date** | 2026-09-10 |
| **Status** | **Draft — pending team review** |
| **Evidence** | Two read-only code investigations — 2026-08-27 (3-worker fan-out: timeout config / error classification / streaming seams) and 2026-09-10 delta re-verification at HEAD `31f2f216` (683 commits between). All `file:line` anchors cite the 2026-09-10 state unless marked `(2026-08-27)`. |
| **Related** | [`docs/retry-architecture.md`](retry-architecture.md) (2026-08-26 snapshot — several anchors now stale; see §7) |

---

## 1. Background — the 2026-08-27 incident

A code-review worker's streaming LLM call (model=coding) stalled for ~26 minutes. Timeline (from `ensemble.log` 2026-08-27):

- 14:30:53 — LLM invoke (89 messages) starts; HTTP read deadline armed (~610s, monotonic clock).
- 14:31:02–14:46:37 — **entire daemon frozen, zero log lines for 15.6 min** (machine sleep; macOS monotonic clock does not advance during sleep, so the read deadline froze with it).
- 14:46:37 — wake; all overdue timers fire at once (slack reconnects 935s+, readiness degraded).
- 14:56:45 — read deadline expires (610s of *awake* monotonic time later) → `ReadTimeout`.
- 14:56:54 — tenacity retry succeeds; worker resumes and completes at 14:58:32.

Two conclusions: (a) the stall class is real but was mostly sleep-amplified — fixed at the environment layer (`sudo pmset -c sleep 0`, applied); (b) detection is too slow (610s) and observability is misleading (see §2), motivating the plan in §4.

## 2. Executive summary — three premise corrections

The investigations corrected three assumptions that shaped the original proposal:

| # | Assumption | Reality | Evidence |
|---|---|---|---|
| 1 | "ReadTimeout is non-retryable — the log says will not retry" | **False.** `httpx.ReadTimeout` is a member of `TIMEOUT_EXCEPTIONS` (via parent `httpx.TimeoutException`) and already retries under the `timeout_attempts=3` budget. The log line comes from the catch-all `except Exception` branch and is simply lying. | `daemon/llm_error_classifier.py:483-487`, `:791-800`, `:1025` |
| 2 | "The 26-min call hung on the SDK's 600s default" | **Ours.** Configured `request_timeout: 610` (`config.yaml:25` → `LLMConfig` `daemon/config.py:141`, threaded at `daemon/services/instance_lifecycle.py:1229`). Incident math closes exactly. | §1 timeline |
| 3 | "A watchdog can hook the async client or the chunk layer" | **Neither.** The hot path is sync invoke on an executor thread (`daemon/graph.py:4692-4698`); the async client is constructed but never exercised. SSE `:heartbeat` comment lines are dropped by the openai SDK decoder (`openai/_streaming.py:360`) before LangChain sees anything — only the raw-byte transport layer observes them. | `daemon/graph.py:4692-4698`; `openai==2.24.0` `_streaming.py` |

## 3. Verified current state (2026-09-10)

### 3.1 LLM call-site timeout map (14 sites)

| # | Site | Construction | Effective HTTP timeout today |
|---|---|---|---|
| 1 | Agent chat hot path (standard+vision) | `daemon/graph.py:5182-5196` via `clean_llm_config` (`daemon/graph.py:2297-2432`) | **610s** (`LLMConfig.request_timeout`) |
| 2 | LoopRepairer summarization | `daemon/graph.py:1631-1632` | 610s + outer wait_for |
| 3 | WatchoverEvaluator | `daemon/graph.py:5809-5810` | 610s + wait_for (10s default) |
| 4 | WatcherContextBuilder | `daemon/services/watcher_context_builder.py:149-150` | 610s + wait_for (300s) |
| 5 | Compaction summarization | `daemon/compaction.py:3352/3364` | 610s HTTP + **adaptive wall-clock cap** (90s + 60s/100k tokens, cap 300s, `daemon/config.py:871-913`, `daemon/compaction.py:1145-1167`) |
| 6 | Title generation | `daemon/services/title_generation.py:90-111` | **request_timeout OMITTED** + wait_for 30s + facade cap |
| 7 | Keyword extraction | `daemon/services/keyword_extraction.py:363-384` | **OMITTED** + wait_for |
| 8 | Child-report summarization | `daemon/services/child_reports.py:794` | **OMITTED** + wait_for 30s |
| 9 | Child-report repair | `daemon/services/child_reports.py:1472` | **OMITTED** + wait_for |
| 10 | LCA report judge (NEW since 2026-08-27) | `daemon/services/attestation_report_judge.py:484` | **min(25s judge timeout, 610s)** — punch-list `d6e30d9d` added per-attempt timeout precisely because a hung first attempt pins the `to_thread` worker |
| 11-13 | Skill search / evolution / embedding chat (raw SDK) | `daemon/services/skill_search_service.py:144-152`, `skill_evolution_service.py:94-101`, `skill_embedding_service.py:145-152` | SDK default 600s + facade cap 45s (between attempts); `request_timeout` threaded from `daemon/manager.py:1213` is **dead config** (never read) |
| 14 | Embeddings calls (raw SDK) | `daemon/services/skill_embedding_service.py:193-200` | SDK default 600s |

Key trap (2026-08-27 finding, mechanically verified then): when a LangChain site omits `request_timeout`, LangChain passes `timeout=None` explicitly; the openai SDK treats explicit `None` as given → httpx deadline disabled at the HTTP layer; only the abandon-but-don't-kill `asyncio.wait_for` caps remain (the executor thread + socket stay alive). The 2026-09-10 re-verification contested the exact mechanics (SDK-default-600s reading, taken from a docblock without source-trace) — see §6, contradiction #1. Either way the omission is a bug; §4 L0 fixes it.

### 3.2 Error classification & retry pipeline

- Classification wrapper: `classify_llm_errors._run_with_classification` (`daemon/llm_error_classifier.py:907-1027`), branch order load-bearing: BadRequest `:913` → APIStatus `:921` → **APITimeout `:927`** → APIConnection `:930` → socket errors `:933` → … → catch-all `:1025` ("Unexpected error (will not retry)").
- `TIMEOUT_EXCEPTIONS = (openai.APITimeoutError, httpx.TimeoutException, TimeoutError)` at `:483-487`; checked FIRST in `RetryByCategory` `:791-800` (timeout-first ordering is deliberate — `APITimeoutError` inherits `APIConnectionError`).
- Important asymmetry: the openai SDK wraps **request-level** timeouts as `APITimeoutError` (caught `:927`), but **mid-stream** read timeouts during SSE chunk iteration escape the SDK wrapper and arrive as bare `httpx.ReadTimeout` → catch-all `:1025` → misleading log, but tenacity still retries via `TIMEOUT_EXCEPTIONS` membership.
- Retry budgets: `llm_retry_transient_attempts=10` / `llm_retry_timeout_attempts=3` (`daemon/config.py:528/:530`); HA ceiling 13 (`derive_ha_attempt_ceiling`, `daemon/llm_error_classifier.py:509-523`); primary slice `PRIMARY_TIMEOUT_MAX=2` (`:506`).
- `agent_node` retry-exhausted catch tuple `daemon/graph.py:4908-4914` does NOT include `httpx.TimeoutException` → exhausted timeouts log as "Unexpected error after retries" (`:4915-4917`) — second misleading line.
- Wall-clock amplification context: 13 attempts × 610s worst case ≈ 2h stuck on one invoke. L0 (§4) shrinks this to minutes.

### 3.3 Streaming path, client ownership, injection seams

- Hot path: sync `current_llm.invoke` offloaded via `loop.run_in_executor` (`daemon/graph.py:4692-4698`); tenacity wraps at `_run_with_retry` (`:5227-5228`) built by `_wire_retry_and_failover` (`:5022`, Retrying at `:5133-5138`).
- `clean_llm_config` (`daemon/graph.py:2297-2432`) is the single chokepoint for all LangChain sites: strips `model_vision` / `base_url_backup` / `buffer_response_header` (`:2362-2366`); injects `streaming` (`:2376-2383`) and `stream_usage`; **conditionally injects gzip http clients** when `OPENAI_REQUEST_GZIP=true` (`:2417-2431`; dev `.env` has it ON).
- Gzip feature (commit `3e17576b`): `GzipRequestTransport` / `make_gzip_httpx_client` (`daemon/services/llm_gzip.py:239-336`) — a committed, tested precedent of exactly the transport-wrapper pattern the watchdog needs; pinned to `httpx 0.28.1` private API (`httpx._content.ByteStream`).
- `X-LLMProxy-Buffer-Response: true` header (commit `85ae6e72`): wired via `default_headers` at 6 sites (e.g. `daemon/graph.py:7151-7159`), default ON (`daemon/config.py:233-242`). Header-only — zero transport collision — but proxy-side buffering-vs-streaming semantics are unverified (ops question, §8).
- Failover: `FailoverController._mutate_client_base_url` (`daemon/llm_error_classifier.py:639-692`) mutates `base_url` in place on the same clients → injected http clients/timeouts survive primary↔backup swaps.
- Partial-content safety: streamed chunks accumulate in a function-local list (langchain-core `chat_models.py:1185-1233`); daemon persists only after invoke returns (F2 hoist, `daemon/graph.py:4975-5017`) → mid-stream abort leaves zero partial residue in checkpoint.
- Dependency pins (unchanged across both investigations): openai 2.24.0, httpx 0.28.1, httpcore 1.0.9, langchain-openai 1.1.10, langchain-core 1.2.16.

## 4. The hardening plan — L0 / L1 / L2

Three layers, validated by both investigations:

- **L1 — log-truth fix (was: "make timeouts retryable" — they already are).** Two surgical, behavior-preserving patches: explicit `except httpx.TimeoutException` branch before the catch-all (`daemon/llm_error_classifier.py:1025`, ~5 lines) and add `httpx.TimeoutException` to the `agent_node` catch tuple (`daemon/graph.py:4908-4914`, ~1 line).
- **L0 — timeout tightening + latent-bug fix.** `clean_llm_config` inject-if-absent `request_timeout` (copies the streaming-injection pattern; one edit covers all LangChain sites and fixes the four OMITTED sites in §3.1); per-endpoint knob `LLMConfig.request_timeout_backup` (mirroring `base_url_backup`); keep 610s default, tighten the read timeout to a 45s-class value for streaming endpoints — safe precisely because heartbeat keep-alives bound legitimate inter-byte gaps at ~10–15s (see L2 rationale below) —. ⚠️ Do NOT tighten the raw-SDK sites (11-14) — they are non-streaming; 45s would false-abort legitimate generations. Coordinate: retune facade `wall_clock_cap_s` (`daemon/services/llm_failover.py:567-572`) in the same change.
- **L2 — wall-clock byte-liveness watchdog.** Custom sync `httpx.Client(transport=WatchdogHTTPTransport(...))` injected via the same chokepoint. Transport wraps each SSE response's byte stream (gate on `Content-Type: text/event-stream`; non-SSE no-ops); every raw read stamps `time.time()` (wall clock — immune to the monotonic-freeze-during-sleep hole); a daemon-wide watchdog thread (1s tick) closes streams whose last-byte gap exceeds ~45s; the wrapper re-types the resulting `httpx.ReadError` into a retryable exception (raw `ReadError` is non-retryable, `daemon/llm_error_classifier.py:433-479`). Per-response scoping (create in `handle_request`, deregister in `finally`) makes stale-timestamp leakage across retry attempts impossible. **New since re-verification: must COMPOSE with the gzip transport** — recommended order `WatchdogHTTPTransport(GzipRequestTransport(httpx.HTTPTransport()))` (watchdog owns the response stream; gzip mutates request bytes only); both share the httpx 0.28.1 pin.

  **Heartbeat as the liveness signal (design rationale).** The proxies emit SSE `:heartbeat` comment keep-alives (~10–15s cadence, operator-verified on the primary) while a request is in flight. This bounds legitimate inter-byte silence: a healthy stream — even during long thinking pauses with zero content tokens — always carries bytes within roughly one heartbeat interval. Therefore "no bytes for > threshold (3–4× heartbeat cadence; ~45s default)" is a reliable discriminator for a dead connection or stalled stream. Because the openai SDK drops comment lines above the transport layer (§3.3), the liveness signal must be observed at the raw-byte transport layer — which is exactly where the watchdog sits. Net effect: dead-connection detection drops from 610s (today's read deadline) to ~45s, and the abort rides the existing tenacity retry + base_url failover for immediate recovery instead of a zombie wait. This inverts the heartbeat's original job: it was added to keep intermediaries from killing idle connections (CF 125s); here it doubles as the client-side health beacon. Caveat: heartbeat proves the transport path is alive, NOT that the upstream LLM is progressing — the total-generation wall-clock cap remains the backstop for that class.

## 5. Consolidated decision table

Legend — Fix class: 🔴 Must-fix (active bug / blocks plan correctness) · 🟠 Should-fix (required with its layer) · 🟡 Nice-to-have · ⚪ Optional. Effort: XS <1h · S 1-2h · M half-day+.

| # | Hardship / Finding | Fix class | Priority | Packet | Effort | Confidence | Evidence | Notes / Depends on |
|---|---|---|---|---|---|---|---|---|
| 1 | Classifier logs "will not retry" for timeout-class errors that actually retry | 🔴 Must-fix | P0 | L1-fix | S | 🟢 | `llm_error_classifier.py:1025` | Ship now |
| 2 | `agent_node` catch tuple lacks `httpx.TimeoutException` → exhausted timeouts log misleadingly | 🔴 Must-fix | P0 | L1-fix | XS | 🟢 | `graph.py:4908-4917` | With #1 |
| 3 | 4 secondary sites omit `request_timeout` → hung first attempt pins `to_thread` worker (LCA `d6e30d9d` fixed the identical bug) | 🔴 Must-fix | P0 | L0-fix | S-M | 🟢 omission / 🟡 mechanics | §3.1 sites 6-9; precedent `attestation_report_judge.py:448-471` | Inject-if-absent resolves mechanics dispute moot |
| 4 | Heartbeat cadence unknown on backup proxy; `X-LLMProxy-Buffer-Response` wire semantics unknown | 🟠 Should-fix (gate) | P0-gate | Ops probe | S | 🟡 | `config.py:233-242` | Blocks only the tuning VALUE of #5, not the code |
| 5 | Tighten per-endpoint read timeout (45s class, streaming sites); `request_timeout_backup` knob | 🔴 Must-fix (core) | P1 | L0-fix | M | 🟢 | `config.py:141`; swap-safe `llm_error_classifier.py:639-692` | Value depends on #4 |
| 6 | Facade `wall_clock_cap_s=45` stops truncating storms once one attempt can consume 45s+ | 🟠 Should-fix | P1 | L0-fix | S | 🟢 | `llm_failover.py:567-572` | Same PR as #5 |
| 7 | Watchdog forced-close surfaces as non-retryable `httpx.ReadError` | 🔴 Must-fix (with watchdog) | P1 | Watchdog | S | 🟢 | `llm_error_classifier.py:433-479` | Re-type at wrapper |
| 8 | Gzip transport occupies the `http_client` seam; watchdog must compose; shared httpx 0.28.1 pin | 🟠 Should-fix (design) | P1 | Watchdog | S | 🟢 | `llm_gzip.py:239-336`; `graph.py:2417-2431` | Watchdog outermost |
| 9 | Hot path is sync; async client dormant — async-only injection = dead code | 🟠 Design constraint | P1 | Watchdog | XS | 🟢 | `graph.py:4692-4698` | Sync client only |
| 10 | SSE `:heartbeat` invisible above transport; non-SSE must no-op | 🟠 Design constraint | P1 | Watchdog | S | 🟢 | `openai/_streaming.py:360` | Core design |
| 11 | macOS sync close-unblocks-recv empirically unverified | 🟠 Should-fix (gate) | P1-gate | Experiment | S | 🟡 | async analog confirmed only | Must pass before watchdog merges |
| 12 | Watchdog thread lifecycle + threshold knob home | 🟠 Design decision | P1 | Watchdog | S | 🟡 | — | Coder brief |
| 13 | Post-wake policy: abort vs grace | 🟡 Decision | P2 | Watchdog | XS | 🟡 | — | Default: abort-immediately |
| 14 | Raw-SDK sites: no explicit timeout + dead `request_timeout` config. ⚠️ Do NOT tighten (non-streaming) | 🟡 Nice-to-have | P2 | Cleanup | S | 🟢 | `manager.py:1213` | Keep ≥600s until they stream |
| 15 | `docs/retry-architecture.md` stale (anchors; 9→10 facade sites; "45s" claim) | 🟡 Nice-to-have | P2 | Doc | S | 🟡 | §7 | Update with #1/#5 PR |
| 16 | `wrap_langchain_failover` seam gap — cleans config but never rebuilds client | 🟡 Nice-to-have | P2 | Audit | S | 🟡 | `llm_failover.py:678-692` | Verify before watchdog rollout |
| 17 | L3 turn-retry never engages for timeout exhaustion (no error_type stamping) | 🟡 Nice-to-have | P2 | Future | M | 🟢 | `worker_pool.py:825-836` | Defer |
| 18 | Retry-knob `default=` binds `PRIMARY_*` at def-time (hot rotation stale) | 🟡 Nice-to-have | P2 | Doc note | XS | 🟢 | `llm_error_classifier.py:497-523` | Document only |
| 19 | 13:52 incident ~11s `ReadTimeout` unexplained by any mapped deadline | ⚪ Optional | P3 | Tester backlog | S | 🔴 | logs 2026-08-27 | Non-blocking |
| 20 | Per-model timeout (`LLMModelWeight` = model+weight, `extra="ignore"`) | ⚪ Optional | P3 | Deferred | M | 🟢 | `registry.py:140-200` | Per-endpoint covers need |
| 21 | Wire `http_async_client` alongside sync | ⚪ Optional | P3 | YAGNI | XS | 🟢 | — | Only if async migration |

Recommended order: #1+#2+#3 (three small worker dispatches, disjoint files) → #4 probe (ops) → #5+#6 → #7-#12 watchdog coder brief → #14-#16 follow-up batch.

## 6. Adjudicated contradictions (for reviewer awareness)

1. **"4 sites = HTTP timeouts disabled (None)" vs "SDK default 600s"** — 2026-08-27 traced installed langchain+openai source (None is passed explicitly → deadline disabled); 2026-09-10 re-verification reasoned "falls to SDK default" from a docblock without source-trace. Adjudication: 2026-08-27 mechanics stand (higher evidence quality, 🟡), but moot for the fix — inject-if-absent closes it either way.
2. **"read-timeout exposure is theoretical" vs incident evidence** — request-level timeouts ARE wrapped as `APITimeoutError` (caught `:927`); MID-STREAM timeouts escape as bare `httpx.ReadTimeout` — exactly the 14:56:45 incident class. Both true in their domains; L1 patch remains warranted.

## 7. `docs/retry-architecture.md` drift (update alongside this plan)

That doc is a self-warned 2026-08-26 snapshot: most anchors drifted (~1,500 lines in `daemon/graph.py`); "9 secondary sites" → now 10 facade sites (LCA judge added); "45s wall-clock cap" no longer representative (compaction adaptive 90-300s; judge 25s). Key current anchors: retry knobs `config.py:528/:530`; ceiling `llm_error_classifier.py:509-523`; hot-path invoke `graph.py:4692-4698`; catch tuple `graph.py:4908-4917`.

## 8. Open questions & pending decisions

1. **Ops probe (double-duty):** heartbeat cadence on BOTH proxies (`llm.ensem.dev` primary, `llm.daoduc.org` backup) AND whether `X-LLMProxy-Buffer-Response` changes stream delivery — one wire-capture answers both. Gates only the 45s tuning value.
2. Post-wake policy (abort-immediately recommended).
3. Watchdog abort exception type: new `StreamLivenessError` (clean telemetry; requires classifier registration) vs reuse a `TRANSIENT_EXCEPTIONS` member (works today, muddier).
4. macOS sync close-unblock experiment (#11) — 10-line spike before watchdog merge.
5. 13:52 anomaly (#19) — carried as diagnostic debt.

## 9. Provenance & references

- Investigations: 2026-08-27 workers 23dbd352 (L0 timeout config), f4aadc03 (L1 classifier), 276a2ece (L2 streaming seams); 2026-09-10 workers 8d928d73 (config/classifier delta), 1a424457 (stream-seams delta). Dispatcher/synthesis: developer[v2].
- Key commits in the 13-day window: `85ae6e72` (X-LLMProxy-Buffer-Response), `3e17576b` (OPENAI_REQUEST_GZIP), `d6e30d9d`+`7a899517` (LCA judge + per-attempt timeout), `59951b8f` (compaction adaptive timeout), `f6be340f` (PR2 perf taps).
- Environment fix applied 2026-08-27: `sudo pmset -c sleep 0` (AC never-sleep) — removes the sleep-amplified stall class at the OS layer.
