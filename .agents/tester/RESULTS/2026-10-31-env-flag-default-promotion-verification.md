# TEST & VERIFY — env-flag default promotion (`feature/env-flag-default-promotion`)

- **Verdict: SHIP** ✅
- Date: 2026-10-31 · Tester lead instance: this session · 6 workers (5 verification + 1 re-baseline)
- Worktree: `agents-ensemble-wt-env-flag-default-promotion` (main checkout and prod daemon NEVER touched; port 8088 never touched)
- Branch: `feature/env-flag-default-promotion` · base `latest@a577d4893` · HEAD under test `0ecdc4fc1134fdb4a5152f0de224fbd7e29c246c` (5 commits; daemon reports v0.28.0)
- Change under test: `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` default OFF→ON (gate = env ∧ config; kill switch `=0` retained; unset/blank→ON silent; 0/false/no/off→OFF; unknown→warn-once+ON cached; set-but-blank/UNKNOWN fails boot at pydantic validation); ~46 env flags documented in `.env.example`+`.env.prod.example`; `docs/setup.md:445` corrected; resolver refactor (token frozensets).

## Scope Decision / Gate Tier

- ensure.md tier chosen BEFORE runs: **Tier 1** (feature lane; diff touches no core-seam path — resolver/config/boot-log/docs, not graph/manager/api/instance_messaging/task_processor/job_queue_service).
- Blast radius = the ri_* suites/packs + boot boundary + docs. Scoped runs only; full suite NOT warranted (no cross-module refactor).
- **A/B base leg: NOT fired — zero reds in any leg.** The only anomaly (pack harness exit 1) was classified pre-existing by git evidence: `git log/diff a577d4893..HEAD -- test/packs/` is EMPTY — the branch never touched any ri_* pack script.

## 1. Independent re-run of bounded suites (W1 `eee033c8`) — PASS 71/71

All invocations via `timeout 300 ./scripts/run_tests_scrubbed.sh -p no:cacheprovider -q --tb=short <files>` (ambient POSTGRES_* scrubbed; host may carry POSTGRES_DB=ensemble_prod — never hit). Marker pre-check: none of the 5 files carry integration/postgres/slow/conformance markers → all collect under default addopts; `--override-ini` fallback not needed.

| File | Expected | Collected | Passed | Failed | Skipped | Exit |
|---|---|---|---|---|---|---|
| tests/unit/services/test_b_kill_switch_registry.py | 15 | 15 | 15 | 0 | 0 | 0 |
| tests/unit/services/test_b_fail_open.py | 9 | 9 | 9 | 0 | 0 | 0 |
| tests/unit/services/test_report_integrity_guard.py | 37 | 37 | 37 | 0 | 0 | 0 |
| tests/integration/test_ri_off_behavioral_probe.py | 5 | 5 | 5 | 0 | 0 | 0 |
| tests/integration/test_report_integrity_repro.py | 5 | 5 | 5 | 0 | 0 | 0 |
| **Combined (cross-file shape)** | 71 | 71 | **71** | 0 | 0 | 0 |

Developer's green claim VERIFIED. ~29s test execution total. Truth-table coverage (unset/blank/`1`/`0`/`off`/garbage) confirmed covered by the registry pins — no duplication needed. Hygiene: porcelain 0 rows before/after; `install-audit.jsonl` 346→346 lines, sha256 unchanged (`881e133a…`) — writer-test hazard did not fire. Warnings cosmetic only (DeprecationWarning event-loop ×2, SAWarning sqlmodel — pre-existing style).

## 2. Pack runs + re-baseline (W2 `9ad3c2ce`, W2b `99bf7edf`, W5 `f2012dd5`)

| Pack | Result | Counts | Notes |
|---|---|---|---|
| `test/packs/ri_guard_enforcement_unit_test.sh` | PASS (test-level W2; exit 0 + `RESULT: PASS` after W5 harness fix) | **76/76** (6.6–6.9s) | composition 37+9+15+11+4 confirmed exactly |
| `test/packs/ri_off_behavioral_probe_test.sh` | PASS exit 0 | **5/5** (1.3s) | grew 4→5; new `[unset]`→default-ON case |
| `test/packs/ri_incident_repro_integration_test.sh` | PASS exit 0 (re-run post-edit) | **5/5** (1.2s) | count unchanged |

- **Pack-harness defect found & fixed (test code, 1 line, pre-existing):** `ri_guard_enforcement_unit_test.sh` aborted on the SUCCESS path with `EXIT_CODE: unbound variable` (exit 1, no RESULT line) — `|| EXIT_CODE=$?` at :103 never assigns when pytest exits 0 and `set -u` kills at :104. Fix: `EXIT_CODE=0` init before the pytest invocation. Post-fix: exit 0, `RESULT: PASS`, 76/76. **Pre-existing at base** (branch untouched `test/packs/`); the sibling scripts do not share the bug (probe captures `PYTEST_RC=$?` unconditionally; repro uses bare `CMD; EXIT_CODE=$?` — cosmetic residual there: on a FAIL path `set -e` aborts before its RESULT-FAIL echo, exit code still propagates correctly; left untouched, out of scope).
- **Re-baseline commit `b70da96bf`** (`test: re-baseline ri_* packs after b-guard default promotion`, path-limited, 4 files +31/−22; amended once to fold in the enforcement header's stale `default falsy = LOG-ONLY ship state` prose): PACKS.md rows 2117/2119 (counts 72→76, 4→5; Last Run 2026-10-31 @ 0ecdc4fc1 → post-amend tree b70da96bf; semantics corrected to ship-default-ON), enforcement header counts 34→37 / 11→15 / 69→76 (+ provenance parentheticals now cite measurement), probe + repro headers' ship-default-OFF/unset prose corrected to default-ON semantics with actual pytest ids. Assertions: `rev-list --count a577d4893..HEAD` = 6, exactly 4 files in commit, porcelain empty, no upstream (amend safe).
- **Brief-arithmetic correction (evidence over brief):** task brief said header 69→73; measured truth is **69→76** (BOTH 34→37 and 11→15 grew: 69+3+4=76). Re-baseline uses measured values.
- Note (pre-existing pack property, informational): probe pack invokes `.venv/bin/pytest` directly (no scrubbed wrapper); probe is SQLite/file-backed so ambient PG env is inert.

## 3. Isolated live boot E2E — activation proof (W3 `ca76201e`) — PASS, all 3 phases

Isolated boots from the worktree on `127.0.0.1:8095`, `DATA_DIR=/tmp/envflag-test-<ts>-<phase>` (removed after), `.env` sourced (421 B, contains no WC_ vars → Phase A is a true default boot). All kills by exact recorded PID; port 8088 untouched; porcelain clean throughout.

- **A — default boot:** livez **200 @ 10s** (`{"status":"alive","uptime_seconds":3.77,"version":"0.28.0"}`). boot.log:64: `Report-integrity (b) terminal-waiting guard resolved: ENABLED (default — enforcement: one adjudication notice injected to the parent at the completion stamp; never blocks) (env leg only; explicit report_integrity.b_terminal_waiting_guard_enabled: false in config.yaml still vetoes) (env WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED=<unset>). Restart required to flip.` — ENABLED **with config-veto note** ✓
- **B — explicit `=0`:** livez **200 @ 8s**. boot.log:64: `...resolved: DISABLED (explicit =0 — log-only mode: the [ReportIntegrityGuard] soak log still fires at completion stamps; no notice is ever injected) (env ...=0). Restart required to flip.` ✓
- **C — negative, set-but-BLANK:** boot **FAILS @ ~3s, exit 1, no listener ever bound** (all polls `000`, `lsof -ti :8095` empty). `pydantic_core._pydantic_core.ValidationError: 1 validation error for ReportIntegrityConfig · b_terminal_waiting_guard_enabled · Input should be a valid boolean, unable to interpret input [type=bool_parsing, input_value='', input_type=str]` raised at `load_config → Config(**config_dict)` from `daemon/api.py:4870 create_app` — documented production boundary PROVEN. ✓

## 4. Docs + static verification (W4 `cc534a00`) — PASS 4/4

1. `.env.example` (373–430) vs `.env.prod.example` (154–211) documented-flag block: **`cmp` exit 0 — byte-identical** (6549 B / 58 lines both). Full-file diff hunks all outside the block and legitimately dev-vs-prod (port 8079/9797, HOST, PG db/password placeholders, prod-only readiness/image-gen blocks, dev-only verbose docs). One benign diff alignment artifact at the block's leading separator — no substantive difference.
2. `docs/setup.md:445` — exact MATCH vs `daemon/config.py:4622–4683` (`ReportIntegrityConfig`, `Field(default=True)`, authoritative-semantics docstring): unset→ON; blank/UNKNOWN→boot FAILS (pydantic ValidationError on the binding); `=0`→OFF kill switch / revert path (NEVER blanking); resolver leniency = config-less readers only; YAML-false-veto / YAML-true-never-forces; restart-required.
3. Flag census: **47 = 47** in both files (pattern `^# [A-Z_][A-Z0-9_]* —`; 30 kill-switch ship-ON + 16 opt-in + 1 tri-state) — consistent with "~46 binary flags".
4. ensure.md Core static: `dev.sh` carries `--timeout-graceful-shutdown 10` (line 155) ✓

## 5. ensure.md status (scoped, Tier 1)

- Critical "no regressions in changed packs": **PASS** (all scoped ri_* packs green, pre-existing harness bug fixed and classified).
- Critical deadlock/concurrency pack: **out of blast radius** (diff touches env-flag resolution, pydantic config, boot log, docs — no concurrency/async-loop paths).
- Critical dev.sh flag: **PASS** (static).
- Release Gate: not triggered (not a big/critical/architecture change).
- Contradictions: none — all validations ran as packs with dual-layer timeouts.

## 6. Worker ledger

| Worker | Instance | Node | Result |
|---|---|---|---|
| W1 suites | `eee033c8-9249-44ec-b551-9b73ae9071b8` | n-suite | PASS 71/71 |
| W2 enforcement pack | `9ad3c2ce-fea6-4558-8b30-9db6df8b7a1f` | n-pack-enf | PASS 76/76 (+harness-bug find) |
| W2b probe pack | `99bf7edf-817c-4a79-9dc6-25f7d718a5bf` | n-pack-probe | PASS 5/5 |
| W3 boot E2E | `ca76201e-6a3a-4f4e-8bd2-8ead64272f69` | n-boot-e2e | PASS 3/3 phases |
| W4 docs static | `cc534a00-e427-4e40-9841-4fd89321c864` | n-docs | PASS 4/4 |
| W5 re-baseline | `f2012dd5-884d-402a-8319-7022403724ef` | n-rebaseline | commit `b70da96bf` |

## Overall

- Unit/integration suites: ✅ 71/71 · Packs: ✅ 76/76 + 5/5 + 5/5 · Boot E2E: ✅ 3/3 · Docs: ✅ 4/4
- Reds: **0** anywhere → base A/B leg not required; only anomaly classified pre-existing with git evidence
- Quick fixes applied: 1-line pack-harness `EXIT_CODE` init (test code, pre-existing bug) — included in `b70da96bf`
- **Verdict: SHIP**
