# Phase 1: Fix Bugs (Tier P1) — Restore Operator Signal + Stop the Restart Storm

Date: 2026-08-24
Author: phase-plan worker (1 of 3) operating under `plan-creation` skill, dispatched by planner[v2]
Branch: `feature/schedule-review-improve` @ `46349698` (v0.11.0)
Plan-tier contract: `decisions.md §D4` — Phase-1 internal ordering is **INV-1 BEFORE INV-2 (sequential, same file)**, with **INV-3 dispatchable in parallel as a separate sub-slice** (source-layer orthogonal to job-queue).

Mandatory amendment authority: `decisions.md §D7.1–D7.7` (pinned by the leader; the canonical D7 section is being written in parallel). The INV-1, INV-2, INV-3, and quarantine amendments below explicitly apply the rulings in §D7.1, §D7.4, §D7.6, and §D7.7; the full §D7.1–D7.7 set is authoritative for this pass.
Parent plan: `plan-overview.md` (this directory). Research inventory: `research-findings.md` (line refs spot-verified 2026-08-24).

---

## Phase Header — Quoted Inventory

| INV | Tier | e2e-Gate | File / Lines (verbatim from research-findings.md) | Verdict |
|-----|------|----------|---------------------------------------------------|---------|
| INV-1 | P1 | Release gate | `daemon/services/job_processor.py:1263-1271` | LIVE (HIGH); silent `except Exception: pass` swallows `complete_job` + `_cleanup_in_progress_tracking` failures |
| INV-2 | P1 | Release gate | `daemon/services/job_processor.py:825-854` (W1 skip rationale) ↔ `857-997` (ACTIVE loop) ↔ `945-987` (re-spawn path) | LIVE (HIGH); orphan-recovery bypasses `start_job_atomic_with_lock`, can orphan `job_locks` rows / violate PG trigger `trg_job_locks_active_guard` |
| INV-3 | P1 | Core gate, **source pack only** | `daemon/sources/registry.py:648-649` (unconditional `backoff = 2.0`) ↔ `705-718` (reset gate measures time-since-last-success, not run duration) | LIVE (CRITICAL — INCIDENT 11:17–11:30 today); restart-storm fingerprint matches the sustained-DNS-outage pattern |

All three line refs were spot-verified against current source on 2026-08-24 (see Phase-Plan Worker contract item #3). The Phase 1 internal ordering is locked by `decisions.md §D4` and is **not re-opened here**.

---

## Objective

Eliminate three Tier-P1 live defects that suppress operator signal or cause resource exhaustion, with no behavioral change outside those defect sites, and with the test/coverage evidence required by `.agents/tester/rules/ensure.md` baked into the verification table.

Testable single-sentence: **after this phase lands, an unrecoverable job-processor failure is visible through rate-limited observation mode and, when `JOB_PROCESSOR_RAISE_ON_ERROR=true`, through the tightened outer failure path; message-job orphan recovery follows the W1-skip/startup-recovery contract; and source-adapter backoff resets only after a run lasting at least `success_threshold`.**

---

## Component Inventory

| File | INV | Why this file | Per-task modification scope |
|------|-----|---------------|------------------------------|
| `daemon/services/job_processor.py` | INV-1 | Inner `except Exception: pass` lives at lines 1263-1271 (post-`complete_job`+`_cleanup_in_progress_tracking`) | Add observation-mode, dual-cap ERROR logging around the post-complete cleanup, preserve cleanup in `try/finally`, and re-raise only when `JOB_PROCESSOR_RAISE_ON_ERROR=true` (default OFF). The outer `_process_loop` handler at lines 653-654 is tightened with a failure counter / DLQ marking so the optional re-raise has observable semantics. |
| `daemon/services/job_processor.py` | INV-1 | Targeted unit test path lives alongside the module | New: `tests/unit/services/test_job_processor_error_handler.py` — fixture-only coverage of default observation, kill-switch re-raise, `finally` cleanup, and outer-handler counter/DLQ semantics; no full queue re-instantiation |
| `daemon/services/job_processor.py` | INV-2 | W1 skip (825-854), ACTIVE loop (857-997), and the task-job re-spawn path (945-987) share this file | Keep the W1-skip behavior for `job_type == "message"`; add monitor counters and contract tests only. Do not acquire a message-job `job_locks` row: message JobItems are pure mirrors and their startup owner is `JobRecoveryService.recover_on_startup` (see `decisions.md §D7.4`). |
| `tests/unit/services/test_job_processor_orphan_recovery.py` | INV-2 | Regression test path required by `plan-overview.md` Success Criterion #3 | New file; mock queue + recovery service; assert monitor counters increment, the W1-skip fires for an ACTIVE message orphan, and `recover_on_startup → reset_active_to_queued` flips the row and releases the lock atomically. |
| `daemon/sources/registry.py` | INV-3 | Backoff reset at 648-649 (unconditional) and 705-718 (mis-gated) | Record `run_duration` at error-entry; gate both resets on `run_duration >= success_threshold`; clear `_run_start_time` only at supervisor exit; verify the value survives adapter/supervisor restart (see `decisions.md §D7.6`). |
| `tests/unit/sources/test_source_registry_backoff.py` | INV-3 | Clock-injected regression test path | New file; virtualize `time.monotonic`, cover 59.9s → no reset, 60.0s → reset, 0.0s → no reset, and fast-failing-start backoff growth without wall-clock waits. |

**Files NOT touched in Phase 1**: `daemon/repositories/task/repository.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/work_status.py`, `daemon/services/turn_transitions.py`, `daemon/sources/circuit_breaker.py`, `daemon/sources/rate_limiter.py`. Any temptation to touch them belongs to Phase 2.

---

## Sub-Slice Strategy (Parallel Dispatch)

Phase 1 fans into **two dispatchable sub-slices**:

| Sub-Slice | Worker Inputs | Sequence | Rationale |
|-----------|---------------|----------|-----------|
| **A. Job-Queue (INV-1 + INV-2)** | `job_processor.py` + new test file | **INV-1 first, then INV-2** (sequential within the same worker) | `decisions.md §D4`: same handler area; fixing the swallow first prevents the recovery fix's own errors from being silently re-suppressed. Both items are job-queue-tier and share `concurrency_atomic_unit_test` + `job_queue_unit_test` packs in their verification. |
| **B. Source-Adapters (INV-3)** | `sources/registry.py` + new test file | **Independent** — can run parallel to Sub-Slice A | INV-3 is source-layer orthogonal: zero overlap with `job_processor.py`. Independent pack (`sources_unit_test`) gates it. Phase-1 plan-overview coupling row explicitly marks INV-3 ⟂ INV-1+INV-2. |

**Dispatch outcome**: Sub-Slice A is one worker. Sub-Slice B is a second worker, runnable alongside Sub-Slice A or split across instances if the dispatcher (planner v2) chooses. Both sub-slices converge on a single release commit on `feature/schedule-review-improve`. **DO NOT** merge Sub-Slice B's commit before Sub-Slice A; merge order is irrelevant for INV-3 but the branch hygiene requirement in `plan-overview.md` Success Criterion #9 (single coherent merge commit per phase) is preserved by merging both sub-slices in one phase-close PR.

---

## Tasks

Sub-Slice A (Job-Queue — sequential within the slice per `decisions.md §D4`):

### Sub-Slice A, Task 1 (INV-1, part A — diagnostic + fix)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| A1 | Static check: confirm the target `except Exception: pass` is present exactly once in `job_processor.py:1263-1271`; separately record the sibling log-only blocks at lines 535 and 571 so they are not mistaken for this defect. | none | One target match before the edit; the two sibling sites are explicitly listed for Cycle-3. |
| A2 | Replace the target swallow with **observation mode** (default): preserve `_cleanup_in_progress_tracking(job.job_id)` in `try/finally`, log the inner exception with traceback at ERROR level, and apply a dual cap — per `job_id` over 30s plus a per-`JobProcessor` global cap of approximately 100/min. Re-raise only when `JOB_PROCESSOR_RAISE_ON_ERROR=true` (kill-switch; default OFF). | A1 | Target block has no `pass` or silent swallow; both cap windows, cleanup guarantee, default-off switch, and conditional re-raise are documented and covered by A5. |
| A2b | Tighten the OUTER handler at `_process_loop` (`job_processor.py:653-654`): on a propagated handler failure, increment a failure counter and perform DLQ marking (or the equivalent durable failure accounting) so `JOB_PROCESSOR_RAISE_ON_ERROR=true` has observable semantics rather than becoming log-and-continue. | A2 | The optional re-raise reaches the tightened handler; a fixture proves the counter/DLQ signal increments and cleanup is not lost. |
| A3 | Add a doc-block comment above the new block explaining the invariant: cleanup runs in `finally`; the default is rate-limited observation, not re-raise; and no `pass` clause remains. | A2, A2b | Comment is visible in the 1263-1271 region and names both caps plus the kill-switch default. |
| A4 | Verify `complete_job(FAILED)` is called with `error=str(e)` in both the success and failure paths of the inner block, independent of whether the optional re-raise is enabled. | A2, A2b | Both call-sites use `DemandState.FAILED` with `error=str(e)`; reviewer confirms the outer handler does not turn it into silent success. |

### Sub-Slice A, Task 2 (INV-1, part B — targeted unit test)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| A5 | Create `tests/unit/services/test_job_processor_error_handler.py`. Cover `test_complete_job_failure_in_error_handler_propagates` and the default observation path: mock `JobProcessor._queue_service.complete_job` to raise a synthetic `RuntimeError`; assert (a) cleanup always runs, (b) default mode logs within the dual cap without re-raising, (c) `JOB_PROCESSOR_RAISE_ON_ERROR=true` reaches the tightened outer handler, and (d) the counter/DLQ signal is observable. | A2, A2b | `pytest tests/unit/services/test_job_processor_error_handler.py --tb=short -q` returns the expected passing count for the matrix. |

### Sub-Slice A, Task 3 (INV-2, part A — primary path: monitor-only flag)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| A6 | Add a monitor-only module-level flag, e.g. `JOB_PROCESSOR_RECOVERY_LOCKS_MONITOR = False` (default), plus runtime counters for message-orphan recovery-path attempts and W1-skip dispatches. When the flag is False, do not change the production path; increment counters only when the message-orphan recovery path is reached. | none | Counters are present; default is OFF; no message-job dispatch or `job_locks` behavior is changed. |
| A7 | Wire the monitor counters to a rate-limited DEBUG log: when the `job_type == "message"` ACTIVE orphan reaches the W1-skip path, log that `spawn_instance_with_mcp` and `enqueue_message` were not dispatched and that startup recovery remains the owner. The log line MUST include `job_id`, `instance_id` (or the explicit missing value), `queue_name`, and `agent_id`. | A6 | Log format is stable, grep-discoverable, and proves the message-orphan path is observation-only. |
| A8 | Run a synthetic message-job workload (N=200 jobs, 8 concurrency) and record **counter volume** for attempted recovery, W1 skips, and startup recovery; do not compare p99 response time against `latest` because monitor-only changes no production behavior. | A7 | The close-out records per-path counter volume and a keep-primary / adopt-fallback decision based on that evidence (per `decisions.md §D7.4`); no latency regression is used. |

### Sub-Slice A, Task 4 (INV-2, part B — message-orphan recovery contract regression test)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| A9 | Create `tests/unit/services/test_job_processor_orphan_recovery.py`. Test name: `test_message_orphan_recovery_uses_w1_skip_and_startup_recovery`. Assert the actual contract: (a) monitor counters increment when the message-orphan recovery path is reached; (b) the W1-skip fires for a `job_type='message'` ACTIVE orphan, with no `spawn_instance_with_mcp` or `enqueue_message` dispatch; and (c) `JobRecoveryService.recover_on_startup → reset_active_to_queued` flips the row to QUEUED and releases the lock atomically. Do not assert `start_job_atomic_with_lock` for a message orphan. | A7 | `pytest tests/unit/services/test_job_processor_orphan_recovery.py --tb=short -q` returns 1 passed and the test names the W1-skip/startup-recovery ownership boundary. |

### Sub-Slice A, Task 5 (INV-2, part C — fallback path: scoped W1-skip extension, CONDITIONAL)

> **Trigger condition (per `decisions.md §D4`, `decisions.md §D7.4`, and the monitor-only decision in `plan-overview.md` Risk #2)**: execute A10 only when A8's counter-volume evidence shows that the W1-skip fallback is needed. If counter volume is immaterial, A10 is **NOT** merged; record the keep-primary decision. A p99-RT delta is not a valid gate for this monitor-only path.

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| A10 (fallback) | Extend the W1 skip (lines 825-854) to also skip `job_type == "message"` ACTIVE-orphan recovery when the JobItem has no paired `job_locks` row AND the Task row still exists. The recovery path is then left to `JobRecoveryService.recover_on_startup` (the documented owner of the W1-bypass). Document the trade-off inline (the recovery path can now race against the W1-skip on the same item; the existing `recover_on_startup` invariants handle the race). | A8 counter-volume evidence | W1-skip extension is in place only when the evidence gate selects it; the line comment cites §D7.4 and records the boot-only recovery residual risk. |

Sub-Slice B (Source-Adapters — runs in parallel with Sub-Slice A; one worker may own both if the dispatcher chooses to serialize):

### Sub-Slice B, Task 1 (INV-3, part A — diagnostic + core fix)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| B1 | Static check: confirm `registry.py:648-649` is the unconditional `backoff = 2.0` reset and lines 705-718 are the mis-gated reset. | none | Single match each; verified against current source. |
| B2 | Introduce `adapter._run_start_time: Optional[float] = None`. At the RUNNING transition (lines 642-645 area), set `adapter._run_start_time = time.monotonic()`. At the **entry** of the error `except` block (around line 680), compute and store `run_duration = time.monotonic() - adapter._run_start_time`; do not clear `_run_start_time` on stop/error transitions. Clear it at exactly one site — supervisor exit (around line 720). | none | The reset input is captured before the error transition; static search shows one clear site. |
| B2a | Verify that `_run_start_time` is bound to state that survives a supervisor restart. If a fresh adapter object is constructed per restart, move ownership to the surviving supervisor record or reject that construction shape; do not silently reset the clock value. Record the verified object identity / ownership decision in the implementation note and leave the choice open for the implementing worker if it cannot be proven by inspection. | B2 | B6 includes a restart-boundary fixture; the implementation records whether the value is adapter-bound or supervisor-bound. |
| B3 | Replace the line-649 unconditional `backoff = 2.0` with a conditional using the already-computed `run_duration`: reset only when `run_duration is not None and run_duration >= success_threshold`; otherwise leave `backoff` unchanged. | B2, B2a | `grep -nE '^\s*backoff = 2\.0' daemon/sources/registry.py` shows ONE occurrence (line 649) inside the duration conditional. |
| B4 | Tighten the line-705-718 reset gate to use the same `run_duration`/surviving `_run_start_time` value: reset iff `run_duration is not None and run_duration >= success_threshold`, with both reset sites gated by that invariant. | B2, B2a, B3 | Reset block no longer references `last_success_time`; static grep and B6 verify the ordering. |
| B5 | Update the W1-skip header comment (lines 700-704 area) to document the new invariant: backoff resets only on ≥`success_threshold` run duration and the state survives restart. | B4 | Comment updated; precise wording cited in the commit message. |

### Sub-Slice B, Task 2 (INV-3, part B — clock-injected regression test)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| B6 | Create `tests/unit/sources/test_source_registry_backoff.py` using a clock seam (monkeypatch `time.monotonic` in the `daemon.sources.registry` namespace or `_clock`). Cover (at minimum): (i) a sustained outage represented by k fast-failing starts, with `backoff >= initial * multiplier**k`; (ii) 59.9s → no reset; (iii) 60.0s → reset; (iv) 0.0s → no reset; and (v) a restart that preserves the run-start state. | B4 | `pytest tests/unit/sources/test_source_registry_backoff.py --tb=short -q` returns the expected passing count with no wall-clock sleeps. |
| B7 | Run a clock-injected integration-shape smoke test against `daemon.sources.registry.py` that exercises the supervisor restart loop and the sustained-DNS-outage fingerprint. Do **not** lower the real 60s threshold to 2s: the threshold is time control, not a timing tolerance. Assert state-machine decisions (`backoff >= initial × multiplier^k` after k fast-failing starts), not elapsed seconds or a wall-clock restart count. | B6 | Smoke test records the state-machine decision deterministically and returns PASS under the bounded pack timeout. |

---

## Coupling Map (intra-Phase 1 + inter-phase handoff)

| | Sub-Slice A | Sub-Slice B | Phase 2 |
|---|---|---|---|
| Sub-Slice A | — (A1→A2→A2b→A3→A4→A5; A6→A7→A8→A9, with A10 conditional on counter volume) | independent (separate files; separate test files; separate pack) | **loose — INV-1 fix may surface more SKIP contention → INV-4 next**: Phase-2-Block note required (see Handoff below) |
| Sub-Slice B | independent | — (B2→B2a→B3→B4→B5→B6→B7) | **independent — no Phase-2 coupling**; source-layer defects (INV-8, INV-10, INV-11) co-locate but do not depend on Phase-1 commits |
| Phase 2 | loose (handoff) | independent | — |

### Intra-Phase 1 (tight couplings — these are non-negotiable)

- **A1 → A2 → A2b → A3 → A4 → A5**: A2 produces the observation-mode code; A2b gives the optional re-raise an observable outer-handler outcome; A3 documents; A4 verifies; A5 proves. Same code region; same commit.
- **A6 → A7 → A8 → A9 (and conditionally A10)**: A6 builds the metric scaffolding; A7 enables the log; A8 records counter volume; A9 writes the contract test; A10 is the fallback gate by counter-volume evidence.
- **B2 → B2a → B3 → B4 → B5 → B6 → B7**: B2 introduces state and captures duration; B2a verifies restart ownership; B3 and B4 are the two duration-gated rewrites; B5 documents; B6+B7 prove via clock injection and state-machine assertions.

### Inter-Phase Handoff (Phase 1 → Phase 2)

The Phase 1 → Phase 2 handoff is **loose but not free**:

- **INV-1 → INV-4 (per `plan-overview.md` Coupling row)**: INV-4 (Unbounded SKIP contention TOCTOU at `job_processor.py:711-714` ↔ `1054-1116`) sits in the same ACTIVE-loop block as INV-1 (lines 1263-1271 are downstream of 1054-1116 start). Today the silent-swallow masks most SKIP-loop errors. **After INV-1 lands**, any previously-swallowed SKIP-loop error will now ERROR-log and may show up as a flush of errors at deploy time. **Handoff note (mandatory, paste into Phase 2 plan-overview header)**: "Phase 2 INV-4 work MUST coordinate with the INV-1 error-logging change. If deploys see a flush of ERROR logs from `job_processor.py:711-714` (the SKIP-loop region) after this branch ships, treat those logs as a Phase 2 INV-4 telemetry opportunity — measure contention + size the backoff/jitter needed. Do NOT silence them at the INV-1 site; let them surface." The handoff is documented in this plan; Phase 2's plan reader owns the response.

- **INV-2 → INV-9 (per `plan-overview.md` Coupling row and `decisions.md §D5`, §D7.4)**: INV-9 (PENDING message-job guard gap untested) is authored as a **test-after** in Phase 3, reading the Phase-1 commit for INV-2's W1-skip / `recover_on_startup → reset_active_to_queued` contract. No action is required at Phase 1 close besides not deleting the A9 seam.

- **INV-3 → INV-11 (per `decisions.md §D5`, §D7.6)**: INV-11 (Source adapter error-path coverage) is authored in Phase 3 as test-after, reading B6/B7 to know what to assert. No action is required at Phase 1 close.

---

## Risks (Phase-1 specific; complementing `plan-overview.md` table)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| P1-1 | INV-1 fix surfaces a flush of previously-swallowed errors at deploy, including errors from `complete_job` itself (e.g., DB connection blips, queue repo contention) | High | Medium | Per-task A2 applies the dual cap (per-`job_id` 30s plus approximately 100/min globally). After deploy, observe logs on the **demo** environment (port 7979) for 10 minutes; pass only with stable queue depth and per-`job_id` error rate ≤2× the historical baseline. Do not use a load-shadow replica; defer if the gate fails. |
| P1-2 | INV-2's monitor-only default (per `decisions.md §D7.4`) can remain in production if the A8 counter-volume evidence is missing or immaterial, leaving the underlying bypass unfixed | High | Medium | A8 must record attempt/skip/startup-recovery counts and the keep-primary / adopt-fallback decision. The close-out reviewer rejects a path decision without that evidence; fallback is not merged merely because a latency benchmark changed. |
| P1-3 | INV-3's `_run_start_time` may be lost if a fresh adapter object is constructed for each supervisor restart; a B6 fixture that does not exercise object identity could pass while production still resets on fast-fail | High | Medium | B2a verifies whether the value belongs to a surviving adapter or supervisor record and records the decision; B6 includes the restart boundary; B7 uses injected time. Per `decisions.md §D7.6`, no assumption of survival is made without proof. |
| P1-4 | INV-2 monitor-only leaves the message-orphan bypass unfixed in production; if the A10 fallback is adopted, `recover_on_startup` is boot-only, so mid-session orphans wait for daemon restart | High | Medium | Document both residuals in the close-out and the W1-skip comment. Keep monitor-only as the no-behavior-change default; adopt A10 only when counter-volume evidence requires it. The boot-only limitation is an accepted `decisions.md §D7.4` trade-off. |
| P1-5 | INV-3 timing tests can become flaky if they wait on real wall-clock time or lower the threshold to 2s | Medium | Low | B6/B7 must use a module-level monkeypatch or `_clock` seam and assert duration-boundary/state-machine decisions (59.9/60.0/0.0 and `multiplier**k`), with no wall-clock 2s or 10s assertions. |
| P1-6 | The phase-close PR from this plan introduces multiple commits (Sub-Slices A and B) which could bloat the merge commit per `plan-overview.md` Success Criterion #9 | Low | Medium | Use squash-merge (or a final empty commit consolidating both sub-slices) at Phase 1 close. Document the squash rationale in the PR body citing Success Criterion #9. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | INV-1 target swallow is removed while sibling log-only blocks remain explicitly documented | Targeted `grep`/read-back of `job_processor.py:1263-1271`; grep for `except Exception: pass`, `JOB_PROCESSOR_RAISE_ON_ERROR`, per-`job_id`/30s cap, and global/100-per-minute cap; close-out note names lines 535 and 571 | Zero target `pass`; default-off switch + both caps documented; exactly two sibling sites listed for Cycle-3 |
| 2 | INV-1 observation mode and kill-switch re-raise preserve cleanup and produce an observable outer-handler outcome | `pytest tests/unit/services/test_job_processor_error_handler.py --tb=short -q` plus A5 assertions | Default matrix passes without re-raise; enabled matrix proves conditional re-raise, cleanup, and counter/DLQ signal |
| 3 | INV-2 message-orphan contract uses monitor counters, W1-skip, and startup recovery rather than a message-job `job_locks` acquisition | A9 test `tests/unit/services/test_job_processor_orphan_recovery.py` plus code review of `job_processor.py:841-843` and `job_queue/repository.py:2169+` | A9 PASS: counters increment, W1-skip prevents both dispatch calls, and `recover_on_startup → reset_active_to_queued` releases the lock atomically |
| 4 | INV-2 primary/fallback decision is based on counter-volume evidence, not p99 latency | Phase-1 close-out PR contains the A8 attempt/skip/startup-recovery counter table and an explicit `decisions.md §D7.4` decision | One documented path; A10 is not merged without evidence |
| 5 | INV-3 both backoff resets use run duration captured before error handling and survive the relevant restart | Static grep for both reset gates and one `_run_start_time` clear; B2a/B6/B7 clock-injected fixtures | No `last_success_time` gate; 59.9s and 0.0s do not reset, 60.0s resets, state survives restart, and k fast failures grow backoff by `multiplier**k` |
| 6 | INV-3 sustained-outage regression is represented without wall-clock time | B7 clock-injected integration-shape smoke | State-machine assertion PASS; no 2s threshold-lowering or 10s wall-clock restart assertion |
| 7 | Phase 1 close-out includes both sub-slices and all amendment evidence | `git log`/PR body inspection for A1–A10/B1–B7, counter table, demo observation, and D7 citations | One coherent phase close PR; all required artifacts present |
| 8 | Demo post-deploy observation is safe and bounded | Ten-minute log observation on demo (7979), queue-depth sampling, and per-`job_id` error-rate comparison | Queue depth stable and error rate ≤2× historical baseline; otherwise deploy is deferred |

---

## ensure.md Compliance (Verification Matrix)

Quoted verbatim from `.agents/tester/rules/ensure.md`:

> "Pack-mapped: requirements reference packs in PACKS.md (or static checks), NOT bare `pytest` commands."
> "Scoped by blast radius: validate only requirements relevant to the change set."
> "Run as packs: every validation executes as a pack (or ad-hoc pack) with the dual-layer 5-min timeout — NEVER as a bare, unbounded `pytest` command."
> "Quarantine-aware: tests in `.agents/tester/QUARANTINE.md` are skipped and do not fail a requirement."
> "No `-x`: never use pytest `-x` (stop-on-first-failure) for suite runs — it hides the full picture. Use `--tb=short -q`."

The plan honors these rules. Every gate invocation below is a **pack** (or an explicit static check), each with a 5-minute timeout, no `-x`, with `--tb=short -q`. **`tests/mock_test_job_queue_api.py` and `test/packs/mock_job_queue_test.sh` will be quarantined at cycle pre-flight (D7.7) before any Phase-1 gate run**; this phase does not create or repair that quarantine row.

### Core Gate (every change; scoped to Phase 1 blast radius)

| ensure.md Item | Validation | Threshold | Maps to Sub-Slice |
|----------------|-----------|-----------|-------------------|
| No regressions in changed packs | `timeout 300 bash test/packs/job_queue_unit_test.sh` + `timeout 300 bash test/packs/sources_unit_test.sh` | All PASS | A + B |
| Deadlock / concurrency integrity (`concurrency_atomic_unit_test`) | `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` | PASS | A (job-queue layer) |
| No sync DB calls on asyncio event loop (covered by `concurrency_atomic_unit_test` thread-identity tests) | same pack run above (canonical 13-file invocation) | PASS | A |
| `dev.sh` includes `--timeout-graceful-shutdown 10` (static check) | `grep -n '\-\-timeout-graceful-shutdown 10' dev.sh` | 1 match | Phase 1 hygiene (no edit to `dev.sh`, but verify untouched) |
| All callers of converted async functions properly await — `_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats` | `grep -nE '(await )?(_get_system_prompt_tokens|_compute_context_usage|get_queue_stats)\(' daemon/ -r` | All call-sites `await`-prefixed | A (no new callers expected; verify no regressions) |
| Original deadlock scenario (parent→child→complete) works without blocking | covered by `concurrency_atomic_unit_test` | PASS | A |
| No dead code from the fix (deleted code was truly unused) | static import-check + grep | no dangling imports | A + B |
| INV-1 target-handler acceptance | Targeted `grep` for `except Exception: pass`, `JOB_PROCESSOR_RAISE_ON_ERROR`, per-`job_id`/30s cap, and global/100-per-minute cap; verify `_process_loop` counter/DLQ path | Target pass = 0; default OFF, both caps, and kill-switch documentation present; sibling lines 535/571 remain and are close-out noted | A |
| INV-2 monitor/recovery contract | A8 counter-volume table + A9 orphan-recovery test | Counts recorded; W1-skip/no-dispatch + startup atomic reset assertions PASS | A |
| INV-3 duration/clock contract | B2a/B6/B7 static and clock-injected tests | 59.9/60.0/0.0 boundaries, restart survival, and multiplier growth PASS | B |

### Release Gate (BIG/CRITICAL — `job_processor.py` changes are cross-module + e2e-impact; `sources/registry.py` is the active incident root-cause)

Per `ensure.md`: "Run ONLY when blast-radius determines the change is big/critical (cross-module, architecture refactor, release)." Both INV-1 and INV-2 change `job_processor.py` (cross-module — propagates through queue + instance + checkpoint paths); INV-3 is the active incident root-cause. **The Release gate runs for the whole Phase 1.**

| ensure.md Item | Validation | Threshold | Maps to Sub-Slice |
|----------------|-----------|-----------|-------------------|
| Prerequisites: daemon running, SSL clean, `PYTEST_TIMEOUT=280`, queue cleanup before each test | `./dev.sh` (health at `localhost:8079`); `unset SSL_CERT_FILE SSL_CERT_DIR`; `PYTEST_TIMEOUT=280 --override-ini="timeout=280"`; `GET /api/jobs?status=pending` cleanup before each test | Daemon /livez + /readyz green; queue empty | All |
| Full non-integration suite green (excluding QUARANTINE.md) | All non-integration packs in PACKS.md run in parallel via `test/packs/`, each with 5-min cap, ignoring `mock_job_queue_test` only after its D7.7 pre-flight quarantine row is present | All PASS | All |
| E2E: Normal parent→child workflow completes (happy path) | `timeout 300 bash test/packs/e2e_workflows_ensure_test.sh` OR `PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest tests/e2e/test_e2e_workflows.py --override-ini="addopts=" --override-ini="timeout=280" -m integration -k "test_parent_child_workflow_happy_path" --tb=short -q` | PASS | A (Sub-Slice A is e2e-impact) |
| E2E: Pause after spawn, then resume works correctly | same pattern, `-k "test_pause_after_spawn_then_resume"` | PASS | A |
| E2E: Terminate after spawn, then revive documented | same pattern, `-k "test_terminate_after_spawn_then_revive"` | PASS | A |
| E2E: 3-level cascade (leader→tester→staggered workers): reports delivered, no premature completion, no stuck completion | `timeout 300 bash test/packs/e2e_workflows_ensure_test.sh` OR `PYTEST_TIMEOUT=280 timeout 320 .venv/bin/pytest tests/e2e/test_e2e_workflows.py --override-ini="addopts=" --override-ini="timeout=280" -m integration -k "test_three_level_cascade_reports" --tb=short -q` | PASS | A |
| Scoped source pack verification (INV-3) | `timeout 300 bash test/packs/sources_unit_test.sh` | PASS (and B7 smoke) | B (Sub-Slice B keeps the blast radius scoped to sources_unit_test; no e2e needed) |

**E2E tests are run one by one** per `ensure.md` ("each makes real LLM calls; combined exceeds 5-min cap"). The 3-level cascade is the longest single test (~280-320s including LLM calls); the other three are faster. Daemon must remain up between tests.

**Sticky-handle**: `Sub-Slice B` is permitted to pass without Release-gate E2E because the source-layer does not affect the e2e_workflows pack surface; the source pack + B6/B7 tests are sufficient. The Core-gate `concurrency_atomic_unit_test` still runs against the full daemon.

---

## Deliverables & Exit Criteria

Phase 1 closes when **all** of the following are true:

1. **Code changes** (Sub-Slice A + Sub-Slice B) merged to `feature/schedule-review-improve`. Single coherent phase-PR per Success Criterion #9.
2. **All Core-gate items PASS** (table above).
3. **All Release-gate items PASS** (table above), OR an explicit deferred-justification in the PR body if any Release-gate item is skipped (e.g., environment cannot bring up daemon — must be cited in a critical-note and the gate re-run as a follow-up).
4. **A8 counter-volume result documented** in the PR body (table format) — records attempted recovery, W1 skips, and startup recovery and confirms either the primary (monitor-only) or fallback (scoped W1-skip) path under `decisions.md §D7.4`.
5. **Demo observation complete** — ten minutes on port 7979, stable queue depth, and per-`job_id` error rate ≤2× historical baseline; otherwise deployment is deferred.

The Phase-1 worker reports back to the dispatcher (planner v2) on completion with: (a) PR link, (b) per-task green/red status table, (c) A8 counter-volume table for INV-2 path selection, (d) the demo observation result, and (e) any deviation from this plan (which the dispatcher routes to a `decisions.md` amendment, not a phase-plan override).

---

## Open Questions (Phase-1 scope, delegated back to dispatcher)

1. **A8 counter-volume interpretation**: what volume of message-orphan attempts, W1 skips, and startup recoveries constitutes sufficient evidence to select A10? **Default if unset**: keep the monitor-only primary and do not merge A10; record the evidence gap for reviewer decision.
2. **B2a restart object identity**: does `_run_start_time` live on a surviving adapter instance or on the supervisor record? The implementing worker must verify the actual object lifecycle; if fresh adapters are constructed per restart, move the state to the surviving owner or stop and amend the plan rather than assuming survival.
