# Pre-Merge Gate: Settings timezone picker tzdata sync — `fix/timezone-list-tzdata-sync` @ 0eef5a98

Date: 2026-10-03 · Tester: independent gate (report-only, zero commits, zero repo-file modifications)
Branch: `fix/timezone-list-tzdata-sync` @ `0eef5a98c05e4d7acdd6ceb345121b01a0cda4eb` (base 30cf345e = latest = v0.16.11; chain 30cf345e → 58beec81 → c45a69ac → 745afb13 → 0eef5a98). All 4 workers verified HEAD before running.
Change under test: new `GET /api/settings/timezones` (sorted `zoneinfo.available_timezones()`, cached at import) + FE picker API-first (Intl fallback → text-input row) + guards/specs. `daemon/util/tz.py` untouched.

## VERDICT: 🟢 PASS — merge-ready

**Symptom (user could not find `Asia/Ho_Chi_Minh` in the Settings timezone dropdown): DEAD.**
Runtime evidence (dev daemon :8079, `ENSEMBLE_SELF_ENV=dev`, engine `localhost:5432/ensemble_dev`):
```
STATUS: 200  content-type: application/json  {"timezones": [...]}  size 8844
COUNT: 498
HCM_PRESENT: True    (Asia/Ho_Chi_Minh IS served)
SAIGON_ABSENT: True  (Asia/Saigon is NOT)
SORTED: True         (list == sorted(list), full array)
VALIDATED: 498/498   (every entry constructs zoneinfo.ZoneInfo(name); .venv python)
```
Gate totals across all packs: **391 passed / 0 failed / 74 skipped (baseline-exact) / 0 errors / 0 timeouts — zero fix-caused failures, nothing to triage.**

---

## Item 1 — BE pack: `tests/test_settings_api.py` — ✅ PASS

- Command: `timeout 300 .venv/bin/python -m pytest --override-ini="addopts=" -m postgres tests/test_settings_api.py -v --tb=short`
- Counts: **37P / 0F / 0S / 0E** · duration 25.7s (pytest) / 31s wall
- All 5 new `TestGetTimezoneOptions` tests green, itemized:
  1. `test_returns_non_empty_list` — PASS
  2. `test_list_is_sorted` — PASS
  3. `test_includes_canonical_asia_ho_chi_minh` — PASS
  4. `test_every_entry_is_a_valid_iana_zoneinfo_key` (⊆-valid-keys invariant) — PASS
  5. `test_known_canonical_zones_present` — PASS
- **Count correction (commission-side, not code):** file holds 37 total *post-change* = 32 pre-change + 5 new. This reconciles exactly with the prior gate's 32/32 (PACKS.md, 2026-10-02 @ 4ac8fd4a). The commission's "was 37/37 pre-change" was the post-change count. No action on branch.

## Item 2 — Surrounding regression (tz/scheduling boundary) — ✅ PASS (all 4 sub-runs match prior baselines)

Boundaries chosen (audit: grep `daemon.util.tz|available_timezones|ZoneInfo` over tests/):

| Candidate | Decision |
|---|---|
| `tests/unit/services/test_current_time_injection.py`, `tests/unit/services/test_user_timezone_utils.py` | INCLUDED (a) |
| `tests/unit/test_tz_resolver.py`, `tests/unit/services/test_scheduling_service.py`, `tests/unit/tools/test_scheduling_tools.py` | INCLUDED (b) |
| `tests/test_scheduler_adapter.py` (37 tz hits) | covered inside (c) registered pack — no extra sub-run |
| `tests/test_settings_api.py` (7 tz hits) | EXCLUDED — item 1 covers the full file; preference endpoint untouched by diff |

| Sub-run | Command (essentials) | Counts | Duration | Baseline match |
|---|---|---|---|---|
| (a) injection unit | `timeout 300 .venv/bin/python -m pytest --override-ini="addopts=" tests/unit/services/test_current_time_injection.py tests/unit/services/test_user_timezone_utils.py -v --tb=short` | 30P/0F/0S/0E | 0.45s | ✅ 30/30 |
| (b) scheduling tz unit (5-rung matrix) | same pattern, `test_tz_resolver.py + test_scheduling_service.py + test_scheduling_tools.py` | 66P/0F/0S/0E | 1.7s | ✅ 66/66 |
| (c) `tests/packs/scheduled_tasks_acceptance.sh` (registered) | `timeout 300 bash tests/packs/scheduled_tasks_acceptance.sh` | 166P/0F/0S (9W pre-existing) | 28s wall | ✅ 166P/0F/0S |
| (d) `test/packs/concurrency_atomic_unit_test.sh` (registered; ensure.md Core #2/#3 closure) | `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` | 98P/0F/74S (57W pre-existing) | 67s wall | ✅ 98P/0F/74S baseline-exact |

Coder's "~138 neighbors" = 42-item-1-file(37)+30+66 = 133 unit-neighbor tests + scheduler adapter inside (c) — boundary fully covered.

## Item 3 — FE pack — ✅ PASS

- Jest: `cd frontend && timeout 300 node_modules/.bin/jest src/app/pages/settings/settings.component.spec.ts src/app/services/settings.service.spec.ts --ci --runInBand`
  → **120/120** (component **107** + service **13**), 11.3s. Matches commission expectation (107 + service suite). One pre-existing TS151001 advisory, identical to prior gate.
- Typecheck: `timeout 300 node_modules/.bin/tsc --noEmit -p tsconfig.app.json` → **0 errors**, ~6s.

## Item 4 — Runtime symptom-dead smoke — ✅ PASS

- Safety: `ENSEMBLE_SELF_ENV=dev` exported pre-boot (worker shell default was `live` — process-level export isolated it; standing rule confirmed load-bearing); DB-target guard held (`Creating PostgreSQL engine: localhost:5432/ensemble_dev`); 9797 (live) + 7979 (demo) verified healthy post-task; 8079 freed; all shutdown kills PID+cwd-asserted, never by port.
- ensure.md Core #4 static check: `--timeout-graceful-shutdown 10` present at `dev.sh:102` ✅.
- Boot 11s → livez 200; assertions <1s; total wall <25s; outer `timeout 300` + internal 240s deadline + cleanup trap.
- **Playwright e2e: SKIPPED** (commissioned fallback invoked): `playwright` binary present but `.local-browsers` missing — installing browsers = disproportionate setup. FE unit specs (107, incl. picker DOM behavior: API-first feeding, HCM selectable) + the API-level runtime check are the merge-gate evidence.
- Evidence artifacts: `/tmp/tzsmoke-response.json`, `/tmp/tzsmoke-dev.log`, `/tmp/tzsmoke-driver.log`.

## Item 5 — Fix-caused failures: NONE

0 reds anywhere in the gate. No triage required. Known pre-existing reds (job_queue `TestSite1InlineMirrorFinalize` ×3, `enqueue_shared` drift; QUARANTINE.md: message-metadata prune pair, mcp_server_crud, attestation migration) did not intersect the boundary — excluded by scope, not skipped silently.

## ensure.md closure (Core, blast-radius scoped)

- Critical #1 no regressions in changed packs: ✅ (items 1–4 all PASS)
- Critical #2/#3 concurrency + no-sync-DB-on-loop: ✅ pack (d) 98P/0F/74S baseline-exact
- Critical #4 dev.sh graceful-shutdown flag: ✅ static grep dev.sh:102
- Important (await-callers) / Nice-to-have (dead code): N/A — scoped out (referenced functions untouched by this diff)
- Release Gate: NOT triggered (scoped router+FE change, no architecture impact)
- Contradictions / Improvement Notices: none — ensure.md is properly pack-mapped; all validations ran as packs under dual-layer timeouts.

## Observations for future commissions (non-blocking)

1. **dev.sh cleanup trap:** MCP subprocesses (context7, open-design) + uvicorn reload child inherit the :8079 LISTEN socket fd — killing only the `dev.sh` bash PID leaves the port bound. Cleanup must enumerate `ss -ltnp` pid= entries, assert `cwd == repo-root`, TERM each. → LESSONS/2026-10-03-dev-daemon-smoke-cleanup-socket-fd.md
2. Worker-shell `ENSEMBLE_SELF_ENV` defaulted to `live` on this host — every daemon boot must export `dev` explicitly (4 prior env-poison incidents; guard held here via process-level export + engine-line check).
3. PACKS.md commission-summary entry intentionally NOT appended (report-only gate; finalize lane owns commits). Summary block available above for paste-in.

**Overall: PASS — `fix/timezone-list-tzdata-sync` @ 0eef5a98 is merge-ready.**

Worker instances: 63cbc98a (BE), e440b8d5 (regression), 55a05e64 (FE), f4e78216 (runtime smoke).
