# Review: Usage-Limit Deferral Path (Approach D — dedicated path, reused machinery)

| Field | Value |
|---|---|
| **Date** | 2026-08-27 — **REV2** (review of the REVISED plan; supersedes the rev1 verdict below) |
| **Document reviewed** | [`docs/plans/usage-limit-deferral-path.md`](usage-limit-deferral-path.md) (DRAFT — REVISED 2026-08-27, incorporating rev1) |
| **Cross-referenced** | [`docs/plans/usage-limit-window-recovery.md`](usage-limit-window-recovery.md) (parent, adjudication §1a) · `daemon/llm_error_classifier.py` · `daemon/repositories/task/repository.py` · `daemon/services/worker_pool.py` · `daemon/services/task_processor.py` · `daemon/services/message_processing_errors.py` · `daemon/services/stale_task_recovery.py` · `daemon/services/llm_failover.py` · `daemon/repositories/instance/repository.py` · `daemon/config.py` · `config.yaml` — all anchors re-verified read-only 2026-08-27 (rev2 pass) |
| **Verdict (rev2)** | **Approve with one required change.** All rev1 findings (both blockers §2.1/§2.2, H1 §3.1, M1 §3.2, minors §4.1-4.5) are correctly incorporated — verified against code, not just against the revision notes. One NEW required change: the W4.2 terminal composition fires `_notify_parent_of_failure` unconditionally, and the W4.2d gate-closed fallthrough can emit the episode's ONE report while the episode is still alive (§2.1 below). Three minors (§3). |
| **Verdict (rev1, superseded)** | Approve with required changes — two blockers (stage-2 cascade §2.1; nonexistent terminal mechanism §2.2), one high (bus-watcher stranding §3.1), one medium (stale-recovery crash window §3.2), five minors. All now verified incorporated. |

---

## 1. Verified correct (rev2 — incorporation matrix + anchor re-verification)

### 1.1 Rev1 findings → revision, all verified against code

| Rev1 finding | Revision | Verification |
|---|---|---|
| §2.1 🔴 stage-2 cascade fires before the worker seam | W3 carve-out (`except UsageLimitError: raise`) placed before the generic `except Exception` | The cascade is exactly as rev1 described: `task_processor.py:422-453` runs `handle_message_processing_error` (`:432`) + `_record_metrics_for_task(succeeded=False)` (`:452`). The carve-out placement, its "no policy, unconditional re-raise" scoping, the invariant-2 restatement ("two constructions"), and its dedicated test (§5.2) are all correct. |
| §2.2 🔴 deadline terminal mechanism did not exist | W4.2 self-composed terminal (`fail_task` + `_notify_parent_of_failure('usage_limit_deadline')` + watcher notify) | `_handle_task_failure` (`worker_pool.py:831-842`) still has no parent notification — the re-spec was necessary. The composition mirrors the `max_retries_exceeded` precedent (`:795-800`) correctly; `_notify_parent_of_failure` (`:590-625`) routes `manager._send_error_report` with `error_type` as required. Accepted-alternative note (window-aware carve-out) correctly documented as rejected. (But see §2.1 below for a guard gap in the new composition.) |
| §3.1 H1 bus-watcher stranding | W4.2b — `_cancel_bus_watchers_for_task(task.id, retry_task.id)` after every successful deferral | Helper confirmed at `worker_pool.py:627-660`; TIMEOUT lane's own release at `:782`; production-incident rationale carried into the plan. Test 4 extended to both watcher kinds with deferral notifications counted in "exactly once" — correct. |
| §3.2 M1 stale recovery kills mid-window episode | W8 — bypass threaded through `force_cancel_and_schedule_retry`, set when the instance has a live anchor (review option 1) | Guard confirmed at `repository.py:3364`; recovery callsites confirmed at `stale_task_recovery.py:368/:658` (force) and `:474/:727` (plain). Test 7b added; risk-table row retired. (See §3.1 for a coverage-clarity nit.) |
| §4.1 timing keys under `queue:` | Moved to `services:` beside `task_timeout_minutes` (`config.yaml:169-191`, read `svc.*` at `daemon/manager.py:5841-5849`) | Correct; patterns stay in `queue:` (right — `QueueConfig` is the `cc753c2f` pattern home, `_parse_csv_or_json_list` at `config.py:478-489`). |
| §4.2 facade type-swap | W1 audit + facade typed-surface test | Incorporated; graceful-fallback counts unchanged. |
| §4.3 anchor nit (`:755` not `:666`) | Fixed (`worker_pool.py:755` inside `_handle_cancellation`, entry `:662`) | Confirmed accurate. |
| §4.4 beyond-last-slot contract edge | W5 explicit extension-by-900s spec + docstring requirement + test-plan line | Correct and testable. |
| §4.5 "DLQ-eligible" wording | Test 5 reworded to terminal-report parity with the TIMEOUT lane | Correct — the task/worker lane never DLQs. |

### 1.2 Anchors re-verified (rev2 pass)

- `schedule_retry` is `daemon/repositories/task/repository.py:2878-3054`; gate UPDATE's `retry_count < :max_retries` term at `:2946` is a **separate WHERE line** from `retry_scheduled = false` (`:2945`) and the status guard (`:2947`) — W2's drop-only-that-term change remains well-formed. Backoff derivation at `:2976-2988`; F6 watcher migration inside `RetryTurn` (step 5, `:3020-3036`).
- Claim eligibility `next_retry_at IS NULL OR next_retry_at <= :now_str` with **no** `retry_count` gate — `repository.py:1189` — bypass-grown counts still claimable.
- `_process_with_timeout` except chain: `except TimeoutError` `worker_pool.py:514-521`, generic `except Exception` `:578` — W4's insertion point (after TimeoutError, before generic) is correct.
- Classifier: blocklist `llm_error_classifier.py:108-112` (`token plan`, `usage limit`, `invalid params`); bare-`APIError` branch `:850-867`; `ValueError` branch `:894-905`. Usage-limit patterns ⊂ blocklist, so check-before-blocklist ordering preserves all other shapes byte-identically; `invalid params` stays untyped terminal.
- `is_retry = task.retry_count > 0 or original_resume_mode` — `task_processor.py:352` — checkpoint resume rides the inherited `message_id`, exactly the TIMEOUT lane's behavior.
- Metadata helpers `set_metadata` / `delete_metadata` — `daemon/repositories/instance/repository.py:1204` / `:1380` — usable from the worker thread; JSONB survives restart.
- `task_timeout_minutes` default 125.0 at `daemon/config.py:590`; `WorkerPool(...)` wiring at `daemon/manager.py:5841-5849`; `max_retries` default 3 (`worker_pool.py:181` area, constructor).
- Parent plan §1a adjudication (Approach D) exists as cross-referenced; D's scope is independent of the parking plan.
- Schedule math: cumsum 180/480/1080/1980 then +900 — ~26 wakes in 21600 s; ±10 % jitter (max ±90 s) cannot overlap the 900 s slot spacing.

## 2. Required before implementation (rev2)

### 2.1 🔴 W4.2 terminal composition: `_notify_parent_of_failure` is unguard-raced, and the W4.2d fallthrough can report while the episode is still alive

Two connected defects in the new (correctly self-composed) terminal branch:

**(a) The report is not gated on the `fail_task` race outcome.** The plan's own
precedent — the `max_retries_exceeded` path it cites (`worker_pool.py:795-808`) —
gates the **watcher notify** on `failed_task is not None` (the atomic repo write
proving we won the status guard race; the Phase 2 Batch 2 convention, docstring at
`:844-866`), but fires `_notify_parent_of_failure` unconditionally. W4.2's pseudocode
copies that shape (`if failed_task is not None` guards only the watcher notify,
plan §W4.2 lines 205-206). For the TIMEOUT lane the unconditional parent notify is
defensible (the lane just observed `schedule_retry` return None — the episode IS
over). The usage-limit path adds a caller of that notify that the TIMEOUT lane does
not have: the W4.2d fallthrough.

**(b) W4.2d's fallthrough justification is only half-true.** "safe: something else
already decided the task's fate" covers terminal fates (operator cancel, concurrent
failure). But `schedule_retry` also returns None when the gate's `retry_scheduled =
false` term loses to a **concurrent retry-creating** actor — e.g. the stale
sweeper's anchor-gated `force_cancel_and_schedule_retry` (W8, `stale_task_recovery.py:368/:658`)
creating a recovery child for this very task. In that shape the fallthrough fires
`fail_task` (returns None — task is CANCELLED with a live child), then
`_notify_parent_of_failure('usage_limit_deadline')` anyway, and `_send_error_report`
performs child → ERROR + hierarchy cleanup **on an episode that continues via the
recovery child**. Exactly-once is violated and the episode is zombie-killed — the
same class of bug rev1 §2.2 closed, re-entering through the fallthrough door.

**Fix (one line of spec):** gate the *entire* terminal composition — parent notify
included — on `fail_task(task.id, ...) is not None`; when `fail_task` returns None
(including the W4.2d fallthrough), log one line and return. Extend acceptance
test 5: assert `_notify_parent_of_failure` is NOT called when `fail_task` loses the
race (add the gate-closed-with-live-episode case beside the existing gate-closed
test in §7 "Worker seam").

## 3. Minor (rev2 — address before or at merge)

1. **W8 coverage sentence missing.** The hole (plan §W8) names four recovery
   callsites; the decision threads bypass only through
   `force_cancel_and_schedule_retry` (`:368/:658`). That is **correct** — episode
   parents are CANCELLED with `retry_scheduled = true` (cancel + child insert is one
   transaction), so the C2 `retry_scheduled` check (`stale_task_recovery.py:468`)
   skips them and the plain `schedule_retry` callsites (`:474/:727`) are unreachable
   for episode shapes — but the plan should say so. Without the sentence an
   implementer will blanket-thread the bypass through all four callsites, weakening
   the "gate cannot over-reach" argument the anchor scoping is designed to make.
2. **Anchor lifecycle at terminal unspecified.** W6 clears the anchor only on the
   success callback; W4.1 is set-once. A deadline-terminal episode therefore leaves
   `usage_limit_first_seen_at` set forever. If the instance is ever re-used
   (restart, recovery, manual resume), the next quota hit reads the stale anchor,
   computes `elapsed > window`, and goes instantly terminal — no fresh 6 h window —
   and W8's bypass over-reaches on the stale anchor. Specify: the terminal
   composition (W4.2) clears the anchor (soft-fail, same as W6's clear); reconcile
   test 6's "anchor re-stamped" wording with W4.1's set-once semantics (set-once
   *per episode*, where terminal ends the episode).
3. **Handler robustness.** `_handle_usage_limit` is invoked from inside the
   `except UsageLimitError` block in `_process_with_timeout`; an exception raised by
   the handler itself (e.g. the anchor metadata write fails) propagates out of the
   except block — the sibling `except Exception` at `:578` does NOT catch it, and
   the worker loop sees an unexpected error attributed to the wrong cause. Spec
   soft-fail for the anchor read/write (log-and-proceed with `first_seen = now` as
   the degenerate case) so the handler cannot raise.

## 4. Summary (rev2)

The revision did everything rev1 asked and did it correctly — the incorporation
matrix above was verified against code, not just against the plan's change notes,
and every anchor still resolves. The new required change is narrow and ironic: the
W4.2d "safe fallthrough" added during revision re-opens the exact rev1 §2.2 bug
class (a report on a live episode) because the self-composed terminal — unlike its
TIMEOUT precedent — has a caller that reaches it while the episode may still be
running. Gate the whole composition on the `fail_task` race outcome, add the two
spec sentences (§3.1 coverage, §3.2 anchor-at-terminal) and the soft-fail note
(§3.3), and this is ready for implementation.

---

## Revision history

- **rev2 (2026-08-27):** Approve with one required change — §2.1 (gate the terminal
  composition on `fail_task` outcome; fix the W4.2d fallthrough). Minors §3.1-3.3.
  All rev1 findings verified incorporated (§1.1); anchors re-verified (§1.2).
- **rev1 (2026-08-27):** Approve with required changes — blockers §2.1 (stage-2
  cascade) and §2.2 (nonexistent terminal mechanism); H1 §3.1 (bus watchers);
  M1 §3.2 (stale-recovery crash window); minors §4.1-4.5. Incorporated in full by
  the REVISED plan of the same date.
