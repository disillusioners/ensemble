# wi-b97ec302 — Sources-Regression Fix: Phase-5 Closure Gate

- **Date:** 2026-11-07
- **Branch:** `fix/wi-b97ec302-sources-regression`, tip `a59a18cd5`, base `6b571771d`
- **Gate workers:** `b38553d3` (boot legs G1/G2/G5), `19ecf925` (pack legs G3/G4)
- **Verdict: GO — G1-PASS (both directions) · G2-PASS · G3-PASS (mutation-meaningful) · G4-PASS (net-new ∅) · G5-PASS.** No blockers. `do NOT push/merge/stage` was honored — giter owns the lane close; this file + the PACKS.md entry are the tester's uncommitted artifacts for the sweep.
- **Skill attribution:** boot leg ran no injected skill (DIY from `LESSONS/2026-11-07-scratch-boot-repro-harness-recipe.md` + the fix diff's own contract); pack leg ran `test-pack-execution`. No skill_feedback recorded by the boot worker (no skill consumed); the recipe lesson file carries the durable harness knowledge.

## G1 — ORIGINAL SYMPTOM, BOTH DIRECTIONS: **PASS** (decisive leg)

Fixture (seeded per recipe; >120 disabled one-shot scheduler rows with NEWER `created_at`, discord OLDEST, telegram/slack stopped, one separate stale `status='running'` non-autostart row):

| Leg | Auto-start | Creation lines | LIST (default) | Stale-running row |
|---|---|---|---|---|
| FIX `a59a18cd5` | `Scheduled auto-start for 1 adapters (delayed 60.0s)` + `Creating adapter for: my-discord-bot` → `Registered adapter: my-discord-bot` (boot log :237-246) | telegram/slack ABSENT (rc=1) — historical skip preserved | **124 rows, total=124**, all chat/stale rows + 120 scheduler present | `Reconciled 1 stale running source rows to stopped` — my-stale-telegram-bot flipped; discord SPARED (ends `error` from the dummy-token gateway failure — the "started rows untouched" proof) |
| BASE `6b571771d` | `Scheduled auto-start for 0 adapters` | ABSENT (rc=1) | **exactly 100 rows, all scheduler, 0 chat, NO `total` key** | stays `running` (no sweep) |

The base leg reproduces the original live failure byte-for-behavior → the gate is discriminating, not vacuous. Tolerable per spec: `Discord Gateway failed: Improper token has been passed.` (dummy credential; we assert scheduling/creation/reconciliation, not gateway success).

## G2 — LIST API CONTRACT: **PASS**

- `limit=50` walk: 3 pages (50/50/24), disjoint, union == default id set, `created_at DESC` preserved, **total=124 on every page** (untruncated).
- `limit=0` → 422; `limit=-1` → 422; `offset=-1` → 422 (FastAPI `greater_than_equal`).
- `limit=5000` → 200 (clamp, not reject). **Disclosure:** with 124 seeded rows the observable value is min(1000,124)=124 — the clamp-vs-422 discriminator is the API-level proof; the exact 1000-ceiling behavior is asserted in the 14-node pin pack (`test_sources_pagination_regression_pin.py`), which passed green (G3). Combined, the ceiling is covered.
- `response.sources` key intact (FE backward compat).

## G3 — PIN PACK + MUTATION: **PASS (mutation-meaningful)**

- Pack: `sources_pagination_regression_pin_unit_test`, 14 nodes, wrapper `test/packs/sources_pagination_regression_pin_unit_test.sh` (hermetic SQLite — no daemon, no PG, no ports). Registered on the branch at `a59a18cd5`.
- **G3a green:** 14 passed, exit 0, 46.40s (`03-g3a-green.log`).
- **G3b mutation re-plant** (byte-faithful restore of the old `list_source_configs()` no-arg call in `SourceRegistry.start_all`, single site — diff in `04a-g3b-mutation-diff.txt`): **4 RED / 10 GREEN**, exact documented ids:
  - `TestBootLoaderPaging::test_boot_loader_schedules_oldest_discord_row`
  - `TestStaleRunningReconciliation::test_stale_running_rows_flip_to_stopped`
  - `TestStaleRunningReconciliation::test_scheduled_row_is_spared_running_status`
  - `TestStaleRunningReconciliation::test_sweep_failure_never_blocks_boot`
- The mutation FAILED-set digest **matched the lane's recorded** `7360f63ec343a04d884906515e3852050d8f959fc36d83b9691823a2fbebdfdf` (`raw-FAILED-lines-sorted-nl`).
- **Restore-clean proof:** mutant clone discarded; clean fix clone re-run 14 passed / exit 0; fix clone porcelain clean (mutation never touched shared tree).

## G4 — SCOPED NET-NEW: **PASS — net-new ∅**

Identical command sets at both revs, each `timeout 300`, `-q --tb=short -rfE -p no:randomly`, per-variable env scrub:

| Run | Scope | Nodes / result (both revs) |
|---|---|---|
| A | changed test files (`test_sources_registry.py`, `test_chart_image_delivery_e2e.py`) | 53 passed / 4 deselected / 0 FAILED |
| B | sources subsystem (12 files) | 397 passed + 5 xfailed / 0 FAILED |
| C | scheduling family (10 files) | 357 passed + 2 FAILED (identical ids both revs) |
| D | source-adjacent + registry-named (9 files) | 387 passed / 0 FAILED |
| SU | `sources_unit_test.sh` pack | 213 passed, exit 0 |
| SA | `scheduled_tasks_acceptance.sh` pack | 169 passed, exit 0 |
| E | prompt-artifact baseline `--check` | `PROMPT-BASELINE: OK — 181`, exit 0 (seal re-pins clean) |
| PIN | pin pack (tip only; absent at base by construction) | 14 passed at tip; wrapper exit 127 at base |

**FAILED diff:** the 2 Run-C reds are IDENTICAL at both revs → pre-existing, deterministic (reproduced 3×, 5.1–5.3s), NOT lane-caused:
- `tests/unit/test_scheduler_deferred_mechanism_probe.py::TestP2ClaimGateSerializes::test_p2_pending_task_is_blocked_then_claimed_after_free`
- `tests/unit/services/test_scheduling_service.py::TestUserTimezoneRung::test_user_setting_rung_covers_once_anchor_site`

**Net-new (tip-only FAILED) on the lane's change surface = ∅.**

## Disclosures (carried, none blocking)

1. G2's exact 1000-row clamp ceiling is pinned at the unit/pin-pack level (G3 green), not observable at the 124-row API fixture — combined coverage justifies PASS.
2. `scheduled_tasks_acceptance.sh` (a pre-existing registered pack) internally sets `ENSEMBLE_SELF_ENV=dev` and boots an in-process daemon against `ensemble_dev` per its own documented configuration — disclosed; no manual dev-DB access outside the pack, and `ensemble_dev` is the dev database, not production. House-hygiene note for the pack owner (self-env and DB target should be visible at the wrapper level).
3. Dummy-token Discord Gateway failure ×5 then SIGTERM — expected fixture artifact, ends `error` (proves the sweep spares started rows).
4. `paging activity from _load_all_configs` produced 0 grep hits at this scale (single 500-row scan page; 124 < 500) — decisive proof is fix(124/total) vs base(100/no-total) behavior.
5. Pydantic v1 UserWarning noise in Py3.14 venv boot logs — harmless, matches the morning repro.
6. Reviewer-routed W1/W2/W3 belong to a follow-up lane (not this gate).

## Teardown (confirmed, both workers)

- Boot leg: all 4 boot PIDs gone, ports 18079/18080 free, `ensemble_smoke_wi_b97ec302*` DBs dropped (exists-check `[]`), fence unchanged (MainPID 3422961 / NRestarts 0 before==after), zero writes outside `/tmp/wi-b97ec302-verify-boot/`.
- Pack leg: mutant clone discarded, both scratch clones clean at the correct revs, no stray processes/DBs, all writes under `/tmp/wi-b97ec302-verify-packs/` (1.0 GB evidence + clones).

## Artifacts

- `/tmp/wi-b97ec302-verify-boot/evidence/`: `00-ambient-env.txt`, `01-checkouts.txt`, `02-expected-behavior.md` (fix-diff-derived contract), `03-import-shadow-proof.txt`, `g1fix-*`, `g1base-*`, `g2-*`, `g5-*`.
- `/tmp/wi-b97ec302-verify-packs/evidence/`: `01-checkouts`, `02a-changed-files`, `03-g3a-green.log`, `04a/04b/04c-g3b`, `05-restore-clean.log`, `06-g4-failed-diff.txt`, `g4-{tip,base}-{A,B,C,D,E,SU,SA,pin}.log`.

## Next

giter owns lane close (commit tester artifacts, push branch, merge to latest at leader call, stage the release for the user's ceremony). Nothing here touched the live daemon, the shared checkout, or the lane branch.
