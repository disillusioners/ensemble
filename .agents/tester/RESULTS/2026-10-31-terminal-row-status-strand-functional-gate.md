# FUNCTIONAL GATE — terminal-row status strand fix (9f0ed4f5 incident family)

- **Date:** 2026-10-31
- **Branch:** `fix/terminal-row-status-strand` @ HEAD `c2484bc16269c104a142fd25b6fb1d88b0b1c449` (prep-reported short form `c248bc16`), clean
- **Base (A/B):** `61574cc8e3c134690a1a669d4c7b2c00090f4203` (ancestor, rc=0)
- **Lane worktree:** `/home/nea/ensemble-src-wt-terminal-row-status-strand` (fenced .env: `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev`, `DAEMON_PORT=8079`; NO `.venv`)
- **Gate owner:** tester (test leader); 8 workers (1 prep infra + 7 gates), 0 re-dispatches, 0 losses
- **Fix under test:** T1 `instance_messaging.py:3852-3889` turn-start terminal→RUNNING via `manager._revive_terminal_instance` (PAUSED-exempt, fail-open+logged, no revive-budget/attestation reset) · T2 `child_reports.py:2958-2995` hold writes WAITING_CHILDREN only on terminal rows (`_TERMINAL_REARM_STATUSES=(COMPLETED,ERROR,FAILED)`, TERMINATED excluded), heal hand-off `posture=None` · T3 `child_reports.py:3277-3353` terminal-aware idempotency-guard exception. Files touched: `daemon/manager.py`, `daemon/services/child_reports.py`, `daemon/services/instance_messaging.py`, `daemon/services/report_integrity_guard.py`.

## VERDICT: ✅ PASS-WITH-NOTES — GO for giter finalize (commit → merge --no-ff → bump v0.28.2 → tag → stage → push; NO promote — user ceremony owns the flip)

All 7 gates PASS. Notes are non-blocking coverage/method disclosures (§Notes), each with a one-pin follow-up for the owner lane.

## Per-gate results

### Gate A — original-symptom proof: PASS
Harness drives the REAL chain in-process: `MessageProcessingPipeline.execute` → real claim → real `ExecutionGateService` → real funnel `manager._process_message_with_tracking` → fake LangGraph (sole LLM seam; START polled inside `astream` = strictly after reactivation) → real stage-4 COMPLETED → real stage-5.5 RUNNING→WAITING_CHILDREN bus → real `_process_child_completion_and_notify_parent` (real ChildReportsService + pre-stamp mission-live hold). Real SQLite StaticPool + real instance/message-queue repos + real bus + PENDING watcher. Harness byte-identical both legs (sha256 `f83d72ff4a6b617810f836bc6079f124613a26fb64980732aee5fd6a9137e42a`), identical command, 4 runs/leg (1 + 3 stability), 5.8–8.1s wall each.

| Leg | PRE | START | POST | versions | Result |
|---|---|---|---|---|---|
| BASE `61574cc8e` | completed | **completed** | **completed** | 1→1 (ZERO writes) | defect REPRODUCES (the hole: still completed mid-turn; pre-stamp hold bare-returns with no positive write) |
| HEAD `c2484bc16` | completed | **running** | **waiting_children** | 1→2 | defect CLOSED |

Evidence strings stable across all repeats: `TRS_EVIDENCE PRE=completed START=completed POST=completed` (base) vs `PRE=completed START=running POST=waiting_children` (head). Mocked seams (4 groups, nothing status-bearing): fake LangGraph graph; `_has_checkpoint`/`_maybe_compact_context`; `manager._checkpointer=None` (transcript read takes `messages=[]`); manager external sinks only (live-hub SSE, injection slot, question-pause deferral, watchover-terminate, skill-injection, skill-context cache, source dispatcher=None). Leg detection structural (funnel source contains the chokepoint call AND `_TERMINAL_REARM_STATUSES` exists) — never path-based. NOTE: this arc discriminates T1+T2; T3 is not the discriminator on a live tree (pre-stamp hold precedes it) — T3 is covered by Gate B unit pins + mutation M-T3. Evidence: `/tmp/trs-gate/gate-a/` (REPORT.md, master harness, per-leg logs ×4, durable `trs-evidence-{base,fix}.log`).

### Gate B — pins + mutation meaningfulness: PASS
- Green at HEAD: `tests/unit/services/test_terminal_row_status_strand.py` **16P/0F** (3.34s).
- Pin file absent at base proven (`git cat-file -e 61574cc8e:<path>` rc=128 fatal).
- **Base-code leg** (private worktree, 4 daemon files reverted to base, pin file kept): **9F/7P RED** with exact strand signatures (rows staying `completed`; `idempotency_skip` vs `deferred_waiting_children`; stale heal posture; no fail-open warning). Restored with `git diff` EMPTY + sha match + porcelain 0.
- **Mutation legs** (private worktree `-wt-trs-b`, sha256 before→planted→restored each, all reverted-clean):

| Plant | RED | Nodes |
|---|---|---|
| M-T1 reactivation unreachable | **4** ×3 | chokepoint_flips…, process_report_delivery_turn…, full_arc…, chokepoint_fail_open… |
| M-T2 hold re-arm write removed | **2** ×3 | hold_re_arms_terminal_root…, hold_heal_rereads_live_row |
| M-T3 unconditional skip restored | **2** ×3 | guard_terminal_with_live_children…, guard_error_row_with_live_children… |
| M-1 stale posture `posture=_held_row.status` | **2** | hold_heal_rereads_live_row, hold_non_rearmed_rereads_live_row |
| M-2 TERMINATED re-added to tuple | **1** | hold_terminated_root_stays_terminated |
| A-1 fail-open try/except removed | **1** | chokepoint_fail_open… (`RuntimeError: revive-boom` propagated; sentinel regex no longer matched) |

Determinism: T1/T2/T3 RED sets identical across 3 runs each. Zero failed plants. Post-restore sanity 16P/0F @ HEAD sha. Evidence: `/tmp/trs-gate/gate-b/`.

### Gate C — net-new reds across touched + adjacent packs: PASS (all three bundles)
Identical commands both legs (differing only in worktree); FAILED sets extracted, sorted, diffed; 3× isolation on any red; import guards pasted per leg.

| File | HEAD | BASE | FAILED delta |
|---|---|---|---|
| tests/unit/services/test_terminal_row_status_strand.py | 16P/0F | ABSENT (new; not overlaid) | n/a — green |
| tests/unit/services/test_child_reports.py | 50P/0F | 48P/0F | ∅ (+2 HEAD-only negative-leg pins, both pass) |
| tests/unit/test_report_delivery_recovery_service.py | 42P/0F | 42P/0F | ∅ |
| tests/unit/services/test_waiting_children_watchdog.py | 51P/0F | 51P/0F | ∅ |
| tests/integration/test_report_integrity_repro.py | 5P/0F | 5P/0F | ∅ (`--override-ini="addopts="` BOTH legs — integration marker) |
| tests/repositories/test_turn_reconciler.py | 24P/**3F** | 22P/**3F** | **∅ — byte-identical sets** |
| tests/job_queue/test_watcher_loss_terminal_strand.py | 26P/0F | 26P/0F | ∅ |
| tests/job_queue/test_watcher_loss_terminal_strand_gap.py | 13P/0F | 13P/0F | ∅ |
| tests/unit/test_resume_router_report_dup.py | 16P/0F | 16P/0F | ∅ |
| tests/unit/services/test_question_pause_completion_guard.py | 8P/0F | 8P/0F | ∅ |

**Totals: HEAD 251P/3F vs BASE 229P/3F → NET-NEW REDS = 0.** The 3 turn_reconciler failures are the ledgered base-attributed family (rcc 2026-10-19 row), PROVEN base-red here with byte-identical node IDs + signatures, deterministic 18/18 isolated rc=1: `test_missing_task_sets_admission_done_orphan_reason` (:566 `assert 'active' == 'done'`), `test_missing_task_deletes_lock` (:634 `assert 0 >= 1`), `test_hard_deleted_task_watcher_deleted` (:1109). Evidence: `/tmp/trs-gate/c{1,2,3}/`.

### Gate D — real PostgreSQL legs: PASS
PG 16.15, psycopg3 3.3.4, serial, `--override-ini="addopts=" -m postgres`, fresh throwaway DB per leg, throwaway LOGIN role, `PG_TEST_ALLOWED_DBS` fence. Isolation PROVEN per leg (`current_database()` = throwaway, `is_prod=f`; ambient `POSTGRES_HOST=10.44.0.2` present in worker env and scrubbed per-variable on every run). **`ensemble_prod` never addressed**; live daemon untouched; teardown proven (5 DBs + role dropped, catalog counts 0, creds shredded).
- **Mirror-interplay pins on real PG:** `test_running_root_keeps_jobitem_active` + `test_waiting_children_root_keeps_jobitem_active` (tests/repositories/test_turn_reconciler.py:1327/:1375 — NEW at HEAD, SQLite-bound in-repo) were driven onto real PG by an external driver importing the test module's own seed helpers/read-assertions verbatim (`/tmp/trs-gate/trs_pg_pin_driver.py`): HEAD **3/3 PASS** (running-root→active+NULL; waiting_children-root→active+NULL; control completed-root→done+`completed`); BASE **3/3 PASS** — explained: `_LIVE_INSTANCE_NOT_EXISTS_GUARD` (task/repository.py:338) predates the lane; T1/T2/T3 sit upstream of it. Lane introduces no PG-visible mirror delta.
- **PG seam subset** (7 files, 108 collected/leg, seam-symbol selected; no dedicated PG reconciler file exists): **72P/8F/26E/2xf IDENTICAL both legs; FAILED sets 39 vs 39, `diff` empty** → zero net-new, zero fixed. All 39 map to ledgered families (TDD-red `_sweep_orphan_watchers` Phase-1 pin; `_MinimalManagerProxy` fixture drift vs `_migrate_overloaded_image_refs_rows` @ manager.py:9003 incl. 24 setup ERRORs; TestLaneKillSwitchesPG lane-map ×2); deterministic in isolation. Runtimes 187s/183s total, each file under its own `timeout 300`. Evidence: `/tmp/trs-gate/d/`.

### Gate E — interaction matrix no-regression: PASS
19 targeted pins green (runtimes 0.94–2.47s), full 16-pin suite 2.65s. Every row pinned-and-green or UNPINNED-but-code-verified; zero pinned-and-RED.

| Row | Pins | Citation | Verdict |
|---|---|---|---|
| E1 hold no-churn non-terminal | hold_running_root_no_write_no_churn · chokepoint_non_terminal_no_write · 2 child_reports negative legs | child_reports.py:2965 gate → :2966-2970; manager.py:10503-10504 | ✅ pinned |
| E2 healer lane via posture=None | hold_heal_rereads_live_row · hold_non_rearmed_rereads_live_row · downgrade hand-off · zombie ×2 | :2989-2991 pass None; :2458-2499 re-read → WAITING_CHILDREN→heal_instance :2494-2497 / RUNNING→heal_running_zombie :2489-2493 | ✅ pinned (BOTH selections) |
| E3 PAUSED exemption ×3 | T1: chokepoint_paused_exempt (+ healer paused skip) | T1 manager.py:10493-10504; T2 :2965+:84-88; T3 :3290-3293 + claim gate task/repository.py:3022-3024 | ✅ T1 pinned; T2/T3 UNPINNED-code-verified |
| E4 revive budget + attestation | chokepoint_preserves_attestation_counters · chokepoint_fail_open… | budget increment sole site manager.py:4422 (agent-tool lanes only; awk-grep over full T1 path = ZERO counter calls); write touches status/last_activity/version only :10525-10528; attestation reset lives in the enqueue-time lane :2615-2616 | ✅ attestation pinned; budget UNPINNED-code-verified |
| E5 TERMINATED stays terminated | hold_terminated_root_stays_terminated · guard_all_terminal_keeps_idempotency_skip | tuple :84-88 (rationale :80-83); claim gate binds status IN (paused,terminated) :3202-3203 ⇒ T1 structurally unreachable for TERMINATED | ✅ pinned |

Evidence: `/tmp/trs-gate/e/` (EVIDENCE.md + E0–E5.log).

### Gate F — fences/discipline: PASS (enforced per-dispatch, evidenced per-worker)
- **Import guards pasted per leg by every worker** — `daemon.__file__` resolved inside each leg worktree every time (the `.pth` is a static path to `/home/nea/ensemble-src`; PYTHONPATH prefix is load-bearing).
- **Per-variable env scrub verbatim everywhere**; `env -u POSTGRES_*` never used. Ambient `POSTGRES_HOST=10.44.0.2` detected in worker env and neutralized on every run (Gate D documented the scrub).
- **No live contact:** no run touched :9797 or `ensemble_prod`; no daemon started; no dev.sh; :8088 untouched; Gate D isolation proven at catalog level; others in-process/no-daemon.
- **FAKE_HOME:** Gate A ran under `HOME=/tmp/trs-gate/gate-a/fake-home` both legs.
- **Tree hygiene:** primary + shared base worktrees 0 porcelain before AND after every worker; all mutations confined to private worktrees with sha256 before/after + `git diff` empty restore proof; **zero commits/merges/stage/tags by any gate worker** (giter owns finalize).
- **Dual-layer timeout:** every pytest invocation wrapped `timeout 300`; longest observed run 187s (PG leg total, per-file wrapped); no TIMEOUTs.
- **Paste-execute:** Gate E's greps executed and outputs cited before asserting; one misnamed node invocation corrected in-flight and explicitly excluded from evidence.
- **Certified-interpreter pattern:** `cd <LEG> && PYTHONPATH=<LEG> /home/nea/ensemble-src/.venv/bin/python` (3.13.15 / pytest 9.0.2) everywhere; never bare `python`, never `uv run`.

## Notes (non-blocking) + follow-ups for the owner lane
1. **D-method note:** the two mirror-interplay pins are bound to the file-local in-memory SQLite fixture in-repo; real-PG exercise used an external driver importing the module's own seed helpers (verbatim pin bodies on a real-PG engine). Sound and reproducible, but **follow-up: bind the pins to the PG fixture natively** so the postgres shard covers them.
2. **E unpinned rows (one-pin fixes):** E3/T2 hold-branch PAUSED variant; E3/T3 idempotency-guard PAUSED drive (double-protected by the claim gate today); E4 revive-budget counter pin (increment site manager.py:4422 proven unreachable from T1; attestation half IS pinned).
3. **B coverage notes:** 6 of 16 pins are negative-contract pins that legitimately survive this 6-plant set; `guard_count_failure_fails_to_skip` still needs a "count-failure re-arms" mutation to be mutation-proven; `chokepoint_preserves_attestation_counters` is vacuously satisfied under M-T1 (independently covered by E4's mechanical citation).
4. **A arc note:** the symptom harness discriminates T1+T2 on a live tree; T3's exception is reached only on a still-terminal-row shape — covered by unit pins + M-T3, not by the funnel arc (disclosed, by design).
5. **Prep recipe defect (neutralized):** RECIPE.md §46–50 wrongly listed `test_report_delivery_recovery_service.py` + `test_resume_router_report_dup.py` as HEAD-only; both are present at base (§43/54/64 correct). Every Gate C worker re-derived presence with `test -f` per leg instead of trusting the recipe. Two real path corrections stand: both files live under `tests/unit/`, not `tests/unit/services/`; the second `test_turn_reconciler` is `tests/repositories/`.
6. **Base-attributed families re-adjudicated at this pair (61574cc8e ↔ c2484bc16):** turn_reconciler ×3 (rcc row — unchanged) and the PG 39-node set (ledgered families; QUARANTINE addendum row added). Zero new members; zero un-quarantine candidates.

## Scratch inventory (gate-owned, disposable; kept for staged-verification mirroring)
`/home/nea/ensemble-src-wt-trs-base` (shared base leg, clean) · `-wt-trs-a-head` / `-wt-trs-a-base` (Gate A private, untracked harness only) · `-wt-trs-b` (Gate B private, restored clean @ HEAD sha) · `/tmp/trs-gate/` (RECIPE.md + all gate evidence). None block giter finalize; remove after the staged-verification pass mirrors the commands.

## Exact commands (for staged-verification mirroring)
Canonical A/B template (both legs identical except `<LEG>` = HEAD worktree vs base worktree; from `/tmp/trs-gate/RECIPE.md`):
```
cd <LEG> && env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u POSTGRES_URL -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE PYTHONPATH=<LEG> timeout 300 /home/nea/ensemble-src/.venv/bin/python -m pytest <PATHS> -q --tb=short -p no:randomly -p no:cacheprovider
```
Import guard per leg: `cd <LEG> && PYTHONPATH=<LEG> /home/nea/ensemble-src/.venv/bin/python -c "import daemon; print(daemon.__file__)"`
Integration file variant: append `--override-ini="addopts="` (both legs). PG variant: throwaway role+DB, `PG_TEST_HOST/PORT/DB/USER/PASSWORD` + `PG_TEST_ALLOWED_DBS=<db>`, serial, `--override-ini="addopts=" -m postgres`.
Gate A harness command (verbatim in §A; adds `HOME=<fake-home>` and `-rP`).

## ensure.md (scoped Core)
- Critical #1 no regressions in changed packs: **PASS** (all in-scope packs green or byte-identical-base; net-new = 0).
- Critical #2/#3 concurrency pack: not in this change set's blast radius (no concurrency-surface files touched: instance_messaging/child_reports/manager/report_integrity_guard only) — not run, scoped out per blast radius.
- Critical #4 dev.sh flag: unchanged by branch (no dev.sh touch in `git diff --name-only`); static check n/a to this gate.
Release Gate: NOT triggered (scoped daemon-service fix; no architecture change).
