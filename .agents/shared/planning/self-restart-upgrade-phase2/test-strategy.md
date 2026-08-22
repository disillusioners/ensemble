# Test Strategy — Self-Restart / Self-Upgrade Phase 2 (P2.1 / P2.2 / P2.3)

- **Date:** 2026-08-22 · **Author:** W3 (test-strategy / promotion-ladder / decisions)
- **Wave:** 3-worker planning wave, branch `plan/self-restart-upgrade-phase2` @ `653e8e71`
- **Sibling docs (owned by others — referenced, never authored here):** `plan-overview.md` + `phaseN-plan.md` (W1), `tool-api-design.md` + `risk-register.md` (W2)
- **Governing ADRs:** ADR-005 (D2 APPROVED: cap 3/24h), ADR-009, ADR-012, ADR-014, ADR-015 from `.agents/shared/planning/auto-restart-upgrade/decisions.md`; deviations recorded as ADR-016…020 in `decisions.md` (this dir)

> **NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.**

**Environment topology (verified):** dev = 8079, repo, never a deploy target (`scripts/deploy.sh:13-17`) · demo = 7979, `~/agents-ensemble-demo`, PG `ensemble_demo`, the rehearsal target · live = 9797, `~/agents-ensemble`, READ-ONLY, out of bounds · sandbox = ad-hoc install dir + throwaway PG (e.g. `ensemble_test`) + custom port (8377 precedent, RESULTS file §1 "exit-75" row).

---

## 1. Test Level Matrix

Legend: **U** = unit (repo, pack-runnable, no daemon) · **I** = integration (repo, mocked or sandbox daemon) · **SB** = sandbox drill harness (own install dir + own port + throwaway PG; throwaway = created and dropped per run) · **D** = demo drill (7979, `ensemble_demo`, under observation; every mutation is a planned drill step, never incidental) · **L** = live — FORBIDDEN in this initiative; anything that would need it is USER-GATED (see `promotion-ladder.md`).

### P2.1 — Release & upgrade pipeline (releases/ trios, manifest, journal, rollback.lock.d mkdir-lock, current symlink; env-parameterized stage/promote/rollback; integrity + version smoke; auto-rollback; cap 3/24h; launcher journal-sweep ADR-012)

| Area | Level | Where | What it proves | Pass criteria (objective) | Artifact |
|---|---|---|---|---|---|
| Journal write/read atomicity | **U** | repo pack | `releases/state.json` transitions (idle→in-flight→committed / →rolled-back) are atomic writes; torn-write safe | Fixture state.json round-trips all txn states; crash-mid-write simulated (truncated write) detected/rejected | pack log + `test/packs/` new pack script |
| Journal sweep logic (ADR-012) | **U** | repo pack | Sweep decision table: in-flight >600s & flipped → execute rollback; >600s & not flipped → clear txn; ≤600s → leave. Sweep **counts as auto-rollback** for ADR-005 counters | All decision-table rows green against fixture `state.json` + fixture release dirs; counter/quarantine mutations asserted | pack log |
| Cap 3/24h + cooldown 10min + quarantine | **U** | repo pack | Journal counter math: 4th rollback in 24h → halt-for-human state; rollback inside cooldown → refused; quarantined version skipped by promote resolution | Boundary cases (3→4, 24h±1s expiry, cooldown±1s) assert exact states | pack log |
| Manifest integrity + version smoke | **U/I** | repo pack | Trio checksums verify; `manifest.json` fields (`binary_version`, `rollback_safe`, checksums — ADR-004 M5) validated; version smoke (see ADR-027 in `decisions.md`) compares expected vs reported version | Corrupted-byte fixture fails checksum; tampered manifest fails validation; smoke mismatch → refuse promote | pack log |
| stage → promote → gate → commit cycle | **SB** | sandbox install (own port e.g. 8378, throwaway PG) | The full happy pipeline against a real sandbox launcher install: stage trio → preflight → SIGTERM bounded → atomic flip → restart → `/livez`≤60s + `/readyz`≤120s (`deploy.sh:112-113` budgets) → 300s soak (ADR-005) → journal commit | Journal ends `committed`; `current` symlink → new version; `--launcher-state`/`data/launcher.log` show clean stop/start; version verify green | sandbox journal copy + launcher log excerpt + RESULTS file |
| Promote → gate-fail → auto-rollback | **SB** | sandbox | Failure leg: induce `/readyz` fail on target via `ENSEMBLE_READINESS_FORCE_DEGRADED=1` in the sandbox `.env` — one-way fail-safe, **cannot false-green** (`daemon/services/readiness.py:48-67`, verified) | Within 10-min outer window: flip-back to `previous`, journal `rolled_back`, cooldown stamp set, counters incremented, halt only at cap | sandbox journal + launcher log |
| Full e2e regression (daemon code) | **e2e** | dev repo | Any P2.1 PR that touches daemon job/task/queue internals → release gate (see §2) | ensure.md release-gate items 1–5 green | RESULTS file per ensure.md convention |
| Promote drill under observation | **D** | demo 7979 | The same pipeline, real daemon, real PG `ensemble_demo`, rehearsal conditions | Same criteria as sandbox cycle + demo env identity asserted 4× (path/port/DB/engine marker — DevOps precedent, RESULTS §2 row (f)) | drill record in `.agents/tester/RESULTS/` + journal entry |

### P2.2 — Agent-facing tools (`daemon/tools/upgrade_tools.py`: `system_restart` + `system_upgrade` + `release_info` + `upgrade_status`; [4 tools — architect 2026-08-22] category `system_upgrade`; ari `tools.allow`; env-target permission model)

| Area | Level | Where | What it proves | Pass criteria | Artifact |
|---|---|---|---|---|---|
| Tool registration resolution | **U** | repo pack | Category expansion reaches ari's allow-list: `tools.allow: ["system_upgrade", …]` resolves to concrete tool names; **other agents default-deny** (no allow → tool invisible — ADR-015 registration mechanics) | allow-list resolution unit asserts exactly {system_restart, system_upgrade, release_info, upgrade_status} ∪ existing 14 ari entries for ari; a non-allow agent's constructed tool surface contains none of the four | pack log |
| KNOWN_TOOL_NAMES drift lock | **U** | repo pack | New tools must join the frozen-binary fallback set — `tests/unit/tools/test_frozen_tool_name_discovery.py:223` drift test must stay green when `daemon/tools/` changes (verified convention: source ∪ `KNOWN_TOOL_NAMES`) | `test_known_tool_names_matches_source_exactly_no_drift` PASS after adding the 4 tools; regen run **in source mode only** | pack log |
| Gate enforcement — env-target | **U/I** | repo pack | Env-target permission model (ADR-017): demo/dev/sandbox targets execute freely (user directive — no human-confirmation gate on non-live targets); **live target requires the 3-factor runtime gate (D-FA3.1: param + HUMAN-origin marker + action-binding nonce) — a fabricated `user_confirmed: true` must NOT unlock live** | Matrix test: (live, no confirm) → refuse; (live, `user_confirmed:true` fabricated by agent, no marker) → refuse; (live, genuine user session marker + explicit confirm) → allowed; (demo, no confirm) → allowed. **Scope note: the live-confirm ALLOWED case applies to `system_upgrade` only — live `system_restart` is refused outright this initiative (A2/§3.1), so its matrix is refusal-only. Zero-live-contact: all cases are unit-mocked at the gate layer — no test ever sends a request to any live port** | pack log |
| Gate enforcement — fail-closed refusal additions (S-31) | **U/I** | repo pack | Three fail-closed refusal paths beyond the base matrix: `nonce-verification-unavailable` (daemon restarted between nonce issuance and consumption → HUMAN row wiped by the ephemeral-MessageQueue cleanup → refuse, "re-run dry_run"); `env-marker-absent` (`ENSEMBLE_SELF_ENV` missing from `INSTALL_DIR/.env` → every ACTOR tool refuses; read tools still answer); `layout-divergence` (journal exists but `current/` unresolvable → all pipeline mutations refuse per D-FA5.3) | Each path refuses with the exact reason string and **zero side effects** (no txn opened, no flip, no lock left held); `env-marker-absent` still serves reads | pack log |
| Gate enforcement — env identity | **U/I** | repo pack | The tool must not trust caller-claimed env; it resolves self-env from the staged `ENSEMBLE_SELF_ENV` marker in `INSTALL_DIR/.env` (D-FA2.3; staged by P2.1 T2) and refuses any `target_env` ≠ marker value (self-match before live-gate logic, D-FA2.4) | Unit-mocks all four marker values (dev/demo/live/sandbox) → self-match resolves correctly, cross-env refused; **marker absent → actor tools refuse fail-closed (`env-marker-absent`, S-31) with zero side effects — read tools still answer**; **PORT-derivation fallback asserted ABSENT: the test verifies port-based derivation is NOT attempted** (D-FA2.3 rejected mechanism) | pack log |
| Ari-driven restart drill (P2.2) | **SB** | sandbox | Ari-driven restart on sandbox: tool call → graceful stop (SIGTERM path, not raw kill — ADR-016 constraint) → launcher respawn → tool result delivered post-restart | Restart observed; ari's turn completes post-restart; sandbox journal/state consistent | sandbox launcher log + RESULTS file |
| Ari-driven upgrade drill | **SB→D** | sandbox first, then demo | Full ari-driven upgrade: `system_upgrade` → pipeline → gates → commit; progress/result sequencing verified | Journal `committed`; tool result = structured terminal state (not intermediate); version verify | drill record (§3 D7) |
| Concurrent-attempt refusal | **SB** | sandbox | `rollback.lock.d` mkdir-lock (D-FA5.1) / in-flight txn: second `system_upgrade` while one in-flight → lock refusal (lock = mkdir-acquired dir with `owner`/`run_id`/`heartbeat` files, stale-breakable >300s via `mv` to `rollback.lock.stale.<pid>`) | Second call returns structured refusal naming the owner txn; no double-flip; stale-lock break >300s works | sandbox journal (refusal entry) |
| Daemon-code regression | **e2e** | dev repo | See §2 — the P2.2 **sequencing seam** (post-turn restart via deferred pattern) is the only P2.2 area that may trigger the release gate | ensure.md items green when triggered | RESULTS file |
| Tool-call at stop boundary | **SB** | sandbox | Known semantics: an in-flight tool call at stop is LOST; turn resumes from node boundary (verified research). Assert the design accounts for it: `system_restart` must not be the in-flight-lost call — result written before stop, or delivered via resume path | Observed sequencing matches W2's chosen contract; deviation documented ⟪SEAM: exact result-delivery contract awaits architect enrichment via W2 `tool-api-design.md`⟫ | sandbox transcript |

### P2.3 — Rollout ladder + drills + 3 carry-overs + alerting

P2.3's "tests" ARE the drills (§3) + the ladder gates (`promotion-ladder.md`) + the N-clean-cycles measurement (§4). Unit-testable pieces:

| Area | Level | Where | What it proves | Pass criteria | Artifact |
|---|---|---|---|---|---|
| Alert emission logic | **U/I** | repo pack | Burst-abort, rollback-cap halt, promote refusal each emit a structured alert event carrying journal evidence (ADR-025 channel choice in `decisions.md`) | Each trigger path produces exactly one alert with correct kind + payload (journal snapshot) | pack log |
| Drill runbook existence + dryness | static | repo | The runbook (created by P2.3) covers all 8 drills with the restore steps that are NOT instant | All §3 drills have procedure + pass criteria + artifact fields; carry-over 3's restart-required note present verbatim | runbook file itself |
| Ladder bookkeeping | **U** | repo pack | Clean-cycle ledger staleness rule: a new release resets the count (see §4.3) | Ledger unit: version change between cycles → counter reset asserted | pack log |

---

## 2. E2E Gate Trigger Analysis (release gate per `.agents/tester/rules/ensure.md`)

**The rule, quoted verbatim (`ensure.md:43-53`, release-gate section):**

> ### Critical (release-gate)
> - [ ] Full non-integration suite green (excluding QUARANTINE.md)
>   - Validation: run ALL non-integration packs (see PACKS.md) in parallel, each with the 5-min cap; quarantined tests skipped. NOT a bare `pytest tests/` — run via the packs.
> - [ ] E2E: Normal parent→child workflow completes (happy path)
>   - Validation: `timeout 300 bash test/packs/e2e_workflows_ensure_test.sh` or `PYTEST_TIMEOUT=280 timeout 300 .venv/bin/pytest tests/e2e/test_e2e_workflows.py --override-ini="addopts=" --override-ini="timeout=280" -m integration -k "test_parent_child_workflow_happy_path" --tb=short -q`
> - [ ] E2E: Pause after spawn, then resume works correctly
>   - Validation: same pattern, `-k "test_pause_after_spawn_then_resume"`
> - [ ] E2E: Terminate after spawn, then revive documented
>   - Validation: same pattern, `-k "test_terminate_after_spawn_then_revive"`
> - [ ] E2E: 3-level cascade (leader→tester→staggered workers): reports delivered, no premature completion, no stuck completion, state switching
>   - Validation: `timeout 300 bash test/packs/e2e_workflows_ensure_test.sh` or `PYTEST_TIMEOUT=280 timeout 320 .venv/bin/pytest tests/e2e/test_e2e_workflows.py --override-ini="addopts=" --override-ini="timeout=280" -m integration -k "test_three_level_cascade_reports" --tb=short -q`

**Trigger rule (decision rule for every P2 PR):** a PR requires the FULL release gate iff its diff touches `claim_pending_task`, `turn_transitions`, `reconcile_turn_mirror`, `job_processor`, or `job_locks` — the job/task/queue system named in `ensure.md:44` framing (project critical note: "Full e2e test … MANDATORY if changes touch job/task/queue system").

| Planned P2 item | Touches job/task/queue core? | Gate? | Rationale |
|---|---|---|---|
| P2.1 `releases/` layout, stage/promote/rollback scripts, journal | No — install-dir + scripts only; no daemon queue code | **No** | Repo scripts + launcher; daemon untouched. Sandbox drills cover behavior. |
| P2.1 launcher journal-sweep implementation (fills `launcher.sh:151-174` stub) | No — launcher runs below the daemon | **No** | Launcher suite (`launcher_supervisor_unit_test` pack, 74/74 baseline) + sandbox sweep drills. |
| P2.2 tool registration (`upgrade_tools.py`, registry, ari `meta.json`) | No — registration seam (`instance.py` allow-list resolution) is not in the five core paths | **No** | Unit: registration + drift-lock (§1). |
| P2.2 `system_restart` / `system_upgrade` tool bodies (pipeline invocation, gating) | No — tools call the pipeline; they do not mutate queue internals | **No** (scope watch below) | Sandbox drills cover. |
| **P2.2 sequencing seam: post-turn restart via deferred pattern** | **MAY — if implemented via graph post-callbacks / turn-transition hooks** | **YES if it touches post-graph callback scheduling, turn transition emission, or `reconcile_turn_mirror` mirrors; NO if implemented as a pure job-queue-external timer** | ⟪SEAM: W1's phase2-plan.md fixes the mechanism; the gate trigger follows the diff, not the intent — the PR description must state which of the five paths the diff touches⟫ |
| P2.3 runbook / ladder / alert wiring (SSE emission sites) | No | **No** | Alert emission is additive publish sites. |

**Standing rule:** when in doubt, run the gate. The gate is also the default for any P2 PR whose blast-radius is classified big/critical per `ensure.md:5` (cross-module → release gate).

**Prerequisites whenever the gate runs** (`ensure.md:36-41`): daemon via `./dev.sh`, `unset SSL_CERT_FILE SSL_CERT_DIR`, `PYTEST_TIMEOUT=280` + `--override-ini="timeout=280"`, queue cleanup (`GET /api/jobs?status=pending`) before each test, one-by-one execution. **Venv gotcha:** run `uv sync --extra dev` first — bare `uv sync` STRIPS the dev extra and causes pytest-timeout drift (project critical note; RESULTS file §1 "venv/uv-sync" row).

---

## 3. Drill Matrix (drills are first-class tests)

**Carry-overs closed by these drills — quoted verbatim** (`.agents/tester/RESULTS/2026-08-22-ar-phase1-followups-verification.md:45-47`):

> 1. "Launcher retry loop not observed end-to-end live: the exit-75 smoke proves the daemon exits 75 and the launcher *message* says it will retry; the full real loop (exit 75 → supervisor actually respawns with capped backoff → recovery) remains unit-level (launcher suite) + sandboxed. Phase 2 (auto-restart hardening) should include one live tempfail→recovery cycle on demo."
> 2. "Exit-78 (config error) path remains unit-covered only (63/63) — consistent with pre-merge 'optional' framing; fold into Phase 2 live smoke if cheap."
> 3. "P7 drill on deployed daemon requires restart to restore (env knob read per refresh tick; `readiness.py:50-67`) — document in Phase 2 drill runbook so green-restore steps aren't assumed instant."

Every drill: **named procedure · safe induction method · objective pass criteria · named artifact**. Demo drills run on 7979 / `ensemble_demo` only. Sandbox drills: own port + throwaway PG (never live). All drill records land in `.agents/tester/RESULTS/` following the `2026-08-22-*` naming convention, plus a cumulative ledger section in the P2.3 drill runbook.

| # | Drill (source) | Env | Safe induction | Pass criteria | Artifact |
|---|---|---|---|---|---|
| **D1** | **Tempfail→respawn→recovery full cycle** (carry-over 1) | **demo** (the one live tempfail cycle Phase 2 owes) | Point the demo daemon's PG env at a dead socket (unreachable-127.0.0.1:port simulation, exact pattern of the exit-75 smoke, RESULTS §1) → daemon exits 75 → restore env → observe respawn | Full loop observed: exit 75 → launcher capped backoff (5s→60s, budget-EXEMPT per ADR-011) → respawn succeeds after restore → `/livez` green. Timestamps from `.launcher-state` + `data/launcher.log`. **Burst budget NOT consumed** (state file shows no count increment) | drill record: timestamped `.launcher-state` copies + log excerpt + RESULTS file |
| **D2** | **Exit-78 config-refuse smoke** (carry-over 2) | **sandbox** | Induce fatal config: missing binary (empty install dir) or invalid env in a sandbox launcher install | Launcher exits 78 IMMEDIATELY; **no restart loop** (exactly one attempt); log shows refuse reason | sandbox launcher log + exit-code transcript |
| **D3** | **P7 readiness green→red→green** (carry-over 3) | **demo** | Set `ENSEMBLE_READINESS_FORCE_DEGRADED=1` in demo `.env` + restart; restore requires **restart again** — quote verbatim in the runbook: *"P7 drill on deployed daemon requires restart to restore (env knob read per refresh tick; `readiness.py:50-67`)"* | 200/`reasons:[]` → 503/forced-reason → 200/`reasons:[]` with timestamps; `/livez` stays 200 throughout (independence); **restore documented as restart-required, not instant** (RESULTS §1 P7 row precedent: 15:36:31→15:36:49→15:37:27) | drill record with payload + timestamps; demo `.env` restoration verified (no knob left behind — RESULTS §2 row (h) precedent) |
| **D4** | **Promote → gate-pass → commit** | sandbox, then demo | Normal promote of a known-good release | Journal `committed`; gates `/livez`≤60s + `/readyz`≤120s green within budgets (`deploy.sh` phase 5); 300s soak clean; `current` → new version; version verify | journal entry + RESULTS file |
| **D5** | **Promote → gate-fail → auto-rollback** | sandbox, then demo | `ENSEMBLE_READINESS_FORCE_DEGRADED=1` on the TARGET env pre-restart — one-way fail-safe, cannot false-green (`readiness.py:48-67`) | Flip-back within 10-min window; journal `rolled_back`; cooldown stamped; counter +1; `/readyz` of previous version green post-flip | journal + launcher log + RESULTS file |
| **D6** | **Rollback-cap halt-for-human** | sandbox | 3 consecutive gate-fail rollbacks (D5 induction) | 4th attempt → **refused**, journal enters `halt_for_human`, alert emitted, counters visible; recovery = explicit human ack (documented path) | journal sequence (3 rollbacks + halt) + alert evidence |
| **D7** | **Ari-driven restart** (P2.2) | sandbox → demo | Ari calls `system_restart` on the env it runs in | Daemon restarts (SIGTERM path, never raw kill); ari's tool result sequencing verified; **in-flight child report delivered within the expected recovery window — account the ~10-min ReportDeliveryRecovery lag OR the resumed-turn path** (periodic-only, 300s interval / 10-min age bound / batch 100 / NO boot sweep — `daemon/services/report_delivery_recovery.py:75-80,136`, verified); MessageQueue is EPHEMERAL (`clear_all(preserve_in_flight=True)` at startup — `daemon/manager.py:596,607`, verified) | tool transcript + journal/state + recovery-window evidence in RESULTS file |
| **D8** | **Concurrent-attempt refusal** (P2.2) | sandbox | Trigger second `system_upgrade` while a txn is in-flight | Structured lock refusal naming owner txn; no double-flip; stale-lock (heartbeat >300s, D-FA5.1 mkdir lock `rollback.lock.d`) break verified | journal refusal entry + transcript |

**Restart-semantics accounting (design constraint on D7 and every restart drill):** tasks stay PROCESSING on crash (not FAILED); `StaleTaskRecovery.recover_on_startup` sweeps stale RUNNING >15min + orphaned CANCELLED + watchover markers (`daemon/services/stale_task_recovery.py:637-795`, verified); LangGraph freezes at node boundary, resume via `is_retry`; an in-flight tool call at stop is LOST. Drills must therefore never assert instant delivery of anything queued pre-stop — they assert delivery **within the recovery window** (worst case: next 300s recovery tick + 10-min age bound). The known Task↔JobItem reconciliation gap is pre-existing and OUT of P2 scope — drills that trip it document, not fix.

---

## 4. N-Clean-Demo-Cycles Gate Measurement

### 4.1 Clean cycle — OBJECTIVE definition

A **clean cycle** = all of the following, on demo (7979, `ensemble_demo`) — **this §4.1 is the SINGLE canonical definition** (approver-ruled 2026-08-22; `phase3-plan.md` D1's table is the per-criterion evidence mapping subordinate to this, and `promotion-ladder.md` S3 + ADR-021 bind HERE):

1. One full **ari-driven upgrade cycle**: `system_upgrade` → promote → gates pass (`/livez` ≤60s, `/readyz` ≤120s) → **300s soak** → version verify (reported version == manifest `binary_version`) → **no rollback** → journal `committed`.
2. One **restart cycle** clean: ari-driven or drill restart → respawn → gates green → no readiness degradation attributable to the restart.
3. **No readiness degradation outside drills**: zero unplanned `/readyz` 503 episodes during the cycle window (log-scan assertion).
4. **No unintended work loss** *(folded from phase3 D1 c5, 2026-08-22)*: any in-flight job/turn at stop resumed and completed post-restart (checkpoint-resume evidence: job id list before stop ↔ terminal states after; child reports delivered within the recovery window).
5. **Zero live contact** *(folded from phase3 D1 c6, 2026-08-22)*: live pid checkpoint byte-identical at cycle start/end (Phase-1 §5 precedent) — constraint-compliance artifact per cycle.

Failure of any clause disqualifies the cycle (record it as a failed cycle with cause — failed cycles do NOT reset to zero automatically; see ADR-021 in `decisions.md` for the reset-on-fix question).

### 4.2 N — recommendation

**N = 3** (⚠ flagged for user decision — ADR-021 in `decisions.md`). Rationale: 3 covers day-boundary variance of the periodic recovery paths (each cycle exercises ≥2 recovery ticks), matches the rollback-cap 3/24h symmetry, and keeps wall-clock cost ≈ 3 × (gate 3min + soak 5min + observation) per release. Default if user silent: 3.

### 4.3 Where recorded + staleness rule

- **Per cycle:** `releases/state.json` journal entries (machine-checkable: 3 committed txns, 0 rollbacks, version monotonic per §4.1) + a verification file per cycle under `.agents/tester/RESULTS/` (naming: `2026-MM-DD-selfrestart-phase2-clean-cycle-{n}-{env}.md`, following the `2026-08-22-*` convention).
- **Cumulative:** a ledger section in the P2.3 drill runbook (runbook created by P2.3 — does not exist yet): `cycle # / date / version / journal txn id / verdict / evidence link`.
- **Staleness rule:** cycles expire if the binary/manifest changes mid-ladder — **a new release resets the count to 0** (ledger unit test in §1 P2.3 asserts this). The N gate must be satisfied by cycles all targeting the SAME release version.

---

## 5. Environment Discipline (how nothing ever touches live)

1. **Target-env parameterization:** every pipeline action takes an explicit target resolved from install-dir path + port + DB name, and **asserts all three** before acting. No action ever derives its target from ambient CWD or bare `localhost`.
2. **The one-digit-typo hazard (7979 demo vs 9797 live):** any script or drill that could reach 9797 by a single-character typo MUST assert the install-dir path string (`~/agents-ensemble-demo` exact) before any HTTP or process action — a mismatch aborts. Recommendation: pipeline scripts contain **no literal `9797` anywhere**; live targeting exists only behind the USER-GATED flow (`promotion-ladder.md` §USER-GATED table).
3. **Sandbox discipline:** sandbox = own install dir (e.g. `/tmp/ens-sandbox-$$`), own port (8377 precedent), throwaway PG DB created+dropped per run, isolated `data/` dir. `ENSEMBLE_DEPLOY_LIVE` is never set by any test/drill path; scripts that read it treat unset = refuse live (`deploy.sh` exit-78 semantics, verified).
4. **Demo mutations are drill-scoped only:** demo changes happen exclusively as steps of a named drill with a restore step; every drill record verifies demo `.env` restoration (RESULTS §2 row (h) precedent).
5. **Live pids untouched:** drills assert (read-only `ps`/lsof) that live listener pids are identical before/after — the P4/P5 DevOps precedent (live pids quoted at ≥4 checkpoints, RESULTS §2 row (f)).
6. **USER-GATED marker:** any step whose only correct execution environment is live is written as a runbook the USER executes or explicitly approves — never executed by this initiative's agents (full table in `promotion-ladder.md`).

---

## 6. Pack / Harness Conventions

- **All new tests join `test/packs/`** — dual-layer 5-min timeout, never bare `pytest` (`ensure.md:4-6`). Suggested pack names: `release_journal_unit_test`, `upgrade_gate_unit_test`, `upgrade_tools_registry_unit_test`, `drill_ledger_unit_test`.
- **No `-x`** for suite runs; `--tb=short -q` and review all failures (`ensure.md:8`).
- **Quarantine-aware:** pre-existing failures live in `.agents/tester/QUARANTINE.md` (5 known TestAccessMemoryArchive failures — pre-existing, quarantined; do not let them red the gate).
- **Venv gotcha:** `uv sync --extra dev` before gates — bare `uv sync` strips the dev extra → pytest-timeout drift (project critical note; RESULTS §1).
- **Launcher changes** → `launcher_supervisor_unit_test` pack (74/74 baseline, RESULTS §3) must stay green; journal-sweep implementation extends this pack per the ADR-012 contract embedded at `launcher.sh:151-174` (verified).
- **Frozen-binary drift lock:** any `daemon/tools/` change re-runs `test/packs/frozen_tool_name_discovery_unit_test.sh`; regen one-liner runs **in source mode only**.

---

## 7. Assumptions & Open Seams

- ⟪SEAM: exact `system_restart` result-delivery contract (pre-write vs resume-path) awaits architect enrichment via W2 `tool-api-design.md`⟫
- ⟪SEAM: P2.2 deferred-restart mechanism (post-graph callback vs external timer) fixes the e2e-gate trigger; W1 `phase2-plan.md` owns the choice⟫
- ⟪SEAM: drill-runbook file format + location owned by P2.3 implementation; this strategy defines its required content (§3, §4.3)⟫
- Assumption: demo daemon is running the Phase 1 launcher (live-validated @ `653e8e71`) before any Phase 2 drill — verified as a pre-drill checklist item, not assumed.
