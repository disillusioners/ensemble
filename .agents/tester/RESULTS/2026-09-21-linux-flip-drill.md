# Test Report: P2.1 Linux Atomic-Flip Drill (sandbox rung)

Date: 2026-09-21T15:0x UTC (session)
Instance IDs: f8e682ba-95a3-47a0-876e-42cb08ddb0fb (sanity tally, `test-pack-execution`) · 84adeb01-dfb3-4566-ac97-10b060a9a378 (drill, `e2e-test`)
Branch under test: `fix/portable-atomic-flip-linux` @ `a89c56cb` (base latest @ 246b7325) + gate-owned drill-fixture commit `becf782f` (1 line)

## Summary
- Sanity unit tally: **36/36 PASS** (`tests/test_atomic_flip.sh`, 1s)
- E2E drill (sandbox `/home/nea/agents-ensemble-dev`, port 8388): **DRILL-CLEAN for the fix under test**
- New pre-existing GNU-host defects found (production pipeline source, NOT caused by fix, NO-FIX authorization honored): 2 (1 blocker-class for orphan-recovery paths)
- Quick fixes applied: 1 (drill script fixture, commit `becf782f`)
- Quarantined: 0 (T9 drill row FAIL = environmental `make(1)` missing on host — drill-script row, not a pack test)

## Scope Decision
Full suite not warranted. Change set = `scripts/upgrade/lib.sh` atomic flip + launcher mirror + `tests/test_atomic_flip.sh` (unmerged branch). No `daemon/` code touched → no job/task/queue execution-lane intersection → per project e2e rule, `dev.sh` boot gate NOT triggered; the drill IS the appropriate e2e. ensure.md Core packs (concurrency_atomic etc.) out of blast radius. `tests/test_atomic_flip.sh` and `test/drills/p21_upgrade_pipeline_drill.sh` are NOT pack-registered (branch-scoped artifacts; register at merge time if desired).

## Gate 1 — Sanity tally (worker f8e682ba)
- Branch: `fix/portable-atomic-flip-linux` ✓ · HEAD: `a89c56cb fix(upgrade): make atomic symlink flip portable across BSD and GNU mv` ✓
- Tally verbatim: `== summary: 36 passed, 0 failed ==` · RESULT: PASS · runtime 1s
- 8 scenario banners green incl. launcher `_js_flip_current` mirror + dispatch-table drift guard. Expected negative-path stderr (`ln: Permission denied` in read-only scenario) by design.

## Gate 2 — E2E drill (worker 84adeb01)

### Invocations (all env-scrubbed: `-u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PASSWORD -u POSTGRES_PORT -u POSTGRES_USER -u PORT`; ambient leak CONFIRMED present on this host: `PORT=9797 POSTGRES_DB=ensemble_prod POSTGRES_HOST=10.44.0.2` — scrub is load-bearing)
1. Scripted drill: `HOME=$(mktemp -d) timeout 1500 bash test/drills/p21_upgrade_pipeline_drill.sh /tmp/p21-drills/gnu1` (runbook §1.4 isolated-HOME; §0.4 ps-anchored live-pid checkpoint)
2. Manual DR-4(d)/T7 sweep fallback: `timeout 1500 bash /tmp/p21-drills/gnu1-mansweep/run.sh` (per-step: stage 240s / promote 300s)
3. Baseline stage v0.13.8: `ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.13.8 TARGET=sandbox INSTALL_DIR=/home/nea/agents-ensemble-dev PORT=8388 timeout 900 bash scripts/upgrade/stage.sh sandbox` (stale `dist/` removed first; FL-29 detach-to-tag restored immediately)
4. Baseline promote v0.13.8: `ENSEMBLE_PROMOTE_SOAK_S=30 … promote.sh sandbox`
5. **THE FLIP** — promote v0.13.9: `ENSEMBLE_PROMOTE_SOAK_S=30 VERSION=v0.13.9 … promote.sh sandbox`
6. Rollback: `TARGET=sandbox INSTALL_DIR=… PORT=8388 timeout 600 bash scripts/upgrade/rollback.sh sandbox`

### Evidence (verbatim, per task requirement a–e)
- **a. Pre-drill**: `readlink …/current` → `(absent)`; releases/ = `v0.13.9` only; journal `{"current":null,"previous":null,…}`; install .env → port 8388, `ENSEMBLE_SELF_ENV=sandbox`
- **b. FLIP**: `upgrade-promote[sandbox]: current -> releases/v0.13.9 (atomic flip)` — NO `mv: invalid option -- 'h'` in ANY of ~25 flips; post-flip `readlink current` → `releases/v0.13.9`; exit 0, `COMMITTED: current=v0.13.9 previous=v0.13.8`
- **c. Health (v0.13.9)**: `livez OK: {"status":"alive","uptime_seconds":1.33…,"version":"0.13.9"}` · `readyz OK: {"status":"ready","components":{"database":true,"queue_freshness":true,"services":true},"detail":{"reasons":[],…}}` · `version verify OK: 0.13.9` · soak 30s green
- **d. Rollback**: `target: journal previous = v0.13.8` → `current -> releases/v0.13.8 (atomic flip)` → re-gate `livez OK "version":"0.13.8"`, `readyz OK "reasons":[]` → `rollback complete: current=v0.13.8 serving 0.13.8; window count 1/3`, exit 0; post-rollback `readlink current` → `releases/v0.13.8`
- **e. Journal**: post-flip `{current:"v0.13.9", previous:"v0.13.8", in_flight:null}` + history `promote v0.13.9 committed (gate+soak green; previous=v0.13.8)`; final `{current:"v0.13.8", previous:"v0.13.9", in_flight:null, rollback_window_count:{24h:1}, cooldown_until:null, quarantined:[]}` + history `manual rollback → v0.13.8 (re-gate green; window count 1/3)`. Full transcripts: `/tmp/p21-drills/gnu1/`, `/tmp/p21-drills/gnu1-mansweep/`
- **Isolation**: live-pid checkpoint (§0.4) byte-identical start vs end (30646/30668/30696, `*:9797`); demo never touched; pid files `/tmp/p21-drills/gnu1/live-pid-{start,end}.txt`

### Per-phase results
| Phase | Result |
|---|---|
| stage | PASS (drill 0/A–E green; real v0.13.8+v0.13.9 manifests verified; T9 row FAIL = `make` missing, environmental) |
| promote-gates | PASS (livez→readyz→version-verify→soak green on real v0.13.9 promote; halt-no-previous branch exercised, spec-conformant) |
| FLIP | **PASS — portable `mv -T -f` dispatch proven on GNU, real install included** |
| health | PASS (verbatim above) |
| rollback | PASS (repoint → re-gate → 1/3 window, exit 0) |
| journal | PASS (consistent with every observed readlink) |

Scripted drill itself: `timeout 1500` tripped mid-Phase-F (exit 124) — budget trip, NOT a hang (flip+gates green at kill). Evidence completed via manual runbook sweep + real-sandbox sequence.

## GNU-host findings (classified; NONE caused by the fix)
1. 🔴 **PRE-EXISTING, blocker-class for recovery paths**: `_iso_to_epoch` (lib.sh:86) BSD-only `date -ju -f` → always fails on GNU. Observed live: `adopt_stale_txn` refuses (`unparseable started_at — pipeline-busy`); launcher journal sweep fails closed (`in_flight started_at unparseable — leaving untouched, boot proceeds`) → no repoint/sweep_rollback/quarantine/counter, lock held. Phase-H cross-writer sweep_rollback unreachable on GNU by construction; 24h rollback window never expires.
2. 🟠 **PRE-EXISTING**: cooldown arm (lib.sh:709) BSD `date -v+Ns` → GNU fallback stamps `cooldown_until=now` (ADR-005 anti-flap silently inert); `journal_cooldown_active` then treats stamp as unparseable→ACTIVE → post-auto-rollback promotes refused on GNU until manual journal surgery. (Not hit in final sequence: single rollback, no subsequent promote.)
3. 🟢 **PRE-EXISTING, benign**: retention mtime fallback `stat -f '%m'` (lib.sh:1279) — `st=0` fallback on GNU; manifest `staged_at` is primary.
4. 🟢 Environmental: `make(1)` not installed → T9 drill row FAIL; drill wall-clock on GNU ≈27+ min (70s stop windows + per-file sha256 spawns) — exceeds `timeout 1500`; a blind full re-run will re-trip mid-Phase-F.

## Quick Fixes Applied
- 84adeb01: `test/drills/p21_upgrade_pipeline_drill.sh:377` — BSD-only `date -ju -v-700S` → GNU-first portable form w/ BSD fallback. 1 line, 1 file. Commit `becf782f` (on branch; merge carries it). `git diff a89c56cb` touches nothing else; pipeline source untouched.

## ensure.md Validation Results (scoped)
- Core-Critical "No regressions in changed packs": **PASS** — changed-scope equivalents: atomic-flip suite 36/36 + drill phases green (no PACKS.md packs in blast radius; see Scope Decision)
- Core-Critical concurrency pack / dev.sh static: out of blast radius (no daemon/ change) — not run, by scope rule
- Release Gate: not triggered (non-architecture shell/scripts change; per project e2e lane rule dev.sh boot gate N/A)
- Improvement Notices: none (no contradicting methods encountered)

## Sandbox end-state changes (leader must know)
- `current → releases/v0.13.8` (LKG post-rollback), daemon healthy on 8388
- releases/: v0.13.8 + v0.13.9; journal anchored (current=v0.13.8, previous=v0.13.9, window 1/3)
- Throwaway local PG on 127.0.0.1:54329 (DB `ensemble_sandbox`, data `/tmp/p21-sbx-pg`) STILL RUNNING to keep sandbox bootable (sandbox .env had no PG config; `.env.pre-drill-backup` retained). Stop: `pg_ctl -D /tmp/p21-sbx-pg/pgdata stop`
- `config.yaml` copied into INSTALL_DIR (was missing; promote halt-no-previous root cause)

## Overall Status
- Sanity unit gate: ✅ PASS (36/36)
- E2E drill: ✅ **DRILL-CLEAN for the fix under test → safe to proceed to merge + demo resume**
- Carry-over risk (separate commission needed BEFORE demo/live rely on orphan-recovery or post-rollback promote entry on GNU): `_iso_to_epoch` + cooldown-arm portability (findings 1–2)
- Documentation: RESULTS (this file) + LESSONS/2026-09-21-gnu-flip-drill-findings.md + PACKS.md gate entry
