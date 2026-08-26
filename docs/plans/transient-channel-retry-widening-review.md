# Review: Retryable Classification for Non-Status Transient Channels (L1 Widening)

| Field | Value |
|---|---|
| **Date** | 2026-08-26 |
| **Document reviewed** | [`docs/plans/transient-channel-retry-widening.md`](transient-channel-retry-widening.md) (DRAFT 2026-08-26) |
| **Cross-referenced** | [`docs/bugs/transient-llm-failures-non-retryable-instance-death.md`](../bugs/transient-llm-failures-non-retryable-instance-death.md) · [`docs/retry-architecture.md`](../retry-architecture.md) §4/§9/§11 · `daemon/llm_error_classifier.py` · `daemon/services/llm_failover.py` · `daemon/services/message_processing_errors.py` · `daemon/config.py` · `config.yaml` — all anchors verified read-only 2026-08-26 |
| **Verdict** | **Approved with one required fix** — work unit 2's insertion point shadows `openai.APIResponseValidationError` (§2.1). Four should-address items (§3), one explicit scope caveat (§4). Everything else verified correct (§1). |

---

## 1. Verified correct

- **All `file:line` anchors in the plan are accurate against current code.**
  `_run_with_classification` is `daemon/llm_error_classifier.py:544-608`; the generic branch is `:606-608`; `TRANSIENT_EXCEPTIONS` is `:108-138`; `RetryByCategory` transient membership `:447-462`; slice-swap `_decide_after_count` `:489-516`; `derive_ha_attempt_ceiling` `:168-182`; facade `_classify_raw_sdk_exceptions` `daemon/services/llm_failover.py:236-283`; `_classify_error_type` `daemon/services/message_processing_errors.py:72-148` with the `TransientAPIError` branch at `:136-137` and the `ValueError` catch-all at `:142`; `_parse_csv_or_json_list` `daemon/config.py:50-83`.
- **The additive-only thesis holds.** Membership in `TRANSIENT_EXCEPTIONS` is the single lever: the predicate counts members transient (`:447-462`) and transient counts drive the L2 primary-slice swap (`:489-516`), so the new channels also trigger L2 backup failover with no further wiring. Budget/backoff/ceiling code paths untouched.
- **Corpus classification vs proposed defaults is correct.** `All models rate limited` (21 events) and `context deadline exceeded` (2) hit the allowlist; `Token Plan usage limit … (2056)` hits the blocklist (`token plan`/`usage limit`) → stays terminal; `tool call result … (2013)` gets no allowlist hit → stays terminal; `no generations found` (4) and `ultimate_model_retry_exhausted` (8) hit the ValueError patterns. 44/47 converted to L1 retries, terminal shapes unchanged.
- **Work unit 3 placement is safe.** A `ValueError` branch immediately before the generic `except Exception` (`:606`) is shadowed by nothing — no earlier handler catches `ValueError`.
- **Work unit 4 needs no classifier branch.** `httpx.RemoteProtocolError` propagates unchanged through the generic handler; tuple membership alone suffices (`httpx` already imported at `:7`). Declining the broader `httpx.ProtocolError`/`TransportError` parents is the right call. `httpx.ReadTimeout` ⊂ `TimeoutException` ∈ `TIMEOUT_EXCEPTIONS` (`:142-146`) — confirmed, re-verification-only is correct.
- **Work unit 5 parity is real, not assumed.** `_classify_raw_sdk_exceptions` converts only `APIStatusError` today (`llm_failover.py:272-282`); the facade already imports from `llm_error_classifier` (`:221-228`), so importing the shared matchers gives parity with no pattern-list duplication.
- **Work unit 6 is safe from the `ValueError` catch-all.** `TransientLLMError` subclasses `Exception` directly, so `message_processing_errors.py:142` cannot swallow it; placing the branch beside `:136-137` is correct.
- **Test 7's "10 attempts" expectation matches the `count < transient_max` convention** documented in the strategy docstring (`llm_error_classifier.py:370-374`).
- **Not subclassing `TransientAPIError`** is correct — its ctor requires an `APIStatusError` and `.status_code`.

## 2. Required before implementation

### 2.1 🔴 Work unit 2 insertion point shadows `openai.APIResponseValidationError`

The plan says to insert the bare-`openai.APIError` branch *"after the `APIConnectionError`/socket handlers, **before** `LLMResponseValidationError`"* — i.e. between `:572` and `:573`. But
`openai.APIResponseValidationError` (handled at `:576-579`) is a **direct subclass of `openai.APIError`** and sits *after* that insertion point in the except chain. The new branch would catch it first:

- the existing `:576-579` handler becomes dead code;
- it logs `[LLM] Non-retryable API error: …` for an exception that **is** retried (retryability survives only by accident — the plain re-raise preserves `TRANSIENT_EXCEPTIONS` membership via `:127`), producing a log that contradicts actual behavior.

**Fix:** insert the branch **after** the `openai.APIResponseValidationError` handler (after `:579`), keeping every existing subclass handler above it. Also add `APIResponseValidationError` to test 4's subclass-shadow regression list, which currently only covers `APIConnectionError`/`APITimeoutError`/`APIStatusError`.

## 3. Should address (non-blocking)

### 3.1 Budget category for relayed timeouts (C1 `context deadline exceeded`)

The wrapper counts these as **transient** (10 attempts), not timeout (3). Each attempt can cost the upstream's full timeout, and the hot path has **no wall-clock cap** (bounded only by `task_timeout_minutes=125`; see `docs/retry-architecture.md` §5's own asymmetry warning). Worst case ≈ 10 × upstream-timeout + jitter — the exact wall-clock amplification the architecture doc warns about. §6's open question treats the split as metrics-only; it is actually a budget question. Either:

- route a `kind='timeout_body'` to the **timeout** category inside `RetryByCategory` (small predicate change — slightly violates "membership is the only lever", so document it), **or**
- keep transient classification and put the accepted wall-clock math in §5's risk table.

Pick one explicitly before merge.

### 3.2 Log-anchor drift

Non-matching bare `APIError`s will now log `[LLM] Non-retryable API error: …` instead of `[LLM] Unexpected error (will not retry)`. The old string is an evidence anchor — the bug doc's extraction recipe greps it (`§Evidence anchors`), as may dashboards. The plan should note the new log line so future corpus extraction greps both.

### 3.3 Config placement

The sketch puts the new keys under `llm:`, but the sibling retry settings `llm_retry_transient_attempts` / `llm_retry_timeout_attempts` live under `queue:` in `config.yaml:91-106` (with their explanatory comment block). Follow wherever `LLMConfig` actually reads from — do not create a second home for LLM retry configuration. If the keys end up under `queue:`, update §7's yaml sketch accordingly.

### 3.4 Blocklist dead entries

`unauthorized` / `api key` can never reach the bare-`APIError` branch: auth errors arrive as `APIStatusError` subclasses and are caught at `:558-563`. Harmless defense-in-depth, but trim them or comment that they are unreachable-by-design to avoid implying coverage.

## 4. Scope caveat — necessary, not sufficient

The plan is honest that parking is out of scope, but the acceptance criteria should state the **expected residual behavior** explicitly: after this ships, a provider-wide outage still burns the full budget in ~1–2 min, the instance still dies `ERROR`, the parent still receives `RECOVERY_GUIDANCE_HINT` (the replacement-storm mechanism — `docs/retry-architecture.md` gap #7 — persists until the parking plan lands), and observer-lane jobs still strand. Post-ship, a repeat of the Aug-26 06:51 storm sends ~10× more requests at an already-dying provider before the same 15 deaths. Recording this now prevents it being mistaken for a regression later.

## 5. Summary for the implementer

| Item | Disposition |
|---|---|
| §2.1 insertion point (`APIError` branch after `:579`, not before `:573`) | **Required** — merge blocker |
| §3.1 timeout-body budget category | Decide + document before merge |
| §3.2 log-anchor note, §3.3 config home, §3.4 blocklist trim | Address in plan text |
| §4 residual-behavior note in §4 acceptance criteria | Address in plan text |
| Everything else (units 1, 3, 4, 5, 6, 7, 8, compat guarantees, risks) | Verified sound as written |
