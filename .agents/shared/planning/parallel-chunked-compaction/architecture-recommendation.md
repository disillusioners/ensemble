# Architecture Recommendation: Parallel Chunked Summarization (Compaction Engine)

Date: 2026-09-01
Analyzed against: `latest @ 7394e716` (dispatch targeted `e863f010`; drift = version-bump commit, `daemon/compaction.py` + tests + FE + `llm_failover.py` byte-identical between the two — line citations valid for both)
Worker: architect-worker-parallel-chunks (`2cfa80d2`), skill `data-flow-design`
Architect verification: critical prompt-construction lines, serial loop, FE copy string, and semaphore wiring independently re-read.

## VERDICT: EASY→MEDIUM — implementable (~1–3 days incl. honest test migration)

## The Critical Question — Answered: INDEPENDENT (parallelizable)

Per-batch prompts are self-contained. Evidence (verified by direct read):

- **`daemon/compaction.py:1219-1228`** — the summarization prompt is a static template
  ("Summarize the following conversation segment. Preserve: …") plus
  `f"Conversation:\n{conversation_text}"`. `conversation_text` is built at **:1194-1217**
  solely from that call's `batch_groups` argument. **No line in the loop references any
  prior batch's summary.**
- **`daemon/compaction.py:1107-1145`** — the serial loop: budget check between iterations
  (:1114), `await self._summarize_single_batch(batch, context)` (:1129),
  `partial_summaries.append(partial)` (:1130). Results are collected, not threaded.
- **`daemon/compaction.py:1159`** — `_merge_summaries(partial_summaries, …)` is the ONLY
  consumer of prior summaries and runs AFTER the loop. Merge/condense (:1238-1308) is
  serial by design (direct merge ≤3, hierarchical pairwise 4+, optional condense).

Not rolling. Not adaptable-needed. Parallelization is safe at the prompt level.

## Recommended Design (Phase 1)

### 1. Bounded parallel batch pool
- Replace the loop body region `compaction.py:1107-1145` with N tasks over
  `_summarize_single_batch`, gated by `asyncio.Semaphore(chunk_concurrency)`.
- Reassemble by **task-list index = batch_idx** — `asyncio.gather` preserves input order.
  **Do NOT use `as_completed`** (loses the chronological invariant that
  `_build_partial_replacement_messages` :1469-1471 relies on).
- Keep the existing per-prompt adaptive timeout (`_summarization_timeout_s`) per task; it
  composes with gather as the per-task failure boundary.

### 2. Budget → shared deadline
- Today: budget checked between calls (:1114; comment :1100-1103 pins "between LLM calls
  only", per prior decisions D-B5/D-B6). Under parallelism: wrap the gather in
  `asyncio.wait_for(gather, timeout=operation_budget_s)` → on `asyncio.TimeoutError`:
  cancel in-flight, keep completed set, `stop_reason="budget"`.
- D-B5/D-B6 constraint is preserved: gather+wait_for lives entirely inside
  `_summarize_chunked`, before any caller-side `aupdate_state` (:1196 / graph.py:3518).
- Cancellation discipline: `except Exception:` only, never `except BaseException: pass`
  (repo lesson — `CancelledError` is `BaseException`). Existing narrowed excepts at
  :1131 (`TimeoutError, asyncio.TimeoutError`) are the pattern to keep.

### 3. Partial-path semantics (contiguous prefix → per-batch set)
- Old rule: keep prefix-completed summaries, drop contiguous un-summarized tail
  (mechanically: `break` at :1126/:1145).
- New rule: keep each **completed** batch's summary in batch-index order; drop each
  incomplete batch's messages individually. Marker semantics unchanged —
  `_build_partial_replacement_messages` (:1464-1488) already RemoveMessages ALL
  compactable groups then re-adds summaries + marker + preserved tail, so it is
  **mechanically correct for non-contiguous survival with zero helper changes**.
- `failed_batches` observability migration: redefine "skipped" = task never acquired the
  semaphore before deadline; "failed" = started and timed out/errored. Tests today
  distinguish these (:1121-1125 comments).
- 🔴 **FE wire copy must change** — `frontend/src/app/components/chat-interface/chat-interface.component.ts:341`:
  "kept the summarized sections, trimmed the un-summarized **older** section" is false
  under non-contiguous survival. Same-PR fix, wording needs a design pass.

### 4. Config knob
- `daemon/config.py` (~:791, CompactionConfig block): `chunk_concurrency: int =
  Field(default=3, ge=1, le=32)` → env `COMPACTION_CHUNK_CONCURRENCY` (matches the
  existing `COMPACTION_*` prefix family: `COMPACTION_OPERATION_BUDGET_S`, etc.).
- **Default 3**, not 4 — the operator runs a local LLM proxy; conservative default,
  one-env-var bump after soak.

### 5. Rate-limit / retry interaction
- Per-call retry budget already exists: tenacity `stop_after_attempt`/`stop_after_delay`
  on 429/5xx (`llm_failover.py:567-572`). N-way concurrency multiplies in-flight retries
  (N×3 worst case) against a proxy with no backup URL. Default 3 keeps this tame.
- **Finding (architect grep):** `manager.py:433` constructs
  `asyncio.Semaphore(config.limits.llm_concurrency)` ("max concurrent LLM calls across
  all instances", default 10, `config.py:471`) — but `_llm_semaphore` has **zero acquire
  sites in daemon/** — it is a dead primitive. Compaction bypasses any global cap today.
  Phase 1: `chunk_concurrency` is the only ceiling; multi-instance stacking (M instances
  × 3 calls) is unbounded but operator-visible. Wiring the global semaphore is a separate
  follow-up (touch every LLM call site; deadlock review vs failover retries) — do not
  bundle.

### 6. Merge/condense — stays serial (Phase 1), with a flagged scaling cliff
- At 26 summaries: hierarchical pairwise ≈ 4 rounds ≈ 14-15 serial calls. If merge-call
  latency ≈ batch-call latency (27s), merge alone ≈ 6-7 min and busts any budget.
  Mitigating assumption (plausible, unverified): merge inputs are short summaries, so
  per-call latency ≪ 27s. **Soak decides.**
- Phase 2 (conditional, defer): parallelize within merge rounds (pairwise tree per round
  is embarrassingly parallel per level, ~2x).
- **batch_size stays 20 in Phase 1.** (Worker suggested shrinking to 12 to raise chunk
  count — overridden in synthesis: shrinking increases merge fan-in and total calls with
  no bench data; treat `batch_size` as a soak-tunable, not a default change.)

### 7. Auto-path impact — acceptable, desirable
All three callers share `compact_state`: proactive (`instance_messaging.py:1185`),
reactive CLE retry (`graph.py:3513`), on-demand `/compact` (`compact_executor.py`).
Parallel chunks reduce wall-clock and timeout incidence for all three. Burst behavior is
bounded by `chunk_concurrency`. No caller-side change required.

### 8. Test migration (honest behavior change, `tests/unit/test_compaction.py`)
- `test_b_second_batch_timeout_partial_summary` (:1600) — rewrite: docstring :1525 pins
  the serial contiguous-prefix contract. New assertions: completed-set may be
  non-contiguous; `failed_batches` = exact complement; marker exactly once. Extend the
  stub (:1613-1621) to simulate non-contiguous completion.
- `test_c_budget_exhaustion_partial_summary` (:1651) — rewrite for deadline semantics
  (`stop_reason="budget"`, non-contiguous surviving set).
- `test_budget_exhaustion_stops_remaining_chunks` (:1823) — rewrite: today pins serial
  early-exit ("stop after first chunk"); new contract = gathered set IS the completion set.
- `test_a_first_batch_timeout_truncation_with_marker` (:1548), `test_proactive_and_reactive_partial_summary_match` (:1722), `TestChunkedOutcomeDataclass` (:1883), `tests/unit/services/test_compact_executor.py` wire-mapping tests — no change.
- NEW: `test_chunked_partial_summary_non_contiguous` (0,2,4 succeed; 1,3,5 fail → 3
  summaries + 6×RemoveMessage + 1 marker).
- NEW: `test_chunked_deadline_cancels_in_flight` (wait_for timeout → budget outcome with
  actually-completed set; in-flight cancelled cleanly).
- Verify `tests/unit/test_compaction_multimodal.py` has no implicit serial-order assertion.

## Budget arithmetic (418k-token case, 26 batches, ~27s/batch)
- Serial today: 26×27 ≈ 702s ≫ 300s → dies at 12/26 (matches user log).
- Concurrency 3: ceil(26/3)×27 ≈ 243s + merge → tight against 300s.
- Concurrency 4: ≈ 175s + merge → fits IF merge calls are fast (see §6).
- Recommendation: **keep 300s default** (design intent "5min max"); document the sizing
  formula `budget ≥ ceil(batches/concurrency) × max_per_batch + merge_budget`; operator
  tunes `COMPACTION_OPERATION_BUDGET_S` / `COMPACTION_CHUNK_CONCURRENCY`.

## Risk List
- 🔴 FE wire copy inaccuracy (`chat-interface.component.ts:341`) — user-facing lie under
  non-contiguous survival; same-PR fix, mandatory.
- 🟡 Merge-phase serial cost at high batch counts — may dominate after chunk speedup;
  soak-gated Phase 2 (parallel merge rounds).
- 🟡 Retry-storm multiplication on 429/5xx (N×3 in-flight retries, single-endpoint local
  proxy, `llm_failover.py:567-572`) — bounded by default 3.
- 🟡 `FailoverController` concurrency safety — concurrent 429s may race the base-URL swap
  (`llm_failover.py:629` mutates state); worker did not trace the lock. Flag for
  implementer review; low likelihood at default 3 but nonzero.
- 🟡 Test semantics migration (3 rewrites + 2 new) — must be written as behavior changes,
  not mechanical renames, or the partial-path contract silently rots.
- 🟢 Dead `_llm_semaphore` (manager.py:433) — pre-existing; wiring it is out of scope but
  recorded here as the natural home for a future global ceiling.
- 🟢 Budget arithmetic at concurrency 3 is tight at 26 batches — document formula; knob
  is one env var.

## Decisions Pending (leader)
1. Default concurrency: 3 (recommended, conservative for local proxies) vs 4 (fits
   arithmetic more comfortably).
2. Keep 300s operation budget (recommended) vs bump for parallel mode.
3. FE replacement copy wording (needs design review).
4. Phase 2 merge parallelization — defer until soak data (recommended).

## Open Questions
- Real merge-call latency and local-proxy throughput at concurrency 3/4/5 (operator
  soak; worker could not bench).
- `FailoverController` swap race under concurrent 429s (implementer review).
- Per-call client construction (`compaction.py:1348` builds a fresh
  `ThinkingChatOpenAI` + wrapper per batch) — N constructions + N HTTP pools under
  parallelism; acceptable for short-lived calls, worth a profile note.
