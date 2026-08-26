# Plan: Retryable Classification for Non-Status Transient Channels (L1 Widening)

| Field | Value |
|---|---|
| **Status** | IMPLEMENTED 2026-08-27 — all 8 work units landed (review findings incorporated 2026-08-26: §2.1 insertion-point fix applied; §3.1 resolved as timeout-category routing; §3.2–3.4 and §4 addressed). Acceptance verified by targeted test packs (classifier / facade parity / error-report / config). |
| **Goal** | Make the four non-status-code transient failure channels (bare `openai.APIError`, 200-body `ValueError` shapes, mid-stream `httpx.RemoteProtocolError`, relayed upstream timeouts) retryable at L1, so the configured 10-attempt transient budget actually runs instead of dying at attempt 1. Genuine terminal errors (quota, bad params, auth) stay byte-identically non-retryable. |
| **Scope** | MEDIUM — single coder. Spans `daemon/llm_error_classifier.py` (exception + branches + `TRANSIENT_EXCEPTIONS`), `daemon/services/llm_failover.py` (`_classify_raw_sdk_exceptions` parity), `daemon/services/message_processing_errors.py` (error-type mapping), `daemon/config.py` + `config.yaml` (pattern lists), targeted tests. |
| **Risk** | Pattern false-positives making genuine bugs retryable (bounded by allowlist + blocklist + regression tests); interaction with the parking plan's `ProviderRateLimitError` (keep disjoint — see §6). |
| **Evidence** | Fatality corpus in [`docs/bugs/transient-llm-failures-non-retryable-instance-death.md`](../bugs/transient-llm-failures-non-retryable-instance-death.md) — 47 instance-ERROR events 2026-08-19→26, 44 transient-with-zero-retries. All `file:line` anchors verified read-only 2026-08-26 (reviewer-verified). |
| **Related** | [`docs/plans/transient-channel-retry-widening-review.md`](transient-channel-retry-widening-review.md) (review — approved with required fix, incorporated) · [`docs/bugs/transient-llm-failures-non-retryable-instance-death.md`](../bugs/transient-llm-failures-non-retryable-instance-death.md) (why) · [`docs/plans/rate-limit-episode-parking.md`](rate-limit-episode-parking.md) (post-exhaustion parking — complementary, see §6) · [`docs/retry-architecture.md`](../retry-architecture.md) §4/§9/§11 |
| **Proxy dependency** | The proxy owner is deploying ultimate-routing transparency (bug doc RC2 note), which will eliminate the `ultimate_model_retry_exhausted` channel at the source. This plan treats that pattern as **config-driven** (removable without code change). All other channels are proxy-independent. |

---

## 1. Problem

The classifier `_run_with_classification` (`daemon/llm_error_classifier.py:544-608`) treats
as retryable only: `APIStatusError` with retryable status (`:558-563`), `APITimeoutError`,
`APIConnectionError`, raw socket errors, and the typed validation exceptions. Everything
else falls into the generic `except Exception` at `:606-608` — logged
`[LLM] Unexpected error (will not retry)` and re-raised — so tenacity's predicate never
matches and the turn dies on **attempt 1 of 10**.

The production corpus (bug doc) shows 44 of 47 instance deaths over 7 days arrive through
channels that never match a retryable branch:

| Channel | Terminal error | Events | Exception type today |
|---|---|---|---|
| C1 | `All models rate limited` / relayed `context deadline exceeded` | 23 | bare `openai.APIError` (no status code) |
| C2 | `{'type': 'ultimate_model_retry_exhausted'}` (HTTP 200 dict body) | 8 | `ValueError` (parsed by LangChain) |
| C3 | `peer closed connection ... (incomplete chunked read)` | 7 | `httpx.RemoteProtocolError` (confirmed: raised through `httpx._transports.default.map_httpcore_exceptions`) |
| C4 | `No generations found in stream.` (HTTP 200, zero-chunk SSE) | 4 | `ValueError` (from LangChain stream aggregation) |

All four are transient by nature. The single 18:52 `ReadTimeout` sighting did **not** kill
an instance (`httpx.ReadTimeout` ⊂ `httpx.TimeoutException` ∈ `TIMEOUT_EXCEPTIONS` —
already retryable); membership is re-verified in work unit 4 but no fix expected.

## 2. Design

Additive-only, with one documented predicate exception (unit 2a). One new wrapper
exception + three classifier branches + one tuple member + one error-type mapping.
L1/L2 mechanics (budget, backoff, ceiling) are otherwise untouched — membership in
`TRANSIENT_EXCEPTIONS` is sufficient: `RetryByCategory` (`:447-464`) already counts
members as transient, and transient counts drive the primary-slice swap (`:489-516`), so
proxy hiccups now also trigger L2 backup failover; timeout-kind routing (unit 2a) is the
sole deviation.

### Work unit 1 — New wrapper exception

In `daemon/llm_error_classifier.py`, next to `TransientAPIError` (`:45`):

```python
class TransientLLMError(Exception):
    """Transient failure delivered through a non-status channel (bare
    APIError message, 200-body ValueError, stream shape). Wrapping makes
    it a TRANSIENT_EXCEPTIONS member so L1 tenacity / L2 failover treat
    it like any transient error."""

    def __init__(self, kind: str, original: BaseException):
        self.kind = kind          # 'api_error_body' | 'value_error_body' | ...
        self.original = original
        super().__init__(f"Transient LLM error ({kind}): {original}")
```

Add `TransientLLMError` to `TRANSIENT_EXCEPTIONS` (`:108`). Do **not** subclass
`TransientAPIError` (its ctor requires an `APIStatusError` and `.status_code`).

The `kind` field carries the budget category consumed by the predicate (unit 2a):
`'api_error_body'` / `'value_error_body'` → transient; `'timeout_body'` → timeout.

### Work unit 2a — Timeout-kind routing in `RetryByCategory` (review §3.1, decided)

Relayed upstream timeouts (`context deadline exceeded`, C1's 2 events) get
`kind='timeout_body'`. Each attempt can cost the upstream's full timeout and the hot path
has no wall-clock cap — budgeting them as transient (10 attempts) risks the exact
wall-clock amplification `docs/retry-architecture.md` §5 warns about. In
`RetryByCategory.__call__` (`:440`), extend the timeout check:

```python
if isinstance(exception, TIMEOUT_EXCEPTIONS) or (
    isinstance(exception, TransientLLMError) and exception.kind == "timeout_body"
):
```

**Documented deviation** from "membership is the only lever": this is the single
predicate change in the plan; budget/backoff/ceiling wiring is otherwise untouched.
Timeout-kind attempts consume the 3-attempt timeout budget and drive the timeout
primary-slice cap (`:489-516`), consistent with `APITimeoutError` treatment.

### Work unit 2 — C1: bare `openai.APIError` message patterns

In `_run_with_classification`, insert the branch **after** the
`openai.APIResponseValidationError` handler (after `:579`), keeping every existing
subclass handler above it. **Placement is load-bearing (review §2.1):**
`APIResponseValidationError` is a direct subclass of `APIError` (MRO verified) — an
earlier insertion would shadow the `:576-579` handler, making it dead code and logging
`Non-retryable` for an exception that retries.

```python
except openai.APIError as e:   # bare APIError — no status code channel
    kind = "timeout_body" if _matches_timeout_body(str(e)) else "api_error_body"
    if _matches_transient_apierror(str(e)):
        logger.warning(f"[LLM] Transient API error (bare, pattern-matched), will retry: {_truncate_error(e)}")
        raise TransientLLMError(kind, e) from e
    logger.error(f"[LLM] Non-retryable API error: {_truncate_error(e)}")
    raise
```

`_matches_transient_apierror(msg)`: case-insensitive substring allowlist **and not**
blocklist. Allowlist/blocklist from config (work unit 7). Blocklist is mandatory-severity:
allowlist hit + blocklist hit → non-retryable (protects quota shapes like
`Token Plan usage limit reached`, which shares wording families with allowlist entries).

**Log-anchor note (review §3.2):** non-matching bare `APIError`s now log
`[LLM] Non-retryable API error: …` instead of `[LLM] Unexpected error (will not retry)`.
The old string is an evidence anchor (bug-doc extraction greps it) — corpus extraction
must grep **both** strings; the bug doc's Evidence-anchors section is updated at
implementation time (acceptance 5).

Expected classification of the corpus:
- `All models rate limited` (21 events) → allowlist `all models rate limited` → retryable, transient category.
- `context deadline exceeded (Client.Timeout ...)` (2) → allowlist `context deadline exceeded` → retryable, **timeout category** (unit 2a).
- `Token Plan usage limit reached ... (2056)` (1) → blocklist `token plan` / `usage limit` → terminal (unchanged).
- `invalid params, tool call result does not follow tool call (2013)` (1) → no allowlist hit → terminal (unchanged).

### Work unit 3 — C2/C4: `ValueError` body shapes

Insert before the generic `except Exception`:

```python
except ValueError as e:
    if _matches_transient_valueerror(str(e)):
        logger.warning(f"[LLM] Transient error body (ValueError, pattern-matched), will retry: {_truncate_error(e)}")
        raise TransientLLMError("value_error_body", e) from e
    raise  # genuine data bug — unchanged, non-retryable
```

Expected classification:
- `No generations found in stream.` (4) → allowlist `no generations found` → retryable.
- `{'code': 'exhausted', ..., 'type': 'ultimate_model_retry_exhausted'}` (8) → allowlist
  `ultimate_model_retry_exhausted` → retryable **until the proxy transparency update
  ships**; then remove the pattern from `config.yaml` (no code change). Keep the pattern
  in code defaults only if the proxy update is not yet deployed at merge time.
- All other `ValueError`s → pass through (unchanged).

`MalformedLLMResponseError` (RC3 str-body) is deliberately **NOT** handled here: its
payload logging (commit `d4b7b8a4`) has produced no samples yet, and the proxy update may
remove the shape. Deferred — see §6 open questions.

### Work unit 4 — C3: transport exceptions

Add to `TRANSIENT_EXCEPTIONS`:

```python
httpx.RemoteProtocolError,   # peer closed mid-body (incomplete chunked read)
```

Do not add the broader `httpx.ProtocolError` / `httpx.TransportError` parents — a stray
`ConnectError` is already covered by `openai.APIConnectionError`, and over-broad parents
risk making genuinely broken-endpoint loops burn the full budget. Re-verify during
implementation that `httpx.ReadTimeout` (⊂ `TimeoutException` ∈ `TIMEOUT_EXCEPTIONS`,
`:144`) needs nothing — the single 18:52 sighting self-recovered.

### Work unit 5 — L2 facade parity

`daemon/services/llm_failover.py` `_classify_raw_sdk_exceptions` (`:236-283`) mirrors the
classifier for the 9 secondary sites. Apply units 2–3 there (same helpers, imported from
`llm_error_classifier` — do not duplicate the pattern lists). The facade's 45s wall-clock
cap stays; secondary sites get the same retryability, bounded by their existing budget.

### Work unit 6 — Error-type mapping for parents

`_classify_error_type` (`daemon/services/message_processing_errors.py:72-148`): add a
branch mapping `TransientLLMError` → `error_type='transient_error'` (same type the
`TransientAPIError` path produces, `:136-137`). After L1 exhaustion, parents then see
`transient_error`/`warning` instead of `invalid_data`/`execution_error` — no behavior
change beyond the label (the `RECOVERY_GUIDANCE_HINT` problem is the parking plan's
domain, not this plan's).

### Work unit 7 — Config

**Config home (review §3.3, corrected):** the sibling retry settings
`llm_retry_transient_attempts` / `llm_retry_timeout_attempts` live in **`QueueConfig`**
(`daemon/config.py:371-391`), serialized under **`queue:`** in `config.yaml:85-106` — not
`LLMConfig`/`llm:`. The new keys join them (single home for LLM retry configuration):

```yaml
queue:
  # ... existing llm_retry_transient_attempts / llm_retry_timeout_attempts ...
  # Non-status transient-channel pattern matching (docs/plans/transient-channel-retry-widening.md):
  #   allowlist: bare openai.APIError messages treated as transient (timeout-body
  #     patterns route to the 3-attempt timeout budget; others to the 10-attempt
  #     transient budget)
  transient_apierror_allowlist: ['all models rate limited', 'context deadline exceeded']
  transient_apierror_timeout_patterns: ['context deadline exceeded']
  #   blocklist: mandatory precedence over the allowlist (quota / bad-params shapes
  #     stay terminal). Auth shapes are unreachable here by design — auth errors
  #     arrive as APIStatusError and are caught at the status branch — so they are
  #     NOT listed (review §3.4).
  transient_apierror_blocklist: ['token plan', 'usage limit', 'invalid params']
  #   ValueError-body patterns (200-body proxy errors, empty SSE stream)
  transient_valueerror_patterns: ['no generations found', 'ultimate_model_retry_exhausted']
```

Use `Annotated[list[str], NoDecode]` + `_parse_csv_or_json_list` (`config.py:50-83`), same
mechanism as the parking plan's `rate_limit_body_patterns`. Empty allowlist disables the
branch (pure pass-through — the additive-off switch); `transient_apierror_timeout_patterns`
defaults to the timeout-body subset of the allowlist.

### Work unit 8 — Tests (targeted; full suite is the tester's domain)

In `tests/unit/test_llm_error_classifier.py` (+ a facade test file):

1. **Channel tests (C1–C4):** each corpus shape → wrapped `TransientLLMError`, kind
   correct, `.original` preserved; predicate (`RetryByCategory`) counts them transient.
2. **Regression tests (must stay non-retryable):** 2013 bad-params `APIError`, 2056
   Token-Plan `APIError` (allowlist-miss AND blocklist-hit variants), generic
   `ValueError`, `AttributeError`, `BadRequestError` / `ContextLengthExceededError`,
   non-pattern bare `APIError`.
3. **Blocklist precedence:** synthetic message matching both lists → non-retryable.
4. **Ordering:** subclass-first except precedence over the new `APIError` branch —
   regression covers `APIConnectionError`, `APITimeoutError`, `APIStatusError`,
   **`APIResponseValidationError`** (review §2.1 — the branch sits after its handler;
   the test pins the order), and `BadRequestError`.
4a. **Timeout-kind budgeting (unit 2a):** `TransientLLMError(kind='timeout_body')`
   consumes the timeout budget (3 attempts) and timeout primary-slice cap;
   `kind='api_error_body'` consumes transient — assert via predicate counts.
5. **Facade parity:** one C1 case through `_classify_raw_sdk_exceptions`.
6. **Config parsing:** csv/json list forms; empty allowlist disables.
7. **End-to-end spirit check:** a fake invoke raising bare `APIError('All models rate
   limited')` under `Retrying` yields 10 attempts (not 1) — mirrors the Aug-26 06:51
   storm shape.

## 3. Compatibility guarantees

- **Non-retryable semantics unchanged for everything not allowlisted** — the two new
  branches only fire on pattern hits; misses re-raise byte-identically.
- **L1 budget / backoff / ceiling / failover slices unchanged** — membership is the
  only lever except the single documented predicate routing of `kind='timeout_body'` to
  the timeout budget (unit 2a); `derive_ha_attempt_ceiling` (`:168-182`) untouched.
- **Post-exhaustion cascade unchanged** — exhausted `TransientLLMError` re-raises
  (`reraise=True`) like `TransientAPIError` today: instance ERROR, observer-lane
  finalize. Parking is the parking plan's job.
- **Parking-plan disjointness:** this plan introduces no `ProviderRateLimitError` and no
  episode state. When the parking plan lands, its detector can special-case
  `TransientLLMError(kind='api_error_body')` with rate-limit patterns — one seam, no
  conflict. The two pattern configs stay separate (this plan = retryability; parking plan
  = episode classification).

## 4. Acceptance criteria

1. All four corpus channels classify retryable; all terminal corpus shapes (2056, 2013)
   and genuine-bug shapes classify non-retryable (regression green).
2. Attempt-count test: storm shape exhausts the full transient budget under `Retrying`.
3. Existing classifier/facade tests pass unmodified except where the new branches are
   exercised.
4. `config.yaml` documents each pattern with its corpus incident reference.
5. After implementation: update `docs/retry-architecture.md` §4 (L1 retryable
   classification list) + §11 gap #10 → resolved, flip the bug doc Status to
   `Fixed (RC1)`, and extend the bug doc's Evidence-anchors grep recipe to include
   `[LLM] Non-retryable API error` (review §3.2).

**Residual behavior after ship (review §4 — expected, not a regression):** this plan is
necessary but not sufficient. During a provider-wide outage that outlasts the fast budget,
each affected invoke now burns the full transient budget (10 attempts with
exponential-jitter waits — ~9 min without HA) instead of dying instantly; a repeat of the
Aug-26 06:51 storm therefore sends ~10× more requests at an already-dying provider
**before the same instance deaths**. The instance still dies `ERROR`, the parent still
receives `RECOVERY_GUIDANCE_HINT` (replacement-storm mechanism, gap #7), and observer-lane
jobs still strand — all until the parking plan lands. Recording this now so the amplified
request volume is not mistaken for a regression.

## 5. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Allowlist false-positive retries a genuine bug 10× | Medium | Narrow defaults from corpus; mandatory blocklist; regression tests 2–3; config-removable without deploy |
| New `except openai.APIError` shadows an existing subclass branch | Medium | Branch inserted after ALL subclass handlers incl. `APIResponseValidationError` (`:579`); test 4 pins the order |
| Timeout-body wall-clock amplification (10 × upstream timeout, no hot-path cap) | Medium | Resolved: `kind='timeout_body'` routes to the 3-attempt timeout budget (unit 2a) — same bound as `APITimeoutError` |
| `ultimate_model_retry_exhausted` retry futile while proxy hash-lock persists (same message list → same exhausted state) | Low | Retries are cheap (fail in seconds); proxy transparency update removes the channel; pattern config-removable |
| Parking-plan interaction (double-wrap) | Low | Disjoint exception types; documented seam (§3) |
| Stream-empty retry loops on a hard-broken endpoint | Low | Bounded by the 10-attempt budget + L2 swap; same bound as 429 today |
| Amplified request volume during provider-wide outages (10× per invoke) | Medium | Accepted residual (§4); bounded per-invoke; parking plan is the structural fix |

## 6. Open questions (non-blocking)

- `MalformedLLMResponseError` str-payload patterns — wait for `d4b7b8a4` samples + proxy
  update; likely obsolete.
- `Retry-After` honoring — remains deferred (parking plan §10).

## 7. Implementation notes

- Branch off `latest`; check for stale partial branches first (house convention).
- `uv sync` (PEP 735 groups, no `--extra dev`); `pytest tests/unit/test_llm_error_classifier.py -v`.
- Single coder, ~half-day including tests; no DB migrations, no routers, no scheduler.
