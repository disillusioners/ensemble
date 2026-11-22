# Test Report: Engine Arc Phase-1 live-defects lane — regression + original-symptom gate

Date: 2026-11-21T15:30Z
Lane: `fix/phase1-live-defects` @ HEAD `c8db252e8` (base `b71742fe3`), worktree `/home/nea/ensemble-src-wt-live-defects-p1`
Worker instances: e768a315 (regression A/B), 8bd65120 (D1 boot smoke), 3a90ac60 (D2 retention/D-6), cdb033f9 (D-2/D-3 edges), a0a7b435 (prep/git investigation)
Evidence root: `.agents/tester/EVIDENCE/2026-11-21-live-defects-p1/` (330 files, ~1.5 MB, all `.txt`, mode 600/dir 700)

## Summary

- **Gate verdict: PASS — both fixes verified symptom-dead on final HEAD; net-new deterministic failures = 0.**
- Regression: 0 deterministic net-new vs TRUE executed base (one host-timing flake, adjudicated + quarantined).
- Original symptoms: D1 born-dead DEAD (boot smoke @ c8db252e8); D2 retention/stall DEAD (incl. D-6 bounds the dev left untested).
- Behavioral edges: D-2 ERROR visibility confirmed per-tick; D-3 dead-detection does NOT re-arm after recovery (Phase-2 input).
- Quarantined (new rows): 4 deterministic reaper reds + 2 host-timing flakes.
- Incidents: one CONTAINED credential exposure in evidence capture (anomaly 9ee4975a); brief premise falsified (pack 177/177 — anomaly 48f2fc17, worker-filed).

## Gate item 1 — QUARANTINE pre-check + TRUE base FAILED-list

- QUARANTINE.md pre-check: no existing row covered the 4 reaper reds (rows 157/183/185 are different defect classes). **DECISION: rows ADDED** (2 rows, appended 2026-11-21). Rationale: deterministic, present in EVERY env shape, 3 of 4 sit in a lane-TOUCHED test file (every future family gate re-trips them), base-EXECUTED attribution now exists (not derived), and the dev's EVIDENCE-only pointer referenced a DIFFERENT lane's base — a standing row prevents re-derivation.
- TRUE base list: executed at detached worktree `/tmp/live-defects-base-b71742fe3` (removed after); uv.lock sha256[:16] `ad418aa6d2fe4ca3` BOTH legs; venv freeze diff EMPTY; Python 3.14.7/117 pkgs both legs.
- Dev's attribution RE-VERIFIED and CORRECTED: all 4 reds confirmed byte-identical (node id + first error line) at base and HEAD — but they are **every-shape reds, not "ambient-dependent"** (2×2 PG-pin × ambient-scrub measured: the 4 survive all shapes; only the 2 stamp reds vanish under PG-off+scrubbed). The dev's 2026-11-02 EVIDENCE pointer was a different lane's base — superseded by this gate's executed base.

## Gate item 2 — Full regression on touched scopes

Scopes from `git diff b71742fe3..c8db252e8 --stat` (5 prod + 4 test files) + grep-derived referencing files (12 + 1 PG pair). All commands identical both legs, per-variable env fence (`ENSEMBLE_SELF_ENV=dev`, five `POSTGRES_*` pinned to ensemble_dev @127.0.0.1:5432), every run `timeout`-wrapped.

| Scope | base | head | verdict |
|---|---|---|---|
| S1 pack `bash_spawn_reaper_feature_test.sh` | 171P/6F | 175P/6F | same node set — pack FAILS both legs (premise correction) |
| S1′ direct 4-file PG-pinned | 169P/8F | 173P/8F | same node set |
| S2 `test_c2_orphan_watcher_sweep.py` | 17P/0F | 24P/0F | PASS/PASS (+7 lane nodes) |
| S3 `test_bash_spawn_retention_sweep.py` (new) | — | 21P/0F | PASS (HEAD-only) |
| S4 PG pack `bash_spawn_repository_pg_test.sh` | 5P/0F | 5P/0F | PASS/PASS |
| S5 12 grep-derived files + PG pair | incl. 6 shared reds | identical | one base-red head-GREEN (job_feedback_observer flake-class), one head-only flake (adjudicated) |

FAILED-list diff (canonical node ids, all scopes):
- NET-NEW deterministic: **0**
- Head-only node: `test_bash_ram_guard.py::…test_the_leaderless_tree_is_still_watched_and_killed` — host-timing flake: files sha256-identical base↔HEAD; ordering matrix (plain/order-swapped/alternating/settled/7× whole-file 83P both legs) kills the "burst"; quarantined.
- Base-only node (fixed-by-nothing): `test_job_feedback_observer.py::…test_tier1_sibling_owned_row_skipped` + the ram-guard under-report flake — flake mirror, not a lane fix.
- SHARED reds (13): 4 reaper (quarantined this gate) + 4 stamp + 3 auto_kill_integration + 1 manager_shutdown + 1 `test_06f500af…pg` — all covered by standing rows (157/183/185).

Premise correction (worker anomaly 48f2fc17): `bash_spawn_reaper_feature_test.sh` does NOT pass 177/177 under the fenced invocation — `scripts/run_tests_scrubbed.sh` (inside the pack) unconditionally unsets `POSTGRES_*`, so the pack shape (6F) and the direct PG-pinned shape (8F) are different populations. The 2026-10-31 177/177 was a different commit (5f89691a1) before these reds appeared.

## Gate item 3 — Original symptoms dead (closing gate)

### (a) OrphanWatcher born-dead — PASS
Boot smoke on FINAL HEAD c8db252e8 (dev's smoke was @ 6aa357dc3; this is the hardening commit's own):
- Command: `env PORT=8191 DAEMON_PORT=8191 ENSEMBLE_SELF_ENV=dev <five POSTGRES_* pins> ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 ENSEMBLE_LOG_CONSOLE_LEVEL=DEBUG ENSEMBLE_DB_AMBIENT_ALLOW=1 timeout 420 .venv/bin/python -m daemon` (adaptations disclosed: `python -m daemon` is the canonical serving entry — `daemon.api` only builds the app; ambient-allow = sanctioned first boot for a worktree without persisted ensemble.json).
- Identity: `/proc/<pid>/environ` → POSTGRES_DB=ensemble_dev, ensemble_prod count 0, PORT=8191, sweep-pin 0. ✔
- readyz green in **21s** (Emperor SLO 120s).
- Wiring-assertion ERROR: **0** (absent). Dead-detection ERROR/WARNING: **0**. Effective-run evidence: **3** (`no orphan PENDING watchers found` at ticks 2,3 after bus wired; `_effective_runs_total`=2 ≥2 across ≥2×interval). Tick 1 pre-`init_dependency_bus` DEBUG = expected late-bind window. Orphan-sweep exceptions: 0 (38 unrelated pre-existing tracebacks = metrics F18 lane, explained).
- Teardown: SIGTERM own PID only, port freed, pgrep clean. ✔
- Transcript: `EVIDENCE/2026-11-21-live-defects-p1/d1-bootsmoke-head-c8db252e8.txt` + `identity-verification.txt` + `grep-excerpts.txt`.

### (b) Retention/stall — PASS (D-6 covered)
- Existing new test file 21/21 + scratch probes 13/13 (import-source proven).
- D-6 bounds: 200-lookup / 25-sweep / 60s-deadline ALL hold (`bash_spawn_reaper.py:2098/2099`, deadline via `asyncio.wait_for` const :140). Env-knob clamp [60,86400] holds; hardened `inf`/`1e400` parse → fallback default 900, NO raise (:229).
- Retention prune runs per-tick (boot-only + `if not rows: return 0` skip is dead); terminal-instance stall sweep routes through the SAME kill-safety core (`sweep_instance → sweep_rows`, :1410); PAUSED excluded (:2364); missing-instance rows skipped; master switch before ANY repo touch (:2210); no-op paths counted + rate-limited (first + every 50th, :266).
- GAP (non-blocking): no injectable clock/mono seam for the 60s deadline — verified by module-constant patch (0.1s) + slow fake sweep. Phase-2: add the seam.
- Evidence: `existing_test_run.txt`, `scratch_bounds_run.txt`, `noop_rate_limit_capture.txt`, `coverage_map.txt`.

## Gate item 4 — Behavioral edges (actual behavior, Phase-2 inputs)

- **D-2 (bus exists, every sweep raises): ERROR is PER-TICK visible** (not rate-limited, not silent): 5 raising ticks → 5 `[ERROR]` lines with tracebacks (`orphan_watcher_sweep.py:283-289`); service survives (5/5 ticks ran, no crash). Raising sweeps do NOT count effective (increment :266 sits after the await :260; `effective_runs_total` stayed 0). **Bonus gap: a perpetually-raising bus NEVER trips dead-detection** — `_check_dead_detection()` is only invoked on the bus-is-None branch (:257), never in the except handler.
- **D-3 (re-arm after recovery): NO.** Phase 1: ERROR fires exactly once + rate-limited WARNING; Phase 2: recovery INFO + effective runs 1→2; Phase 3 (dead again, 12 ticks > 2×interval grace): **0 ERROR, 0 WARNING** (DEBUG only). Root cause: `_dead_detection_fired` one-way latch (:338, never reset) AND `_effective_runs_total > 0` early-return (:331-332) permanently suppress both re-ERROR and WARNING after first recovery.
- Evidence: `d2-d3-behavioral-probe-2012863.txt` (full quoted 3-phase sequence), `d2-d3-existing-tests-2013175.txt`.

## Scope Decision

Full-suite NOT warranted: change set = 9 files (5 prod, 4 test) in two service families + api wiring → ran the registered reaper packs + direct touched files + 12 grep-derived referencing files + PG pair. Whole tree skipped (~3058 items, unrelated families). Expanded beyond the dev's ≤2-min targeted set exactly as the mission required (full regression on touched scopes).

## Findings routed to leader (no fixes applied by this gate)

1. 🟠 **Pack registration gap**: `test_bash_spawn_retention_sweep.py` (new, 479 lines) and `test_c2_orphan_watcher_sweep.py` (modified, +277) are referenced by NO registered pack — the two largest test changes of this lane are pack-invisible. Recommend registering (extend `bash_spawn_reaper_feature_test` or a new retention pack) — leader's call; not done here (mission: no fixes).
2. 🟠 **D-3 no-re-arm** + **D-2 raising-bus-never-trips-dead-detection** — real observability gaps for Phase-2 (the service can go permanently silent about a dead bus after one recovery).
3. 🟡 **No injectable clock seam** for the retention 60s deadline (verified via constant-patch; seam owed to Phase-2).
4. 🟡 **Premise corrections for the record**: pack 177/177 is shape-dependent (scrubbed-runner unsets POSTGRES_*); "ambient-dependent" reds are actually every-shape reds.
5. 🔴 (contained) **Credential exposure class**: `test_bash_spawn_stamp.py` assertion reprs render captured child env CLEARTEXT — anomaly 9ee4975a; root fix = render sorted env NAMES only (test-debt).

## Documentation Updated

- [x] QUARANTINE.md — 2 rows appended (4 reaper reds deterministic; ram-guard flake pair)
- [x] RESULTS/2026-11-21-live-defects-p1-tester-gate.md — this report
- [x] LESSONS/2026-11-21-reaper-pack-shape-divergence.md — scrubbed-runner vs PG-pinned population divergence
- [x] EVIDENCE/2026-11-21-live-defects-p1/ — 330 evidence files (credential redaction applied)
- [ ] PACKS.md — intentionally NOT edited (registration gap reported, leader's call)

## Overall Status

- Regression (net-new deterministic): ✅ 0
- Original symptom (a) born-dead: ✅ DEAD @ c8db252e8
- Original symptom (b) retention/stall incl. D-6: ✅ DEAD
- Behavioral edges D-2/D-3: ✅ tested, actual behavior reported (2 Phase-2 findings)
- **Testing Complete: ✅ READY — gate PASS; findings + Phase-2 inputs routed to leader**
