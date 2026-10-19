# Functional Verification — question-pack boot-rehydrate fix (qpk-rehydrate)

- **Date**: 2026-10-19
- **Verdict**: 🟢 **PASS — all 3 gates green, 0 new failures vs parent**
- **Worktree**: `/home/nea/ensemble-src-wt-qpk-rehydrate` (branch `fix/question-pack-boot-rehydrate`)
- **HEAD**: `d1377f5963ee08286f6a592ec784f48502a80533` | **Parent base**: `333b2c22553338acf685de00ac29630fe2d3bfee`
- **Diff 333b2c225..d1377f596**: `daemon/api.py`, `daemon/manager.py`, `tests/test_question_pack_boot_rehydrate.py` (test-side = only the new test file; no conftest changes)
- **Workers**: 6 (w1 `5c54c92f` G1 · w2 `20ba6e11` G2 · w3 `cb1509a8` inventory · w4 `688c1621` sweep A · w5 `7a018e77` sweep B · w6 `c7bec6d5` sweep C). 0 re-dispatches, 0 timeouts.
- **Headline at HEAD**: **433 passed / 0 failed / 0 timeouts** (7 fix-target + 176 sweep A + 151 sweep B + 99 concurrency pack).
- **Headline at parent**: 6 failed / 1 passed (all behavioral) — mutation-style regression pin proven.

---

## Gate 1 — BEFORE/AFTER proof — ✅ PASS (worker `5c54c92f`, skill test-pack-execution)

**HEAD run** (cwd worktree, `.env` sourced + `ENSEMBLE_SELF_ENV=dev`, fence `DB=ensemble_dev HOST=127.0.0.1 PORT=5432`):
```
timeout 300 ./.venv/bin/pytest tests/test_question_pack_boot_rehydrate.py -v --tb=short -p no:cacheprovider
→ 7 passed, 1 warning in 4.39s   (exit 0)
```
Per-test: helper-exists / restores-pack-with-original-id / silently-swallows-unwired / loud-signal-on-missing-wiring / summary-log-line / setup_worker_pool-wires-then-restores / lifespan-structural-ordering — all PASSED.

**Parent run** (isolated: `git archive 333b2c225 | tar -x -C $(mktemp -d /tmp/qpk-parent.XXXXXX)`, HEAD test file + `.env` copied in, `PYTHONPATH=$TMP:$SITE` with asserted `daemon.__file__ = $TMP/daemon/__init__.py`):
```
→ 6 failed, 1 passed, 1 warning in 5.44s   (exit 1)
```
All 6 failures **behavioral, not environmental**:
- ×5 `ImportError: cannot import name '_boot_rehydrate_question_packs' from 'daemon.api'` (helper absent in parent)
- ×1 `AssertionError: Expected EXACTLY ONE _boot_rehydrate_question_packs(manager) call in daemon/api.py lifespan, found 0.`
- The 1 parent-pass (`test_rehydrate_method_silently_swallows_when_task_repo_unwired`) is AST/inspect-based by design.

Note: developer's claim was "4 failed/1 passed at parent" — direction correct, count imprecise; authoritative independent count is **6F/1P**. Cleanup verified (no `/tmp/qpk-parent.*` leftovers; worktree `git status --porcelain` empty before and after).

## Gate 2 — restart-simulation on real PG `ensemble_dev` — ✅ PASS (worker `20ba6e11`, skill integration-test)

Harness: mktemp-fenced (`/tmp/qpk-sim.*` + EXIT trap), `timeout 280` outer + `signal.alarm(240)` inner, total wall **8.159s**. Double DB fence (bash `. ./.env` + Python re-derive via `EnsembleConfig.get_postgres_url()`, asserted host=127.0.0.1 port=5432 db=ensemble_dev). Ambient `POSTGRES_HOST=10.44.0.2 / ensemble_prod` was present in the environment and **overridden by the fence on every run** — live DB never touched. Live prod daemon observed on 9797 (PID 131667) — noted, untouched.

**Seed (durable only, RAM empty)** — instance `inst-qpk-sim-74227ae5-04d63154`, task `paused / suspension=awaiting_answer`, pack ONLY in `instances.metadata['question_pack_payload']`; pre-boot check `QuestionManager._packs[iid] = None`.

**Real boot sequence** (exact lifespan order: `manager.setup_worker_pool()` facade at `daemon/manager.py:8675` → `await _boot_rehydrate_question_packs(manager)` at `daemon/api.py:725`):
```
INFO daemon.services.question_manager: QuestionManager.rehydrate_from_payloads: restored 1 pending question pack(s) from instance_metadata
INFO daemon: question-packs rehydrated: 1 (candidates=2)
```
- **(a) Original id restored**: `restored_pack_id = 7047a5a5-6c4f-48b6-a2d8-9aaa97ae2354 == seeded_pack_id` (no re-mint). `candidates=2` = our seed + a first-run orphan (harness dev artifact) — both correctly counted, both cleaned afterwards.
- **(b) Answerable**: pack queryable in RAM post-boot (pending question for the instance).

**Answer routing post-boot** — `POST /api/instances/{id}/answer` (route `daemon/routers/instances.py:1063` → shared impl `daemon/routers/answer_helper.py:551`; payload `{"answers": {"q-original": "alpha"}, "question_pack_id": "7047a5a5-…", "resume_message": null}`):
- **HTTP 200** (NOT 410 QUESTION_PACK_LOST, NOT 400 QUESTION_PACK_MISMATCH); body `status="answered"`, `pack_id` preserved through CAS, `resume_route="answer_gate_existing_turn"` (real T3 answer-gate flow).
- Task transition: `paused/awaiting_answer → pending/suspension=None` (resume_cascade_db_sync).
- Metadata shadow cleared (`clear_question_pack_metadata`); RAM pack `status=answered`.

**Cleanup**: targeted deletes by seeded ids; cross-table scan (`messages`, `job_queue`, `events`, `message_metadata`) → 0 leftover rows; final state `0 rows LIKE 'inst-qpk-sim-%'`, 0 stray paused rows.

**Methodology caveat (disclosed)**: pool construction / thread fan-out / `StaleTaskRecovery.start()` were stubbed at the `PoolOrchestrator.setup` seam; the `_task_repo = TaskRepository(engine=manager._engine)` wiring — the exact dependency whose absence caused the original `AttributeError` — runs INSIDE the real facade. Rationale: avoid spawning live workers against the shared dev DB. `initialize()` itself was not separately driven; full lifespan ordering is pinned independently by Gate 1's `test_lifespan_structural_ordering_contract`.

## Gate 3 — targeted regression sweep — ✅ PASS (workers `cb1509a8`/`688c1621`/`7a018e77`/`c7bec6d5`)

Inventory: 111 adjacent files matched; scoped to question/answer/pack + pause/resume/boot/messaging + ensure.md concurrency pack. E2E/integration-marked files excluded (need real daemon `./dev.sh` — not warranted for this scoped verification; listed in inventory worker output for a future pack if wanted).

| Pack | Files | Result | Runtime |
|---|---|---|---|
| `qpk_sweep_a_question` (ad-hoc) | 14 question/answer/pack-named | ✅ **176 passed** / 0 failed | 7.78s |
| `qpk_sweep_b_pausresboot` (ad-hoc) | 13 pause/resume/boot/messaging | ✅ **151 passed** / 0 failed | 15.22s |
| `concurrency_atomic_unit_test` (registered, ensure.md Core #2/#3) | `test/packs/concurrency_atomic_unit_test.sh` | ✅ **99P / 0F / 74S** — baseline-exact | 75.86s |
| fix-target (Gate 1 HEAD run) | `tests/test_question_pack_boot_rehydrate.py` | ✅ 7 passed | 4.39s |

Zero failures anywhere at HEAD → NEW-vs-parent classification never triggered. Sweep A summary line verbatim: `176 passed, 8 warnings in 7.78s`; Sweep B: `151 passed, 8 warnings in 15.22s`; Sweep C: `99 passed, 74 skipped, 58 warnings in 75.86s (0:01:15)`.

Path correction: `test_discard_on_startup_dependency_bus_race.py` lives at `tests/unit/services/` (both the inventory table and the dispatch initially omitted `services/`); worker verified real location and ran the intended file (14 tests green).

## ensure.md status (Core, blast-radius scoped)

- Critical #1 no regressions in scoped packs: ✅ (all packs above PASS)
- Critical #2 deadlock/concurrency (`concurrency_atomic_unit_test`): ✅ baseline-exact
- Critical #3 no sync DB calls on event loop (same pack, thread-identity): ✅
- Critical #4 `dev.sh --timeout-graceful-shutdown 10`: ✅ (present, `dev.sh:142`)
- Important #1 (await-grep for 3 historic async converts): not separately grepped — out of this diff's blast radius (no async-signature conversions in the 3-file diff); thread-identity coverage green
- Important #2 (original deadlock scenario): ✅ via pack C
- Release Gate: **not run — not warranted** (isolated fix verification, not a release/architecture change)
- Contradictions / Improvement Notices: none (all validations ran pack-mapped with dual-layer timeouts)

## Caveats & observations

1. G2 seam stub disclosure (above) — load-bearing wiring real, pool internals stubbed; protective on shared dev DB.
2. Dev's parent-count claim (4F/1P) corrected to measured 6F/1P — same direction, stronger proof.
3. `/bin/sh` fence hazard re-confirmed (sweep C first attempt): `source` is a no-op under `/bin/sh`, so the fence ECHOED ambient poison values (`ensemble_prod`/10.44.0.2) before POSIX `. ./.env` re-fence. No harm — matches the known occ#6 env-unsourced class. **All future fences: use POSIX `.` sourcing.**
4. SQLAlchemy named-param colon × PG `::jsonb` cast collision in seed scripts → use `CAST(:value AS jsonb)` (recorded in harness; see LESSONS).
5. PACKS.md registration drift: `tests/test_question_pack_boot_rehydrate.py` was unregistered → registered via this commission's PACKS.md block (newest-first, keep-both convention).

## Landmine compliance

No run pointed at `ensemble_prod` or 10.44.0.2 (fence asserted per worker, ambient poison caught and overridden); `/home/nea/agents-ensemble` never touched; all scratch under `mktemp /tmp/qpk-*` with escape-guarded cleanup; no process kills (live 9797 daemon observed untouched); SQLite fresh-boot migration landmine never hit (all runs on PG or mocked fixtures); no repo modifications, no commits (verification-only — nothing to commit).

## Overall

| Gate | Verdict |
|---|---|
| 1 BEFORE/AFTER proof | ✅ PASS (7/7 @HEAD; 6F/1P behavioral @parent) |
| 2 restart-simulation | ✅ PASS (original-id restore; answerable; answer POST 200, resume, no 410/400) |
| 3 regression sweep | ✅ PASS (433 green at HEAD, 0 failures, 0 new vs parent; concurrency baseline-exact) |
| **Overall** | 🟢 **VERIFIED — fix is functionally proven; no regressions detected** |
