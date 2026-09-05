# Test Report: Auth Module Unit Tests

Date: 2026-09-05T04:16Z (run window)
Repo: agents-ensemble — branch `latest` @ `5d7a0695abb8a49fae5e51fd903cae0527b4b987`
Instance IDs: c8612771-b1a7-4103-bce2-ba844668782f (authz pack), 826278e5-f1de-452c-9cb2-d11b4a5a6842 (spawn_team_members pack)

## Summary
- Total: 126 tests | Passed: 126 | Failed: 0 | Errors: 0 | Timeouts: 0
- Unit packs: 2 | Mock packs: 0
- Quick fixes applied: 0 | Commits: 0 (no code changed)
- Quarantined: 0 skipped in these packs this run

## Scope Decision
> Request: "run the unit tests for the auth module". Repo has no literal `auth/` source module; the authorization surface maps to exactly two registered unit packs — `authz_auto_derive_unit_test` (daemon/tools/_auth.py: `_check_team_membership` auto-derive, deny-by-default, ari no-spawn contract) and `spawn_team_members_unit_test` (team_members spawn authorization gate). Ran those two, skipped all other packs. Full suite not warranted — targeted verification run, no change signal.

## Unit Test Results

| Pack | Worker | Result | Counts | Runtime |
|---|---|---|---|---|
| authz_auto_derive_unit_test (`test/packs/authz_auto_derive_unit_test.sh`) | c8612771 | ✅ PASS | 82/82 | 3.37s |
| spawn_team_members_unit_test (`tests/test_spawn_team_members.py`) | 826278e5 | ✅ PASS | 44/44 | 3.88s |

Execution wrappers (dual-layer honored): `timeout 120 bash test/packs/authz_auto_derive_unit_test.sh`; `timeout 120 python -m pytest tests/test_spawn_team_members.py --tb=short -q`. No `-x`, no broad-suite commands, quarantine rules respected.

## Failures
None.

## Notes / Observations
- **Pack count drift (benign)**: PACKS.md row for `spawn_team_members_unit_test` recorded 27 tests (last run 2026-07-25); file now collects 44. Matches recent gate entries (2026-08-26/27 record 44/44). Row refreshed to 44 in PACKS.md.
- **Warnings (non-failing, 545 in spawn pack)**: (1) `langchain_core` pydantic-v1 UserWarning on Python 3.14 — environment-level; (2) `asyncio.iscoroutinefunction` DeprecationWarning at `daemon/tools/instance.py:1476/1537/1610` — removal slated Python 3.16; replacement `inspect.iscoroutinefunction()`. 🟢 nice-to-have cleanup pass, not blocking.
- ensure.md: not run as a separate pass — this was a verification run with no change set; the Core "changed packs PASS" intent is satisfied by both packs green. Full ensure.md validation only warranted for a change gate.

## Action Needed
- None blocking. Optional: schedule `asyncio.iscoroutinefunction` → `inspect.iscoroutinefunction` cleanup (3 call sites) ahead of Python 3.16.

## Documentation Updated
- [x] PACKS.md — both rows: Last Run 2026-09-05, PASS counts, spawn_team_members count refreshed 27→44
- [x] RESULTS/2026-09-05-auth-unit-tests.md — this report
- [ ] MOCK_TESTS.md — n/a (no mock packs run)
- [ ] QUARANTINE.md — no changes (0 quarantine events)

## Overall Status
- Unit Tests: ✅ PASS (126/126)
- **Testing Complete: ✅ READY** (auth scope)
