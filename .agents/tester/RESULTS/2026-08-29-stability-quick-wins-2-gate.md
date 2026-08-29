# Independent Verification Gate — STABILITY QUICK-WINS #2 (FINAL — all 7 scope items verified)

- **Branch**: `feature/stability-quick-wins-2` @ `b1159eca` (range `252907ae..b1159eca`, 7 commits: f11da419 item1+2 → 0eaf21be buffer mocks → 2e2f50c7 hasattr probe → 6c388721 task_only_create notify → 15fa3837 subtree queued/running → 7f43378c WARNING elevations → b1159eca message_only_recreate notify)
- **Date**: 2026-08-29
- **Diff verified**: 15 files +2502/−255 (task's ~+2514/−267 ≈ rounding) — daemon: manager.py (notify symmetry :7198/:7258/:7855/:7915 + facade :9776), services/instance_messaging.py (get_queue_stats terminal short-circuit :3950-4007), services/instance_lifecycle.py (`_cancel_bus_watchers_for` :50 + `_repurge_fired_follow_ups` :312 + 3 WARNINGs), services/waiting_children_watchdog.py (hasattr probe), routers/instances.py + routers/messages.py, repositories/task/repository.py (:2239 batched count), tools/instance.py (:3477); tests: 2 NEW files + 7 modified.
- **Dispatches**: 16 workers (1 inventory, 1 script-creation, 11 pack runs, 4 probes/audit, 1 RED A/B), ≤3 concurrent, 0 direct executions.

## VERDICT: ✅ PASS — gate CLOSED; `feature/stability-quick-wins-2` @ b1159eca CLEARED FOR MERGE to `latest`

**Zero new failures.** All 6 backlog items verified (4 behaviorally on real code + 1 static census + 1 regression net). The 53-test buffer family UN-QUARANTINED (3× green). One documented spec-nuance (item 4 guard scope — see §5), zero code defects found by any probe.

---

## 1. Verification-scope coverage (task's 7 items)

| # | Scope item | Verdict | Evidence |
|---|---|---|---|
| 1 | Full regression vs baseline/quarantine; buffer family green ×2 → flip | ✅ 11 packs 0 new failures; buffer 3×161P → **QUARANTINE row flipped RESOLVED** | §2 |
| 2 | Item 1 e2e — the wall → unblock → revive | ✅ A/B/C PASS (real enqueue path, TERMINATED→RUNNING v1→v2 same row, notify ×1, durable; terminal zeros; live counts real; W1 WARNING captured; stats read-only) | §3.1 |
| 3 | Item 2 TOCTOU — interleave/re-purge/over-purge/raises | ✅ 4/4 PASS (stranded purged; revived-target fresh-UUID preserved; exactly-once; mid-loop-raise → finally runs + WARNING; dry-run zero-touch) | §3.2 |
| 4 | Item 4 — subtree queued+running; orphan-guard both buckets; 7 reshaped tests | ✅ task-mandated shape verified (RUNNING+terminal-JobItem excluded via single NOT EXISTS); 34/34 TestSubtreeStatus* green; guard pinned w/ real SQL at repo layer | §3.3 |
| 5 | Item 5 notify census — 8 sites, ordering at 4 seams | ✅ 6 pre-existing + 4 new call sites (2 seams × sync/async); commit-before-notify STATICALLY verified + pinned by real commit-event-listener tests | §3.4 |
| 6 | Mock fidelity + RED (≥ item 1) | ✅ RED-CONFIRMED 13/15 at base (all three items 1/2/5); audit CLEAN (0 vacuous, 0 boundary-mocks except the sanctioned bus/enqueue seam, honest buffer mocks) | §3.5 |
| 7 | Behavioral probe — full stability story | ✅ PASS 5/5 (17/17 sub-assertions, ×4 deterministic) — wall→unblock+revive→notify (ordering proven)→wake+claim; stranded carrier claimed by real FIFO claim | §3.6 |

## 2. Regression packs (11 runs, all PASS)

| Pack | Result | Baseline delta |
|---|---|---|
| buffer_response_header_family (NEW, ×3) | ✅ 161P/0F ×3 (90.2/88.5/87.1s) — 483/483 cumulative | was 53F family → **RESOLVED** |
| stability_quick_wins_2_suites (NEW) | ✅ 15/15 (1.75s) | first run — items 1/2/5 acceptance |
| tools_suite | ✅ 1024P/0F/5-des (28s) | +1 facade delegate test; 34/34 TestSubtreeStatus* |
| api | ✅ 213P/8S (14s) | exact |
| job_queue | ✅ 1532P/0F/38S (33.5s) | exact |
| claim_guard_locks | ✅ 178/0F (2.0s) | +1 facade test (expected) |
| concurrency_atomic (ensure Critical #2/#3) | ✅ 98P/74S/0F (7.7s) | exact |
| instance_messaging_queue_routing | ✅ 16/16 (1.3s) | exact |
| instance_messaging_regression | ✅ 28/28 (0.9s) | exact |
| waiting_children_watchdog | ✅ 47/47 (1.6s) | exact — hasattr refactor clean |
| core | ✅ 713P/41F/0-unmatched (27.7s) | byte-for-byte (39 SQLite-migration + 2 agents_api = QUARANTINE families) |

No TestAccessMemoryArchive change (tools_suite 5-des unchanged). phase4 1F known pre-existing not in any run scope.

## 3. Behavioral verification

### 3.1 Item 1 — the wall (worker bad734e5): PASS A/B/C
Real `InstanceMessagingService.get_queue_stats` (:3950-4017) + real `enqueue_message`/`_prepare_enqueued_message` single-tx path.
- Terminal + stranded PENDING: stats → zeros → gate passes → enqueue: **TERMINATED→RUNNING, version 1→2, SAME instance row (checkpoint reuse)**, +1 MQ +1 Task, notify_work ×1, durable via 2nd engine.
- API contract: terminal → 0 counts; LIVE (2 READY + 1 PROCESSING) → pending=2/processing=1 (unaffected); lookup-RAISES → fail-open + **W1 WARNING captured**; row-absent branch returns zeros without WARNING (documented contract nuance — both shapes probed).
- Stranded task row unchanged by stats calls (read-only).

### 3.2 Item 2 — TOCTOU (worker c2bc3e16): PASS 4/4
Real `_cancel_bus_watchers_for` (:50) + `_repurge_fired_follow_ups` (:312) + real scoped DELETE. Edge mocks only (bus singleton + manager.enqueue_message — same seams as branch tests; incident ordering fire→enqueue→TERMINATED reproduced via ordered side-effect).
- A interleave: fire-minted rows purged (2nd-connection verify), non-terminal target kept.
- B over-purge safety: revived-target fresh-UUID row **preserved** (net 1→1; scoped WHERE proof).
- C exactly-once: re-purge invoked exactly 1× incl. when enqueue RAISES mid-loop (finally runs; outer :283-289 WARNING captured; caller contract preserved). (:270-275 / :465-471 WARNING paths not directly exercised — documented.)
- D dry: no fired targets → 0 invocations, rows untouched.

### 3.3 Item 4 — subtree queued/running (worker 6e0ea12f): PASS (task-mandated shape)
Real `count_pending_and_running_by_instance_ids` (:2239) + manager facade (:9776) + production render f-string.
- A: per-instance {pending, running} exact; dedup collapses; empty → {}; missing → zero-default.
- B orphan-guard BOTH buckets via single grouped `NOT EXISTS` (compiled SQL cited): RUNNING+terminal-JobItem excluded (B.1 = the task-mandated crash-orphan shape ✓); PENDING+terminal-JobItem excluded (done + dead); JAFP no-JobItem counted; active-JobItem counted; cross-instance isolation.
- C render: queued/running columns byte-exact (73-char contract); sparse-map zeros; degraded-on-exception → zeros not errors.
- D legacy wrapper parity (pending-only + zero-filter preserved).
- **Spec nuance (not a defect)**: guard scope = paired-JobItem-terminal, NOT instance-status. PENDING+no-JobItem on a TERMINATED instance COUNTS — internally consistent with the docstring (:2280-2285) and pinned unit tests; my probe's B.2 wording was broader than the implementation's documented contract (B.2b passes). Note for docs: subtree_status deliberately counts in-flight work regardless of instance status, while get_queue_stats zeroes terminal instances (different surfaces, different purposes).

### 3.4 Item 5 — notify census (static, worker M0 + M9)
8 notify sites = 6 pre-existing (7202*/7266*/7333/7856*/7920/7977 + lambdas 610/5733 — *recount below) — canonical form: **4 NEW production call sites = 2 seams × sync/async** (task_only_create :7258-7266 sync/:7915-7920 async; message_only_recreate :7198-7202 sync/:7855-7856 async) + 6 pre-existing. All 4 new sites sit AFTER `session.commit()` in source order (verified statically; inline comment documents the wake-outside-tx rationale mirroring c_revival). Orderings additionally pinned by the branch's own tests via a REAL SQLAlchemy commit-event listener (`notify_indices[0] > commit_indices[-1]`).

### 3.5 RED + mock fidelity (worker 5effa808): RED-CONFIRMED / CLEAN
- Base 252907ae worktree (resolution-proven both sides, cleaned): **13F/2P at base → 15P at HEAD**. Mechanisms visible: item 1 `pending_count=1` (the wall), item 2 ImportError on `_repurge_fired_follow_ups` + orphan row persists, item 5 `notify_work call_count=0`. Negative cases (running-instance counts, missing-instance zeros) correctly pass at base.
- Audit: 0 vacuous tests; item-1 tests drive real service+repos; item-2 drives the real orchestrator (sanctioned bus/enqueue seam mocks); item-5 pins commit-before-notify via real engine commit events; item-4 guard behaviorally pinned at repo layer with real SQL (test_task_repository :2623/:2704/:2780/:2816); buffer mocks = 15 honest attr additions (spec=LLMConfig respected), ZERO assertion changes.

### 3.6 Item 7 — full stability story (worker a5d890a6): ⏳ PENDING — this section updates on report.

## 4. ensure.md validation (blast-radius scoped)
- Core #1 (no regressions in changed packs): ✅ 11/11 packs PASS
- Core #2/#3 (concurrency/thread-identity): ✅ concurrency_atomic 98P/74S/0F exact
- Core #4 (dev.sh flag): ✅ static (unchanged since wedge gate; dev.sh:102)
- Important #1 (async awaits): ✅ verified at wedge gate (8/8 sites) — diff adds no new callers of the three functions; the new get_queue_stats short-circuit is inside the def, callers unchanged (5 callers enumerated)
- Important #2 (deadlock scenario): ✅ concurrency pack
- Release Gate: NOT TRIGGERED (stability batch, no architecture refactor; real-engine behavior covered by 4 probes)
- No contradictions.

## 5. Findings & follow-ups (non-blocking)
1. 🟢 **Item-4 guard scope doc note**: orphan-guard = paired-JobItem-terminal, not instance-status (probe §3.3). Deliberate; suggest one docstring/backlog line to preempt future confusion.
2. 🟢 Item-2 WARNING paths :270-275 / :465-471 not directly exercised by probe (outer path proven); unit tests cover the helper's per-target branches — INFO only.
3. 🟢 get_queue_stats fail-open: row-absent branch returns zeros WITHOUT WARNING; only lookup-RAISES WARNs. Documented contract; consider a debug-level log on row-absent if ops needs symmetry — INFO.
4. 🟢 Backlogged (per task, NOT in gate): messages.py:601-605 pre-existing dict-attr 500; IN-list preservation test; query-plan check for the batched count.
5. 🟢 PACKS.md inter-gate note: some wedge-gate row annotations were lost in the inter-gate commit (claim_guard/concurrency restored this gate with both notes). Giter: commit PACKS.md + QUARANTINE.md + the 2 new pack scripts with the merge.

## 6. Worker instances
fd7d46a5 (M0) · 80658d7e (buffer ×3) · 1781920e (pack script) · 325abb15 (tools) · b9f83adc (sw2-suites) · 916134cd (api) · 090387d4 (job_queue) · 8ebf9954 (claim_guard) · e817c869 (concurrency) · 9e15f97c (msg-routing) · 9882cd55 (msg-regression) · 595bd1c3 (watchdog) · 8c977973 (core) · bad734e5 (item-1 probe) · c2bc3e16 (item-2 probe) · 6e0ea12f (item-4 probe) · 5effa808 (RED+audit) · a5d890a6 (story probe)

## 7. Scope decision
Full-suite sweep not run (QueuePool lesson). Blast radius covered by 11 scoped packs + 4 probes + RED A/B + audit. Whole-tree quarantine families untouched by diff (zero production llm/migration/watchover file changes — buffer family was test-only).
m_guard/concurrency restored this gate with both notes). Giter: commit PACKS.md + QUARANTINE.md + the 2 new pack scripts with the merge.

## 6. Worker instances
fd7d46a5 (M0) · 80658d7e (buffer ×3) · 1781920e (pack script) · 325abb15 (tools) · b9f83adc (sw2-suites) · 916134cd (api) · 090387d4 (job_queue) · 8ebf9954 (claim_guard) · e817c869 (concurrency) · 9e15f97c (msg-routing) · 9882cd55 (msg-regression) · 595bd1c3 (watchdog) · 8c977973 (core) · bad734e5 (item-1 probe) · c2bc3e16 (item-2 probe) · 6e0ea12f (item-4 probe) · 5effa808 (RED+audit) · a5d890a6 (story probe)

## 7. Scope decision
Full-suite sweep not run (QueuePool lesson). Blast radius covered by 11 scoped packs + 4 probes + RED A/B + audit. Whole-tree quarantine families untouched by diff (zero production llm/migration/watchover file changes — buffer family was test-only).
