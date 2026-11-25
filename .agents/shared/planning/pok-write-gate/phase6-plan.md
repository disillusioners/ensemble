# Wave 1 · Phase 6: Wave 1 Tests + Packs

## Objective
Prove Wave 1 (ledger, family-count coordination + counter carry-over, mechanical enforcement, grant removal + capture duty, metrics foundation) with units, mutation legs, scratch-store fence, tool sweep, pack registration, baseline check. **The strict-family-count unit test and its prefix-superset mutation leg belong to the PARALLEL LANE together with the fix they exercise** (leader de-scope 2026-11-24) — this phase keeps only the tests for the surviving Phase-2 work (refusal-path consumption + carry-over).

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Wave 1 unit tests (ledger append/query+both audit rows, family-full refusal on the corrected count, counter carry-over lineage, reason validation, R0 fence, basis enum) | Phases 1–5 | Tests green |
| 2 | Mutation: reason validation removed → RED | Phase 3 | RED |
| 3 | Mutation: grant re-added to kb-writer → RED | Phase 4 | RED |
| 4 | Mutation: counter carry-over dropped from supersede → RED | Phase 2 | RED |
| 5 | Pack registration `pok_write_gate_w1_unit_test` in `.agents/tester/PACKS.md` | none | Pack clean |
| 6 | Ledger schema test | Phase 1 | Self |
| 7 | Tool sweep green | Phase 3 | Green |
| 8 | Baseline `--check` rc=0 (4-agent sealed edits) | Phase 4 | rc=0 |
| 9 | Scratch-store fence (in-memory sqlite two-table create_all; no live write) | none | No live write |

## Coupling
- **Tight with:** Phases 1–5 (proves them).

## Risks
- Worktree packs registration drift — cross-check main repo PACKS.md copy.
- Mutation legs on throwaway `git archive` extract, never live worktree.

## Exit Criterion
All Wave 1 units green; 3 mutation legs RED (reason/grant/counter-carry-over); pack registered; baseline rc=0; scratch fence honored.
