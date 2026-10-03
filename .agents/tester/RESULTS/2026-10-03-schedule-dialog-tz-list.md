# Pre-Merge Gate: schedule-create dialog API timezone list — `fix/schedule-dialog-tz-api-list` @ 0b1ba048

Date: 2026-10-03 · Tester: independent gate (report-only, zero commits, zero repo-file modifications)
Branch: `fix/schedule-dialog-tz-api-list` @ `0b1ba04833212a79fe2c92e5c1a164e6045cac12` (base 0d7b5cf3; chain bdade361 + 0b1ba048). All 3 workers verified HEAD pre-run. FE-only diff (+731/−70); backend byte-untouched (independently re-confirmed by item-3 baseline-exact run).

## VERDICT: 🟢 PASS — merge-ready, with ONE documented deviation (runtime boot skipped — port fence; substitute evidence chain below)

**Defect (dialog offered only 12 hardcoded zones from a static array, e.g. no Asia/Ho_Chi_Minh): DEAD.**
- **Criterion (a) — no static array remains in production code: PROVEN** (audit below, zero remnants).
- **Criterion (b) — options come from the endpoint, all 12 legacy zones selectable: PROVEN by evidence chain** (endpoint contract tests at this exact commit + morning live-runtime on the byte-identical endpoint + dialog DOM pins), with the fresh dev-boot curl skipped due to a port fence (see Deviation).

Gate totals: **353 passed / 0 failed / 0 skipped / 0 errors / 0 timeouts — zero fix-caused failures.**

---

## Item 1 — FE target: dialog spec — ✅ PASS

- `cd frontend && timeout 300 ./node_modules/.bin/jest src/app/components/schedule-create-dialog/schedule-create-dialog.component.spec.ts --ci --runInBand`
- **30/30** (29 prior + 1 new editMode-prefill pin) · 2.57s
- New pin itemized: `editMode prefill pins this.data.timezone || 'UTC' at the patchValue call` (+ companion `editMode prefill preserves a stored zone and falls back to "UTC" when unset`) — both green.
- Suite also pins: "static TIMEZONES array removed" 5/5 · "picker source chain" 8/8 · "12 legacy zones resolve in the canonical list" 3/3 · "fallback text input writes to form.timezone" 4/4 · "default + editMode prefill" 3/3 · "template branches on isTzNativeSupported()" 3/3 · "async load observability" 3/3.

## Item 2 — FE pure-move regression + typecheck — ✅ PASS

- Settings suites: `jest src/app/pages/settings/settings.component.spec.ts src/app/services/settings.service.spec.ts --ci --runInBand` → **120/120** (component 107 + service 13) · 11.42s (morning baseline identical).
- Typecheck: `timeout 300 ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` → **0 errors** · ~6s · frontend-local binary used (typosquat `tsc@2.0.4` via bare npx outside frontend/ avoided).

## Item 3 — BE regression (no accidental drift) — ✅ PASS

- `timeout 300 .venv/bin/python -m pytest --override-ini="addopts=" -m postgres tests/test_settings_api.py -v --tb=short` → **37/37** · 26.98s · baseline-exact vs morning gate (25.7s). Includes the 5 `TestGetTimezoneOptions` endpoint invariants (sorted, HCM inclusion, all-entries-valid-ZoneInfo, non-empty, known-canonical) executing the REAL router code at 0b1ba048 against real PG — this is criterion-(b) evidence leg #1.

## Item 4 — Scheduling adjacency — ✅ PASS

- `timeout 300 bash tests/packs/scheduled_tasks_acceptance.sh` → **166P/0F/0S** · 21.96s · baseline-exact (28s morning). Covers `test_scheduler_adapter.py`, `test_scheduler_api.py`, `test_scheduled_tasks_e2e.py` (incl. D9 four-tool wiring).
- **Concurrency pack EXCLUDED (commissioned)**: FE-only diff (+731/−70, 5 files, all `frontend/src/**`) touches zero daemon concurrency / async-DB surface. ensure.md scopes Core #2/#3 to the change set's packs; this morning's baseline-exact run (98P/0F/74S @ 0eef5a98, backend byte-identical since) stands as the most recent evidence if wanted.

## Item 5a — Static-array-dead audit — ✅ PASS (criterion (a) PROVEN)

Legacy-12 extracted verbatim from git history (`git show bdade361^:frontend/.../schedule-create-dialog.component.ts`, cross-verified against the deletion diff hunk):

UTC · America/New_York · America/Chicago · America/Denver · America/Los_Angeles · Europe/London · Europe/Paris · Europe/Berlin · Asia/Tokyo · Asia/Shanghai · Asia/Singapore · Australia/Sydney

Audit results:
- `TIMEZONES` identifier: **0 hits** in production code (repo-wide cross-check incl. scripts/docs: also 0).
- Zone-literal sweep (production scope, specs/tests/mocks excluded): 8 hits, **all LEGIT SCALAR** — 3× dialog component (`timezone: ['UTC']` form default L167; editMode fallback L190; submit fallback L349 + one comment) — exactly the commissioned `'UTC'`-default pattern; 2× untouched generic form-field schemas (edit/add-source-modal, `type: 'text'` placeholders); 1× `timezone-format.ts:53` GMT/UTC/Z offset-format helper; 1× model JSDoc. Dialog HTML: 0 hits. Settings page (touched by pure-move): 0 hits — pickers route through the API list.
- **0 PRODUCTION REMNANTS.**

## Item 5b — Runtime dev-boot check — ⚠️ SKIPPED (port fence) — criterion (b) via substitute evidence chain

- **Fence event:** port 8079 occupied by a SIBLING lane's daemon — `/home/nea/dev-daemon-8079-v0.16.11/payload/ensemble-prod` (pid 3170090, started 08:35:26, cwd ≠ repo). Per fence: STOP, inspect-only, no signal. Context indicates this is the od-smoke lane's post-promote dev-DB smoke (its RESULTS file actively growing per commission). Worker honored the fence exactly; live 9797 + demo 7979 verified 200 throughout.
- **Options rejected:** (1) kill/wait on pid 3170090 — cross-lane sabotage risk, never; (2) cross-tenant curl of that daemon — INVALID evidence: it runs v0.16.11 (promoted 04:36Z) which PREDATES the tz-picker branch merge, so it likely lacks `/api/settings/timezones` entirely, and a different code version can't evidence this commit; (3) boot on an alternate port — dev.sh's `.env`-sourced port + a second daemon on the shared dev DB would risk interfering with the sibling smoke mid-flight.
- **Substitute evidence chain for criterion (b):**
  1. Endpoint contract at THIS commit: item-3's 37/37 incl. all 5 `TestGetTimezoneOptions` invariants (sorted; `Asia/Ho_Chi_Minh` included; every entry ZoneInfo-valid) — real router + real PG at 0b1ba048.
  2. Live-runtime on byte-identical endpoint: this morning's gate-1 curl (2026-10-03, 0eef5a98): 200/JSON, 498 zones, HCM present, Saigon absent, sorted, 498/498 ZoneInfo-valid. Backend byte-untouched between gates (item-3 baseline-exact confirms).
  3. Consumption layer: dialog spec 30/30 pins API-fed options incl. "12 legacy zones resolve in the canonical list" (3/3) and fallback-input binding.
- **Playwright: SKIPPED** — `.local-browsers` still absent; additionally `frontend/e2e` contains no schedule-dialog spec at all (only the Jest component spec). Dialog spec DOM-level pins + API contract tests are the gate evidence, per commissioned fallback.
- **Optional follow-up:** re-run the runtime pack verbatim once 8079 frees (5-min task) if the caller wants the fresh-boot curl line at this commit. Not required for the verdict on an FE-only diff.

## Item 6 — Fix-caused failures: NONE

0 reds across all packs. Known pre-existing reds (job_queue `TestSite1InlineMirrorFinalize` ×3, `enqueue_shared` drift, QUARANTINE.md trio) did not intersect the boundary — excluded by scope, stated, not silently skipped.

## ensure.md closure (Core, blast-radius scoped)

- Critical #1 (changed packs PASS): ✅ items 1–4 all PASS
- Critical #4 (dev.sh `--timeout-graceful-shutdown 10`): ✅ re-grepped this gate — dev.sh:102
- Critical #2/#3 (concurrency pack): **scoped out** — FE-only change set, zero concurrency surface; commissioned exclusion; rationale above
- Release Gate: NOT triggered (scoped FE change). Contradictions/improvement notices: none.

## Artifacts & hygiene

- This file: untracked, finalize lane commits. `RESULTS/2026-10-03-odsp-grand-finale-od-smoke.md` untouched by all workers (verified).
- LESSONS addendum: `LESSONS/2026-10-03-port-8079-multi-lane-fence.md` (sibling-lane daemon fence pattern).
- Evidence temp files: `/tmp/schedsmoke-*.log|json` (static worker; runtime boot never attempted).

Worker instances: ba906272 (FE), 786b13c6 (BE+sched), 9b2fc2de (static+runtime-fence).

**Overall: PASS — `fix/schedule-dialog-tz-api-list` @ 0b1ba048 is merge-ready** (runtime-boot deviation documented above with substitute evidence; optional fresh-boot re-run available on request).
