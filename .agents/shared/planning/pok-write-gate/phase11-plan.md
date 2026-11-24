# Wave 2 · Phase 11: Wave 2 Tests + Packs

## Objective
Prove Wave 2 (4-outcome gate, authority split, G1 answer tool, soft-max librarian) with the shadow-mix validation gate, CONTRACT tests, the seven FORK PINS (each with a mutation leg on the CHOSEN shape), a marked LLM path, and pack registration. No real LLM in default runs.

**Acceptance criterion (burden of proof on mechanical stages):** any MECHANICAL pre-filter stage must be justified by a MEASURED agreement rate, not assumption — the measured acceptance bands are the PERSISTED hand-labelled sets (`evidence-rederiv-2026-11-23/funnel-and-agreement.md` Figure 2, 2026-11-24): **Band B 13/16 = 81.2%** (18 named hand-judged borderline rows; R/G axis n=16; the C5 near-dup pair is excluded at 0/2 — the mechanical label set cannot express that class) and **Band C 42/50 = 84.0%** (50-row hand-classified tail; 5/25 hand-R lexical misses, 3/25 hand-G lexical false-positives); predicate for both bands: `ROLLING:` present ⟺ hand-R, absent ⟺ hand-G. The relayed agreement figure for the 64-row semantic borderline band is UNBACKED (per-row labels for that band were never persisted — attested by history `2bf04468` + `cutlist-gap-2026-11-23.md:50`) and is DROPPED (its digits are not reproduced here by design — see the fold ledger). Honest framing: 81–84% lexical-vs-label agreement on the borderline bands still leaves 16–19% error on exactly the contested rows — below the bar for an admission decision; the judge mandate stands on the Emperor's ruling PLUS this measured residual. The semantic R1/R2 core is empirically mandated; mechanical stages carry the burden of proof.

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Shadow mix ≈ census mix gate (disagreement ⇒ rubric wrong, do NOT enforce) | Phases 7–10 | Comparison test |
| 2 | 5 family CONTRACT tests (each names alternative home) | Phase 7 | Self |
| 3 | MERGE/ASK/authority/verify-stamp tests | Phases 7–8 | Self |
| 4 | Soft-max + consolidation-basis + cold-start-floor tests | Phase 10 | Self |
| 5 | Mutation: remove gate call → RED | Phase 8 | RED |
| 6 | Mutation: fail-open flip → RED (D2 fail-closed pin) | Phases 7–8 | RED |
| 7 | Mutation: RAG-twin rejection → RED (R1-beats-R4 guard) | Phase 7 | RED |
| 8 | Fork pin TTL — expiry ⇒ `pok_gate_expired`, no write; TTL default 1200s; mutation: missing ASK answer defaults to accept → RED | Phase 8 | Pin green + mutation RED |
| 9 | Marked LLM integration test (excluded from default runs) | Phase 7 | Marked |
| 10 | Pack registration `pok_write_gate_w2_unit_test` in `.agents/tester/PACKS.md` | none | Pack clean |
| 11 | Fork pin G — `pok_gate_answer` in KNOWN_TOOL_NAMES (269 → 270); mutation: answer-field branch inside `pok_upsert` → RED | Phase 8 | Pin green + mutation RED |
| 12 | Fork pin F — MERGE delta-execution requires a completed ASK answer under `merge_policy=ASK_WITH_PROPOSAL`; mutations: auto-execute without confirmation → RED; no-delta merge writing a row → RED | Phase 7 | Pin green + 2 mutations RED |
| 13 | Fork pin CACHE — cached ASK replay re-mints `gate_id` and NEVER returns accept; mutation: ASK-cache-hit → accept → RED | Phase 8 | Pin green + mutation RED |
| 14 | Fork pin SOFT-MAX — value lives at `pok.soft_max.v1` in project metadata; mutation: a soft-max/meta row inside the POK store → RED (doctrine) | Phase 10 | Pin green + mutation RED |
| 15 | Fork pin PASS — job registered UN_GATED + in `JOB_MAINTENANCE_SECTIONS`; mutation: boot path contains pass execution → RED (SLO guard) | Phase 10 | Pin green + mutation RED |
| 16 | Fork pin COUNTER — merge/supersede preserves verify_count lineage; mutation: counter reset on supersede → RED | Phase 2 | Pin green + mutation RED |
| 17 | Alias + stub + switch pins — `POK_GATE_MODEL` literal `"quick"` (never the global model); mode=stub in prod → forced llm; both sibling settings keys OFF at build | Phases 7–8 | Pin green |

## Coupling
- **Tight with:** Phases 7–10 (proves them); Phase 2 (counter pin).

## Risks
- Shadow-mix gate is the precondition for P1 enforcement — do not skip.
- The answer-path acceptance must run with the switch ON (enforce mode) — shadow evidence is not admissible for the ASK path.
- Mutation legs on throwaway archive; marked LLM path never in default runs.

## Exit Criterion
Shadow-mix gate green; CONTRACT/MERGE/ASK/authority/soft-max tests pass; 7 fork pins green with their mutation legs RED; alias/stub/switch pins green; LLM path marked; pack registered.
