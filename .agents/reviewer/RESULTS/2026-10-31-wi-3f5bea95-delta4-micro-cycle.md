# Delta-4 Pass — micro-cycle-4 @ `f636756aa` (wi-3f5bea95) — CODE PHASE CLOSED

- **Branch/worktree:** `fix/eventloop-sync-db-hotpaths` · **Date:** 2026-10-31
- **Worker:** `cc131574` (`code-review`, agentic); all mutation/plant runs on `/tmp/git archive` copies (`/tmp/delta4/{base,base_b1183,head,mut_a..f}`); per-variable env scrub (ambient `POSTGRES_DB=ensemble_prod` / `ENSEMBLE_SELF_ENV=live` scrubbed BY NAME); scratch PG only; live `:9797` never contacted
- **Scope:** `git diff --stat 054ba46b6..f636756aa` = exactly 3 files, 232 insertions / 1 deletion
- **VERDICT: ✅ APPROVE-WITH-ADVISORIES** — 0 CRITICAL · 1 WARNING (pack-registration gap; gate-lane item, not a delta defect) · 6 ADVISORY

---

## ITEM 1 — submit-failure permit leak: delegation is EQUIVALENT

- **(a) Arithmetic exactly as mandated** (`db_offload.py:232-251`): `_inflight -= 1` → head **live** waiter loop (skips cancelled/done) → `_in_transit += 1` + `set_result` + `return` (no permit increment) → else `_permits = min(+1, W)`; `_in_transit` untouched for the caller's own token. Caller's token is `_inflight` by construction on both acquire paths, and no done-callback exists yet (submit raised), so the decrement cannot target another actor's token. Called exactly once (`:429-430`), before `add_done_callback`.
- **(b) No worker-thread assumption:** `do_release()` is *already* a loop-thread function (canonical site reaches it via `loop.call_soon_threadsafe`, `:445-447`). No lock (module has zero locking primitives), no assert, no logging, no alarm checks (`_check_alarm()` only from `acquire()`).
- **(c) Race-safe inline:** **no `await`** between `acquire()` returning and `do_release()` (both `copy_context()` and `executor.submit` are synchronous); a concurrent worker release can only run on a later loop iteration. Proven behaviourally by the mut_c invariant break.
- **(d) Token reaches the waiter, and must:** restoring `_permits` would **deadlock** (`acquire()` parks on `if self._waiters or self._permits <= 0` — a parked deque plus a free permit blocks everyone). Mutation (b) reproduces exactly that: parked task starved → `TimeoutError`.

### Mutation table (my runs, 21-node pack)

| leg | plant | observed | RED |
|---|---|---|---|
| BEFORE `054ba46b6` | pre-fix + HEAD tests | 3 failed/18 passed (raw `RuntimeError` escapes; `parked` starves) | exactly the 3 new pins |
| (a) | delete `gate.do_release()` | 3 failed/18 | all 3 |
| (b) | permit-restore substituted for transfer | **1 failed/20** | **ONLY** the with-waiter pin (starved waiter) |
| (c) | `_in_transit -= 1` for own token (draft bug) | 3 failed/18 | all 3 — invariant msg `permits=2 inflight=0 in_transit=-1 sum=1 W=2` |
| (d) *mine* | drop `from exc` | 1 failed/20 | ONLY the with-waiter pin (`__cause__`) |
| AFTER `f636756aa` | — | **21 passed** | — |

Discriminator CORRECT: (b) reddens only the with-waiter pin — neither vacuous nor over-broad.

## ITEM 2 — degraded Task-branch `root_only`: disposition CORRECT

Same pin, both directions: **BASE `b1183a637` fail-open, child KEPT → pin PASSES**; HEAD `f636756aa` → PASSES (whole file 77 passed); plant **broad** one-liner → RED; plant **narrow** one-liner → RED; Job branch under same poison → **`OperationalError` propagates** (healthy control returned root-only exactly).

Judgement: (i) fail-closed **contradicts** BASE for both variants — BASE's `_lookup_instance` also yields `None` and `root_only and _instance is not None and parent_id` keeps the row (`work_resolver.py:1513-1515`); (ii) the Job-branch mirror is genuinely **not** a one-liner — `_batch_child_instance_ids` (`:2367-2390`) has no `try/except`, so mirroring converts a soft degrade into a 500. Recording pin is mutation-meaningful and bidirectional.

## ITEM 3 — comment-only: PROVEN at bytecode level

`_Gate`'s six code objects identical between `054ba46b6` and `f636756aa` (`co_code`, `co_names`, `co_consts`, `co_varnames`, `co_flags`); only `offload` differs (Item 1). Zero behaviour change.

## ALSO

- **Registered packs all green at registered counts:** 9/9 · 14/14 · 10/10 · 6/6 (real PG, scratch DB count 0 before/after).
- **Delta-3's pre-stage condition RESOLVED:** the collected-node fence is present (`test_instance_children_order_repository.py:178`) and green in the 9/9 run (restored `10b3c982`). My delta-3 close condition is discharged.
- **Fences:** no `.agents/tester/` in the diff; no migrations/scripts/readiness; tracked dirt is two foreign files mtime-stamped 3.5 h pre-session; no uncommitted coder work.
- **Prior cl-osures intact** (grep sweep): `_in_transit` at all six sites; cancel-propagation pin green; W-1..W-4; static ORDER BY pin; C1 forwarding; byte-identity machinery.

## 🟡 WARNING (gate-lane item, not a delta defect)

Neither delta file has a registered pack or collected-count fence: `test_db_offload_seam.py` (21 nodes) and `test_work_resolver.py` (77 nodes) are absent from `.agents/tester/PACKS.md`. This lane's own history includes a pin pack deleted undeclared by a developer commit (`09ce9d369`, restored `10b3c982`) — exactly the failure mode a fence prevents. **Fix:** register both packs with invocation + expected counts before the functional run, or add a `test_collected_node_count_fence`-style guard.

## ADVISORY (tidier)

1. `db_offload.py:430` message mislabels the reachable trigger (orderly drain cannot reach `submit` — `_CLOSED` + `gate.close()` happen first; reachable = `BrokenThreadPool` or a foreign `get_executor()` caller). Suggest `db_offload submit failed ({type(exc).__name__}): {exc}`.
2. Test narrative overstates drain reachability; reword to "broken pool / foreign shutdown".
3. Only ONE of three pins asserts `__cause__` — say so, or add it to pins 2/3.
4. `:207` comment correct on `InvalidStateError`, silent on the loop-closed `RuntimeError` axis.
5. Item-2 disclosure nuance: the blast-radius widening is exact for **partial** failures; add the qualifier.
6. `:234` `if self._inflight > 0` tolerates underflow — optional debug assert or `release_underflow` counter.

## Disposition

**CODE PHASE CLOSED.** All three items verified closed with independent reproduction; no new defect; no invariant regression; nothing previously clean regressed; delta-3's condition discharged. The lane proceeds to the functional gate. One gate-lane action rides along (register the two packs), and the tidier owns the six advisories.
