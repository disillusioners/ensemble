# Independent Verification — wedge-capture instrumentation (pre-staging gate)

- **Date**: 2026-10-23 (03:14–~05:0x UTC)
- **Commissioned by**: leader (stage-only gate; giter stages AFTER this gate)
- **Target**: worktree `/home/nea/ensemble-src-wt-wedge-capture`, branch `feature/wedge-capture-watchdog` @ HEAD `968a0c0b21d066102df8fcc3a6b76b825eca8beb` (22 commits over base `639b7129bc9cf663a35901a4e87dbd5fe697a977`, verified ancestor)
- **Evidence commit**: docs-only, branch `tester/wedge-capture-verification` (this file + PACKS.md wedge registration only; NOT merged; feature branch untouched)
- **Fence compliance**: `/home/nea/ensemble-src` never touched; zero live contact (no 9797/7979, no `ensemble_prod`, no stage.sh/promote/systemctl on ensemble-main); per-variable env scrub on every invocation (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u SSL_CERT_FILE -u SSL_CERT_DIR` — never the `POSTGRES_*` glob); all PG work on throwaway scratch DBs (created + dropped with proof); base legs in `/tmp/wedge-base-verify` (detached clean worktree @ base, own venv, editable-pth verified in-tree).

## Verdict summary

| Gate | Verdict | One line |
|---|---|---|
| G1 wedge corpus (9 packs + collection) | **PASS** | 189/189 across 9 packs; boot_smoke collection CLEAN (10 nodes) |
| G2 Lane A simulated-wedge matrix | **PASS** | legs i/ii/iii + attribution + cooldown + kill-switch + negatives (nproc=8); SC-2 deviation disclosed |
| G3 Lane B opt-in boot smoke | **FAIL (harness) / product PASS via manual boot** | 8P/2F — both failures are harness defects; product evidence (boot-alive, boot record, 202→dir+row, kill-switch) obtained manually |
| G4 live-PG re-proof | **PASS** | Jsonb adaptation, ON CONFLICT dedup via real unique index, sidecar reconcile — 4/4 on real PG 16.15; 6 secondary findings |
| G5 T39/T40 cross-check | **PARTIAL** | annex A2 PASS; T39 predicate 4/4 evaluable (2 form-nuances); T40 evidence deliverable NOT materialized; SC-2 env+duration proven only in composition |
| G6 regression slice (6 items, HEAD vs base) | **PASS** | NET-NEW = ∅ on all 6; 1 BASE-only red RESOLVED by branch (legitimate re-pin); 6 pre-existing base-identical reds disclosed |
| G7 known-residual register | **PASS** | 4/4 hold-as-described, none worse (W2 measured ≤~1.0s vs stated ≤~2s) |

**Overall: PRODUCT SURFACE VERIFIED — PASS-WITH-DISCLOSURES. Lane B harness is broken as committed (its boot-alive pin cannot pass under its own documented prerequisites) — recommend fixing the harness before staging, or staging with explicit disclosure + immediate follow-up. Caller adjudicates.**

---

## G1 — Full wedge corpus (house scrubbed runner)

All packs run one-at-a-time (OOM discipline), each with dual-layer timeout (outer `timeout 300` + pyproject per-test 30s active), per-variable env scrub, worktree venv (pre-existing, no sync needed).

| # | Pack (pytest path) | Tests | Verdict | pytest s |
|---|---|---|---|---|
| 1 | tests/unit/test_wedge_capture_sampler.py | 20 | PASS | 1.15 |
| 2 | tests/unit/test_wedge_capture_readback.py | 26 | PASS | 2.24 |
| 3 | tests/unit/test_wedge_capture_writer.py (carries checkpoint_perf_tail surface) | 42 | PASS | 1.41 |
| 4 | tests/unit/test_wedge_capture_cli.py | 15 | PASS | 4.06 |
| 5 | tests/unit/test_wedge_capture_registration.py | 17 | PASS | 1.72 |
| 6 | tests/unit/test_wedge_capture_boot_record.py | 29 | PASS | 0.51 |
| 7 | tests/unit/test_wedge_capture_triggers.py | 27 | PASS | 0.54 |
| 8 | tests/unit/test_wedge_capture_wiring.py | 3 | PASS | 3.28 |
| 9 | tests/unit/test_wedge_capture_no_autocontinue_disable.py | 10 | PASS | 0.75 |
| — | **TOTAL** | **189** | **9/9 PASS** | max 4.06 |

Command shape (per pack): `timeout 300 env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u SSL_CERT_FILE -u SSL_CERT_DIR .venv/bin/python -m pytest <pack> --tb=short -q`

- boot_smoke collection cleanliness: `ENSEMBLE_WEDGE_SMOKE=1 … --collect-only -q` → **CLEAN, 10 tests collected in 0.25s, zero import errors, zero INTERNALERROR**.
- Registration pack pins: exactly-16 set-equality; allow-set exactly {ari, leader, maintenancer}; default-deny static (fail-closed blank/non-holder) + behavioral (empty-allow synthetic agent resolves ZERO wedge tools).
- boot_record pack pins: exactly 5 `[BOOT_CONTINUE]` mode carriers (by message text, call-block parse); 50-file prune-oldest cap (behavioral; value at source boot_record.py:58); effective-mode read from the running process's ContinueResult carrier (never re-derived); intent record cannot fabricate a mode.
- triggers pack pins: sustain both directions (fires-at-5.0s-sustained-15s; no-fire-before-window; 4.99s/60s no-fire); H2 lag-only discriminator explicit (CPU-idle 1.0% + lag 9.0s → fires LAG only); F-06 ≥5% floor; cadence-independence; per-reason cooldown fingerprint.
- no_autocontinue pack pins: diff-vs-base zero added `VAR=0` tokens (mutation leg proves detection); .env.example/systemd/15-knob-block clean.
- **Coverage notes (disclosed)**: (1) CLI `--wait` absence is pinned behaviorally (single-request/no-poll + docstrings) — no flag-explicit argparse census. (2) D37 `status()`-never-waits is a docstring invariant — no behavioral pin in the corpus. (3) never-wedge self-exclusion IS pinned and executed: sampler `test_mr6_the_observer_is_excluded_from_the_attribution` (tid 222 excluded, per_thread == [111]).

## G2 — Lane A simulated-wedge matrix (no PG)

Command: `timeout 300 env -u … ENSEMBLE_WEDGE_SMOKE=1 PYTEST_TIMEOUT=280 .venv/bin/python -m pytest tests/integration/test_wedge_capture_boot_smoke.py --override-ini="addopts=" --override-ini="timeout=280" --tb=short -q`
**Result: PASS — 6 passed / 0 failed / 4 skipped (skips = the Lane-B systemd+PG surface, verbatim reasons recorded; skip ≠ pass, expected for lane A).** pytest 29.79s.

- leg-i CPU burn → CPU fires only (LAG silent). leg-ii loop sleep → LAG fires, CPU+concentration silent (H2 discriminator; multi-core guard passed, **nproc=8 recorded**). leg-iii loop spin → LAG+CONCENTRATION fire, loop-thread native id in attributed ids, injected `wedge_smoke_injected_spin` frame **visible in stacks.txt** (F-13).
- Cooldown: exactly **one** capture over a 5s sustained burn (one WARNING line corroborates). Kill-switch: `status().enabled=False`, manual enqueue → `watchdog_disabled`, zero capture dirs. Matrix: 3 legs, each with negative pair (D43).
- Artifacts without PG: 7-file set + **`.pending_row.json` full-row sidecar**; `row_written=None`; degradation is loud, not swallowed: `WARNING … metadata row write failed … no_postgres_dsn` (db_capture.py:446-447 → watchdog.py:606-618).
- Teardown: no wedge python procs, no listeners (Lane A starts no persistent process; SIGTERM-only is Lane B's assertion).
- **SC-2 deviation (source-confirmed)**: leg-ii runs `LAG_S=2.0` (plan: 5.0), `LAG_SUSTAIN_S="0"` (plan: 2), 3.0s block (plan: ≥5s/6s); leg-iii `LAG_S=2.0/SUSTAIN=0`, 6.0s spin (wedge_smoke.py:154-155, :175-176; test file :114/:148). Sustain-window semantics are pinned at integration-value defaults in the triggers pack (5.0s/15s), so SC-2 is proven **in composition**, not by any single pack at the prescribed env+duration.
- **Caveat**: `injection_window` is captured (wedge_smoke.py:680) and serialized only in failure messages — never persisted to artifacts, and **no test asserts captures fall inside it** (grep: zero assertion sites). Recommended harness improvement.

## G3 — Lane B opt-in boot smoke (the implementer could not run)

Command (corrected): both `ENSEMBLE_WEDGE_SMOKE=1` **and** `ENSEMBLE_WEDGE_SMOKE_UNIT=1` (see F3), `PG_TEST_*` exported (5 conventional names), scratch PG provisioned (`wedge_gate_1792728188`), PYTEST_TIMEOUT=280, addopts override. **Result: FAIL — 8 passed / 2 failed / 0 skipped** (73.03s).

### Failures — both HARNESS defects, not product defects
- **F1 (structural) `TestBootWiring::test_an_enabled_boot_leaves_a_live_watchdog_on_app_state`**: `pg_test_env_pairs()` (wedge_smoke.py:554-562) returns every `PG_TEST_*` env var and `scratch_env()` (:516-532) passes them **verbatim** into the child env — but **nothing translates `PG_TEST_*` → `POSTGRES_*`** (grep daemon/: zero `PG_TEST` hits; the daemon reads only `POSTGRES_URL`/`POSTGRES_*`, persistence.py:144-155). With no `POSTGRES_*` the child defaults to **SQLite** and dies at the known-impossible migration: `MigrationError: Migration 20260714_000001 failed: (sqlite3.OperationalError) near "EXISTS"`. **The committed boot-alive gate cannot pass under its own documented prerequisites** (its skip reason names only `ENSEMBLE_WEDGE_SMOKE_UNIT=1 and a scratch PG (PG_TEST_*)`).
- **F2 `TestDaemonBootLane::test_the_cgroup_and_live_fence_hold`**: `/proc/<pid>/cgroup` read race — the probed process exits before the read.
- **F3 (UX, disclosed deviation by the gate)**: `smoke_enabled()` gates on `ENSEMBLE_WEDGE_SMOKE` (:73-74) while `unit_lane_enabled()` gates on `ENSEMBLE_WEDGE_SMOKE_UNIT` (:77-78) — "SMOKE_UNIT implies lane A" is **false**; without `ENSEMBLE_WEDGE_SMOKE=1` all 10 tests skip in 0.28s. The skip-reason text is misleading.

### Product-side commissioned evidence — obtained via MANUAL boot (correctly-wired `POSTGRES_*` → same scratch DB, port 18099, fake HOME, SIGTERM teardown)
- **(a) Watchdog boot-alive (api.py ship-blocker class closure)**: `GET /api/wedge-captures/status` → `{"enabled":true,"running":true,"started_at_monotonic":333739.76…,"last_probe_age_s":0.82,"lag_probe_state":"ok","loop_thread_native_id":3436704,"auto_continue_mode":"on"}`; log: `Wedge-capture watchdog started: dir=/tmp/wedge-manual-boot-…/wedge-captures interval=1.0s cpu>=99.0%/30.0s lag>=30.00s/15.0s`. The pack's own assertion body (`assert_watchdog_boot_alive`, :568-580: not-None + running + started_at_monotonic) is correct — only the harness plumbing fails.
- **(b) Boot record + effective mode**: `boot-records/20261023T040813707860Z.json` → `"effective_mode":"on"`, `"mode_semantics":"literal-\"0\"-only disable…"`, `passes.autocontinue {auto_continue_mode:"on", auto_continue_raw_env:"1", candidates:0…}`; log `[BOOT_CONTINUE] orphan-sweep summary: … boot_epoch=2026-10-23T04:08:13.707860 mode=on` + `AutoContinue boot pass: ContinueResult(… auto_continue_mode='on', auto_continue_raw_env='1')`.
- **(c) Manual capture on the booted daemon**: `POST /api/wedge-captures/capture` → **202 Accepted** `{"status":"accepted","capture_id":"20261023T040922Z-ensemble-vm-manual-1ac27930","reason":"manual","enqueued":true,…}` → watchdog: `servicing manual capture request …` → `capture written dir=… reason=manual artifacts=8 duration=0.11s row_written=True`; on disk 8 files (incl. readyz.json + checkpoint_perf_tail.json); scratch DB `wedge_captures` **count=1** (`manual | manual`).
- **(d) Kill-switch-absence grep on the booted tree**: 22 hits, **ZERO persisted disables** (10 .agents prose, 1 tests docstring, 1 stale log line from a prior lane's 02:54 boot, 3 daemon comment/docstrings; no .env, no unit, no config, no code path sets it). Pack 9's diff-vs-base verdict (offenders==[]) is authoritative.
- Teardown: no orphan procs/units, port 18099 freed, graceful SIGTERM (`Graceful shutdown complete`); scratch role+DB `wedge_gate_1792728188` **dropped with proof** (datname/rolname queries empty).
- **New observations**: (i) db_capture SQL assumes a `tasks` relation — on a wedge-only fresh scratch DB the DB block degrades fail-soft (`executing-turns read failed: relation "tasks" does not exist`; capture still completed) — nil impact on the real ensemble DB. (ii) `manifest.row_written=None` **by design** (file-first D12; manifest written before the row step) — authoritative row signals = completion log + DB row + absent sidecar. (iii) injection_window never persisted (see G2 caveat).

## G4 — Live-PG behavioral re-proof (scratch PG only)

Real worktree modules + REAL `.sql` applied via the runner's own UP-extraction (direct `psql -f` would execute the DOWN section — see finding 3). Scratch: `wedge_proof_1792728198_{sql,mc}` + role, **dropped with proof** (pg_database/pg_roles counts 0; /tmp artifacts removed).

| Proof | Verdict | Key evidence |
|---|---|---|
| A — Jsonb adaptation via real `write_capture_row` | **PASS** | `{"status":"ok"}`; readback `pg_typeof=json`, Python `dict`, deep-equal incl. nested list/None/bool; **negative control refused**: bare dict → `ProgrammingError: cannot adapt type 'dict'` |
| A′ — same on production-shaped jsonb (model DDL) | **PASS** | `pg_typeof=jsonb`; ON CONFLICT resolves with two unique indexes present |
| B — ON CONFLICT dedup via real unique index | **PASS** | 2nd write `ok`, `count=1`, **first row wins**; unique index named by raw-INSERT probe `UniqueViolation … idx_wedge_captures_capture_dir` |
| C — `.pending_row.json` reconcile end-to-end | **PASS** | 0→1 row, payload match, sidecar consumed; re-run `count=1` (no duplicate) |
| D — no prod contact | **PASS** | `ensemble_prod` absent from cluster; only scratch DBs in activity census |

Table shape (from .sql): `metrics_snapshot json NULL` is the only JSON column; `capture_id UNIQUE`, unique index `idx_wedge_captures_capture_dir`.

**Findings register (G4-sourced)**:
1. 🟢 **Latent defect, zero live blast radius**: `WedgeCaptureRepository.insert()` raises `AttributeError ('NoneType' object has no attribute 'arg')` on a payload without `created_at` — `payload.setdefault("created_at", …columns["created_at"].default.arg)` runs before the try (Pydantic `default_factory` ≠ Column default). Verified **no production caller** (watchdog/router use reconcile_pending/delete_by_dirs/list_recent/get/get_by_dir; reconcile path safe — model fills created_at).
2. 🟠 **Schema-record drift**: `.sql` declares `metrics_snapshot json`; model `create_all` produces **jsonb**. Invisible in production (create_all wins; runner is SQLite-only/PG no-op), but a DB provisioned from the `.sql` alone loses jsonb operator capability. The .sql is the persistent record — fix for record fidelity.
3. 🟠 **`.sql` manual-apply footgun**: `psql -f` executes the DOWN section (4 DROPs). Must apply via the runner's UP-extraction; document this.
4. 🟢 **Reconcile dedup is an existence check (SELECT-then-insert), not ON CONFLICT** — invariant holds (re-run no-dup; per-sidecar try/except makes concurrent double-insert safe via the unique index), but the mechanism differs from the ON CONFLICT wording that lives only in `write_capture_row`.
5. 🟢 **`write_capture_row` returns `{"status":"ok"}` on a dedup no-op** — caller sets `row_written=True` for a row that was not inserted (token means "statement executed", not "row inserted"). Sidecar lifecycle unaffected (row exists either way).
6. 🟢 **Duplicate unique index on capture_dir in the model shape** (`idx_wedge_captures_capture_dir` + `unique=True` column constraint) — redundant, harmless (ON CONFLICT inference proven).
7. ℹ️ Fence note: `ensemble_dev` is CONNECT-granted to PUBLIC on this cluster (a first probe iteration opened a connect-only, zero-SQL session before being replaced by ACL inspection); prod not present on this cluster. Verification lanes must treat env/DSN discipline as the fence.
8. ℹ️ `create_engine("postgresql://…")` fails in this venv (psycopg2 missing) — use `postgresql+psycopg://`.

## G5 — T39/T40 sibling-matrix cross-check

- **Annex integrity / A2 contract: PASS** — `recurrence-evidence-matrix.md` sha `0c191ac8c1798deb6df02f87ed5651e6840c4ff1581b98c09f09631e9193fd62`, `architecture-recommendation.md` sha `6c17a3138fa0a759f5a789b328d8541d1a33fc64396ed2f31be5c585468c3fcf6`; planning dir porcelain-clean (unedited).
- **T39 four-part H2 predicate: PRESENT (evaluable) 4/4** in `tests/unit/test_wedge_capture_writer.py`: (i) `pool_checked_out == pool_size_current` direct (:586); (ii) `== pool_max` via three conjoined literal asserts under one saturated fixture (:785-787 — no direct pairwise); (iii) `pool_waiting_clients` exact-value asserts `==9`/`==4` (:587/:788 — no literal `>=1` comparator; exact-value is stronger on the fixture); (iv) `pool_checkout_timeouts is None` + reason `"not_exposed_by_psycopg_pool_3.3.1"` (:790-795) + collector :299-303 + readback :311 + version tripwire :823-837.
- **T40 SC→pack table: NOT MATERIALIZED** — the plan's T40 deliverable (`.agents/tester/RESULTS/<date>-wedge-capture-evidence.md` containing the SC-1..15 → pack gate table) does not exist on the branch. Implicit mapping exists only across plan rows (SC-2 → T36 boot smoke leg-ii).
- **SC-2 verdict**: no single pack proves SC-2's literal env+duration; leg-ii deviates (G2 table above); sustain semantics proven in composition (triggers pack + leg-ii e2e firing). Per commission guidance, plan rows AC26/AC49/D47 were NOT reused as gates.
- Plan-row self-unsatisfiability warning honored: the boot_record pack's own docstrings record the rejected "every emitted record carries mode=" form and pin the five named sites instead.

## G6 — Regression slice (HEAD 968a0c0b2 vs BASE 639b7129b, identical invocation both legs, per-var scrub, digest method)

| # | Slice | HEAD | BASE | NET-NEW |
|---|---|---|---|---|
| 1 | sealed_artifacts_tripwire (class-qualified node, addopts override, -p no:randomly) | PASS 1/1 | FAIL 0/1 (stable 2/2) | **∅** (RESOLVED = 1: branch heals a pre-existing stale-baseline red; re-pin verified LEGITIMATE — artifacts byte-identical both trees, branch never touched them, baselines moved onto pre-existing content, commits 9689b8508/950352447) |
| 2 | api_unit_test (pack script; internal timeout 120) | FAIL 269P/6F/8S | FAIL 269P/6F/8S | **∅** — FAILED sets byte-identical (4× list_instances mock-seam drift, 2× Mock-not-awaited messages.py:326; pre-existing base debt). PACKS row baseline 213P/8S is STALE (corpus now 269P both trees) |
| 3 | live_views_p3_seven_pin_set (7 files, addopts override + `-m "integration or not integration"` + `-p no:randomly`) | PASS 136/136 | PASS 136/136 | **∅** — node sets identical; **0 deselected proven** (counterfactual: 127/136 = silent trap without override — the 9 integration nodes); row baseline 137→136 tree-neutral drift |
| 4 | boot_probes_unit_test (pack script; internal timeout 120) | PASS 83/83 | PASS 83/83 | **∅** — collected==executed both trees |
| 5 | post_restart_arm_notify_sweep_unit_test (pack script; internal timeout 110) | PASS 33/33 | PASS 33/33 | **∅** — exact baseline |
| 6 | router cage ad-hoc (tests/unit/test_router_scope_manifest.py; +40/−11 branch delta is data-only) | PASS 7/7 | PASS 7/7 | **∅** — same 7 test names both trees; HEAD pins the wedge router via cage DATA (scope map :91, census 31→32 :264-266, decorated 100→104 :322-326); BASE has zero wedge hits |

**Series verdict: NET-NEW = ∅ across all 6 slices — zero branch-caused regressions.** Disclosures: api_unit_test's 6 reds are pre-existing base-identical mock-seam debt (routed, not new); tools_suite_unit_test EXCLUDED from the slice (chronic 110s cap / 3,284 nodes; registry lane surgically covered by the seven-pin set); `test_router_class_manifest.py` + `test_service_tool_flag_off_byte_identical.py` ride the seven-pin set's lane (the latter is in the set; the former is inside the router cage family via scope-manifest data pins).

## G7 — Known-residual register (verify-as-described; none filed as defects)

| Residual | Verdict | Evidence |
|---|---|---|
| W2 budget granularity floor (≤~2s overshoot, <2.5s phase) | **HOLDS (conservative)** | docstring db_capture.py:108-120 (floor statement :114-116); `int(budget*0.4)==0 ⟺ budget<2.5` exact; modelled worst single-phase overshoot **≤~1.0s** (end-to-end 1.002s) — real envelope ~half the stated bound. File-artifact steps entry-gated only = documented split (writer.py:29-33) |
| Mid-run reconcile borrows pooled engine on watchdog thread (W5) | **HOLDS 4/4** | pooled `Session(self._engine)` (repository.py:173, engine from manager); watchdog thread path watchdog.py:275→416/447→504 (boot-half on lifespan thread :344 — the accepted W5 shape); budget-CHARGED (`started` at :489 precedes reconcile :504; never a fresh clock) — precision: charged but not statement_timeout-CAPPED; swallow-disciplined (:696-713 + per-sidecar try/except + loop guard) |
| Degraded-row parity pin (hardcoded 11-key healthy set) | **HOLDS** | literal at test_wedge_capture_writer.py:296-300 (11 keys counted); convention-bound both sides (degraded row also hand-written; readback does not re-pin the set) |
| tools-LIST TOCTOU (microscopic, informational) | **CONFIRMED** | list_tools() dict-copy = GIL-atomic stale-snapshot (benign, add/update-only); list_tools_by_category() live iteration → worst case `RuntimeError: dictionary changed size` surfaced as tool error; no sync primitives; no production removal path; no security decision |

## Five most important evidence lines

1. `POST /api/wedge-captures/capture → HTTP/1.1 202 Accepted … capture written dir=… reason=manual artifacts=8 duration=0.11s row_written=True` (real booted daemon, real PG row count=1) — the manual-capture lane works end-to-end.
2. `MigrationError: Migration 20260714_000001 failed: (sqlite3.OperationalError) near "EXISTS"` — Lane B's committed boot-alive gate boots SQLite because `PG_TEST_*` never reaches the daemon; the pin cannot pass as committed.
3. `{"enabled":true,"running":true,"started_at_monotonic":333739.76…,"lag_probe_state":"ok"}` — the api.py ship-blocker class is behaviorally closed on a correctly-wired PG boot.
4. `refused :: UniqueViolation: duplicate key value violates unique constraint "idx_wedge_captures_capture_dir"` + `count=1` on re-insert — ON CONFLICT dedup holds via the REAL unique index on real PG.
5. `NET-NEW = HEAD_failed − BASE_failed = ∅` — reproduced identically across all 6 regression slices (zero branch-caused regressions).

## Action items (for the implementer lane / caller — none applied by this gate)

- 🔴 **Lane B harness**: translate `PG_TEST_*` → `POSTGRES_*` (or export POSTGRES_*) in `scratch_env()`; fix the `/proc/<pid>/cgroup` read race; fix skip-reason/env-gate docs (both vars required). Until then the boot-alive pin is inert.
- 🟠 T40 evidence deliverable (SC→pack gate table RESULTS file) not materialized — this document partially covers it; the plan's own T40 close-out still owed.
- 🟠 SC-2: either move leg-ii to the prescribed env+duration (LAG_S=5.0? plan: sustain=2, block ≥5s/6s) or adjudicate the composition-proof in the plan record.
- 🟠 `.sql` `metrics_snapshot` json→jsonb record drift; document runner-based UP-extraction as the only sanctioned manual apply.
- 🟢 remaining register items (latent insert() defect, row_written token semantics, duplicate index, injection_window persistence, --wait flag-explicit pin, D37 behavioral pin) — follow-up polish, none blocking.

**Report path**: `.agents/tester/RESULTS/2026-10-23-wedge-capture-independent-verification.md` (this file). Docs-only evidence commit on `tester/wedge-capture-verification` (sha in the gate report to the caller).
