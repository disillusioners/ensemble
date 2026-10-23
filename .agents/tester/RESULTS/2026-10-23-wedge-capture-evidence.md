# Wedge-capture evidence pack — the SC→pack gate table (T40 / AC56)

- **Date**: 2026-10-23
- **Deliverable**: phase3-plan.md T40 — the gate table (SC-1..SC-15 → the pack that
  proves each), the raw evidence paths, the environment fences, and the explicit
  OPEN list. **A7's measurement** is recorded in §2.
- **Author**: the implementer lane (this file is OUR deliverable at a
  tester-visible path; it is NOT the independent verification — that lives in
  `2026-10-23-wedge-capture-independent-verification.md` on the tester's own
  branch, referenced read-only below).
- **Target**: worktree `/home/nea/ensemble-src-wt-wedge-capture`, branch
  `feature/wedge-capture-watchdog`, HEAD = the commit carrying this file (the
  final harness batch: Lane B env translation, alive-sampled cgroup, skip-text
  correction, SC-2 leg-ii envelope alignment, migration record fix).
- **Fences on every number below**: `scripts/run_tests_scrubbed.sh` (per-variable
  `POSTGRES_*`/libpq scrub, echo-verified zero survivors); Lane A gated by
  `ENSEMBLE_WEDGE_SMOKE=1`; Lane B additionally by `ENSEMBLE_WEDGE_SMOKE_UNIT=1`
  + `PG_TEST_*` (translated to `POSTGRES_*` for the child by
  `tests/support/wedge_smoke.py::scratch_env`); no live contact (no 9797/7979,
  no `ensemble_prod`, no promote/systemctl).

---

## 1. The gate table — SC-1..SC-15 → the proving pack

Pack abbreviations: **SMOKE** = `tests/integration/test_wedge_capture_boot_smoke.py`
(Lane A `ENSEMBLE_WEDGE_SMOKE=1`; Lane B + `ENSEMBLE_WEDGE_SMOKE_UNIT=1` +
`PG_TEST_*`); unit packs by their class groups in `tests/unit/`.

| SC | Claim (plan-overview §6) | Proving pack | Status |
|----|--------------------------|--------------|--------|
| SC-1 | A CPU-burning harness produces ≥1 capture dir within 10s with the full file set (`manifest.json`, `stacks.txt`, `threads.csv`, `lag.json`, `metrics.json`) | `SMOKE::TestLegI_CpuBurn` (e2e trigger→capture, ≤10s window) + `tests/unit/test_wedge_capture_writer.py::TestFileSet` (the exact file set) + `TestCpuThreshold` (triggers pack) | **PROVEN** — Lane A green 2026-10-23 (6P incl. leg-i). NOTE: SC-1's literal tiny-threshold env (`CPU_PCT=1`/`LAG_S=0.5`) is superseded by the per-leg knob discipline (D43/RK-22 — a global tiny set makes the cooldown proof indeterminate); the proof is the leg-i SHAPE (CPU fires, LAG silent) at per-leg knobs. |
| SC-2 | With `LAG_S=2.0` **and** `LAG_SUSTAIN_S=2` and a loop block **≥5s (6s in the leg)**, the lag trigger fires AND the capture names `sustained_loop_lag` — H2 detectable without any CPU peg | `SMOKE::TestLegII_LoopSleep` — **the aligned Lane A leg-ii e2e**: env `ENSEMBLE_WEDGE_CAPTURE_LAG_S=2.0` + `_LAG_SUSTAIN_S=2` (`smoke.LEG_LAG`), block `smoke.SC2_LOOP_BLOCK_S=6.0`, hold `SC2_LEG_HOLD_S=7.0`; negatives CPU+CONCENTRATION silent (H1/H2 discriminator, multi-core only, nproc recorded) + `tests/unit/test_wedge_capture_triggers.py::TestLagThreshold/TestSustainRule` (semantics) + `sampler::TestLagProbeRealLoop` | **PROVEN at the prescribed envelope** — 2026-10-23 standalone evidence run (scrubbed): `capture_count=1`, `fired=[sustained_loop_lag]`, manifest `observed={lag_s:4.004, sustained_s:2.002, threshold_s:2.0, sustain_s:2.0}`, `watchdog_alive=True, missed_samples=0`, `nproc=8`, CPU+CONC silent. The 6s block spans past the fire moment (B1 arithmetic: over-threshold run starts ~2.0s in, fires when the 2s sustain is met at lag≈4.0). The pre-2026-10-23 divergence (`SUSTAIN=0`, 3.0s block) is CLOSED. |
| SC-3 | `_THREAD_CONCENTRATION_PCT=50`, single-threaded burn fires concentration with ≥85% attributed to exactly one `native_id` (name+comm present); A15 negative pair + the live-wedge third leg | `SMOKE::TestLegIII_LoopSpin` (LAG+CONC fire, attributed to `loop_thread_native_id`, injected frame VISIBLE in `stacks.txt` — F-13) + leg-ii's silent negatives (the discriminator's negative direction) + `tests/unit/test_wedge_capture_sampler.py::TestThreadAttribution` (incl. MR-6 observer self-exclusion) + `triggers::TestConcentrationThreshold` | **PROVEN** — Lane A green 2026-10-23 (leg-iii: both reasons fire, loop thread attributed, spin frame in stacks; leg-ii negatives silent at nproc=8). |
| SC-4 | A capture during full saver-pool checkout still writes all files and NEVER blocks on the pool | `tests/unit/test_wedge_capture_writer.py::TestPoolIndependence` (a spy pool that raises on ANY access is never called) + `TestPoolVocabulary` (T39 canonical counters) | **PROVEN** — pack green (42/42, corpus 2026-10-23 189/189). |
| SC-5 | The metadata row lands in `wedge_captures` populated (`trigger_reason`, `capture_dir`, `created_at`, `host`, `release_version`, `summary`, `metrics_snapshot`); LIST returns it; FETCH returns the full payload | `tests/unit/test_wedge_capture_writer.py::TestRowWriteLegs` + `tests/unit/test_wedge_capture_readback.py::TestList/TestFetch` (+ live-PG re-proof G4 in the independent verification: jsonb adaptation + ON CONFLICT dedup + sidecar reconcile 4/4 on real PG 16.15) | **PROVEN** (unit) + **live-PG corroborated** (tester G4). |
| SC-6 | A sustained wedge yields EXACTLY one capture per cooldown window | `SMOKE::TestCooldown` (one capture over a 5s sustained burn) + `triggers::TestCooldown` (per-reason fingerprint) | **PROVEN** — Lane A green 2026-10-23. |
| SC-7 | Retention cap (count=3 over a 5-capture run leaves exactly 3 dirs+rows, oldest removed); A3 directory-authoritative WITH the row write forced to fail | `tests/unit/test_wedge_capture_writer.py::TestPrune` | **PROVEN** — pack green. |
| SC-8 | Effective AutoContinue mode in the boot record and in the read-back status as `auto_continue_mode` (on/off/unknown only); OFF branch records `off` + extended `[BOOT_CONTINUE]` line | `tests/unit/test_wedge_capture_boot_record.py` (TestRecordIsWrittenOnEveryBoot, TestOffBranchRuntime, TestIntentRecordSurvivesARaisingPass) + `tests/unit/test_wedge_capture_readback.py` (status carrier `auto_continue_mode` on/off assertions) | **PROVEN** — packs green (29/29 + 26/26). |
| SC-9 | The `[BOOT_CONTINUE]` extension is additive, not duplicative (A18 hybrid: static site-count unchanged vs base + runtime positive-set from the 5 named sites) | `tests/unit/test_wedge_capture_boot_record.py::TestA18HybridStatic` (+ the 5-sites runtime pins in the same pack) | **PROVEN** — pack green; the "every emitted record" form was correctly rejected as unsatisfiable (pack docstrings record the rejected form). |
| SC-10 | `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART=0` appears NOWHERE in the diff / env / units / fixtures | `tests/unit/test_wedge_capture_no_autocontinue_disable.py` (TestNoAddedDisable diff-vs-base + mutation leg proving detection) | **PROVEN** — pack green (10/10); kill-switch-absence grep on a booted tree corroborated by the tester's manual boot (zero persisted disables). |
| SC-11 | The 13-file pin ceremony in ONE commit; `PRIVILEGED_TOOL_CATEGORIES` exactly 16; member appended after `board-link`; 4 non-editing gates pass unmodified | `tests/unit/test_wedge_capture_registration.py` (TestPrivilegedCategory 16-member set-equality, TestAllowLists {ari,leader,maintenancer}, TestStaticRegistration, TestAuthorizationHelper, TestReadOnlyPosture, TestAttributePin) | **PROVEN** — pack green (17/17); the ceremony landed as one commit (verified by the independent gate). |
| SC-12 | The seal tripwire passes unchanged; the full T1–T41 change list ∩ the 35-row `SEALED_SHA_BASELINES` is EMPTY (`daemon/constants.py` untouched is one row of that proof) | `tests/test_chart_image_delivery_e2e.py::test_phase_d_does_not_modify_sealed_artifacts` | **PROVEN** — green on this branch (independent gate G6 slice 1: PASS 1/1, HEAD vs BASE — the branch never touched the sealed artifacts). |
| SC-13 | Prompt-artifact baseline check 161/161 clean after the `meta.json` allow-list edits | `python3 scripts/protection/generate_prompt_artifact_baseline.py --check` | **PROVEN** — re-run by this lane 2026-10-23: `PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256`. |
| SC-14 | The boot smoke runs in a `systemd-run` transient unit with a cgroup assertion (NOT under live `ensemble-main.service`) and a SIGTERM-only teardown; live daemon PID/epoch/`livez` unchanged | `SMOKE::TestDaemonBootLane::test_the_cgroup_and_live_fence_hold` (cgroup sampled WHILE ALIVE — the F2 fix) + `test_the_unit_argv_never_targets_the_live_unit` + `TestBootWiring::test_an_enabled_boot_leaves_a_live_watchdog_on_app_state` (boot-alive, F1-fixed env translation) | **PROVEN (product) / EXECUTION OWNED BY THE G3 RE-RUN** — the harness is now correct (env translated, cgroup alive-sampled); the product behavior itself was proven by the tester's MANUAL correctly-wired boot (watchdog alive on `app.state`, 202→dir+row, SIGTERM teardown, live fence unchanged). The committed pack's opt-in execution is the tester lane's G3 step. |
| SC-15 | Staged release: migration delta EXACTLY +1 (108→109), zero base-tree references, `rollback_safe=true source=explicit` with the STANDING RULE reasoning, explicit `ENSEMBLE_ROLLBACK_SAFE=1` | stage-time gate (S-5) — `scripts/upgrade/stage.sh`, giter's step AFTER this gate | **OPEN** (by design) — staging is not this lane's step. The +1 file is `daemon/migrations/versions/20261022_000002_create_wedge_captures.sql` (prefix unchanged, additive `IF NOT EXISTS` UP, comments-only DOWN — record-fixed 2026-10-23). |

**Corpus anchor**: 9 unit packs = 189/189 green (independent gate G1, 2026-10-23);
boot-smoke collection CLEAN 10/10 nodes.

---

## 2. A7's measurement — the stall-ledger append cost (AC24)

Method (worktree venv, `CHECKPOINT_PERF_LOGS=0`, `logging.disable(CRITICAL)`,
best-of-3 over N=100,000 calls, loop no-op reference 0.10 µs/op):

| Measurement | µs/op | Verdict |
|---|---|---|
| **A7 seam fast path** (`record_slow_saver_op`, `duration_ms` < 5000 cut — ONE int comparison) | **0.93** | **< ~1 µs/op — PIN HOLDS** (net of loop ≈0.83) |
| A7 seam append branch (≥ 5000, the locked deque append) | 0.97 | also < 1 µs/op |
| PRE-EXISTING prometheus `saver_op_latency_seconds.observe` (runs REGARDLESS of the log gate — the W3 metric surface, NOT part of the A7 seam) | 3.02 | out of A7 scope — disclosed |
| Whole `log_saver_op` (logging disabled) | 4.52 | decomposition: 0.93 seam + 3.02 metric + 0.56 call overhead |

**Reading**: AC24's pin ("< ~1 µs/op, the stall-ledger append cost") HOLDS on the
seam A7/T16 added (0.93 fast path / 0.97 append). The whole-call figure (4.52) is
dominated by the pre-existing histogram observe that the log gate deliberately
does NOT suppress ("Metric records REGARDLESS of the log gate — operator's SLO
surface is independent of log volume", checkpoint_perf.py). The append never
holds the lock across the timed op (call order: `time_saver_op` awaits the op,
then `log_saver_op` in `finally`) — pinned behaviorally in the writer pack.

---

## 3. Annex §8 impl-time commands — folded as pinned verification steps (T40)

Owner P1/P3 per the annex §8 runbook. These are LIVE-INCIDENT-TIME runbook
steps (they run against an AFFECTED daemon's log store); their SEAMS are
pinned now, their execution belongs to the first real wedge:

| Annex §8 step | The pinned seam on this branch | Status |
|---|---|---|
| G1–G5 `[BOOT_CONTINUE]` greps (incl. OFF pair G3+G4, orphan-sweep G5) | `boot_record` pack: the 5 named logger call sites, mode-carrier positive set, OFF-branch extended line | **SEAM PINNED** — execution is live-incident-time |
| G6/G7 `[CheckpointPerf] op=… latency_ms=…` greps | `checkpoint_perf.py::log_saver_op` (§2 above) + writer pack `checkpoint_perf_tail.json` surface (synthetic 6s `aget` appears with `latency_ms=6000`, AC24) | **SEAM PINNED + BEHAVIORALLY PROVEN** (pack) |
| G8 `[/Messages]` slow-path grep | `daemon/persistence.py:465` aget path (unchanged by this lane) | **SEAM EXISTS (pre-existing)** |
| D1 task-stamp SQL (`auto_continued_at = :epoch`, cleared-on-success gotcha) | readback `auto_continue_mode` carrier + boot_record `ContinueResult` counters | **SEAM PINNED** — SQL is the runbook's own text |

---

## 4. Raw evidence paths

- Independent verification (READ-ONLY, tester's branch, NOT merged):
  `git show 2f899ec2d:.agents/tester/RESULTS/2026-10-23-wedge-capture-independent-verification.md`
  — G1 189/189; G2 Lane A matrix; G3 product-PASS-via-manual-boot + the two
  harness defects (both FIXED by this batch); G4 live-PG 4/4; G6 NET-NEW=∅.
- Lane A re-run after the SC-2 alignment (this lane, scrubbed runner):
  `ENSEMBLE_WEDGE_SMOKE=1 … -k "TestLegI_CpuBurn or TestLegII_LoopSleep or TestLegIII_LoopSpin or TestCooldown or TestKillSwitchLeg or test_the_leg_matrix"` → **6 passed / 4 deselected (the Lane B set) in 33.24s**.
- SC-2 standalone evidence run (§1 SC-2 row): manifest
  `observed={lag_s:4.004, sustained_s:2.002, threshold_s:2.0, sustain_s:2.0}`,
  capture `20261023T045721Z-…-sustained_loop_lag`, loud no-PG degradation as
  designed (`no_postgres_dsn`).
- Migration record fix proof: the runner's own `MigrationFile.parse` on the
  fixed `.sql` → UP = 4 additive `IF NOT EXISTS` statements; DOWN = **0
  executable statements** (comments-only body, raw section non-empty so
  `rollback_migration` still finds a DOWN section and degrades to a ledger
  clear).

## 5. Environment fences

`scripts/run_tests_scrubbed.sh` (per-variable scrub, echo-verified) on every
invocation; `PG_TEST_*` is the ONLY PG credential surface in tests (translated
to `POSTGRES_*` at the child seam — never exported into the test process);
scratch PG is provisioned + dropped by the tester lane; Lane A needs no DB;
`nproc` recorded in every leg observation (F-14: single-core hosts SKIP the
negative-bearing legs, never fail).

## 6. OPEN list

| Item | Reason it is OPEN | Owner |
|---|---|---|
| SC-15 stage gates (+1 migration delta, `rollback_safe=true source=explicit`, baseline 161/161 at stage time, `ENSEMBLE_ROLLBACK_SAFE=1` invocation) | Stage-only commission: staging runs AFTER this gate | giter (S-5) |
| Lane B committed-pack execution (boot-alive on `app.state`, alive-sampled cgroup, in-unit legs) | Opt-in (`ENSEMBLE_WEDGE_SMOKE_UNIT=1` + `PG_TEST_*`); the tester's focused G3 re-run owns it | tester (G3) |
| Annex §8 G1–G8/D1 execution against a real affected daemon's logs | Runbook steps by construction (no live wedge yet) | diagnostics lane |
| Registered residuals (NOT SC-blocking, green-classified): `insert()` `.default.arg` latent defect (no production caller), `row_written` dedup token semantics, duplicate `capture_dir` unique index, `injection_window` not persisted, CLI `--wait` flag-explicit pin, D37 `status()`-never-waits behavioral pin | recorded in the independent verification's register; later polish lane | follow-up lane |
| leg-iii duration note: D43 prescribes a 3–5s spin; the harness runs 6.0s with a documented margin rationale (the concentration capture briefly occupies the watchdog; the lag series needs headroom AFTER it) — deliberately NOT changed by the SC-2 alignment batch (leg-ii was the only in-scope leg) | disclosed deviation, own row above | — |
