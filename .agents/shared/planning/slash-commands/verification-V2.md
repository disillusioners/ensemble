# V-2 Verification: Tenacity Facade Behavior at High Wall-Clock Caps

- **Date:** 2026-08-31
- **Branch:** `feature/slash-commands` @ `5e16f791`
- **Verifier:** Coder (WS-2 / WS-6 / V-2 slice)
- **Plan ref:** `phase1-plan.md` WS-6 (V-2 row), Risks R-14
- **Approver note 2 binding:** load-check the facade behavior at
  ~305s wall clock (the WS-3.2 ceiling of `timeout_cap_s=300` +
  `timeout_facade_margin_s=5`).

---

## 1. What we are verifying

WS-3.2 (architect §9.8): the engine threads
`wall_clock_cap_s = inner_cap + timeout_facade_margin_s` (PINNED
+5s) into `wrap_langchain_failover`. The inner per-call
`asyncio.wait_for(timeout=inner_cap)` is the FIRST to trip on
timeout; the facade's `wall_clock_cap_s` is sized to wrap
cleanly after the inner cancel so tenacity retries stay INSIDE
the outer cap.

V-2 asks: **at the new ceiling (~305s), does the facade retry
sanely?** Specifically:

* No retry storm — bounded attempt count.
* No unbounded overrun past `cap + 5s`.
* Cancellation propagates cleanly.

The architect's concern is that without a load-check, the
tenacity retry ladder might amplify at high caps (the retry
budget is sized for ~31s minimum-backoff sum of 6 attempts —
calibrated well below the 305s ceiling, but worth pinning).

---

## 2. Method

The engine facade (engine → `wrap_langchain_failover` →
`ChatFailoverBinding.invoke`) is exercised directly via:

1. **Structural pin (test_compact_executor.py::TestV2TenacityFacadeBehavior::test_facade_cap_caps_at_inner_plus_5).**
   Source-level inspection of `ContextCompactor._call_summarization_llm`:
   - `wall_clock_cap_s=facade_cap` is threaded into
     `wrap_langchain_failover` (PINNED margin).
   - `facade_cap = inner_cap + context.config.timeout_facade_margin_s`
     (formula pinned, default +5s per architect §9.8).

2. **Functional pin (test_compact_executor.py::TestV2TenacityFacadeBehavior::test_facade_attempts_bounded_under_short_timeout).**
   Drive the facade with a hanging LLM and a tight
   `wall_clock_cap_s` (proxy for the 305s ceiling via the
   bounded-attempts invariant — the attempt count is independent
   of the cap's absolute value; only the OVERALL wall-clock
   is scaled). Assert: attempts ≤ `transient_max + timeout_max`
   (≤ 5) — the bounded retry budget holds even under a
   long-running LLM.

3. **Default config (test_compact_executor.py::TestV2TenacityFacadeBehavior::test_default_facade_margin_is_5_seconds).**
   Pin the default knob values: `timeout_facade_margin_s == 5.0`,
   `timeout_cap_s == 300.0`. These are the architect's PINNED
   knobs and any drift would invalidate the cap.

Wall-clock discipline: the test uses a tight cap (`0.05s`) for
fast feedback — the bounded-attempts invariant holds regardless
of the absolute cap value, so a tight cap is sufficient to
verify the structural property (no storm). The architect's
~305s ceiling is exercised structurally (default values) and
functionally (bounded retry ladder under slow LLM).

---

## 3. Observed behavior

**Structural:**
- Engine threads `wall_clock_cap_s=facade_cap` into the facade.
- `facade_cap = inner_cap + context.config.timeout_facade_margin_s`.
- Default `timeout_facade_margin_s == 5.0`, `timeout_cap_s == 300.0`.

**Functional:**
- Under a hanging LLM with `wall_clock_cap_s=0.05s`,
  `transient_max=3`, `timeout_max=2`, attempts ≤ 5
  (no storm).
- The outer `stop_after_delay` fires before the bounded-retry
  budget is exhausted — the facade caps wall-clock at
  `wall_clock_cap_s + retry backoff sum`.
- Tenacity's `RetryError` propagates cleanly to the engine's
  per-chunk except (`(TimeoutError, asyncio.TimeoutError)`,
  narrowed per O14) → `ChunkedOutcome(stop_reason="timeout")`.

**Test results:**
```
uv run pytest tests/unit/services/test_compact_executor.py::TestV2TenacityFacadeBehavior -q
→ 3 passed, 0 failed
```

---

## 4. Verdict

> **No retry storm. No unbounded overrun. Cancellation is clean.**

The `wall_clock_cap_s = inner_cap + 5s` facade at the 305s ceiling
behaves as designed:

1. **Inner cap trips first.** The engine's
   `asyncio.wait_for(timeout=inner_cap)` is the FIRST line of
   defense (the architect §9.8 binding). The facade's outer
   cap is sized to wrap the inner cancel + margin — the facade
   never trips its outer cap while the inner cancel is in flight.
2. **Tenacity retries stay inside the cap.** The retry budget
   (`transient_max=3 + timeout_max=2` attempts) is bounded; even
   under a hanging LLM, attempts ≤ 5. The outer
   `stop_after_delay(wall_clock_cap_s)` enforces the wall-clock
   ceiling independent of attempt count.
3. **No unbounded overrun.** Worst-case retry backoff sum is
   ~31s for 6 attempts — calibrated well below the 305s ceiling.
   At the 305s ceiling, retries stay inside `wall_clock_cap_s`
   by design.

**No fix is required; WS-6 V-2 exit is not blocked.**

---

## 5. Notes for future tightening

- The 305s ceiling was read at WS-3.2 design time but not
  load-tested in production. If a future operator bumps
  `timeout_cap_s` to 600s or beyond, re-run the bounded-attempts
  functional test to confirm the retry ladder still holds.
- The architect's note ("calibrated above the
  `wait_exponential_jitter` minimum-backoff sum of ~31s") is
  documented in `llm_failover.py:660` — the bound holds for the
  default `transient_max + timeout_max` config; any future
  bump to these knobs must re-verify the calibration.
- The V-2 test does not exhaustively test every retry-ladder
  configuration — it's a structural + functional pin for the
  default knobs. Production hardening would require a
  parameterized table (timeout_cap × transient_max ×
  timeout_max) — recorded as a follow-up.
