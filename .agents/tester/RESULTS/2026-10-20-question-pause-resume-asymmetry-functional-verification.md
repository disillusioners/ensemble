# Functional Verification Gate — `fix/question-pause-resume-asymmetry`

**Date:** 2026-10-20
**Branch / tip:** `fix/question-pause-resume-asymmetry` @ `b1ec0c0d7` (base `1ebdf469f` = origin/latest)
**Worktree:** `/home/nea/ensemble-src-wt-qprasym` — fenced dev env (`ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`)
**Lane:** tester (functional verification gate) — dev + reviewer lanes had already run their bounded checks
**Interpreter:** worktree `.venv` = CPython 3.14.7 / pytest 9.0.2 (built by `uv sync`, 124 pkgs, ~2s)

> Status: **FINAL** — all 9 gates adjudicated. Verdict: **READY FOR MERGE + STAGING** (see §6).

---

## 0. Scope decision

Commission asked for the full regression suite. Full suite **warranted**: this is a merge+staging gate on a cross-module change (10 production files under `daemon/`, incl. `instance_lifecycle.py` (+188), `repositories/instance/repository.py` (+146), `routers/instances.py`, `routers/answer_helper.py`, `manager.py`, `api.py` boot pass, plus 2 NEW service modules) touching the pause/resume + answer-delivery core. The project already ships a committed full-suite layout (12 regression partitions P-1..P-12, pre-registered in `PACKS.md`), so the full run is executed as those registered packs — no ad-hoc glob partitioning, no deselection of baseline families.

**Executor split:** 11 concurrent workers, one pack each, every invocation double-layered (`timeout 300` outer + the pack's own internal watchdog) and env-scrubbed (16-var `env -u` sweep).

---

## 1. Verdict table (rolling)

| # | Gate | Verdict | Evidence |
|---|------|---------|----------|
| 1 | ORIGINAL SYMPTOM — `tests/manual/question_pause_strand_repro.py` | ✅ PASS | exit 0, 2× NO STRAND (both shapes), §4a |
| 2 | Scenarios a–d (answer-release / restart-backstop / age-bound both sides / question-protection) | ✅ PASS (unit 19/19 + real-process release leg via gate 5; re-arm stratum unit-proven, disclosed) | §4b + §4g |
| 3a | Question-adjacent packs (`test_question_dismiss.py` 15 · `question_validation_targeted_unit_test.sh` · branch 40-case pack) | ✅ PASS | 15/15 · 140/140 · 40/40 — §4c |
| 3b | Adjacent surfaces (boot rehydrate + sweep ordering, watchover, midflight QA, RDRS, orphan sweep) | ✅ PASS (with 1 test-side quick fix, A/B-evidenced) | 643/643 — §4e |
| 3c | Full-suite regression — partitions P-1..P-12 (+ split successors) | ✅ PASS — 12/12 partitions adjudicated, ~291 partition failures, **0 branch-caused** (ledgered / base-observed / symbol-exonerated with disclosure) | §5 + per-partition adjudications |
| 4 | N4 real-PostgreSQL assertion (ensemble_dev) | ✅ PASS | 6/6 cases, real SQL on record — §4f |
| 5 | Boot smoke on 8079 (lifespan order, sweep log line, livez/readyz, clean stop) | ✅ PASS (3/3 phases; 2 disclosed deviations, neither branch-caused) | §4g |
| 6 | Sweeper behavior (idempotency, kill-switch, threshold) | ✅ PASS | probes PASS ×3 + unit kill-switch node — §4d |
| 7 | Deliberate-pause protection (tombstone, answer + dismiss paths) | ✅ PASS | 7/7 tombstone nodes green, §4b |
| 8 | Route-table readback | ✅ PASS | recon-verified, below |
| 9 | Hygiene (migration delta, daemon/tools footprint, fencing, live-daemon fence) | ✅ PASS | recon-verified, below |

---

## 2. Gate 8 — route-table readback (independent, at tip) — **PASS**

App imported clean under the scrubbed env; **240 routes** enumerated.

```
/api/instances/{instance_id}/question/dismiss | POST | dismiss_question | obj=APIRoute | isAPIRoute=True
```

- (i) Dismiss endpoint binds handler `dismiss_question` — corroborated at source `daemon/routers/instances.py:1238-1239`:
  ```python
  @router.post("/{instance_id}/question/dismiss", dependencies=[scope_required("instances:write")])
  async def dismiss_question(
  ```
- (ii) Underscore-prefixed endpoint names app-wide: **count 0** (expect 0) — no hits.

---

## 3. Gate 9 — hygiene — **PASS**

- **Migration delta vs latest:** `git diff <base>..HEAD --stat -- daemon/migrations/versions/` → **EMPTY** (0 files; also 0 across all of `daemon/migrations/`).
- **`daemon/tools/` footprint:** `git diff <base>..HEAD --name-only -- daemon/tools/` → **EMPTY** (0 files).
- **Test fencing audit** over all 3 branch test/pack files (`test/packs/question_pause_resume_asymmetry_unit_test.sh`, `tests/manual/question_pause_strand_repro.py`, `tests/unit/test_question_pause_resume_asymmetry.py`): **CLEAN** — zero hits for `/home/nea/agents-ensemble`, `$HOME/agents-ensemble`, `~/agents-ensemble`, `agents-ensemble/releases`; zero `rm -rf` / `shutil.rmtree` / `os.remove` / `unlink`; HOME never resolved. Temp fencing present: pytest `tmp_path` (`sqlite:///{tmp_path}/question_pause_resume.db`, NullPool+WAL) and `tempfile.TemporaryDirectory(prefix="qpause-repro-")`. No `FAKE_HOME` needed because HOME is never resolved — satisfies the absolute live-shape-path rule by construction.
- **Live-daemon fence:** live daemon (9797) untouched, `ensemble_prod` unreachable under the scrub (`postgres_env_available=False` ⇒ sqlite default), port 8088 untouched.

---

## 4a. Gate 1 evidence — ORIGINAL SYMPTOM (must-pass) — **PASS**

Run: `timeout 300 env <SCRUB> .venv/bin/python tests/manual/question_pause_strand_repro.py` → **exit 0**, 8 s. `grep -c "RESULT: NO STRAND"` = 2; `grep -c "RESULT: STRAND PRESENT"` = 0. Import-resolution proof binds the verdict to the worktree tree (`daemon.instance_lifecycle` → wt-qprasym; branch-only modules `question_pause_set.py` + `stale_pause_sweep.py` present; `question_pause_refused` exists).

**Shape A (side-effect-child strand) — observed transition, verbatim:**
```
pause cascade parked: ['asker-1', 'root-1', 'side-1']
side-1 (side-effect target) status : running
side-1 paused_at                   : None
root-1 (parent) status             : waiting_children
re-armed (woken) instances         : ['asker-1', 'root-1', 'side-1']
```
Child un-paused (`running`, `paused_at=None`) and able to complete; parent leaves `waiting_children` downstream; the side-effect child **is in the re-armed set** (the pre-fix strand left it out). Answer status: `answered`.

**Shape B (late-pause race) — observed, verbatim:**
```
answer status      : answer_fallback_enqueued
late pause refused : True
side-1 status      : running
root-1 status      : waiting_children
```
Late pause refused by the branch fix itself (`pause_instance_cascade: REFUSING question pause for asker-1... — pack state='answered'`). Tree not parked with no answering counterpart.

Disclosures: (i) layer-2 watchdog absent in the manual script (outer `timeout 300` sole guard; 8 s runtime — non-issue); (ii) harness-stderr noise (MagicMock SSE emission; shape-B `answer_fallback_enqueued` is a harness ordering artifact — the asker was not paused at answer time so production's Defect-3 fallback fired — NOT a product defect).

---

## 4b. Gate 2 (a–d) + Gate 7 evidence — branch commission pack — unit level

Pack run: `timeout 300 bash test/packs/question_pause_resume_asymmetry_unit_test.sh` → **exit 0, RESULT: PASS, 40 passed / 0 failed / 0 skipped**, 45.58s wall. Node map (same file, `-v`): **40/40 PASS** again. Zero quarantine rows on this file (verified by grep, not assumed) — every green here is load-bearing. Worktree venv 3.14.7; HEAD unchanged pre/post.

**Scenario (a) — 6/6 PASS** incl. `test_scenario_a_answer_releases_side_effect_paused_child`, `test_waiting_children_parent_is_restored_and_woken` (parent restored to `waiting_children` + `b2_wc_wake` route), both symptom-repro pins, `test_recorded_target_with_paused_turn_is_resumed_by_handle`, `test_cas_loser_branch_also_releases_recorded_set`.
**Scenario (b) — 2/2 PASS.** Restart mechanism (verbatim from the test, `tests/unit/test_question_pause_resume_asymmetry.py:1585-1587`): a NEW `_FakeManager` is constructed over the same file-backed SQLite engine (`sqlite:///{tmp_path}/question_pause_resume.db`, NullPool+WAL) while the RAM-only question pack is provably lost (`get_question_pack(...) is None`); the recorded paused set survives in the DB, the answer path rehydrates from the durable shadow and releases; the boot-sweep variant recovers a stranded instance → `RUNNING` + woken. *In-process new-manager simulation; the real new-PROCESS leg is gate 5's boot smoke over persisted `ensemble_dev` state (with gate 4's leftover candidate row).*
**Scenario (c) — PASS, both sides verbatim (`:1694-1700`):**
```python
assert "stale-1" in result["instances"]                       # older (3h) IS swept → RUNNING
assert _status(engine, "stale-1") == InstanceStatus.RUNNING.value
assert "young-1" not in result["instances"]                   # younger (5min) NOT swept
assert _status(engine, "young-1") == InstanceStatus.PAUSED.value
assert "young-1" not in _woken_ids(manager)
```
(bound `age_bound_seconds=7200`.)
**Scenario (d) — 3/3 PASS**: pending question protects the asker (`skipped_protected >= 1`); D1 pins the predicate as TREE/LINEAGE-scoped (not asker-scoped); the counter-pin proves a resolved question releases the tree.

**Gate 7 — deliberate-pause tombstone, 7/7 PASS:**
- Answer path (`:603-610`): deliberate pause survives — `PAUSED` retained, node excluded from `_woken_ids` ("the tree-scoped resume must not flip it back to running").
- Dismiss path (`:839-849`, drives the REAL endpoint function `dismiss_question`): same — "the dismiss path flipped a deliberately parked instance back to running — the exclusion hole" assertion holds (stays `PAUSED`, not re-armed); non-deliberate members still released.
- Sweeper side: T1 (`paused_external`/`watchover_setup` never swept), T2 (task-less deliberate pause protected by marker), stale-question-marker → tombstone replacement, N5 (already-parked node loses stale positive marker; `(marker or {}).get("deliberate") is True`).

**Caveat for future re-runs (non-blocking):** the pack script overrides per-test timeout to 60s and clears `addopts`; a bare `pytest <file>` uses pyproject's 30s default. Both invocations green under both profiles at tip. To be noted in PACKS.md at aggregation.

**Concurrent-lane note:** during this pack, another of this gate's own workers (adjacent-surfaces lane) ran a 37-file sweep in the same worktree — zero file overlap (verified), hermetic file-backed SQLite fixtures, no contamination surface. Both runs green.

---

## 4c. Gate 3a evidence — question-adjacent packs — **PASS (3/3 packs, 195/195 cases)**

| Pack | Counts | Runtime | Notes |
|---|---|---|---|
| `qprasym_dismiss_unit` → `tests/test_question_dismiss.py` | **15/15** | 2.8 s | dismiss route-binding surface; `dismiss_question` at `daemon/routers/instances.py:1239`, unwind mirror `:777`, error branches `:1360/1366/1460` |
| `question_validation_targeted_unit_test` (pack script, internal 110s deadline) | **140/140, 10/10 files** | 58 s | interpreter fence verified: shebang → worktree `.venv` 3.14.7, `import daemon` resolves INSIDE the worktree |
| `question_pause_resume_asymmetry_unit_test` (branch pack) | **40/40 ×2 invocations** | 45.6 s / 43.9 s | see §4b |

Drift disclosure (validation pack): pack header + PACKS.md row 1480 claim **126**; actual at tip = **140** (+13 `test_question_tools_validation.py` 48→61, +1 `test_question_manager.py` 17→18; other 8 files exact). File growth, not a selection error — the 8 unchanged files prove the selection set stable. **Doc-drift only**; PACKS.md row corrected at aggregation (this lane owns PACKS.md).

Zero quarantine rows across all three packs' files (grepped; the single mention of `test_question_deferred_pause_edge_cases.py` in the ledger is an explicit no-row companion note) — every green is load-bearing, any red would have been NEW by construction.

**Main-checkout disclosure (not this worktree):** `/home/nea/ensemble-src` carries pre-existing dirt from OTHER lanes (` M .agents/approver/active.md`, ` M .agents/tester/PACKS.md`, ` M .agents/tester/QUARANTINE.md`, ` M .agents/tidier/notes.md`, ` M agents/tidier[v2]/skill-set.yaml`). Not created or touched by this gate; flagged for the merge lane. This gate's evidence commit happens in the WORKTREE only.

---

## 4d. Gate 6 evidence — sweeper behavior — **PASS**

Closes the two recon coverage gaps (F3: no idempotency test; F4: kill-switch/threshold only at function-param level). Ad-hoc probes in scratch `mktemp -d` (nothing added to the repo); every invocation `timeout 300` + 16-var env scrub; branch harness helpers imported (not copied); REAL `sweep_stale_pauses` / `unpause_instance_after_question_pause` / `_pending_question_in_tree` / `find_stale_paused_instances` driven.

**Idempotency (no double wake) — PASS.** One task-less stale candidate (`stranded-1`, marker age > 2h, no task row, no pending question). Wakes counted via the harness manager's three recorders (`wakes`/`resume_calls`/`enqueued`), snapshotted before/after each call:
```
sweep1: {candidates:1, resumed:1} → delta {wakes:1, resume_calls:0, enqueued:0}; status paused→running; paused_at→null
sweep2 (identical state): {candidates:0, resumed:0} → delta {wakes:0, resume_calls:0, enqueued:0}; no exception
```
Production log pair captured: `stale-pause sweep: candidates=1 resumed=1 …` then `candidates=0 resumed=0`. Mechanism (code): release flips status (`question_pause_set.py:1043-1049`) + single silent checkpoint resume (`:1186-1237`); selector only admits `status=='paused'` or PAUSED-task rows (`repositories/instance/repository.py:926-938`) → released row leaves the selector. Fresh-engine rebuild (Part B) reproduces the same shape.

**Kill-switch env — PASS.** `SERVICES_STALE_PAUSE_SWEEP_ENABLED=false` → parsed `ServicesConfig.enabled=False`, wiring constructs the service disabled, **no thread**, boot pass makes **0** sweep calls (skipped + "disabled by config — skipped"). `=true` and unset (default **enabled**) → 1 boot sweep call. Both wiring sites executed verbatim via source-slice `exec` (no re-implementation): `pool_orchestrator.py:343-350` + `.start()`; `api.py:2530-2543`.

**Threshold config — PASS.** `SERVICES_STALE_PAUSE_SWEEP_AGE_BOUND_SECONDS=60` (with `…_INTERVAL_SECONDS=42`, `…_BATCH_CAP=7`) → parsed 60/42/7 and the boot pass invoked with exactly those; unset → default **7200** (interval 300, batch cap 100). Env names proven empirically (values track the vars), not derived. Full env surface: `SERVICES_STALE_PAUSE_SWEEP_{ENABLED,INTERVAL_SECONDS,AGE_BOUND_SECONDS,BATCH_CAP}` (`daemon/config.py:1959,1967,1976,1995`, `env_prefix` `:1737-1740`).

**Fence sanity — PASS.** Scrubbed process held **zero** socket FDs; `postgres_env_available=False`; sqlite branch taken (`ensemble_config.py:82-91`); the 13 netns PG connections belong to the pre-existing live/demo daemons (pids 1147/2296421), untouched.

**Follow-up debt (nice-to-have, not blocking):** (1) F3 regression test should be added to the branch suite, scoped to the task-less candidate class or modeling the `ResumeTurn` PAUSED→PENDING transition — the harness stub `resume_processing_job` does not perform it (C2 trap; would encode the stub as the product). (2) The wake-less stratum (status=running + PAUSED task) re-selects candidates each cycle in the harness — production's task transition removes the row; harness-only artifact.

---

## 4f. Gate 4 evidence — N4 real-PostgreSQL assertion — **PASS (6/6)**

Executed the REAL predicate (`SQLModelInstanceRepository.find_stale_paused_instances`) + REAL `sweep_stale_pauses` against PG 16.15 `ensemble_dev`. Target proof: `select current_database() → ensemble_dev` (local socket `/var/run/postgresql`, peer auth, PG role `nea`; the same instance listening on 127.0.0.1:5432). `ensemble_prod` / 10.44.0.2 never contacted; 9797/8088 untouched; no daemon booted.

| Case | Result | Evidence |
|---|---|---|
| (i) OLD (3.000 h) | ✅ SELECTED + resumed | in predicate result; status → running |
| (ii) YOUNG (300 s) | ✅ NOT selected | `paused_at < cutoff` excludes; stays paused |
| (iii) PROTECTED asker | ✅ NOT selected | D1 `has_pending=True` → "outstanding question in lineage tree still owns the pause" |
| (iv-a) owner alive (running) | ✅ NOT selected | pending pack + live owner → protected |
| (iv-b) owner dead (terminated) | ✅ SELECTED + resumed | same pending pack, terminal owner ⇒ stale shadow ⇒ swept |
| (v) DELIBERATE tombstone | ✅ NOT selected | `{"deliberate":true,...}` — not a candidate |

Service sweep: `candidates=4 resumed=2 skipped_protected=2 errors=0`. Owner-liveness arm per `stale_pause_sweep.py:222-223` + `TERMINAL_INSTANCE_STATUSES` (`repository.py:41`); RAM-pack arm and durable-shadow arm each protect independently (supplementary pass). Predicate SQL (verbatim, captured by `before_cursor_execute`): marker filter `metadata -> 'question_paused' ->> 'pack_id' IS NOT NULL AND (metadata -> 'question_paused' ->> 'paused_at') < :cutoff`, no pending/running task EXISTS-guards, paused-status admission, suspension_reason IN ('awaiting_answer','paused_by_parent'), `ORDER BY paused_at LIMIT 1000`; batched owner-liveness `SELECT instance_id, status FROM instances WHERE instance_id IN (...)`. Age bound `DEFAULT_STALE_PAUSE_AGE_BOUND_SECONDS=7200`, cutoff `2026-10-20T16:34:09Z`.

**Observation (not a failure):** the age bound is a lexicographic TEXT comparison of ISO strings — correct only under the all-UTC-ISO writer convention (currently holds). 

**Deviations disclosed:** (1) `.env` role `ensemble_dev_app` FAILED auth (stale SCRAM verifier — broken for every dev lane on this host; flagged to commissioner, dev-role password reset authorized for gate 5); connected as socket-peer superuser to the same dev DB instead. (2) Sweep ran with real repos but no wake primitives — selection/protection proven, the wake/re-arm leg deferred to gate 5 by design. (3) The designated leftover was released once by the service sweep then restored to the exact seeded shape (verified by read-back). (4) 4 aborted harness attempts (proof/seed phase, zero residue). Cleanup: 7 instance rows + 2 hierarchy rows deleted; final `qprasym_gate4_` count in `instances` = **exactly 1** (`qprasym_gate4_old_cand`, paused, marker age 3.019 h at handoff — the sole `question_paused`-marked row in the DB); pre-existing instances 34 → 34 unchanged.

**Leftover handoff to gate 5:** `instance_id=qprasym_gate4_old_cand`, `paused_at=2026-10-20T15:34:09.575502+00:00`, marker `{'question_paused': {'pack_id': 'qprasym_gate4_pack_old', 'paused_at': '…'}}`, agent `worker`, no parent, no task row.

## 4g. Gate 5 evidence — REAL boot smoke — **PASS** (+ gate 2 real-process leg closed)

Booted the REAL daemon from the worktree (`PROC_CWD` proven, banner `Starting Ensemble v0.25.7`) on the fenced env (8079 / ensemble_dev), three bounded phases.

**Credential repair (authorized, disclosed):** `.env` role `ensemble_dev_app` repro-failed auth (`FATAL: password authentication failed`) — repaired via socket-peer superuser with `ALTER ROLE ensemble_dev_app WITH PASSWORD <.env value>` (value never printed; `select current_database()=ensemble_dev` proven before any write; TCP auth then verified). Dev-only; no prod role/host touched. This unblocks every dev lane on this host.

**FENCE-GAP FINDING (commission env recipe):** the strip list omitted `PORT` — ambient `PORT=9797` leaks through `config.yaml → daemon.port: ${PORT:-8088}` and the yaml-init kwarg BEATS `DAEMON_PORT`. First boot ran full lifespan then died at bind on 9797 (address-in-use; live prod pid 2296421 never touched, uptime continuous). Fixed: `-u PORT -u HOST` + explicit `PORT=8079 HOST=127.0.0.1`. House scrub list updated. On a host where 9797 were free this leak would bind prod's port — worth a permanent fix (config precedence: env `DAEMON_PORT` should win over ambient `PORT`).

**Phase 1 — normal boot (PASS):** livez/readyz 200 (`version 0.25.7`, all components true) in 15 s. Ordering evidence (verbatim, log lines): `:122` ChatSourceWorkerPool started → `:123` `question-packs rehydrated: 0` → `:132` **`stale-pause sweep: candidates=1 resumed=0 skipped_protected=0 errors=0 (cutoff=…, age_bound=7200s)`** — sweep strictly AFTER setup_worker_pool; rehydrate pass undisturbed. The persisted gate-4 leftover was DETECTED and RELEASED by the fresh process (gate 2(b) real leg): `[RESUME] instance=qprasym_ has_checkpoint=False` → `question_pause_set: no checkpoint … cannot be re-armed; leaving the instance idle (no limbo 'running with no work')` → `release … restored_status=running outcome=wake_refused`. **Deviation (fixture-shaped, disclosed):** `resumed=0` not ≥1 — the gate-4 fixture has no checkpoint/task row, so the wake is refused and the marker is RETAINED by the documented fail-safe (`question_pause_set.py:1329-1347`: refused/errored releases keep the marker so the backstop retries). The commission's bar — "either the answer path or the boot sweep releases it" — is MET (paused → released/idle on a real process); the re-arm stratum (checkpoint + paused task → RUNNING + woken) is proven at unit level (scenario-b nodes, §4b). Wiring nuance disclosed: the periodic service's immediate first tick (`pool_orchestrator.py:341-350` inside setup_worker_pool) pre-empts the `api.py` boot pass by ~3 s, so the boot pass's own line reads `candidates=0` after the service already swept. Clean shutdown: SIGTERM → exit 143 within 2 s, `Graceful shutdown complete`, no orphans, `PORT_8079_FREE`.

**Phase 2 — kill-switch boot (PASS):** `SERVICES_STALE_PAUSE_SWEEP_ENABLED=false` → verbatim `daemon.api - INFO - Stale-pause boot sweep: disabled by config — skipped` + `StalePauseSweepService disabled by config; skipping start` (BOTH lanes honor the switch); `grep -c "stale-pause sweep: candidates="` = **0**; strongest proof: the fixture row survived a full healthy boot byte-untouched (`('paused', '2026-10-20T15:34:09…', marker intact)`). Clean shutdown again; 8079 freed.

**Phase 3 — cleanup + fences (PASS):** `qprasym_gate4_` rows deleted (instances 1, hierarchy 0) → final `qprasym%` = 0; total instances back to **34** (exact pre-existing count); marker rows 0; no scratch DBs created; 9797 still live-prod's (pid 2296421 continuous); 8088 untouched; all three boot PIDs gone. Logs preserved at `/tmp/qprasym-smoke-A7DJTn/`.

**Pre-existing infra notes (non-branch):** MCP computer-use warmup fails on this host (libXtst.so.6 missing); double boot banner (`__main__` + `api` both log it — branch touches neither site).

## 4. Recon findings carried into the gate (pre-execution)

**F1 — "code frozen at `e7634c39d`" is inaccurate.** Tip `b1ec0c0d7` (the docs tail) additionally touches production code: `daemon/services/instance_lifecycle.py` +1/−1 — a comment swap (`# noqa: BLE001 — fail-open` → `— fail-CLOSED`), disclosed in the commit message as "ONE comment line". **No logic change.** All verdicts in this file are **tip verdicts at `b1ec0c0d7`**, which is strictly stronger than the stated freeze point. Disclosed, not blocking.

**F2 — PACKS.md registration gap (FIXED by this lane).** The branch's own pack `test/packs/question_pause_resume_asymmetry_unit_test.sh` was committed **unregistered** (`PACKS.md` diff vs base = EMPTY). Registered by this lane in a new dated section (`## Gate packs 2026-10-20 — question pause/resume asymmetry functional gate`), together with the repro script, the dismiss pack, the adjacent-surfaces pack, the real-PG probe and the probes pack. The manual repro script is registered as an ad-hoc pack (it is a script, not a pytest pack).

**F3 — coverage gap: no idempotency test.** No branch test sweeps identical state twice. The only double call (`test_deliberate_pause_with_paused_task_is_not_swept`, lines 397/413) changes the discriminator between calls — a discriminator pin, not an idempotency proof. Gate 6 is therefore executed via an **ad-hoc double-sweep probe** (scratch, mktemp) that counts wakes before/after each call.

**F4 — coverage gap: kill-switch + threshold are function-parameter-level only.** No test references `stale_pause_sweep_enabled` / `stale_pause_sweep_age_bound_seconds` / `ServicesConfig`; env names would be `SERVICES_STALE_PAUSE_SWEEP_ENABLED` / `SERVICES_STALE_PAUSE_SWEEP_AGE_BOUND_SECONDS` (`env_prefix="SERVICES_"`, `daemon/config.py:1740`), consumed at `daemon/api.py:2530-2542` (boot pass) and `daemon/services/pool_orchestrator.py:341-350` (construction). Gate 6 therefore also carries an **env→config→wiring probe**, and gate 5 carries a **boot-time kill-switch boot** (sweep line must be absent).

**F5 — two PRE-EXISTING whole-tree collection blockers** (neither in this branch's diff): `tests/packs/g7_unique_index_smoke_test.py:121` module-level `sys.exit(0)` → pytest `INTERNALERROR`; `tests/e2e/test_context_injection_hybrid.py:32` module-level `requests.get("http://localhost:8079")` → collection ERROR (live-daemon import dep, already a known QUARANTINE family). Whole-tree sizing/collection therefore requires `--ignore=tests/packs --ignore=tests/e2e/test_context_injection_hybrid.py`. Relevant to the P-12 partition (it sweeps `tests/e2e/`) — the worker is told to expect this blocker.

**F6 — interpreter hazard.** The main checkout's `/home/nea/ensemble-src/.venv` is **Python 3.13.15** with an editable `.pth` resolving `import daemon` to the MAIN tree — using it as `PYTEST_BIN` would run the wrong interpreter against the wrong code. **This gate runs exclusively on the worktree venv (3.14.7)**; every worker is instructed to report the interpreter it actually used, and to abort if it resolves outside the worktree.

**F7 — shared worktree.** A foreign lane ran pytest against this worktree's `.venv` during recon (~18:03Z). Every worker therefore takes a porcelain snapshot before and after its run; the expected delta is exactly ` M .agents/tester/PACKS.md` (this lane's registration edit). Any other porcelain line is reported LOUDLY.

**Suite scale (collect-only, env-scrubbed, worktree venv):** whole `tests/` = **28,442 collected / 896 deselected** in 22.22s (excluding the two F5 blockers); `tests/job_queue/` 2,150; `tests/integration/` 1,085; `tests/test_question*.py` 111; `tests/test_question_dismiss.py` 15; branch suite 40. `pyproject` markers: `integration, postgres, no_xdist, slow`; default `addopts = -m 'not integration and not postgres and not slow'`, per-test `timeout=30`, `asyncio_mode=auto`, `testpaths=["tests"]`. `tests/manual/` is not collected by pytest's naming convention (not an explicit exclusion) — the repro script is executed directly.

**Quarantine ledger (relevant surfaces):** `tests/unit/test_phase4_manager_decomposition.py::TestFacadeDelegationPattern::test_manager_pause_instance_cascade_delegates_to_lifecycle_service` (stale `pause_instance_cascade` contract — **verified NOT worsened**: the branch changed `resume_instance_cascade`, not `pause_instance_cascade`); `tests/e2e/test_e2e_workflows.py:1894 test_pause_after_spawn_then_resume`; pause-during-report family ×3; `tests/integration/test_cold_resume_ttl.py` ×2; `tests/e2e/test_answer_dismiss_flow.py` ×1; `test_job_queue_proxy_phase1` derived-status map ×7 (includes the `waiting_children→processing` fall-through = scenario (a)'s parent-state surface); `test_paused_auto_resume_fallback` ×5; `test_pause_race_w7_jobitem_skip` ×1; `test_instance_messaging_queue_routing.py::…::test_router_forwards_queue_id_to_enqueue_message_job` ×1; WATCH: `test_slash_commands_router.py::TestRunningAckOrdering::test_ack_returns_and_waiting_emits_while_pause_blocked`. The 5 case files inside this branch's blast radius carry **zero** quarantine rows.

---

## 5. Execution log

**Machine context:** `nproc = 8`; partitions ran under concurrent sibling lanes (1-min load peaks 7.3 → 15.0) — no timeout anomalies despite contention; partition waves staged accordingly. Worktree-hygiene notes: (i) ` M .agents/shared/planning/designer-agent/install-audit.jsonl` (+9 `kms_issue` rows appended at daemon import/collection — documented gotcha, not branch-caused; queued for owner-lane revert before the evidence commit); (ii) ` M tests/unit/test_watchover_crash_recovery.py` = the adjacent-surfaces lane's OWN authorized test-side quick fix (§4e) — committed with this gate's evidence.

**P-2 failure adjudication (FAIL-by-baseline, 0 branch-caused):** 9F = (a) 7 node-identical to the Active-QUARANTINE `job_queue_proxy_phase1` derived-status family (all fall through to canonical `'pending'`; line drift 293/418/… → 312/440/… traced to pre-branch commits `6e5a7cb86` + `df92e8a25`, both ancestors of base — node IDs unchanged, NOT a rotation); (b) 2 additional failures, both Active-QUARANTINE-ledgered: `test_scheduling_service.py::TestUserTimezoneRung::test_user_setting_rung_covers_once_anchor_site` (`next_run_at_utc=None` — the "once-anchor TIME-BOMB" family; anchor future-dated at baseline legs, past since 2026-10-05, cannot be branch-caused) and `test_service_tool_manager.py::test_stop_pid_already_dead_returns_pid_dead` (`KeyError: 'reason'` — exact node+line+signature match vs the 2026-10-02 row, base-identical). Branch-causation ruled out by construction: the branch diff touches none of the 5 production seams behind the 9 failures (`work_status.py`, `work_resolver.py`, `repositories/job_queue/repository.py`, `scheduling_service.py`, `service_tool_manager.py`); `instance_lifecycle.py` references the canonical map only in a comment. **P-2 header comment stale** (claims 1,132; actual 2,913) — flagged for PACKS.md maintenance.
**P-11 failure adjudication (FAIL-by-quarantine, 0 branch-caused):** 16/16 failures match the Active-QUARANTINE 2026-10-19 consolidated 15-node `tests/job_queue/` row node-for-node plus that row's `tz_matrix` LOCATION-ATTRIBUTED clause (the 16th = an xdist worker crash on `gw6`, predicted to reappear on worktree legs). Cross-check: 2026-10-19 HEAD-leg parity at 2,150 collected / 38 skipped, fail delta +1 = exactly the location-artifact node. Vs the older 2026-09-23 13-node baseline the composition delta is fully explained by ledger evolution (U1 fixback re-anchored the feedback-observer nodes; `tool_registration` / `n8_hot_path_pin` / `job_answer_tool` joined later rows; `wedge_resolver` is the documented 1P/1F flipper). **Partition verdict: PASS-with-known-reds.**

**`install-audit.jsonl` attribution (resolved):** the +9 `kms_issue` rows were minted by THIS gate's own concurrent lanes running imports with cwd in the worktree (daemon-log correlation: lane `2e91499d` at 18:25:31, rows land 13 s later; every `kms_request` call site lives in `tests/unit/*`, none in any partition pack's scope). Test-mint noise in a tracked planning file — reverted by the owner lane before the evidence commit (the main checkout's real audit file is untouched).
**P-12 FINAL verdict (completing pass `3950b3ef`, node-level): FAIL-by-pre-existing, 0 branch-caused.** Recovered the deleted pack's exact invocation from `git show 2c8d4b190^` (default addopts NOT cleared — every `pytest.mark.integration` test silently deselected, same addopts defect the sibling integration pack documents; one hardcoded quarantine deselect; per-test 240 s; 280 s inner guard). Three legs mirroring it exactly (sequential, `--tb=line` only deviation): A1 integration half-1 (72 files) **17F/520P/4E/2S in 114.7 s** · A2 half-2 (73 files) **14F/528P in 106.1 s** · B e2e **4F/26P in 19.7 s** — Σ 1,115 collected (arithmetic complete: 1,074+35+4+2), no leg hit the guard, no retries. Collected drift 734→1,115 = corpus growth (branch added ZERO files to these dirs; opencode half dead by design, excluded with disclosure). **39 failing nodes:** (a) 11 BASELINE-MATCHED (U11 complete_cancel ×3 + pause_race_w7 ×1; lcan/lcau ×2 exact-node ledger hits; vscode C1 ×1; answer_dismiss_flow ×1; pause-during-report family ×3 — exact QUARANTINE rows); (b) 22 ledger-NEW, **ALL branch-exonerated by symbol-level proof** — vision-facade ×5 (`test_spawn_intelligence_tier`, `VisionModelNotAllowedError` introduced by `a01207220`, ancestor of base; **executed base-parity exists via P-10's A/B** on the identical `compare_tools.py` seam), m2 dead-letter vocabulary ×1 (`orphaned_no_task` stamp pre-branch `3617157c3`), explorer shared-context ×1 (injection sites untouched), macOS hard-coded initdb path ×1, message-metadata prune ×2 + attestation revive ×1 (**SOLO-PASS proven** — shared-process pollution class), maintenancer meta/memory.md ×9 (`agents/` untouched by branch: `git diff --name-only base..HEAD -- agents/` empty — repo-content drift), `.alist()` repo-wide guard ×1 (zero branch call sites; `checkpoint_adapter.py` untouched), mission vocab ×1 (zero `notify_watchers`/`enqueue_message` branch call sites); (c) 6 ROTATED still-red (complete_cancel B1 signature shift; `wc_wake_pure_hang` ×3 httpx→langgraph-Checkpoint ImportError; vscode SPA ×2 — `frontend/dist` not built in this worktree) + **17 HEALED** (the SSL-env httpx class :72 — 17 of 23 healed, 0 reproduced with the httpx signature; bucket5 ×6; dead_letter ×4). **Three replacement packs authored + validated by real runs** (e2e 4F/26P 18.3 s; integration_a 17F/523P 110.6 s; integration_b 14F/528P 218.4 s — watch B's budget under contention, 62% of guard): explicit file lists (a new integration file is never silently skipped), 3-way runner resolution (`~/.local/bin/uv run --no-sync` → `uv` → `.venv/bin/python -m pytest`), F5 ignore + quarantine deselect documented in-header. **Deviation disclosed:** no executed base-parity leg for the 22 (symbol-level + solo-pass + cross-A/B evidence instead — read-only lane discipline on a shared repo). **P-12 ledger debt:** PACKS.md row 1492 re-pointed by this lane at aggregation; `regression_integration_test.sh`'s 350/360 s guards exceed the house pattern (not edited — other lane's registered pack; overlap with new A/B halves flagged: A/B include chat-source files that pack excludes).

**Perf pack post-ruling state (quick-fix reuse `e5722f83`):** amended `-k` (adds `not 100x10k-variance` + dispatcher comment; `bash -n` OK; selection proof 10/14 collected, 4 deselected, no substring collision). Run: **completes in 42.99 s** (was 280 s+ TIMEOUT) — `4 failed / 6 passed / 4 deselected`, RESULT: FAIL. Residual classification: `test_variance_across_history_depths_component_below_threshold` (`KeyError: (100,10000)`) + `test_aget_component_decomposition_reported` (missing mean-cells `{(100,10000)}`) = **3rd/4th members of the approved hard-asserts-cell-set family** (they read the excluded 100x10k-variance producer's cell; the serial form isolates the true coupling vs LEG B's xdist form) — **ruling EXTENDED**: both added to the guard with the same removal-conditions note, executed in the final quiet-host lane. `test_2x_baseline_anchor[100x150]/[100x400]` = the documented **load-sensitive anchor family** (AC-3.3 wall-clock; run at load 6.2→9.7 with a 96%-CPU competitor; measured 4–8× baseline latency) — NOT excluded per the standing rule (pre-existing families run and are adjudicated from inventory); **quiet-host solo run is the arbiter**, scheduled after the last lane drains. Sibling-lane corroboration: the P-12 split lane independently hit and solved the same `uv`-not-on-PATH hazard (`~/.local/bin/uv run --no-sync` fallback) — PACKS.md maintenance will note the `.venv/bin/pytest` direct style as house default for new packs.

**P-12 TIMEOUT + STALE-LEDGER FINDING.** The registered pack `test/packs/regression_integration_opencode_e2e_test.sh` was DELETED upstream on 2026-09-19 (`2c8d4b190` — "split P-12 into 3 sub-packs") because it breached the 300 s cap solo; PACKS.md row 1492 is therefore a STALE POINTER to a dead path. Successors at HEAD: `regression_chat_source_integration_test.sh` (chat-source subset), `regression_integration_test.sh` (integration half, own 350 s/360 s guards — exceeds the house 280/300 cap), and `legacy/test/packs/regression_opencode_e2e_test.sh` (BROKEN: `PROJECT_DIR` resolves to `legacy/` and `legacy/tests/e2e/` does not exist; the opencode half is dead by design — `daemon/opencode/` + `tests/opencode/` archived to `legacy/` by `300ac0ba4`, so `daemon.opencode` is unimportable). The lane reconstructed the deleted pack VERBATIM from `git show 2c8d4b190^:…` (untracked, removed after the run) with 2 disclosed adaptations (opencode scope dropped as archived; F5 `--ignore=tests/e2e/test_context_injection_hybrid.py` added — the module-level `requests.get("http://localhost:8079")` raises ConnectionError at import when 8079 is free, aborting the e2e half's collection). All timeout layers unchanged (script-inner 280 s, per-test 240 s override, command-level 300 s, `test_pause_after_spawn_then_resume` deselect intact). Result: inner 280 s guard fired at **96%** (1,043 P / 34 F / 1 E / 2 S of ~1,125 collected; last ~4% never reported) → **TIMEOUT, not a verdict** (no node ids: `-rf` summary never printed and `.pytest_cache/lastfailed` was not updated). Magnitude is consistent with the expected ledger families (SSL-scrub httpx class ×23 — the scrub strips `SSL_CERT_FILE`/`SSL_CERT_DIR`, the exact trigger named by the 2026-10-19 ledger row; U11 cluster ×11; vscode C1 ×1) but node-level buckets are NOT assignable from a killed run. Branch-reach caveat recorded: the branch DOES touch `repositories/instance/repository.py` + `instance_lifecycle.py`, so a marker-only run cannot exclude branch attribution for nodes on those surfaces. Completing pass dispatched (split scope, junitxml node ids). **P-12 ledger debt: row 1492 must be re-pointed to the sanctioned split + a new e2e-only pack; the broken legacy opencode/e2e pack needs a fix or a retirement note.**

**P-10 A/B base-leg RESULT (closes the unledgered cluster):** detached base `1ebdf469f` (verified merge-base; note `origin/latest` has since advanced to `bfeca369d` — the A/B anchor is the pre-branch state, still correct), import-resolution-guarded `PYTHONPATH` recipe (`.pth` verified static-path, no MetaPathFinder; guard output recorded). The 7 test files are byte-identical base↔tip, so node IDs are unconfounded. Base leg: 126 P / **37 F** in 17.16 s. Match table over the enumerated reference set (38 nodes — the branch leg's "28" was an arithmetic slip; its own enumeration totals 38): **37/37 deterministic nodes MATCHED-PRE-EXISTING** (identical signatures incl. the exact `VisionModelNotAllowedError` string and source lines :403/:599; facade commit `693e24db5` independently confirmed an ancestor of base), **0 HEALED-AT-BASE (zero branch regressions)**, **1 NOT-REPRODUCED-AT-BASE** — `test_resource_monitor_self_cost.py::…::test_attribution_on_holds_p1_budget`, a `median < 5.0 ms` wall-clock gate: failed at the branch leg's full-partition xdist run (5.407 ms) but PASSED at base solo (load 4.1), under 16 synthetic burners (load 9.3→9.8), and under `-n auto` (load 9.8); `resource_monitor.py` has zero branch commits and the config diff contains no budget lines → **adjudicated load-sensitive, disclosed, not branch-caused**. **P-10 final: 53F = 25 ledger-matched + 37 observed-pre-existing + 1 load-sensitive disclosed — ZERO branch-caused.** Ledger debts flagged: vision-facade ×29 + team-members ×4 + SimpleNamespace ×2 + resource-monitor ×1 + bash-registry ×1 need QUARANTINE rows; `spawn_limit` message-text refresh ("near CONSTRAINT"→"near EXISTS"). AB worktree removed + pruned; fences held.

**P-10 failure adjudication (53F: 25 ledger/baseline-matched + 28 unledgered, branch-reach zero, A/B leg dispatched):** (a) 25 matched — `spawn_limit` ×9 (fresh-SQLite 20260714 trap; message text rotated "near CONSTRAINT"→"near EXISTS", ledger refresh owed), `skill_evolution_config` ×2 (2026-09-07 "NEW-at-baseline" flag RESOLVED — since ledgered), worker_notification ×2 + atomic_dequeue ×1 + grace_period ×1 (documented partition-context flake families re-manifesting). (b) 28 unledgered — vision-facade ×29 (`VisionModelNotAllowedError: allowed_models is not a list (got MagicMock)` via `create_instance_tools`→`compare_tools._verify_vision_allowed`; seam from commit `693e24db5` designer facade, post-dates the 2026-09-07 baseline) across `test_spawn_team_members` ×17 + `test_send_message_context_param` ×6 + `test_send_message_load_skill` ×6; team-members meta drift ×4; SimpleNamespace-rot ×2 (`task_processor.py:552` — same family P-9 saw ×10); resource-monitor ×2 (raw-minute bucket; self-cost 5.4ms-vs-5ms load-sensitive); bash-registry auto-kill ×1. Static proof: `changed_in_branch = 0` for every implicated production file. Per house standard, a base-leg A/B (7 files at detached base `1ebdf469f`) is running to convert static exculpation into observed-pre-existing evidence. (c) healed: `terminal_orphan_matrix[pending-True-active]` (the v0.25.3-adjacent node) baseline 1F→0F.

**P-5 failure adjudication (13/13 ledger-matched, 0 branch-caused):** `test_gaia_agent` ×3 (standing 2026-10-07 row, verbatim `'service'` allow-list signature) + `test_governor_recursion_guard` ×10 (standing 2026-10-07/10-12 row, `_make_manager` vision fixture seam via compare_tools `_verify_vision_allowed`; intra-family shape split drifted 5+5→4+6, same fixture seam, same total). Zero failures outside the two families; the stale 2026-09-03 baseline set (11F misc-cluster + llm ×2) did not reproduce at all — superseded by newer ledger rows; pack-header family list also stale. Branch diff touches none of the failing seams. P-5 baseline re-baseline queued for maintenance.

**P-4 failure adjudication (48/48 ledger-matched, 0 branch-caused):** `builtin_mcp_servers` 21E (byte-exact 17E+4E split, mock-gap rotated slash_commands→service_tool, ledger tracks the new form); `configure_builtin_idempotency` ×15 (2026-10-12 NEWLY-LEDGERED row, opendesign-template 404 bootstrap rot — dominant red mass, no fix commissioned yet); `coder_developer_migration` ×5; `devops_agent` ×3; `api_router_extraction` ×2 (module-size pin value-drifted 2,047→4,598 lines vs 1600 pin — nature unchanged); `caller_model` vision ×1; `bearer_p5` ×1 (env-shaped: surfaces BECAUSE of the mandated POSTGRES_* scrub; deterministic, ledgered, pending its own fix commission). Arithmetic reconciles exactly: 10 − 1 healed (`test_coder_agent::test_load_coder_prompts` now green) + 18 newly-ledgered = 27F; 21E count-identical to baseline. Anchors: the authoritative 2026-10-12 consolidated + 2026-10-19 rcc re-observation rows (the 2026-09-08 PACKS.md row is stale). Skips ×9 count-only (no ids from `-rf`) — flagged, not adjudicated.

**P-8 FINAL verdict (completing pass + real base leg): FAIL-by-pre-existing, 0 branch-caused — OBSERVED, not static.** LEG A (scope minus performance, xdist, junit node ids): HEAD **43F/1564P/54S in 71.9 s**; base leg (detached `1ebdf469f`, symlinked venv + PYTHONPATH guard): **44F/1563P/54S in 73.0 s**; node-set diff: **BOTH-RED=43 · HEAD-ONLY=0 · BASE-ONLY=1** → zero branch regressions, one base-only heal. Families: governor vision-fixture ×16 (ledger family, node-set rotated; test + `compare_tools.py` byte-identical base↔HEAD — ledger-hygiene debt: no node row); `test_api` ×6 LEDGERED 2026-10-19; `phase4_metrics_trigger_init` ×6 + `skill_service_init` ×3 (20260714 SQLite trap); `charter_render_capture` ×3 BASE-PROVEN UNLEDGERED (file created 2026-10-04/07, byte-identical base↔HEAD); `agents_api` ×2 UNLEDGERED-PRE-EXISTING (since base 708ee7a5); chokepoint ×2 + ui_prefs ×2 + enqueue ×1 + attestation ×1 LEDGERED; chart_tools_reuse ×1 (ledgered context-flake). `test_jsonb_migration` errors did NOT recur. **LEG B characterization (corrects the record):** the perf-matrix "producer-die consumer-fail" family is NOT load sensitivity — it is **xddist worker-isolation**: producers publish into module-level globals (`_CELL_RESULTS` etc.), so under `-n auto` consumers on other workers see empty/partial tables (3 skips + 3 KeyError); the 2026-09-07 row's "3/3 solo PASS" was the serial-correctness tell. The suite is **single-process-only**. LEG B TIMEOUT'd at 280 s (test phase done 275.9 s, junit never written) on the `[100x10k-variance]` cell (266.6 s solo, PASSES). **DISPATCHER RULING:** the 100x10k-variance cell is excluded at PACK level only (documented in-script; run solo; split-debt ledgered) alongside the quarantined 1x10k hang cell; the code-derived extra terms (all_six_cells_populated, nfr_summary_print — hard-assert all six cells, impossible while 1x10k is excluded) approved. **LEG C (architecture fix, test-code only, committed with evidence):** P-8 pack minimal diff (performance/ removed from scope + citation comment) + NEW `test/packs/regression_performance_test.sh` (serial-by-design, dual-layer 280/300, quarantine-aware -k with removal conditions, `.venv/bin/pytest` direct — also flagged: P-8's own `uv run` PATH hazard, out of mandate). Post-amendment pack run + final script in the quick-fix lane's report. Perf-leg numbers under host contention — load caveat recorded; a quiet-host re-run is optional debt, the branch does not touch performance code.

**P-8 TIMEOUT (inner 280s @ 98%) — TTQA + Test Architecture Fix in flight.** The partition hit its internal guard at 98% progress (1,575 P / 48 F markers / 64 S observed pre-kill; NO node ids — summary never printed). Root cause is STRUCTURAL, ledger-corroborated: the scope tail `tests/performance/test_message_api_cost.py` carries ~30s/cell producer tests plus the `[1x10k]` parametrize cell QUARANTINED 2026-10-19 as an UNINTERRUPTIBLE hang (external wall-kill only), and P-8 has no exclusion for it; corpus also grew ~+56% vs the 2026-09-07 baseline (1,072 → ~1,680). The cap was NOT raised. Completing pass = Leg A (scope minus performance, junitxml node ids, full ledger adjudication) + Leg B (performance solo with the ledger-documented `-k "not 1x10k"` quarantine guard) + Leg C (permanent split: new `regression_performance_test.sh` pack + minimal P-8 scope edit — test-code only, committed with this gate's evidence). Ledger-status pass from the timeout leg already resolves the formerly-HIGH-SIGNAL nodes into the ledger: `test_api` ×2 + `test_instance_ui_prefs_api` ×2 + `test_enqueue_shared` ×1 all LEDGERED 2026-10-19/09-06; `test_phase4_metrics_trigger_init` ×7 + `test_agents_api` ×2 = UNLEDGERED-PRE-EXISTING (20260714 class, first observed 2026-09-07 @ base 708ee7a5) — ledger-hygiene debt flagged.

**P-6 failure adjudication (12/12 quarantine-matched, 0 branch-caused):** maintenancer release-tag pin ×1 (v0.12.4-vs-0.25.7 drift, 2026-10-12 row); maintenancer prompt-convention ×3 (agents/maintenancer/memory.md missing — 2026-10-20 agent-restart row); project_manager prompt-composition ×2 (2026-09-07 row); `repository_claim_lane` ×6 (2026-10-19 rcc row's ledgered ×5↔6 batch-ordering variant). Branch-attribution proof: all 4 failing test files + their production surfaces (incl. `repositories/task/repository.py`, `pyproject.toml`, agents/ prompts) are untouched by the branch diff. Healed-green: models_split, phase4 `pause_instance_cascade` delegation pin (re-anchored to `cascade_to_root=True`), `test_paused_auto_resume_fallback` ×5 (AsyncMock class fix), KMS env-resolution ×5 (environment-conditioned — reddens only when `SYSTEM_ENCRYPTION_KEY` unset; it is SET in ambient env and the scrub does not unset it). **Baseline correction applied by the lane:** the PACKS.md P-6 row (2026-09-07) is stale AND self-inconsistent (1,848+7+40=1,895 ≠ stated 1,890 collected); the authoritative anchor is the 2026-10-14 RESULTS row (2,503P/16F/44S) — against it this run is −4F, 0 NEW. P-6 PACKS.md row refresh queued for maintenance.

**P-1 failure adjudication (13/13 quarantine-matched, 0 branch-caused):** families — `test_inner_soul_compaction` update-memory-md ×3 (2026-10-14 Cluster A + 2026-10-20 agent-restart row, base-executed); `test_knowledge_tools` explore model-kwarg ×2 (stale-vs-1a40bc56 since v0.16.0); `test_prompt_section_reference_integrity` bare-md ×3 + odd-fence ×2 (byte-identical at base per 2026-10-07/10-20 rows); `test_instance_tools` planner subtree registration ×2 (2026-10-17 row); `test_snapshot_tools` gate-flag drift ×1 (2026-10-20 row). Branch-innocence corroborated twice: gate 9's empty `daemon/tools/` footprint AND empty diff under `agents/` + `tests/unit/tools/`. The P-1 baseline row (0F @ 2026-09-23) is stale vs the latest-forward corpus — re-baseline flagged.

**P-7 failure adjudication (1 pre-existing, 0 branch-caused):** sole failure `test_terminal_reason_mirror_set_regression.py::test_no_new_terminal_reason_literals_without_mirror_set` — baseline/QUARANTINE row-100 family, signature ROTATED 2→3 literals (`+wedge_guard_terminated`, upstream commits 4442ed908/c832e5bb4, pre-branch). Branch exoneration is static-proof: all three literals exist at base `1ebdf469f` in files the branch never touched; `git log -S<literal> 1ebdf469f..b1ec0c0d7 -- daemon/` = zero occurrence-count change. **Premise correction (accepted):** the branch's own suite `test_question_pause_resume_asymmetry.py` is `test_q*` → NOT in P-7's [s–z] glob (0 of 40 nodes collected there); its in-partition home is P-6 `[m–r]` (in flight) and its standalone verdict is already 40/40 via the branch pack (§4b).

**P-9 failure adjudication (FAIL-by-baseline, 0 branch-caused):** 65F = (a) 41 baseline-matched (progressive_dispatch fresh-SQLite `20260714` DROP-CONSTRAINT trap ×18; memory_integration U9 family ×16; turn_reconciler rcc-2026-10-19 row ×3; innate_skills ×1; models error-codes count-literal ×1; migration_api macOS-path ×1; queue_routing MagicMock-await ×1); (b) 24 "new vs the 2026-09-07 baseline", every one excluded from branch causation by direct evidence — ×10 `SimpleNamespace has no attribute 'priority'` (token `priority` appears 0× in the branch diff), ×7 memory-stub/prompt-cache family (row 144 clusters), ×4 AsyncMock-compare watch enrichment, ×2 macOS-path FileNotFoundError, ×1 migration corpus-count pin (`105 == 96` fails; test last bumped 95→96 @ 56cc28b4e 2026-10-14; corpus is 105 at latest — the branch's migration delta is EMPTY), ×1 phase8 changelog naming gate (test added 2026-10-18); (c) 28 baseline nodes now GREEN (injection_api ×26 MagicMock-await class healed; llm_load_balance; skill_evolution flake). **All 7 question-path files in the i–q range passed in-partition** (question_api/dismiss/manager/pack_boot_rehydrate/tools/tools_validation/untested_paths). Wall 244 s vs baseline 21 s = concurrent-lane contention (load peaked 20.08 on 8 cores) — no timeout, no anomaly.


| Lane | Pack | Worker | Result | Counts | Runtime |
|------|------|--------|--------|--------|---------|
| A1 | `tests/manual/question_pause_strand_repro.py` | `36981081` | ✅ PASS exit 0 | 2× NO STRAND, 0× STRAND PRESENT | 8 s |
| A2 | `tests/test_question_dismiss.py` | `40911f9c` | ✅ PASS exit 0 | 15/15 | 2.8 s pytest / 8 s wall |
| A3 | `test/packs/question_validation_targeted_unit_test.sh` | `489fa570` | ✅ PASS exit 0 | 140/140 (10/10 files) | 58 s wall |
| A4 | `test/packs/question_pause_resume_asymmetry_unit_test.sh` (+ verbose node map) | `e04af7fb` | ✅ PASS / PASS exit 0 ×2 | 40/40 both invocations | 45.6 s / 43.9 s |
| A5 | adjacent-surfaces file set | `9b372f61` | ✅ PASS exit 0 (post-fix) | 643/643, 60 deselected; 1 test-side quick fix | 203 s as-found / 279.9 s post-fix |
| A6 | real-PG N4 probe (`ensemble_dev`) | `2e91499d` | ✅ PASS 6/6 | candidates=4 resumed=2 skipped_protected=2 errors=0 | 0.8 s harness + ~9 s arms (~16 min lane) |
| A7 | idempotency + config-wiring probes | `c3546bda` | ✅ PASS ×3 | probe1 PASS · probe2 4/4 · probe3 PASS | 6.8 s / ~3.4 s×4 / 3.6 s |
| B1 | P-2 `regression_unit_services` | `4166c10d` | ⚠️ FAIL-by-baseline, 0 NEW | 2904 P / 9 F (7 baseline + 2 quarantine-ledgered); drift 1,359→2,913 | 164.7 s pytest / 177 s wall |
| B2 | P-3 `regression_unit_smaller_subdirs_routers` | `72be0ad8` | ✅ PASS exit 0 | 1147 P / 0 F / 22 S (baseline 0F reproduced; drift 647→1169) | 143.4 s pytest / 156.3 s wall |
| B3 | P-9 `regression_top_level_i_q` | `557f9136` | ⚠️ FAIL-by-baseline, 0 NEW (branch-excluded by symbol test) | 3078 P / 65 F / 73 S (41 baseline-matched + 24 base-drift/healed-families); drift 2,503→3,216 | 244.2 s pytest / 257 s wall |
| B4 | P-11 `regression_job_queue` | `32064b4b` | ⚠️ FAIL-by-quarantine, 0 NEW | 2096 P / 16 F (16/16 ledger-matched) / 38 S; drift 1,911→2,150 | 161.1 s pytest / 175 s wall |
| B7 | P-7 `regression_unit_loose_s_z` | `9dc360cc` | ⚠️ FAIL-by-baseline (1 pre-existing, branch-exonerated) | 1943 P / 1 F / 11 S — 53 of 54 baseline F + both E healed; watchover ×47 cluster fully green (143 nodes); drift 1,037→1,955 | 54.3 s pytest / 60 s wall |
| B5 | P-1 `regression_unit_tools` | `bdc36398` | ⚠️ FAIL-by-quarantine, 0 NEW / 0 ROTATED | 3783 P / 13 F (13/13 ledger-matched) / 7 S; drift 2,840→3,783 | 88.8 s pytest / 94 s wall |
| B6 | P-6 `regression_unit_loose_m_r` | `bc28aa4a` | ⚠️ FAIL-by-quarantine, 0 NEW (branch suite GREEN in-partition) | 2564 P / 12 F (12/12 ledger-matched) / 44 S; drift 2,563→2,620; branch file: 0 failures in-partition | 95.3 s pytest / 101 s wall |
| B8 | P-8 `regression_top_level_a_h` | `1812169b` + completing `e5722f83` | ⚠️ FAIL-by-pre-existing, **0 branch-caused (base-leg A/B: head-only = 0)**; perf split landed | HEAD 43 F/1564 P vs base 44 F — both-red 43, head-only 0; perf scope split to new pack (serial-only, ruling applied) | 71.9 s HEAD / 73.0 s base |
| B9 | P-4 `regression_unit_loose_a_d` | `55e0ce77` | ⚠️ FAIL-by-ledger, 0 NEW (48/48 exact-count matched) | 2324 P / 27 F / 9 S / 21 E; drift 1,050→2,381 | 121.0 s pytest / 131 s wall |
| B10 | P-5 `regression_unit_loose_e_l` | `398a5a98` | ⚠️ FAIL-by-ledger, 0 NEW (13/13 family-matched) | 1647 P / 13 F; drift 1,116→1,660; old 11F baseline fully rotated away | 110.8 s pytest / 124 s wall |
| B11 | P-10 `regression_top_level_r_z_misc` | `f6fd2af5` + A/B `f299f5df` | ⚠️ FAIL-by-pre-existing: 25 ledger + 37 base-observed + 1 load-sensitive — **0 branch-caused** | 2772 P / 53 F / 44 S / 5 xf; drift 2,311→2,874; A/B: 37/37 matched, 0 healed-at-base | 125.3 s + 17.2 s A/B |
| B12 | P-12 `regression_integration_opencode_e2e` | `e6e4654a` + completing `3950b3ef` | ⚠️ FAIL-by-pre-existing, **0 branch-caused (node-level, 3 legs)**; stale row re-pointed; 3 replacement packs landed | 1074 P / 35 F / 4 E / 2 S of 1,115 (legs A1 17F + A2 14F + B 4F) | 114.7 + 106.1 + 19.7 s |
| C | boot smoke (8079) | `d0136c6e` | ✅ PASS 3/3 phases | candidates=1 released (wake_refused, fail-safe); kill-switch: 0 sweep lines + fixture untouched; cleanup exact (34 instances, 0 markers) | 22 s + 22 s + <5 s |

---

## 6. Final verdict

**ALL 9 GATES PASS → `fix/question-pause-resume-asymmetry` @ `b1ec0c0d7` is READY FOR MERGE + STAGING** (stage-only; the user's nonce ceremony owns promote).

| # | Gate | Verdict |
|---|------|---------|
| 1 | ORIGINAL SYMPTOM (must-pass) | ✅ PASS — exit 0, 2× NO STRAND, both shapes, exact transitions verbatim |
| 2 | Scenarios a–d | ✅ PASS — 19/19 unit nodes + real-process release leg; re-arm stratum unit-proven (disclosed) |
| 3 | Full regression suite (3a question packs 195/195 · 3b adjacent 643/643 · 3c partitions P-1..P-12 + successors) | ✅ PASS — **0 branch-caused failures** across ~29,300 executed cases; ~292 failures ALL ledgered / base-observed / symbol-exonerated |
| 4 | N4 real-PG predicate | ✅ PASS — 6/6 on ensemble_dev, SQL verbatim |
| 5 | Boot smoke (real daemon, 8079) | ✅ PASS — 3/3 phases, ordering proven, kill-switch both lanes, clean stop, cleanup exact |
| 6 | Sweeper behavior | ✅ PASS — idempotency (delta 0) + env→config→wiring 4/4 |
| 7 | Deliberate-pause protection | ✅ PASS — 7/7 tombstone nodes (answer + dismiss + sweeper sides) |
| 8 | Route table | ✅ PASS — dismiss→`dismiss_question`; 0 underscore endpoints (240 routes) |
| 9 | Hygiene | ✅ PASS — migration delta EMPTY; daemon/tools EMPTY; fencing CLEAN; live fence held throughout |

**Execution totals:** 16 worker lanes (recon, 7 wave-A, 12 partition lanes incl. 2 completing + 1 A/B + 2 quick-fix reuses). Gate packs: 15+140+80+643 cases + 2 repro shapes + 6 PG cases + 8 probe cases + 3 boot phases. Partitions: ~27,527 collected / ~26,900 passed / ~292 failed+errored — every failure adjudicated (ledgered baseline families; P-8/P-10 base-leg A/B: head-only failures = 0; P-12: 22 symbol-exonerated with 5 carrying executed base-parity via P-10's A/B on the identical seam). Quarantine discipline respected throughout (0 quarantined-node gate failures; 1 documented pack-level deselect family for the perf cells).

**Quick fixes applied (1, committed with this evidence):** `tests/unit/test_watchover_crash_recovery.py` — to_thread assertion positional→identity (+16 lines, single file, A/B-evidenced test-side staleness; production contract proven intact).

**Test-architecture maintenance landed (test-code only):** P-8 split (performance/ out, minimal diff) + NEW `regression_performance` pack (serial-only; quarantine-aware guards with removal conditions) + P-12 successors `regression_integration_a/_b` + `regression_e2e` (explicit file lists; dead upstream pack retired in PACKS.md).

### Disclosed limitations (none blocking)
1. **Gate 2(b) re-arm stratum** proven at unit level only (real-process leg proved detect+release+fail-safe; a checkpoint-carrying fixture would be needed for a real-process wake — `resumed>=1` is structurally unreachable for a checkpoint-less fixture by the documented fail-safe).
2. **P-12's 22 ledger-new exonerations** are symbol-level (producing-code diff/ancestry) + 2 solo-pass + cross-A/B for the shared vision family — an executed per-node base-parity leg was deliberately not run (read-only discipline on the shared repo).
3. **Perf 2× anchors** (`test_2x_baseline_anchor[100x150]/[100x400]`) fail base≡HEAD on a quiet host at 4–8× the v1 pins — **baseline drift on latest, branch-independent, base-proven**; routed as test-debt (re-baseline `BASELINE_2X` or investigate `aget` cost). Last demonstrable green: the ledger's undated "3/3 solo PASS".
4. **Wiring nuance:** the periodic sweep service's first tick pre-empts the api.py boot pass (~3 s) — boot-time recovery works, but the boot-pass line reads `candidates=0` when the service already swept.
5. **F1:** tip carries a one-line comment swap in `instance_lifecycle.py` beyond the stated freeze point — behavior-neutral, disclosed; all verdicts are tip verdicts.

### Follow-up debts (for the next commissions / maintenance pass — none block this merge)
- QUARANTINE.md hygiene: ~15 unledgered pre-existing families enumerated in PACKS.md's gate maintenance ledger (vision-facade cluster ×50 across 3 partitions, maintenancer ×9, team-members ×4, charter ×3, etc.) + `spawn_limit` message-text refresh.
- Stale PACKS.md baseline rows + stale in-script header comments (P-1/P-2/P-3/P-5/P-6/P-8/P-10) — flagged, do not adjudicate future gates against them.
- Perf split-debt (per-cell packs / slow-tier for 1x10k + 100x10k-variance) + the 2×-anchor re-baseline commission.
- F3/F4 regression tests for the branch suite (idempotency — scoped to the task-less class or modeling ResumeTurn; kill-switch/threshold env wiring) — currently covered by this gate's ad-hoc probes only.
- Infra: `ensemble_dev_app` dev credential repaired this gate (was broken host-wide); **config precedence fix owed** — ambient `PORT` beats `DAEMON_PORT` via config.yaml (fence gap; on a host with 9797 free an ambient leak would bind prod's port).
- Boot-pass/boot-sweep double-log cosmetic; `wc_wake_pure_hang` httpx→langgraph-ImportError rotation needs a ledger refresh.

### Fences (held throughout, verified at every lane)
Live daemon 9797 / `ensemble_prod` / 10.44.0.2 NEVER contacted; port 8088 never touched; all test daemons on 8079 only, freed after; ensemble_dev writes confined to `qprasym_gate4_`-prefixed rows (deleted; final instance count back to the pre-existing 34); no live-shaped HOME paths; 16-var env scrub (+`PORT`/`HOST` after the fence-gap find) on every invocation.

