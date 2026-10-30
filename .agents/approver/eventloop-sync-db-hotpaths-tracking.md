# Approval Tracking — eventloop-sync-db-hotpaths (wi-3f5bea95)

Plan set: .agents/shared/planning/eventloop-sync-db-hotpaths/ (10 files, ~2,653 lines, 5 phases)
Artifact class: PLAN (large, multi-phase) -> skill `plan-approval`
Dispatch mode: 2 section-partitioned workers (large-plan exception)

---

## Iteration 001 — 2026-10-29T23:5xZ — REJECTED

Workers (both `plan-approval`, cold context):
- W-A `1428e3a8` -> error (upstream rate limit) -> revived -> error -> replacement `dace8760` -> REPORTED
- W-B `5c6ccad5` -> error -> revived -> error -> replacement `880bf6f6` -> error -> revived -> REPORTED

Both scopes ultimately obtained. No verification gap in the final verdict.
Failure mode across all six failed attempts: upstream `Rate limit exceeded` only; zero substantive output lost.

### Blocking findings (5, after dedup)

B1. Permit-release site contradicts the prescribed executor call form.
    phase1-plan.md task 1.1 prescribes `loop.run_in_executor(...)` (returns an asyncio Future, cancelled
    when the awaiting task is cancelled) while D-43 mandates release in the EXECUTOR future's done-callback.
    Implementing as written fires the callback at cancel time -> the over-release hazard D-43 exists to close,
    and cancellation leaks permits. Fix: name the substitution (`_EXECUTOR.submit(ctx.run, partial(...))` +
    `fut.add_done_callback(release)` + `await asyncio.wrap_future(fut)`). [W-B #1]

B2. Admission gate / drain specified on primitives that cannot carry the required semantics.
    phase1-plan.md task 1.4 names `asyncio.Semaphore`, which cannot fail parked waiters (drain step 2) and
    exposes no waiter count (pin xiii, D-53 alarm). Plus W-B: the closed-flag check vs `submit` is non-atomic
    -> a late submission hits `shutdown(wait=False, cancel_futures=True)` and surfaces
    `RuntimeError: cannot schedule new futures after shutdown` instead of the defined error, and
    `cancel_futures=True` propagates CancelledError rather than the uniform contract error.
    Fix: specify a custom gate (permit counter + deque of asyncio.Future, release in done-callback) and an
    atomic submit-vs-close protocol (register-then-check under the gate, or close-then-drain remaining queue).
    [W-A #2 + W-B #2, merged — both workers converged independently]

B3. G5 byte-identity success criterion is unsatisfiable AND unmechanised.
    (a) middleware hashes raw response bytes with no normalization hook, so the mandated children-order
        normalization cannot be applied to the hash;
    (b) fixture stamps created_at/updated_at/last_activity_at with `datetime.now(timezone.utc)`
        (seed.py:116, :244) -> BASE vs HEAD bodies cannot match even for identical code;
    (c) no `gate_g5` exists in the harness; no `compared_shapes == 7` assertion anywhere;
    (d) instrument SHAPES has 8 entries — `jobs_active_badge` omitted from the frozen 7, absent from the
        §(d) table and G9 targets, and it is a jobs shape with NO limit param -> C-4 cannot bound its enrichment.
    Fix: pin the fixture clock; implement gate_g5 incl. the shape-count fence and the `/api/instances/{id}`
    detail request §(e) requires; set compared_shapes to the real count (8) or exclude a named shape with a
    recorded reason; state that the tester re-hashes post-normalization bodies. [W-A #3]

B4. C-4 batched patch-up recipe breaks response-byte identity and has no buildable owner.
    phase3-plan.md task 3.5 promises byte-identical enrichment, but `_job_to_record` renders
    started_at/completed_at from `task_timing` (work_resolver.py:1466/:1477, helpers :875-882/:903-913) and
    `_task_started_at(None)` -> None. The stated three-batch recipe omits the per-page task-timing batch
    (`get_timing_by_work_ids`, already at :1440-1445) -> repaired page jobs render null timestamps where BASE
    rendered real ones. Also no owner/entry point/by-ids surface (no JobItem get-by-ids method exists; adding
    one would breach exit criterion 8's three-file boundary).
    Fix: add the task-timing batch as the fourth batch; place the patch-up in `list_work` behind an optional
    `ensure_job_ids` param (or a resolver batch entry point); pin field-level equality for repaired page jobs,
    not just `work_record is not None`. [W-A #4]

B5. P2 exit criterion 6 ("git diff -U0 shows ZERO deleted lines over the six touched files") is unsatisfiable.
    Tasks 2.2 (replace per-row loop with ONE batch call), 2.3/2.4 (wrap two manager calls), 2.6(c) (add
    include_descendants=False to two call sites) each delete or modify lines -> >=5 deletions for any correct
    implementation. A mechanical reviewer REDs a correct phase or induces a falsified proof.
    Fix: scope additive-only to daemon/constants.py + the untouched `list_child_ids_permanent` body + "both P1
    constant names still present"; replace "ZERO deleted lines" with "no deletions outside the listed edit
    regions". [W-A #1]

### Non-blocking notes carried forward
- P1 exit criterion 3 says api.py changes are "the lifespan drain call only" while task 1.3 puts `configure()`
  in the same lifespan edit — wording fix (criterion 3).
- G5 detail endpoint: §(e) requires `/api/instances/{id}`.children in the compared set; SHAPES has no detail
  request — name the pin or the artifact.
- G9 "frozen" ranges are ranges ("4-5", "~8") while labelled FROZEN FROM FORMULA ARITHMETIC — state an exact
  integer or a one-sided <= bound.
- `_batch_instances` empty-input early return (work_resolver.py:2408-2409) must return ({}, False); plan only
  specifies degraded=True on the SQLAlchemyError path.
- C-1 batched children SELECT shares D-B's observation window (row committed between batch and fallback is
  missed) — disclosed for D-B (risk 7) only; add one sentence.
- Task 2.4 should state `by_parent.get(instance_id, [])` so a childless instance yields [] (matching BASE).
- Re-entrancy guard (D-52) covers only the `asyncio.run`-inside-worker vector; `run_coroutine_threadsafe`
  (forbidden by TB-5 as discipline, not code) stays unguarded. Pin must be driven via asyncio.run inside the
  worker — state it.
- `configure()` un-configured/idempotent contract unspecified (`_EXECUTOR is None` behaviour; second call
  leaks the prior executor).
- Caller-side "no session across await offload(...)" is stated as a callee rule only; none of the in-scope
  sites holds one, but add the router-side line so a future site cannot regress it.
- Citation drift (harmless, +/-1): work_resolver.py :1778/:1847/:1878 vs :1779/:1847/:1878;
  jobs_crud.py :942/:950 vs :943/:951; SQLAlchemyError path :2417-2432 vs :2416-2431;
  manager.py:2785-2799 resolves to `_session_scope` not a write scope (TB-6 argument survives);
  constants.py env helpers at 420-428 not 423-436.
- Task 4.3 "Depends On" repeats "4.1" (cosmetic).

### Verified positives (both workers)
- Anchor integrity strong: ~25 (W-A) / 20+ of 22 (W-B) file:line citations resolve within +/-1; six
  `list_child_ids_permanent` production call sites confirmed, 2 in scope.
- Residual discipline exemplary: 19-entry routed-out register, G1 four named residuals, "scoped closure —
  never class-closure" clause, T-3 deferral with named owner and re-entry rule.
- G6 anti-false-green fences strongest in the set; G2 honestly annotated `commission-body, unverified`.
- D-54 refusal to add ORDER BY inside list_child_ids_permanent (4 out-of-scope co-callers) is the right call.
- Pool sizing chain verified: constants.py:264 (15), factory.py:434-435 (overflow 10), readiness.py:165 (25),
  `_refuse_pool_admission_at_capacity` probe-scoped (:1627, :1929) — D-23 correction accurate.

### Next
Iteration 002: re-dispatch cold workers after the developer folds B1-B5. Workers must NOT receive this file.

---

## Iteration 002 — 2026-10-30T00:44Z — REJECTED

Workers (both `plan-approval`, cold context, section-partitioned; no rejection history passed):
- W-core `b7e35977` approve-worker-core: plan-overview/architecture-recommendation/decisions/BASELINE/technical-analysis/phase1/phase2 → REJECTED (2 blocking, 15 notes)
- W-phases `46da7968` approve-worker-phases: phase3/phase4/phase5 (+overview/decisions cross-reads) → APPROVED (0 blocking, 6 notes)

Both scopes obtained first pass; zero rate-limit failures; no verification gap.

### Iteration-001 blocker closure adjudication (approver, post-verdict comparison)
- B1/B2 (offload seam + admission gate): closure NOT sustained — the iteration-001 prescribed FORMS are present
  (submit + done-callback release + wrap_future; custom gate permit-counter + waiter deque), but fresh evaluation
  found two NEW, deeper blocking defects inside that revised mechanism (RB1/RB2 below).
- B3 (G5): sustained — no fresh blocker; byte-identity pinned at field level; residuals enumerated; harness
  dependency disclosed and out of scope (W-phases report; W-core F5).
- B4 (C-4 patch-up): sustained — no fresh blocker; W-phases N1 (non-blocking): router call-site shape implied,
  add the 2-line sketch.
- B5 (P2 exit criterion): sustained — no fresh blocker (W-core covered phase2; nearest note F13, non-blocking).

### Blocking findings (2, from W-core)

RB1. Parked-waiter cancellation permit accounting contradicts the alarm model.
     phase1-plan.md task 1.4 + pin (ix): pin asserts "cancel while awaiting acquire ⇒ permits return to full",
     but the alarm model states waiters do NOT hold permits (permits = workers; waiters + in-flight = alarm sum).
     Unresolved: does acquire reserve at enqueue (double-counts in the alarm) or not (pin (ix) nonsensical)?
     A coder cannot implement without inventing semantics.
     Fix: decide reserve-at-enqueue vs no-reserve; restate pin (ix) as the matching observable (e.g.
     permits == in-flight after a cancellation storm; a cancelled waiter alters the permit count ONLY under the
     chosen reservation model; alarm-sum formula holds throughout).

RB2. Cross-thread wake pattern unspecified — TB-5's literal wording forbids every legal option.
     Release runs in the executor future's done-callback on the worker thread (D-43), but the plan never names
     the primitive that wakes an event-loop-parked asyncio.Future waiter. plan-overview §TB-5 forbids callbacks
     into the loop (asyncio.run_coroutine_threadsafe, loop.call_soon_threadsafe to a coroutine); read literally
     the gate is unwakeable. The safe pattern — loop.call_soon_threadsafe(waiter.set_result/set_exception, ...)
     (future RESOLUTION, not coroutine scheduling) — is neither named nor exempted.
     Fix: name the wake primitive in phase1-plan.md task 1.1/1.4 (incl. loop-reference capture at configure());
     amend TB-5 to forbid coroutine-scheduling callbacks on the loop while explicitly exempting thread-safe
     asyncio.Future resolution via call_soon_threadsafe.

### Non-blocking notes carried forward (deduped)
Phases 3-5 (W-phases): N1 task 3.5 router call-site (ensure_job_ids alongside limit) implied — add 2-line
  sketch; N2 task 3.10(D) kind="report"/None test files left to discovery; N3 G1 headline over-claims vs D-50
  scoped-closure phrasing — retitle "zero in-scope instances-path frames (exempt set per §routed-out)"; N4 task
  5.1 hard-blocked on tester increment #2 (track on board); N5 5.11 started-not-finished marker is a production
  touch — guard + pin byte-inert in 5.2; N6 4.7 fallback should default "produce or block", disclosure the
  exception.
Core (W-core): F2 state acquire→submit→callback→await ordering in task 1.1; F3 correct "wrap_future bridges
  without wrapper-future cancellation semantics" wording — cancellation DOES propagate (no-op stop for a running
  thread); don't let a coder disable it; F4 state single-loop cooperativity for close-vs-register atomicity;
  F8 drain step-3 hang-case sentence (non-daemon threads at process exit); F11 alarm fires when waiters > 0 /
  sum exceeds a defined threshold; F13 harness attribution: out-of-scope co-callers (instance_read/instance_ops
  tools, stale-pause sweep, mission strand healer) can still produce narrow captures at HEAD; F14 anchor offsets
  ±2 (tools/instance.py :6168 vs :6170; instance_lifecycle.py :5110-5116 vs :5113-5117); F6 "five site-groups" →
  six; F9 thread-name-prefix false-positive (low); F10 TB-6 static-pin process gap; F12 IN-list ceiling deferred
  to new D-id (fine for ≤100 page sizes); F15 G1 INCONCLUSIVE exit-code mapping = tester-increment scope.
Merged: W-phases N4 + W-core F5 — tester-harness dependency disclosed, G5/5.1 not self-runnable from this plan.

### Verified positives (both workers)
- 33+ daemon anchors verified in-tree across both workers; all resolve (±2 lines; F14 offsets noted).
- Honesty discipline verified strong by both: D-50 scoped-closure clause, 19-entry routed-out register,
  enumerated residuals, G1/G2/G4/G6/G7/G11 re-specifications, capacity-25 correction, D-42 pre-conversion-slice
  restructure, D-B degradation-aware absence set.

### Next
Iteration 003 (FINAL before 3-cap): developer folds RB1+RB2 (phase1 seam spec; the two fixes are narrow and
do not touch later phases' contracts) + optionally the carried notes. Workers must NOT receive this file.
If iteration 003 rejects → Status: ESCALATED per cap.

---

## Iteration 003 — 2026-10-30T01:22Z — APPROVED (FINAL; cap not reached)

Workers (both `plan-approval`, cold context, FRESH spawns — no revival of prior-iteration workers):
- W-core-3 `acae26cf` approve-worker-core-3: overview/architecture-recommendation/decisions/BASELINE/technical-analysis/phase1/phase2 → APPROVED (0 blocking, 4 notes)
- W-phases-3 `652db57b` approve-worker-phases-3: phase3/phase4/phase5 (+overview/decisions cross-reads) → APPROVED (0 blocking, 7 notes)

Both scopes obtained first pass; zero failures; no verification gap. ~54 anchors verified in-tree
across both workers; comparator equivalence (task 3.3) independently re-derived; library-semantics
claims verified against actual asyncio/SQLAlchemy/CPython behavior.

### Iteration-002 blocker closure adjudication (approver, post-verdict)
- RB1 (permit accounting): CONFIRMED CLOSED — D-79 NO-RESERVE model (waiters hold no permits;
  permits == workers == in-flight; cancelled parked waiter returns nothing) verified consistent across
  D-43/D-66/D-67/D-53; every path (success, exception, cancel-parked, cancel-in-flight, close-race,
  drain) has a named owner and a pin ((ix)/(x)/(xi)/(xiii), late-submit-after-close). Contradiction-free
  and simultaneously assertable.
- RB2 (wake primitive): CONFIRMED CLOSED — D-80 names loop.call_soon_threadsafe(waiter.set_result/
  set_exception) as thread-safe FUTURE RESOLUTION; loop captured once at configure(); TB-5 amended
  verbatim in ALL THREE homes (decisions D-35, plan-overview TB-5, phase1 tasks 1.1/1.5); rule and
  prescription now agree — no home re-forbids the sanctioned primitive.
- Spot-probes: F3 wrap_future cancellation semantics now stated accurately (no-op stop for a running
  thread; done-callback still releases); F8 drain hang-case named (exit path fails loudly; non-daemon
  threads bound the exit; systemd TimeoutStopSec=90/SIGKILL backstop — never a silent wedge); F14
  instance_lifecycle anchors resolve exact (:5113/:5115); tools/instance.py anchor adjudicated below;
  no fold-introduced contradiction found by either worker (fold index maps stale claims to correcting D-ids).

### Cross-worker conflict adjudicated by approver (direct 4-line read, aggregation merge)
tools/instance.py: W-core-3 read the manager.list_instances(limit=DEFAULT_PAGE_LIMIT) call at :6168;
W-phases-3 at :6170. ADJUDICATED at HEAD 3e976696b: call at :6170 (:6168-6169 = comment block; :6165 =
def). W-phases-3 correct — the plan's fold-4 correction to :6168 went the wrong direction; the original
:6170 citation was right. Non-blocking (grep-resolvable one-line citation drift).

### Notes carried to implementation (deduped; all non-blocking under the workers' materiality bar)
 1. [W1 fold REQUIRED] Harness tree-sha pin stale: plan pins 88009e7b (c3d87af26 tree) but increment #2
    landed (3e976696b → harness tree 3e281cc5); one untracked WIP file also reds the porcelain fence.
    Fail-closed (can only RED, never falsely pass) and 5.1 blocks all HEAD runs until increment #2 +
    council review close — re-pin to the reviewed tree + state the re-derivation rule when the block lifts.
 2. [W1 fold REQUIRED] Shape-canon count: plan G5 says compared_shapes == 8; landed gate_g5 fences 9
    (8 shapes + detail request; main.py:787-800). Instrument is the stricter and is what runs — carry the
    9-entry form in the 5.10 handoff or restate the G5 row.
 3. Phase5 exit criterion 8 says "ten items"; task 5.9 enumerates twelve — stale count; larger set governs.
 4. Phase4 task 4.1 Depends-On self-references "4.1" (should be P1).
 5. Phase2's tools/instance.py citation should read :6170 (adjudicated above).
 6. Citation drift ±1-8 lines elsewhere (work_resolver :2416 vs :2417; :2003; tests :745 vs :750;
    :2328 vs :2320; D-78 :949/:951) — all grep-resolvable; no coder misled.
 7. release() thread home implied, not mandated — implementer states the chosen thread home in the module
    docstring (pins 1.9(ix)/(x) convict a racy implementation either way).
 8. Cancelled-waiter deque-removal primitive unspecified — outcome pinned by 1.9(ix)(b); either standard
    mechanism satisfies.
 9. D-25 "six kwargs" historical wording retained — superseded by A-10 (five; work.py:181-187 verified
    in-tree); D-79 item 5 convention covers.
10. technical-analysis.md retained as unamended history; fold index maps every stale claim to its
    correcting D-id.
11. Task 3.0/3.8 acceptance wording runs ahead/behind its own scope — wording only.

### Verdict basis
Zero blocking findings from either fresh worker under the explicit materiality bar (unbuildable /
self-contradiction / lying gate). Gates verified honest (G1-G11 incl. G4(e), G6 four fences, G5 re-spec),
safety invariants upheld AND pinned, D-42 comparator equivalence proven against BASE, D-B absence set
three-site closure verified, B4-family four-batch patch-up complete with field-level pins. APPROVED.

### Closure
Plan APPROVED at iteration 003. Lane proceeds to implementation; W1-fold items 1-2 above are required
before any HEAD-mode gate run (they bind the harness, which is tester-owned and was out of scope).
