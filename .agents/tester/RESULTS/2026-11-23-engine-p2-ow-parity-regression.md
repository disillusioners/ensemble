# Engine Phase-2 OrphanWatcher — Parity + Regression Tester Gate (2026-11-23)

Lane: `feature/engine-pilot-orphan-watcher` @ `30266ed20c6132ea7ee3c7f7f393ec14179ea59c` (branch, tree clean, 8 commits over base) · TRUE BASE `7243143d08b623786129c4d03ce019d5a3ca2763` (ancestor-verified rc=0) · worktree `/home/nea/ensemble-src-wt-engine-pilot-ow` (fenced dev).
Gate tier: **Tier-2** (mechanical trigger: `daemon/api.py` core-seam touch). Recipes loaded by deterministic path: `regression-ab-partition-run`, `failed-digest-equality-gate` v1.0.3, `ab-base-leg-provisioning-artifacts`.
Measured change set `7243143d0..HEAD`: **15 files** (3 planning docs, PACKS.md, probe transcript, .gitignore, `daemon/api.py`, `daemon/services/engine/boot.py`, 2 scripts, 1 pack script, 4 test files). Daemon delta = exactly {`daemon/api.py`, `daemon/services/engine/boot.py`} ⇒ no daemon-side widening beyond caller scope.
Worker instances: preflight `bc70211b` · probe `dd81b8d1` · bootslo `cbf8cacf` · pack-parity `a81b6ac6` · family `1de858b5` · unit59 `c0ad7538` · dormant `9d7d82ad` · diffaudit `c2a31af2` · regression `336026c6` · attribution `eb1b7847`. Zero re-dispatches; all reported with tool evidence. Scratch: `/tmp/engine_p2_gate/` (all logs cited below live there).

---

## T1a — RO-guard probe REPRODUCED (re-run, not just read) — MEASURED
Command: byte-faithful probe (cmp-verified vs transcript `RESULTS/2026-11-23-engine-pilot-ro-guard-probe.txt` lines 22–109) run from the lane worktree with its `.venv`, per-variable scrub + `ENSEMBLE_SELF_ENV=dev`, `timeout 120`. Log: `/tmp/engine_p2_gate/ro_probe_rerun.txt`.
- BEFORE leg (pre-fix connect-event SET): `WRITE ALLOWED  <-- NOT READ-ONLY (guard INERT)` — defect reproduced as evidence.
- AFTER leg (current `_ro_engine`): `role assertion -> {... 'role_asserted': True}`; `WRITE REFUSED — InternalError: (psycopg.errors.ReadOnlySqlTransaction) cannot execute CREATE TABLE in a read-only transaction`; `SELECT still works -> engine_runs rows = 298`.
- F2 leg (prod-shaped DSN + write-capable role, FAKE conn — no network): `REFUSED LOUDLY: REFUSING to run: the target database 'ensemble_prod' is PROD-SHAPED but the connected role 'ensemble_prod_writer' is not read-only-shaped ...`
- Deltas vs transcript: `probe_utc` and `engine_runs` 283→298 (organic soak growth; probe writes go to a session TEMP table only). All decision lines byte-identical.
**RESULT: PASS** (1.4 s). Target: `ensemble_dev` @127.0.0.1 — never prod.

## T1b — Boot-SLO (ONE run) — MEASURED
Command: `timeout 300 bash scripts/measure/engine_pilot_boot_slo.sh --source /home/nea/ensemble-src-wt-engine-pilot-ow` (script pins `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` + asserts it; `DATA_DIR=./data_dev` worktree-local). Log: `/tmp/engine_p2_gate/boot_slo.txt`.
- **(1) launch→readyz UPPER BOUND: 26.445 s** (`launch_to_readyz_seconds`, harness stopwatch; livez+readyz GREEN at +26445 ms).
- **(2) daemon-reported boot_epoch→readyz: 9.000 s** — Emperor SLO ≤ 120 s: **WITHIN** (`verdict=PASS`, `slo_basis=daemon-reported`; boot_epoch 2026-11-23T20:14:23.484318Z).
- Dormant posture on the booted daemon (log line): `engine boot posture: enabled=True shadow=True registrations=12 observer specs (execute_fn withheld); use_engine_flags=all unset (0/12); executable_specs=0`.
- Port 19100 (house 19100–19199); teardown clean (no listener, no `-m daemon` process, TMP_ROOT trap removed). Live ports 9797/9798 untouched.
**RESULT: PASS** (28 s wall).

## T2 — Parity surface — MEASURED (all green)
| Surface | Invocation (verbatim) | Result |
|---|---|---|
| Runtime-parity pack (NEW, registered) | `cd $WT && timeout 300 bash test/packs/engine_orphan_pilot_runtime_parity_test.sh` | **PASS 32/32** — `32 passed, 1 warning in 2.12s` (10 runtime-parity + 15 truth-table + 7 spec-truthfulness) |
| Pilot family (direct pytest) | fenced `pytest tests/unit/services/test_orphan_watcher_truth_table.py tests/unit/services/test_orphan_watcher_spec_truthfulness.py -q` | **PASS 22/22** — `22 passed, 1 warning in 0.53s` (15 + 7; slicing proof per file 15/7) — brief's "expect 7" = the spec-truthfulness file alone |
| Parity unit file | fenced `pytest tests/unit/services/test_orphan_watcher_engine_parity.py -q` | **PASS 23/23** — `23 passed, 20 warnings in 29.61s` (file's true count; slicing 23) |
| Parity unit PACK (pre-existing, registered 2026-11-22) | `cd $WT && timeout 300 bash test/packs/engine_orphan_pilot_parity_test.sh` | **PASS 59/59** — `59 passed, 20 warnings in 34.55s` (23 parity + 18 dualpath + 13 wiring + 3 dormant + 2 diff-audit — reconciles the 23-vs-59 scope question) |
| Dormant-posture pack (Leg D, release-blocking) | `cd $WT && timeout 300 bash test/packs/engine_dormant_posture_test.sh` | **PASS 3/3** — `3 passed, 1 warning in 4.77s` (in-process dormant / posture line / all flags unset) |
| Diff-audit pack (Leg E, release-blocking) | `cd $WT && timeout 300 bash test/packs/engine_diff_audit_test.sh` | **PASS 2/2** — `2 passed, 1 warning in 0.45s`; **ownership map MEASURED 69 rows** (AST literal `EXPECTED_DAEMON_PATHS` = 69 unique; file comment 48+6+14+1=69); BASE_SHA `8894f86a`; committed `daemon/` diff at tip = 32 paths ⊆ 69, **0 offenders** |

## T3 — Tier-2 A/B regression, FAIL-list diff vs TRUE BASE — MEASURED
Partition (14 explicit files; scratch-marker pre-check 0 hits): the 4 orphan/engine-pilot test files + `tests/unit/services/engine/` 7 files + `tests/api/test_engine_routes.py` + `tests/unit/migrations/test_engine_substrate_migration.py` + `tests/unit/services/test_engine_diff_audit.py` (widening: change-set touch).
Provisioning: scratch DETACHED base worktree `/home/nea/ensemble-src-wt-ep2gate-base` @ `7243143d0` (checkout-family name; lane worktree untouched). Runner ONCE: `/tmp/engine_p2_gate/run_leg.sh` (sha256 `cc6c9c4db2362598b90c290e305b06bb37d2966311217a1f33e62a99e8afd779`), canonical scrub + in-child `FENCE BREACH` guard + `ENSEMBLE_SELF_ENV=dev` + `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` + `timeout 240`.
- Fence self-test matrix A/B/C/D: **MATRIX_ALL_PASS** (A in-child poison → BREACH, no CHILD-RAN; B positive control OK; C1/C2 scrub → `[<unset>]`; D drop-only-`-u POSTGRES_DB` → BREACH).
- Import proofs: BASE leg `import daemon` → `/home/nea/ensemble-src-wt-ep2gate-base/daemon/__init__.py`; TIP neutrality (from /tmp) → TIP path.
- Base list: 11 files (3 new family files ABSENT at base — verified via `git cat-file -e`; `test_engine_diff_audit.py` PRESENT at base).
- Slicing proof: **BASE 151 nodes / 11 files; HEAD 183 nodes / 14 files; delta +32 = 10+15+7 exactly; per-file counts identical on all 11 shared files.**
- Legs (BASE first, sequential, `-n` not used): BASE rc=1 — `1 failed, 150 passed, 37 warnings in 45.86s`; HEAD rc=0 — `183 passed, 37 warnings in 45.56s`; ERROR lines 0 both legs. Budget used 104 s of the 90-min advisory.
- Digest (BARE-ID, prefix+suffix stripped, sorted): base sha256 `1a2fff9551673650326e16a9956edfa568de8b09219148ee6687a689860cb021` (1 node) · head sha256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (∅).

### FAIL-list diff table (base vs HEAD)
| Set | Nodes | Verdict |
|---|---|---|
| NET-NEW (head−base) | **∅** | ✅ required for PASS |
| PRE-EXISTING ∩ | ∅ | — |
| FIXED (base−head) | 1 — `tests/unit/services/test_engine_diff_audit.py::TestEngineDiffAudit::test_daemon_diff_is_confined_to_ownership_map` | 3× isolated adjudication: BASE 3/3 FAIL, HEAD 3/3 PASS, digests byte-identical to leg runs ⇒ stable, NOT flaky, NOT a provisioning artifact. Mechanism: HEAD-anchored containment pin (`git diff 8894f86a..HEAD -- daemon/` ⊆ hardcoded map) reddens at base via the FOREIGN pre-existing path `daemon/tests/test_inject_allowed_models.py` (governor Phase-3 `fc3c8ad1b`); the lane's re-anchor row resolves it. Branch-resolved content difference. |
- Teardown: base worktree removed (`worktree list | grep -c ep2gate-base` → 0); global prune deliberately skipped (4 foreign prunable /tmp worktrees untouched); main-checkout status sha identical before/after; TIP HEAD unchanged `30266ed20…`, 0 tracked-dirty lines.

## T4 — Quarantine + pre-existing-red attribution — MEASURED
- HEAD failing files = **∅** (`head.nodeids.txt` = 0 lines) ⇒ the "diff base..HEAD must be EMPTY" acceptance rule is **vacuously satisfied** for the partition. (Documentation check on the base-only node: `test_engine_diff_audit.py` IS lane-touched by design — the re-anchor — and is NOT a HEAD failure.)
- Quarantine overlap: all 14 partition paths grepped (fixed-string) against `.agents/tester/QUARANTINE.md` (whole file, stricter than Active-only) → **0 matching files**. Active rows (upgrade_tools steal-race RESOLVED, nest_report metrics-router stale pin, skill_evolution StaticPool, launcher 8b-c2, agent-restart set) — none reference the partition.
- Metrics-confinement pin `tests/unit/metrics/test_metrics_family_adjudication.py` (run scoped+fenced): **RED at HEAD — `1 failed, 47 passed, 1 warning in 3.23s`**; failing node `TestNoProducerInstrumentation::test_the_daemon_diff_is_confined_to_the_allowed_files`; **live offender list byte-identical to the lane disclosure's 14 paths** (pilot-report.md §10 item 9.1). Attribution: `git diff 7243143d0..HEAD -- <pin file>` = **EMPTY** ⇒ foreign-attributed pre-existing RED (the brief's "KNOWN pre-existing RED" is CORRECT; the preflight's green expectation was refuted by measurement).
- Lane's "14 offenders, zero intersection with this lane's files" claim: **VERIFIED** — group-B-14 ∩ 15-file change set = ∅; live offender list ∩ {`daemon/api.py`, `daemon/services/engine/boot.py`} = ∅; literals: `ALLOWED_DAEMON_DIFF` = 30 paths (contains `daemon/api.py`), `PRE_EXISTING_DAEMON_OFFENDERS` = 70 unique (contains `engine/boot.py`, group C) — both lane daemon paths absorbed, neither reddens.

## T5 — Fence audit — MEASURED
- Worktree `.env`: `POSTGRES_DB=ensemble_dev` · `POSTGRES_HOST=127.0.0.1` (port 5432) · `DAEMON_PORT=8079` · `ENSEMBLE_SELF_ENV=dev` · mode `600` (password masked, never printed). All 5 fence assertions PASS.
- No `ensemble_prod` DSN in any gate command (probe targets dev; F2 uses a FAKE connection; the A/B runner asserts the fence in-child with the 4-case matrix proving both layers). All spawns used per-variable scrubs (no glob-scrub). Boot-SLO booted ONLY from the lane worktree, port 19100, sweep pinned `=0`. Main checkout untouched (status sha identical). Prohibitions honored: no promote/stage/tag/merge/push, no kill-switch flip, no daemon restart, no writes to the live install/prod DB, no JobLockSweep contact, no lane code/test modifications.

## Findings (REPORT-ONLY — validate-only mandate; nothing fixed)
1. 🟠 **PACKS.md registration staleness**: the `engine_diff_audit` row still says "68-path literal" but the measured literal is **69** (the lane's +1 FOREIGN row `daemon/tests/test_inject_allowed_models.py` post-dates the 54→68 count). Registration exists only in the worktree's branched PACKS.md (main-repo copy has none of the engine rows — known frozen-canonical-checkout drift). Owning lane should re-count at merge.
2. 🟠 **Metrics-confinement pin RED (foreign)**: 14 offenders, unchanged by this lane (diff EMPTY). Re-anchor belongs to the metrics-family lane, not this one.
3. 🟢 Base-tree copy of the diff-audit containment gate is red at `7243143d0` (stale literal vs its own pinned range; `origin/latest` has advanced to `48c5f866c`) — resolved at HEAD by this lane's re-anchor; disclosure-only.
4. 🟢 Brief-figure corrections (measured): diff-audit map = **69** (brief correct; registration 68 stale; older POK "48" historical). Parity-pack "expect 59" = the registered PACK, not the single file (file = 23). Preflight prose "13 files" was a miscount — measured change set = **15 files** (list itself consistent).
5. 🟢 Non-blocking warnings across packs: pre-existing pydantic-v1-on-Python-3.14 shim + `asyncio.iscoroutinefunction` deprecation (`daemon/services/engine/dispatch.py:51`) — unrelated to verdicts.

Measured vs inferred: every count/verdict above is MEASURED (command + log cited); the only inferred items are the organic-growth explanation for `engine_runs` 283→298 and the classification of the base-only red as branch-resolved (backed by 3× stable digests + mechanism reading, per the adjudication ladder).

---

## VERDICT: PASS

(Zero net-new failures vs true base; all parity surfaces green; probe + boot-SLO reproduced/measured; quarantine and pre-existing-red attribution verified; fence intact throughout. Findings 1–2 are documentation/foreign-lane items for the lane closer — neither blocks merge-readiness on this gate's evidence.)
