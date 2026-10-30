# Code Council DELTA Re-Review — fix cycle @ `836334e87` (wi-3f5bea95)

- **Branch/worktree:** `fix/eventloop-sync-db-hotpaths`
- **Commits:** `4c4020a20` (7 files +282/−35) · `83993e3c0` (2 files +102) · `836334e87` (1 file +18)
- **Date:** 2026-10-30 · **Council:** governor `db1a5cbc`, 2 councilors (`agentic`, `coding`), 2 rounds, converged
- **Baseline:** `.agents/reviewer/RESULTS/2026-10-30-wi-3f5bea95-code-council-p1-p4.md` (prior REJECT)
- **VERDICT: 🔴 REJECT (delta)** — 1 CRITICAL (new, production-reachable, unpinned) · 4 WARNING (delta-blocking) · ride-with-disposition items

Prior CRITICALs **C1 and C2-barging are genuinely CLOSED**. One new critical replaces them — introduced by the fix itself.

---

## 🔴 CRITICAL-1 — Transfer-style release loses the token on cancel-after-wake

`daemon/services/db_offload.py:174-187` (`do_release`), `:140-160` (acquire's `except CancelledError`).

**Mechanism.** `do_release` pops the head waiter, decrements `_inflight`, and `set_result`s the waiter's future **without** incrementing `_permits` — the slot transfers. The woken waiter claims it with `_inflight += 1` at `:186`. If the task is cancelled in the micro-gap between `set_result` and resumption, `except asyncio.CancelledError` cannot distinguish *"never woken — holds nothing"* (correct: do nothing) from *"woken — token in hand"* (must return the slot). It does nothing in both cases ⇒ **token dropped**. `permits` is never restored; `configure()` runs once in the lifespan ⇒ recovery requires a **daemon restart**. Because the gate defers-never-rejects, the failure mode is an **indefinite hang**, not an error.

**Required invariant** (the module docstring `:29-34` still claims `permits == workers == inflight` — arithmetically impossible, omits the third state):

```
permits + inflight + in_transit == W      # waiters hold nothing; in_transit >= 0
```

HEAD actually holds `permits + inflight == W − in_transit` with `in_transit` **untracked and droppable to zero**.

**Claim paths audited (both councilors independently):** woken-waiter fast path `:183-187` — sole legitimate claimer, future resolved once ✓ · another parked waiter — cannot proceed without its own future ✓ · fresh acquirer `:154-158` — gated on `permits > 0`, and `waiters > 0 ⟹ permits == 0` ⇒ **structurally cannot barge** ✓ · `do_release` — decrements only ✓ · `close()`/`fail_waiters()` — shutdown-only, benign ✓ · cancel **before** wake `:176-182` ✓ · **cancel between wake and claim — 🔴 LOST**.

**Production reachability (proved; `coding` retracted an earlier immunity claim).** `:175 await waiter` is an ordinary await point. Path: `routers/{messages,queues,work,instances}.py → await _offload(...) → offload() :329-353 → :353 await gate.acquire() → :175 await waiter`. Cancellation sources: uvicorn client disconnect, anyio cancel-scope expiry, task-group abort, `wait_for` wrappers, shutdown. A waiter exists **exactly when all 6 workers are saturated** — the state this lane exists to survive.

**Evidence — 260-trial sweep via the public `offload()` API only:**

| Tree | Result |
|---|---|
| HEAD, W=1, natural done-callback release, cancel timer swept across the release tick | **29 / 260 permanent capacity-loss events**, all `(0,0,0)` with no future outstanding |
| `4c4020a20^` (pre-fix), identical driver | **0 / 260** |
| HEAD, post-loss `await asyncio.wait_for(offload(...), 1.0)` | **`probe WEDGED: offload() never returns`** |

Regression confirmed: **29/260 → 0/260**, introduced by `4c4020a20`. An earlier run appeared to self-heal — artifact of manually releasing a slot whose future was still outstanding (a stale second `do_release`); with no future remaining nothing can ever release.

**Fix:** track in-transit state (`_transfers` counter, or test `waiter not in self._waiters` in the handler). On post-wake cancellation **return the token**: re-transfer to the next live waiter if the deque is non-empty, else `_permits = min(_permits + 1, _workers)`. Assert `permits + inflight + in_transit == W` in pins.

---

## 🟡 WARNING (delta-blocking — all in this payload)

**WARNING-1 — the C2 repro pin does not catch the defect it is named after.** `tests/unit/services/test_db_offload_seam.py:271-301`. Paired artifact both councilors converged on:

| # | Tree | Result |
|---|---|---|
| a′ | `0d8693820` (= `4c4020a20^`) + HEAD test | **PASS** — defect live, test green |
| b1 | HEAD + only `do_release` reverted | **FAIL** `:291` — but this hybrid is not real code (HEAD removed the `_permits <= 0` guard the pre-fix release depended on) |
| b2 | HEAD + whole `db_offload.py` reverted to `4c4020a20^` | **PASS** — a full revert eludes the suite |

Cause: `do_release` is synchronous; `set_result` queues w1's wakeup via `call_soon` **during** it, and `create_task(fresh)` at `:286` is created *after* — so `fresh.__step` queues **behind** the wakeup and FIFO always admits w1. The barging interleaving never occurs; the pre-fix pass at `:291` is **permit-consumption coincidence**, not barging detection.
**Fix:** move `create_task(fresh)` **above** `do_release()` so the pre-fix path REDs at `:289` (`DbOffloadClosedError` on `await w1` — the true discriminator); add a cancel-a-woken-waiter node; add a transfer+cancel **storm** node asserting the invariant at every step plus a bounded subsequent `acquire()` (anti-wedge).

**WARNING-2 — `test_xii` hardcodes `cwd="/home/nea/ensemble-src-wt-…"`** → ERRORs after standard worktree teardown (verified) and until then tests a foreign tree; it also exercises `configure(max_workers=3)` instead of the `LIMITS_DB_OFFLOAD_MAX_WORKERS` → `LimitsConfig` → `api.py:735` chain D-55/W-10 asks for. **Fix:** `env=` + in-process, `cwd=str(PROJECT_ROOT)` derived from `__file__`.

**WARNING-3 — named-pair constants defined but imported nowhere** (`constants.py:1766-1768`; router still hardcodes `Query(True)` at `instances.py:395/396`; constants appear only in comments). "Present and used" is half-met. **Fix:** import and use them.

**WARNING-4 — W5 residual:** `cancel_futures` still has 1 hit (`:268`), and `shutdown(wait=True)` at `:259-272` is **unbounded and blocks the event loop** (sole caller `api.py:734` is in the lifespan on the loop thread). **Fix:** enforce `_DRAIN_BOUND`; drop the `cancel_futures` fallback (it cancels queued work whose awaiters then hang); correct the comment.

---

## ✅ Converged closures (verified, not re-litigated)

- **C1 CLOSED** — router reproduced with a **non-empty** page: BASE 500 → HEAD 200, with and without `?limit=`; `limit=None` unbounded all the way down (`work_resolver.py:1471`); service **forwards** (`job_queue_service.py:1836-1841`). `test_jobs_mission_id_filter.py` 8 nodes flip red→green = genuine pre-existing coverage surfaced.
- **C2 original barging CLOSED** — repro flips: BASE `DbOffloadClosedError` on an OPEN seam → HEAD waiter ADMITTED, fresh parks; no-barge is **structural**.
- **W1(a) mutation-meaningfulness CLOSED** — per-mutant table: finally-release mutant → 1 failed/14 passed, RED at `test_x_during_flight_not_released`; wrapper-release mutant → same node RED; unmutated control 15 passed. Not assertion theater. (Note: the old `sleep(0.6)` node `test_cancelled_caller_propagates` passes both mutants — superseded, must not masquerade as coverage.)
- **W2 static ORDER BY pin** — present, captures the statement, REDs on removal (partial caveat below).
- **W5 5 of 6** — `_WARN_SUM` assigned `:277-279`; `_MAX_WORKERS` module-level `:232` (`executor._max_workers` grep = 0); `DbOffloadUnconfiguredError` gone, 0 importers; dead `:257-259` gone; re-entrancy pin uses nested `asyncio.run`.
- **Fences** — zero `.agents/tester/**` in coder commits; `daemon/migrations/`, `scripts/`, `daemon/services/readiness.py` untouched; no uncommitted coder work. **Net-new regressions 0** (32 → 24 failed on the affected 8-file surface; the 8 fixed are all C1).

## Ride-with-disposition (bounded, not delta-blocking)

- **W2 pin blind spot:** REDs on ORDER-BY **removal**, stays **GREEN on a direction flip** (`instance_id DESC`). Since this pin is gate-carrying (BASE-side control impossible on this host), assert the ordering **clause**, not token presence.
- **`_GATE = None` drops the old gate without `close()`/`fail_waiters()`** (`:271-272`) — a waiter parked on the old gate hangs forever (verified at BASE **and** HEAD: `g1.closed=False, waiters=1`). **Pre-existing**, not delta-blocking — but the commit rewrote the comment above it to assert a drain contract the code does not provide. Call `_GATE.close()` before dropping.
- `test_detail_shares_source` is tautological (calls `list_child_ids_for_parents` twice); `test_multiset_not_set` cannot detect set-vs-multiset (seeded ids `c1`/`c1b` differ). The real single-source claim is grep-verifiable (`instance_lifecycle.py:5115` vs `:5145`).
- `ensure_job_ids` patch-up draws from `_patch_jobs` **pre-`root_only`**, so `?limit=` can re-add child JobItems `root_only=True` dropped — the two shapes can source a child status from different layers. Unverified end-to-end; document or exclude.
- `test_limit_bounds_conversion` inspects `WorkResolverService.list_work` (whose `limit` predates this delta) — the class this fix extended, `JobQueueService.list_work`, has no signature pin.
- `test_jobs_list_limit.py` is vacuous (empty fixture ⇒ `if jobs:` never runs ⇒ GREEN at BASE); real coverage exists via the pre-existing pack — pin-quality note.
- Lazy-init "one form, stated" sits in a comment rather than the module docstring (D-78 / plan 1.3).

## Required before re-review (one cycle)

1. CRITICAL-1: in-transit tracking + token return on post-wake cancel; restate the docstring invariant `permits + inflight + in_transit == W`.
2. WARNING-1: reorder the barging pin so it REDs against the real pre-fix pair; add cancel-a-woken-waiter and transfer+cancel-storm nodes asserting the invariant.
3. WARNING-2 (portable test + real env chain), WARNING-3 (import the constants), WARNING-4 (bounded drain, no `cancel_futures`).

Fixing CRITICAL-1 without WARNING-1 leaves the fix unprotected — that pairing is what produced this round.
