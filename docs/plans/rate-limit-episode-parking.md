# Plan: Provider Rate-Limit Episode Parking (5h window)

| Field | Value |
|---|---|
| **Status** | DRAFT — Pending Team Review. Date: 2026-08-26. Design converged; all five decision points approved by the user (§7). |
| **Goal** | On provider-wide rate-limit exhaustion, park the job and auto-retry from checkpoint until first-429-observation + configurable window (default 5h = provider quota reset), then fail terminally with a distinct, diagnosable reason. **Hard constraint:** additive-only — existing retry semantics for ALL non-rate-limit errors stay byte-identical. |
| **Scope** | LARGE — single coder (the chain is interdependent; no safe parallel partition). Spans `daemon/llm_error_classifier.py`, `daemon/graph.py`, `daemon/services/message_processing_errors.py`, `daemon/services/error_reporting.py`, `daemon/services/job_feedback_observer.py`, `daemon/repositories/job_queue/` (+ `models.py` enum, one SQL migration), retry scheduler, `daemon/config.py`. |
| **Risk** | `terminal_reason` enum addition vs `_derive_legacy_status` canonical map / `reconcile_turn_mirror` MIRROR_SET drift; scheduler scoping leak; anchor stamping seam. See §9. |
| **Evidence** | All `file:line` anchors verified read-only by two investigation passes on 2026-08-26. Code may drift — re-verify anchors at implementation time. |
| **Related** | `docs/plans/turn-reconciler-named-transitions.md` (MIRROR_SET check for new `terminal_reason`), `docs/plans/report-lane-decoupling.md` (finalize-lane authority), `daemon/services/llm_failover.py` (L2 HA), `daemon/services/job_retry_engine.py` (L4 retry primitives) |

---

## 1. Problem

The LLM gateway intermittently returns HTTP 429 with body
`{'error': {'type': 'rate_limit', 'code': 'rate_limit', 'message': 'All models rate limited'}}` —
a **provider-wide quota outage** whose reset window is ~5 hours. Today the failure cascade is:

1. The fast retry budget (10 transient attempts, exponential-jitter backoff, per-invoke reset; LLM HA
   failover primary-slice 3/2 with a 45s wall-clock cap on the v2 facade) exhausts in ~1–2 minutes.
2. The instance dies `ERROR`.
3. The parent receives a misleading error report — `error_type='transient_error'`, severity
   `warning`, with `RECOVERY_GUIDANCE_HINT` advising "waiting never works; revive once, then spawn a
   replacement" — precisely wrong during a provider-wide outage. Parents historically spawned
   replacement storms on this advice.
4. The job strands, because observer-finalized jobs are never auto-retried (§2, *Job finalize*).

The fatal case is not the intermittent brownout (those survive the fast budget today); it is a
sustained all-models outage outlasting ~2 minutes.

---

## 2. Evidence base (verified read-only 2026-08-26; two investigation passes)

- **429 classification.** 429 is classified `TransientAPIError` at `daemon/llm_error_classifier.py:561`;
  the body text is never inspected — no provider-wide discriminator exists.
  `RETRYABLE_STATUS_CODES` at `:20`.
- **In-turn retry (L1).** tenacity `Retrying(reraise=True)` wired at `daemon/graph.py:3503-3518`;
  `agent_node` post-retry exhaustion catch at `graph.py:3340-3349`; per-INVOKE budget reset
  (`llm_error_classifier.py:426-432`). **No wall-clock cap on the agent-chat hot path**
  (asymmetry vs the facade).
- **HA failover (L2).** `wall_clock_cap_s=45` exists only in `daemon/services/llm_failover.py:481,520`
  (LangChain facade) and `:806` (raw-SDK); `stop_after_delay` between attempts; primary slice
  `PRIMARY_TRANSIENT_MAX=3`/`PRIMARY_TIMEOUT_MAX=2` (`llm_error_classifier.py:164-165`) with
  cross-category reset on URL swap (`:505-512`).
- **Exhaustion path.** `task_processor.py:422-453` generic-exception branch →
  `handle_message_processing_error` (`message_processing_errors.py:296-310`) → error report
  envelope built at `error_reporting.py:734-740` (`error_type` is a free string param at `:403`;
  severity via `CRITICAL_ERROR_TYPES` `:26`/`:476`; `RECOVERY_GUIDANCE_HINT` `:41-48` appended
  `:739`). Note: the `'rate_limit'` branch at `message_processing_errors.py:105-106` is
  **UNREACHABLE** — a raw `openai.APIStatusError` never survives the `TransientAPIError` wrapper;
  the actual type on this path is `'transient_error'` (`:136-137`).
- **Job finalize.** Lifecycle event → `JobFeedbackObserver._finalize_job_db_sync`
  (`daemon/services/job_feedback_observer.py:3249-3302`): `admission_state=DONE`,
  `terminal_reason='failed'` (`models.py:392` discriminator), `failed_at` set — and **zero**
  retry-engine references on this lane. `JobRetryEngine.should_retry`/`maybe_retry` are only
  consulted on the `complete_job(FAILED)` lane (`job_queue_service.py:3242-3368`). `RetryScheduler`
  (`daemon/api.py:431-441`, opt-in default-off) only **wakes** already-requeued jobs
  (`repository.py:2889-2925`); it never transitions done→queued. Instance→job link is resolved at
  finalize via `_get_processing_job_for_instance` (`job_feedback_observer.py:632`).
- **Job retry primitives (L4).** `atomic_retry` (`repository.py:1364-1510`) requeues guarded
  (queued + `next_retry_at`, clears `failed_at`, bumps `retry_count`); backoff base=60s max=3600s
  multiplier=2.0 + jitter (`job_retry_engine.py:73-102`). Revive path works on `ERROR` instances:
  `instance_messaging.py:1486-1510`; `is_retry=True` → checkpoint resume (`task_processor.py:352`).
  `task_timeout_minutes=125` (`config.py:467`) forbids holding one task for 5h.
- **Runtime logs** (`ensemble.log.3`, 2026-08-20): two windows (00:10, 00:38), both intermittent
  brownouts that **survived** the fast budget; the current log shows plain `429 Rate limit` events
  also surviving. The fatal case is a sustained all-models outage outlasting ~2 min.
- **Prior art / hazards.** `registry.py:705-718` backoff-reset bug (anchor to concrete events, not
  time-since-success); `LoopBreaker` threshold=3 (`graph.py:939-1086`, `config.py:935-940`) forbids
  in-agent retry loops; pause-first-then-quiesce convention (pause cancels `graph_task`; resume is
  DB-only + `is_retry` dispatch); `checkpoint_ttl` 7d ≫ 5h.

---

## 3. Goal

On provider-wide rate-limit exhaustion, **park the job and auto-retry from checkpoint** until
first-429-observation + configurable window (default 5h = provider quota reset), then **fail
terminally with a distinct, diagnosable reason**.

**Hard constraint: additive-only — existing retry semantics for ALL non-rate-limit errors stay
byte-identical.**

---

## 4. Design

Three phases: **A** detect & anchor (LLM layer), **B** classify & park (finalize seam), **C**
re-dispatch (existing machinery). Numbered items 1–8 are work units; cross-references below use
these numbers.

### Phase A — Detect & anchor

**1. Provider-wide discriminator: new exception.** New exception
`ProviderRateLimitError(TransientAPIError)` in `daemon/llm_error_classifier.py`. The subclass stays
matched by `TRANSIENT_EXCEPTIONS` so L1 tenacity / L2 failover / HA behavior is unchanged.
Detection: when classifying a 429 `APIStatusError`, the body/message is matched against
configurable patterns (default `['all models rate limited']`, case-insensitive substring).
Detection sites: the `APIStatusError` branch in `_run_with_classification`
(`llm_error_classifier.py` ~:558-563) and `_classify_raw_sdk_exceptions` (`llm_failover.py`
~:236-283). The wrapper preserves `.status_code`/`.original` (`:50-51`).

**2. First-sighting anchor.** Persist `rate_limit_first_seen_at` (ISO) into INSTANCE metadata on
first `ProviderRateLimitError` sighting (survives restart/pause/revive). Stamp where instance
identity exists — the `agent_node` catch (`graph.py:3340-3349`) or via graph retry_config/client
construction (`graph.py:3586-3610`); the implementer picks the least invasive correct seam. Rules:
set only if absent (monotonic per episode); **CLEAR on successful LLM response** (fresh clock per
episode).

### Phase B — Classify & park at the finalize seam

**3. Error-type classification.** `_classify_error_type` (`message_processing_errors.py:72-148`):
map `ProviderRateLimitError` → `error_type='rate_limit_exhausted'` (leave the dead `'rate_limit'`
branch in place). Thread `error_type` additively into the lifecycle event payload
(`data['error_type']` at `:284-289`; the observer reads the dict at `job_feedback_observer.py:899-915`
— no error-text parsing).

**4. Envelope variants** (`error_reporting.py`):
- **(a) PARKED notice** — informational: "work parked: provider rate-limit window; auto-resumes by
  {deadline}; do NOT spawn replacements".
- **(b) TERMINAL at deadline** — critical severity, window exceeded.

During an active episode, **suppress the standard per-attempt error report** (the parked notice
replaces it — kills replacement-spawn storms).

**5. Observer branch.** In `_finalize_job_db_sync` Step 1 (`job_feedback_observer.py:3249-3302`),
inside the existing `WriteGuardSession`, keyed on `error_type=='rate_limit_exhausted'`:
- `now < deadline` → **PARK**: guarded requeue (atomic_retry-style UPDATE:
  `admission_state='queued'`, `next_retry_at=now+cadence±jitter`; **preserve** episode columns and
  `retry_count` — deferrals do NOT consume `max_retries`), stamp episode columns if unset
  (deadline = first_seen + window; first_seen from instance anchor, fallback=now), send the parked
  notice, release the instance as today.
- `now ≥ deadline` → **TERMINAL**: `terminal_reason='rate_limit_deadline'` (**new** enum value at
  `models.py:392`; MUST verify against the `_derive_legacy_status` canonical map AND
  `reconcile_turn_mirror` MIRROR_SET; update additively if required), critical envelope.

**6. Migration.** Additive nullable columns `first_rate_limit_at TEXT NULL`,
`rate_limit_deadline_at TEXT NULL` on `job_queue_items`. Ordered checksummed SQL migration, PG +
SQLite parity. `atomic_retry` must NOT clear them on requeue; success finalize, DLQ replay
(`dlq.py:292`, `:431`) and manual `POST /jobs/{id}/retry` DO clear them (fresh episode).

### Phase C — Re-dispatch on existing machinery

**7. Scoped scheduler auto-enable.** `RetryScheduler` wakes rate-limit-parked jobs even with the
global default-off; ordinary-retry default-off semantics unchanged. Parked jobs are exactly the
`find_retryable_jobs` shape (queued + `next_retry_at` ≤ now); add a filter if needed
(`retry_scheduler.py:164-197`).

**8. Re-dispatch rides the revive path.** The `send_message` revive path
(`instance_messaging.py:1486-1510`) with `is_retry=True` → checkpoint resume
(`task_processor.py:352`). Window not lifted → attempt fails fast (~2 min) → re-parks. Success
after the episode → clear episode columns + instance anchor atomically in success finalize.

---

## 5. Config

(`daemon/config.py` + `config.yaml` mirroring, ~:85-156)

- `JobSystemConfig`: `rate_limit_window_seconds=18000`, `rate_limit_retry_cadence_seconds=900`,
  `rate_limit_retry_jitter_seconds=90`.
- `LLMConfig`: `rate_limit_body_patterns` `list[str]` default `['all models rate limited']` via
  `Annotated[list[str], NoDecode]` + `_parse_csv_or_json_list` (`config.py:50-83`).

---

## 6. Compatibility guarantees (overlay, not replacement)

- **L0 SDK / L1 tenacity / L2 HA failover for 429/5xx/timeouts: unchanged** — the subclass is
  additive; ordinary `429 Rate limit` brownouts keep surviving on the fast path.
- **Task retry (L3) + job retry (L4) for non-rate-limit failures: unchanged**; `max_retries`
  semantics preserved; episode deferrals are deadline-bounded and budget-free.
- **Pause/resume, stale-recovery, compaction, LoopBreaker: untouched by construction** (parking is
  out-of-band at the job level; each attempt is a fresh short task; cancellation branches
  `task_processor.py:394-421` take precedence).
- **DLQ replay + manual retry APIs: additive** (clear episode fields).
- **`terminal_reason` canonical map + 8 mirror tables**: the new enum value requires a MIRROR_SET
  check (implementation risk, tracked in §9).

---

## 7. Approved decisions (user, 2026-08-26)

1. Episode deferrals bypass `max_retries` (the deadline is the bound).
2. Scoped scheduler auto-enable for parked jobs.
3. Suppress per-attempt parent error reports during an active episode; one parked notice + one
   terminal at deadline.
4. Detection patterns configurable, default `['all models rate limited']`.
5. Manual retry + DLQ replay start a fresh episode.

---

## 8. Success criteria / acceptance

1. 429 "All models rate limited" (pattern-matched) → `ProviderRateLimitError`; plain 429/5xx
   classify byte-identically to today (regression test).
2. Exhaustion inside the window → job parked (queued + future `next_retry_at`), `retry_count`
   unchanged, episode columns stamped, parked notice sent (no standard error report).
3. `now ≥ deadline` → `terminal_reason='rate_limit_deadline'`, critical envelope, DLQ-eligible.
4. Scheduler wakes parked jobs with the global scheduler default-off unchanged for ordinary
   retries.
5. Success after the episode clears episode state (columns + instance anchor).
6. Anchor survives daemon restart (persisted) and resets after clean success.
7. Targeted tests green; full suite untouched (tester's domain).

---

## 9. Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| `terminal_reason` enum addition breaks canonical map / MIRROR_SET drift | High | Medium | Verify `_derive_legacy_status` + `reconcile_turn_mirror` MIRROR_SET; additive updates only; targeted test on legacy derivation. |
| Scheduler scoping leak (ordinary retries auto-fire when they shouldn't) | Medium | Low | Scoped filter keyed on episode columns; targeted test with global default-off. |
| Anchor stamping seam invasive (graph client lacks instance identity) | Medium | Medium | Stamp at the `agent_node` catch where context exists; fallback=now at finalize (documented precision loss). |
| In-process thundering herd on wake | Medium | Low | 900s cadence ± 90s jitter. |
| Suppression hides real errors if classification false-positives | Medium | Low | Pattern list is exact-substring, default narrow; parked notice includes deadline + original error details. |

---

## 10. Open questions / future work (non-blocking)

- Respect provider `Retry-After` header when present (needs header capture in the classifier —
  deferred).
- Multi-replica coordination of the episode anchor (single-daemon scope today — deferred).
- Optional in-process `RateLimitRegistry` for sub-scheduler herd protection (deferred).

---

## 11. Implementation notes

- Branch `feature/rate-limit-episode-parking` off `latest`. **A prior coder dispatch was TERMINATED
  before producing work — check for a partial `feature/rate-limit-episode-parking` branch before
  starting fresh.**
- Dev deps via bare `uv sync` (PEP 735 dependency-groups; NO `--extra dev`).
- Targeted tests only (classifier detection; observer park/deadline branch; anchor set/clear;
  config parsing); the full suite belongs to the tester.
- Estimated effort: **LARGE — single coder** (the chain is interdependent; no safe parallel
  partition).
