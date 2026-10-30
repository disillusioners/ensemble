# Plan Gate Review — wi-3f5bea95 (event-loop sync-DB hot paths)

- **Target:** `.agents/shared/planning/eventloop-sync-db-hotpaths/` @ `42764588c`, branch `fix/eventloop-sync-db-hotpaths`
- **Gate type:** plan + architecture (no implementation exists)
- **Date:** 2026-10-29
- **Reviewers:** 3 skill-matched workers (`plan-review`, `architecture-review`, `code-review`) + 1 narrow callee-transaction check
- **VERDICT: REJECT** — 8 criticals, all plan-text/ownership fixes; no architecture redesign required

Two of three independent reviewers returned REJECT (plan, code). The third returned
APPROVE-WITH-ADVISORIES *conditional on folding its two criticals before Phase 1* — which,
under a strict rubric, is the same disposition.

---

## Critical (blocks implementation)

**C-1 §3.3 is unimplementable as written — pre-conversion limit slice anchored on a post-conversion sort.**
`phase3-plan.md:17`; `daemon/services/work_resolver.py:1504-1521`. The plan requires the limit
slice to run "on the merged + sorted SOURCE ROWS" and cites `:1520` as the sort. `:1520` is
`records.sort(...)` — the **last** statement of `list_work`, sorting already-converted
`WorkRecord`s (conversion inline at `:1408`, `:1461`, `:1472`). No pre-conversion sort exists in
the function. The required restructure is never specified, and the **stable tie-break rule is
unspecified** — equal `created_at` currently orders by append order (Task block then Job block);
a pre-conversion sort must reproduce that or `limit=N` changes the delivered set.
*Fix:* accumulate `source_rows = [(normalized_created_at, row)]` from both branches in current
append order, sort with the same `_normalize_sort_key(reverse=True)`, slice, then convert only the
slice. Re-cite `:1520` as the comparator contract, not the slice site. Disclose that the Task
branch's `project_id`/`root_only` filters run post-conversion (`:1402-1406`) so a sliced Task
population can deliver < N.

**C-2 Admission gate underspecified — over-release and permanent-deadlock modes both open.**
`phase1-plan.md:18` (task 1.4), `:23` (pin 1.9(vi)). "An `asyncio.Semaphore` acquired with
`await`" — permit count unstated, release site unstated, no cancellation pin. If released in the
caller's `finally` on cancellation, the permit returns while the thread still runs (gate stops
bounding; executor queue becomes the unbounded path B4 exists to close). If skipped on a
cancellation path, permits leak and the seam deadlocks permanently after N cancellations — worse
than the B1 hazard that *is* guarded. Pin 1.9(vi) cannot see either; it never cancels.
*Fix:* permits == `DB_OFFLOAD_MAX_WORKERS`; release in the **executor future's done-callback**
(thread-side truth), never the caller's `finally`; add pins (ix) cancel while awaiting `acquire`
and (x) cancel while awaiting the result — both assert permits return to full AND the seam still
completes afterwards.

**C-3 P3 breaks four exact-count test packs with no adjudication rubric; P5 G6 then fails deterministically.**
`tests/unit/services/test_work_resolver_query_budget.py:295,324,410` (`assert spy.counts["instances"] == 2 / == 1 / == 0`),
`test_fix_c_read_model_split.py:750` (`call_count <= 5`), `test_mission_resolver.py:770,998`,
`test_job_queue_proxy_phase1.py:703,734`. Task 3.1 adds a second `_batch_instances` call, so `== 2`
becomes `== 3` for task-bearing shapes. P3's exit criteria name only the W-1 pin; P5's G6
("net-new reds = 0") therefore fails with no owner, no task, and no rubric separating legitimate
count increase from N+1 regression.
*Fix:* add a P3 task re-deriving those exact-count expectations, adjudicating each in the phase
evidence; name the four packs in P3's exit criterion and P5 5.4's scoped-suite list.

**C-4 G4 passes on a no-op HEAD — the discriminating assertion is prose, not predicate.**
`plan-overview.md:241`; `decisions.md:454`; `architecture-recommendation.md:343`. PASS iff (a)
STALL_HEAD ≤ STALL_BASE, (b) max, (c) p95+one TICK, (d) non-vacuity. The discriminating signal —
"STALL > 0 at BASE, 0 at HEAD" on the synthetic leg — appears only in prose. A HEAD identical to
BASE satisfies (a)-(d).
*Fix:* add PASS condition (e): `STALL_HEAD_synthetic == 0 AND STALL_BASE_synthetic > 0`, BASE-red
proven on the same leg.

**C-5 G6 has no collected-count or digest fence — silent collection loss reads as PASS.**
`plan-overview.md:243`; `phase5-plan.md:18` (non-vacuity scoped to *scoped* suites only). Two known
mechanisms make HEAD collect fewer nodes: the `g7` module-level `sys.exit(0)` class (named at
`:336`, given no assertion) and the async-hang class that killed the v0.27.11 whole-tree gate
(which shipped on compensating scoped evidence). Under the stated rule, every BASE-red that
vanishes because a module failed to collect scores "absent at HEAD → pass".
*Fix:* assert (i) `collected_HEAD >= collected_BASE` (declared skips), (ii) failed-count + FAILED
digest both sides, (iii) any collection error = FAIL not skip, (iv) a **pre-agreed** fallback for
the async-hang class (v0.27.11 compensating-evidence recipe, sha-pinned, replay preserved) decided
before the gate, not after it hangs.

**C-6 Phantom anchor: `jobs_crud.py:900-902` is a comment block — G10 and pin 3.9(b) are vacuous.**
`plan-overview.md:208,247`; `phase3-plan.md:19` (3.5), `:23` (3.9b). Confirmed independently by two
reviewers: `:900-902` holds comments about `work_resolver.list_work`/`_batch_instances` and filter
alignment; `grep "legacy JobItem-mirror\|degrades to"` = 0 hits. The real legacy-mirror fallback is
`if work_record is None:` at `:153` inside `_job_to_response`, plus the mission-ref degrade at
`:384-405`, triggered by `work_records_by_id.get(job.job_id)` at `:951`. G10 is the only defence
against the silent mirror-path regression D-29 exists to prevent.
*Fix:* re-anchor to `:951` + `:153`/`:384`; define the observable (per-request count of page-jobs
rendered with `work_record is None`) and assert "0 that were 1 at BASE".

**C-7 No boot / startup / shutdown gate anywhere, for the lane's only lifespan edit.**
`phase1-plan.md:21` (task 1.7 adds `await db_offload.shutdown()` before `await manager.shutdown()`
— `daemon/api.py:3809`, `daemon/manager.py:15223`); zero `livez|readyz|boot smoke` hits across the
planning set. Every prior staged lane ran an arena-local boot smoke. A drain that deadlocks or an
executor that fails to construct is a daemon that will not start or will not stop, and no gate
would see it.
*Fix:* P5 task "staged-build boot smoke (arena-local)": banner version, `/livez`+`/readyz` 200, and
a SIGTERM teardown **with a bridged read in flight** asserting no `Engine is disposed` and a
bounded exit. Fence per the standing critical note (staged boot smokes are not isolated by default —
path derivation resolves the live install root): scratch data dir, per-variable env scrub, live
`state.json` sha256 unchanged.

**C-8 Phase 5.1's blocking dependency is instruction-only and already violated.**
`phase5-plan.md:15,31`; `decisions.md:465-469` (D-37.4: repair lands as its own reviewable commit,
council-reviewed before any HEAD run). Observed in this worktree now: the repair exists as
uncommitted modifications + 4 untracked files in `.agents/tester/harness/eventloop-phase0/`, on the
same branch the developer will implement in. Consequences: (1) no reviewable commit for 5.1's
acceptance; (2) "no HEAD run before review" is unenforced — `run_all.sh` can run from this dirty
tree; (3) G7/G12 path-based fences are **already red** (untracked planning files + modified harness
are indistinguishable from developer writes by path alone); (4) `git add -A` / `commit -a` silently
absorbs the tester's WIP — the untracked-collider class the repo conventions call out.
*Fix:* (a) land the repair as a commit on a distinct ref/worktree by tester identity before P5,
with BASE-red evidence committed; (b) mechanical assertion: recorded `git rev-parse
HEAD:.agents/tester/harness/eventloop-phase0` equal to the reviewed tree sha + `git status
--porcelain` empty over that path at any HEAD run; (c) forbid `git add -A` in the lane, stated in
P5; (d) re-scope G12 to author/commit attribution, not path presence; (e) state which commit
carries the planning set.

---

## Warnings (fold in the same pass)

| # | Area | Citation | Issue | Fix |
|---|---|---|---|---|
| W-1 | criterion scope | `plan-overview.md:238` vs `:59,102,246,301`, `:96,97`; `jobs_crud.py:943` | G1's PASS predicate covers only the instances-path class, but the commission sentence is "sustained_loop_lag wedge captures stop". Verified residual on-loop sites: `jobs_crud.py:943` (sync `get_dlq_by_job_id` in `async def list_jobs` at `:730`), missions shape, prefs (`instances.py:496`), per-turn slot read (`daemon/graph.py:868`). A capture from any of those still wedges the loop and G1 stays green. | State the narrowing explicitly in the Objective + G1 row, name the four residuals; add a non-blocking companion assertion "captures at HEAD whose loop section contains any sqlalchemy/psycopg frame = 0" so residual fires are visible. |
| W-2 | latency criterion | `plan-overview.md:239,341-343`; `BASELINE.md:39` | The floor is gated on the flat(10) leg only. BFS(100) is the same endpoint and the slowest shape (p50 858.7 ms / p95 1000.1 ms at BASE); only its rts count is fenced (G9). | Add a BFS p95 BASE-relative leg, or state in G2 that BFS latency is measured-not-gated with the reason. |
| W-3 | gate ambiguity | `plan-overview.md:238` | "0 captures at HEAD" reads as both PASS and INCONCLUSIVE in one sentence. | Mechanise: `HEAD: captures >= 1 AND loop-section-hits == 0`; `captures == 0 ⇒ INCONCLUSIVE`; PASS requires the BASE artifact cited by path + sha256. |
| W-4 | gate independence | `plan-overview.md:240` | G3 ("same as G1") is unfalsifiable standalone; no artifact to fail if G1 never runs. | `PASS ⟺ G1 not INCONCLUSIVE AND narrow_BASE >= 4/8 AND narrow_HEAD == 0`. |
| W-5 | tautology | `plan-overview.md:274,296-303`; `phase5-plan.md:19` | G9 totals are "~" ranges and expectations are "updated from the developer's measured HEAD run" — the bound becomes a drift detector between two runs of the same code. Shape sets are correctly exact. | Freeze totals from the formula arithmetic before the HEAD run; measured run may only confirm or trigger a recorded amendment. |
| W-6 | baseline mislabel | `plan-overview.md:248,349`; `decisions.md:155` | G11 asserts "vs live v0.27.13"; live is **v0.27.12** (verified: `current` symlink, `/livez`). Arithmetic survives (115 files; v0.27.13's own delta was EMPTY) but the sentence hides that promoting this artifact supersedes unpromoted v0.27.13 and that the rollback target is v0.27.12. | Restate as "vs live v0.27.12 (re-derive at stage)"; name v0.27.13's unpromoted content in the rollback paragraph. |
| W-7 | fence realism | `plan-overview.md:244,367` | G7 "0 .env edits" contradicts house machinery: `stage.sh` re-stages `ENSEMBLE_SELF_ENV=live` and has chmod'd the live `.env` (v0.27.3, v0.27.6 disclosures). Either red at stage or silently unenforced. | Scope to "no `.env` content authored by this lane; machinery-owned marker/chmod disclosed and verified byte-identical sha256 pre/post". |
| W-8 | phase bookkeeping | `phase2-plan.md:54` vs `phase1-plan.md:54` | P2's exit criterion says `git diff --name-only` shows "exactly five production files … plus two constant lines in `constants.py`" — that lists six files; `constants.py` is also counted in P1's four. | Restate as six files; make additive-only mechanical (`git diff -U0` has zero deleted lines; both P1 constant names present). |
| W-9 | detail-path mechanism + blast radius | `phase2-plan.md:16,18`; `instance_lifecycle.py:5143`; `instance_read.py:818`; `instance_ops.py:348`; `stale_pause_sweep.py:298`; `mission_strand_healer.py:1518` | 2.4 requires the detail path to "share the ordered source" but names no mechanism. The obvious one (ORDER BY inside `list_child_ids_permanent`) silently reorders four other production surfaces not in the compared set. | Choose routing `:5143` through `list_child_ids_for_parents([id])` and **forbid** touching `list_child_ids_permanent`; list the four co-callers as unchanged/DO-NOT-TOUCH; extend pin 2.7(c) to statements emitted on both paths. |
| W-10 | config wiring | `phase1-plan.md:16,17`; `daemon/manager.py:756,779` | `ThreadPoolExecutor(max_workers=DB_OFFLOAD_MAX_WORKERS)` uses a name defined nowhere (`_DEFAULT` is); `config.limits.*` is manager-reachable while `offload()` is called from routers, and construction timing is unstated → env knob either dead or import-order-dependent. | Construct lazily via `db_offload.configure(...)` in the lifespan; pin the override in a subprocess; align the constant names; state read-at-import vs read-in-lifespan explicitly. |
| W-11 | non-vacuity of synthetic leg | `phase5-plan.md:15`; `plan-overview.md:238`; `:241` | Nothing defines what makes the synthetic handler-load leg admissible. The in-progress repair already carries `modes.py` with literal stack text — a valid harness self-test, but not evidence a daemon blocked. If synthesis is allowed, G1's BASE-positive and G4's signal are satisfiable with zero load. | Leg must drive real concurrent requests at the bridged endpoints; captures used as evidence must come from the daemon's own wedge-capture; synthesized stacks admissible only for self-test and labelled. |
| W-12 | shutdown | `phase1-plan.md:21`; `scripts/systemd/ensemble-daemon.service:60,115` | Drain stops accepting and `cancel_futures=True`, but with C-2's gate the executor queue is empty by construction — the accumulating class is the **semaphore waiters**, which drain never touches. Bound value unstated; pool threads are non-daemon so the process can outlive the bound into `TimeoutStopSec=90`. | Four-step drain: closed flag → fail all waiters with the same defined error → wait in-flight to `DB_OFFLOAD_DRAIN_BOUND_S` → `shutdown(wait=False, cancel_futures=True)`; add the constant; pin the waiter-failure path. |
| W-13 | deadlock guard | `phase1-plan.md:19,17` | B1 mandates "a runtime guard that raises a typed error" but never names the detection mechanism; the natural `ContextVar` marker is defeated because `copy_context()` is taken in the loop thread at submit time. | Specify thread identity: `threading.current_thread().name.startswith("db-offload")` → raise before submitting. Defence-in-depth, matching B7. |
| W-14 | cancellation | `phase1-plan.md:20` | "Contain `BaseException`" swallows `asyncio.CancelledError`, converting cancellation into a "defined outcome" daemon-wide. | `except asyncio.CancelledError: raise` before any BaseException handler; add pin (xi) caller cancellation propagates unchanged with permit returned. |
| W-15 | observability / governance | `phase1-plan.md:24`; `decisions.md:225` | `DB_OFFLOAD_QUEUE_WARN_DEPTH` alarms on executor queue depth, which is ~0-1 by construction behind the gate. The real backlog is the waiter set, which nothing counts — and this alarm is the designated **re-open trigger for the async-native campaign (D-19)**, so the deferral's escape hatch cannot fire. | Count waiters + in-flight, alarm on the sum relative to permits; pin by saturating the gate. |
| W-16 | readiness behaviour | `plan-overview.md:363`; `daemon/services/readiness.py:999-1020` | "readiness.py untouched" is enforced by a diff assertion, which proves the file is unmodified, not that behaviour is unperturbed: ≤6 persistent borrowers perturb the `checkedout()` series that `_checkedout_rising` requires to be strictly monotonic — biasing toward false-negative (under-reported leak). | Keep DO-NOT-TOUCH; add a behavioural leg comparing `leak_signal.{detected,checkedout_rising,streak}` and `pool_state_sqlalchemy` BASE vs HEAD under steady-state bridge traffic; record occupancy delta. |
| W-17 | enforcement anchor | `phase1-plan.md:25` | TB-1..TB-7's "a fourth bridged callable is a deliberate failure" has no production anchor — the registry is UNFUNDED (D-19), so 1.11 checks a hardcoded list against itself, and it is authored in P1 before P2-P4 create the sites. | Add a source-scan pin over `daemon/` (every `offload(` site must be in the expected set); run it at P5. |
| W-18 | limit propagation | `phase3-plan.md:17`; `jobs_crud.py:815` | 3.4 detects **presence** via raw query params but never specifies which **value** is forwarded; the endpoint clamps at `:815` (cap `MAX_JOB_LIST_LIMIT=100`). Forwarding the raw param means `?limit=10000` enriches 10,000 rows behind a 100-row page. | Forward the clamped local `limit`; presence-only detection via `query_params`; pin `?limit=10000` enriches ≤ 100. |

---

## Advisories

- **A-1** `plan-overview.md:102` / `phase3-plan.md:22` — T-3 is the only deferred item with no named owner. Add a backlog board item.
- **A-2** `plan-overview.md:242,245` — G5 and G8 have no non-vacuity fence. G5: `compared_shapes == 7` (frozen list), ≥1 request per shape. G8: `len(selected) == <declared N>` in the pack file.
- **A-3** `plan-overview.md:239,193` vs `phase4-plan.md:18` — G2's diagnostic says "raise the pool to 6" when 6 is already binding; §6.2 and 4.4 disagree. Repoint at `LIMITS_DB_OFFLOAD_MAX_WORKERS`.
- **A-4** `phase3-plan.md:15-16` — `_batch_instances`' signature changes after its first new caller. Promote 3.2's step (1) to a task 3.0 so each call site is written once.
- **A-5** `phase1-plan.md:57` vs `:21` — P1 exit criterion 6 says "no production call site changed" in the phase that changes `daemon/api.py`; say "no *bridged* call site".
- **A-6** `plan-overview.md:251,343,406` — G2's 0.23s/0.51s anchors are disclosed in five places (adequate), but no fallback exists if the sibling composite record cannot confirm them, and they are the PASS/FAIL boundary. Name the fallback: "commission-mandated, annotated unverified, never re-baselined".
- **A-7** `phase1-plan.md:22`; `decisions.md:493` — "~1,400 executor **sites**" is an occurrence count (1,400 lines / 1,401 occurrences; **1,066** invocation sites at HEAD). Gate-irrelevant but the label is wrong. The 1,351 @ `6bebb9ba0` drift claim is exact.
- **A-8** `phase2-plan.md:16`; `plan-overview.md:135` — no plan file mentions chunking. Add one sentence: no chunking; a chunking implementation REDs the statement-count pin by design.
- **A-9** `phase3-plan.md:23` — 3.9(a)/(f) exercise only all-absent and all-absent-degraded. Add a mixed non-degraded batch node (present rows keep Instance fields, orphan keeps the mirror, `instance_absent` True only for the orphan).
- **A-10** `plan-overview.md:69` vs `daemon/routers/work.py:182-186` — "six kwargs" lists five names and the signature takes five. Fix the count (a developer hunting a sixth could add one). Also `queues.py:652` → `:651`.
- **A-11** `plan-overview.md:129` — TB-7 governs DB writes only; nothing regulates shared in-process state. One real instance exists: `repository.py:197` `_descendant_cap_warn_last_emit`, mutated `:1606-1608` (`.clear()` at `len > 256`). Six threads can lose a throttle entry — log-rate only, never results. Consider TB-8 (idempotent reads, no shared mutation).

---

## Verified clean (no action)

- **D-23 is the standout of the plan.** The claim that F4-A protects the bridge is not merely corrected — it is *forbidden* in six places with a reviewer grep. Independently confirmed: `_refuse_pool_admission_at_capacity` defined `readiness.py:1541`, called only `:1627`/`:1929`.
- **Pool envelope re-derived correctly:** `constants.py:264` (15) + `factory.py:434-435` (overflow 10) vs `readiness.py:165` (capacity 25). The "overflow is not a worker budget" correction is right and materially strengthens the safety argument.
- **Schema-EMPTY mechanically enforced** per phase (P1/P3/P4 exit criteria + `plan-overview.md:349`); no migration file in any phase's list; `LimitsConfig` correctly classed as config-not-schema.
- **Stage-only fence survives** (G7 + 5.8, command-output-evidenced). No promote command anywhere in the set.
- **Ordering closed:** D-B (3.2) before the conversion slice (3.3) before router limit propagation (3.4); pre-conversion slice correctly placed inside P3; P1-first is right.
- **D-B mechanism sound:** `_batch_mission_fields` (`:2127-2175`) keys only by present instances, so an orphan's `mission_fields` is `None` and the `:1878` fallback fires for 100% of orphans; message-type orphans pay both `:1847` and `:1878` (explains 1,434 singles over 1,250 orphans).
- **Degradation-aware absence set (D-28)** correctly derives from the existing W-1 contract and preserves ABSENT vs PRESENT-BUT-EMPTY; pin 3.9(f) tests the distinction.
- **Children ordering contract** is multiset + per-parent + global + static SQL pin + all-rows + detail-shares-source (mechanism gap = W-9, not the contract).
- **`241` is BASELINE-measured and reproducible** (`BASELINE.md:39,48`; 100 roots + 141 descendants on `limit=100` root-based pagination) — not a commission-body number.
- **`include_descendants` keep+pair is a genuine no-op** (both constants re-encode existing defaults; zero result-set delta).
- **Anchor discipline: ~73 of ~78 cited `file:line` values print the promised content exactly**, including the non-obvious A15 corrections.
- **Callee transaction residual cleared:** `instance/repository.py:1434-1645` and `defer_block_resolver.py:274-340` contain **zero `await`**, construct their own session/connection, and take no session parameter → safe to run on a worker thread as-is; nothing to add to DO-NOT-BRIDGE.

---

## Gaps / unverified

- G2's 0.23s/0.51s anchors: not re-derivable from `BASELINE.md`; commission-body by the plan's own admission (disclosed in five places, bounded, tester-confirmed pre-gate). Disclosed — judged adequate subject to A-6.
- Per-shape post-lane rts projections: arithmetic over BASE, not measurable pre-implementation.
- The five "measured" pre-fold numbers in D-29/D-30 (8/10, 76/100, 34/100 patch-ups; 49/179 rts): inherited from the architect/commission body, not re-derivable from committed BASELINE — flagged inherited, not verified.
- Whether the in-progress harness repair keeps BASE red and whether its HEAD mode is sound: **out of scope by instruction**; not reviewed, not run.
- No executed test/suite result: this was a plan-level gate; no implementation exists.

---

## Leader decisions required

1. **Ratify or widen the criterion (W-1).** Is acceptance "wedge captures stop" (commission) or "instances-path loop frames = 0" (G1)? The residual on-loop sites (`jobs_crud.py:943`, missions, prefs, per-turn slot) will still wedge the loop. G1 will be green either way, so the narrowing must be stated in the criterion row or someone will quote a green G1 as closing the commission.
2. **G2's PASS boundary owner (A-6)** if the sibling composite record lacks 0.230/0.510.
3. **Which commit carries the planning set and the tester repair (C-8)** — house precedent ("planning rides the developer's commit") is in tension with G7/G12's path fences, and the repair currently belongs to nobody.
4. **Pre-agreed whole-tree fallback for the async-hang class (C-5)** — v0.27.11 shipped on compensating evidence after the same class hung; deciding mid-gate is how false greens are born.
