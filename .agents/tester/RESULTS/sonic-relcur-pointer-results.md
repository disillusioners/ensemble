# TEST RESULTS — sonic relcur-pointer (integration / TEST half)

- Verdict: **TEST: PASS**
- Tree: worktree `/home/nea/ensemble-src-wt-relcur`, branch `sonic/relcur-pointer`, tip `27f913009`; base `a1130e257` (verified ancestor).
- Fences: worktree `.env` only (dev `ensemble_dev` @127.0.0.1:5432, `DAEMON_PORT=8079`); all pytest runs with 5× `POSTGRES_*` + `DATABASE_URL` unset and `ENSEMBLE_SELF_ENV=dev`; no live promote, no `promote.sh`, no writes to live tree.

## Packs (all timeout-wrapped)
| pack | result |
|---|---|
| `tests/packs/relcur_flip_moves_alias_test.sh` | 15 passed / 0 failed |
| `tests/packs/relcur_eviction_refuses_pointer_target_test.sh` | 14 passed / 0 failed |
| `tests/packs/relcur_repair_idempotent_test.sh` | 9 passed / 0 failed |

## Pytest pins
- `tests/unit/services/test_release_pointer_check.py` + `tests/unit/tools/test_upgrade_tools.py`: **251 passed**, 0 failed (30.78s).

## Mutation trail
- (c) boot detection: mutated `ok = not warn_reasons` → `ok = True` (no-op satisfiable) in `daemon/services/release_pointer_check.py` ⇒ **6 RED** (mismatched/dangling/absent/top-mismatch/third-version/drift-token nodes); restored ⇒ **10 GREEN**.
- (a) flip moves alias + pointer==journal: pack LEG4/LEG5/LEG6 mutation legs ran green inside the 15/0 pack (revert alias hunk ⇒ no move; r1 flip-site compare ⇒ L2 alarm; gut `pointer_alias_assert_commit` ⇒ L3 detection RED).
- (d) repair idempotency: pack runs repair twice, run2 no-op + inode unchanged (9/0).
- (b) eviction-refusal pin transfer to lane 60d008ef: documented in `COORDINATION-TO-retention-lane-60d008ef.md` (main repo copy byte-identical to worktree copy) and in HANDOFF §Known risks / §What changed; eviction pack run as PROVISIONAL only, no canonical eviction pins built here.

## Quarantine
- `.agents/tester/QUARANTINE.md`: no entry references `relcur*`, `release_pointer_check`, or `pointer-alias-repair`. Only `test_upgrade_tools.py::TestScriptsPinSelfAdvance::test_a2_concurrent_steal_race_single_winner` appears, marked RESOLVED 2026-11-20 (pin-side fix, zero production changes); full 251-pin run green. No collision.

## Pre-existing red spot-check
- At base `a1130e257` (temporary detached worktree, since removed): `bash tests/test_rollback_target.sh` ⇒ C6 FAIL (`old-format in_flight ... halt event` expected 'halt', actual open_txn/intent_flip/sweep_deferred), PASS=34 FAIL=1 — identical to handoff-disclosed pre-existing red. Not attributable to this change.

## FAILED-list diff vs true base
- `pytest tests/unit/tools/ -q` on merged: **24 failed / 5778 passed / 6 skipped / 1 xfailed**.
- Same command at base `a1130e257`: **24 failed**, identical node-id list (diff empty). Breakdown: `test_checkpoint_pool_config`×1, `test_dbpool_census_families`×2, `test_inner_soul_compaction`×3, `test_instance_tools`×2, `test_prompt_section_reference_integrity`×16. Net-new failures = 0. No new red ⇒ no isolated order/load re-run required.

## Read-only corroboration
- `bash -n` on `lib.sh`, `promote.sh`, `pointer-alias-repair.sh`: OK.
- Live pointers: `/home/nea/agents-ensemble/current` → `releases/v0.31.42`; `/home/nea/agents-ensemble/releases/current` → `v0.31.42` (repaired, healthy).
- Staged binary sha256 `8509ed12af66048cf246cebb41e4fadfc70af12314c43f3428164d474a8d8107` matches handoff 4-way value.
