# HANDOFF — job-watch-restart-persistence (sonic lane)

Commission: read-only investigation + design sketch — "Do job watches survive a daemon restart, and what watch-path state is memory-only?" Emperor decision input; docs-only; NO code/config/migration changes; no release ceremony.

Branch: docs/job-watch-restart-persistence (this file lands on its tip). Declared base: commit `a1130e257a` (origin/latest at lane open). Deliverables in this directory: REPORT-1a-watch-storage.md, REPORT-1b-restart-behavior.md, REPORT-1c-memory-only-inventory.md, DESIGN-SKETCH.md.

RE-DISPATCH #1 (fix wave): REVIEW NEEDS-REVISION + TEST PASS received; fix items A–H applied. DESIGN-SKETCH.md was fully RE-ANCHORED against the declared base `a1130e257a` (every anchor re-derived via `git show a1130e257a:<path>`; the canonical detached checkout `bf4bfde6e` is never an anchor source). The integration branch also absorbed the intervening base advance (origin/latest 044a6d20a → 96e3a94bf, unrelated lanes) via `merge --no-ff`; all deliverable blobs verified byte-identical across it.

## Files touched across modules
Wave 1:
- Module A (docs/jwr-storage @ 7a140e0b0): .agents/shared/planning/sonic-team/job-watch-restart-persistence/REPORT-1a-watch-storage.md (239 lines)
- Module B (docs/jwr-restart @ 1971919dc): .agents/shared/planning/sonic-team/job-watch-restart-persistence/REPORT-1b-restart-behavior.md
- Module C (docs/jwr-memory @ 790fd351f): .agents/shared/planning/sonic-team/job-watch-restart-persistence/REPORT-1c-memory-only-inventory.md
- Module D (docs/jwr-design @ 0426ce87e): .agents/shared/planning/sonic-team/job-watch-restart-persistence/DESIGN-SKETCH.md
Fix wave (RE-DISPATCH #1):
- Module B (docs/jwr2-restart @ b4195701f): REPORT-1b §5.1 exactness (+13/−2)
- Module C (docs/jwr2-memory @ 1ad17d2c4): REPORT-1c §3 row #13 claim-site relabel (+1/−1)
- Module D (docs/jwr2-design @ 199831fcb): DESIGN-SKETCH full re-anchor + review fixes (+39/−36)
- Integrator: HANDOFF.md (this revision) + base-advance merges
No source, config, migration, or test file touched (read-only commission; fenced in every brief; per-module commits are pathspec-limited single-file).

## What changed
Docs-only. The integrated decision input, aggregated:
1. Watch registration is ALREADY DB-persisted: job_watchers is the single storage behind all five tool surfaces (watch_job/watch_jobs/watch_mission/unwatch_job/list_watched_jobs) — there is NO in-process watch registry (REPORT-1a §0/§2). stored_watches is a usage-limit-lane-scoped quarantine only; timeout lane, stale-recovery, ordinary claiming, S15, zombie-GC stay outside it (REPORT-1a §4 — matches deferred-debt (a)/(b) with zero drift).
2. Watches DO survive restart at the registry level. Boot = idempotent table create + ONE terminal-reconcile scan (api.py:3121 reconcile_terminal_watches) replaying missed terminal [JOB_EVENT]s + WatchReconcileSweepService first tick (instantiation api.py:2183, immediate first tick, then 300s) + recovery finalize paths that notify (REPORT-1b §1–§2). "Terminal DURING downtime" is VACUOUS: the daemon is the sole writer; the only mid-downtime mutation class is a hard reboot mid-finalize, which startup recovery covers (REPORT-1b §2(ii) — the sketch now inherits this and does NOT list it as an open gap). There is NO question-pack-style boot re-hydration — and none is needed: there is no RAM watch state to rehydrate (REPORT-1b §1.7). Enqueued-but-undelivered [JOB_EVENT]s are recovered by message_queue boot recovery EXCEPT rows targeting terminal instances (deliberately stranded, revive hazard).
3. What IS memory-only (Emperor's suspicion, confirmed + extended): InstanceManager._pending_injections RAM FIFO (manager.py:1782; job_inject is one of four WRITERS, not the queue itself), DependencyBus RAM state incl. the documented child-error→COMPLETED crash edge, the claim→enqueue in-flight window (work_notifier.py:954-958 CAS claim → :1031-1045 notify-list assembly → :1097-1180 envelope+enqueue; row CAS-spent, envelope unwritten = silent loss; S15 covers exceptions only, not process death), LiveEventHub SSE subscribers, PP1 dedupe set, sweep streak/inflight dicts — 22 states tabulated (REPORT-1c). CORRECTIONS to briefed assumptions: per-instance busy guards are DB-backed and survive restart.
4. PP1 zero-watcher terminal-fire (v0.26.4 running-zombie records): ORTHOGONAL to restart persistence — the incident was a DB watcher-row DELETE, not RAM loss; the two converge only on the durable job_completed re-fire anchor (REPORT-1c final section).
5. DESIGN-SKETCH (deliverable 2) — substance re-verified correct by both review lanes; in the fix wave the sketch was RE-ANCHORED against the declared base a1130e257a and corrected: extend job_watchers with mission_instance_id (not a third table — three-writer contention quote lives at stored_watch_models.py:39-41); RECOMMEND deliver-once at boot (duplicates tolerated BY DESIGN: skill.md:11-14 — the FIRST [JOB_EVENT] is the signal, later ones echoes, act once; AND "a revived mission needs a fresh watch_mission" — the daemon revive path re-arms NO job-watch row (instance_messaging.py:2603-2608/:2715-2726), so auto re-arm would be convenience beyond contract, open question); TTL/cleanup rides WatchReconcileSweepService (watcher-terminal reap + age TTL reusing MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS=6h at mission_live_guard.py:221); MAX_WATCHES_PER_INSTANCE=50 at job_queue.py:116 enforced at FIVE arm sites (comparison lines 1614/2762/3063/4470/4534; count-fetch lines adjacent — the sketch names which is which); migrations dir count 137; blast radius additive-only, no frozen contract touched.
6. PREMISE CORRECTION for the Emperor: git shows 29651c06e (watch-loss fix) IS an ancestor of tag v0.31.42 and the watch-path diff v0.31.42..base is EMPTY — the standing project note "live :9797 vulnerable until promote" is stale w.r.t. that tag (REPORT-1b §5; running-daemon version itself was out of fence).

## What is UNTESTED
- All runtime behavior: NO daemon boot, NO pytest, NO DB contact anywhere in this lane across BOTH waves (read-only fence). Every finding is a static source trace anchored at a1130e257a.
- Running live-daemon version not verified (needs a probe; fenced).
- Timeout-lane silent-loss family: identified as parked risk (REPORT-1a risk (a)); no runtime evidence collected.
- Integration seams unexercised: cross-module consistency is human-read + anchor re-verification; the fix wave's integrator spot-check re-derived 10 sketch anchors at the pinned base object (all resolved exactly; stale-anchor grep over the sketch returns empty).

## Known risks
- ORIGINAL WAVE-1 DEFECT (now fixed, recorded for audit): the DESIGN-SKETCH's anchors were derived from the canonical DETACHED checkout (bf4bfde6e), not the declared base — ~13 sites + 5 anchors resolving to unrelated code. Full enumerated set that was wrong and is now corrected at a1130e257a: manager.py _pending_injections 1634→1782; api.py reconcile_terminal_watches 2916→3121; job_queue.py MAX_WATCHES_PER_INSTANCE 104→116; cap sites "4 @ 1538/2637/2928/4342/4406"→FIVE @ 1614/2762/3063/4470/4534 (comparisons); api.py service instantiation 2003/2038/2124→2183/2225/2299; claim CAS work_notifier.py:836-860→947-958 + watcher_repository.py:441-452 (two tiers); C1 buckets :773-803→881/888/901; migration count 130→137; attribution stored_watch_repository.py→stored_watch_models.py:39-41. Module D re-anchored the ENTIRE sketch against the base object in RE-DISPATCH #1; integrator spot-checked 10 of them. The earlier assurance "verified by integrator at the merged tree" is RETRACTED as false for the sketch (it covered only the two drifts then known).
- Fix-wave extras, honestly recorded: the reviewer's consolidated fix list itself carried a wrong path (.agents/tester/packs/…); module B verified the pack script's real location at base (test/packs/pok_cap_read_budget_independence_test.sh, mode 100755) and REPORT-1b §5.1 records it. Module D additionally found the old instance_messaging.py:2432 revive cite wrong at base (real sites :2603-2608/:2715-2726).
- Residual ASSUMPTIONs in the sketch (deliberately left open for the Emperor, not silently decided): watcher-terminal reap query method; no watcher-keyed CAS variant exists today (existing claim method is job-keyed); cap-count filter semantics if a state column were adopted; _ensure_postgres_columns acceptability for the column add; evaluate_mission_terminal has NO counterpart in daemon/ or tests/ at base (canonical is evaluate_mission_live, mission_live_guard.py:327).
- Pre-existing latent (not introduced here): reconcile_terminal_watches call at api.py:3121 is NOT wrapped in try/except — an exception aborts boot (REPORT-1b §6, item 4).
- Terminal-instance stranded [JOB_EVENT] rows across reboot — durability contract question left open (REPORT-1b §6, item 3).
- stored_watches records original_watch_events only; the post-arming ARMED event set is not durable anywhere (REPORT-1a §4 nuance).
- job_watchers has NO CREATE TABLE migration (predates the zero-exceptions house rule; created via metadata.create_all at api.py:1358).
- POK job-lookup-01 wording needs a leader-owned KB correction: 8 FUNCTIONS / 14 record.work_id code sites, and watch_jobs (4 sites) is missing from the enumeration (REPORT-1a §5/§7).
- Plan-open explore() timed out after 300s and was NOT retried (one-call bound); briefs were seeded from commission context + committed prior-lane records instead — residual risk: KB-held experience not reflected in the reports.
- origin/latest is live-moving today: advanced twice during this lane (044a6d20a → 96e3a94bf); the final push fetch-verified and fast-forwarded over the then-current remote head. If it advanced again post-push, the tip is ancestor-clean by construction (never force-pushed).

## How to verify
On the merged tree (docs/job-watch-restart-persistence == origin/latest after push); anchors pinned via `git show a1130e257a:<path>` (NOT the working checkout):
- git log --oneline -14   # wave-1 module commits + merges + fix-wave commits + merges + handoff
- ls .agents/shared/planning/sonic-team/job-watch-restart-persistence/   # 5 files
- Spot-checks: git show a1130e257a:daemon/api.py | sed -n '3121p' (reconcile_terminal_watches); git show a1130e257a:daemon/manager.py | sed -n '1782p' (_pending_injections); git show a1130e257a:daemon/tools/job_queue.py | sed -n '116p' (MAX=50); git show a1130e257a:daemon/services/work_notifier.py | sed -n '881p;888p;901p;954p' (buckets + CAS claim); git show a1130e257a:daemon/migrations/versions/ | grep -c '\.sql' (=137)
- git merge-base --is-ancestor 29651c06e v0.31.42; echo $?   # expect 0
- git diff v0.31.42..a1130e257a -- daemon/tools/job_queue.py daemon/services/work_notifier.py daemon/services/watch_reconcile_sweep.py daemon/repositories/job_queue/   # expect empty
- Read order for the Emperor: REPORT-1a → REPORT-1b → REPORT-1c → DESIGN-SKETCH.

## Bash-raises used
none — every command ran under the default <=600 s rule (both waves).

## Complexity self-assessment
medium (assessed pre-dispatch; proceeded without handover pause). Read-only investigation + docs-only outputs; four disjoint modules; wave-1 merges all clean. RE-DISPATCH #1 (the leader's single permitted fix wave, REVIEW NEEDS-REVISION / TEST PASS): three owner bounces executed (B/C/D), no re-investigation, no scope creep, all merges clean; this handoff revision records the enumerated drift fix set. No user/Ari override; no verdict softening.
