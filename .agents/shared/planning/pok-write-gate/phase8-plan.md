# Wave 2 · Phase 8: Authority Split + G1 Answer Tool + Shadow Integration

## Objective
Bind the AUTHORITY SPLIT: the gate NEVER writes — it returns a verdict; the TOOL executes: write (ACCEPT); supersede (MERGE delta, count-preserving); NO write (MERGE no-delta); archive residue under a ruled basis; the audit records BOTH verdict and execution. Add the **G1** answer path (`pok_gate_answer`, TTL 1200s, 6 typed refusals) with fail-closed pending state. Wire the gate in SHADOW (default OFF) with **two sibling** settings keys.

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Gate is a pure function: returns a typed verdict, ZERO writes | Phase 7 | Unit test (no writes emitted) |
| 2 | TOOL executes: write (ACCEPT); supersede (MERGE delta, count-preserving); archive residue under a ruled basis | T1 | Unit test execution |
| 3 | Audit records BOTH verdict and execution (2 rows; 3 rows on the ASK chain) | T1/T2 + Phase 1 | Unit test both rows |
| 4 | ASK payload: `pok_upsert` returns a structured "confirmation required" (gate_id + structured question) | T1 | Tool test |
| 5 | ASK answer path = **G1**: dedicated `pok_gate_answer(gate_id, answer)` tool; the answer carries NO body — the pending entry stores the adjudicated stripped body + all upsert inputs; execution re-runs `POKService.upsert` with `gate_fn=None` so scope/fence/budget/no-op/caps re-check at answer time | T4 | PIN: tool registered in KNOWN_TOOL_NAMES (269 → 270); mutation: answer-field branch inside `pok_upsert` → RED |
| 6 | Pending-gate state IN-PROCESS, **TTL 1200s** (env `ENSEMBLE_POK_GATE_ASK_TTL_S`, min 120 / max 3600), owner-scoped; NO live-store writes for pending state | T5 | TTL unit test; no DB write |
| 7 | Missing answer → NO write (fail-closed, never default accept) + audit ask+answer; **6 typed refusals**: `pok_gate_unknown`, `pok_gate_expired`, `pok_gate_already_answered` (audited no-op), `pok_gate_not_owner` (audited), `pok_gate_answer_required`, `pok_gate_answer_unrecognized` (unclassifiable free-text ⇒ no write) | T5/T6 | Unit test per refusal |
| 8 | SHADOW default OFF — no write refused by the judge; **shadow-ASK advisory-only** (verdict logged `phase=ASK_SHADOW`, `gate.advisory=true`, NO pending mint, NO "confirmation required" payload — that framing on an already-committed write is misleading and shadow forbids it) | T1 | Config test |
| 9 | `gate_fn` injected strictly BETWEEN the no-op echo (:432) and supersede (:433) — a supersede must be judged (the digest carries the prior row ⇒ MERGE detection works); bypass = None | Phase 3 | Unit test gate called only on non-no-op |
| 10 | No circular import (gate → ledger via engine) | T9 | Import smoke test |
| 11 | Enforcement switch = **TWO SIBLING settings keys**, both default OFF at build: `pok_gate.semantic_enforcement` + `pok_gate.softmax_tightening` (DB-backed, `settings:write`, NOT env — an env flip is a live config change at deploy + restart) | T8 | Config test: both OFF |
| 12 | Tier pre-check: a pinned/curated merge target is curator-only (`pok_service.py:592-611`) and invisible in the digest — the TOOL pre-checks the target tier; an untouchable target → ASK routed to leader/PM (CURATOR), never auto-merge | T2 | Unit test: pinned target → ASK-to-curator |
| 13 | Cache × ASK: ASK cached AS-ASK under the D5 key; a replay re-asks with the SAME question but re-mints a fresh `gate_id` + TTL; the ANSWERED outcome is NEVER cached as ACCEPT (caller self-attestation must not launder into a durable gate accept); stub/llm entries segregated | Phase 7 | PIN + mutation: ASK-cache-hit → accept → RED |
| 14 | 3-row ASK audit chain keyed by `gate_id` + `phase`: `ASK_MINTED` → `ASK_ANSWERED` → `ASK_EXECUTED`; plain ACCEPT/MERGE/REJECT keep 2 rows; expired/never-answered gates surfaced by the librarian pass (`phase=ASK_EXPIRED`) | T3 | Unit test 3 rows |
| 15 | **R-RET outcome mapping**: NEW-write retrievability failure → ASK-with-rename-proposal (remedy in `alternative`/`proposed_slug`), never auto-reject; shadow = advisory-only (no pending mint); TOOL executes re-slug via the supersede_and_insert machinery (`repository.py:286-311`); Phase-2 coordination (T2.COORD) + carry-over (T2.7) — body preserved, count-neutral | T1/T2 + Phase 7 | Unit test: rename→ASK, TOOL re-slug preserves body; mutation: R-RET hard-reject → RED |

## Coupling
- **Tight with:** Phase 7 (gate service); Phase 1 (ledger audit); Phase 10 (soft-max tightening feeds ASK; the pass surfaces expired gates).
- **Loose with:** Phase 9 (route table in the rejection envelope).

## Risks
- Emperor's catch: a missing OR unclassifiable answer must never default to accept — TTL + fail-closed + 6 typed refusals.
- Shadow answer-path ships dark — T4–T7 acceptance must be exercised with the switch ON in unit tests (plus one dev-lane exercise); never trusted on shadow evidence.
- Rubber-stamped proposals — mitigated by the FULL target body in the payload; the ledger records proposal AND executed body (edit-distance measurable).

## Exit Criterion
Gate writes nothing; TOOL executes; audit records both; G1 tool registered + pinned; TTL 1200s pending state with no live-store write; missing/unclassifiable answer fail-closed + audited; SHADOW default OFF with advisory-only ASK; two sibling switches OFF; tier pre-check routes to curator; cached ASK never accepts; 3-row audit chain; R-RET rename-remedy execution (never auto-reject, shadow advisory-only). (Wave-1 half of Phase 5's record_verification wiring lands here — `record_verification` called on verdicts with stamping proven.)
