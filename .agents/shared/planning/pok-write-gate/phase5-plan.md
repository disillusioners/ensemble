# Wave 1 · Phase 5: Metrics Foundation + Standing Defects

## Objective
Lay the metrics substrate that the librarian (Phase 10) will read. Addendum-3 item 7 REVERSES addendum-2's retire-with-disclosure for `record_verification`: it GETS A CALLER (the gate path). The vector leg stays retire-with-disclosure. State the trusted-vs-ignored metric set explicitly.

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Wire `record_verification` (`repository.py:384`) into the gate path — gate verdicts/merges/refusals/admissions stamp verify metrics | W2 Phase 7/8 (caller site) | **W1 half:** the caller is DESIGNED and the metric-write seam is reserved (trusted/ignored set documented) — Doc note + diff review. **W2 half:** `record_verification` actually called on verdicts with stamping proven — Unit test verify_count stamps > 0 |
| 2 | Vector leg retire-with-disclosure note (embeddings 0 + seam never passes query_embedding; no caller) | Phase 0 | Doc note in guideline measured-state |
| 3 | Trusted-vs-ignored metric set documented (design-amendments §9): TRUST gate-stamped verify, curator tiers, strict family, verdict-derived counts; IGNORE access_count-alone, age, vector, pre-stamp verify, inert tiers | none | Doc note + diff review |
| 4 | Docstring class boundary (operational how-to vs knowledge) in `pok_service.py` | none | Diff review |
| 5 | Never-age/access note (112 zero-retrieval rows — 499-era architect-prose figure, NO in-branch source; S-2 reconciliation in plan-overview Research Insights) | none | Doc note |
| 6 | Standing-defect residual rows in ledger | Phase 1 | Unit test residual rows |

## Coupling
- **Tight with:** Phase 1 (metrics sink); Phase 10 (soft-max reads these metrics); W2 Phase 7/8 (gate stamps them).
- **Loose with:** Phase 12 (guideline measured-state consumes notes).

## Risks
- Do NOT ship a metrics-driven librarian flying only on access_count (retrieval-driven, not use-driven).
- record_verification wiring is now MANDATORY (supersedes retire-with-disclosure); vector leg still retired.

## Exit Criterion
Trusted/ignored metric set documented; vector leg retired-with-disclosure; docstring boundary + never-age note; residual rows in ledger. (Wave 2 half — `record_verification` wired to gate path with stamping proven — lands in Phase 7/8's exit criterion.)
