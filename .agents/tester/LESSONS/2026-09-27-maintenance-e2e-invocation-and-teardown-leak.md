# Maintenance e2e invocation contract + deterministic boot-script teardown leak (2026-09-27)

**Context:** independent Playwright re-run of the 14-case maintenance gate (`frontend/playwright.maintenance.config.ts` + `frontend/scripts/boot-e2e-maintenance-daemon.sh`). Attempt 1 produced a false FAIL; attempt 2 passed 14/14. Three contract facts + one real defect.

## Invocation contract (attempt-1 lessons)
1. **Worker-env DSN token REQUIRED**: spec `beforeAll` (maintenance-checkpoint-cleanup.spec.ts:35) throws unless `ENSEMBLE_DB_DSN` OR `POSTGRES_HOST` is set in the TEST-WORKER process env — but the spec module-load scrubs `POSTGRES_*` (:65), so only `ENSEMBLE_DB_DSN` survives to the guard. webServer `env:` does NOT propagate to test workers; the boot script re-exports `POSTGRES_*` only inside webServer#1's process tree. Correct invocation: `export ENSEMBLE_DB_DSN='postgresql://ensemble@127.0.0.1:15432/ensemble_e2e_maint_disposable'` (non-prod name satisfies the :41-43 `/ensemble_prod/i` refusal). Dispatcher-side "unset POSTGRES_*" hygiene alone makes the suite unrunnable BY DESIGN.
2. **File filter REQUIRED**: config `testDir './e2e'` sweeps ALL e2e specs (141 tests); the config's own doc (:20-21) prescribes the positional filter. Correct: `npx playwright test -c playwright.maintenance.config.ts maintenance-checkpoint-cleanup --project maintenance`.
3. **`--project` is variadic**: a positional filter placed AFTER `--project maintenance` gets swallowed as a project name → instant "Project(s) not found" (no boot, no tests). Positional args go BEFORE `--project`. Also `--reporter=line` prints no per-test durations for passes (use `--reporter=json` if timings become contractual).

## Deterministic teardown leak (real test-infra defect, F1, pre-existing)
Playwright stops webServer#1 with SIGTERM to the boot script; the script's cleanup path (`pg_ctl stop` + `rm -rf` of PGDATA and `data_e2e_maintenance/`) NEVER runs → `postgres -D /tmp/pg_e2e_maint_<pid>` survives LISTENING on :15432. Reproduced 2/2 attempts (pids 85676, 88633 matched their boot logs); 8 dead `/tmp/pg_e2e_maint_*` clusters from earlier (FE-dev) runs corroborate a systematic leak. Fix direction: make the boot script's trap fire on SIGTERM (likely the daemon `exec` replaces the shell, killing the trap) or have Playwright `action_stop` handle teardown. Runner-side mitigation until fixed: post-run port check + kill own leftover (port-verified).

**Rules:**
1. e2e stack invocations must carry the DSN token + positional filter (encode in the pack script when this gate gets one).
2. Any e2e run MUST end with a port 15432 leak check; leaked postgres from YOUR run is yours to kill (port+datadir-verified only).
3. Do not read "attempt FAIL" as product verdict until the invocation contract is proven satisfied — check the beforeAll guard error signature first.
