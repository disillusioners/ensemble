# Phase 1 — Pattern-(h) Implementation

## 1. Objective

Implement the decided design: the `discount_instance_id` guard parameter, the pattern-(h) leg
(predicate + decision chain + write + notify), the three provenance tokens, the knob/kill-switch
trio with boot log, the observability surface, and the api.py call-site wiring. The Phase-0
fixture closes at HEAD; every existing E1 caller is untouched by default.

Design references: technical-analysis §3 (shape), §4 (liveness design), §5 (finalize semantics),
§6 (write seam), §7 (watcher handling), §8 (idempotency), §9 (audit), §10 (knobs); decisions
D-1..D-7, D-10..D-12.

## 2. Prerequisites

Phase 0 complete (AC-0.1..AC-0.4); fixture shape available for the smoke; fences standing.

## 3. Decisions

| ID | Decision | Rationale (cite) | Rejected alternatives |
|----|----------|------------------|------------------------|
| PD-1.1 | `discount_instance_id: str \| None = None` added to `evaluate_mission_live`; discount conjunct `and not (discount_instance_id and tree_id == discount_instance_id)` at the TWO sites already carrying `skip_root` (`mission_live_guard.py` ~:896 and ~:982); docstring notes the generalization of `skip_root` | D-2; technical-analysis §4 — strict generalization of the existing pre-stamp discount; default None = zero behavioral change for every existing caller | A separate wrapper function (duplicates the walk); mutating `skip_root` semantics (breaks strand-healer/child_reports callers) |
| PD-1.2 | Age key = `MAX(completed_at)` over the instance's Task rows — `completed_at` is the terminal finalize stamp set by `complete_task` / `fail_task` / cancel paths (D1 timing semantics, `task/repository.py:701-718` docstring), so ONE column covers all three verdicts; batch-fetch via the existing per-work_id helper shape | D-2 monotone requirement; technical-analysis §4 leg 2 ("task terminal stamps, NOT last_activity_at"); verified in-source this lane | COALESCE chains over updated_at fallbacks (unnecessary — completed_at is set on ALL terminal paths); `last_activity_at` (explicitly rejected: enqueue-refreshable, frozen-by-design for stalled instances) |
| PD-1.3 | Candidate selection = ONE pre-filtered SQL round-trip per drift pass: `job_type='task' AND admission_state='active' AND deleted_at IS NULL` + JOIN task-on-work_id (terminal) + NOT EXISTS instance-scoped non-terminal task + age filter + `LIMIT batch_cap`; repository method; per-candidate E1 off-loop; remainder next tick | D-12; technical-analysis §10 (batch-cap semantics); R4 mitigation (loop-latency class) | Per-candidate probing in Python loops (round-trip explosion — the v0.27.14 incident class) |
| PD-1.4 | The (h) write calls `JobRepository.force_finalize_orphan(job_id, terminal_reason=token)` VERBATIM — no new writer, no alias unless review demands readability; census unchanged (23 writers) | D-4; technical-analysis §6 — the census is frozen; the F5 shape is trigger-suite-proven and mutation-proven | A new repository writer (constitution churn); direct SQL in the service layer (violates the typed-method sweep rule, findings-riders §2) |
| PD-1.5 | Fail-CLOSED polarity: `live=False` with `error or timed_out` ⇒ SKIP + `error_skips` counter, next cycle retries | D-2.3; technical-analysis §4 error-polarity section — deliberate divergence from the f-arms' fail-open WARN (last-resort backstop, hours-wide age bound) | Copying the f-arms' fail-open (would terminalize on unverifiable state) |
| PD-1.6 | Knobs are three `ServicesConfig` Fields (`drift_reconcile_zombie_close_enabled/_age_bound_seconds/_batch_cap`, defaults True/7200/25, `ge=1` FAIL FAST), wired through the api.py drift block → loop kwargs → `reconcile_drift_states` args, exactly like `f1_tree_activity_max_age_seconds` (`api.py:1446-1487`); one-shot boot INFO log in the `emit_orphan_f1_boot_log` / `emit_defer_autopromote_boot_log` precedent | D-6; findings-riders §5 template guidance (modern ServicesConfig shape over the legacy env-only F1 resolver) | Env-only module-global resolver (the legacy F1 pattern — explicitly not the one to copy) |

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|---------------|----|
| T1.1 | Add `discount_instance_id` param + conjuncts at the two `skip_root` sites + docstring | Param present; default None ⇒ existing verdicts byte-identical (pin lands Phase 3); discounted evaluation excludes exactly the named instance from the non-terminal count | `daemon/services/mission_live_guard.py` (signature ~:327-360; conjunct sites ~:896, ~:982) | — | R2 | AC-1.1 |
| T1.2 | Implement the candidate query as a repository method (PD-1.3): task-kind + active + not-deleted + work_id-task terminal JOIN + instance inflight NOT EXISTS (`job_recovery_service.py:4779` shape) + `MAX(completed_at)` age filter + **deterministic ORDER BY per S5 (reviewer council): oldest-terminal-first `MAX(task terminal stamp) ASC` with `job_id` as tiebreak — `LIMIT batch_cap` without ORDER BY is non-deterministic across ticks (starvation/overflow possible)** + LIMIT | One SQL round-trip returns only age-qualified zombie candidates **in a stable oldest-first order (S5)**; healthy trees return empty (their non-terminal task rows fail the NOT EXISTS / terminal JOIN) | `daemon/repositories/job_queue/repository.py` (new method; stamps via `daemon/repositories/task/repository.py` batch helper shape :701-718) | — | R1, R4 | AC-1.2 |
| T1.3 | Implement `_pattern_h_zombie_active_close` leg inside `reconcile_drift_states`: kill-switch gate → candidate fetch (batch-capped) → per-candidate decision chain [bus gate (`_pattern_f_check_bus_pending` :4636 precedent; bus-unavailable ⇒ fail-safe skip) → discounted E1 (`discount_instance_id=job.instance_id`, `bus_pending_count=0`) → live=True ⇒ HOLD token; live=False clean ⇒ write; error/timeout ⇒ SKIP (PD-1.5)] → write via `force_finalize_orphan` with the task-verdict token → notify via `JobQueueService.notify_watchers` — **the NOTIFY HALF ONLY of the f2 helper composition (`job_recovery_service.py:4103-4234`; A7 as corrected by reviewer W3: the helper's write half releases the job lock in a SEPARATE step 2 (`:4188-4235` — its own crash window between finalize and release), whereas the (h) write seam `force_finalize_orphan` pairs finalize+release in ONE transaction (`repository.py:4294-4347`) — reuse the notify composition, never the helper's write path)** → **W1 notify-status fidelity: the notify STATUS is the canonical task-verdict string via `canonicalize_status(token)` — NEVER the raw `zombie_active_*` token (not in `ALL_TERMINAL_STATES` = `["completed","settled","failed","cancelled","dead_letter"]`, `watcher_models.py:13`, so the terminal-cleanup branch at `job_queue_service.py:506-509` would misroute/never clean watcher rows) and NEVER the f2-literal `'completed'` (hardcoded in the helper at `:4103-4234` — would mislabel failed/cancelled verdicts); the status feeds bucket classification + `[JOB_EVENT]` payload (`job_queue_service.py:377`, `work_notifier.py:346-380`)** → terminal watcher cleanup rides the notify path (`job_queue_service.py:506-509`) → the (h) close log line carries `notify=<outcome>` (A3: the outcome is recorded even if the notify leg is lost post-commit) | The Phase-0 fixture closes within one drift cycle of eligibility: admission DONE + token + same-txn lock release; every skip path emits its reason token; every close line carries `notify=`; **the failed-verdict fixture notifies status='failed' (canonical), not 'completed' and not the raw token** | `daemon/services/job_recovery_service.py` (new leg method + call in the drift pass; namespace `reconcile_drift_states: Pattern (h) zombie-active close`) | T1.1, T1.2 | R1, R3, R5, R6 | AC-1.3 |
| T1.4 | Register the three tokens in `_STATUS_CANONICAL_MAP` (`zombie_active_completed→completed`, `zombie_active_failed→failed`, `zombie_active_cancelled→cancelled`); NO `_TERMINAL_STATUSES` change; verify `_CANONICAL_TO_SOURCES` auto-derivation picks them up (`terminal_reason_variants_for`); **A4 disclosure folded: NEVER stamp `failed_at` on heal** — JobRetryEngine reads `failed_at` not `terminal_reason` (`job_retry_engine.py:183, 287`), so healed `zombie_active_failed` rows foreclose `atomic_retry` BY DESIGN (re-arming 2h+-stale work is the harm the omission prevents); **A5 folded: attribution/forensics queries use `terminal_reason IN ('zombie_active_completed','zombie_active_failed','zombie_active_cancelled')`, never prefix-LIKE** (PG btree prefix-blind without `text_pattern_ops`) | Canonical status derives truthfully from each token; `is_terminal` unchanged; tokens query-attributable via `idx_job_queue_terminal_reason` (IN-list form); no `failed_at` write anywhere in the leg | `daemon/services/work_status.py` (map :67-142; reverse index :181-205) | — | R8 | AC-1.4 |
| T1.5 | Add the knob trio to `ServicesConfig` (PD-1.6 defaults/ge-constraints/descriptions, env `SERVICES_DRIFT_RECONCILE_ZOMBIE_CLOSE_*`) + extend `reconcile_drift_states` signature with the three kwargs + wire `api.py` drift block reads → loop kwargs (`_periodic_drift_reconcile_loop` call, `api.py:1477-1487`) + one-shot boot INFO log | Knobs read from config at boot; OFF ⇒ COMPLETE no-op (no candidate query, no probe); out-of-range FAILS FAST at boot; boot log line present | `daemon/config.py` (ServicesConfig, near the drift family :2008-2270 / strand-heal template :2645-2695); `daemon/api.py` (:1446-1487 block); `daemon/services/job_recovery_service.py` (signature :925+) | T1.3 | R1 | AC-1.5 |
| T1.6 | Observability: fold (h) counters into the drift stats dict — `examined / age_qualified / mission_live_holds / error_skips / finalized / race_lost_noop / zero_watcher_warns`; skip reason tokens (`zombie_close_skipped_mission_live / _age_unmet / _bus_pending / _guard_error / _disabled`); the examined-candidates line doubles as the class's P1-style visibility line (today the class emits ZERO warnings, findings-core Q6); the (h) log line references the PP1 zero-watcher token family for forensics correlation (§7). **A1 folded (architect §6/§10): the (h) counter block logs whenever `examined>0` — NOT gated on the loop's `reconciled>0` condition (`api.py:3942-3948`; distinct boot-time twin at `api.py:2456` — cite corrected per reviewer A3); non-zero `error_skips` surfaces in the drift-stats WARN line; an escalating WARN fires when the same job accumulates ≥K≈40 consecutive error_skips/mission_live_holds (~3.3h @300s). CF-3 folded (reviewer council): the streak state is a SERVICE-LEVEL DICT on `JobRecoveryService` (strand-healer precedent `child_reports.py:292` `_strand_heal_inflight` — service-level in-memory holder), keyed `job_id → consecutive skip/hold count`, reset-on-close (successful finalize clears), evict-when-candidate-disappears (no longer a candidate ⇒ drop); restart-reset acceptable, disclosed (in-memory, no persistence) — stuck-closed visibility is operational, not just forensic** | Stats appear in the drift pass log whenever ANY candidate was examined; every skip attributable by token; the class is VISIBLE pre-close; repeated holds/skips escalate; streak entries reset on close and evict on candidate disappearance | `daemon/services/job_recovery_service.py` (leg + stats fold + streak dict); `daemon/api.py` (loop log condition for the (h) block, `:3942-3948` neighborhood) | T1.3 | R6 | AC-1.6 |
| T1.7 | Smoke at HEAD with the Phase-0 fixture (same fences): fixture closes; E1 self-hold verdict flips to `live=False` under `discount_instance_id=<instance>`; lock row gone; token present; kill-switch OFF rerun ⇒ no close (pre-fix behavior) | HEAD-side evidence note mirroring AC-0.3 | evidence doc (executor-recorded) | T1.3, T1.5 | — | AC-1.7 |
| T1.8 | Migration-delta invariant check: `git diff --stat` over `daemon/migrations/versions/` at phase exit = EMPTY; `git status` shows NO new `*.sql`; confirm NO new SQLModel model/table was introduced anywhere in the diff | Working tree carries zero schema surface (Phase 5 re-verifies at stage) | repo-wide diff check | T1.1-T1.6 | — | AC-1.8 |
| T1.9 | **A2 probe/commit TOCTOU closure (architect §10 posture, parameterized per reviewer council W2):** a retry child minted between the candidate inflight probe and the finalize UPDATE is not re-checked inside the transaction. **The seam has a LIVE SECOND CALLER — `cleanup_non_terminal_jobs` orphan reaper (`job_queue_service.py:2180`)** — so the closure is a PARAMETER: `force_finalize_orphan(..., recheck_instance_inflight: bool = False)` — existing callers byte-identical by default; the (h) leg ALWAYS passes True (re-run the instance inflight probe, one SELECT, inside the same `engine.begin()` before the guarded UPDATE; probe fails ⇒ abort the txn, skip, next cycle). FALLBACK RULE: pin+disclose ONLY on demonstrated contract disturbance during implementation, and any fallback disclosure MUST name the **RETRY-REARM ORPHAN CORNER explicitly** (the one non-consistent terminal ordering — a re-arm racing the finalize — bounded by the 7200s gate), never softened to "shared window" | Parameter present, default False, (h) always True; either the in-txn re-check is in place (probe visible in the same transaction) or the pin+disclose posture is recorded with the retry-rearm-orphan-corner disclosure in the lane evidence — no silent TOCTOU, no softened wording | `daemon/repositories/job_queue/repository.py` (:4288-4347, parameterized) and/or `tests/job_queue/` (pin option, lands Phase 3 Pack J) | T1.3 | R1, R3 | AC-1.9 |

## 5. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC-1.1 | `evaluate_mission_live(discount_instance_id=None)` — every existing call site's verdict byte-identical (pin in Phase 3; static + behavioral) |
| AC-1.2 | Candidate query returns ONLY age-qualified zombie candidates in ONE round-trip; batch cap applied; remainder re-appears next tick |
| AC-1.3 | Decision chain order + polarity exactly per D-2: zombie predicate ∧ age ∧ bus_pending==0 ∧ discounted-E1-live=False-clean; ANY unprovable leg ⇒ skip with reason token; write + notify via the census seam |
| AC-1.4 | Three tokens registered; canonical statuses truthful; `is_terminal`/status-filter surfaces unchanged |
| AC-1.5 | Knobs wired (config → kwargs → reconciler); FAIL FAST on out-of-range; OFF ⇒ complete no-op; boot INFO logged once |
| AC-1.6 | Counters + skip tokens in the drift stats; class visible pre-close |
| AC-1.7 | Phase-0 fixture closes at HEAD within one cycle of eligibility; OFF rerun leaves it untouched |
| AC-1.8 | Zero migration/schema surface in the phase diff (the D-5 invariant) |
| AC-1.9 | A2 TOCTOU dispositioned per the architect posture: in-txn inflight re-check inside `force_finalize_orphan` OR pin+disclose with the residual window recorded — never silent |

## 6. Verification method

T1.7 is the phase gate (fixture close + OFF rerun + E1 verdict flip). Code-level verification is
deferred to Phase 3's mutation-verified pins — this phase ships code + smoke evidence only.
Round-trip count for the candidate fetch reviewed against the D-12 off-loop discipline (R4).

## 7. Risks & flip-cost contingencies (D-1/D-3/D-6 RATIFIED @ `9657be93a` — contingencies retained as priced flip paths per architect §12)

> The architect CONFIRMED all four previously-held-open decisions (D-1 3.95/5 · D-3 tokens,
> pass-through provably harmful · D-6 default-ON 4.15 vs 3.20 · D-8 rider 4.35). The
> contingencies below are retained verbatim as the priced flip paths for the two NON-BLOCKING
> leader-ratification notes (D-6 posture; D-8 rider scope) — they are history + cost records,
> not open questions.

- **D-1 flip (dedicated sweep service instead of the (h) leg):** T1.3's leg method moves into a
  new service file + a new api.py boot block (asyncio-task pattern, `api.py:1709-1751` precedent)
  + shutdown path; T1.5's kwargs wiring becomes constructor args. +1 wiring task, same knobs, same
  predicate/write/notify bodies, same Phase-3 pack semantics. No schedule redesign — roughly one
  extra implementation + review cycle.
- **D-3 flip (pass-through-the-task-reason instead of three new tokens):** foreclosed by the
  architect's proof of harm (`work_status.py:401-416` fall-through would misreport failed/cancelled
  as completed; `work_resolver.py:1765-1770` would leak the raw token) — retained as a priced
  record only. Mechanically it would mean: T1.4 drops entirely
  (no `_STATUS_CANONICAL_MAP` entries); attribution moves to the (h) log line + counters only;
  Phase 3's token pins re-point to pass-through assertions; the `terminal_reason IN
  ('zombie_active_completed','zombie_active_failed','zombie_active_cancelled')` forensics query
  is lost (disclose in the handoff). Smaller diff, weaker attribution, provably harmful reads.
- **D-6 flip (default-OFF first release):** T1.5's `enabled` default flips to `False`; the boot
  log states the opt-in env; Phase 3's kill-switch pins invert posture (OFF-by-default no-op +
  operator opt-in close test — the fixture test sets the env ON); AC-1.7 unchanged in substance.
  One-line code change + pin re-point.
- **R5 note:** if the real-PG trigger leg (Phase 3, pack I) ever rejects the close, the escape is
  NOT a trigger bypass — route back to design review (`force_finalize_orphan`'s docstring already
  argues trigger compliance, `repository.py:4237-4257`).
