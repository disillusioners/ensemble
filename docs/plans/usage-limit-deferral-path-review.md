# Review: Usage-Limit Deferral Path (Approach D — dedicated path, reused machinery)

| Field | Value |
|---|---|
| **Date** | 2026-08-27 — **REV3** (independent pass over the REVISED plan; supersedes the rev2 verdict below) |
| **Document reviewed** | [`docs/plans/usage-limit-deferral-path.md`](usage-limit-deferral-path.md) (DRAFT — REVISED 2026-08-27 rev2, incorporating rev1+rev2) |
| **Cross-referenced** | [`docs/plans/usage-limit-window-recovery.md`](usage-limit-window-recovery.md) (parent, adjudication §1a) · `daemon/llm_error_classifier.py` · `daemon/repositories/task/repository.py` · `daemon/services/worker_pool.py` · `daemon/services/task_processor.py` · `daemon/services/message_processing_errors.py` · `daemon/services/message_processing_pipeline.py` (stage boundaries — NEW this pass) · `daemon/services/stale_task_recovery.py` (**post-retry action blocks `:399-540`, `:640-793` — NEW this pass**) · `daemon/manager.py:4118-4137` (`_on_stale_task_permanent_failure` callback — NEW) · `daemon/services/llm_failover.py` · `daemon/repositories/instance/repository.py` · `daemon/config.py` · `config.yaml` — all anchors re-verified read-only 2026-08-27 (rev3 pass) |
| **Verdict (rev3)** | **Approve with required changes.** The rev2 required change (§2.1 race-gated terminal) and all three rev2 minors are correctly incorporated — verified against code, not the revision notes. Two NEW required changes, both in a region **neither prior pass examined**: stale recovery's *post-retry* actions. §2.1 🔴 startup recovery reports permanent failure for successfully-RETRIED tasks (misindented notify at `stale_task_recovery.py:699-712` — fires `_send_error_report` on a live episode, the rev2 §2.1 zombie-kill re-entering through this door); §2.2 🔴 the grace sweep fails the episode's message on a successful anchor-gated recovery (`:531-536` — the recovery child inherits the now-FAILED `message_id`). Five minors (§3). |
| **Verdict (rev2, superseded)** | Approve with one required change — §2.1 (gate the terminal composition on `fail_task` outcome; fix the W4.2d fallthrough). Minors §3.1-3.3. All rev1 findings verified incorporated (§1.1); anchors re-verified (§1.2). |
| **Verdict (rev1, superseded)** | Approve with required changes — two blockers (stage-2 cascade §2.1; nonexistent terminal mechanism §2.2), one high (bus-watcher stranding §3.1), one medium (stale-recovery crash window §3.2), five minors. All now verified incorporated. |

---

## 1. Verified correct (rev3 — incorporation matrix + anchor re-verification)

### 1.1 Rev2 findings → revision, all verified against code

| Rev2 finding | Revision | Verification |
|---|---|---|
| §2.1 🔴 terminal composition unguard-raced / W4.2d fallthrough reports on a live episode | W4.2 gates the ENTIRE composition (parent notify + watcher notify + anchor clear) on `fail_task(...) is not None`; gate-closed fallthrough is log-and-return with the W8-recovery-child rationale spelled out | The pseudocode, the "stronger gate than its TIMEOUT precedent" argument (`worker_pool.py:795-800` fires parent notify unconditionally — verified), and acceptance test 5's race-LOST + gate-closed-with-live-episode cases are all present and correct. The `_handle_task_failure`-has-no-parent-notify constraint (`worker_pool.py:831-842`) re-verified — the self-composed terminal remains the only mechanism. |
| §3.1 W8 coverage sentence missing | "Coverage scope (review rev2 §3.1 — deliberately NOT all four callsites)" paragraph | Verified against code: episode parents are CANCELLED with `retry_scheduled = true` atomically with the child insert (gate UPDATE + `RetryTurn.run` in one `engine.begin()`, `repository.py:2925-3036`), the C2 skip is at `stale_task_recovery.py:468`, and `find_orphaned_cancelled_tasks` (`repository.py:3472-3482`) also excludes `retry_scheduled = true` rows — the plain `schedule_retry` callsites (`:474/:727`) are indeed unreachable for episode shapes. The do-NOT-blanket-thread instruction is explicit. |
| §3.2 anchor lifecycle at terminal unspecified | W6 clear-site 2 (terminal composition clears the anchor, soft-fail); risk-table row; test 6 reconciled to set-once-per-episode with BOTH ends clearing | Present and consistent across W4.2 pseudocode, W6, §5.6, and the risk table. |
| §3.3 handler robustness | W4 preamble (soft-fail anchor read/write, `first_seen = now` degenerate) + §7 handler-robustness test | Present; the uncaught-out-of-the-except-block rationale matches Python semantics (sibling `except Exception` at `:578` does not catch handler raises). |

### 1.2 Anchors re-verified (rev3 pass)

- Gate UPDATE's three terms on separate WHERE lines — `repository.py:2945` (`retry_scheduled = false`), `:2946` (`retry_count < :max_retries`), `:2947` (status IN) — W2's drop-only-that-term change remains well-formed. Same shape in `force_cancel_and_schedule_retry` at `:3362-3365`.
- Claim eligibility `next_retry_at IS NULL OR next_retry_at <= :now_str` with no `retry_count` gate — `repository.py:1189` — bypass-grown counts still claimable.
- `_process_with_timeout` except chain: `except TimeoutError` `worker_pool.py:514-521`, generic `except Exception` `:578` — W4's insertion point correct. `_notify_parent_of_failure` (`:590-625`) accepts `error_type` + `message_id`; `_cancel_bus_watchers_for_task` (`:627-660`) and the TIMEOUT lane's release (`:782`) as described.
- Classifier: blocklist `llm_error_classifier.py:108-112`; bare-`APIError` branch `:850-867`; `ValueError` branch `:894-905` — both accept the check-before-blocklist ordering; `_normalize_patterns` (`:136-138`) reusable for `usage_limit_patterns`.
- **Stage-2 boundary (new verification):** the execution gate / work_fn runs OUTSIDE the pipeline's internal post-processing try (`message_processing_pipeline.py:432-437` vs `:450`) — a `UsageLimitError` from the LLM call inside work_fn propagates as an exception to `task_processor.process`'s outer try, where the W3 carve-out (`task_processor.py:422` insertion point, before the generic `except Exception`) re-raises it untouched. The carve-out mechanism is sound. (But see §3.3 for the stages-3-6 invariant note.)
- Metadata helpers `set_metadata` / `delete_metadata` — `daemon/repositories/instance/repository.py:1204` / `:1380` — sync, dialect-aware, usable from the worker thread.
- `is_retry = task.retry_count > 0 or original_resume_mode` — `task_processor.py:352`; `_build_callbacks` success side (`:822-856`) accepts the W6 anchor-clear insertion.
- `ServicesConfig` timing knobs at `daemon/config.py:590-610`; `WorkerPool(...)` wiring at `daemon/manager.py:5841-5849` — W7's placement beside `task_timeout_minutes` correct.
- `retry_count` consumer audit (performed this pass — closes the plan's §6 grep-audit risk row): task-lane budget gates are only `repository.py:2946`/`:3364` (both handled by W2/W8) and the Python-side `stale_task_recovery.py:428` (see §3.5); claim path gate-free (`:1189`); job-queue, message-queue, and blueprint `retry_count` columns live on separate tables — unaffected by task-lane bypass growth.

## 2. Required before implementation (rev3)

Both findings are in stale recovery's **post-retry action blocks** — the code that runs AFTER `force_cancel_and_schedule_retry` returns. Rev1 §3.2 and rev2 §3.1 examined the recovery *callsites* (which retry method, with which flags); neither pass examined what recovery does next. W8 routes the usage-limit episode through exactly this code, so it is now load-bearing for acceptance test 7b.

### 2.1 🔴 Startup recovery reports permanent failure for successfully-RETRIED tasks (`stale_task_recovery.py:699-712`)

In `recover_on_startup` Phase A (stale RUNNING → force-cancel + retry), `recovered += 1` and the
`_on_task_permanently_failed` callback sit at the **sibling level** of `if retry_task:` / `else:` —
the notify fires on EVERY task, including those whose retry child was just successfully created.
Contrast the grace sweep (`:441`, inside the else) and orphan Phase B (`:757`, inside the else),
which place it correctly.

The callback routes `manager._on_stale_task_permanent_failure` (`manager.py:4118-4137`) →
`_send_error_report(error_type='stale_task_failure')` → child ERROR + hierarchy cleanup + parent
envelope. On the exact test-7b scenario — mid-episode crash → restart → Phase A force-cancel WITH
the W8 bypass → recovery child created, episode saved — the parent nonetheless receives a
permanent-failure report and the child instance is killed **while the episode continues via the
recovery child**. This is rev2 §2.1's zombie-kill (a report on a live episode) re-entering through
a door neither prior pass opened. It is also a pre-existing bug: every successful TIMEOUT
crash-recovery today emits a spurious permanent-failure report.

**Fix:** move the notify into the `else` (permanent-fail) branch, gated on
`failed_task is not None` per the Phase 2 Batch 2 convention. Acceptance test 7b must assert NO
parent report on the successful-recovery path. (Plan impact: W8 gains one sentence; the fix
itself is an indentation change in recovery code the plan already touches.)

### 2.2 🔴 Grace sweep fails the episode's message on a successful anchor-gated recovery (`stale_task_recovery.py:531-540`)

`task_acted_upon` is set for the force-cancelled-AND-RETRIED branch (`:415`), so the W6 message
guard `if task_acted_upon and task.message_id: message_repo.fail(...)` marks the message FAILED
while the recovery child — which inherits the same `message_id` (`schedule_retry`'s child_kwargs,
`repository.py:3006`) — is pending. The pipeline's stage-1.5 claim is best-effort on a non-READY
message and stage-4 `complete()` requires processing status (the pipeline's own comments,
`message_processing_pipeline.py:413-424`), so the message stays FAILED even after the window lifts
and the retry succeeds. This contradicts acceptance test 2 ("message NOT `failed`") and test 7b
("episode survives") — the episode's observables are NOT zero across a crash.

**Fix:** skip the message-fail when a retry child was created (scope it to terminal recoveries
only). Arguably the correct shape for TIMEOUT recoveries too — same inherited-message structure.
Assert in test 7b: message status intact after anchor-gated recovery.

## 3. Minor (rev3 — address before or at merge)

1. **Recovery child wakes on exponential backoff, not the W5 schedule.**
   `force_cancel_and_schedule_retry` keeps the `2**retry_count` derivation
   (`repository.py:3395-3399`); with bypass-grown `retry_count` (~26) the delay caps at
   `backoff_max` (3600 s) — a crash burns up to **60 min** of the 6 h window per recovery event.
   W8 already reads the anchor for the bypass; derive `next_retry_at` via the W5 function
   (thread the `next_retry_at` param through `force_cancel_and_schedule_retry` as well) or state
   the accepted dead time in the plan.
2. **W5 jitter can schedule into the past.** An early-jittered wake still has
   `elapsed < cumsum[k]` → the same slot is re-selected → re-rolled negative jitter can land
   before `now` → immediate re-attempt (~2 wakes per slot; the "~26 wake-ups" count and
   wall-clock monotonicity both soften). Clamp `next_retry_at ≥ now + floor` or use one-sided
   additive jitter.
3. **Stages-3-6 invariant unstated.** A `UsageLimitError` raised inside the pipeline's internal
   try (`message_processing_pipeline.py:515-526`) would ride `result.error` — the cascade fires
   inside the pipeline and the W3 carve-out cannot help. Unreachable today (the classifier raises
   inside the LLM call, i.e. work_fn/stage 2 — verified §1.2); add one sentence naming that
   invariant so a future stage refactor doesn't silently break it.
4. **Landing order (§9).** W7 (config keys) lands last, but W1's patterns are config-driven —
   correct only if W1 uses module defaults until W7 lands. Land W7's `usage_limit_patterns`
   plumbing together with W1, or state the interim-defaults assumption.
5. **`stale_task_recovery.py:428` Python-side budget gate.** Unreachable for live episodes
   (a RUNNING task never carries `retry_scheduled = true`, and the gate sits in the
   `force_cancel → None` else where `retry_count >= max_retries` is the expected cause — but with
   the W8 bypass, `retry_count` can exceed `max_retries` on this path with the gate-closed cause
   being `retry_scheduled`, making the `:428` branch misdiagnose). The plan's W8 anchor-gating
   makes the bypass path return a child, so `:428` stays unreachable for episodes — worth one
   sentence in W8 stating the invariant (and §3.5's audit note above).

## 4. Summary (rev3)

The revision did everything rev2 asked and did it correctly — the race-gated terminal composition
is now exactly-once under every race outcome the reviewer can construct at the worker seam. Rev3's
two required changes are both in stale recovery's post-retry actions, a region neither prior pass
examined because W8 only recently made it load-bearing: a misindented parent-notify that reports
permanent failure for successfully-retried startup recoveries (§2.1 — the zombie-kill class again,
pre-existing and now on the episode's critical path), and the W6 message-fail guard marking the
inherited message FAILED under a live recovery child (§2.2). Both fixes are small (a re-indent, a
gate on retry-child creation, two test-7b assertions) and sit in files the plan already touches;
the 1-1.5 day estimate holds. Fix those, take the five minors, and this is ready for
implementation.

---

## Revision history

- **rev3 (2026-08-27):** Approve with required changes — §2.1 (startup-recovery notify fires on
  successful retries — misindent at `stale_task_recovery.py:699-712`) and §2.2 (message-fail on
  successful anchor-gated recovery, `:531-540`). Minors §3.1-3.5 (recovery-child schedule, W5
  jitter past-scheduling, stages-3-6 invariant, landing order, `:428` invariant note). All rev2
  findings verified incorporated (§1.1); anchors re-verified incl. pipeline stage boundaries and
  recovery post-retry blocks (§1.2); `retry_count` consumer audit closed (§1.2 last bullet).
- **rev2 (2026-08-27):** Approve with one required change — §2.1 (gate the terminal
  composition on `fail_task` outcome; fix the W4.2d fallthrough). Minors §3.1-3.3.
  All rev1 findings verified incorporated (§1.1); anchors re-verified (§1.2).
- **rev1 (2026-08-27):** Approve with required changes — blockers §2.1 (stage-2
  cascade) and §2.2 (nonexistent terminal mechanism); H1 §3.1 (bus watchers);
  M1 §3.2 (stale-recovery crash window); minors §4.1-4.5. Incorporated in full by
  the REVISED plan of the same date.
