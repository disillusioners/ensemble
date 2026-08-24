# P2.3 Final Verification — Promotion Ladder + Drills (MERGE GATE)

- **Date:** 2026-08-24 · **Tester:** tester agent (Test Leader) — 15 worker dispatches, 0 direct executions
- **Branch:** `feature/self-restart-p2p3-ladder-drills` @ `202b9488bbb1011571d73c29b17983a832970fcd` (21 commits over base `74040e64` == P2.2 merge commit; verified by 3 independent workers)
- **Verdict: ✅ PASS — MERGE-READY** (zero branch-introduced failures; all phase-pack claims independently reproduced; e2e gate ruled NOT TRIGGERED; live-reach by construction = 0; demo ledger 3-clean/BLOCKED-f2-open confirmed)

## 0. Scope Decision

**Full suite run — warranted:** final merge gate for a 21-commit phase (38 files, +6360/−121), release-gate class per ensure.md. 108 self-contained packs executed across 8 parallel worker batches (A–G); 19 daemon-required packs (live-dev-daemon/playwright/OPENAI_API_KEY class) excluded — e2e gate ruled NOT TRIGGERED (§3), so the release-gate e2e scenarios did not apply. All pack runs verification-only (zero quick fixes, zero repo mutations; every worker recorded `git status --porcelain` before/after — only the two pre-existing `.agents/approver/active.md` + `.agents/tidier/notes.md` entries ever present; HEAD unchanged).

## 1. Full regression suite — pack results

### Batch A — P2.3 phase packs (11/11 PASS, 1020 tests)

| Pack | Result | Counts (verbatim) | Dev claim | Δ |
|---|---|---|---|---|
| upgrade_alerting_unit_test | ✅ | `72 passed, 0 failed` | 72/72 | exact |
| drill_ledger_unit_test | ✅ | `82 passed, 0 failed` | 82/82 | exact |
| watchdog_watcher_unit_test | ✅ | `60 passed, 0 failed` | 60/60 | exact |
| launcher_supervisor_unit_test | ✅ | `195 passed, 0 failed` | 195/195 | exact |
| release_journal_unit_test | ✅ | `271 passed, 0 failed` | 271/271 | exact |
| boot_probes_unit_test | ✅ | `75 passed in 8.36s` | 75/75 | total exact; per-file split stale (see F-2) |
| upgrade_tool_interlock_unit_test | ✅ | `142 passed in 3.03s` | 142/142 | exact |
| upgrade_registration_unit_test | ✅ | `21 passed in 2.60s` | 21/21 | exact |
| frozen_tool_name_discovery_unit_test | ✅ | `6 passed in 0.85s` + `test_known_tool_names_matches_source_exactly_no_drift PASSED` (single-node `-v` quoted) | 6/6 | exact |
| deploy_pipeline_unit_test | ✅ | `53 passed, 0 failed` | — | baseline |
| stop_ownership_unit_test | ✅ | `43 passed, 0 failed` | — | baseline |

### Batches C/E — LLM + tools/frontend (26/26 PASS)

- **Batch C (16/16):** llm_config_override 31 · llm_error_classifier 74 · llm_failover 64 · _adversarial 36 · v2 45 · v2_adversarial 48 · v2_resilience 20 · graph_retry 19 · loop_breaker_integration 20 · loop_detector 28 · loop_repairer 29 · gii_throttle 42 · reasoning_content_regression **43/43 baseline-exact** · reasoning_echo_targeted **51/51 baseline-exact** · image_regression 115 · image_tools 101. Failover family 213 tests incl. 9-site zero-drift AST pins — clean.
- **Batch E (10/10):** tools_suite **`843 passed, 5 deselected, 0 failed`** — deselect set == EXACTLY the 5 quarantined `TestAccessMemoryArchive` tests (the mandated "confirm exactly those 5" — PASS; +7 passes vs 836 baseline = new branch upgrade-tool tests) · registry_validation 140 · tool_config_validation_boot 2 · filesystem_resolver 31 · filesystem_tools 38 · workspace_frontend 298 · workspace_guard 48 · frontend_full 2092 · app_component 50 · opencode_native_tools 505.

### Batches B/D/F1/F2/G — remaining 71 packs

PASS: 59 packs (full tables in /tmp/p23-*.log; counts verbatim in worker reports). FAIL: 6 packs / 9 failure clusters — **ALL attributed PRE-EXISTING at base `74040e64` by worktree re-run** (§4):

| Pack (batch) | HEAD result | Base result | Attribution |
|---|---|---|---|
| job_queue_tools (B) | 1F/72P/4deselect | same command: **1F/72P/4deselect byte-identical** | PRE-EXISTING |
| turn_transitions_reconciler (B) | exit 2, 0 tests collected (`ModuleNotFoundError: hypothesis`) | same 2 collection errors, same lines | PRE-EXISTING |
| c2_core_regression (B) | 38F/167P | 38F in test_manager.py, migration root, same | PRE-EXISTING |
| shared_context_regression (D) | 41F/710P | **41F/710P identical** incl. test_agents_api 34-vs-1 | PRE-EXISTING |
| c2_pg_manager (G) | 38F migration-root + 1F kwargs-drift | both reproduce identically (line 594) | PRE-EXISTING |
| skill_evolution_pg (G) | exit 4, `test_auto_load_skills.py` missing | missing at base too (deleted `eeef8845`, ancestor of base) | PRE-EXISTING |
| wanderer_completion_pg (G) | 2 setup errors (opencode_sessions TRUNCATE) | conftest + repository.py untouched by branch (`36461edd` ancestor) | PRE-EXISTING |
| integration_test (F2) | 19F/196P, 5 clusters | **19F/196P/180deselect identical, all 5 clusters** | PRE-EXISTING |
| mock_job_queue (B) | exit 0 "PASS" but 48 setup errors | identical at base (false-positive harness) | PRE-EXISTING |
| core_unit (F1) | 41F/710P | matches PACKS.md-documented baseline (41 pre-existing, +13 passes, 0 new) | PRE-EXISTING (documented) |

### Standing rule — concurrency multi-run evidence

**Alerting t10d battery: 12 sequential runs (≥10 required), 12/12 PASS, zero variance.** Durations 2.919–3.028s (mean 2.961s, σ=0.030s, **CV 1.01%**). Per-run table preserved in worker report + `/tmp/p23-alerting-run{1..12}.log`; direct xtrace evidence: `SCENARIO t10d: PASS — journal NEVER torn | torn=[]` and `tmp-name uniqueness | unique=120/120 clashes=0` (5 unique t10 marker lines, all PASS).

## 2. Quarantine confirmation

tools_suite deselect == exactly the 5 `TestAccessMemoryArchive` tests (QUARANTINE.md 2026-08-20): `test_access_archive_valid_path`, `test_access_archive_path_traversal_rejected`, `test_access_archive_invalid_format_sanitized`, `test_access_archive_nonexistent_returns_not_found`, `test_access_normal_file_still_works`. Pytest reported exactly "5 deselected" — no second deselect source. job_queue_tools' 4 `TestJobContinue*` deselects verified documented in QUARANTINE.md lines 19–22, mechanism = pack `--deselect` flags added in `63f29197` (ancestor of base). **No new quarantines needed; no new failures.**

## 3. INDEPENDENT e2e-GATE RULING (mine, final)

**NOT TRIGGERED.** Rule (ensure.md:44–53): mandatory full e2e if changes touch job/task/queue system — enumerated five trigger systems. Re-derived over the FULL range `74040e64..202b9488` by a dedicated worker (I reviewed the evidence):

- **Canonical file map:** claim_pending_task + reconcile_turn_mirror → `daemon/repositories/task/repository.py` (+ `instance_lifecycle.py` wrappers); turn_transitions → `daemon/services/turn_transitions.py`; job_processor → `daemon/services/job_processor.py`; job_locks → `lock_repository.py` + `job_lock_manager.py` + `job_queue/models.py`.
- **File-level ∩ diff = ∅** — none of the 8 canonical files in the 38-file diff.
- **Symbol-level ∩ added/removed lines = ∅** — zero hits across all 8 trigger patterns (`claim_pending_task|turn_transition|TurnTransition|ResumeTurn|reconcile_turn_mirror|job_processor|job_locks|uq_job_locks_slot`), so no production/test/doc classification was even needed.
- **Proximity reviewed:** `daemon/tools/job_queue.py` IS in the diff but changes only the `job_create` empty-caller source-stamp (anti-forgery factor 2) + a docstring — not JobItem creation, claim, turn, mirror, processor, or lock machinery. Its one failing test fails identically at base. `daemon/api.py` adds `_register_upgrade_alert_sink` (SSE wiring) with no new calls into trigger modules (imports pre-existed).
- Corroborated post-hoc by the batch-B/G results: job_queue sweep 1530P/38sk/0F **baseline-exact**, admission_starvation 6/6, claim_guard_locks 168/0, concurrency 91P/74S/0F baseline-exact — the five systems' behavior is unchanged.

Ruling: developer + reviewer five-path ∅ claim **independently confirmed**. Release-gate e2e scenarios not run (gate not triggered); full non-integration suite WAS run via packs (ensure.md Release Gate form).

## 4. Pre-existing failure register (base-attribution evidence)

Dedicated worker recreated base `74040e64` in `/tmp/p23-base-wt` (`uv sync --extra dev`) and re-ran every failing scope. **All 9 clusters reproduce identically; the branch contains zero commits touching the affected files.** Root causes (for the hygiene backlog, NOT this merge):

1. **Migration `20260714_000001`** ships PG-only `ALTER TABLE … DROP CONSTRAINT IF EXISTS`; SQLite runner rejects → kills every `InstanceManager(mock_config)` construction on SQLite. Accounts for c2_core 38F, c2_pg_manager 38F, shared_context 41F (via nested core_unit run), core_unit 41F (documented baseline), integration cluster A 14F. Files last touched 2026-07-14/07-31 — pre-branch.
2. **`hypothesis` undeclared** (absent from pyproject/uv.lock at base AND HEAD) → turn_transitions pack collects 0 tests (exit 2) — pack never runs its 4 target suites.
3. **`tests/unit/test_auto_load_skills.py` deleted** (`eeef8845`, ancestor of base) but `skill_evolution_pg_test.sh` still names it → structural exit 4.
4. **`opencode_sessions` absent from ensemble_test** — conftest dynamic TRUNCATE over `SQLModel.metadata.sorted_tables` includes the table (SQLModel added `36461edd`, ancestor of base) → wanderer_completion_pg 2 setup errors (15/17 tests pass).
5. **`mock_job_queue_test` false-positive harness** — `tests/mock_test_job_queue_api.py:1027` swallows `pytest.main` exit code → pack always exits 0 while all 48 tests error in setup (`JobLockManager(lock_repo)` signature change `17551447` postdates fixture `41633433`). **Effective coverage 0 — flagged as the highest-value hygiene item.**
6. **integration clusters B–E:** cold_resume_ttl ×2 stale PAUSED→CANCELLED asserts (c171a289 semantics — same family as the already-quarantined e2e pair); migration critical_notes row-loss 3→0; skill feedback ordering; skill on-miss feedback creation.
7. **c2_pg_manager distinct kwargs-drift** (`suspension_reason='awaiting_answer'` vs bare call) — identical at base, line 594.
8. **job_queue_tools 1F** (`test_job_create_explicit_source_not_overridden`, `assert 'agent:test-agent' == 'manual'`) — identical at base; P2.2-era source-forcing behavior; note this is the anti-forgery direction the P2.3 ledger explicitly carries ("job_create forcing unconditional" carry-over).

## 5. Live-reach by construction — 0 reachable

Static audit of all 6,398 added lines: **LIVE-REACHABLE = 0, NEEDS-JUDGMENT = 0.** `CANONICAL_PORT_DEFAULT="9797"` at `watchdog-watcher.sh:151` is the single sanctioned literal (single-source rule enforced in-file, strengthened by the diff); zero other unsanctioned port literals in code/shell (all other 4-5-digit hits = timestamps/ms-multipliers/sleeps/fixture PIDs). Watchdog is explicit-only `INSTALL_DIR="$1"` with FATAL refusal (no default anywhere). `promote.sh` ADDS the f2-not-verified second refusal factor for TARGET=live (gate strengthened). Executor env-allowlist **byte-identical** base↔HEAD (`PATH,HOME,INSTALL_DIR,PORT,POSTGRES_DB,TMPDIR` + `PG*`) — live keys still stripped at the only spawn seam. All `kill` additions are `kill -0` liveness probes (EPERM=ALIVE). No pkill/killall. Zero ENSEMBLE_*_LIVE setters.

## 6. Demo-state validation (read-only) — CONFIRMED

- **Ledger checker (verbatim, repo script):** `--f2-state open` → cycles: 4 (1 SUPERSEDED v0.10.7-p2.3-b65 + **3 CLEAN v0.10.8-p2.3-b7cyc1** @ 22:06:58/22:33:21/22:42:47Z), `consecutive clean: 3 (need 3, ADR-021)`, **gate verdict: BLOCKED — F2-open §9 hard-block regardless of cycle count**. Counterfactual `--f2-state closed` → **ELIGIBLE** — gate proven state-driven (identical journal, single pivot).
- **3-clean independently re-derived from raw journal:** cycle windows contain zero rollback/sweep_rollback/halt; all 3 cycles ari-driven (launcher.log `system-execution fired promote executor` run_ids + `pending_op.armed_by_instance` Ari UUID, `trigger=post-turn-callback`, human-confirmed=false).
- **Probes:** `/livez` 200 alive, `/readyz` 200 `reasons: []` draining=false. `current → releases/v0.10.8-p2.3-b7cyc1`, `quarantined: []`. `/livez` version "0.10.5" = baked `daemon.__version__` (manifest.binary_version) vs release label v0.10.8-p2.3-b7cyc1 (manifest.version) — distinct identifiers, divergence by design.
- Minor (non-gate): cycle-3 pending_op not yet swept (deferred sweep, fresh-txn leave-alone) — checker counts commit windows; unaffected.

## 7. Mock-quality spot-check — REAL-SEAM (not tautologies)

- **upgrade_alerting (incl. t10d):** production seam throughout — `from daemon.tools import upgrade_journal as uj`; fixtures built BY the production writer (`journal_init/journal_write`); alert path exercised through the real `register_alert_sink`/`_emit_terminal_class_alert` chain wired at `api.py:169`; T8/T10c run REAL `lib.sh` txn helpers in bash subprocesses (`journal_open_txn/close_txn` at lib.sh:529/594); t10d = real `threading.Barrier(3)` on the real journal (2 writer types + continuous reader catching `JournalTorn`; `os.replace` wrapper calls the original — observational; asserts torn=[] AND captured-set tmp-name uniqueness 120/120). Mutation-resistance sampled REAL on 4/5 assertions (t2a ts-parity is a coherence invariant by design). Claimed 72/72 statically reconciled: 33 scenarios × 2 + 2 driver + 4 self-checks = 72.
- **drill_ledger (incl. case-fold t6f–t6h):** SUT = real `ledger_check.py` subprocess with real exit codes; case-fold logic under test is production (`ledger_check.py:143-161` normcase+casefold+samefile); over-refuse counter-test (t6h demo-name accepted) proves non-naive matching. Input journals printf-built (flagged, not disqualifying — reader-SUT input construction; writer↔reader interop covered by alerting T8 + test_release_journal.sh). 82/82 reconciled: 78 asserts + 4 explicit pass branches.
- Zero `tests/mocks/*.py` added in P2.3 (test doubles embedded in self-contained packs per convention) → no MOCK_TESTS.md gap.

## 8. DR evidence audit (2–3 files + summary)

- **DR-1** FAIL→fix→PASS arc honest: initial FAIL (F-DR1-1: exit-75 preflight unreachable on frozen-binary path) → fix `91ace51c` (frozen entry owns boot-DB preflight — explains run_app.py + __main__.py in diff) → re-run PASS 7/7 (5 exit-75 cycles, 5→10→20→40→60 capped tempfail track, crash_count 4→4 exempt from honest baseline, `.env` md5 bit-exact).
- **DR-2** PASS 5/5 — exit 78 captured, zero respawns, sandbox fully torn down (port 8377, throwaway PG dropped).
- **Live-pid checkpoint discipline:** pid 31150/31130 lstart `Aug 22 10:04:07` byte-identical across DR-1 (×4), DR-1-rerun, DR-2; `<live-port>` redaction at capture time; 3-part assert-then-TERM on every stop. Cross-file consistent.
- **Phase-exit-summary:** 6/6 exit criteria mapped to evidence commits; operative verdict documented as BLOCKED (f2-open) — matches §6 independent finding.

## 9. ensure.md validation

| Requirement | Status | Evidence |
|---|---|---|
| Core C1: no regressions in changed packs | ✅ | all phase packs PASS (§1 batch A); 9 failure clusters all pre-existing at base (§4) |
| Core C2: concurrency_atomic PASS | ✅ | `91 passed, 74 skipped, 0 failed` **baseline-exact** |
| Core C3: no sync DB on event loop | ✅ | same pack (thread-identity tests), PASS |
| Core C4: dev.sh `--timeout-graceful-shutdown 10` | ✅ | grep verbatim hit line 102 |
| Important I1: awaits converted properly | ✅ (via C2 pack) | no new sync-DB paths added (diff additive) |
| Important I2: deadlock scenario works | ✅ | same pack PASS |
| Release Gate: full non-integration suite | ✅ | 108 self-contained packs via 8 parallel batches, each `timeout 300` |
| Release Gate: e2e scenarios | N/A — gate NOT TRIGGERED | §3 ruling (independent, five-path ∅) |

No contradictions with ensure.md methods observed (all validations ran as packs with dual-layer timeouts).

## 10. Findings (triaged, none merge-blocking)

- 🟢 F-1 (hygiene, highest value): `mock_job_queue_test` pack is a **false-positive harness** (swallows pytest exit; 48/48 setup errors; effective coverage 0). Pre-existing; recommend fixing the harness or quarantining the pack in a hygiene pass.
- 🟢 F-2 (doc): stale dev-claim decomposition — boot_probes "main_entry 23/23" is now 27 (`test_main_entry.py` 27 + `test_health_probes.py` 48 = 75 total invariant).
- 🟢 F-3 (doc): PACKS.md registration gap — 6 packs on disk unregistered (`e2e_existing_ab`, `e2e_existing_c`, `jq_error_reporting_adhoc`, `plane_domain_access`, `concurrency_atomic`, `vscode_e2e_browser`). Backfilled in PACKS.md this session.
- 🟢 F-4 (hygiene backlog): pre-existing register §4 items 1–4 (SQLite-incompatible migration, undeclared `hypothesis`, dead pack reference, conftest TRUNCATE list) — each deterministic, each predating the branch; candidates for a dedicated test-hygiene initiative.
- 🟡 Noted (known, by design): F2 forge-lane remains OPEN — gate correctly enforces BLOCKED; live promotion stays user-gated per phase plan. Cycle-3 pending_op unswept (deferred sweep by design).

## 11. Overall

- Unit/full-suite (108 packs): ✅ PASS — zero branch-introduced failures
- Concurrency evidence (12× t10d + concurrency pack baseline-exact): ✅ STABLE
- e2e gate: ✅ ruled NOT TRIGGERED (independent five-path derivation)
- Live-reach: ✅ 0 by construction
- Demo state: ✅ 3-clean @ v0.10.8-p2.3-b7cyc1, operative verdict BLOCKED (f2-open) — correctly enforced
- Mock quality: ✅ REAL-SEAM
- **Testing Complete: ✅ READY — merge cleared**
