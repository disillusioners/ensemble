# HANDOFF — job-watch-restart-persistence (sonic lane)

Commission: read-only investigation + design sketch — "Do job watches survive a daemon restart, and what watch-path state is memory-only?" Emperor decision input; docs-only; NO code/config/migration changes; no release ceremony.

Branch: docs/job-watch-restart-persistence (this file lands on its tip). Base: origin/latest a1130e257a. Deliverables in this directory: REPORT-1a-watch-storage.md, REPORT-1b-restart-behavior.md, REPORT-1c-memory-only-inventory.md, DESIGN-SKETCH.md.

## Files touched across modules
- Module A (docs/jwr-storage @ 7a140e0b0): .agents/shared/planning/sonic-team/job-watch-restart-persistence/REPORT-1a-watch-storage.md (239 lines)
- Module B (docs/jwr-restart @ 1971919dc): .agents/shared/planning/sonic-team/job-watch-restart-persistence/REPORT-1b-restart-behavior.md (183 lines)
- Module C (docs/jwr-memory @ 790fd351f): .agents/shared/planning/sonic-team/job-watch-restart-persistence/REPORT-1c-memory-only-inventory.md (82 lines)
- Module D (docs/jwr-design @ 0426ce87e): .agents/shared/planning/sonic-team/job-watch-restart-persistence/DESIGN-SKETCH.md (96 lines)
- Integrator: .agents/shared/planning/sonic-team/job-watch-restart-persistence/HANDOFF.md (this file)
- No source, config, migration, or test file touched (read-only commission; fenced in every brief; verified via per-module pathspec-limited single-file commits).

## What changed
Docs-only. The integrated decision input, aggregated:
1. Watch registration is ALREADY DB-persisted: job_watchers is the single storage behind all five tool surfaces (watch_job/watch_jobs/watch_mission/unwatch_job/list_watched_jobs) — there is NO in-process watch registry (REPORT-1a §0/§2). stored_watches is a usage-limit-lane-scoped quarantine only; timeout lane, stale-recovery, ordinary claiming, S15, zombie-GC stay outside it (REPORT-1a §4 — matches deferred-debt (a)/(b) with zero drift).
2. Watches DO survive restart at the registry level. Boot = idempotent table create + ONE terminal-reconcile scan (api.py:3121 reconcile_terminal_watches) replaying missed terminal [JOB_EVENT]s + WatchReconcileSweepService first tick (immediate, then 300s) + recovery finalize paths that notify (REPORT-1b §1–§2). "Terminal DURING downtime" is vacuous: the daemon is the sole writer (REPORT-1b §2(ii)). There is NO question-pack-style boot re-hydration — and none is needed: there is no RAM watch state to rehydrate (REPORT-1b §4). Enqueued-but-undelivered [JOB_EVENT]s are recovered by message_queue boot recovery EXCEPT rows targeting terminal instances (deliberately stranded, revive hazard).
3. What IS memory-only (Emperor's suspicion, confirmed + extended): InstanceManager._pending_injections RAM FIFO (manager.py:1782; job_inject is one of four WRITERS, not the queue itself), DependencyBus RAM state incl. the documented child-error→COMPLETED crash edge, the claim→enqueue in-flight window (row CAS-spent, envelope unwritten = silent loss; S15 covers exceptions only, not process death), LiveEventHub SSE subscribers, PP1 dedupe set, sweep streak/inflight dicts — 22 states tabulated (REPORT-1c). CORRECTIONS to briefed assumptions: per-instance busy guards are DB-backed and survive restart; the charter's "in-memory busy/counter locks" framing does not apply to them.
4. PP1 zero-watcher terminal-fire (v0.26.4 running-zombie records): ORTHOGONAL to restart persistence — the incident was a DB watcher-row DELETE, not RAM loss; the two converge only on the durable job_completed re-fire anchor (REPORT-1c final section).
5. DESIGN-SKETCH (deliverable 2): extend job_watchers with mission_instance_id (not a third table — avoids the three-writer contention the Option A matrix ruled out); RECOMMEND deliver-once at boot (duplicates tolerated BY DESIGN: watcher prompt contract says the FIRST [JOB_EVENT] is the signal, later ones echoes — act once; anchored at agents/_prompt_system/innate-skills/job-orchestration/skill.md:12); TTL/cleanup rides WatchReconcileSweepService (watcher-instance-terminal reap + age TTL reusing the 6h MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS precedent); blast radius additive-only, no frozen contract touched.
6. PREMISE CORRECTION for the Emperor: git shows 29651c06e (watch-loss fix) IS an ancestor of tag v0.31.42 — the standing project note "live :9797 vulnerable until promote" is stale w.r.t. that tag; empty diff v0.31.42..HEAD over watch-path files (REPORT-1b §5). Running-daemon version itself was out of fence.

## What is UNTESTED
- All runtime behavior: NO daemon boot, NO pytest, NO DB contact anywhere in this lane (read-only fence). Every finding is a static source trace anchored at a1130e257a.
- Running live-daemon version not verified (needs a probe; fenced).
- Timeout-lane silent-loss family: identified as parked risk (REPORT-1a risk (a)); no runtime evidence collected — decision input says whether closing it is a precondition.
- Integration seams unexercised: cross-module consistency was human-read + anchor re-verification only (integrator verified api.py:3121, manager.py:1782, mission_live_guard.py:327 directly at the integration tree); no automated doc cross-check exists.

## Known risks
- Anchor drift is real and frequent in this area: each report carries its own re-anchor table (REPORT-1a §6; REPORT-1b §5.3; REPORT-1c inline). Residual drift INSIDE the merged set: DESIGN-SKETCH cites api.py:2916 (actual :3121) and manager.py:1634 for the C2 checkpoint comment (C locates C2 at manager.py:5227-5229); substance unaffected — verified by integrator at the merged tree.
- DESIGN-SKETCH ASSUMPTIONs: RESOLVED at integration — "evaluate_mission_terminal" does not exist; canonical is evaluate_mission_live (mission_live_guard.py:327; D's flagged resolution confirmed by integrator grep). LEFT OPEN for the Emperor (not silently decided): revived-instance watcher re-arm scope; existence/shape of a watcher-keyed CAS reap method; cap-count semantics after reap; state-column-on-DELETE-table hybrid.
- Pre-existing latent (not introduced here): reconcile_terminal_watches call at api.py:3121 is NOT wrapped in try/except — an exception aborts boot (REPORT-1b §6.4).
- Terminal-instance stranded [JOB_EVENT] rows across reboot — durability contract question left open (REPORT-1b §6.3).
- stored_watches records original_watch_events only; the post-arming ARMED event set is not durable anywhere (REPORT-1a §4 nuance).
- job_watchers has NO CREATE TABLE migration (predates the zero-exceptions house rule; created via metadata.create_all at api.py:1358) — future commissions must not assume a baseline .sql exists.
- POK job-lookup-01 wording needs a leader-owned KB correction: 8 FUNCTIONS / 14 record.work_id code sites, and watch_jobs (4 sites) is missing from the POK enumeration (REPORT-1a §5/§7).
- Plan-open explore() timed out after 300s and was NOT retried (one-call bound); briefs were seeded from commission context + committed prior-lane records instead — residual risk: KB-held experience not reflected in the reports.

## How to verify
On the merged tree (docs/job-watch-restart-persistence == origin/latest after push):
- git log --oneline -10   # four module commits + four merges + handoff
- ls .agents/shared/planning/sonic-team/job-watch-restart-persistence/   # 5 files
- Spot-check anchors: grep -n "reconcile_terminal_watches" daemon/api.py (expect :3121); grep -n "_pending_injections" daemon/manager.py | head -1 (expect :1782); grep -n "def evaluate_mission_live" daemon/services/mission_live_guard.py (expect :327)
- git merge-base --is-ancestor 29651c06e v0.31.42; echo $?   # expect 0
- Read order for the Emperor: REPORT-1a → REPORT-1b → REPORT-1c → DESIGN-SKETCH.

## Bash-raises used
none — every command ran under the default <=600 s rule.

## Complexity self-assessment
medium (assessed pre-dispatch at §2.5; proceeded without handover pause). Read-only investigation + docs-only outputs, four disjoint modules, all merges clean (zero conflicts, no bounces, no re-dispatch loop). No user/Ari override occurred; no verdict softening.
