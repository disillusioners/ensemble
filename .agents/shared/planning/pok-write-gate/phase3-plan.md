# Wave 1 · Phase 3: Mechanical Gate Enforcement

## Objective
Enforce the two MECHANICAL rules NOW: `reason` REQUIRED and the R0 named content fence. No LLM. Typed refusals via the existing `_err` envelope. This is the only enforcement that lands in this plan's build; the semantic judge (4-outcome) is shadow in Wave 2.

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | `reason` (str, default None) + `gate_fn` (default None) DEFAULTS on `POKService.upsert` — the signature defaults are MANDATORY: without them **97 direct `svc.upsert` test sites break** (verified: `tests/unit/services/test_pok_service_unit.py` = 97 `.upsert(` sites) | Phase 0 | Existing 97 sites green; mutation: remove a default → RED |
| 2 | `reason` on `pok_upsert` typed signature + docstring; preserve `_substitute_active_cap` ordering | T1 | Tool schema shows reason required |
| 3 | Reason validation → `pok_reason_required` before service call — **pin the refusal-precedence order (scope → store → reason → fence/budget/family), preserving existing refusal codes**, against the 6 precedence-sensitive tests (`test_pok_tools_unit.py:180,:197,:281,:291,:299,:315`) | T2 | Unit test (order pinned) |
| 4 | R0 named fence enforcement (mechanical, existing fence at `pok_service.py:395` — `fence_check(body)`) | T1 | Unit test: refusal names R0 / content fence |
| 5 | Typed refusals via `_err` (`pok.py:117-120`) | T3/T4 | Unit test envelope shape |
| 6 | Tool sweep — **37** `.invoke(` in `tests/unit/tools/test_pok_tools_unit.py` (18 upsert-index + 13 bare-tool) + **9** in `tests/unit/services/test_pok_trust_tier_unit.py` (6 upsert); unswept upserts will RED once `reason` is REQUIRED | T2 | Existing tests green |
| 7 | Mutation leg: remove reason validation → RED | T3 | RED (throwaway archive) |
| 8 | Ops note: `pok_default_project` = scope classification, not a verdict (AC10) | none | n/a (doc) |
| 9 | PIN: service-signature defaults present — mutation leg: removing `reason`/`gate_fn` defaults must RED (97 sites) | T1 | RED (throwaway archive) |

## Coupling
- **Tight with:** Phase 1 (verdict/basis → ledger); Phase 7 (R0 seam in judge); Phase 8 (gate slot position).
- **Independent of:** Phase 4 (grant removal).

## Risks
- Omitting the defaults is a silent 97-site breakage — the pin in T9 makes it RED.
- Tool-sweep width — the defaults keep direct svc.upsert call sites byte-unchanged.

## Exit Criterion
`reason` required and validated; R0 named on failure; typed refusals; tool sweep green; both mutation legs RED (reason validation + signature defaults).
