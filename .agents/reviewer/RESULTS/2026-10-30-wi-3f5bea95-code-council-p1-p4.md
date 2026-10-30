# Code Council Review — wi-3f5bea95 Phases 1–4 implementation @ `a677bdcaf`

- **Branch:** `fix/eventloop-sync-db-hotpaths` (worktree `/home/nea/ensemble-src-wt-eventloop-sync-db-hotpaths`); diff range `3e976696b..a677bdcaf` excluding foreign `dd6d1ed12`
- **Date:** 2026-10-30 · **Council:** governor `c7b7ebcf` + 2 councilors (`agentic` + `coding`), `code-review` skill, 1 refinement round, unanimous after Round 1
- **Empirical method:** read-only `git archive` to /tmp, in-process signature checks, per-variable env scrub — fences held on both sides
- **VERDICT: 🔴 REJECT** — 2 CRITICAL (both reproduced at runtime by both councilors) / 6 WARNING / 4 ADVISORY

---

## 🔴 CRITICAL

### C1 — `GET /api/jobs` 500s on every non-empty page
`daemon/routers/jobs_crud.py:913-918` passes `limit=`/`ensure_job_ids=` **unconditionally** (only the values are gated by `_explicit_limit`) into `JobQueueService.list_work`, whose keyword-only signature (`daemon/services/job_queue_service.py:1774-1780`: `project_id/status/kind/root_only`) was **untouched by this lane** → `TypeError` → HTTP 500 whenever the page is non-empty (the call sits inside `if jobs:` at `:911`).
Both councilors reproduced: `JobQueueService.list_work() got an unexpected keyword argument 'limit'`.
This also proves the explicit-limit path was **never executed end-to-end** — no test covers the router→service path.
**Fix:** extend `JobQueueService.list_work` with `limit`/`ensure_job_ids` and forward to `WorkResolverService.list_work` (or call the resolver directly at `jobs_crud`); add a route-level pin exercising `GET /api/jobs` with AND without `?limit=`.

### C2 — Gate barging / permit-steal rejects a legitimately parked waiter on an OPEN seam
`daemon/services/db_offload.py:189-212`: `do_release()` increments `_permits` **then** wakes the head waiter; any coroutine reaching `acquire()` in the same loop-tick window (e.g. a new HTTP request) takes the fast path and steals the permit; the woken waiter hits the `permits <= 0` guard and raises the **mislabeled** `DbOffloadClosedError("db_offload seam is closed")` while the seam is open. Reproduced against the real `_Gate` (`SPURIOUS t2 rejected with DbOffloadClosedError while seam OPEN`, `barged=True`). Violates binding B4/D-43/D-67 (**defer-never-reject**) and fires under exactly the load this lane enables. (Coding's round-0 cancelled-waiter wasted-wake warning collapses into this same ordering defect.)
**Fix:** transfer-style release — `do_release` wakes **without** incrementing; the woken waiter claims the transferred token (re-park-on-empty alone does not close the FIFO/barging violation) — plus a barging-repro pin: parked waiter → `release` + interleaved `acquire` → waiter must complete.

---

## 🟡 WARNING

1. **Pin obligations unmet vs binding per-phase exit criteria.** Seam pack is 10/13 nodes — missing (ix) cancellation-storm NO-RESERVE accounting, (x) max-concurrent ≤ W + the must-RED `run_in_executor`-wrapper form, (xii) subprocess knob, (xiii) drain waiter-failure. The one cancellation pin asserts only **after** `sleep(0.6)`, so finally-release AND wrapper-future-release mutants both PASS it. No closed/drain test exists. P2 2.7/2.8 (the 8-node children-order pack), P3 3.9(a–i), and P4 4.5(a–f) packs are **entirely absent** (tests diff contains only `test_db_offload_seam.py`).
2. **Children-order normalization plan-sanctioned but unpinned** — D-6/D-34/D-68 sanction exactly one normalization (children array order; `gate_g5` normalizes then re-hashes both sides), so the byte-drift risk is a suggestion; but the legitimizing 8-node pack (task 2.7) did not land.
3. **P2 2.6(b)+(d) not landed:** named-pair constants absent (`constants.py` untouched — behavior unaffected, literals correct) and the D-A mismatch docstring absent.
4. **P4 exit criterion 7 unmet:** the task-4.7 readiness behavioural leg (leak_signal + pool_state BASE-vs-HEAD) is neither produced nor leader-signed-disclosed; the plan says the phase BLOCKS there (`readiness.py` itself correctly byte-identical).
5. **Seam contract gaps:** `_WARN_SUM` never assigned (configured `wait_warn_sum` silently lost on lazy gate creation; reads private `executor._max_workers`); D-78 un-configured lazy-init form unstated in the docstring; `DbOffloadUnconfiguredError` dead code; dead `:257-259` branch; re-entrancy pin vector deviates from task 1.5/D-52 (main-thread rename instead of nested `asyncio.run` in the worker — still convicts a ContextVar mutant but doesn't prove the real nested-call typed error); re-configure uses `cancel_futures=True` instead of bounded drain/refusal (D-78; production-unaffected — lifespan configures once).

---

## ✅ Converged positives (dual-verified — do not relitigate)

- **`/api/work` no-limit byte-identity proven converter-by-converter** (task `created_at` coercion ≡ BASE key; job `_parse_iso_datetime` identical; stable sort over append order; `to_dict()` request-context-free per R4/D-38).
- **Zero net-new reds empirically: 176/176 both legs** (BASE via read-only git archive); the 7 `test_job_queue_proxy_phase1` reds are pre-existing at BASE; seam pack 10/10.
- Children batch exactly per plan (ONE IN-query + SQL `ORDER BY parent_id, instance_id`; detail path same source; `list_child_ids_permanent` untouched; childless → `[]`); orphan-miss ×3 with empty absence set on degraded.
- **TB-1..TB-7 hold:** no session crosses an await/bridge; `readiness.py` AND `manager.py` absent from the diff (pool_leak fingerprint and F8 sibling untouched); `dispose_db_pools` single production caller (`manager.py:15223`); drain precedes `manager.shutdown()`.
- Commit discipline clean (W1 = 9 planning paths only; zero `.agents/tester/**` in coder commits; foreign `dd6d1ed12` standalone; per-phase file sets exact; migrations empty); `include_descendants` no default flip; explicit-limit presence-only at `:911`; four exact-count packs untouched per D-71.

---

## MUST-CHANGE before re-review

1. **C1:** jobs kwargs + router→service route pin (with/without `?limit=`).
2. **C2:** transfer-style release (or equivalent closing barging) + barging-repro pin.
3. Land missing pin obligations — P1 (ix)/(x)/(xii)/(xiii) with **during-flight** assertions that RED finally-release/wrapper-release mutants; P2 2.7/2.8; P3 3.9; P4 4.5 — or a recorded leader-approved deviation.
4. Land P2 2.6(b) constants + (d) docstring.
5. Produce P4 4.7 readiness behavioural evidence or its leader-signed disclosure.
6. Fix re-entrancy pin vector + docstring/dead-code items (D-78 lazy-init form, capacity-25/F4-A sentence, dead error class/branch).

All six are bounded code/test changes; the architecture and the mechanism are sound — the two criticals are an unwired call path and one ordering bug in the permit handoff, not design flaws.
