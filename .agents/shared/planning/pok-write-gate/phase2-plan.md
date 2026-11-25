# Wave 1 · Phase 2: Family-Count Coordination + Counter Carry-over

## Objective
**Coordination + carry-over — this phase executes NO family-count fix.** The strict per-key family-count fix (`repository.py:221`/`:250`) and the 2-family SPLIT/RE-KEY + ARCHIVE (`guard-inv`, `prompt-assent`) are **DE-SCOPED to a parallel lane** (leader de-scope dispatch, 2026-11-24); this lane consumes their output and re-verifies read-only. What this lane still owns: (1) the coordination record with the parallel lane; (2) the family-full refusal path consuming the corrected count; (3) the read-only re-verify against the census; (4) the **counter carry-over** at supersede, so the trusted-metric chain survives every supersede. Provenance for the parallel lane's execution (not this lane's work): 21 is the PRE-FIX prefix-counting figure (the sprawl-engine evidence); POST-FIX strict membership yields 2 true over-cap families — handled by the parallel lane under **SPLIT/RE-KEY + ARCHIVE** on a ruled basis, never a content-merging supersede (four distinct durable rules cannot fit one ≤240-char paragraph; the standing POK `pok-sweep-01-merge-cap-split` says SPLIT, never merge-with-drop).

**Live ID set (matches `plan-overview.md` task map ID-for-ID):** `T2.COORD` + T2.5 + T2.6 + T2.7 + T2.8. **The 3–4 gap is a documented de-scope artifact — no live Phase-2 row bears 3 or 4** (old T2.1/T2.2 = the count fix + its mutation leg; old T2.3/T2.4 = the 2-family split/re-key + archive — both REMOVED to the parallel lane, 2026-11-24).

## Tasks
| # | ID | Task | Depends On | Acceptance |
|---|----|------|------------|------------|
| 1 | T2.COORD | **COORDINATION** with the parallel lane that owns the family-count fix + the 2-family split/re-key + archive (`guard-inv`, `prompt-assent`). The design constraints TRANSFER to that lane: split-never-merge (standing `pok-sweep-01-merge-cap-split`), ≤240-char escaped-body fence, archive only under a ruled basis (covered-by-surviving-row / relocated / NOW-FALSE). This lane consumes/verifies and EXECUTES NOTHING | Phase 0 (anchor re-verify) | Coordination note filed; zero code written by this lane for the fix; anchors re-grepped AFTER their landing |
| 2 | T2.5 | Family-full refusal uses the corrected count — **depends on the parallel lane's fix landing** | T2.COORD | Unit test refusal (against the corrected count) |
| 3 | T2.6 | Re-verify the corrected count against the census (read-only, against the parallel lane's fix) | T2.COORD | Count re-derived and reported |
| 4 | T2.7 | Counter carry-over at supersede — the successor inherits prior counters (verify_count + any adopted metrics) instead of defaulting to 0 (`models.py:251-260`) | T2.COORD | Unit test: verify_count lineage preserved across supersede at `repository.py:286-311` (`supersede_and_insert`) |
| 5 | T2.8 | PIN: counter carry-over present — mutation leg: dropping carry-over must RED | T2.7 | RED (throwaway archive) |

## Coupling
- **Coordination + dependency shape (NOT "tight with Phase 2" ownership):** this phase no longer OWNS the count fix — it DEPENDS on the **parallel lane** landing the strict per-key fix and the 2-family split/re-key + archive. Its code work (refusal consumption + counter carry-over) touches `daemon/repositories/pok/repository.py` and `daemon/services/pok_service.py` — the SAME files the parallel lane edits (**shared seam**; see `plan-overview.md` risk row 16: sequence + re-verify the anchors after their landing).
- **Tight with:** Phase 1 (the basis enum records the split/archive basis — the split/archive itself is performed by the parallel lane); Phase 5 + Phase 10 (the J trusted-metric chain depends on T2.7).
- **Independent of:** Phase 4 (grant removal).

## Risks
- Anchor drift (`:114-122` stale) — actual `:221`/`:250` verified in Phase 0 (the parallel lane's target site; this lane re-verifies it read-only for coordination).
- Multi-token regex must match the live family key exactly — **the parallel lane's implementation concern**; this lane only re-verifies the resulting count.
- Content-destructiveness if the parallel lane's split/re-key is executed as a merge — the transferred split/archive rule is binding, not advisory.
- Without T2.7 every merge silently resets the trusted metrics, making J self-defending.

## Exit Criterion
Coordination note filed with the parallel lane (the strict per-key fix and the 2-family split/re-key + archive are ITS execution, not this lane's); family-full refusal correct against the corrected count; read-only re-verify reported; counter carry-over landed + its mutation leg RED.
