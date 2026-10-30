# Code Council DELTA Re-Review #2 — cycle-2 @ `f4fa4cc8b` (wi-3f5bea95)

- **Branch:** `fix/eventloop-sync-db-hotpaths` (council pinned the payload at `f4fa4cc8b`; `git diff f4fa4cc8b..HEAD -- daemon/` proven EMPTY — findings hold for the pinned payload; re-derive tip identity at stage time)
- **Date:** 2026-10-30/31 · **Council:** governor `e3f242a0`, 2 councilors (`agentic` + `coding`), both round-0 reports substantive; one refinement round killed twice by the upstream `coding`-alias rate limit (known proxy defect — now costing review rounds, not just coder lanes)
- **Baseline:** `.agents/reviewer/RESULTS/2026-10-30-wi-3f5bea95-code-council-delta-fix.md`
- **VERDICT: 🔴 REJECT (delta)** — 2 CRITICAL (one root cause) · 3 WARNING · 5 ADVISORY

Prior cycle's CRITICAL-1 (token loss) is **genuinely cured**. The fix introduced a different defect in the same handler.

---

## 🔴 CRITICAL — post-wake cancel branch swallows cancellation

`daemon/services/db_offload.py:196-209` — the post-wake `except asyncio.CancelledError` branch **returns** (at `:174`/`:181`) instead of re-raising. Consequences: `acquire()` completes normally → `offload()` proceeds past `await gate.acquire()` (`:395`), **submits the DB call anyway, holds a worker for the full read, and returns its result to a cancelled task**.

- Violates the module's own documented contract (`:26-28` "cancellation is never swallowed").
- **Introduced by this cycle's fix** — probe matrix: property A PASS at pre-fix, FAIL at HEAD, PASS under the `return`→`raise` mutant.
- **Gate under-count reproduced through the public `offload()` API** (100/100): `actual_running=2 / inflight=1`, then `permits=2` with a unit still running → **3 units admitted against W=2**. The `min(...)` clamps preserve the arithmetic sum precisely while `_inflight` under-reports one of its own terms — **a sum invariant cannot detect this**; only instrumenting real running work can.
- **Unpinned:** both CRITICAL-1 pins are green at HEAD **and** green under the correct fix (M2, 16/16); `test_cancel_after_wake_returns_token`'s `except CancelledError: pass` never fires (`DID NOT RAISE`). A fix that cannot fail either way is not gated.

### Disagreement surfaced verbatim (governor adjudicated REJECT; reviewer concurs)

- `coding`: "permit accounting stays correct (verified), so no invariant violation… hardening suggestions, not blockers" → APPROVE-WITH-ADVISORIES.
- `agentic`: "cancellation silently discarded, the work runs anyway, and the admission gate under-counts by one per event — documented-contract regression plus backpressure-bound regression, reproduced 100/100" → REJECT.

Governor's three reasons, all sound: (1) `coding` verified the sum; `agentic` measured **3 admitted against W=2** through the real API — the checks were not measuring the same thing; (2) the swallow alone is a documented-contract regression on the seam this lane exists to harden — a cancelled request holding a worker while the gate believes it free is the saturation failure mode this commission targets; (3) the closure is unpinned.

**The fix (cheap and verified):** change both `return`s in the post-wake branch to `raise`; `offload()` already has `except asyncio.CancelledError: raise`, so propagation is end-to-end. Add ONE pin: a node driven through `offload()` asserting `CancelledError` propagation + callee-not-called + `_inflight == real running`. The `return`→`raise` mutant keeps all 16 seam pins green (M2), so the new pin is the discriminator.

## 🟡 Warnings (carry into cycle 3)

1. **Pin-blindness:** the two CRITICAL-1 pins don't exercise `offload()`; at the true pre-fix tree they RED only via `AttributeError: '_Gate' object has no attribute 'in_transit'` — red-for-the-wrong-reason. Use `getattr(gate, "in_transit", 0)` or document the AttributeError as the base-side control.
2. **`test_detail_shares_source` still calls the same expression twice** (`tests/unit/test_instance_children_order_repository.py:145-173`) — "both-path" is not implemented; the two real call sites (`instance_lifecycle.py:5115` batch vs `:5145` single) remain grep-only. Rename or exercise both service-level shapes.

## ✅ Closed cleanly by both councilors (no disagreement)

- **CRITICAL-1 liveness syndrome genuinely cured:** **0/260** sweep losses, 0 wedges; token re-transfer demonstrably **reaches the next waiter** (30/30 where pre-fix wedged 40/40); no spin/recursion on the all-cancelled drain; invariant asserted per trial.
- **W-1 CLOSED** — barging pin creates the fresh task above `do_release`; REDs on the full pre-fix tree via `DbOffloadClosedError` (the true discriminator, not permit-consumption coincidence).
- **W-2 CLOSED** — `parents[3]` self-asserting; real `LIMITS_DB_OFFLOAD_MAX_WORKERS` → `LimitsConfig` → `configure` chain; displacement fails loudly.
- **W-3 CLOSED** — both named-pair constants used at all four sites (`instances.py:395/396`, `tools/instance.py:6170`, `tools/instance_read.py:1676`); zero hardcoded survivors; no default flip.
- **W-4 CLOSED** — `cancel_futures` zero hits; bounded drain with `shutdown(wait=False)` fallback.
- **Direction-flip pin verified independently by both** — REDs on `instance_id DESC`.
- **Fences clean** — no `.agents/tester/**` absorbed; `daemon/migrations/`, `scripts/`, `readiness.py` untouched; no uncommitted coder work.

## Required for cycle 3

1. One-word fix: both `return`s in the post-wake cancel branch → `raise`.
2. One discriminating pin driven through `offload()`: `CancelledError` propagates, callee not called, `_inflight == real running` (must RED at current HEAD and GREEN after the fix — that is the definition of discriminating).
3. Warning cleanups: `in_transit` getattr tolerance (or documented AttributeError control) in the CRITICAL-1 pins; detail-shares-source exercises both real call sites or is renamed.
4. Then a **delta-3 pass limited to the cancel-propagation axis** — nothing else re-reviewed.
