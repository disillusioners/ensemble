# P2.1 Sandbox Acceptance Drills — Evidence Summary

- **Date:** 2026-08-22/23 (run11) · **Harness:** `test/drills/p21_upgrade_pipeline_drill.sh` + stub double `p21_stub_daemon.py`
- **Result: 75/75 PASS** (see `/tmp/p21-drills/run11/transcript.txt` for the full run; rerun with `bash test/drills/p21_upgrade_pipeline_drill.sh [dir]`)
- **Scope:** throwaway sandboxes (`/tmp/p21-drills/runN/sbx{1,2,3}`, ports 18401-18403), stub "ensemble-prod" doubles (serve/wrongver/exit78/ready503), real `launcher.sh` + `scripts/upgrade/` pipeline; NO real PyInstaller build (T10's wave), NO DB, NO live contact (live pid checkpoint byte-identical at drill end: `30054 31150`).

## Acceptance mapping (phase1-plan T1-T9 rows)
| Row | Drill evidence |
|---|---|
| T1 guards | live w/o `ENSEMBLE_UPGRADE_LIVE=1` → 78 (all 4 scripts); sandbox no-PORT → 78; status prints resolved triple |
| T2 stage | manifest w/ all field groups; `find -name .env` empty; idempotent re-stage (byte-identical manifest); untagged → 78; `ENSEMBLE_SELF_ENV=sandbox` staged into `INSTALL_DIR/.env` |
| T3 integrity | tamper → `status.sh --verify` exit 1 naming file; clean → 0; promote preflight aborts 78 on drift |
| T4 promote | 2 consecutive promotes commit; `/livez` version == manifest `binary_version`; previous updated; lock released; mid-flip SIGKILL leaves `in_flight`+`flipped:true` (T7 sweep input) |
| T5 rollback | wrongver gate fail → journal rollback+quarantine+cooldown+count; quarantined re-promote refused 78; 3rd rollback/24h arms halt, next promote 78 with halt message; `rollback_safe=false` previous → halt-for-human, NO repoint |
| T6 manual | manual rollback succeeds + counts; quarantined target needs `--force` (warning printed) |
| T8 retention | 5 interleaved stage→promote cycles → exactly 3 remain; current+previous survive |
| T9 make | `make upgrade-status` byte-identical to direct invocation (cmp); `grep git pull/fetch scripts/upgrade/` empty |

## Drill-side fixture manipulations (documented, not product behavior)
- `ENSEMBLE_PROMOTE_SOAK_S=4`, `LIVEZ_BUDGET_S=6`, `READYZ_BUDGET_S=6` — sandbox-only drill knobs (production defaults 300/60/120 unchanged).
- Cooldown stamps cleared between phases to sequence rollbacks without 10-min waits (cooldown timing is unit-proven in `release_journal_unit_test`); every clearing is logged in the transcript.
- Drill tags the repo HEAD with the version token for the stage guard (deleted immediately after each stage).
