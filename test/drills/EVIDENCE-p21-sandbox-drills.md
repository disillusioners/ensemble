# P2.1 Sandbox Acceptance Drills — Evidence Summary

- **Date:** 2026-08-23 (run12) · **Harness:** `test/drills/p21_upgrade_pipeline_drill.sh` + stub double `p21_stub_daemon.py`
- **Result: 96/96 PASS** (see `/tmp/p21-drills/run12/transcript.txt` for the full run; rerun with `bash test/drills/p21_upgrade_pipeline_drill.sh [dir]`)
- **Scope:** throwaway sandboxes (`/tmp/p21-drills/run12/sbx{1,2,3,4}`, ports 18401-18404), stub "ensemble-prod" doubles (serve/wrongver/exit78/ready503), real `launcher.sh` + `scripts/upgrade/` pipeline; NO real PyInstaller build (T10's wave), NO DB, NO live contact.
- **Live isolation (ASSERTED, not printed):** the drill snapshots the live install's listener pid set at baseline and FAILS if it changed at the end. Run12: baseline == end == `30054 31150` (PASS).

## Acceptance mapping (phase1-plan T1-T9 rows)
| Row | Drill evidence |
|---|---|
| T1 guards | live w/o `ENSEMBLE_UPGRADE_LIVE=1` → 78 (all 4 scripts); sandbox no-PORT → 78; status prints resolved triple |
| T2 stage | manifest w/ all field groups; `find -name .env` empty; idempotent re-stage (byte-identical manifest); untagged → 78; `ENSEMBLE_SELF_ENV=sandbox` staged into `INSTALL_DIR/.env` |
| T3 integrity | tamper → `status.sh --verify` exit 1 naming file; clean → 0; promote preflight aborts 78 on drift |
| T4 promote | 2 consecutive promotes commit; `/livez` version == manifest `binary_version`; previous updated; lock released; mid-flip SIGKILL (Phase G) leaves `in_flight`+`flipped:true` (T7 sweep input) |
| T5 rollback | wrongver gate fail → journal rollback+quarantine+cooldown+count; quarantined re-promote refused 78; 3rd rollback/24h arms halt, next promote 78 with halt message; `rollback_safe=false` previous → halt-for-human, NO repoint |
| T6 manual | manual rollback succeeds + counts; quarantined target needs `--force` (warning printed) |
| T7 sweep | **Phase H cross-writer (review i2):** a REAL promote SIGKILLed right after `flipped:true` (journal 100% lib.sh-written) → txn aged past the 600s gate → the REAL `launcher.sh` started next sweep-ROLLS-BACK the orphan: repoint to previous + `sweep_rollback` event + counter + cooldown + quarantine + boots the rolled-back release + lock released via dead-owner break |
| T8 retention | 5 interleaved stage→promote cycles → exactly 3 remain; current+previous survive |
| T9 make | `make upgrade-status` byte-identical to direct invocation (cmp); `grep git pull/fetch scripts/upgrade/` empty |

## Final fix cycle (W1-W3) — unit-pack coverage, drills unchanged
The W-fix cycle (2026-08-23, tip `9be59635` → this delta) landed its fixtures in the unit packs, not the drill; the drill's 96 assertions are unchanged and re-proven green on the fixed tree:
- **W1** (stale-heartbeat lock break ignores a live owner) — `launcher_supervisor_unit_test` 8v/8w (sweep defer + `_js_lock_acquire` seam) and `release_journal_unit_test` 7c (lib.sh `lock_acquire` mirror): stale heartbeat + LIVE owner → lock never broken; dead owner → break proceeds.
- **W2** (kill-window heal misfires on same-version re-promote abort) — `launcher_supervisor_unit_test` 8u/8u2 and `release_journal_unit_test` 12e (REAL same-version promote abort): heal requires journal `current` ≠ txn target; same-version abort clears pre-flip with NO rollback/quarantine/count.
- **W3** (adopt_stale_txn recovery writes unchecked) — `release_journal_unit_test` 8m/8n: chmod-induced write failure → txn LEFT OPEN + loud WARN + exit 78 (never close over a failed state write).
- Pack totals this cycle: journal 219 → 243, launcher 167 → 190, drills 96/96.

## Drill-side fixture manipulations (documented, not product behavior)
- `ENSEMBLE_PROMOTE_SOAK_S=4`, `LIVEZ_BUDGET_S=6`, `READYZ_BUDGET_S=6` — sandbox-only drill knobs (production defaults 300/60/120 unchanged).
- Cooldown stamps cleared between phases to sequence rollbacks without 10-min waits (cooldown timing is unit-proven in `release_journal_unit_test`); every clearing is logged in the transcript.
- Drill tags the repo HEAD with the version token for the stage guard (deleted immediately after each stage).
- Phases G/H age the REAL txn's `started_at` to -700s via `journal_update` itself (the only way to cross the 600s sweep gate in drill time; logged fixture manipulation).
