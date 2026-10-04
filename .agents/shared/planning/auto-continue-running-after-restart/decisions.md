# Decisions: auto-continue RUNNING instances after daemon restart

Every decision below adopts the technical-analysis.md recommendation as the plan's working decision (per the G1-G8 adjudication rule), with rationale + the rejected alternative. Nothing the analysis already decided was re-decided. Sources: technical-analysis.md §§Architecture, A-F, G; investigation.md §C; planner's own verification hop for G4.

---

## D1. Continue-in-place via `_schedule_explicit_handle_resume(silent=True)` — NOT enqueue a synthetic "continue" message

**Decision:** The boot pass continues the interrupted turn in place: `find_paused_or_cancellable_turn`-class selection → `_has_checkpoint` → `_schedule_explicit_handle_resume(silent=True, target_work_id=<orphan work_id>, handle_work_id=<orphan work_id>, selected_suspension_reason=None, route_outcome="boot_continue")` → background `_resume_processing_background(is_retry=True, silent=True, message_source="cascade_resume")` → `graph_input=None` → `astream(None)` (pure checkpoint continuation, no HumanMessage injected; `instance_messaging.py:4267-4269`).

**Rationale:** Reuses the most-exercised resume primitive in the codebase (answer-gate + report_or_external_resume routes already drive it); zero new messaging mechanics (AC2); no spurious user-visible message; no new Task/MessageQueue rows.

**Rejected alternative:** the WC-wake precedent's `enqueue_message("continue", source="system:resume_wake", priority=0)` (`manager.py:10837-10860`) — creates a fresh Task + MessageQueue row that would race the existing orphan RUNNING task and inject a spurious HumanMessage — exactly what the user's "no parallel messaging/continuation path" directive forbids.

## D2. Durable idempotency = Option a′ — boot CAS on `task.auto_continued_at`, stamped AFTER the resume schedules

**Decision:** New nullable column `task.auto_continued_at TIMESTAMP`. Per candidate: `_has_checkpoint` pass → resume scheduled with return `{"status": "resuming"}` → ONLY THEN stamp via CAS `UPDATE task SET auto_continued_at=:boot_epoch WHERE id=:id AND status='running' AND (auto_continued_at IS NULL OR auto_continued_at < :boot_epoch)`. Candidate selection uses the same predicate shape folded into one statement (claim-guard convention, `repository.py:2230-2294` anti-starvation invariant). StaleTaskRecovery backstop catches any miss at boot+10 min.

**Rationale (a′ vs plain a):** stamping after the resume actually scheduled makes "marked" ≡ "continued" — eliminates the marked-but-never-resumed window that plain Option (a) (CAS-first-RETURNING) has when the resume then fails (e.g. no checkpoint). The residual crash gap (die between schedule and stamp) re-schedules on next boot, which is safe: `astream(None)` on an advanced checkpoint is idempotent at the LangGraph level and the ExecutionGate per-instance lock serializes.

**Rejected alternatives:** (b) `instances.auto_continued_at` — the per-instance CAS would be a Python pre-check, structurally weaker than a task-row atomic UPDATE, duplicating a predicate the claim-guard already owns; also pollutes the instance row with task-scoped bookkeeping. (c) `idempotency_key` partial-UNIQUE / wake-journal CAS / `uq_job_locks_slot` — wrong substrates (the feature creates NO new row to dedup). (B: extend StaleTaskRecovery with threshold=0) — rejected: burns `max_retries=3` on reboot loops (retry budget is for transient failures, not process death), changes StaleTaskRecovery's blast radius for both boot and steady-state loops, and cannot correctly exclude WAITING_CHILDREN-with-RUNNING-task races.

## D3. Boot placement: `daemon/api.py:1522` — after `sweep_wake_records` (:1521), before `upgrade_journal_sweep.start()` (:1538)

**Decision:** Insert the pass inside the existing boot envelope try/except as its own inner try/except, between the wake sweep block and `upgrade_journal_sweep.start()`.

**Rationale:** After the bus (`init_dependency_bus`, `api.py:1306`) so child spawns from continued turns are tracked; after the wake sweep so the wake's PENDING Task row exists before the resume schedules (deterministic FIFO, AC4); before the periodic tick (consistent state); before the HTTP listener (no user-message race; later user messages simply queue behind per the existing claim path). Anchors verified at cf8efbef.

**Rejected alternatives:** before the wake sweep — breaks FIFO determinism and the arm-notify UX (wake would arrive mid-continued-turn); inside StaleTaskRecovery — conflates age-gated steady-state backstop with boot-corrective (different blast radius; G8 keeps them orthogonal and complementary); after listener-up — invites user-message races during the pass.

## D4. Terminalize-early REJECTED (AC4) — the orphan Task stays `status='running'`

**Decision:** Continue-in-place; the orphan RUNNING row is the durable proof one driver owns the turn. The pass never force-cancels, never reaps, never writes task status.

**Rationale:** The claim-guard (`instance_id NOT IN (SELECT instance_id FROM task WHERE status='running')`, atomic inside `claim_pending_task`) blocks the wake's PENDING claim while the orphan is RUNNING → wake lands FIFO-behind the continued turn → matches arm-notify UX (outcome report AFTER the turn finishes). Terminalizing early opens the claim window → wake + retry/continue compete (Interleaving Y — the double-fire). Pinned by phase-3 tests incl. the structural no-reaper guard.

**Rejected alternative:** `force_cancel_and_schedule_retry` at boot (Interleaving Y / Option B mechanics) — creates the double-turn window and breaks the wake UX.

## D5. G1 — task-level column (`task.auto_continued_at`), per analysis §A

**Decision:** Column on `task`, not `instances`. **Rationale:** atomicity (single-statement CAS mirroring the claim-guard) beats instance-level greppability; strictly smaller patch; the "which instances were continued this boot" question remains answerable by joining. **Rejected:** `instances.auto_continued_at` (Option b) — see D2.

## D6. G2 — do NOT increment `task.retry_count`

**Decision:** The pass never touches the retry budget; `is_retry=True` is forced via the resume kwargs (`is_retry` derivation `task_processor.py:521-527` accepts `original_resume_mode`), so checkpoint resume fires regardless.

**Rationale:** the budget is for transient failures; a reboot loop is recurrent process death — burning budget would permanently fail healthy turns after 3 restarts. Documented in the module docstring. **Rejected:** incrementing (budget symmetry argument) — conflates failure modes.

## D7. G3 — strict `status='running'` task predicate only (no `idle`/`queued`)

**Decision:** The feature is RUNNING-only. `idle`/`queued` instances have no interrupted in-flight turn (no checkpoint worth resuming; an idle instance's last checkpoint is from an already-terminal turn). **Rejected:** widening the predicate — scope creep beyond the user-verbatim feature core.

## D8. G4 — attestation gate: RESOLVED by planner verification hop; no scope change

**Decision:** No attestation handling in the pass. Planner hop (2026-10-04) verified: the only reset site for `attestation_denied_count`/`completion_gate_escalated` is `instance_messaging.py:1977-2015` — keyed on revive-from-terminal via a NEW top-level user/mission message (`priority==1` AND `msg_type==HUMAN`), a path reachable ONLY through `enqueue_message`, which the boot pass never calls. The attestation gate itself is a graph node (`graph.py:5338`) that re-evaluates normally inside the resumed turn; the silent resume inherits the original turn's session state (user-origin window: silent is the explicit skip, `manager.py:8101-8112`).

**Rationale:** closes investigation Unverified #3 with evidence; no Open-Question escalation needed because the verification produced a definitive answer that does not change scope. **Rejected:** treating it as an open question — unnecessary once verified.

## D9. G5 — JobFeedbackObserver: no follow-up needed; NO new terminal tokens

**Decision:** The resume routes through the same `complete_task`/`fail_task` flow as every other resume; the observer's terminal-token contract (accepts only `completed`/`error`/`failed`, `job_feedback_observer.py:316`) is unchanged. The feature introduces NO new terminal status string — pinned in phase-2 review (AC2 audit) and covered by the terminal-token repo trap in risk-register R8.

**Rationale:** structurally identical to cascade-resume, which already settles jobs correctly. **Rejected:** re-verifying observer keying — off critical path, already argued by the analysis.

## D10. G6 — sequential v1

**Decision:** The per-candidate loop is sequential; no `asyncio.gather` over checkpoint probes. Wall-clock target <5 s scheduling is met for observed N (≤33 in the worst observed crash loop). Batched probes = v2 follow-up. **Rejected:** parallel probes — log-linearity and determinism beat a latency win we don't need yet.

## D11. G7 — RUNNING task with terminal/missing instance row: SKIP (no revive, no reap)

**Decision:** The selection subqueries exclude PAUSED/terminal/WC instance rows, so such tasks are invisible to the pass. No revival (user's "terminal never touched" directive; revival requires a fresh user/child message per `instance_messaging.py:1486-1510`). No reaping — StaleTaskRecovery's age-gated backstop owns them. Documented in the module docstring. **Rejected:** revive-from-terminal at boot (injects an unsolicited turn into a dead instance — the exact behavior arm-notify refuses for dead arm-targets); pass-side dead-lettering (duplicates StaleTaskRecovery).

## D12. G8 — StaleTaskRecovery co-existence: orthogonal, no race, no changes

**Decision:** StaleTaskRecovery unchanged. It runs earlier (boot step 5c) and is a boot-time no-op (amnesty clamp short-circuits a young daemon, `repository.py:3155-3181`); it excludes PAUSED/TERMINATED ("recovery must not auto-resume such tasks"); its 60 s steady-state loop is age-gated at 10 min. The new pass is immediate-at-boot; StaleTaskRecovery is the miss-backstop. **Rejected:** any coupling (threshold flips, shared selectors) — blast-radius contamination.

## D13. AC9 — kill-switch: `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART`, env-direct, default ON, `=0` disables, per-boot read

**Decision:** Mirrors `ENSEMBLE_POST_RESTART_ARM_NOTIFY` exactly (constant + `os.environ.get(ENV, "1") != "0"` at the pass entry; NOT cached; no Pydantic field on `ServicesConfig`; no settings-router surface).

**Rationale:** same feature family (corrective restart-recovery), same empirical-gap motivation (32 frozen restarts), same operator escape-hatch semantics: flip env + restart → previous behavior (StaleTaskRecovery backstop) resumes. Default-ON because the current frozen behavior is the bug; R15 default-OFF is for opt-in rollouts (wrong requirement); typed-config is for intervals (explicitly "no kill-switch" HARD POLICY, `config.py:1633-1634`). **Rejected:** R15 metadata toggle (wrong surface: per-project storage + UI; wrong default; instant-flip is meaningless for a boot pass); default-OFF (delays the corrective behavior the user explicitly demanded).

## D14. AC3 — WAITING_CHILDREN skipped; `DependencyBus.start()` owns it; gate concept = bus count

**Decision:** The pass never re-drives WC parents (selection excludes them). The bus's boot re-arm (`_warm_cache` → `_recover_fired_unsent` with the `enqueued_at IS NULL` C1 dedup → `_sweep_orphan_watchers`, `dependency_bus.py:1499-1560`) covers both child-completed-before-death and child-completes-after-reboot orderings. Authoritative gate for any WC reasoning is `bus.count_pending_for_target_sync` (`dependency_bus.py:1069`) — the `instances.status='waiting_children'` string is cosmetic/deprecated. E2E must PROVE the parent wake (phase 5, task 5.5). RAM-only `_parent_error_message` (`dependency_bus.py:466-472`) = follow-up ticket, out of scope, happy path unaffected.

**Rejected alternative:** pass-side WC handling — races `_recover_fired_unsent`/`_sweep_orphan_watchers`, no semantic gain.

## D15. AC7 — PACKS.md is tester-owned; pack SPECS registered in-plan, PACKS.md edit deferred

**Decision:** The implementation lane creates the pack wrapper scripts; `.agents/tester/PACKS.md` rows are added by the tester lane from the SPECs in test-strategy.md. Full-dir gates + cascade e2e per the 2026-10-03 precedent (the v0.13.10 lesson: targeted packs missed a full-dir red).

**Rationale:** role boundary (PACKS.md is the tester's gate registry); the precedent failure mode was under-scoped gates, which the full-dir requirement addresses directly.

## D16. Route-outcome telemetry value `"boot_continue"`

**Decision:** New `route_outcome` value on the `_schedule_explicit_handle_resume` call (existing values: `answer_gate_existing_turn`, `report_or_external_resume` at `manager.py:10616,10657`). Free-form structured string flows to logs; no registry change required (verified: the parameter is a plain string kwarg).

**Rationale:** one grep answers "what did the boot pass continue" in production logs; consistent with the existing telemetry convention.

## D17. No new index on `auto_continued_at`

**Decision:** No index. Selection is gated on `status='running'` (served by `idx_task_status_type_created`); stamp CAS is PK-scoped. Revisit only if boot-pass profiling at N>100 shows need.

---

## D18. Δ1 (architect) — Success-path orphan terminalizer: ADOPTED (leader ratification 2026-10-04, NOT Option C)

**Decision:** In `_resume_processing_background`'s success branch (verified at `manager.py:11666-11697`), after `_process_resume_finalize`, call `complete_task` by `work_id`. The repository's `WHERE status='running'` guard (`repository.py:2803` / `complete_task` docstring + `~:2948` for `fail_task`) makes this a **surgical no-op for cascade/worker shapes** (cascade rows are PENDING by the time a resume finishes; worker shapes never reach this branch directly). It terminalizes **exactly the direct-resume orphan row** that the plan under-modeled.

**Rationale (architect-verified):** `manager.py:11666-11697` is the comment block that REMOVED `complete_task` from the resume path and assigned lifecycle to "the WorkerPool re-claim path", whose documented lifecycle **presupposes a PAUSED→PENDING entry** that a crash-orphan never reaches. Without Δ1, the success path leaves the orphan `status='running'` indefinitely — the claim-guard stays closed → the pending wake cannot claim at turn completion (AC4's FIFO mechanism never fires); `has_instance_busy` stays True (blocks `job_continue`); F10 drift repair can't help (needs a terminal JobItem, `job_recovery_service.py:961-966`). STR becomes the de-facto terminalizer at boot+10 min — with `retry_count` burn (contradicts D6) and ~10-min wake delay. The `WHERE status='running'` guard makes the terminalizer safe to add to a shared resume path: cascade rows are PENDING by then (lifecycle doc at `:11666-11697`); worker shapes go through `claim_pending_task` not this path. Status guard MUST be asserted in tests (M18).

**Rejected alternative (Option C):** Accept STR-mediated success-path recovery, rewrite AC4/D6 language to say so. Rejected because (i) ~10-min wake delay contradicts AC4's deterministic-FIFO framing, (ii) `retry_count` burn on success contradicts D6, (iii) the `WHERE status='running'` guard is a one-line surgical fix — no need to accept the workaround.

**Source:** architecture-recommendation.md Focus 3 (Δ1) + leader ratification 2026-10-04.

## D19. Δ2 (architect) — `boot_epoch=None` → SKIP pass with WARNING (NOT fallback to aware-datetime)

**Decision:** On `boot_epoch is None`, the pass **SKIPS** with a WARNING log (no DB writes, no scheduling). The pre-amnesty clamp (STR uses `boot_epoch` to short-circuit a young daemon, `repository.py:3161, :4501`) vanishes when `boot_epoch` is None, and the just-started 60s STR loop may reap a >10-min-stale orphan mid-pass → resume scheduled against a CANCELLED work_id → two *sequential* drivers (ExecutionGate serializes) = one wasted duplicate continuation. SKIP avoids the race; the orphan stays `status='running'`, STR's normal age-gated backstop continues to own it (next boot retries, or STR reaps at +10 min as designed).

**Rationale:** The plan's T2.2 step (2) fallback `datetime.now(timezone.utc)` is timezone-**aware** while the entire comparison frame is naive-UTC (`_to_naive_utc` `boot_epoch.py:88,114,136`; `now_utc_naive()` `timestamps.py:51`; `_default_created_at_naive_utc` `models.py:273`; amnesty comment `repository.py:3155-3156`). An aware stamp silently corrupts epoch comparisons on SQLite (lexicographic ISO with `+00:00`) and errors on PG TIMESTAMP. If any fallback timestamp is ever retained anywhere in the pass, it MUST be `now_utc_naive()` — but the cleanest fix is to NOT have a fallback at all (epoch-None is a DB-clock capture failure; the pass must not pretend to know what time it is). M19 pins SKIP + WARNING, no fallback.

**Rejected alternative:** fallback to `datetime.now(timezone.utc)` (aware). Rejected because of the frame mismatch and the residual race. UTC fallback with frame stripping (`.replace(tzinfo=None)`) — also rejected, hides the capture failure and adds a code path that future maintainers will not understand (epoch-None is an exceptional condition that deserves a WARNING, not a silent default).

**Source:** architecture-recommendation.md Focus 1 (Δ2) + leader ratification 2026-10-04.

## D20. Δ7 (architect) — Dedicated worktree MANDATE for implementation + E2E lanes

**Decision:** The implementation lane and the demo-E2E lane run in a dedicated worktree at `../ensemble-src-wt-auto-continue` with:
1. **A fresh uv venv inside the worktree**, gated by `python -c "import daemon; print(daemon.__file__)"` resolving INSIDE the worktree before any test run — the editable-install trap (a venv inherited from the main checkout resolves `daemon` to the main checkout path, silently testing the wrong tree; verified, see blueprint: "Repo & Dev Environment Conventions → Worktree editable-install trap").
2. **Explicit env exports** for any daemon run from the worktree (prod-defaults trap, `agents/developer/rule.md:164` — "Assigned wt_path? cd into the worktree before any git op; **never commit on the main checkout**"). DB pinning must override a `POSTGRES_*` **part** (e.g. `POSTGRES_PORT`), not `POSTGRES_URL` — the checkpointer honors `POSTGRES_URL` but repositories read `POSTGRES_*` parts only (F-DR1-2 split-brain, `daemon/persistence.py:79-89` vs `daemon/repositories/factory.py:189-198`); a part override moves BOTH.
3. The demo E2E (port 7979, `~/agents-ensemble-demo/`) itself targets the demo install, not the worktree — evidence bundle captured under the worktree's plan dir.

**Rationale:** `agents/giter/workflow.md:77-83` (Worktree Mode) — sibling worktrees live at `../<repo>-wt-<slug>/` with a KV claim protocol; the worktree MUST be created BEFORE Phase 1 starts (otherwise Phase 1 commits land on the shared main checkout — exactly the hazard the worktree exists to prevent). Phase 0 (NEW) is the worktree+venv gate; P1-P5 all assume it has passed. The plan's existing refs (P1-P5 task numbers, AC table) are unchanged; P0 is a precondition phase that does not renumber them.

**Rejected alternative:** work on the shared main checkout. Rejected because concurrent commissions switch the shared checkout's branch mid-flight (the hazard that motivated this focus area; observed residue already present in the working tree, see architecture-recommendation.md Focus 6).

**Source:** architecture-recommendation.md Focus 6 (Δ7) + leader ratification 2026-10-04.

## D21. Δ4 (architect) — Selection hardening: full exclusion set + `cancel_requested=False` + >1-candidate log-skip

**Decision:** `find_auto_continue_candidates` predicate is **explicit** on the complete instance-status exclusion set — `instances.status NOT IN ('paused','terminated','completed','error','failed','waiting_children')` — AND on `task.cancel_requested = False` (verified field at `models.py:227`, boolean type with default `False`, used by `find_cancellable_tasks` at `:4470-4520`). On an instance with **>1 RUNNING task candidates**, the pass **logs WARNING with both task_ids and SKIPS the instance** (no resume). This is **NOT** the count-then-select raising pattern from `find_paused_or_cancellable_turn` (`:825-832`, which RAISES on >1 and would abort the boot for unrelated candidates) — a single SELECT + a count check is the correct shape (the invariant is convention enforced by the claim-guard SQL, not a DB constraint; bug residue could violate it, and the pass must defensively skip).

**Rationale (architect-verified):** `cancel_requested=False` prevents the pass from double-picking a task that an in-flight STR cancel has already targeted. The full exclusion set is explicit (not "PAUSED/terminal/WC") to remove ambiguity at code-review time and to match the exclusion subquery in `find_cancellable_tasks` (`:4470-4520`, verified). The >1-candidate log-skip is conservative: re-claiming an already-claimed task would corrupt the graph (the guard-release mechanism = `fail_task` for failure + D18 terminalizer for success — both write a single terminal status; a >1-candidate instance means the invariant is broken and we must NOT pick a continuation for any of them).

**Rejected alternative:** resume-newest on >1 (some picks could be wrong); raise on >1 (aborts boot for unrelated candidates); widen the predicate (scope creep).

**Source:** architecture-recommendation.md Focus 7 (Δ4) + leader ratification 2026-10-04.

## D22. Δ5 (architect) — Stagger 5 resumes / 2s + pass metrics

**Decision:** Per-candidate scheduling is staggered: after every 5 resumes scheduled, `await asyncio.sleep(2)` (one cadence). This bounds the boot-time LLM-stampede cost (worst observed N=33 → ~14 s total to schedule all; well inside the lifespan startup window). Add pass metrics: counter `auto_continue_boot_pass_resumes_total{route_outcome="boot_continue"}` + histogram `auto_continue_boot_pass_duration_seconds` (observed duration; ignore suspend/resume tail). Loop stays sequential in terms of *probe* ordering (the per-candidate loop body remains single-flight, no `asyncio.gather` over checkpoint probes — D10 holds); only the SCHEDULING cadence is staggered.

**Rationale (architect-verified):** ExecutionGate is per-instance only (`execution_gate.py:108-150`); `WORKER_POOL_SIZE=5` / `CHAT_WORKER_POOL_SIZE=2` (`constants.py:70/:78`) bound **claim** pools, not graph execution; GII throttle is per-turn (`graph.py:65-72`); LLM failover is per-turn. The only real cross-instance bound is the supervisor proxy's RPM/TPM. Worst observed N=33 → 33 concurrent `astream` LLM calls within the first seconds of boot; cost = N × P99 first-token latency added to boot completion + N × token spend; 429s trip the failover path. Stagger at 5/2s bounds both: ~14 s total at N=33; metric gives empirical grounding to revisit cadence. Flip condition (from architect, leader-ratified): if the proxy is shown to absorb 33 concurrent calls without 429s, drop the stagger and keep the metrics — i.e. metrics are permanent, cadence is revisable.

**Rejected alternative:** unbounded v1 (architect verdict: high risk of LLM 429s at observed N; the cadence is the cheap mitigation); semaphore over execution (requires touching private `_resume_processing_background` — blast radius contamination, no ExecutionGate coupling, and a scheduling-bound semaphore is a no-op for execution).

**Source:** architecture-recommendation.md Focus 7 (Δ5) + leader ratification 2026-10-04.

## D23. Δ6 (architect) — Anchor re-pins (verified at ce148ad2)

**Decision:** All execution-lane citations in this plan re-pinned against the live tree at `ce148ad2` and re-verified by the planner before writing. Verified anchors:

| Plan-cited | Live-tree anchor | Verifier |
|---|---|---|
| `claim_pending_task` `:1733+` | `repository.py:1934` (function def) | grep `^    def claim_pending_task` |
| Claim-guard `:2230-2294` | `repository.py:2230-2310` (the `AND instance_id NOT IN (...)` block plus the S3-invariant comment through `:2310`) | grep `instance_id NOT IN` |
| Bus `start()` `:1499-1560` | `dependency_bus.py:1499` (`start` def); helpers at `:1804` (`_warm_cache`), `:1839` (`_recover_fired_unsent`), `:1896` (`_sweep_orphan_watchers`) | grep `def start\b\|async def _warm_cache\|async def _recover_fired_unsent\|async def _sweep_orphan_watchers` |
| STR "boot step 5c" | `pool_orchestrator.py:264-294` (the `StaleTaskRecovery(...)` constructor + `recover_on_startup()` + `.start()` block — the thread starts here, **earliest** of all boot subsystems) | grep `StaleTaskRecovery` |
| Amnesty `:3155-3162` | `repository.py:3161` (the `if boot_epoch is not None and boot_epoch >= threshold: return []` line in `find_stale_running_tasks`); `:4501` (same in `find_cancellable_tasks`) | grep `boot_epoch` |
| Resume success branch `:10915` | `manager.py:10915` (`_schedule_explicit_handle_resume`); `:11445` (`_resume_processing_background`); `:11666-11697` (the orphan-success-branch docstring — **Δ1 surgical site**) | grep `_schedule_explicit_handle_resume\|_resume_processing_background` |
| Resume failure branch `:11735-11746` | `manager.py` failure handler runs `fail_task` by work_id (architect cite) — verified to match `repository.py:2932` (`fail_task` def) | grep `fail_task` |
| `complete_task` guard `:2803, :2948` | `repository.py:2803` (`complete_task` def), `:2932` (`fail_task` def); `complete_task` returns `None` when the `WHERE status='running'` guard declines | grep `def complete_task\|def fail_task` |
| SQLite second-granularity epoch | `CURRENT_TIMESTAMP` in SQLite returns second-precision (verified); PG `now()` returns microsecond — same-second double boot shares an epoch → second boot skips → safe (STR owns) | architecture-recommendation.md Focus 1 |
| `_resume_processing_background` `:11479` | `manager.py:11479` is the docstring opener (function def at `:11445`); cite the def line for consistency | grep `async def _resume_processing_background` |

**Rationale:** Anchor drift has caused multiple pre-existing incidents (critical note: lane-site citation drift). Every plan citation must grep-verify at use time. This D23 addendum is the snapshot of the verification pass; future use requires re-verification at the current HEAD.

**Rejected alternative:** carry the original `:1733+` / `:2230-2294` / "boot step 5c" / `:10915` etc. anchors unchanged. Rejected because they are provably wrong against `ce148ad2`.

**Source:** architecture-recommendation.md Focus 1 + Focus 2 + Focus 7 (Δ6 + Δ7 evidence), re-verified by planner before write.

## D24. Δ3 (architect) — Document the no-heartbeat window + optional `last_heartbeat_at` stamp

**Decision:** Document in the module docstring of `auto_continue_boot_pass.py` (and in the AC4/AC5 narrative): **continued turns are STR-reapable at boot+10 min** because the resume path (`_resume_processing_background`, `manager.py:11445`) does not write heartbeats (heartbeat writers are per-worker-thread, `worker_pool.py:61-97`; zero heartbeat writes on the manager resume path — architect-verified). STR reap = checkpoint-continuation retry (safe; `astream(None)` from advanced checkpoint is idempotent). `retry_count` burn applies to >10-min turns — this is an **explicit D6 exception**: a successful continued turn that's then STR-reaped at +10 min IS a retry-budget consumer; the >10-min turn is, by definition, a long-turn that the prior boot was attempting. The no-heartbeat window also means `has_instance_busy` stays True until the terminalizer (D18) fires OR until STR reaps.

**Optional mitigation — `last_heartbeat_at` stamp SHOULD-include only if it rides the existing CAS transaction cheaply (leader ratification):** extend `mark_task_auto_continued` to ALSO stamp `last_heartbeat_at = :boot_epoch` in the same `UPDATE … WHERE … AND status='running'` (same atomic CAS — no second statement, no second transaction). This buys exactly one threshold window of immunity (heartbeat is fresh at boot, so STR's `COALESCE(last_heartbeat_at, started_at) < threshold` predicate excludes it for one threshold). If the heartbeat stamp is added later, a follow-up D-addendum records the implementation. As of `6303ba44`, the SHOULD-include decision is **DEFERRED**: implementation lane may adopt if the developer verifies the single-statement extension is truly zero-cost on both drivers (SQLite + PG).

**Escalation flip-condition (architect, leader-ratified):** if the demo E2E or LIVE observation shows **continued turns routinely exceed 10 min**, the heartbeat stamp escalates from 🟡 (document-only) to 🔴 (mandatory), and the stale-reap mid-turn behavior (R20) becomes the dominant failure mode → the heartbeat stamp becomes mandatory. Phase 5 (5.8, NEW) records the observation in the evidence file.

**Rationale:** The plan's R4/R1 narrative ("a continued turn is either terminal or genuinely stuck") is **false for long turns** (architect-verified). Documenting the window is the minimum that ships honest behavior; the optional stamp is the cheap one-statement mitigation.

**Rejected alternative:** write heartbeats from `_resume_processing_background` directly (requires reaching into worker-thread-only writer logic — blast radius contamination, threading concerns); drop the STR reaping for continued turns (changes STR blast radius for unrelated paths); use `is_deferred`/status-only liveness signals (does not match STR's actual predicate).

**Source:** architecture-recommendation.md Focus 3 (Δ3) + leader ratification 2026-10-04.

---

## Decision-to-AC index (updated)

D1→AC1/AC2 · D2→AC5 · D3→AC4/AC6 · D4→AC4/AC5 · D5→AC5 · D6→AC5 (with D24 explicit-exception for >10-min turns) · D7→AC1 · D8→(gate interaction, no AC) · D9→AC7 (terminal-token trap) · D10→(scalability) · D11→AC1 · D12→AC5/AC6 · D13→AC9 · D14→AC3 · D15→AC7 · D16→(observability) · D17→(perf) · D18→AC4/AC5 (success-path terminalizer — extends AC4/AC5 coverage to the success path) · D19→AC6 (epoch-None skip — extends AC6 never-wedge to the capture-failure case) · D20→AC7 (worktree precondition — gate for all execution-lane work) · D21→AC1/AC5 (selection hardening — strengthens AC1 carve-outs, prevents double-pick) · D22→AC2/AC6 (stagger + metrics — bounds LLM-stampede cost, observability) · D23→(anchor hygiene — D3/D12/R4/R11 carry the verified numbers) · D24→AC4/AC5 (no-heartbeat window documented; optional heartbeat stamp; escalation flip-condition).
