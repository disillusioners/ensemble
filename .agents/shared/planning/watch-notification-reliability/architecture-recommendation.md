# Architecture Recommendation — Watch-Notification Reliability (D1+D2) & Watch/Await Vocabulary (D3)

**Status: PROPOSAL — user gate (Debug workflow Phase 3→4). No implementation has been done.**
Date: 2026-09-19 · Prod context: v0.13.3 @ a6442bff · Incident: caller cde5017f, job_watchers row 94463712, lifecycle event 77148
Provenance: 🏛️ Council (skill `resilience-design`; councilors agentic cbb79472 + coding 1598d6bd, governor a51d431d) for Q1; worker (skill `trade-off-analysis`, fbe6c0fc) for Q2; synthesized by architect with independent spot-verification (§9).

---

## 1. Executive Summary

Two decisions, one architecture:

1. **D1+D2 — notification reliability: ship the periodic watch-reconcile sweep + full instrumentation package NOW (user-gated); keep the durable event-outbox as committed target state, telemetry-gated.** The incident class is *demand-side stranding*: a durable `job_watchers` row (the truth) waits on a best-effort in-memory event hop (a latency optimization). A periodic `reconcile_terminal_watches` sweep (`job_queue_service.py:460`) makes **every loss mechanism downstream of row state self-heal within one interval**, and the existing CAS claim primitive (`watcher_repository.py:289-331`) guarantees exactly-once across all notify paths — sweep, observer, outbox, any combination. Six code sites already promise this sweep in comments; today it runs **boot-only** (sole call site `daemon/api.py:992`). Dominant axis: **Risk** (worst case degrades to latency ≤ interval, never loss).
2. **D3 — vocabulary: standardize the MISSION noun.** `watch_mission` (durable) + `await_mission` (pure in-turn poll) become the work-wait pair; `watch_job` is retargeted to transport receipts. `mission_id == instance_id` per ADR-MISSION-01; the caller waits on the **work, never the receipt**. The watcher schema needs no migration (`job_id` is a plain indexed string — FK deliberately removed, `watcher_models.py:57-67`). Dominant axis: **Maintainability** (the tool surface becomes the last consumer aligned with the ratified two-layer vocabulary). Prerequisite under every option: fix the F-2 drift predicate (`job_recovery_service.py:991-993` docstring block; pattern finalize+`notify_watchers` at `:4037`) that closes a task-job `completed` while its mission is live.

**Heal now vs structural:** a controlled restart TODAY heals row 94463712 and revives cde5017f (boot reconcile claims + notifies `'settled'` via `enqueue_message` revival) — but heals the row, not the class. Phase 1 must ride the same rebuild cadence or a second stranded row will appear within hours.

---

## 2. Incident Ground Truth (confirmed, with verification status)

| Fact | Citation | Verified |
|---|---|---|
| Lifecycle event persisted, observer never consumed → `notify_work_watchers` never ran → row 94463712 never claimed → no `[JOB_EVENT]` → caller never revived | `job_feedback_observer.py:951` `_process_event`; `:1902-1999` post-commit loop; `:1226-1284` work-id resolution | 2 independent investigations; exact micro-mechanism **UNVERIFIED by design** — Phase-1 instrumentation pins it (§3.5) |
| Sole backstop `reconcile_terminal_watches` is boot-only | def `job_queue_service.py:460`; sole call `api.py:992` | ✅ architect grep (single runtime call site) |
| Six code sites promise a periodic reconcile sweep that does not exist | `work_notifier.py:140/:251/:476`, `stale_task_recovery.py:993`, `job_feedback_observer.py:1345`, `job_recovery_service.py:3942-3946` | ✅ architect grep — independently corroborates the council's central finding |
| Event bus has a fully silent zero-subscribers early-return AND a QueueFull drop path | `event_bus.py:332` `if not self._global_subscribers:`, `:348` `except asyncio.QueueFull`; second consumer named at `:148` (ResponseDispatcher) | ✅ architect grep |
| F-2 drift sweep finalizes task-jobs DONE `terminal_reason='completed'` + fires `notify_watchers` | `job_recovery_service.py:766/:1387/:4037/:4180`; docstring `:991-993` | ✅ architect grep (D3 worker cited :989-997 — same block) |
| Watcher `job_id` column is a plain string, FK removed — any work-handle fits without schema change | `watcher_models.py:57-58` comment, `:67` `job_id: str = Field(index=True)` | ✅ architect grep |
| `watch_job` keys job_id w/ durable registration + opt-in `mission_terminal`; `await_mission` keys mission_id, pure poll, registers nothing, F7-epoch-safe | `daemon/tools/job_queue.py:1544-1678`; `daemon/tools/missions.py:641-707` | worker code-read |
| ADR-MISSION-01: mission_id == instance_id; mirror wire close = `settled`; `completed`/`failed`/`cancelled` owned by mission layer; mission = divergence-0 read projection over `Instance.status`; F7 revive = new epoch, mission_id stable | `.agents/shared/planning/job-task-retrospective/decisions.md:14-69`; `docs/job-task-system.md` §6.6 | worker code-read (ratified ADR) |

**Framing (council consensus, unanimous):** the watcher row is the durable *demand*; the event stream is best-effort *supply*. Any fix that keeps demand durable and makes supply eventually-consistent closes the class; any fix that only hardens one supply hop leaves the class open.

---

## 3. Q1 — D1+D2: Notification Reliability Architecture

### 3.1 Options evaluated

- **(a) Periodic reconcile sweep** — generalize boot-only `reconcile_terminal_watches` into an always-on scheduler service (template: `JobLockSweepService` wiring `api.py:779-800`, `job_lock_sweep.py:93-100`). ~200 LOC, no schema change.
- **(b) Durable event-outbox + redelivery** — drainer over the already-persisting event rows (`create_event` persists BEFORE the lossy broadcast, `event_bus.py:175-191`): `notified_at` marker or side table, boot catch-up, `FOR UPDATE SKIP LOCKED` batching, GC. ~800–1500 LOC.
- **(c) Both, sequenced** — (a) now unconditionally; (b) as committed target state, gated on Phase-1 telemetry.

### 3.2 Five-axis matrix (council-synthesized)

| Axis | (a) Periodic sweep | (b) Durable outbox | (c) Both — A now, B gated |
|---|---|---|---|
| **Complexity** | **Low** (~200 LOC, no schema; reuses intact reconcile + sweep-service template) | **High** (state on hot shared `event` table, drainer, SKIP LOCKED batching, GC, replay-idempotency audit of `_process_event` incl. deferred-check duplication `job_feedback_observer.py:1127-1137`, migration+rollback) | **Medium-High** |
| **Scalability** | **Good** — O(active watches)/tick, PK-indexed; add status/age filter at ≥10³ watches (today's full scan `watcher_repository.py:371-379` fine at current cardinality) | **Good** — cursor O(delta), but doubles write path on a table shared by all `EventKind`s | **Good** — A bounds B's cutover window |
| **Maintainability** | **High** — joins the established periodic family (drift reconciler `api.py:598-637`, JobLockSweep, EligiblePendingSweep); notify truth stays singular; makes six lying comments TRUE | **Medium-Low** — a second delivery path that must stay semantically aligned with the in-memory path forever — the dual-vocabulary drift class that produced 7807e521 | **Medium** — one dedup primitive (CAS) but needs the status-token priority rule pinned |
| **Risk** | **Low** — CAS-idempotent; worst case = latency ≤ interval, never loss; no migration | **Medium** — replay ordering (old event replayed after newer transitions), hot-table migration, GC, backfill window | **Medium-Low** — CAS dedups A×B races; A covers B's transition window |
| **Cost** | Low | Medium-High | Medium-High |

### 3.3 Recommendation — Option (c), shaped as: **sweep + instrumentation NOW; outbox telemetry-gated**

- **Unconditional Phase 1:** `WatcherReconcileSweepService` — always-on (no new `ENSEMBLE_*` user-togglable flag, per owner policy 7d5285aa; tuning-only cadence knob, `Field(ge=1)` fail-fast, boot line, graceful shutdown, census-clean) calling the existing `reconcile_terminal_watches`. Cadence: councilors split 90s (JobLockSweep precedent) vs 300s (drift-reconciler precedent) — a defensible 90–300s tuning band; **lean 90s** for tighter worst-case at negligible DB cost. Ships **together with** the full instrumentation package (§3.5) — the counters validate the sweep and retro-pin D1.
- **Why (a) alone is honest:** it closes the entire *observed* incident class — any loss mechanism downstream of row state self-heals ≤ interval. The watcher row is deleted only after a successful CAS claim + notify; the sweep re-derives everything from current row state.
- **Why (b) stays in the target state:** `create_event` already persists before broadcasting (`event_bus.py:175-191`) — "the outbox half already exists"; a drainer is cheap to add later precisely because deferral sacrifices little option value. **Trigger for promotion:** Phase-1 counters show recurring in-memory loss with user-visible latency, or the same class hits the other `subscribe_all` consumer (ResponseDispatcher, `event_bus.py:148`). Note: B does **not** close N1 (§6) — the row is deleted post-claim; no replay can re-claim it — so N1 never forces an early B.

### 3.4 F-1 drift-sweep notification policy (adjudicated conflict)

**Verdict: F-1 stays silent — the periodic sweep IS its notify path.**
- Decisive evidence: the incident's own "restart WILL heal" fact proves end-to-end that `reconcile_terminal_watches` resolves and notifies exactly the F-1-finalized mirror-row class (per-kind `'settled'` token via `_derive_legacy_status`, `job_queue_service.py:549-563`). F-1 itself already lags ~300s behind the terminal write, so direct notify buys at most one sweep interval.
- **Condition (must-pass regression pin):** Phase 1 includes a test pinning that an F-1-finalized watched message-mirror is claimed + notified `'settled'` by the sweep. **If the pin cannot be made green → flip to direct-notify via `notify_work_watchers`** (CAS-safe either way). Unverified detail: `'settled'` membership in the sweep's terminal-state set.
- Either way: correct the six comments/docstrings that promise a periodic sweep which is currently boot-only (§2 table).

### 3.5 Observability design — pin D1, detect the class

**Seam pinning (mechanism → counter, not guesswork):**
- `event_bus_drops_total{reason=no_subscribers|queue_full, subscriber, kind}` — instrument BOTH drop paths: the **fully silent** zero-global-subscribers early-return (`event_bus.py:332-333`, zero log lines today — ranked #1 silent candidate, best fits the "nothing in the bracket" forensics) and the QueueFull drop (`:348-351`, WARNING exists, counter missing — ranked #3; this instance showed no QueueFull WARNING).
- Skip-reason enum counters at every observer silent-skip site: `not_lifecycle` (`:984`), `no_data` (`:988`), `no_ids` (`:1005`), `terminated_early` (`:1023`), `ctx_lookup_failed` (`:1056-1057`), `unknown_terminal_status` (`:1970-1971`), `notify_failed` per work_id (`:1993-1999`), `drain_swallowed` (`:617-619`).
- `observer_queue_depth` gauge + observer liveness heartbeat + periodic `[EventStats] published=X delivered=Y consumed=C skipped{...}` line — the **published-vs-consumed delta is the mechanism-agnostic D1 pin**.

**Class detection (mechanism-agnostic):**
- `notify_work_watchers_attempts_total{outcome=notified|skip_no_match|skip_lost_cas|skip_resolve_none|skip_held_mission|failed}` (`work_notifier.py:248`) — makes the **N1 silent-drop class** countable (claim-then-delete + catch-all `:472-486`).
- `[WatchReconcile] scanned=N notified=M unresolvable=K max_terminal_age=Xs` tick line + `reconcile_terminal_watches_claims_total{source=boot|sweep}` — attributes each heal to its layer.
- **Unclaimed-terminal-watch age WARN/gauge:** any active watch whose resolved work is terminal-but-unclaimed past 2× sweep interval → WARN (watch_id, work_id, age). Age computed in Python against `now_utc_naive()`; any SQL `now()-age` predicate binds `now_utc_naive() - timedelta` (timestamps convention; PG sessions UTC).
- Alert rules: drop-rate > 0 (🟡), drop burst > 5/min (🔴), queue depth > 500 for 30s (🟡), orphan age > 2× interval (🔴), notify-failure rate (🟡).
- Forensics convention: time-bracket log queries only — ensemble.log line numbers are not chronological.

---

## 4. Q2 — D3: Watch/Await Vocabulary

### 4.1 Options + weighted comparison (trade-off-analysis worker)

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Weighted |
|---|---|---|---|---|---|---|
| A: keep job_id keying + docs | 2 | 3 | 2 | 2 | 4 | **2.50** |
| B: key on instance_id directly | 3 | 4 | 3 | 3 | 3 | **3.20** |
| C: mission noun (naming layer over instance_id) | 4 | 4 | 5 | 4 | 3 | **4.10** |

- **A is empirically falsified:** the per-kind caveats already existed in ari/jober prompts; the caller was still lied to and improvised `job_continue` + `watch_job` mid-incident.
- **B shares C's key value** (mission_id == instance_id) but re-derives epoch/terminal semantics under the raw instance noun — a second, weaker vocabulary where the ADR already provides the ratified one.
- **C wins:** the caller model collapses to ONE work question with two primitives (await in-turn / watch durable); transport stays job-layer; the wrong-predicate trap becomes structurally hard.

### 4.2 Recommendation — Option C: standardize the MISSION noun

One-sentence rule: **job closes notify job watchers (transport); mission-terminal notifies mission watchers (work).** Mirror-`settled` closes NEVER fire mission watches. For the work question, watching a mirror job is never what a caller wants; for the transport question ("did my submission reach processing?") it is `watch_job`'s one legitimate remaining role.

### 4.3 Tool surface (proposal)

1. **`watch_mission(mission_id, events=['mission_terminal'], epoch_pin=None)` — NEW, durable.** Reuses `job_watchers` (string `job_id` column carries any handle; discriminator or prefix convention). Notify fires ONLY on mission-terminal (`Instance.status` terminal; DEAD admission overrides liveness per W4). HOLD semantics (mission liveness gate, `work_notifier.py:332-346`) become the DEFAULT, not opt-in. Epoch semantics must be specified before ship: fire-on-first-terminal; epoch reported in the notification (F7-safe).
2. **`await_mission` — UNCHANGED** keying, stays pure poll (M3-verified purity decision retained); documented as the in-turn sibling.
3. **`watch_job`/`watch_jobs` — RETAINED, retargeted to transport receipts** ("submission receipt" semantics); `mission_terminal` opt-in kept during migration, then deprecated.
4. **`job_continue` — retained as-is** (already mirror-gated, `job_queue.py:1163-1194`); optional future alias `mission_continue`.

### 4.4 Migration plan

- Phase 0 prerequisite (vocabulary-independent): **F-2 predicate fix** — the drift sweep must not finalize a task-job whose mission is live (`job_recovery_service.py` pattern f2). Re-keying alone does not fix the lie.
- Additive-first: ship `watch_mission` (after Q1 Phase 1 — see ordering risk 🔴 in §6), then prompt migration across ~9 files (ari/{rule,soul,tools_note}.md, jober/{rule,soul,tools_note,workflow}.md, `_prompt_system/innate-skills/job-orchestration/skill.md`): one rule — work question → `await_mission`/`watch_mission`; transport question → `job_get`/`watch_job`.
- Deprecate `watch_job(events=['mission_terminal'])` last; wire vocabulary per M3 (already landed).

---

## 5. Unified Phased Rollout (merged Q1+Q2)

| Phase | Ships | Content | Gate |
|---|---|---|---|
| **0 — heal NOW** | zero code | Controlled prod restart at low-traffic window: boot `reconcile_terminal_watches` (`api.py:992`, ordered before observer start `:1027`) claims row 94463712, notifies `'settled'`, revives cde5017f via `enqueue_message`. Verify: boot reconcile line (`api.py:993-995`), row gone, `[JOB_EVENT]` in caller queue, caller RUNNING. **Do NOT psql-delete the row — the revive IS the delivery.** | operator |
| **1 — structural** | first rebuild | (a) `WatcherReconcileSweepService` (always-on, cadence knob 90s lean); (b) full instrumentation package (§3.5); (c) **F-2 predicate fix** (mission-liveness gate on task-job finalize); (d) regression tests: incident replay (event persisted, observer unsubscribed → sweep heals ≤ interval), CAS race (observer+sweep same row → exactly one `[JOB_EVENT]`), F-1-finalized-mirror sweep-coverage pin (§3.4 condition), status-token priority pin (**current-row state authoritative**; observer's computed `_observ_default_status` `job_feedback_observer.py:1944-1967` is the fast path, never more authoritative than the row) | user |
| **2 — durable mission watches + hardening** | second rebuild | (a) `watch_mission` + mission-watch coverage in the SAME sweep service (one reconciler, both watch kinds); (b) prompt migration (~9 files); (c) N1 fix (re-insert watcher row on enqueue throw, or `claimed_but_not_notified` intermediate status reclaimable by the sweep); (d) retire/harden no-CAS legacy fallback `_notify_watchers_legacy` (`job_queue_service.py:395-458`, reachable only under partial wiring `:360-371`); (e) comment corrections (six sites) | user |
| **3 — telemetry-gated target state** | later rebuild | Durable outbox drainer over already-persisted events (`notified_at`/side table, boot catch-up, SKIP LOCKED batching, GC); ≥1-week soak; deprecate `watch_job` `mission_terminal` opt-in. **Trigger:** recurring in-memory loss w/ user-visible latency, or ResponseDispatcher-lane impact | telemetry |

---

## 6. Risks (merged, severity-ordered, deduplicated)

- 🔴 **Standing until Phase 1 ships:** the D1+D2 loss class is silent and recurring — every `watch_job` on any leader completion is exposed, with zero operator visibility; six code sites assume a reconcile sweep that runs only at boot. (Phase 0+1 close it.)
- 🔴 **Phase 0 restart alone is class-inadequate** — heals the row, not the mechanism; a second stranded row will appear within hours if Phase 1 does not ride the same rebuild cadence. Phase 1 must be pre-staged.
- 🔴 **Ordering constraint (D3×Q1 coupling): a durable `watch_mission` shipped before the periodic sweep + observer-loss hardening is WORSE than in-turn polling** (it would strand until restart). Enforced by the phase ordering above — non-negotiable.
- 🟡 **N1 latent class** — claim-then-delete + catch-all silently drops a watcher invisible to any reconcile (`work_notifier.py:472-486`; docstring `:136-142` contradicted by code `:162-180/:406-410`). (Phase 2c.)
- 🟡 **Legacy no-CAS fallback** double-notify window under partial wiring (`job_queue_service.py:395-458`). (Phase 2d.)
- 🟡 **Status-token divergence** sweep vs observer (`'settled'` vs `'completed'`) — first-claim-wins; needs the one-line priority rule pinned by test. Intersects D3: token follows the layer owning the watch.
- 🟡 **F-2 predicate fix changes finalize behavior** for task-jobs — needs its own test pass against the worker-pool resume path.
- 🟡 **Vocabulary migration surface** — ~9 prompt files; deprecation sequencing matters (additive tool first, opt-in deprecation last).
- 🟢 Observer `_process_event` idempotency under sweep races believed-safe (`:966-968`) — cover by the Phase-1 race test, don't assume.
- 🟢 Sweep scan cost — bounded; add status/age filter at ~10³+ active watches.
- 🟢 Future hygiene: consolidate 4–5 periodic services under one supervisor; `job_continue` → `mission_continue` alias once the noun is established.

---

## 7. Decisions Pending (user)

1. **Phase 0 restart timing** — low-traffic window approval (operational; heals cde5017f now).
2. **Sweep cadence** — 90s (lean; JobLockSweep precedent) vs 300s (drift-reconciler precedent). Tuning knob either way.
3. **Approve Option (c) shape** — sweep+instrumentation unconditional; outbox telemetry-gated (vs committing outbox now).
4. **Approve mission noun (D3 Option C) + new tool** `watch_mission` (tool-count budget); approve epoch-notification semantics spec in Phase 2.
5. **F-2 predicate fix scope/timing** — necessary under every option; bundled into Phase 1 rebuild.
6. **F-1 fallback posture** — accept silent-sweep verdict conditional on the regression pin; pre-approve direct-notify flip if the pin fails.

## 8. Open Questions

- **D1 exact micro-mechanism remains UNVERIFIED** (as dispatched). Ranked silent candidates: (1) zero-global-subscribers early-return `event_bus.py:332-333`; (2) observer silent-skip family; (3) QueueFull drop `:348-351`. The published-vs-consumed delta + both drop counters convert this ranking into a pinned mechanism at Phase 1.
- `'settled'` membership in the sweep's terminal-state set — unverified; the Phase-1 F-1 coverage pin decides (and gates the §3.4 verdict).
- FE/job-panel consumption of `mission_terminal` events — assumed agent-tools-only (out of scope).
- `notify_work_watchers` HOLD semantics under concurrent revive (F7) — code-read only, not executed.

## 9. Evidence & Provenance

- **Council (Q1):** councilor-agentic (cbb79472) + councilor-coding (1598d6bd), skill `resilience-design`, independent read-only verification, unanimous on: demand-side-stranding framing, sweep as immediate structural fix, restart-now heal, D1-mechanism-unverified→instrumentation-pins, CAS as universal idempotency primitive, always-on services + tuning-only knobs. Disagreements (outbox commitment, F-1 policy) adjudicated in §3.3/§3.4 with flip conditions stated.
- **Worker (Q2):** fbe6c0fc, skill `trade-off-analysis`, code-first evidence base; weighted 5-axis comparison; Medium-High confidence.
- **Architect spot-verification (this synthesis):** confirmed by directory-scoped grep — sole boot call site (`api.py:992`), six promise-a-sweep comment sites, FK-free watcher string column (`watcher_models.py:57-67`), both event-bus drop paths (`event_bus.py:332/:348`), F-2 notify seam (`job_recovery_service.py:766/:4037` + docstring `:991-993`).
- **No files were modified by council, governor, worker, or architect beyond this proposal document.** All code citations are read-only.


---

# Engine Phase — Chokepoint Verdict & Fix Design

**Date: 2026-09-20 · Task 1 of the engine phase (pre-implementation gate; read-only). Provenance:** census worker `data-flow-design` (eabf20d3) + hook-design worker `resilience-design` (570edd0b), architect adjudication with spot-verification (`work_notifier.py:369` firing set confirmed verbatim). Follows §1–§9 above (toolset reshape shipped 925f8c11).

## 1. The empirical answer to "is event-driven good enough?"

**As the SOLE mechanism: NO. As the PRIMARY: YES — with two conditions (§4).**

The engine's event-driven primary **already exists**: the canonical chain `JobQueueService.notify_watchers` → `notify_work_watchers` (`work_notifier.py:118`) → CAS claim (`watcher_repository.py:289-352`) → `[JOB_EVENT]` enqueue is wired at 11 call sites (observer `:1250/:1417/:1989`, task_processor `:956-996`, recovery `:766/:1387/:4037/:4180`, DLQ `:360`, retry engine `:467`, stale-recovery `:1006-1008`, manager resume `:10781`). The missing piece is **not a new hook**. What the census proves:

**CHOKEPOINT: NO — 17 disjoint terminal-write points; 6 structurally silent** (+ `reconcile_turn_mirror`'s CASE writes, `task/repository.py:1281-1307`, as a probable 7th). Silent sites: Fix-B inline mirror finalize (`repository.py:2129`, log `:2357` — **the 966e725e path**, unique log-string match), F-1 sweep (`:2385`), Fix-B legacy reap (`:2680`, one-time D2-exempt), `batch_cancel_queued` (`:3846`), `force_finalize_orphan` (`:4034`), pattern-f1 DEAD (`job_recovery_service.py:3825`). Notify is **never transactional anywhere** — always post-commit, caller-chosen; the observer-driven sites are the most atomic (WriteGuardSession bundles JobItem+Instance+lock, `job_feedback_observer.py:3173-4043`).

**Adjudication of the worker conflict** ("all writes flow through the observer" vs "6 structurally silent"): both are right about different families —
- **Instance-backed silent writes** (Fix-B inline, F-1, legacy reap): the observer's independent terminal fan-out re-covers them **if its instance-lifecycle event is consumed** — and the original D1 incident (event 77148 persisted, never consumed) is the demonstrated counterexample with no self-heal.
- **Instance-less terminal writes** (batch cancel over queued jobs, orphan finalize, f1-DEAD, turn-mirror CASE): **no event ever exists** — no hook discipline can fire what nothing observes. Only row-state inspection (the sweep) sees them.

## 2. Exposure classes (named, with coverage)

| # | Class | Covered by hook-only? | Covered by sweep? |
|---|---|---|---|
| 1 | Crash window: commit lands, notify never fires (process death between) | NO — unbounded | ✅ retro-heal ≤ cadence |
| 2 | N1 enqueue-throw: CAS deletes row, `enqueue_message` raises (`work_notifier.py:472-486`) | NO — row gone | ✗ (sweep re-raises the same throw; moves, not closes) |
| 3 | Structurally-silent instance-less writes (sites 6/7/14-f1/10) | NO — no event exists | ✅ |
| 4 | Future finalize sites added without notify discipline | NO (no compile-time guard) | ✅ |
| 5 | Already-stranded rows (966e725e-class retro-heal) | NO | ✅ — uniquely |
| 6 | dead_letter HOLD firing-set gap (`work_notifier.py:369`) | ✗ — hook routes through the same gate | ✗ — sweep routes through the same gate |

## 3. The dead_letter gap — verified, BLOCKING

`work_notifier.py:357-375`: for `mission_terminal` opt-in rows, the firing set is literally `if mission_live not in {"completed", "failed", "cancelled"}` — **`dead_letter` is excluded** (architect grep-confirmed). A mission that only reaches dead_letter (`dead_letter_service.py:202/:336`, `job_retry_engine.py:467`) leaves its mission watchers **held forever**: the row is preserved (`:374`), the claim never runs, and both the hook AND the sweep route through this same gate — **sweep-without-fix is a silent no-op that looks like coverage**, the exact failure shape of this incident class. One-line fix: replace the set with `not _work_status_is_terminal(mission_live)` (helper already imported pattern at `job_queue_service.py:35-37`), preserving non-terminal holds. Ship with a dead_letter-fire pin test.

## 4. Recommended shape (sequenced; user decides)

1. **FIRST (blocking):** the `work_notifier.py:369` firing-set one-liner + pin test. Sequencing is the point: scheduling the sweep before this fix produces false assurance on dead-lettered missions.
2. **Sweep floor:** schedule the EXISTING `reconcile_terminal_watches` (`job_queue_service.py:460-549`, boot-only at `api.py:992`) on the scheduler — wiring only, always-on per flag policy 7d5285aa, cadence as tuning knob (60s proposed, below JobLockSweep's 90s, to bound retro-heal windows). Covers classes 1/3/4/5 — including retro-healing any 966e725e-class stranded rows at activation.
3. **Hook discipline (contract, not new machinery):** every terminal site calls `await JobQueueService.notify_watchers(job_id, canonical_status)` post-commit — never `notify_work_watchers` directly, never wrapped in a swallowing try/except, status bridged via `_derive_legacy_status` when coming from `admission_state`. Optional latency add-on: add the call at the 3 instance-less silent sites (batch-cancel loop, force_finalize_orphan, f1) — correctness is already sweep-covered; hooks there only cut notify latency from ≤cadence to ~immediate.
4. **Probe outcomes:** (a) watch_mission runtime presence — prompts/meta verified shipped (ari soul.md:27/112/114, meta 1.2.0, category-mission registration `job_queue.py:2680`); operator checklist: resolved-toolset dump → `tool_help("watch_mission")` probe → boot census grep → minimal live exercise; ari not calling it is agent-choice/prompt-priority, not wiring. (b) job_create auto-watch mints ALL-6 events (`job_queue.py:835` → `add_watch` defaults, `watcher_models.py:13-16`) — pre-existing semantics, reshape did not change it; recommend NO change (back-compat) + document "mission-shaped work → watch_mission" in tools_note. (c) = item 1.

**Remaining exposure after this shape:** class 2 (N1 enqueue-throw) — accepted for now (the later telemetry-gated outbox phase, §3 of the original recommendation, is its eventual closure); turn-mirror CASE writes — sweep retro-heal only.

**Cost:** one line + pin test (item 1); scheduler wiring + boot line (item 2); prompt/doc touches (item 4b). No schema, no new services, no new flags.

## 5. Risk log (delta)

- 🔴 dead_letter firing-set gap — held-forever watchers; fix MUST precede sweep scheduling (§3).
- 🔴 No structural guard against future silent terminal writers (class 4) — the sweep is the only backstop; a census pin test (grep-spec over terminal-write sites asserting notify-or-exempt classification) would make the discipline testable — proposed as part of item 2's test suite.
- 🟡 N1 enqueue-throw window remains open (class 2) — accepted, outbox-phase closure.
- 🟡 60s cadence = O(active watches) scan per tick; add status/age filter if watch counts reach ~10³ (unchanged guidance from original recommendation).


---

# Engine Phase — Shape (b) Re-Cut: Event-Driven Completion

**Date: 2026-09-20 · SUPERSEDES the recommended-shape (§4) of the prior Engine Phase section above.** User decision: Item 1 (dead_letter firing-set) **landed as 51e38db7** — confirmed correct at HEAD (`work_notifier.py:369` now `not _is_terminal(mission_live)` over the full canonical set, pin test `tests/job_queue/test_work_notifier_n1_pin.py`) — not redesigned. Item 2 (sweep) **CANCELLED, never ships**. Governing principle (user, verbatim): *"polling-as-correctness is a smell — for the core job task mission system polling is fine as last effort, but for agent tools we are just proxying events to agent instances; no polling here."* Boot reconcile + core drift sweeps remain sanctioned last-effort. **No polling exists anywhere in this design.**

Provenance: census re-verification worker (`data-flow-design`, 086fea3d) + pattern/pin-test/observer worker (`structural-design`, 595aef05); architect adjudication.

## 1. Census currency (re-verified at HEAD, post ~50 commits)

`KNOWN_ADMISSION_STATE_WRITERS` (daemon/job_state/constitution.py) is **byte-identical a6442bff→HEAD**; all seven silent sites exist at the same lines (sole drift: f1-DEAD 3825→3826); **zero new terminal-write sites** in the delta (blast radius = observers/TZ binds/lock-scope only). 51e38db7 confirmed as above. The checklist below is current.

## 2. THE HOOK LIST — definitive implementation checklist

**Pattern exemplar (copy this shape):** `daemon/services/job_recovery_service.py:763-774` (`_fail_orphaned_job` — post-`_finalize_terminal`, canonical facade, literal token, 9 lines):

```python
if canonical_job_id is not None:
    stats["recovered"] += 1
    try:
        await self._job_queue_service.notify_watchers(
            job.job_id, "failed", error_message
        )
    except Exception as e:
        logger.warning(
            f"_fail_orphaned_job: notify_watchers failed "
            f"for {job.job_id[:8]}...: {e}"
        )
    return True
```

| # | Site (current line) | Action | Token (canonical) | Data at site | Notes |
|---|---|---|---|---|---|
| 1 | `finalize_mirror_job_at_completion` — repository.py:2129 (caller: task_processor.py:1053) | **HOOK** at caller, post-commit | `'settled'` via per-kind bridge (mirror row; column carries `terminal_reason='completed'` — bridge input, NOT the token) | `job_id` param, refreshed `job_after` | Instance-backed; single session.commit |
| 2 | `reconcile_terminal_message_mirrors` — repository.py:2385 (caller: job_recovery_service.py:872) | **HOOK** at caller, per returned row | `'settled'` (mirror rows) | per-row `_ReapedTerminalMessageMirror(job_id=...)` NamedTuples already returned | F-1 sweep; per-row commits; re-entrancy-safe (notify uses asyncio.to_thread) |
| 3 | `reap_legacy_mirror_zombies` — repository.py:2680 | **EXEMPT** — documented carve-out | n/a (`'orphan_retired'` ∉ `_TERMINAL_STATUSES` — firing it would require a misrepresenting token) | per-row NamedTuples | D2-exempt one-time cutover, ≤3 rows; docstring must state the carve-out; census entry `classification='exempt'` |
| 4 | `batch_cancel_queued` — repository.py:3846 (caller: job_queue_service.py:1279) | **HOOK — special shape** (below) | `'cancelled'` | ⚠️ returns ONLY rowcount — ids must be pre-SELECTed | Bulk, instance-less; POST /api/jobs/cleanup |
| 5 | `force_finalize_orphan` — repository.py:4034 (caller: job_queue_service.py:1322) | **HOOK** at caller | from `terminal_reason` arg (default `'cancelled'`) | `job_id` param; refreshed row via `session.get` | engine.begin atomic UPDATE+DELETE; hook after commit; rowcount=0 → skip |
| 6 | f1-DEAD `atomic_transition` — job_recovery_service.py:3826 (in `_pattern_f_finalize_dead`, :3701) | **HOOK** in-function, post-transition | `'dead_letter'` | `job_id` + `instance_id` in scope | Currently zero notify anywhere in the function |
| 7 | `reconcile_turn_mirror` — task/repository.py:1182 (CASE :1281-1325) | **HOOK** at method exit, post-`engine.begin` | per-kind bridge (`'settled'` for mirrors) | `work_id` param (== JobItem.job_id); result dict | 8 callers — CAS dedups overlap exactly-once; if notify noise shows, move to the 3-4 event-time callers (task_processor:2164/:2525/:2637/:4143) — which fire `notify_work_watchers` DIRECTLY (CAS-equivalent via the facade delegation, job_queue_service.py:377), not the canonical `notify_watchers` — implementation-time grep to confirm result shape before placement |

**6 hooks + 1 exemption.** All follow the exemplar; only site 4 deviates in shape.

## 3. Hook pattern spec (the discipline)

- **Placement:** post-commit at the **service/processor caller level** — repo methods stay pure write-only. Never in-transaction (an enqueue exception must never roll back a landed terminal write; the await must never hold a session). Matches all 12+ wired sites.
- **Delegate:** always `await JobQueueService.notify_watchers(job_id, token, error)` — never direct `notify_work_watchers` (the facade carries the wiring defense + legacy fallback, job_queue_service.py:321-388).
- **Tokens:** canonical only — `{completed, settled, failed, cancelled, dead_letter}`. Bridge via `_derive_legacy_status(admission_state, terminal_reason, job_type)` when the site knows raw values; per-kind dispatch is the source of truth (mirrors → `'settled'` regardless of column reason).
- **Idempotency:** the CAS claim (`watcher_repository.py:289-352`, atomic DELETE…RETURNING) makes dual-fire — hook + observer, or re-entrant recovery paths — exactly-once per `(job_id, instance_id)`. No site-level dedup.
- **Failure isolation:** try/except around the FULL await; `logger.warning` with `job_id[:8]` + token; never propagate; never swallow silently. `notify_status` token (`notify_completed`/`notify_failed:<Exc>`/`notify_skipped_no_service`) recommended for the caller's log line.
- **Guards:** rowcount=0 / write-returned-None ⇒ **no notify** (phantom events delete real watcher rows). Never fire non-terminal; never pass `progress=` from terminal sites; never omit the `await` (strands silently — D1 class).
- **Site-4 special shape (the only bulk site):** pre-SELECT the queued job_ids matching the UPDATE predicate → single atomic UPDATE (unchanged) → **re-SELECT the captured ids now in terminal state** → notify only those. The re-check closes the SELECT↔UPDATE race (a captured id that didn't transition must NOT be notified — a false terminal deletes a real watcher row and delivers a phantom `[JOB_EVENT]`). `RETURNING job_id` rejected: PG-only, breaks the SQLite boot test path.

## 4. Census pin test (REQUIRED)

- **Mechanism:** AST walk over `daemon/` collecting terminal-write shapes (finalize/transition method calls, `admission_state` terminal assignments + adjacent `terminal_reason` writes, bulk-SQL wrappers) + a regex supplement for `terminal_reason='<literal>'` shapes. AST chosen over pure regex (refactor-immune, precise shapes) and over runtime instrumentation (coverage gaps, cost).
- **Fixture:** `TERMINAL_WRITE_CENSUS` list in `tests/job_queue/test_terminal_write_census.py` — one entry per site: `{file, site, anchor, classification: hooked|exempt, hooked_at, reason?}`. The walker is the source of truth; the fixture is the allow-list.
- **RED path:** a discovered site not in the fixture fails with a message naming file:line + shape and the three remediations (hook it / classify hooked with `hooked_at` / classify exempt with reason).
- **Second test:** every `hooked` entry's `hooked_at` must point at a real `notify_watchers` call within ±5 lines (fixture cannot rot).
- **Update procedure:** new site → write hook → add fixture entry. Exemptions carry their reason as the on-call breadcrumb (site 3's entry cites this doc).
- **CI:** fast (<2s, no DB/daemon), `uv run python -m pytest tests/job_queue/test_terminal_write_census.py`, gates the standard matrix.

## 5. Observer question — definitive answer

**NO observer-feed change needed.** With all sites directly hooked: (i) the structural gap (instance-less writes never crossing the observer's lifecycle filter) is closed at the write sites; (ii) hook + observer dual-fire is CAS-deduped exactly-once — the observer becomes a redundant belt for watcher delivery; (iii) the observer remains **load-bearing for everything else** — sole `in_progress` channel (hooks are terminal-only by contract), ResponseDispatcher lanes, attestation `finalizer_counts_as_pending`, SSE/CompletionRegistry fan-out. Redundant-for-delivery ≠ removable. Zero changes to `job_feedback_observer.py`.

## 6. Accepted residuals (for the record)

1. **Crash-window** (finalize-commit ↔ notify): strands until next **boot** reconcile — user-sanctioned (boot reconcile = core-system last-effort; no periodic sweep exists or ships).
2. **N1 enqueue-throw window** (CAS deletes row, enqueue raises, work_notifier.py:472-486): future outbox phase ONLY if ever observed in telemetry; nothing scheduled.
3. **Convention reliance** (future sites forgetting hooks): mitigated by the census pin test — the discipline is testable, not aspirational.

## 7. Test plan sketch (for the developer)

1. **Per-site hook tests** (6): silent-site finalize (fixture-driven) → `notify_watchers` awaited with the canonical token → watcher row CAS-claimed exactly once → `[JOB_EVENT]` enqueued. Include the dual-fire case (hook + observer both fire → single delivery).
2. **Site-4 race test:** pre-SELECT captures id; concurrent transition makes UPDATE miss it → NO notify fires for that id (false-terminal guard).
3. **Census pin:** green with current sites + fixture; **red simulation** — temp file with an unlisted terminal write → RED with the remediation message; hooked-entry verification catches a stale `hooked_at`.
4. **Site-3 exemption pin:** docstring carve-out present; fixture entry `exempt` with reason.
5. **Re-entrancy:** sites 4/5/6 exercised from boot-recovery paths → notify fires once, no sweep re-trigger.

## 8. Risk log (delta)

- 🔴 **Site 4 false-terminal hazard** — the only hook that can BREAK correctness (phantom event + real row deletion) rather than merely miss; the re-SELECT verify guard is load-bearing, not optional.
- 🟡 Site 7 result-shape unverified at method exit — implementation-time grep before placement (8-caller overlap is CAS-safe but wasteful).
- 🟡 `_notify_watchers_legacy` partial-wiring path assumed unwired-in-prod (facade handles it; soft-fail acceptable).
- 🟢 Exemption accumulation in the fixture — reason strings are grep-discoverable breadcrumbs.
